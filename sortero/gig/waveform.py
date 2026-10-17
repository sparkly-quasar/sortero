"""A track's waveforms, as a Pioneer player draws them: the overview, the scrolling detail, colour.

The analysis follows rbxport's (crates/rbl-analysis/src/waveform.rs,
GPL-2.0-or-later): the audio split into lows (below about 200 Hz), mids and
highs (above about 2 kHz), and for every 1/150 s column each band's peak and
the signal's own, out of 255. rbxport filters with one-pole low-passes; here
the split is done with moving averages through audioop, which runs in C,
because Sortero carries no numpy (see tempo.py). Both are approximations of
whatever rekordbox does, and the players only draw them.

The encodings are rbxport's (crates/rbl-anlz/src/encode.rs), whose
ceilings, floors and colour weights were measured against rekordbox's own
files:

  PWAV  400 columns, a byte each: height (2-25) and "whiteness" (treble share)
  PWV2  100 columns, height only (1-15)
  PWV3  150 a second, a byte each, height from the peak squared
  PWV4  1,200 columns of six bytes: peak, 0, 0, then low, mid and high
  PWV5  150 a second, a 16-bit word: red, green, blue (low, mid, high), height
  PWV6  1,200 columns of low, mid and high out of 127 (CDJ-3000's three-band)
  PWV7  150 a second of the same
"""
from . import ffmpeg
from ..tempo import audioop

RATE = 12000
PER_SECOND = 150
PER_COLUMN = RATE // PER_SECOND         # 80 samples
LOW_BOX = 64                            # a 64-sample average twice over: lows below ~190 Hz
HIGH_BOX = 4                            # a 4-sample average: what's left is above ~2-3 kHz


class WaveformError(Exception):
    pass


# --------------------------------------------------------------- analysis
def _box(pcm, n):
    """Moving average over n samples (a power of two), in log2(n) passes."""
    k = 1
    while k < n:
        half = audioop.mul(pcm, 2, 0.5)
        pcm = audioop.add(half, b"\0\0" * k + half[:-2 * k], 2)
        k *= 2
    return pcm


def _earlier(pcm, samples):
    """Undo a filter's delay, so the bands line up with the signal."""
    return pcm[2 * samples:] + b"\0\0" * samples


def from_pcm(pcm):
    """[(low, mid, high, peak)] per 1/150 s, each 0-255, from mono 16-bit PCM at RATE."""
    pcm = pcm[:len(pcm) - len(pcm) % 2]
    below = _earlier(_box(pcm, HIGH_BOX), (HIGH_BOX - 1) // 2)
    low = _earlier(_box(_box(pcm, LOW_BOX), LOW_BOX), LOW_BOX - 1)
    high = audioop.add(audioop.mul(pcm, 2, 0.5), audioop.mul(below, 2, -0.5), 2)
    mid = audioop.add(audioop.mul(below, 2, 0.5), audioop.mul(low, 2, -0.5), 2)
    step, out = 2 * PER_COLUMN, []
    for i in range(0, len(pcm), step):
        def peak(x, scale=1):
            return min(255, audioop.max(x[i:i + step], 2) * 255 * scale // 32768)
        out.append((peak(low), peak(mid, 2), peak(high, 2), peak(pcm)))
    return out


def _ssnd(aiff):
    """The sample data of an AIFF, as little-endian 16-bit. Written to a pipe, the
    chunk sizes can't be filled in, so the data runs to the end of the file."""
    at = aiff.find(b"SSND")
    if at < 0 or aiff[:4] != b"FORM":
        raise WaveformError("ffmpeg didn't return audio")
    offset = int.from_bytes(aiff[at + 8:at + 12], "big")
    data = aiff[at + 16 + offset:]
    return audioop.byteswap(data[:len(data) - len(data) % 2], 2)


def analyse(path):
    """The columns of an audio file. Raises WaveformError if it can't be decoded.

    Decoded to AIFF on a pipe: the ffmpeg Sortero carries has the aiff muxer
    and pcm_s16be (build/ffmpeg/build.sh) but no raw-PCM one."""
    try:
        exe = ffmpeg.require("ffmpeg")
    except ffmpeg.Missing as e:
        raise WaveformError(str(e))
    r = ffmpeg.run([exe, "-v", "error", "-nostdin", "-i", path, "-vn", "-map_metadata", "-1",
                    "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16be", "-write_id3v2", "0",
                    "-f", "aiff", "-"])
    if r.returncode != 0 or not r.stdout:
        raise WaveformError("ffmpeg couldn't decode it")
    return from_pcm(_ssnd(r.stdout))


# --------------------------------------------------------------- encodings
OVERVIEW, PREVIEW, TINY = 1200, 400, 100
BAND_FULL_SCALE = 44                    # PWV6's scale, and the previews' (rbxport)
PWAV_CEILING, PWV2_CEILING, DETAIL_CEILING = 25, 15, 31
PWAV_FLOOR, PWV2_FLOOR = 2, 1           # a player reads a 0 column as "not analysed"
PWV4_MID_GAIN, PWV4_HIGH_GAIN = (4, 5), (13, 20)


def resample(cols, n):
    """n columns, each the loudest of the ones it spans, so transients survive."""
    if not cols:
        return [(0, 0, 0, 0)] * n
    out = []
    for b in range(n):
        start = b * len(cols) // n
        end = min(max((b + 1) * len(cols) // n, start + 1), len(cols))
        span = cols[start:end]
        out.append(tuple(max(c[k] for c in span) for k in range(4)))
    return out


def _band(v):
    return v * BAND_FULL_SCALE // 255


def _band_height(c, ceiling):
    loudest = max(_band(c[0]), _band(c[1]), _band(c[2]))
    return min(loudest * ceiling // BAND_FULL_SCALE, ceiling)


def _detail_height(c):
    full = 255 * 255
    return min((DETAIL_CEILING * c[3] * c[3] + full // 2) // full, DETAIL_CEILING)


def _mono(c, height):
    total = c[0] + c[1] + c[2]
    white = min(7, (c[2] * 7 + total // 2) // total) if total else 0
    return (white << 5) | min(height, DETAIL_CEILING)


def pwav(cols):
    out = bytearray()
    for c in resample(cols, PREVIEW):
        b = _mono(c, _band_height(c, PWAV_CEILING))
        out.append((b & 0xE0) | max(b & 0x1F, PWAV_FLOOR))
    return bytes(out)


def pwv2(cols):
    return bytes(min(max(_band_height(c, PWV2_CEILING), PWV2_FLOOR), PWV2_CEILING)
                 for c in resample(cols, TINY))


def pwv3(cols):
    return bytes(_mono(c, _detail_height(c)) for c in cols)


def pwv4(cols):
    def weigh(v, gain):
        return min(127, (v >> 1) * gain[0] // gain[1])
    out = bytearray()
    for c in resample(cols, OVERVIEW):
        out += bytes((c[3] >> 1, 0, 0, c[0] >> 1, weigh(c[1], PWV4_MID_GAIN),
                      weigh(c[2], PWV4_HIGH_GAIN)))
    return bytes(out)


def _pwv5_colour(c):
    low, mid, high = 2 * c[0], 2 * c[1], 3 * c[2]
    strongest = max(low, mid, high)
    if not strongest:
        return 0, 0, 0

    def share(v, full):
        return (2 * full * v + strongest) // (2 * strongest)
    return share(low, 7), share(mid, 5), share(high, 7)


def pwv5(cols):
    out = bytearray()
    for c in cols:
        r, g, b = _pwv5_colour(c)
        out += ((r << 13) | (g << 10) | (b << 7) | (_detail_height(c) << 2)).to_bytes(2, "big")
    return bytes(out)


def pwv6(cols):
    return b"".join(bytes((_band(c[0]), _band(c[1]), _band(c[2])))
                    for c in resample(cols, OVERVIEW))


def pwv7(cols):
    return b"".join(bytes((c[0] >> 1, c[1] >> 1, c[2] >> 1)) for c in cols)

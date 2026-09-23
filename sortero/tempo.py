"""Tempo check: is a track's BPM tag off by a whole ratio?

Beat trackers - Mixed In Key's, Mixxx's, rekordbox's - often lock on to the
wrong pulse. On hypnotic techno the rolling percussion falls in threes, so a
140 track gets tagged 93.33 (2/3); on a sparse track they count every other
kick and report 70. The number is precise, just scaled.

So this doesn't try to be a beat tracker. It measures how strongly the audio
repeats at a handful of candidate tempos - the tagged BPM and its x2, x3/2,
x4/3 (and inverse) relatives - and reports which one the audio supports. The
caller decides which of those is plausible for the genre.

Deliberately numpy-free: numpy has no universal2 wheel, and the Mac release is
universal. audioop does the per-sample work in C.
"""
import os, math, shutil, subprocess, sys, tempfile, wave

try:
    import audioop
except ImportError:             # removed from the stdlib in Python 3.13
    import audioop_lts as audioop  # noqa: F401  (pip install audioop-lts)

RATE = 11025                    # plenty for kick and hat onsets
HOP = 128                       # ~86 envelope frames per second
FPS = RATE / HOP
WINDOW = 60.0                   # seconds of audio listened to

# The whole-ratio mistakes beat trackers make, and their inverses.
RATIOS = (2.0, 1.5, 4 / 3, 0.5, 2 / 3, 0.75)


class DecodeError(Exception):
    pass


# ------------------------------------------------------------------ decode
def _span(duration):
    """Where to listen: past the intro, but a full window when the track allows."""
    if not duration or duration <= WINDOW:
        return 0.0, WINDOW
    return min(duration * 0.3, duration - WINDOW), WINDOW


def _pygame_pcm(path, start, length):
    """Decode through pygame's mixer - the same one the preview uses."""
    from . import preview
    m = preview._mixer()
    if m is None:
        raise DecodeError("no mixer")
    freq, size, channels = m.get_init()
    if abs(size) != 16:
        raise DecodeError("unexpected mixer format")
    try:
        raw = m.Sound(path).get_raw()
    except Exception as e:
        raise DecodeError(str(e))
    frame = 2 * channels
    a = int(start * freq) * frame
    b = a + int(length * freq) * frame
    if a >= len(raw):
        start, a, b = 0.0, 0, int(length * freq) * frame
    pcm = raw[a:b]
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    elif channels != 1:
        raise DecodeError("unexpected channel count")
    pcm, _ = audioop.ratecv(pcm, 2, 1, freq, RATE, None)
    return pcm, start


def _tool_pcm(path, start, length):
    """ffmpeg if it's installed, else afconvert, which every Mac has."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-v", "quiet", "-ss", str(start), "-t", str(length),
                            "-i", path, "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout, start
    if sys.platform == "darwin" and shutil.which("afconvert"):
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            r = subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1",
                                path, tmp], capture_output=True)
            if r.returncode == 0:
                with wave.open(tmp) as w:
                    pos = min(int(start * RATE), max(0, w.getnframes() - 1))
                    w.setpos(pos)
                    return w.readframes(int(length * RATE)), pos / RATE
        finally:
            os.remove(tmp)
    raise DecodeError("no decoder for this file")


def decode(path, duration=0.0):
    """(mono 16-bit PCM at RATE, where it starts in seconds): WINDOW seconds
    from the body of the track."""
    start, length = _span(duration)
    try:
        pcm, start = _pygame_pcm(path, start, length)
    except DecodeError:
        pcm, start = _tool_pcm(path, start, length)
    if len(pcm) < RATE * 2 * 15:
        raise DecodeError("too short to judge")
    return pcm, start


# ------------------------------------------------------------------ analyse
def _smooth(pcm, n):
    """Moving average over n samples: a crude low-pass, done in C."""
    part = audioop.mul(pcm, 2, 1.0 / n)
    out = part
    for k in range(1, n):
        out = audioop.add(out, b"\x00\x00" * k + part[:-2 * k], 2)
    return out


def _flux(frames):
    """Rises in log loudness, frame to frame."""
    prev, out = None, []
    for v in frames:
        v = math.log1p(v)
        if prev is not None:
            out.append(max(0.0, v - prev))
        prev = v
    return out


def _detrend(env, w=16):
    """Remove the slow swell of breakdowns and drops; keep the pulse."""
    out = []
    run = sum(env[:w])
    for i, v in enumerate(env):
        a, b = i - w // 2, i + w // 2
        if b < len(env) and a >= 0:
            run += env[b] - env[a]
        out.append(max(0.0, v - run / w))
    return out


def envelope(pcm):
    """Onset strength per frame, (all onsets, kicks only).

    All onsets - rises in full-band and treble loudness - measure tempo: the
    first difference of the signal is a cheap treble boost, so hats and claps
    count as well as kicks. Kicks alone, under ~300 Hz, find where the beat
    falls, since a techno hat sits exactly between two beats.
    """
    nxt = pcm[2:] + b"\x00\x00"
    diff = audioop.add(nxt, audioop.mul(pcm, 2, -1), 2)
    low = _smooth(_smooth(pcm, 18), 18)
    step = HOP * 2
    spans = range(0, len(pcm) - step, step)
    full = _flux(audioop.rms(pcm[i:i + step], 2) for i in spans)
    hi = _flux(audioop.rms(diff[i:i + step], 2) for i in spans)
    kick = _flux(audioop.rms(low[i:i + step], 2) for i in spans)
    return _detrend([a + b for a, b in zip(full, hi)]), _detrend(kick)


def _autocorr(env, max_lag):
    n = len(env)
    mean = sum(env) / n
    e = [v - mean for v in env]
    ac = [0.0] * (max_lag + 2)
    for lag in range(1, max_lag + 2):
        ac[lag] = sum(map(float.__mul__, e[:n - lag], e[lag:])) / (n - lag)
    return ac


def _at(ac, lag):
    i = int(lag)
    if i + 1 >= len(ac):
        return 0.0
    f = lag - i
    return ac[i] * (1 - f) + ac[i + 1] * f


class Profile:
    """How strongly a track's audio repeats at any tempo, and where its beats fall.

    start is where the listened-to window begins, in seconds into the track.
    """

    BEATS = (1, 2, 4)

    def __init__(self, env, start=0.0, kick=None, slowest=55.0):
        self.env, self.start, self.kick = env, start, kick or env
        self.max_lag = int(math.ceil(60 * FPS / slowest * max(self.BEATS))) + 1
        self.ac = _autocorr(env, self.max_lag)
        self.scale = max(self.ac[1:]) or 1.0

    def score(self, bpm):
        """Mean periodicity at 1, 2 and 4 beats of this tempo, 0..~1."""
        lag = 60 * FPS / bpm
        return sum(_at(self.ac, lag * k) for k in self.BEATS) / len(self.BEATS) / self.scale

    def best(self, lo, hi, step=0.1):
        """The strongest tempo in [lo, hi]."""
        n = int((hi - lo) / step)
        return max((lo + i * step for i in range(n + 1)), key=self.score)

    def on_beat(self, t, bpm):
        """How much onset energy lands on a grid of this tempo through time t (seconds).

        Used to choose between candidate downbeats a fraction of a beat apart,
        so a frame or two of slack is fine.
        """
        period = 60.0 / bpm
        # kick[i] is the rise into the frame starting at sample (i + 1) * HOP
        env = self.kick
        first = t + math.ceil((self.start - t) / period) * period
        total = count = 0
        x = first
        end = self.start + len(env) * HOP / RATE
        while x < end:
            i = int(round((x - self.start) * FPS)) - 1
            if 0 < i < len(env) - 1:
                total += max(env[i - 1:i + 2])
                count += 1
            x += period
        return total / count if count else 0.0


def profile(path, duration=0.0):
    pcm, start = decode(path, duration)
    env, kick = envelope(pcm)
    return Profile(env, start, kick)

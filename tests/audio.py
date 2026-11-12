"""Small audio files made on the spot, so the tests need no fixtures or tools.

WAV comes from the stdlib wave module. AIFF is written by hand: aifc left the
stdlib in Python 3.13, and the format is three short chunks.
"""
import math, struct, wave

import mutagen
from mutagen.id3 import ID3


def _samples(frames, channels, bits):
    """A quiet sine, the same every time, as signed ints."""
    peak = (1 << (bits - 1)) // 8
    for i in range(frames):
        v = int(peak * math.sin(2 * math.pi * 440 * i / 44100))
        for _ in range(channels):
            yield v


def write_wav(path, seconds=0.25, rate=44100, bits=16, channels=2):
    frames = int(seconds * rate)
    width = bits // 8
    data = b"".join(v.to_bytes(width, "little", signed=True)
                    for v in _samples(frames, channels, bits))
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def _extended(rate):
    """A sample rate as the 80-bit float AIFF's COMM chunk wants."""
    exp = int(math.floor(math.log2(rate)))
    return struct.pack(">HQ", 16383 + exp, int(rate * (1 << (63 - exp))))


def write_aiff(path, seconds=0.25, rate=44100, bits=16, channels=2):
    frames = int(seconds * rate)
    width = bits // 8
    data = b"".join(v.to_bytes(width, "big", signed=True)
                    for v in _samples(frames, channels, bits))
    comm = struct.pack(">hIh", channels, frames, bits) + _extended(rate)
    ssnd = struct.pack(">II", 0, 0) + data
    body = (b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm
            + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd + b"\0" * (len(ssnd) % 2))
    with open(path, "wb") as fh:
        fh.write(b"FORM" + struct.pack(">I", len(body)) + body)
    return path


def tag_chunk(path, *frames):
    """Tag an AIFF/WAV properly, inside the file, the way rekordbox and MIK do."""
    f = mutagen.File(path)
    if f.tags is None:
        f.add_tags()
    for fr in frames:
        f.tags.add(fr)
    f.save(v2_version=3)


def damage(path, *frames):
    """Do what Sortero's tag writer used to: glue a bare ID3 tag to the front."""
    tag = ID3()
    for fr in frames:
        tag.add(fr)
    tag.save(path, v2_version=3)


def chunk(path, cid):
    """The bytes of one top-level chunk (b"SSND", b"data") in an AIFF or WAV."""
    with open(path, "rb") as fh:
        raw = fh.read()
    big = raw[:4] == b"FORM"
    i = 12
    while i + 8 <= len(raw):
        size = struct.unpack(">I" if big else "<I", raw[i + 4:i + 8])[0]
        if raw[i:i + 4] == cid:
            return raw[i + 8:i + 8 + size]
        i += 8 + size + (size % 2)
    return None


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def drum_loop(bpm, seconds=60.0, kick=0.8, hats=0.35, hat_every=0.5, hat_offset=None,
              rate=11025, kick_every=1):
    """A techno-ish loop as mono 16-bit PCM: a kick each beat, hats every
    `hat_every` beats (0.5 offbeats, 1/3 rolling threes, 0.25 sixteenths)."""
    beat = 60.0 / bpm
    kick_shape = [math.sin(2 * math.pi * (55 + 90 * math.exp(-i / (0.01 * rate))) * i / rate)
                  * math.exp(-i / (0.04 * rate)) for i in range(int(0.12 * rate))]
    hat_shape, x = [], 12345
    for i in range(int(0.03 * rate)):           # noise burst, the same every time
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        hat_shape.append(((x / 0x3FFFFFFF) - 1) * math.exp(-i / (0.008 * rate)))
    n = int(seconds * rate)
    buf = [0.0] * n
    hat_offset = hat_every * beat / 2 if hat_offset is None else hat_offset
    for period, t, shape, gain in ((beat * kick_every, 0.0, kick_shape, kick),
                                   (beat * hat_every, hat_offset, hat_shape, hats)):
        while gain and t < seconds:
            s = int(t * rate)
            for i, v in enumerate(shape[:n - s]):
                buf[s + i] += gain * v
            t += period
    peak = max(abs(v) for v in buf) or 1.0
    return b"".join(struct.pack("<h", int(32000 * v / peak)) for v in buf)

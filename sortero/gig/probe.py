"""What an audio file really is, as far as a DJ player cares.

The extension says what a file claims to be. ffprobe says what's inside: a
".wav" can hold 32-bit float, an ".m4a" can be AAC or Apple Lossless, and an
AAC can be the HE flavour no Pioneer player lists.
"""
import json, os

from . import ffmpeg
from .profiles import AudioInfo

LOSSLESS = {"wav", "aiff", "flac", "alac"}


class ProbeError(Exception):
    pass


def _pcm_depth(codec):
    """(bits, is_float) from an ffmpeg PCM codec name such as pcm_s24le."""
    kind = codec.split("_")[1] if "_" in codec else ""
    digits = "".join(ch for ch in kind if ch.isdigit())
    return (int(digits) if digits else None), kind.startswith("f")


def from_ffprobe(data, path):
    """An AudioInfo from ffprobe's JSON. Split out so tests can feed it directly."""
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise ProbeError("there's no audio in it")
    s = streams[0]
    container = (data.get("format") or {}).get("format_name", "")
    codec = s.get("codec_name", "")
    ext = os.path.splitext(path)[1].lower()
    rate = int(s.get("sample_rate") or 0)
    raw_bits = int(s.get("bits_per_raw_sample") or 0) or None
    bit_rate = s.get("bit_rate") or (data.get("format") or {}).get("bit_rate")
    kbps = round(int(bit_rate) / 1000) if bit_rate else None
    extras = {"codec": codec, "container": container,
              "channels": int(s.get("channels") or 0),
              "duration": float(s.get("duration") or (data.get("format") or {})
                                .get("duration") or 0)}

    if codec.startswith("pcm_"):
        depth, is_float = _pcm_depth(codec)
        extras["float"] = is_float
        kind = "aiff" if "aiff" in container else "wav" if "wav" in container else codec
        return AudioInfo(kind, ext, rate, depth=depth, extras=extras)
    if codec in ("flac", "alac"):
        return AudioInfo(codec, ext, rate, depth=raw_bits, extras=extras)
    if codec == "mp3":
        return AudioInfo("mp3" if container == "mp3" else f"mp3 in {container}",
                         ext, rate, kbps=kbps, extras=extras)
    if codec == "aac":
        return AudioInfo("aac", ext, rate, kbps=kbps, aac_profile=s.get("profile"),
                         extras=extras)
    return AudioInfo(codec or "unknown", ext, rate, kbps=kbps, extras=extras)


def probe(path):
    r = ffmpeg.run([ffmpeg.require("ffprobe"), "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", path])
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ProbeError(msg[-1] if msg else "ffprobe couldn't read it")
    return from_ffprobe(json.loads(r.stdout or b"{}"), path)


def is_lossless(info):
    return info.format in LOSSLESS

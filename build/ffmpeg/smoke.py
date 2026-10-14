#!/usr/bin/env python3
"""Check an ffmpeg build does what Gig prep asks of it. Standard library only.

    python build/ffmpeg/smoke.py build/ffmpeg/out

Makes a 24-bit 96 kHz WAV, converts it to MP3 320 and to 16-bit AIFF at
44.1 kHz with the same options Gig prep uses (sortero/gig/convert.py), reads
both back with ffprobe, and runs the decode test sortero/gig/verify.py runs.
"""
import json, math, os, struct, subprocess, sys, tempfile, wave


def main(folder):
    exe = ".exe" if os.name == "nt" else ""
    ffmpeg, ffprobe = (os.path.join(folder, t + exe) for t in ("ffmpeg", "ffprobe"))
    conf = subprocess.run([ffmpeg, "-hide_banner", "-buildconf"], capture_output=True,
                          text=True, check=True).stdout
    for need in ("--enable-libmp3lame", "--enable-libsoxr"):
        assert need in conf, f"built without {need}"

    d = tempfile.mkdtemp()
    src = os.path.join(d, "hi-res.wav")
    rate = 96000
    with wave.open(src, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(rate * 2):
            v = int(2 ** 20 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<i", v)[:3] * 2
        w.writeframes(bytes(frames))

    def run(*args):
        r = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", *args], capture_output=True,
                           text=True)
        assert r.returncode == 0 and not r.stderr.strip(), r.stderr
    resample = "aresample=out_sample_rate=44100:resampler=soxr:precision=28"
    mp3, aiff = os.path.join(d, "out.mp3"), os.path.join(d, "out.aiff")
    run("-i", src, "-map", "0:a:0", "-map_metadata", "-1", "-vn",
        "-af", resample + ":out_sample_fmt=fltp", "-c:a", "libmp3lame", "-b:a", "320k",
        "-ar", "44100", "-id3v2_version", "0", "-write_id3v1", "0", "-f", "mp3", mp3)
    run("-i", src, "-map", "0:a:0", "-map_metadata", "-1", "-vn",
        "-af", resample + ":out_sample_fmt=s16:dither_method=triangular",
        "-c:a", "pcm_s16be", "-ar", "44100", "-write_id3v2", "0", "-f", "aiff", aiff)

    for path, codec in ((mp3, "mp3"), (aiff, "pcm_s16be")):
        r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_streams",
                            path], capture_output=True, text=True, check=True)
        s = json.loads(r.stdout)["streams"][0]
        assert (s["codec_name"], s["sample_rate"]) == (codec, "44100"), s
        run("-xerror", "-i", path, "-map", "0:a:0", "-f", "null", "-")
    print(f"ok: {ffmpeg} converts, resamples with soxr, and decodes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "out"))

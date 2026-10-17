#!/usr/bin/env python3
"""Check an ffmpeg build does what Gig prep asks of it. Standard library only.

    python build/ffmpeg/smoke.py build/ffmpeg/out

Makes a 24-bit 96 kHz WAV, converts it to MP3 320 and to 16-bit AIFF at
44.1 kHz with the same options Gig prep uses (sortero/gig/convert.py), reads
both back with ffprobe, runs the decode test sortero/gig/verify.py runs, and
decodes to an AIFF on a pipe the way the main stick's waveforms are made
(sortero/gig/waveform.py), and scales a cover the way its artwork is
(sortero/gig/artwork.py).
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
    # The waveforms' decode: mono 12 kHz AIFF to stdout (there's no raw-PCM muxer).
    r = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", mp3, "-vn", "-map_metadata",
                        "-1", "-ac", "1", "-ar", "12000", "-c:a", "pcm_s16be", "-write_id3v2",
                        "0", "-f", "aiff", "-"], capture_output=True)
    assert r.returncode == 0 and r.stdout[:4] == b"FORM" and b"SSND" in r.stdout[:200], r.stderr
    assert len(r.stdout) > 12000 * 2 * 1.9, len(r.stdout)        # two seconds of samples
    # The main stick's artwork: a cover scaled to 80 x 80 JPEG (sortero/gig/artwork.py).
    cover, small = os.path.join(d, "cover.jpg"), os.path.join(d, "a1.jpg")
    with open(cover, "wb") as fh:           # a 2 x 2 baseline JPEG, made here, no lavfi needed
        fh.write(bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d"
            "0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d3832"
            "3c2e333432ffc0000b080002000201011100ffc4001f0000010501010101010100000000000000000102"
            "030405060708090a0bffc400b5100002010303020403050504040000017d010203000411051221314106"
            "13516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a34353637"
            "38393a434445464748494a535455565758595a636465666768696a737475767778797a83848586878889"
            "8a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6"
            "d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd3ffd9"))
    run("-i", cover, "-vf", "scale=80:80", "-pix_fmt", "yuvj420p", "-q:v", "3", "-frames:v", "1",
        "-c:v", "mjpeg", "-f", "image2", small)
    with open(small, "rb") as fh:
        head = fh.read()
    assert head[:2] == b"\xff\xd8" and b"\x00\x50\x00\x50" in head[:400], "not an 80 x 80 JPEG"
    print(f"ok: {ffmpeg} converts, resamples with soxr, decodes, decodes for waveforms, "
          "and scales artwork")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "out"))

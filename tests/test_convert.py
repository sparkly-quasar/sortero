"""Gig-safe conversion: what gets converted, what it becomes, and what survives."""
import base64, hashlib, json, os, shutil, subprocess, unittest

from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, GEOB, ID3, TIT2, TKEY, TSSE, TXXX
from mutagen.mp3 import MP3, BitrateMode
from mutagen.mp4 import MP4, MP4FreeForm

from sortero.gig import convert as C, profiles as P, tagcopy
from sortero.gig.probe import probe
from tests.test_tagio import TempDir

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg and ffprobe")


def mik(obj):
    """A Mixed In Key blob as MIK writes it: base64 of JSON, wrapped at 72 characters."""
    b64 = base64.b64encode(json.dumps({**obj, "source": "mixedinkey"}).encode()).decode()
    return "".join(b64[i:i + 72] + "\n" for i in range(0, len(b64), 72))


CUES = mik({"algorithm": 14, "cues": [{"name": "Energy 5", "time": 8.87},
                                      {"name": "Energy 4", "time": 27437.53}]})
GRID = mik({"algorithm": 12, "tempo": 140.0, "beats": [0.00887, 0.43744]})


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def tone(path, rate=44100, codec_args=(), seconds=1):
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration={seconds}",
           "-ac", "2", *codec_args, path)
    return path


def image(path, px):
    ffmpeg("-f", "lavfi", "-i", f"color=c=red:s={px}x{px}", "-frames:v", "1", path)
    with open(path, "rb") as fh:
        return fh.read()


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@needs_ffmpeg
class Base(TempDir):
    def setUp(self):
        super().setUp()
        self.root = self.path("Collection")
        self.lib = self.path("Gig Library")
        os.makedirs(os.path.join(self.root, "Techno"))
        self.cache = C.Cache(self.path("cache.json"))

    def src(self, name):
        return os.path.join(self.root, "Techno", name)

    def go(self, sources, target=C.MP3, profile=P.UNIVERSAL):
        items = C.plan(self.root, sources, self.lib, profile, target)
        return C.run(items, profile, self.cache, log=lambda m: None, workers=2)

    def flac_96(self, name="Hi-res.flac"):
        p = tone(self.src(name), 96000, ("-c:a", "flac", "-sample_fmt", "s32"))
        f = FLAC(p)
        f["TITLE"], f["ARTIST"], f["INITIALKEY"], f["BPM"] = "Quasar", "Daryn", "Cm", "140"
        f["ENERGYLEVEL"], f["COMMENT"], f["LABEL"] = "5", "Cm - Energy 5", "Some Label"
        f["CUEPOINTS"], f["BEATGRID"], f["MOOD"] = CUES, GRID, "dark"
        pic = Picture()
        pic.type, pic.mime = 3, "image/png"
        pic.data = image(self.path("big.png"), 1200)
        f.add_picture(pic)
        f.save()
        return p


class Plan(Base):
    def test_what_needs_a_copy(self):
        ready_wav = tone(self.src("ok.wav"), codec_args=("-c:a", "pcm_s16le"))
        ready_mp3 = tone(self.src("ok.mp3"), codec_args=("-c:a", "libmp3lame", "-b:a", "320k"))
        flac = self.flac_96()
        float_wav = tone(self.src("float.wav"), codec_args=("-c:a", "pcm_f32le"))
        low_mp3 = tone(self.src("32k.mp3"), 32000, ("-c:a", "libmp3lame", "-b:a", "128k"))
        items = C.plan(self.root, [ready_wav, ready_mp3, flac, float_wav, low_mp3,
                                   self.src("gone.mp3")], self.lib, P.UNIVERSAL)
        status = {os.path.basename(it.src): it for it in items}
        self.assertEqual(status["ok.wav"].status, C.READY)
        self.assertEqual(status["ok.mp3"].status, C.READY)
        self.assertEqual(status["Hi-res.flac"].status, C.CONVERT)
        self.assertEqual(status["Hi-res.flac"].target, C.MP3)
        self.assertEqual(status["float.wav"].status, C.CONVERT)
        self.assertIn("32-bit", status["float.wav"].why[0])
        # already lossy: decoded to AIFF, never encoded lossy a second time
        self.assertEqual(status["32k.mp3"].target, C.AIFF)
        self.assertIn("lossy", status["32k.mp3"].notes[0])
        self.assertEqual(status["gone.mp3"].status, C.FAILED)

    def test_copies_mirror_the_collection_layout(self):
        flac = self.flac_96()
        [it] = C.plan(self.root, [flac], self.lib, P.UNIVERSAL)
        self.assertEqual(it.out, os.path.join(self.lib, "Techno", "Hi-res.mp3"))

    def test_same_name_different_format_doesnt_collide(self):
        a = tone(self.src("Track.flac"), codec_args=("-c:a", "flac"))
        b = tone(self.src("Track.wav"), 96000, ("-c:a", "pcm_s24le"))
        outs = {os.path.basename(it.out) for it in C.plan(self.root, [a, b], self.lib,
                                                            P.UNIVERSAL)}
        self.assertEqual(outs, {"Track (flac).mp3", "Track (wav).mp3"})

    def test_newer_player_needs_fewer_copies(self):
        flac = self.flac_96()
        [it] = C.plan(self.root, [flac], self.lib, P.get("cdj-3000"))
        self.assertEqual(it.status, C.READY)


class Library(unittest.TestCase):
    def test_must_be_outside_the_collection(self):
        with self.assertRaises(C.LibraryError):
            C.library_dir("/music/Collection", "/music/Collection/Gig")
        with self.assertRaises(C.LibraryError):
            C.library_dir("/music/Collection", "/music")
        self.assertEqual(C.library_dir("/music/Collection"),
                         os.path.normpath("/music/Collection - Gig Library"))


class Convert(Base):
    def test_flac_becomes_mp3_320_cbr_with_every_tag(self):
        flac = self.flac_96()
        before = sha(flac)
        [it] = self.go([flac])
        self.assertEqual(it.status, C.CONVERTED, it.why)
        self.assertEqual(sha(flac), before)                      # original untouched

        m = MP3(it.out)
        self.assertEqual((m.info.sample_rate, round(m.info.bitrate / 1000),
                          m.info.bitrate_mode), (44100, 320, BitrateMode.CBR))
        self.assertEqual(P.check_audio(P.UNIVERSAL, probe(it.out)), [])
        t = m.tags
        self.assertEqual(t.version[:2], (2, 3))
        self.assertEqual(str(t["TIT2"]), "Quasar")
        self.assertEqual(str(t["TPE1"]), "Daryn")
        self.assertEqual(str(t["TKEY"]), "Cm")
        self.assertEqual(str(t["TBPM"]), "140")
        self.assertEqual(str(t["TPUB"]), "Some Label")
        self.assertEqual(str(t["TXXX:EnergyLevel"]), "5")
        self.assertEqual(str(t["TXXX:MOOD"]), "dark")
        self.assertEqual(str(t.getall("COMM")[0]), "Cm - Energy 5")
        # Mixed In Key's cue points and beat grid, as the GEOB frames MIK uses
        self.assertEqual(t["GEOB:CuePoints"].data, CUES.encode())
        self.assertEqual(t["GEOB:BeatGrid"].data, GRID.encode())
        # 1200px PNG artwork became a JPEG no bigger than 800px
        art = t.getall("APIC")[0]
        self.assertEqual(art.mime, "image/jpeg")
        self.assertEqual(tagcopy.image_size(art.data)[:2], (800, 800))
        self.assertTrue(any("800×800" in n for n in it.notes))

    def test_id3_frames_carry_over_as_they_are(self):
        p = tone(self.src("Hi.aiff"), 96000, ("-c:a", "pcm_s24be"))
        f = AIFF(p)
        f.add_tags()
        geob = GEOB(encoding=3, mime="application/json", desc="CuePoints", data=CUES.encode())
        small_art = image(self.path("small.jpg"), 500)
        for fr in (TIT2(encoding=3, text=["T"]), TKEY(encoding=3, text=["8A"]), geob,
                   TXXX(encoding=3, desc="EnergyLevel", text=["7"]),
                   TSSE(encoding=3, text=["old encoder"]),
                   APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=small_art)):
            f.tags.add(fr)
        f.save(v2_version=3)
        [it] = self.go([p])
        t = ID3(it.out)
        self.assertEqual(t["GEOB:CuePoints"].data, CUES.encode())
        self.assertEqual(str(t["TXXX:EnergyLevel"]), "7")
        self.assertNotIn("TSSE", t)                         # described the old encode
        self.assertEqual(t.getall("APIC")[0].data, small_art)   # small JPEG kept as is
        self.assertEqual(it.notes, [])

    def test_apple_lossless_tags_translate(self):
        p = tone(self.src("Al.m4a"), 48000, ("-c:a", "alac", "-sample_fmt", "s32p"))
        m = MP4(p)
        m["\xa9nam"], m["\xa9ART"], m["tmpo"] = ["Song"], ["Artist"], [124]
        m["----:com.apple.iTunes:initialkey"] = [MP4FreeForm(b"8A")]
        m["----:com.mixedinkey.mixedinkey:cuepoints"] = [MP4FreeForm(CUES.encode())]
        m.save()
        [it] = self.go([p])
        t = ID3(it.out)
        self.assertEqual((str(t["TIT2"]), str(t["TPE1"]), str(t["TBPM"]), str(t["TKEY"])),
                         ("Song", "Artist", "124", "8A"))
        self.assertEqual(t["GEOB:CuePoints"].data, CUES.encode())

    def test_aiff_target_is_16_bit_44k_with_tags_inside(self):
        flac = self.flac_96()
        [it] = self.go([flac], target=C.AIFF)
        self.assertEqual(it.status, C.CONVERTED, it.why)
        with open(it.out, "rb") as fh:
            self.assertEqual(fh.read(4), b"FORM")
        info = probe(it.out)
        self.assertEqual((info.format, info.rate, info.depth), ("aiff", 44100, 16))
        self.assertEqual(str(AIFF(it.out).tags["TIT2"]), "Quasar")

    def test_lossy_source_is_decoded_to_aiff(self):
        p = tone(self.src("32k.mp3"), 32000, ("-c:a", "libmp3lame", "-b:a", "128k"))
        [it] = self.go([p])
        self.assertEqual(it.status, C.CONVERTED, it.why)
        self.assertTrue(it.out.endswith(".aiff"))
        self.assertEqual(P.check_audio(P.UNIVERSAL, probe(it.out)), [])

    def test_ready_files_are_not_copied(self):
        p = tone(self.src("ok.mp3"), codec_args=("-c:a", "libmp3lame", "-b:a", "320k"))
        [it] = self.go([p])
        self.assertEqual((it.status, it.playable), (C.READY, p))
        self.assertFalse(os.path.exists(self.lib))

    def test_broken_file_fails_cleanly(self):
        p = self.src("broken.flac")
        with open(p, "wb") as fh:
            fh.write(b"fLaC" + b"\0" * 2000)
        items = C.plan(self.root, [p], self.lib, P.UNIVERSAL)
        if items[0].status == C.CONVERT:          # some ffprobes accept the header
            C.run(items, P.UNIVERSAL, self.cache, log=lambda m: None)
        self.assertEqual(items[0].status, C.FAILED)
        self.assertTrue(items[0].why)
        left = []
        for d, _, files in os.walk(self.path("")):
            left += [f for f in files if f.startswith(".sortero-tmp")]
        self.assertEqual(left, [])


class Cached(Base):
    def test_second_run_reuses_the_copy(self):
        flac = self.flac_96()
        [first] = self.go([flac])
        stamp = os.stat(first.out).st_mtime_ns
        [again] = self.go([flac])
        self.assertEqual(again.status, C.REUSED)
        self.assertEqual(os.stat(again.out).st_mtime_ns, stamp)
        self.assertTrue(any("800×800" in n for n in again.notes))

    def test_moved_original_is_not_converted_again(self):
        flac = self.flac_96()
        [first] = self.go([flac])
        os.makedirs(os.path.join(self.root, "House"))
        moved = os.path.join(self.root, "House", "Renamed.flac")
        shutil.move(flac, moved)
        [again] = self.go([moved])
        self.assertEqual(again.status, C.REUSED)
        self.assertEqual(again.out, os.path.join(self.lib, "House", "Renamed.mp3"))
        self.assertEqual(sha(again.out), sha(first.out))

    def test_changed_audio_is_converted_again(self):
        flac = self.flac_96()
        self.go([flac])
        tone(flac, 96000, ("-c:a", "flac", "-sample_fmt", "s32"), seconds=2)
        [again] = self.go([flac])
        self.assertEqual(again.status, C.CONVERTED)

    def test_edited_copy_is_not_trusted(self):
        flac = self.flac_96()
        [first] = self.go([flac])
        with open(first.out, "ab") as fh:
            fh.write(b"\0")
        [again] = self.go([flac])
        self.assertEqual(again.status, C.CONVERTED)


class Report(Base):
    def test_report_counts_and_reasons(self):
        ok = tone(self.src("ok.mp3"), codec_args=("-c:a", "libmp3lame", "-b:a", "320k"))
        items = self.go([ok, self.flac_96(), self.src("gone.mp3")])
        lines = C.report_lines(items)
        self.assertEqual(lines[0], "1 already play as they are · 1 converted · "
                                   "0 reused from last time · 1 failed")
        self.assertTrue(any(l.startswith("FAILED") and "gone.mp3" in l for l in lines))
        self.assertTrue(any("FLAC" in l for l in lines))


class FromFfprobe(unittest.TestCase):
    """Probe mapping, from ffprobe output alone: no ffmpeg needed."""

    def info(self, codec, container, **stream):
        from sortero.gig.probe import from_ffprobe
        data = {"streams": [{"codec_type": "audio", "codec_name": codec,
                             "sample_rate": "44100", **stream}],
                "format": {"format_name": container}}
        return from_ffprobe(data, "x.ext")

    def test_mapping(self):
        self.assertEqual(self.info("pcm_s24le", "wav").depth, 24)
        f = self.info("pcm_f32le", "wav")
        self.assertEqual((f.format, f.depth, f.extras["float"]), ("wav", 32, True))
        self.assertEqual(self.info("pcm_s16be", "aiff").format, "aiff")
        self.assertEqual(self.info("aac", "mov,mp4,m4a", profile="HE-AAC").aac_profile,
                         "HE-AAC")
        self.assertEqual(self.info("alac", "mov,mp4,m4a", bits_per_raw_sample="24").depth, 24)
        self.assertEqual(self.info("mp3", "mp3", bit_rate="320000").kbps, 320)
        self.assertEqual(self.info("mp3", "wav").format, "mp3 in wav")


if __name__ == "__main__":
    unittest.main()

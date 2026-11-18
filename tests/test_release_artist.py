"""Clean tags: an artist tag holding a label, a placeholder or a release's artist
list, with the real artist in the title. Built from a real collection's releases."""
import os, shutil, subprocess, unittest
from unittest import mock

from mutagen.id3 import ID3, TPE1, TIT2, TALB

from sortero import fixtags, library, session, settings, supporter, tagcache
from sortero.fixtags import (LABEL, PLACEHOLDER_ARTIST, LISTED, ALBUM_NAME, SAME_RELEASE,
                             VARIOUS, release_credits, release_changes)
from sortero.journal import revert
from sortero.library import Rec
from sortero.tagio import Track, RELEASE_FIELDS
from tests import audio
from tests.test_tagio import TempDir


def rec(folder, name, artist, title, album=None, **kw):
    return Rec(path=f"/m/{folder}/{name}.mp3", rel=f"{folder}/{name}.mp3",
               artist=artist, title=title, album=album, **kw)


def rayzeh_comp():
    """A Bandcamp label download: the label in the artist tag and the filename."""
    album = "Rayzeh Selects 004"
    return [rec("Psytrance", f"Rayzeh - {album} - 0{n} {t}", "Rayzeh", t, album)
            for n, t in ((1, "RamouQ - Shifty Distant"), (2, "Paul Ursin - Atlas"),
                         (3, "Ascetic Mind - Dorje (Original Mix)"))]


def by_path(changes):
    return {r.path: ch for r, ch in changes}


def kinds(credits):
    return {os.path.basename(p): c.kind for p, c in credits.items()}


class LabelInTheArtistTag(unittest.TestCase):
    def test_rayzeh_compilation(self):
        recs = rayzeh_comp()
        got = release_credits(recs)
        self.assertEqual(set(kinds(got).values()), {LABEL})
        c = got[recs[0].path]
        self.assertEqual((c.artist, c.title, c.albumartist, c.compilation, c.label, c.sure),
                         ("RamouQ", "Shifty Distant", VARIOUS, True, "Rayzeh", True))
        self.assertEqual(got[recs[2].path].title, "Dorje (Original Mix)")

    def test_the_fix_keeps_the_label_and_makes_one_album(self):
        recs = rayzeh_comp()
        changes = by_path(release_changes(recs, release_credits(recs)))
        self.assertEqual(changes[recs[0].path], {
            "artist": ("Rayzeh", "RamouQ"), "title": ("RamouQ - Shifty Distant",
                                                      "Shifty Distant"),
            "albumartist": (None, VARIOUS), "compilation": (None, "1"),
            "label": (None, "Rayzeh"),
            # from the Bandcamp filename, "Label - Album - 01 Artist - Title"
            "tracknumber": (None, "1")})

    def test_further_sessions_with_an_uncredited_track(self):
        album = "Fur:ther Sessions Vol. 2"
        recs = [rec("Techno", "a", "Fur:ther Sessions", "Abstract Division - Altered", album),
                rec("Techno", "b", "Fur:ther Sessions", "Mhaol - Stillness", album),
                rec("Techno", "c", "Fur:ther Sessions", "Untitled", album)]
        got = release_credits(recs)
        self.assertEqual(kinds(got), {"a.mp3": LABEL, "b.mp3": LABEL, "c.mp3": SAME_RELEASE})
        c = got[recs[2].path]
        self.assertEqual((c.artist, c.albumartist, c.compilation, c.label),
                         (None, VARIOUS, True, "Fur:ther Sessions"))

    def test_one_track_is_enough_once_the_label_is_known(self):
        album = "Rayzeh Selects 004"
        left = rec("Psytrance", "x", "Rayzeh", "RamouQ - Shifty Distant", album)
        self.assertEqual(release_credits([left]), {})
        fixed = rec("Psytrance", "y", "Paul Ursin", "Atlas", album, label="Rayzeh",
                    albumartist=VARIOUS, compilation="1")
        self.assertEqual(kinds(release_credits([left, fixed])), {"x.mp3": LABEL})

    def test_haze_c_is_for_you_to_pick(self):
        album = "Rayzeh Selects 005"
        recs = [rec("Psytrance", "a", "Rayzeh", "Haze - C - Can't Sleep", album),
                rec("Psytrance", "b", "Rayzeh", "RamouQ - Kinetic", album)]
        credits = release_credits(recs)
        c = credits[recs[0].path]
        self.assertTrue(c.ambiguous)
        self.assertFalse(c.sure)
        self.assertEqual(c.splits, (("Haze", "C - Can't Sleep"), ("Haze - C", "Can't Sleep")))
        self.assertTrue(credits[recs[1].path].sure)

        # not picked: its release still gets the album artist, the names stay
        ch = by_path(release_changes(recs[:1], credits))[recs[0].path]
        self.assertNotIn("artist", ch)
        self.assertNotIn("title", ch)
        self.assertEqual(ch["albumartist"], (None, VARIOUS))
        ch = by_path(release_changes(recs[:1], credits,
                                  {recs[0].path: ("Haze - C", "Can't Sleep")}))[recs[0].path]
        self.assertEqual((ch["artist"], ch["title"]),
                         (("Rayzeh", "Haze - C"), ("Haze - C - Can't Sleep", "Can't Sleep")))


class Placeholders(unittest.TestCase):
    def test_various_artists_traumatismo(self):
        r = rec("Psytrance", "a", "Various Artists", "Kaiser Souzai - Traumatismo",
                "Traumatismo")
        c = release_credits([r])[r.path]
        self.assertEqual((c.kind, c.artist, c.title, c.albumartist, c.compilation, c.label),
                         (PLACEHOLDER_ARTIST, "Kaiser Souzai", "Traumatismo", VARIOUS, True,
                          None))
        self.assertNotIn("label", by_path(release_changes([r], {r.path: c}))[r.path])

    def test_every_spelling_needs_only_one_track(self):
        for name in ("VA", "V/A", "V.A.", "Compilation", "Unknown Artist", "various artists"):
            with self.subTest(name=name):
                r = rec("House", "a", name, "Chaos In The CBD - Midnight In Peckham")
                self.assertEqual(kinds(release_credits([r])), {"a.mp3": PLACEHOLDER_ARTIST})


class ArtistLists(unittest.TestCase):
    ARTISTS = "Polygonia / Dycide / Latmos / Save Your Atoll"

    def test_vivendum_every_track_gets_the_list(self):
        recs = [rec("Techno", "1", self.ARTISTS, "Dycide - Inner Space", "Vivendum"),
                rec("Techno", "2", self.ARTISTS, "Polygonia - Kinetic", "Vivendum"),
                # already credited to one artist: left out, Navidrome split the album
                rec("Techno", "3", "Latmos", "Ostara", "Vivendum"),
                # the same artist on another release
                rec("Techno", "4", "Latmos", "Other", "Something Else")]
        got = release_credits(recs)
        self.assertEqual(kinds(got), {"1.mp3": LISTED, "2.mp3": LISTED, "3.mp3": SAME_RELEASE})
        self.assertEqual((got[recs[0].path].artist, got[recs[0].path].title),
                         ("Dycide", "Inner Space"))
        self.assertEqual({c.albumartist for c in got.values()}, {self.ARTISTS})
        self.assertFalse(any(c.compilation or c.label for c in got.values()))
        self.assertEqual(by_path(release_changes(recs, got))[recs[2].path],
                         {"albumartist": (None, self.ARTISTS)})

    def test_already_right_isnt_listed(self):
        r = rec("Techno", "3", "Latmos", "Ostara", "Vivendum", albumartist=self.ARTISTS)
        other = rec("Techno", "1", self.ARTISTS, "Dycide - Inner Space", "Vivendum")
        self.assertEqual(kinds(release_credits([r, other])), {"1.mp3": LISTED})

    def test_an_uncredited_track_of_a_listed_release(self):
        # the album naming one of the artists is no reason to call the list a label
        recs = [rec("Techno", "1", self.ARTISTS, "Dycide - Inner Space",
                    "Latmos - Vivendum [VIV001]"),
                rec("Techno", "2", self.ARTISTS, "Outro", "Latmos - Vivendum [VIV001]")]
        got = release_credits(recs)
        self.assertEqual(kinds(got), {"1.mp3": LISTED, "2.mp3": SAME_RELEASE})
        self.assertEqual(got[recs[1].path].albumartist, self.ARTISTS)
        self.assertIsNone(got[recs[1].path].label)

    def test_a_comma_list(self):
        r = rec("Techno", "a", "Dycide, Latmos", "Latmos - Ostara", "Vivendum")
        self.assertEqual(kinds(release_credits([r])), {"a.mp3": LISTED})


class TheAlbumNamesTheArtist(unittest.TestCase):
    def test_dojas_ep_with_a_catalogue_number(self):
        album = "DOJAS - Pulsar EP [RZH018]"
        recs = [rec("Psytrance", "a", "Rayzeh", "Pulsar", album),
                rec("Psytrance", "b", "Rayzeh", "Nebula", album)]
        got = release_credits(recs)
        self.assertEqual(set(kinds(got).values()), {ALBUM_NAME})
        c = got[recs[0].path]
        self.assertEqual((c.artist, c.title, c.albumartist, c.label, c.sure),
                         ("DOJAS", None, "DOJAS", "Rayzeh", False))
        self.assertEqual(by_path(release_changes(recs[:1], got))[recs[0].path],
                         {"artist": ("Rayzeh", "DOJAS"), "albumartist": (None, "DOJAS"),
                          "label": (None, "Rayzeh")})

    def test_or_the_labels_bandcamp_page(self):
        r = rec("Psytrance", "a", "Rayzeh", "Pulsar", "DOJAS - Pulsar EP",
                comment="Visit https://rayzeh.bandcamp.com")
        self.assertEqual(kinds(release_credits([r])), {"a.mp3": ALBUM_NAME})

    def test_with_neither_its_left_alone(self):
        for comment in (None, "Visit https://dojas.bandcamp.com"):
            with self.subTest(comment=comment):
                r = rec("Psytrance", "a", "Rayzeh", "Pulsar", "DOJAS - Pulsar EP",
                        comment=comment)
                self.assertEqual(release_credits([r]), {})


class LeftAlone(unittest.TestCase):
    def test_not_matched(self):
        cases = {
            # a version after the dash, on every track
            "versions": [rec("House", "a", "Andy Bach", "Ipanema - Extended Mix", "Ipanema"),
                         rec("House", "b", "Andy Bach", "Copacabana - Extended Mix",
                             "Ipanema")],
            # a real artist's album
            "soundstream": [rec("House", "a", "Soundstream", "Live Your Life", "Soundstream"),
                            rec("House", "b", "Soundstream", "Tear Drops", "Soundstream"),
                            rec("House", "c", "Soundstream", "Soundstream - Live Your Life",
                                "Soundstream")],
            # a split EP crediting each side: lower priority, so not flagged
            "split EP": [rec("Techno", "a", "Kölsch & Tale Of Us", "Kölsch - Grey",
                             "Split EP"),
                         rec("Techno", "b", "Kölsch & Tale Of Us", "Tale Of Us - Dark",
                             "Split EP")],
            # one track naming someone else is the "names another artist" list's
            "one other": [rec("House", "a", "Dalal", "Hraach - Una Mattina", "Mattina"),
                          rec("House", "b", "Dalal", "Sunrise", "Mattina")],
            # the tagged artist and a collaborator: the "repeat" fix's
            "more": [rec("House", "a", "Djuma Soundsystem",
                         "Djuma Soundsystem & Westerby - Koma", "Koma"),
                     rec("House", "b", "Djuma Soundsystem", "Westerby - Lento", "Koma")],
            # parts of one work
            "parts": [rec("Ambient", "a", "Steve Roach", "Part 1 - The Beginning", "Suite"),
                      rec("Ambient", "b", "Steve Roach", "Part 2 - The End", "Suite")],
            # different heads but no album to say they're one release
            "no album": [rec("House", "a", "Some DJ", "Alpha - Beta"),
                         rec("House", "b", "Some DJ", "Gamma - Delta")],
        }
        for name, recs in cases.items():
            with self.subTest(name):
                self.assertEqual(release_credits(recs), {})

    def test_protected_tracks_are_left_out(self):
        recs = rayzeh_comp()
        recs[0].protected = True
        self.assertNotIn(recs[0].path, release_credits(recs))

    def test_leave_as_they_are(self):
        settings.set(fixtags.RELEASE_LEFT_ALONE, [])
        self.addCleanup(fixtags.leave_release_artist, [], True)
        self.addCleanup(settings.set, fixtags.RELEASE_LEFT_ALONE, [])
        recs = rayzeh_comp()
        self.assertEqual(len(fixtags.release_artist_flagged(recs)), 3)
        fixtags.leave_release_artist(recs[:1])
        flagged = fixtags.release_artist_flagged(recs)
        self.assertEqual(len(flagged), 2)
        self.assertNotIn(recs[0].path, flagged)
        fixtags.leave_release_artist(recs[:1], on=False)
        self.assertEqual(len(fixtags.release_artist_flagged(recs)), 3)


class ReleaseFields(TempDir):
    """Album artist, label, compilation and track number, in every format."""
    VALUES = {"albumartist": "Various Artists", "label": "Rayzeh", "compilation": "1",
              "tracknumber": "3/12"}

    def files(self):
        p = self.path("t.mp3")
        ID3().save(p)
        yield p
        yield audio.write_aiff(self.path("t.aiff"))
        yield audio.write_wav(self.path("t.wav"))
        if shutil.which("ffmpeg"):
            for ext in (".flac", ".m4a", ".opus"):
                p = self.path("t" + ext)
                subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi",
                                "-i", "sine=d=0.3", p], check=True)
                yield p

    def test_written_read_back_and_cleared(self):
        for p in self.files():
            with self.subTest(ext=os.path.splitext(p)[1]):
                t = Track(p)
                self.assertTrue(t.ok)
                self.assertEqual({f: t.get(f) for f in RELEASE_FIELDS},
                                 dict.fromkeys(RELEASE_FIELDS))
                for f, v in self.VALUES.items():
                    t.set(f, v)
                self.assertTrue(t.save())
                t = Track(p)
                self.assertEqual({f: t.get(f) for f in RELEASE_FIELDS}, self.VALUES)
                for f in RELEASE_FIELDS:
                    t.set(f, None)
                self.assertTrue(t.save())
                t = Track(p)
                self.assertEqual({f: t.get(f) for f in RELEASE_FIELDS},
                                 dict.fromkeys(RELEASE_FIELDS))

    def test_compilation_zero_isnt_one(self):
        p = self.path("t.mp3")
        ID3().save(p)
        t = Track(p)
        t.set("compilation", "0")
        t.save()
        self.assertIsNone(Track(p).get("compilation"))


class WriteAndUndo(TempDir):
    """The whole fix on real files: names and release fields written, files
    never renamed, and History puts every tag back."""

    def setUp(self):
        super().setUp()
        tagcache.clear()
        self.addCleanup(tagcache.clear)
        for target, value in ((session, "record"), (supporter, "count")):
            p = mock.patch.object(target, value, lambda *a: None)
            p.start()
            self.addCleanup(p.stop)
        self.root = self.path("Music")
        folder = os.path.join(self.root, "Psytrance")
        os.makedirs(folder)
        self.names = []
        for n, title in ((1, "RamouQ - Shifty Distant"), (2, "Paul Ursin - Atlas")):
            name = f"Rayzeh - Rayzeh Selects 004 - 0{n} {title}.mp3"
            tags = ID3()
            tags.add(TPE1(encoding=3, text=["Rayzeh"]))
            tags.add(TIT2(encoding=3, text=[title]))
            tags.add(TALB(encoding=3, text=["Rayzeh Selects 004"]))
            tags.save(os.path.join(folder, name))
            self.names.append(name)

    def test_fix_then_undo(self):
        recs = library.scan(self.root)
        changes = release_changes(recs, release_credits(recs))
        self.assertEqual(len(changes), 2)
        path, n, failed = fixtags.apply(self.root, changes, log=lambda m: None,
                                        kind="release-artist")
        self.assertEqual((n, failed), (2, 0))
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "Psytrance"))),
                         sorted(self.names))
        t = Track(os.path.join(self.root, "Psytrance", self.names[0]))
        self.assertEqual({f: t.get(f) for f in ("artist", "title", *RELEASE_FIELDS)},
                         {"artist": "RamouQ", "title": "Shifty Distant",
                          "albumartist": VARIOUS, "label": "Rayzeh", "compilation": "1",
                          "tracknumber": "1"})
        again = library.scan(self.root)
        self.assertEqual(again[0].albumartist, VARIOUS)
        self.assertEqual(release_credits(again), {})

        revert(path, log=lambda m: None)
        t = Track(os.path.join(self.root, "Psytrance", self.names[0]))
        self.assertEqual({f: t.get(f) for f in ("artist", "title", *RELEASE_FIELDS)},
                         {"artist": "Rayzeh", "title": "RamouQ - Shifty Distant",
                          "albumartist": None, "label": None, "compilation": None,
                          "tracknumber": None})

    def test_undo_restores_what_the_file_had(self):
        # an album artist the scan didn't see (an old cache entry, say) comes back
        p = os.path.join(self.root, "Psytrance", self.names[0])
        recs = library.scan(self.root)
        t = Track(p)
        t.set("albumartist", "Rayzeh")
        t.save()
        path, _, _ = fixtags.apply(self.root, release_changes(recs, release_credits(recs)),
                                   log=lambda m: None)
        revert(path, log=lambda m: None)
        self.assertEqual(Track(p).get("albumartist"), "Rayzeh")


if __name__ == "__main__":
    unittest.main()

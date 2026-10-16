"""A rescan reads only files that changed, and never serves stale tags."""
import json, os, shutil, sys, unittest

from mutagen.id3 import ID3, TPE1, TIT2

from sortero import library, tagcache
from tests.test_tagio import TempDir


def tag(path, artist, title):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TIT2(encoding=3, text=[title]))
    tags.save(path)


class Rescans(TempDir):
    def setUp(self):
        super().setUp()
        tagcache.clear()
        self.addCleanup(tagcache.clear)
        self.root = self.path("Music")
        self.a = os.path.join(self.root, "Techno", "A - One.mp3")
        self.b = os.path.join(self.root, "Techno", "B - Two.mp3")
        tag(self.a, "A", "One")
        tag(self.b, "B", "Two")
        self.reads = []
        real = library._read
        library._read = lambda p: (self.reads.append(p), real(p))[1]
        self.addCleanup(setattr, library, "_read", real)

    def scan(self):
        self.reads.clear()
        return {os.path.relpath(r.path, self.root): (r.artist, r.title)
                for r in library.scan(self.root, order="artist-title")}

    def test_a_second_scan_reads_nothing(self):
        first = self.scan()
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(self.scan(), first)
        self.assertEqual(self.reads, [])

    def test_a_changed_file_is_read_again(self):
        self.scan()
        tag(self.a, "A", "One (Extended Mix)")
        got = self.scan()
        self.assertEqual(self.reads, [self.a])
        self.assertEqual(got[os.path.join("Techno", "A - One.mp3")], ("A", "One (Extended Mix)"))

    @unittest.skipIf(sys.platform.startswith("win"), "Windows has no change time")
    def test_a_tagger_that_keeps_the_dates_is_still_caught(self):
        self.scan()
        st = os.stat(self.a)
        tag(self.a, "Q", "One")                       # same size as before
        os.utime(self.a, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(os.path.getsize(self.a), st.st_size)
        got = self.scan()
        self.assertEqual(got[os.path.join("Techno", "A - One.mp3")], ("Q", "One"))

    def test_a_moved_file_is_not_read_again(self):
        self.scan()
        moved = os.path.join(self.root, "House", "A - One.mp3")
        os.makedirs(os.path.dirname(moved))
        shutil.move(self.a, moved)
        got = self.scan()
        self.assertEqual(self.reads, [])
        self.assertEqual(got[os.path.join("House", "A - One.mp3")], ("A", "One"))
        self.assertNotIn(os.path.join("Techno", "A - One.mp3"), got)

    def test_gone_files_are_forgotten_but_other_collections_kept(self):
        other = self.path("Other")
        tag(os.path.join(other, "C - Three.mp3"), "C", "Three")
        library.scan(other)
        self.scan()
        os.remove(self.b)
        self.scan()
        with open(tagcache._file()) as fh:
            kept = set(json.load(fh)["entries"])
        self.assertEqual(kept, {self.a, os.path.join(other, "C - Three.mp3")})

    def test_a_new_version_starts_afresh(self):
        self.scan()
        with open(tagcache._file()) as fh:
            data = json.load(fh)
        data["version"] = "0.0.1"
        with open(tagcache._file(), "w") as fh:
            json.dump(data, fh)
        tagcache._entries = None                      # as when Sortero starts
        self.scan()
        self.assertEqual(len(self.reads), 2)

    def test_reread_everything(self):
        self.scan()
        tagcache.clear()
        self.scan()
        self.assertEqual(len(self.reads), 2)

    def test_same_records_as_reading_every_file(self):
        cached = [vars(r) for r in library.scan(self.root)]
        tagcache.clear()
        fresh = [vars(r) for r in library.scan(self.root)]
        self.assertEqual(cached, fresh)


if __name__ == "__main__":
    unittest.main()

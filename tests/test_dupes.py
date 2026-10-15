"""Setting duplicates aside: one copy stays, and the sets it was in keep it."""
import os
from unittest import mock

from sortero import dupes, journal, paths, session
from sortero.library import Rec, is_protected
from tests.test_tagio import TempDir


class OneFolder(TempDir):
    def setUp(self):
        super().setUp()
        self.root = self.path("Collection")
        os.makedirs(self.root)
        jdir = self.path("journals")
        os.makedirs(jdir)
        for target, value in ((paths, "journals_dir"), (journal.paths, "journals_dir")):
            p = mock.patch.object(target, value, lambda: jdir)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(session, "record", lambda *a: None)
        p.start()
        self.addCleanup(p.stop)

    def track(self, rel, ext_bytes=b"audio", duration=300, title=None):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(ext_bytes)
        stem = os.path.splitext(os.path.basename(rel))[0]
        return Rec(path=p, rel=rel, size=len(ext_bytes), ext=os.path.splitext(rel)[1],
                   artist="Pig & Dan", title=title or stem, duration=duration,
                   protected=is_protected(rel))

    def playlist(self, name):
        fp = os.path.join(self.root, "_Playlists", name + ".m3u8")
        with open(fp, encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip() and not l.startswith("#")]

    def snapshot(self):
        out = {}
        for d, _, files in os.walk(self.root):
            for f in files:
                p = os.path.join(d, f)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, self.root)] = fh.read()
        return out

    def test_exact_pair_keeps_the_genre_copy_and_the_set_gets_a_playlist(self):
        first = self.track("Sets/Burn/01 Alpha.mp3", title="Alpha")
        dup = self.track("Sets/Burn/02 Madness.mp3", title="Madness")
        last = self.track("Sets/Burn/03 Zeta.mp3", title="Zeta")
        filed = self.track("Techno/Comp/02 Madness.mp3", title="Madness")
        recs = [first, dup, last, filed]
        group = [dup, filed]
        self.assertIs(dupes.keeper(group), filed)

        _, n = dupes.quarantine(self.root, [group], log=lambda m: None,
                                recs=recs, one_folder=True)
        self.assertEqual(n, 1)
        self.assertFalse(os.path.exists(dup.path))
        self.assertTrue(os.path.exists(filed.path))
        self.assertEqual(self.playlist("Sets - Burn"), [
            "../Sets/Burn/01 Alpha.mp3", "../Techno/Comp/02 Madness.mp3",
            "../Sets/Burn/03 Zeta.mp3"])

    def test_better_copy_in_a_set_moves_into_the_genre_folder(self):
        flac = self.track("Sets/Burn/Madness.flac", b"lossless audio")
        mp3 = self.track("Techno/Madness.mp3", b"lossy")
        _, n = dupes.quarantine(self.root, [[flac, mp3]], log=lambda m: None,
                                recs=[flac, mp3], one_folder=True)
        self.assertEqual(n, 1)
        moved = os.path.join(self.root, "Techno", "Madness.flac")
        self.assertTrue(os.path.exists(moved))
        self.assertFalse(os.path.exists(flac.path))
        self.assertFalse(os.path.exists(mp3.path))
        self.assertEqual(self.playlist("Sets - Burn"), ["../Techno/Madness.flac"])

    def test_without_one_folder_nothing_moves_and_no_playlist_is_made(self):
        flac = self.track("Sets/Burn/Madness.flac", b"lossless audio")
        mp3 = self.track("Techno/Madness.mp3", b"lossy")
        dupes.quarantine(self.root, [[flac, mp3]], log=lambda m: None,
                         recs=[flac, mp3], one_folder=False)
        self.assertTrue(os.path.exists(flac.path))
        self.assertFalse(os.path.exists(os.path.join(self.root, "_Playlists",
                                                     "Sets - Burn.m3u8")))

    def test_existing_playlists_follow_the_copy_that_stays(self):
        dup = self.track("Sets/Burn/Madness.mp3")
        filed = self.track("Techno/Madness.mp3")
        pl = os.path.join(self.root, "_Playlists", "Friday.m3u8")
        os.makedirs(os.path.dirname(pl))
        with open(pl, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n../Sets/Burn/Madness.mp3\n")
        # Even the plain set-aside, as the setup guide uses it, repoints playlists.
        dupes.quarantine(self.root, [[dup, filed]], log=lambda m: None)
        self.assertEqual(self.playlist("Friday"), ["../Techno/Madness.mp3"])

    def test_an_existing_set_playlist_is_merged_not_replaced(self):
        dup = self.track("Sets/Burn/Madness.mp3")
        other = self.track("Sets/Burn/Other.mp3", title="Other")
        filed = self.track("Techno/Madness.mp3")
        pl = os.path.join(self.root, "_Playlists", "Sets - Burn.m3u8")
        os.makedirs(os.path.dirname(pl))
        with open(pl, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n../Techno/Elsewhere.mp3\n../Sets/Burn/Madness.mp3\n")
        dupes.quarantine(self.root, [[dup, filed]], log=lambda m: None,
                         recs=[dup, other, filed], one_folder=True)
        self.assertEqual(self.playlist("Sets - Burn"), [
            "../Techno/Elsewhere.mp3", "../Techno/Madness.mp3", "../Sets/Burn/Other.mp3"])

    def test_a_picked_copy_is_kept_instead_of_the_best_one(self):
        flac = self.track("Techno/Madness.flac", b"lossless audio")
        mp3 = self.track("House/Madness.mp3", b"lossy")
        self.assertIs(dupes.keeper([flac, mp3]), flac)
        self.assertIs(dupes.keeper([flac, mp3], {mp3.path}), mp3)
        _, n = dupes.quarantine(self.root, [[flac, mp3]], log=lambda m: None,
                                recs=[flac, mp3], one_folder=True, chosen={mp3.path})
        self.assertEqual(n, 1)
        self.assertTrue(os.path.exists(mp3.path))
        self.assertFalse(os.path.exists(flac.path))

    def test_a_copy_picked_from_a_set_still_moves_to_the_genre_folder(self):
        filed = self.track("Techno/Madness.flac", b"lossless audio")
        in_set = self.track("Sets/Burn/Madness.mp3", b"lossy")
        _, n = dupes.quarantine(self.root, [[filed, in_set]], log=lambda m: None,
                                recs=[filed, in_set], one_folder=True,
                                chosen={in_set.path})
        self.assertEqual(n, 1)
        kept = os.path.join(self.root, "Techno", "Madness.mp3")
        self.assertTrue(os.path.exists(kept))
        self.assertFalse(os.path.exists(filed.path))
        self.assertFalse(os.path.exists(in_set.path))
        self.assertEqual(self.playlist("Sets - Burn"), ["../Techno/Madness.mp3"])

    def test_a_pick_only_affects_its_own_group(self):
        a1 = self.track("Techno/A.flac", b"lossless a", title="A")
        a2 = self.track("House/A.mp3", b"a", title="A")
        b1 = self.track("Techno/B.flac", b"lossless b", title="B")
        b2 = self.track("House/B.mp3", b"b", title="B")
        steps = dupes.plan([[a1, a2], [b1, b2]], one_folder=True, chosen={a2.path})
        self.assertEqual([k for _, k, _ in steps], [a2, b1])
        self.assertEqual(dupes.describe(a2, a2, os.path.dirname(a2.path), True, {a2.path}),
                         "keep (your pick)")

    def test_undo_puts_everything_back(self):
        flac = self.track("Sets/Burn/Madness.flac", b"lossless audio")
        mp3 = self.track("Techno/Madness.mp3", b"lossy")
        other = self.track("Sets/Burn/Other.mp3", title="Other")
        pl = os.path.join(self.root, "_Playlists", "Friday.m3u8")
        os.makedirs(os.path.dirname(pl))
        with open(pl, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n../Techno/Madness.mp3\n")
        before = self.snapshot()
        jp, _ = dupes.quarantine(self.root, [[flac, mp3]], log=lambda m: None,
                                 recs=[flac, mp3, other], one_folder=True)
        self.assertNotEqual(self.snapshot(), before)
        journal.revert(jp, log=lambda m: None)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    import unittest
    unittest.main()

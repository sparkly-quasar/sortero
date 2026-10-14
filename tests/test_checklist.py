"""The Gig prep checklist: what's done, what's next, and what the main button does."""
import time, unittest

from sortero.gig import checklist as K, convert as C
from sortero.gig.profiles import AudioInfo


def item(status, target=C.MP3, seconds=60):
    info = AudioInfo("flac", ".flac", 96000, depth=24, extras={"duration": seconds})
    return C.Item(f"/c/{status}.flac", f"{status}.flac", status, info, target)


def stick(ready, name="MAIN", summary="rekordbox library, 3 tracks"):
    return K.StickCheck(name, time.time(), ready,
                        "GIG READY" if ready else "Not ready yet: 1 thing to fix.",
                        summary, "" if ready else "exFAT isn't read by every player.")


def statuses(st):
    return {s.key: s.status for s in K.steps(st)}


def step(st, key):
    return next(s for s in K.steps(st) if s.key == key)


class Steps(unittest.TestCase):
    def test_five_steps_in_gig_order(self):
        self.assertEqual([s.key for s in K.steps(K.State())], list(K.KEYS))

    def test_nothing_chosen_yet(self):
        st = K.State()
        self.assertEqual(statuses(st), {"set": K.NEXT, "tracks": K.WAITING,
                                        "main": K.WAITING, "backup": K.WAITING,
                                        "ready": K.WAITING})
        self.assertIsNone(K.next_step(K.steps(st)))
        self.assertEqual(K.current(K.steps(st)), "set")
        self.assertIn("play order", step(st, "set").text)

    def test_chosen_so_the_tracks_are_next(self):
        st = K.State(["Warmup", "Peak"], 40)
        nxt = K.next_step(K.steps(st))
        self.assertEqual((nxt.key, nxt.button), ("tracks", "Check the tracks"))
        self.assertEqual(step(st, "set").text, "Warmup → Peak · 40 tracks")

    def test_copies_needed(self):
        st = K.State(["Warmup"], 3, items=[item(C.READY), item(C.CONVERT), item(C.CONVERT)])
        nxt = K.next_step(K.steps(st))
        self.assertEqual((nxt.key, nxt.button), ("tracks", "Make 2 copies"))
        self.assertIn("2 need a copy", nxt.text)

    def test_nothing_needs_a_copy_goes_straight_to_the_main_stick(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY), item(C.READY)],
                     apps="rekordbox and Engine DJ")
        self.assertEqual(statuses(st)["tracks"], K.DONE)
        nxt = K.next_step(K.steps(st))
        self.assertEqual((nxt.key, nxt.button), ("main", "Make the playlists file"))
        self.assertEqual(nxt.title, "Main stick (rekordbox and Engine DJ)")
        # the backup can be done now, but the main stick comes first
        self.assertEqual(statuses(st)["backup"], K.WAITING)
        self.assertEqual(step(st, "backup").button, "Write the backup stick…")

    def test_after_making_the_xml_check_the_main_stick(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY)], xml_path="/lib/x.xml")
        nxt = K.next_step(K.steps(st))
        self.assertEqual((nxt.key, nxt.button), ("main", "Check the main stick…"))

    def test_a_main_stick_that_fails_says_so_calmly(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY)], xml_path="/x", main=stick(False))
        main = step(st, "main")
        self.assertEqual(main.status, K.NEXT)
        self.assertTrue(main.text.startswith("Not ready yet: 1 thing to fix."))
        self.assertIn("exFAT", main.text)
        self.assertEqual(statuses(st)["ready"], K.WAITING)
        self.assertNotIn("NOT", main.text)

    def test_main_stick_passes_so_gig_ready_and_backup_is_next(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY)], xml_path="/x", main=stick(True))
        self.assertEqual(statuses(st)["main"], K.DONE)
        self.assertEqual(statuses(st)["ready"], K.DONE)
        self.assertTrue(K.ready(st))
        ready = step(st, "ready").text
        self.assertTrue(ready.startswith("GIG READY."))
        self.assertIn("“MAIN” checked", ready)
        self.assertIn("No backup stick yet", ready)
        self.assertEqual(K.next_step(K.steps(st)).key, "backup")
        self.assertEqual(K.current(K.steps(st)), "backup")

    def test_both_sticks_checked(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY)], xml_path="/x", main=stick(True),
                     backup_check=stick(True, "BACKUP", "3 tracks in 1 folder"))
        self.assertEqual(set(statuses(st).values()), {K.DONE})
        self.assertIsNone(K.next_step(K.steps(st)))
        self.assertEqual(K.current(K.steps(st)), "ready")
        self.assertIn("Backup “BACKUP” checked", step(st, "ready").text)
        self.assertIn("Eject both sticks", step(st, "ready").text)

    def test_a_backup_check_that_fails(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY)], main=stick(True),
                     backup_check=stick(False, "BACKUP"))
        b = step(st, "backup")
        self.assertEqual((b.status, b.button), (K.NEXT, "Write the backup again…"))
        self.assertEqual(statuses(st)["ready"], K.DONE)      # the backup isn't required

    def test_checked_in_an_earlier_session(self):
        then = time.time() - 3600
        st = K.State(["Warmup"], 2, main=stick(True), earlier=then)
        self.assertEqual(statuses(st)["tracks"], K.DONE)
        self.assertIn("Done earlier", step(st, "tracks").text)
        self.assertEqual(statuses(st)["ready"], K.DONE)

    def test_copied_tracks_still_count_as_needing_a_copy(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY), item(C.CONVERTED)], copied=True)
        tracks = step(st, "tracks")
        self.assertEqual(tracks.status, K.DONE)
        self.assertIn("1 needs a copy", tracks.text)
        self.assertIn("1 copy ready", tracks.text)

    def test_waiting_steps_are_just_a_title(self):
        for s in K.steps(K.State()):
            if s.status == K.WAITING:
                self.assertEqual(s.text, "", s.key)


class When(unittest.TestCase):
    def test_today_yesterday_and_earlier(self):
        now = time.mktime((2026, 10, 14, 21, 30, 0, 0, 0, -1))
        self.assertEqual(K.when(now - 600, now), "21:20")
        self.assertEqual(K.when(now - 86400, now), "yesterday 21:30")
        self.assertEqual(K.when(now - 5 * 86400, now), "9 Oct 21:30")

    def test_stick_check_round_trip(self):
        chk = stick(True)
        self.assertEqual(K.StickCheck.from_dict(chk.to_dict()), chk)
        self.assertEqual(K.StickCheck.from_dict({**chk.to_dict(), "new": 1}), chk)


class Estimates(unittest.TestCase):
    def test_space_a_copy_takes(self):
        self.assertEqual(K.estimate_bytes([item(C.CONVERT, C.MP3, 60)]), 60 * 40000)
        self.assertEqual(K.estimate_bytes([item(C.CONVERT, C.AIFF, 60)]), 60 * 176400)
        self.assertEqual(K.estimate_bytes([item(C.READY)]), 0)

    def test_check_text(self):
        text = K.check_text([item(C.READY), item(C.CONVERT, seconds=300), item(C.FAILED)])
        self.assertTrue(text.startswith("1 track already plays as it is."), text)
        self.assertIn("1 needs a copy, about 11.4 MB", text)


if __name__ == "__main__":
    unittest.main()

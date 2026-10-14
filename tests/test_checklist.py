"""The Gig prep checklist: what's done, what's next, and what the main button does."""
import unittest

from sortero.gig import checklist as K, convert as C
from sortero.gig.profiles import AudioInfo


def item(status, target=C.MP3, seconds=60):
    info = AudioInfo("flac", ".flac", 96000, depth=24, extras={"duration": seconds})
    return C.Item(f"/c/{status}.flac", f"{status}.flac", status, info, target)


class Report:
    def __init__(self, ready):
        self.ready = ready
        self.verdict = "GIG READY" if ready else "NOT READY: 1 problem to fix."


def statuses(st):
    return {s.key: s.status for s in K.steps(st)}


class Steps(unittest.TestCase):
    def test_nothing_chosen_yet(self):
        st = K.State()
        self.assertEqual(statuses(st), {"playlists": K.NEXT, "check": K.WAITING,
                                        "copies": K.WAITING, "stick": K.WAITING,
                                        "verify": K.WAITING})
        self.assertIsNone(K.next_step(K.steps(st)))

    def test_chosen_so_check_is_next(self):
        st = K.State(["Warmup", "Peak"], 40)
        nxt = K.next_step(K.steps(st))
        self.assertEqual((nxt.key, nxt.button), ("check", "Check tracks"))
        self.assertIn("Warmup, Peak", K.steps(st)[0].text)

    def test_copies_needed(self):
        st = K.State(["Warmup"], 3, items=[item(C.READY), item(C.CONVERT), item(C.CONVERT)])
        nxt = K.next_step(K.steps(st))
        self.assertEqual((nxt.key, nxt.button), ("copies", "Make 2 copies"))
        self.assertEqual(statuses(st)["check"], K.DONE)

    def test_nothing_needs_a_copy_goes_straight_to_the_stick(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY), item(C.READY)])
        self.assertEqual(statuses(st)["copies"], K.DONE)
        nxt = K.next_step(K.steps(st))
        self.assertEqual((nxt.key, nxt.button), ("stick", "Write to a stick…"))
        # a rekordbox stick can be checked without writing a backup first
        self.assertEqual(statuses(st)["verify"], K.NEXT)

    def test_after_writing_the_backup(self):
        class Written:
            written, unchanged, skipped = ["a", "b"], ["c"], [("d", "why")]
        st = K.State(["Warmup", "Peak"], 4, items=[item(C.READY)], backup=Written(),
                     stick_name="GIG")
        stick = next(s for s in K.steps(st) if s.key == "stick")
        self.assertEqual(stick.status, K.DONE)
        self.assertIn("3 tracks in 2 numbered folders on GIG", stick.text)
        self.assertIn("1 skipped", stick.text)
        self.assertEqual(K.next_step(K.steps(st)).key, "verify")

    def test_copied_tracks_still_count_as_needing_a_copy(self):
        st = K.State(["Warmup"], 2, items=[item(C.READY), item(C.CONVERTED)], copied=True)
        check = next(s for s in K.steps(st) if s.key == "check")
        self.assertIn("1 needs a copy", check.text)

    def test_waiting_steps_are_just_a_title(self):
        for s in K.steps(K.State()):
            if s.status == K.WAITING:
                self.assertEqual(s.text, "", s.key)

    def test_after_copying(self):
        st = K.State(["Warmup"], 2, items=[item(C.CONVERTED), item(C.FAILED)], copied=True)
        copies = next(s for s in K.steps(st) if s.key == "copies")
        self.assertEqual(copies.status, K.DONE)
        self.assertIn("1 copy ready", copies.text)
        self.assertIn("1 couldn't be used", copies.text)

    def test_stick_verdict(self):
        st = K.State(["Warmup"], 1, items=[item(C.READY)], report=Report(False))
        verify = next(s for s in K.steps(st) if s.key == "verify")
        self.assertEqual((verify.status, verify.text),
                         (K.NEXT, "NOT READY: 1 problem to fix."))
        st.report = Report(True)
        self.assertEqual(statuses(st)["verify"], K.DONE)


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

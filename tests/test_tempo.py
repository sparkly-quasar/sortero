"""BPM fixing on drum loops whose true tempo is known: the fixes it should make,
and the "fixes" a wrong reading of the audio would make."""
import unittest

from sortero import bpmfix, tempo
from tests.audio import drum_loop

TECHNO = (115, 160)


def profile(pcm):
    env, kick = tempo.envelope(pcm)
    return tempo.Profile(env, 0.0, kick)


class Fixes(unittest.TestCase):
    def judge(self, pcm, tag):
        return bpmfix.judge(profile(pcm), tag, *TECHNO)

    def test_two_thirds(self):
        self.assertEqual(self.judge(drum_loop(140), 93.33), 1.5)

    def test_rolling_threes(self):
        self.assertEqual(self.judge(drum_loop(140, hats=0.6, hat_every=1 / 3), 93.33), 1.5)

    def test_three_quarters(self):
        self.assertEqual(self.judge(drum_loop(140, hat_every=0.25), 105.0), 4 / 3)

    def test_half_time(self):
        self.assertEqual(self.judge(drum_loop(140), 70.0), 2.0)
        self.assertEqual(self.judge(drum_loop(140, kick_every=2), 70.0), 2.0)


class LeftAlone(unittest.TestCase):
    def judge(self, pcm, tag):
        return bpmfix.judge(profile(pcm), tag, *TECHNO)

    def test_a_slow_track_with_loud_triplet_hats(self):
        # Correlates more strongly at x3/2 than at its own tempo, which the
        # autocorrelation margin alone took for a 140 track tagged 93.
        pcm = drum_loop(93, kick=0.05, hats=1.0, hat_every=1 / 3)
        p = profile(pcm)
        self.assertGreater(p.score(139.5), p.score(93.0) + bpmfix.MARGIN)
        self.assertIsNone(bpmfix.judge(p, 93.0, *TECHNO))

    def test_a_slow_track_with_sixteenths(self):
        self.assertIsNone(self.judge(drum_loop(100, hat_every=0.25), 100.0))

    def test_a_track_that_changes_tempo(self):
        pcm = drum_loop(140, seconds=30) + drum_loop(120, seconds=30)
        self.assertIsNone(self.judge(pcm, 93.33))

    def test_a_breakdown_doesnt_count_as_a_tempo_change(self):
        pcm = drum_loop(140, seconds=40) + drum_loop(140, seconds=20, kick=0, hats=0.01)
        self.assertTrue(bpmfix.steady(profile(pcm), *TECHNO))


class SpectralLine(unittest.TestCase):
    def test_a_pulse_train_has_a_line_at_its_tempo_and_none_at_two_thirds(self):
        p = profile(drum_loop(140))
        self.assertGreater(p.line(140.0), 10 * p.line(93.33))


if __name__ == "__main__":
    unittest.main()

"""What to play next: scoring a blend from key, tempo and energy, and ordering a set."""
import os, unittest

from sortero import nextup, playlists
from sortero.library import Rec
from tests.test_tagio import TempDir


def rec(name, key=None, bpm=None, energy=None, artist=None, folder="Techno"):
    rel = os.path.join(folder, name + ".mp3")
    return Rec(path=os.path.join(os.sep, "music", rel), rel=rel, artist=artist or name,
               title=name, key=key, bpm=None if bpm is None else str(bpm),
               energylevel=None if energy is None else str(energy))


class Key(unittest.TestCase):
    def test_moves_round_the_wheel_rank_as_djs_rank_them(self):
        score = lambda b: nextup.key_fit("8A", b)[0]
        self.assertEqual(score("8A"), 1.0)
        self.assertGreater(score("9A"), score("8B"))      # next door beats relative
        self.assertEqual(score("7A"), score("9A"))        # either way round
        self.assertGreater(score("8B"), score("9B"))      # relative beats diagonal
        self.assertGreater(score("9B"), score("10A"))     # diagonal beats +2
        self.assertGreater(score("10A"), score("6A"))     # a lift beats a drop
        self.assertGreater(score("3A"), 0)                # +7: a semitone up
        self.assertEqual(score("2B"), 0.0)

    def test_wraps_from_12_to_1(self):
        self.assertEqual(nextup.key_fit("12A", "1A")[0], 0.9)
        self.assertEqual(nextup.key_fit("1B", "12B")[0], 0.9)

    def test_musical_keys_are_read_through_the_record(self):
        # Am is 8A, Em is 9A: one step on the wheel.
        f = nextup.fit(rec("a", key="Am"), rec("b", key="Em"))
        self.assertEqual(f.key, 0.9)
        self.assertIn("Next on the wheel (8A → 9A)", f.reasons)

    def test_no_key_is_neutral_and_gives_no_reason(self):
        self.assertEqual(nextup.key_fit(None, "8A"), (nextup.NEUTRAL, None))
        self.assertEqual(nextup.key_fit("8A", "nonsense"), (nextup.NEUTRAL, None))


class Tempo(unittest.TestCase):
    def test_close_tempos_blend_and_far_ones_jump(self):
        self.assertEqual(nextup.tempo_fit(128, 128.5)[0], 1.0)
        near, far = nextup.tempo_fit(128, 132)[0], nextup.tempo_fit(128, 136)[0]
        self.assertGreater(near, far)
        self.assertGreater(far, 0)
        self.assertEqual(nextup.tempo_fit(128, 140), (0.0, "Tempo jump (128 → 140 BPM)"))

    def test_half_and_double_time_count_as_close(self):
        self.assertEqual(nextup.tempo_fit(140, 70), (1.0, "Same tempo, half time"))
        self.assertEqual(nextup.tempo_fit(87, 174), (1.0, "Same tempo, double time"))

    def test_the_sign_says_which_way_the_fader_goes(self):
        self.assertTrue(nextup.tempo_fit(125, 130)[1].startswith("+4%"))
        self.assertTrue(nextup.tempo_fit(130, 125)[1].startswith("-4%"))

    def test_bpm_tags_in_the_forms_tools_write(self):
        for tag, want in (("128", 128.0), ("127.98", 127.98), ("128,00", 128.0),
                          (" 140 BPM", 140.0), ("", None), ("0", None), ("abc", None)):
            with self.subTest(tag=tag):
                self.assertEqual(nextup.bpm_of(rec("x", bpm=tag or None)), want)


class Energy(unittest.TestCase):
    def test_direction_decides_which_change_is_best(self):
        e = nextup.energy_fit
        self.assertGreater(e(5, 6, "build")[0], e(5, 5, "build")[0])
        self.assertGreater(e(5, 5, "build")[0], e(5, 4, "build")[0])
        self.assertGreater(e(5, 5, "hold")[0], e(5, 6, "hold")[0])
        self.assertGreater(e(6, 5, "drop")[0], e(6, 7, "drop")[0])
        self.assertGreater(e(5, 6, None)[0], e(5, 9, None)[0])

    def test_unknown_direction_is_refused(self):
        with self.assertRaises(ValueError):
            nextup.fit(rec("a"), rec("b"), direction="sideways")


class Suggest(unittest.TestCase):
    def setUp(self):
        self.now = rec("Now", key="8A", bpm=128, energy=6)
        self.lib = [
            self.now,
            rec("Perfect", key="8A", bpm=128, energy=7),
            rec("Neighbour", key="9A", bpm=127, energy=6),
            rec("Clash", key="2B", bpm=128, energy=6),
            rec("TooFast", key="8A", bpm=150, energy=6),
            rec("Unknown", bpm=128),
            rec("Mix", key="8A", bpm=128, energy=6, folder="Recorded Mixes"),
            rec("Now", key="8A", bpm=128, energy=6, folder="House"),  # another copy
        ]

    def names(self, picks):
        return [r.title for r, _ in picks]

    def test_best_first_without_itself_copies_recordings_or_clashes(self):
        got = self.names(nextup.suggest(self.now, self.lib, direction="build"))
        self.assertEqual(got, ["Perfect", "Neighbour", "Unknown"])

    def test_clashes_can_be_asked_for(self):
        got = self.names(nextup.suggest(self.now, self.lib, clashes=True))
        self.assertIn("Clash", got)
        self.assertIn("TooFast", got)

    def test_the_rest_of_the_set_is_left_out(self):
        got = self.names(nextup.suggest(self.now, self.lib, exclude=[self.lib[1].path]))
        self.assertNotIn("Perfect", got)

    def test_n_caps_the_list(self):
        self.assertEqual(len(nextup.suggest(self.now, self.lib, n=1)), 1)

    def test_every_suggestion_says_why(self):
        for _, f in nextup.suggest(self.now, self.lib):
            self.assertTrue(f.reasons)
            self.assertLessEqual(f.score, 1.0)


class FromYourPlaylists(TempDir):
    def setUp(self):
        super().setUp()
        self.a = rec("A", key="8A", bpm=128, energy=6)
        self.b = rec("B", key="2B", bpm=128, energy=6)   # clashes with A
        self.c = rec("C", key="9A", bpm=128, energy=6)
        for r in (self.a, self.b, self.c):
            r.path = self.path(r.rel)
        playlists.write(self.dir, "Gig 2026-10-19", [self.a.path, self.b.path, self.c.path])

    def test_tracks_played_next_get_a_lift_and_say_so(self):
        h = nextup.History.from_playlists(self.dir)
        self.assertEqual(h.times(self.a.path, self.b.path), (1, 0))
        plain, lifted = nextup.fit(self.a, self.b), nextup.fit(self.a, self.b, history=h)
        self.assertGreater(lifted.score, plain.score)
        self.assertEqual(lifted.reasons[0], "You've played it next before")

    def test_a_pairing_you_chose_survives_a_key_clash(self):
        h = nextup.History.from_playlists(self.dir)
        self.assertNotIn(self.b, [r for r, _ in nextup.suggest(self.a, [self.b])])
        self.assertIn(self.b, [r for r, _ in nextup.suggest(self.a, [self.b], history=h)])

    def test_the_other_way_round_counts_for_less(self):
        h = nextup.History().add([self.a.path, self.b.path])
        forward = nextup.fit(self.a, self.b, history=h).score
        backward = nextup.fit(self.b, self.a, history=h).score
        self.assertGreater(forward, backward)

    def test_a_played_set_can_weigh_more_than_a_playlist(self):
        h = nextup.History().add([self.a.path, self.c.path], weight=3)
        self.assertEqual(h.times(self.a.path, self.c.path), (3, 0))

    def test_no_playlists_folder_is_an_empty_history(self):
        self.assertEqual(len(nextup.History.from_playlists(self.path("nowhere"))), 0)


class Order(unittest.TestCase):
    def test_smooth_puts_each_track_next_to_its_neighbours_on_the_wheel(self):
        # Shuffled: 10A 8A 12A 9A 11A. Smoothest is a walk round the wheel.
        keys = ["10A", "8A", "12A", "9A", "11A"]
        tracks = [rec(k, key=k, bpm=126, energy=6) for k in keys]
        got = [r.key for r in nextup.order(tracks)]
        self.assertIn(got, (["8A", "9A", "10A", "11A", "12A"],
                            ["12A", "11A", "10A", "9A", "8A"]))

    def test_build_up_climbs_in_energy(self):
        tracks = [rec(f"e{e}", key="8A", bpm=126, energy=e) for e in (7, 3, 9, 5, 4, 8, 6)]
        got = [r.energy for r in nextup.order(tracks, "build")]
        self.assertEqual(got, sorted(got))

    def test_peak_climbs_then_lands(self):
        tracks = [rec(f"t{i}", key="8A", bpm=126, energy=e)
                  for i, e in enumerate((3, 4, 5, 6, 7, 8, 9, 6, 5))]
        got = [r.energy for r in nextup.order(tracks, "peak")]
        top = got.index(9)
        self.assertGreater(top, 0)
        self.assertLess(top, len(got) - 1)
        self.assertLess(got[-1], 9)

    def test_a_pinned_opener_stays_first(self):
        tracks = [rec(k, key=k, bpm=126, energy=6) for k in ("8A", "9A", "10A", "11A")]
        got = nextup.order(tracks, start=tracks[2])
        self.assertIs(got[0], tracks[2])
        self.assertEqual(sorted(r.key for r in got), sorted(r.key for r in tracks))

    def test_every_track_comes_back_once(self):
        tracks = [rec(f"t{i}", key=f"{i % 12 + 1}{'AB'[i % 2]}", bpm=120 + i, energy=i % 10 + 1)
                  for i in range(25)]
        for name in nextup.SHAPES:
            with self.subTest(shape=name):
                got = nextup.order(tracks, name)
                self.assertCountEqual([id(r) for r in got], [id(r) for r in tracks])

    def test_the_same_tracks_give_the_same_order(self):
        tracks = [rec(f"t{i}", key=f"{i % 12 + 1}A", bpm=124, energy=5) for i in range(8)]
        self.assertEqual(nextup.order(tracks), nextup.order(list(reversed(tracks))))

    def test_tiny_sets_and_unknown_shapes(self):
        self.assertEqual(nextup.order([]), [])
        one = [rec("x")]
        self.assertEqual(nextup.order(one), one)
        with self.assertRaises(ValueError):
            nextup.order([rec("a", energy=3), rec("b", energy=7)], "zigzag")


class Shape(unittest.TestCase):
    def test_shapes_start_at_the_bottom_and_stay_in_range(self):
        for name in ("build", "peak", "waves"):
            with self.subTest(shape=name):
                s = nextup.shape(name, 12, 3, 9)
                self.assertEqual(len(s), 12)
                self.assertAlmostEqual(s[0], 3)
                self.assertTrue(all(3 - 1e-9 <= v <= 9 + 1e-9 for v in s))

    def test_smooth_has_no_shape(self):
        self.assertIsNone(nextup.shape("smooth", 10, 3, 9))


class Transitions(unittest.TestCase):
    def test_one_per_blend_and_weak_links_score_low(self):
        seq = [rec("a", key="8A", bpm=126, energy=5), rec("b", key="9A", bpm=126, energy=6),
               rec("c", key="3B", bpm=140, energy=6)]
        got = nextup.transitions(seq)
        self.assertEqual(len(got), 2)
        self.assertGreater(got[0].score, got[1].score)
        self.assertTrue(got[1].clash)


if __name__ == "__main__":
    unittest.main()

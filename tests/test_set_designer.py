"""Set designer, driven as you would: search, add, reorder, order, save, send to Gig prep."""
import os, shutil, tempfile, time, tkinter as tk, unittest
from unittest import mock

from mutagen.id3 import ID3, TBPM, TCON, TIT2, TKEY, TPE1, TXXX

from sortero import library, playlists
from sortero.gig import sources

# (artist, title, key, bpm, energy)
TRACKS = [("Ana Lusk", "Harbour", "8A", 124, 5),
          ("Ana Lusk", "Slow Tide", "8A", 124, 6),
          ("Dagny Holt", "Fracture Line", "9A", 125, 7),
          ("Halden Voss", "Subgrid", "2B", 138, 9),
          ("Juno Farrow", "Glasshouse", "8B", 124, 6),
          ("Kepler Vale", "Ninth Hour", "7A", 123, 4)]


def write(root, artist, title, key, bpm, energy):
    p = os.path.join(root, "Techno", f"{artist} - {title}.mp3")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    t = ID3()
    for f in (TPE1(encoding=3, text=[artist]), TIT2(encoding=3, text=[title]),
              TKEY(encoding=3, text=[key]), TBPM(encoding=3, text=[str(bpm)]),
              TCON(encoding=3, text=["Techno"]),
              TXXX(encoding=3, desc="EnergyLevel", text=[str(energy)])):
        t.add(f)
    t.save(p)
    return p


class SetDesigner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sortero-sets-")
        # Sortero's settings and journals go here, not to the real ones
        cls.base = mock.patch("sortero.paths._base",
                              lambda kind: os.path.join(cls.tmp, "app-" + kind))
        cls.base.start()
        cls.root = os.path.join(cls.tmp, "DJ Collection")
        cls.paths = {(a, t): write(cls.root, a, t, k, b, e) for a, t, k, b, e in TRACKS}
        from sortero import settings
        for key, value in (("root", cls.root), ("setup_complete", True),
                           ("check_updates_on_launch", False)):
            settings.set(key, value)
        from sortero.app import Sortero
        try:
            cls.app = Sortero()
        except tk.TclError as e:                  # no display to open a window on
            cls.base.stop()
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise unittest.SkipTest(f"Tk can't open a window here ({e})")
        cls.app.withdraw()
        cls.app.recs = library.scan(cls.root)
        cls.app.health = library.health(cls.app.recs)

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.base.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # Playlists count as history, so one test's saved set mustn't lift another's
        shutil.rmtree(playlists.playlist_dir(self.root), ignore_errors=True)
        self.sd = self.app.screens["sets"]
        self.app.show("sets")
        self.sd.set = []
        self.sd.search_var.set("")
        self.sd.direction.set("any")
        self.sd.history = None
        self.sd.player.clear()
        self.sd.listening = None
        self.sd._changed()

    # ---------------------------------------------------------- helpers
    def rec(self, artist, title):
        p = os.path.normcase(self.paths[(artist, title)])
        return next(r for r in self.app.recs if os.path.normcase(r.path) == p)

    def titles(self):
        return [r.title for r in self.sd.set]

    def suggested(self):
        return [self.sd.rows[i][0].title for i in self.sd.next_tv.get_children()]

    def search(self, text):
        self.sd.search_var.set(text)
        self.sd.refresh_next()          # what the debounce would run

    def wait(self):
        while self.app.task.running:
            self.app.update()
            time.sleep(0.01)
        self.app.update()

    # ---------------------------------------------------------- tests
    def test_an_empty_set_says_how_to_start(self):
        self.assertEqual(self.suggested(), [])
        self.assertIn("Search for a track to start", self.sd.why.cget("text"))
        self.assertEqual(str(self.sd.bar.primary.cget("state")), "disabled")

    def test_search_then_add_starts_the_set_and_suggestions_follow(self):
        self.search("harbour")
        self.assertEqual(self.suggested(), ["Harbour"])
        self.sd.add_selected(first=True)
        self.assertEqual(self.titles(), ["Harbour"])
        self.assertEqual(self.sd.search_var.get(), "")
        got = self.suggested()
        # Same key and tempo first; the clashing 2B at 138 is left out; Harbour isn't
        # suggested after itself.
        self.assertEqual(got[0], "Slow Tide")
        self.assertNotIn("Subgrid", got)
        self.assertNotIn("Harbour", got)
        self.assertIn("What fits after “Ana Lusk - Harbour”", self.sd.next_label.cget("text"))

    def test_search_ranks_by_fit_after_the_last_track(self):
        self.sd.add(self.rec("Ana Lusk", "Harbour"))
        self.search("a")            # matches nearly everything
        got = self.suggested()
        self.assertEqual(got[0], "Slow Tide")
        self.assertEqual(got[-1], "Subgrid")
        self.assertNotIn("Harbour", got)

    def test_the_energy_direction_changes_the_order(self):
        self.sd.add(self.rec("Ana Lusk", "Slow Tide"))      # 8A, energy 6
        self.sd.direction.set("build")
        self.sd.refresh_next()
        build = self.suggested()
        self.sd.direction.set("drop")
        self.sd.refresh_next()
        drop = self.suggested()
        self.assertLess(build.index("Fracture Line"), build.index("Harbour"))
        self.assertLess(drop.index("Harbour"), drop.index("Fracture Line"))

    def test_add_goes_after_the_selected_track(self):
        for t in (("Ana Lusk", "Harbour"), ("Dagny Holt", "Fracture Line")):
            self.sd.add(self.rec(*t))
        self.sd.set_tv.selection_set("0")
        self.sd.refresh_next()
        self.assertIn("after “Ana Lusk - Harbour”", self.sd.next_label.cget("text"))
        first = self.suggested()[0]
        self.sd.add_selected()
        self.assertEqual(self.titles(), ["Harbour", first, "Fracture Line"])

    def test_a_track_goes_in_once(self):
        r = self.rec("Ana Lusk", "Harbour")
        self.sd.add(r)
        self.sd.add(r)
        self.assertEqual(self.titles(), ["Harbour"])
        self.assertIn("already in your set", self.sd.why.cget("text"))

    def test_move_and_remove(self):
        for t in (("Ana Lusk", "Harbour"), ("Ana Lusk", "Slow Tide"), ("Juno Farrow", "Glasshouse")):
            self.sd.add(self.rec(*t))
        self.sd.set_tv.selection_set("2")
        self.sd.move(-1)
        self.assertEqual(self.titles(), ["Harbour", "Glasshouse", "Slow Tide"])
        self.assertEqual(self.sd.set_tv.selection(), ("1",))
        self.sd.remove()
        self.assertEqual(self.titles(), ["Harbour", "Slow Tide"])

    def test_blends_are_marked(self):
        for t in (("Ana Lusk", "Harbour"), ("Ana Lusk", "Slow Tide"), ("Halden Voss", "Subgrid")):
            self.sd.add(self.rec(*t))
        words = [self.sd.set_tv.set(i, "blend") for i in self.sd.set_tv.get_children()]
        self.assertEqual(words, ["", "Smooth", "Clash"])
        self.assertIn("1 rough blend", self.sd.set_label.cget("text"))

    def test_order_then_undo(self):
        for t in (("Kepler Vale", "Ninth Hour"), ("Dagny Holt", "Fracture Line"),
                  ("Ana Lusk", "Harbour"), ("Ana Lusk", "Slow Tide")):
            self.sd.add(self.rec(*t))
        before = self.titles()
        self.sd.shape_var.set("Build up")
        self.sd.keep_first.set(False)
        self.sd.order_set()
        self.wait()
        energies = [r.energy for r in self.sd.set]
        self.assertEqual(energies, sorted(energies))
        self.assertTrue(self.sd.undo_link.winfo_ismapped() or self.sd._undo is not None)
        self.sd.undo_order()
        self.assertEqual(self.titles(), before)

    def test_keep_the_opener(self):
        for t in (("Halden Voss", "Subgrid"), ("Ana Lusk", "Harbour"),
                  ("Ana Lusk", "Slow Tide"), ("Dagny Holt", "Fracture Line")):
            self.sd.add(self.rec(*t))
        self.sd.shape_var.set("Build up")
        self.sd.keep_first.set(True)
        self.sd.order_set()
        self.wait()
        self.assertEqual(self.titles()[0], "Subgrid")

    def test_save_as_playlist_and_replace(self):
        for t in (("Ana Lusk", "Harbour"), ("Ana Lusk", "Slow Tide")):
            self.sd.add(self.rec(*t))
        with mock.patch("sortero.screens.set_designer.simpledialog.askstring",
                        return_value="Friday warmup"):
            self.assertEqual(self.sd.save(), "Friday warmup")
        got = playlists.entries(self.root, "Friday warmup")
        self.assertEqual([os.path.normcase(p) for p in got],
                         [os.path.normcase(r.path) for r in self.sd.set])
        self.sd.add(self.rec("Juno Farrow", "Glasshouse"))
        with mock.patch("sortero.screens.set_designer.simpledialog.askstring",
                        return_value="Friday warmup"), \
                mock.patch("sortero.screens.set_designer.messagebox.askyesno",
                           return_value=True) as ask:
            self.sd.save()
        self.assertIn("Replace it", ask.call_args[0][1])
        self.assertEqual(len(playlists.entries(self.root, "Friday warmup")), 3)

    def test_saved_playlists_count_as_history(self):
        a, b = self.rec("Ana Lusk", "Harbour"), self.rec("Halden Voss", "Subgrid")
        playlists.write(self.root, "Odd pair", [a.path, b.path])
        self.sd.history = None
        self.sd.add(a)
        self.assertIn("Subgrid", self.suggested())      # a clash, but you've paired them

    def test_send_to_gig_prep_saves_and_adds(self):
        self.sd.add(self.rec("Ana Lusk", "Harbour"))
        picker = self.app.screens["gig"].picker
        with mock.patch("sortero.screens.set_designer.simpledialog.askstring",
                        return_value="For the gig"), \
                mock.patch("sortero.screens.set_designer.messagebox.askyesno",
                           return_value=False), \
                mock.patch.object(picker, "take", return_value=True) as take:
            self.sd.send_to_gig()
        src = take.call_args[0][0]
        self.assertEqual((src.kind, src.name), (sources.SORTERO, "For the gig"))

    def test_start_from_a_playlist(self):
        a, b = self.rec("Ana Lusk", "Harbour"), self.rec("Dagny Holt", "Fracture Line")
        gone = os.path.join(self.root, "Techno", "Nobody - Gone.mp3")
        playlists.write(self.root, "Peak", [a.path, gone, b.path])
        src = sources.Source(sources.SORTERO, playlists.playlist_dir(self.root), "Peak")
        self.sd._take_playlist(src, replace=True)
        self.assertEqual(self.titles(), ["Harbour", "Fracture Line"])
        self.assertIn("1 not in your collection", self.sd.order_note.cget("text"))
        self.sd._take_playlist(src, replace=False)          # nothing twice
        self.assertEqual(self.titles(), ["Harbour", "Fracture Line"])

    # ---------------------------------------------------------- listening
    def pick(self, tv, iid):
        """Select a row as you would: by hand, not by the list itself."""
        self.sd._by_hand(tv)
        tv.selection_set(iid)
        self.app.update()

    def loaded(self):
        return os.path.basename(self.sd.player.path) if self.sd.player.path else None

    def test_picking_a_suggestion_loads_it_from_the_body(self):
        r = self.rec("Ana Lusk", "Harbour")
        for x in self.app.recs:
            x.duration = 300.0
        self.sd.add(r)
        self.app.update()
        first = self.sd.rows["0"][0]
        # The list selected its first row by itself: nothing changes in the player
        self.assertIsNone(self.loaded())
        self.pick(self.sd.next_tv, "1")
        second = self.sd.rows["1"][0]
        self.assertEqual(self.sd.player.path, second.path)
        self.assertIn(f"Listening to {second.display}", self.sd.listen_label.cget("text"))
        self.assertAlmostEqual(self.sd.player.pos.get(), 90.0)    # 30% in: past the intro
        self.pick(self.sd.next_tv, "0")
        self.assertEqual(self.sd.player.path, first.path)

    def test_picking_a_track_in_the_set_loads_it(self):
        for t in (("Ana Lusk", "Harbour"), ("Dagny Holt", "Fracture Line")):
            self.sd.add(self.rec(*t))
        self.app.update()
        self.pick(self.sd.set_tv, "0")
        self.assertEqual(self.loaded(), "Ana Lusk - Harbour.mp3")
        # the suggestions follow the new track, but don't take the player
        self.assertIn("after “Ana Lusk - Harbour”", self.sd.next_label.cget("text"))
        self.assertEqual(self.loaded(), "Ana Lusk - Harbour.mp3")

    def test_adding_what_you_hear_keeps_it_playing(self):
        self.sd.add(self.rec("Ana Lusk", "Harbour"))
        self.app.update()
        self.pick(self.sd.next_tv, "0")
        heard = self.sd.player.path
        with mock.patch.object(self.sd.player, "load") as load:
            self.sd.add_selected()
            self.app.update()
        load.assert_not_called()
        self.assertEqual(self.sd.player.path, heard)

    def test_jumps_to_intro_body_and_outro(self):
        r = self.rec("Ana Lusk", "Harbour")
        r.duration = 400.0
        with mock.patch("sortero.preview.can_seek", return_value=True):
            self.sd.listen_to(r)
        for where, at in (("outro", 340.0), ("intro", 0.0), ("body", 120.0)):
            with self.subTest(where=where):
                self.sd.jump_to(where)
                self.assertAlmostEqual(self.sd.player.pos.get(), at)

    def test_space_plays_and_pauses(self):
        self.sd.listen_to(self.rec("Ana Lusk", "Harbour"))
        with mock.patch.object(self.sd.player, "toggle") as toggle:
            for tv in (self.sd.set_tv, self.sd.next_tv):
                self.assertEqual(self.sd.play_pause(), "break")
        self.assertEqual(toggle.call_count, 2)
        self.assertIn("<Key-space>", self.sd.next_tv.bind())

    def test_leaving_the_screen_stops_playing(self):
        self.sd.listen_to(self.rec("Ana Lusk", "Harbour"))
        with mock.patch.object(self.sd.player, "stop") as stop:
            self.app.show("library")
        stop.assert_called()
        self.app.show("sets")

    def test_the_set_is_remembered(self):
        for t in (("Ana Lusk", "Harbour"), ("Juno Farrow", "Glasshouse")):
            self.sd.add(self.rec(*t))
        self.sd.set = []
        self.sd._loaded_for = None          # as after a restart
        self.sd.shown()
        self.assertEqual(self.titles(), ["Harbour", "Glasshouse"])


if __name__ == "__main__":
    unittest.main()

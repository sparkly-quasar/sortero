"""Gig prep's stick steps: the Eject and Format buttons, in a real (hidden) window."""
import os, shutil, tempfile, tkinter as tk, unittest
from unittest import mock

from sortero import settings
from sortero.gig import convert as C, disks
from tests.test_checklist import item


class GigWindow(unittest.TestCase):
    """Gig prep in a real (hidden) window, with one checked track that's ready."""
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="sortero-gig-")
        for key, value in (("root", cls.root), ("setup_complete", True),
                           ("check_updates_on_launch", False)):
            settings.set(key, value)
        from sortero.app import Sortero
        try:
            cls.app = Sortero()
        except tk.TclError as e:                  # no display to open a window on
            raise unittest.SkipTest(f"Tk can't open a window here ({e})")
        cls.app.withdraw()
        cls.gig = cls.app.screens["gig"]
        cls.gig.state.items = [item(C.READY)]
        cls.gig.state.copied = True

    @classmethod
    def tearDownClass(cls):
        app = getattr(cls, "app", None)
        if app:
            app.destroy()
        shutil.rmtree(cls.root, ignore_errors=True)


class StickTools(GigWindow):
    def tools(self, which):
        self.gig.open(which)
        self.app.update_idletasks()
        w = getattr(self.gig, f"{which}_w")
        return {k: w[k].cget("text") for k in ("eject", "format", "quick")
                if w[k].winfo_manager()}

    def test_both_stick_steps_can_eject_and_format(self):
        from sortero.gig import formatting
        for which in ("main", "backup"):
            shown = self.tools(which)
            self.assertEqual(shown["eject"], "Eject a stick…", which)
            if formatting.available():
                self.assertEqual(shown["format"], "Format a stick as FAT32…", which)

    def test_the_steps_own_stick_is_named_when_its_plugged_in(self):
        with mock.patch.object(self.gig, "_mount", return_value="/Volumes/GIG"):
            self.assertEqual(self.tools("backup")["eject"], "Eject “GIG”")
            with mock.patch.object(disks, "eject", return_value="") as eject:
                self.gig.eject_stick("backup")
            eject.assert_called_once_with("/Volumes/GIG")

    def test_not_while_sortero_is_writing(self):
        with mock.patch.object(self.gig, "_mount", return_value="/Volumes/GIG"), \
                mock.patch.object(self.app.task, "running", True), \
                mock.patch("sortero.screens.gig.messagebox") as box, \
                mock.patch.object(disks, "eject") as eject:
            self.gig.eject_stick("main")
        eject.assert_not_called()
        box.showinfo.assert_called_once()

    def test_never_the_drive_with_the_music(self):
        music = disks.mount_point(self.root)
        with mock.patch.object(self.gig, "_mount", return_value=None), \
                mock.patch("sortero.screens.gig.filedialog.askdirectory",
                           return_value=os.path.join(self.root, "x")), \
                mock.patch("sortero.screens.gig.messagebox") as box, \
                mock.patch.object(disks, "eject") as eject:
            self.gig.eject_stick("main")
        eject.assert_not_called()
        self.assertIn("won't eject", box.showwarning.call_args[0][1])
        self.assertTrue(music)


class SorteroWritesIt(GigWindow):
    """The main stick's "Sortero writes it" says what it writes, for the players chosen."""

    def setUp(self):
        # A set is chosen, so steps 2 and 3 aren't waiting for one
        patch = mock.patch.object(self.gig.picker, "names", return_value=["Warmup"])
        patch.start()
        self.addCleanup(patch.stop)

    def players(self, profile):
        from sortero.screens.gig import _profile_label
        before = self.gig.profile_var.get()
        self.addCleanup(self.gig.profile_var.set, before)
        self.gig.profile_var.set(_profile_label(profile))
        self.gig.open("main")
        self.app.update_idletasks()
        return self.gig.sortero_why.cget("text")

    def test_pioneer_and_denon_players_get_both_libraries(self):
        from sortero.gig import profiles
        why = self.players(profiles.EVERY)
        self.assertTrue(why.startswith("rekordbox and Engine DJ libraries, for every "
                                       "Pioneer or Denon player."), why)
        self.assertIn("hasn't been tried", why)

    def test_pioneer_players_get_rekordbox_only(self):
        from sortero.gig import profiles
        why = self.players(profiles.UNIVERSAL)
        self.assertTrue(why.startswith("rekordbox libraries, for every Pioneer player."), why)
        self.assertNotIn("Engine DJ", why)

    def test_change_players_opens_step_2s_choice(self):
        self.gig.options.pack_forget()
        self.gig.open("main")
        self.gig.change_players()
        self.app.update_idletasks()
        self.assertEqual(self.gig.open_key, "tracks")
        self.assertTrue(self.gig.options.winfo_manager())
        self.gig.change_players()           # already open: stays open
        self.assertTrue(self.gig.options.winfo_manager())


if __name__ == "__main__":
    unittest.main()

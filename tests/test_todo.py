"""The To do screen builds its cards from a real (tiny) collection.

Nothing else exercised it, so a helper deleted by mistake left To do blank in
a release while every other test passed.
"""
import os, shutil, tempfile, tkinter as tk, unittest

from sortero import library, settings


def _texts(widget, out):
    try:
        text = widget.cget("text")
        if text:
            out.append(text)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        _texts(child, out)
    return out


class TodoCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="sortero-todo-")
        for rel in ("Techno/Nora En Pure - Sphinx.mp3", "Processed/A - B.mp3",
                    "To Be Processed/C - D.mp3"):
            p = os.path.join(cls.root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").close()
        for key, value in (("root", cls.root), ("setup_complete", True),
                           ("check_updates_on_launch", False)):
            settings.set(key, value)
        from sortero.app import Sortero
        try:
            cls.app = Sortero()
        except tk.TclError as e:                  # no display to open a window on
            raise unittest.SkipTest(f"Tk can't open a window here ({e})")
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        app = getattr(cls, "app", None)
        if app:
            app.destroy()
        shutil.rmtree(cls.root, ignore_errors=True)

    def refresh(self):
        self.app.recs = library.scan(self.root)
        self.app.health = library.health(self.app.recs)
        todo = self.app.screens["todo"]
        todo.refresh()
        return " | ".join(_texts(todo.cards, []))

    def test_cards_for_what_needs_doing(self):
        text = self.refresh()
        for expected in ("with no genre", "not analysed yet", "waiting in Processed",
                         "waiting in To Be Processed"):
            self.assertIn(expected, text)
        self.assertNotIn("couldn't be worked out", text)

    def test_a_failing_check_keeps_the_cards_before_it(self):
        todo = self.app.screens["todo"]
        real = todo._checks

        def broken(app, h, live, root):
            todo._job("A card made before the failure", "")
            raise RuntimeError("boom")
        todo._checks = broken
        try:
            text = self.refresh()
        finally:
            todo._checks = real
        self.assertIn("A card made before the failure", text)
        self.assertIn("Part of To do couldn't be worked out", text)
        self.assertNotIn("Nothing needs doing", text)


class LibraryNextSteps(unittest.TestCase):
    """A list that needs a decision says what to do, and offers it."""

    @classmethod
    def setUpClass(cls):
        from mutagen.id3 import ID3, TPE1, TIT2
        cls.root = tempfile.mkdtemp(prefix="sortero-lib-")
        for name, artist, title in (
                ("040. Hraach - Una Mattina (Original Mix)_PN", "Dalal",
                 "Hraach - Una Mattina (Original Mix)"),
                ("037. DAVÍ - Boarding Call_PN", "Orbient", "DAVÍ - Boarding Call"),
                ("Andy Bach - Ipanema - Extended Mix", "Andy Bach", "Ipanema - Extended Mix")):
            p = os.path.join(cls.root, "Sultry", name + ".mp3")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tags = ID3()
            tags.add(TPE1(encoding=3, text=[artist]))
            tags.add(TIT2(encoding=3, text=[title]))
            tags.save(p)
        for key, value in (("root", cls.root), ("setup_complete", True),
                           ("check_updates_on_launch", False), ("title_artist_ignored", [])):
            settings.set(key, value)
        from sortero import fixtags
        fixtags._left_alone_cache = None
        from sortero.app import Sortero
        try:
            cls.app = Sortero()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk can't open a window here ({e})")
        cls.app.withdraw()
        cls.app.recs = library.scan(cls.root)
        cls.app.health = library.health(cls.app.recs)

    @classmethod
    def tearDownClass(cls):
        from sortero import fixtags
        settings.set("title_artist_ignored", [])
        fixtags._left_alone_cache = None
        app = getattr(cls, "app", None)
        if app:
            app.destroy()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_title_names_a_different_artist(self):
        app = self.app
        app.show_library("title_artist")
        app.update()
        lib = app.screens["library"]
        self.assertEqual(sorted(r.title for r in lib.rows),
                         ["DAVÍ - Boarding Call", "Hraach - Una Mattina (Original Mix)"])
        self.assertEqual(lib.guide.winfo_manager(), "pack")
        self.assertIn("New artist and New title columns", lib.guide_lab.cget("text"))
        shown = lib.tv.cget("displaycolumns")
        self.assertEqual(shown.index("new_artist") + 1, shown.index("new_title"))
        hraach = next(i for i in lib.tv.get_children()
                      if lib.rows[int(i)].artist == "Dalal")
        self.assertEqual((lib.tv.set(hraach, "new_artist"), lib.tv.set(hraach, "new_title")),
                         ("Hraach", "Una Mattina (Original Mix)"))
        self.assertEqual(str(lib.bar.primary.cget("state")), "disabled")
        lib.tv.selection_set(lib.tv.get_children())
        lib._sync()
        self.assertEqual(lib.bar.primary.cget("text"), "Use the title's artist for 2 tracks…")

        lib.tv.selection_set(hraach)
        lib.leave_title_artist()
        self.assertEqual([r.title for r in lib.rows], ["DAVÍ - Boarding Call"])

    def test_no_guide_or_becomes_on_plain_lists(self):
        app = self.app
        app.show_library("all")
        app.update()
        lib = app.screens["library"]
        self.assertEqual(lib.guide.winfo_manager(), "")
        self.assertNotIn("new_artist", lib.tv.cget("displaycolumns"))
        self.assertNotIn("new_title", lib.tv.cget("displaycolumns"))


class SpellingsWindow(unittest.TestCase):
    def setUp(self):
        from sortero import fixtags
        from sortero.library import Rec
        try:
            self.tk = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk can't open a window here ({e})")
        self.tk.withdraw()
        self.addCleanup(self.tk.destroy)
        settings.set(fixtags.CHOICES, {})
        self.addCleanup(settings.set, fixtags.CHOICES, {})
        self.fixtags = fixtags
        self.recs = [Rec(path=f"/m/{a}{i}.mp3", rel=f"{a}{i}.mp3", artist=a, title="T")
                     for i, a in enumerate(["Christopher Schwarzwalder"] * 3
                                           + ["Christopher Schwarzwälder", "ANNE", "ANNĒ"])]

    def test_rows_and_choices(self):
        from sortero.screens.spellings import SpellingsDialog, EditSpelling
        closed = []
        d = SpellingsDialog(self.tk, self.recs, on_close=closed.append)
        rows = {k: d.tv.item(k, "values") for k in d.tv.get_children()}
        self.assertEqual(rows["christopherschwarzwalder"][1], "Christopher Schwarzwälder")
        self.assertEqual(rows["christopherschwarzwalder"][2], "Sortero's guess")

        e = EditSpelling(d, d.rows["anne"], lambda v: d._chosen("anne", v))
        e.leave.set(True)
        e.save()
        self.assertEqual(d.tv.item("anne", "values")[1:], ("Leave these as they are",
                                                           "your choice"))
        e = EditSpelling(d, d.rows["christopherschwarzwalder"],
                         lambda v: d._chosen("christopherschwarzwalder", v))
        e.var.set("Christopher Schwarzwälder")       # the guess: no choice needed
        e.save()
        self.assertNotIn("christopherschwarzwalder", self.fixtags.spelling_choices())
        d.close()
        self.assertEqual(closed, [True])
        self.assertEqual(self.fixtags.spelled_differently(self.recs),
                         [("Christopher Schwarzwälder", ["Christopher Schwarzwalder"])])


if __name__ == "__main__":
    unittest.main()

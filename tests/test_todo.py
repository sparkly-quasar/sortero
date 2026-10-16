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


if __name__ == "__main__":
    unittest.main()

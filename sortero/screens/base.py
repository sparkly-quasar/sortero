"""What every screen in the main window has in common."""
import os
from tkinter import ttk, messagebox

from .. import ui, organize, paths
from ..common import is_spam

APP = "Sortero"
RESCAN = "Cmd-R" if paths.IS_MAC else "Ctrl+R"


def needs_genre(r):
    g = (r.genre or "").strip()
    return not g or is_spam(g)


def is_mix(r):
    """Your own set recordings: never analysed, never placed."""
    return r.is_recording or bool(r.duration and r.duration >= organize.MIX_MIN_SECONDS)


def changes(s):
    """A safety net summary as people count it: files moved and tags edited."""
    return ui.plural(s["moves"] + s["tags"] or s["operations"], "change")


def in_unsorted(r):
    return organize.UNSORTED in r.rel.split(os.sep)[:-1]


class Screen(ttk.Frame):
    """A title with a one-line summary, the screen's own content, then an action bar.

    Subclasses fill in build(). invalidate() runs after every scan; shown() runs
    each time the screen comes to the front.
    """
    title = ""
    summary = ""
    details = None
    nav = None          # sidebar entry to light up, when it isn't this screen's own
    crumb = None        # (label, screen key) for a tool that lives under another screen

    def __init__(self, parent, app, key):
        super().__init__(parent, padding=(ui.PAD, 18, ui.PAD, 14))
        self.app, self.key = app, key
        crumb = None
        if self.crumb:
            label, target = self.crumb
            crumb = (label, lambda: app.show(target))
        self.header = ui.Header(self, self.title, self.summary, self.details, crumb=crumb)
        self.header.pack(fill="x", pady=(0, ui.SECTION))
        self.bar = ui.ActionBar(self)
        self.bar.pack(side="bottom", fill="x", pady=(ui.SECTION - 4, 0))
        self.build()

    def build(self):
        pass

    def invalidate(self):
        pass

    def shown(self):
        pass

    def hidden(self):
        """Runs when another screen takes this one's place."""
        pass

    def need_scan(self):
        if self.app.recs:
            return False
        if self.app.require_root():
            messagebox.showinfo(
                APP, "Sortero is still reading your collection. Try again in a moment."
                if self.app.task.running else
                f"Sortero hasn't read your collection yet. Press {RESCAN} to read it.")
        return True

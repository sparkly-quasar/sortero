"""The free version's limit, as the rest of the app meets it.

Previews, undo, History and the safety net are never limited: only the step that
actually changes tracks asks allow() first.
"""
import tkinter as tk
from tkinter import messagebox

from . import licence

APP = "Sortero"
WHOLE_LAYOUT = ", because the whole layout moves together so your playlists stay right"


def allow(parent, count, what, split=True, why=""):
    """How many of `count` tracks this action may change now. 0 means stop."""
    cap = licence.limit()
    if cap is None or count <= cap:
        return count
    if not split:
        if messagebox.askyesno(
                APP, f"{what} would change {count:,} tracks. The free version of Sortero "
                     f"changes up to {cap} tracks at a time, and this can't be done in "
                     f"batches{why}.\n\nSee Sortero Pro?", parent=parent):
            show_pro(parent)
        return 0
    ans = messagebox.askyesnocancel(
        APP, f"The free version of Sortero changes up to {cap} tracks at a time.\n\n"
             f"{what}: do the first {cap} of {count:,} now? Run it again for the next "
             f"{cap}, or get Sortero Pro to do them all at once.\n\n"
             f"Yes does {cap} · No shows Sortero Pro · Cancel stops", parent=parent)
    if ans is None:
        return 0
    if not ans:
        show_pro(parent)
        return 0
    return cap


def take_groups(groups, cap):
    """Whole duplicate groups whose extra copies fit within the cap."""
    out, n = [], 0
    for g in groups:
        extra = len(g) - 1
        if out and n + extra > cap:
            break
        out.append(g)
        n += extra
    return out


def show_pro(parent):
    """Bring the main window to the Sortero Pro screen, even from a dialog."""
    app, w = None, parent
    while w is not None:
        if hasattr(w, "screens"):
            app = w
            break
        w = getattr(w, "master", None)
    if app is None:
        return
    if parent is not app:
        try:
            parent.grab_release()
        except tk.TclError:
            pass
    app.show("pro")
    app.lift()
    app.focus_force()

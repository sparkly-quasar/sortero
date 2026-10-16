"""Check the spellings Clean tags will give artists tagged more than one way.

One row per artist: every spelling its tags use, with how many tracks use
each, and the one it will become. Double-click a row to pick another of its
spellings, type your own, or leave that artist's tags as they are. Choices
are remembered (fixtags.choose_spelling), so the preview, the fix and the To
do card all follow them, and an artist left alone stops being flagged.
"""
import tkinter as tk
from tkinter import ttk

from .. import fixtags, ui

LEAVE_LABEL = "Leave these as they are"


def _found(spellings):
    return "  ·  ".join(f"{n} ×{c}" for n, c in spellings.most_common())


def _becomes(row):
    if row["chosen"] == fixtags.LEAVE:
        return LEAVE_LABEL
    return row["chosen"] or row["guess"]


class SpellingsDialog(tk.Toplevel):
    def __init__(self, parent, recs, on_close=None):
        super().__init__(parent)
        self.recs, self.on_close, self.changed = recs, on_close, False
        self.title("Artist spellings")
        self.transient(parent.winfo_toplevel())
        self.geometry("900x560")
        self.minsize(700, 400)

        pad = ttk.Frame(self, padding=ui.PAD)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="Artist spellings", style="Heading.TLabel").pack(anchor="w")
        ui.WrapLabel(pad, style="Muted.TLabel",
                     text="These artists are tagged more than one way. Each becomes the "
                          "spelling on the right when you clean tags. Sortero guessed it: "
                          "an accented spelling first, then the one more tracks use. "
                          "Double-click a row to pick another spelling, type your own, or "
                          "leave that artist's tags as they are. Your choices are "
                          "remembered.").pack(fill="x", pady=(2, ui.GAP))

        bottom = ttk.Frame(pad)
        bottom.pack(side="bottom", fill="x", pady=(ui.GAP, 0))
        ttk.Button(bottom, text="Done", default="active",
                   command=self.close).pack(side="right")
        self.reset_btn = ttk.Button(bottom, text="Use Sortero's guesses for all",
                                    command=self.reset_all)
        self.reset_btn.pack(side="left")

        f, self.tv = ui.tree(pad, [("found", "Spellings in your tags", 470),
                                   ("becomes", "Becomes", 250), ("who", "", 110)],
                             height=14, selectmode="browse")
        f.pack(fill="both", expand=True)
        self.tv.bind("<Double-Button-1>", lambda e: self.edit(self.tv.identify_row(e.y)))
        self.tv.bind("<Return>", lambda e: self.edit(self.tv.focus()))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.fill()
        self.grab_set()

    def fill(self):
        self.rows = {r["key"]: r for r in fixtags.spelling_review(self.recs)}
        self.tv.delete(*self.tv.get_children())
        for key, row in self.rows.items():
            self.tv.insert("", "end", iid=key, values=(
                _found(row["spellings"]), _becomes(row),
                "your choice" if row["chosen"] is not None else "Sortero's guess"))
        self.reset_btn.configure(state="normal" if any(
            r["chosen"] is not None for r in self.rows.values()) else "disabled")
        if not self.rows:
            self.tv.insert("", "end", values=("Every artist is spelled one way.", "", ""))

    def edit(self, key):
        if key not in self.rows:
            return
        EditSpelling(self, self.rows[key], lambda value: self._chosen(key, value))

    def _chosen(self, key, value):
        fixtags.choose_spelling(key, value)
        self.changed = True
        self.fill()
        if key in self.rows:
            self.tv.selection_set(key)
            self.tv.focus(key)
            self.tv.see(key)

    def reset_all(self):
        for key, row in self.rows.items():
            if row["chosen"] is not None:
                fixtags.choose_spelling(key, None)
                self.changed = True
        self.fill()

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self.on_close:
            self.on_close(self.changed)


class EditSpelling(tk.Toplevel):
    """Pick or type the spelling one artist should have."""

    def __init__(self, parent, row, on_done):
        super().__init__(parent)
        self.row, self.on_done = row, on_done
        self.title("Spell this artist")
        self.transient(parent)
        self.resizable(True, False)

        pad = ttk.Frame(self, padding=ui.PAD)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="Tagged as", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(pad, text=_found(row["spellings"])).pack(anchor="w", pady=(0, ui.GAP))

        self.leave = tk.BooleanVar(value=row["chosen"] == fixtags.LEAVE)
        current = row["chosen"] or row["guess"]
        ttk.Label(pad, text="Spell it").pack(anchor="w")
        self.var = tk.StringVar(value=row["guess"] if self.leave.get() else current)
        self.box = ttk.Combobox(pad, textvariable=self.var, width=44,
                                values=[n for n, _ in row["spellings"].most_common()])
        self.box.pack(fill="x")
        ttk.Label(pad, style="Muted.TLabel",
                  text="Pick one of its spellings, or type your own.").pack(
            anchor="w", pady=(2, ui.GAP))
        ttk.Checkbutton(pad, text=LEAVE_LABEL + " (stop flagging this artist)",
                        variable=self.leave, command=self._sync).pack(anchor="w")

        buttons = ttk.Frame(pad)
        buttons.pack(fill="x", pady=(ui.SECTION, 0))
        ttk.Button(buttons, text="Use Sortero's guess",
                   command=lambda: self._done(None)).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", default="active",
                   command=self.save).pack(side="right", padx=(0, ui.GAP))
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self._sync()
        self.grab_set()
        self.box.focus_set()
        self.box.select_range(0, "end")

    def _sync(self):
        self.box.configure(state="disabled" if self.leave.get() else "normal")

    def save(self):
        if self.leave.get():
            self._done(fixtags.LEAVE)
            return
        value = self.var.get().strip()
        if not value:
            self.box.focus_set()
            return
        # choosing exactly what Sortero would guess is the same as no choice
        self._done(None if value == self.row["guess"] else value)

    def _done(self, value):
        parent = self.master
        self.destroy()
        try:
            parent.grab_set()
        except tk.TclError:
            pass
        self.on_done(value)

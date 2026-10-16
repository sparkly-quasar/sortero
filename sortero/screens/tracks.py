"""Library: every track in one list, with the tools for fixing what's missing.

This is where the old Needs Work and Genres tabs ended up. They were two views of
the same list, so they're one list with a filter now.
"""
import os, re, threading
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, organize, genres, folders, review, paths, fixtags, settings, library
from ..common import ARTIST_TITLE, NAME_ORDERS, NAME_ORDER_LABELS
from .base import Screen, APP, needs_genre, is_mix, genre_ignored, set_genre_ignored

# A predicate of None means the screen supplies it: the filter needs to look at
# the whole collection, not one track.
FILTERS = [
    ("all", "All tracks", lambda r: True),
    ("genre", "Needs a genre", needs_genre),
    ("genre_ignored", "Ignored for genres", genre_ignored),
    ("key", "Not analysed yet (no key)", lambda r: not r.analyzed),
    ("energy", "No energy rating", lambda r: r.energy is None),
    ("artist", "No artist", lambda r: not r.artist),
    ("repeat", "Artist in the title", None),
    ("title_artist", "Title names a different artist", None),
    ("swapped", "Artist and title look swapped", None),
    ("bpm", "No BPM", lambda r: not r.bpm),
    ("bitrate", "Low bitrate (under 192 kbps)",
     lambda r: 0 < (getattr(r, "bitrate", 0) or 0) < 192000),
    ("lossless", "Lossless files (FLAC, WAV, AIFF)",
     lambda r: os.path.splitext(r.path)[1].lower() in (".flac", ".fla", ".wav", ".aif",
                                                       ".aiff")),
]
LABELS = {k: label for k, label, _ in FILTERS}

COLUMNS = [("artist", "Artist", 160), ("title", "Title", 230),
           ("new_artist", "New artist", 160), ("new_title", "New title", 230),
           ("key", "Key", 46),
           ("bpm", "BPM", 46), ("energy", "Energy", 54), ("genre", "Genre", 130),
           ("suggested", "Discogs suggests", 140), ("folder", "Folder", 180)]


# Lists that ask for a decision say what they are and what to do, right above
# the table: a filter on its own left people at a list with no way forward.
GUIDES = {
    "title_artist": "These titles start with an artist who isn't the one in the artist "
                    "tag, and the filename agrees with the title. Check the New artist "
                    "and New title columns, select the tracks it gets right (⌘A for all), and press "
                    "the button at the bottom right. For any that are right as they "
                    "are, such as a remix credited to the original artist, select them "
                    "and choose Leave as they are, so they stop being listed.",
    "repeat": "These titles have the artist in them. Clean tags fixes them all at "
              "once: press the button at the bottom right to preview the changes "
              "first. The New artist and New title columns show what each one "
              "will be.",
    "swapped": "These look like the artist and title went in the wrong way round. "
               "The New artist and New title columns show them swapped. Select "
               "the ones that are "
               "wrong and press the button at the bottom right.",
    "key": "These have no key yet. Select the ones to analyse and press the button "
           "at the bottom right: they move to 'To Be Processed' for your analysis "
           "tool.",
}
if not paths.IS_MAC:
    GUIDES["title_artist"] = GUIDES["title_artist"].replace("⌘A", "Ctrl+A")
# Lists that show what a track's artist and title will be after their fix
BECOMES = ("title_artist", "repeat", "swapped")
NEW_COLUMNS = ("new_artist", "new_title")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


def _camelot(r):
    m = re.match(r"(\d+)([AB])", r.camelot or "")
    return (int(m.group(1)), m.group(2)) if m else (99, "")


class LibraryScreen(Screen):
    title = "Library"
    summary = ("Every track in your collection. Show what needs work, select tracks, "
               "and fix them together.")
    details = ("Choose what to show, then select tracks. Shift-click selects a range. "
               "Click a column heading to sort by it: sorting by artist or folder lets "
               "you select whole groups at once. Right-click a track to edit its "
               "artist, title or album. Set a genre directly, or use More to "
               "copy genres from folder names or look them up on Discogs. Tracks that "
               "should never have a genre can be ignored from More, so they stop "
               "being flagged.\n\n"
               "Tracks with no key need your analysis tool. Show 'Not analysed yet', "
               "select them and send them to analysis: they move to 'To Be Processed', "
               "and once you've saved the results into 'Processed', To do offers to sort "
               "them back in. If you use Platinum Notes, run it before analysing, because "
               "it re-encodes the audio.\n\nEverything here can be undone from History.")

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, ui.GAP))
        # packed before the left-hand controls so its width is reserved; the
        # filter, search box and their labels together overflow this row in a
        # small window and would otherwise clip it
        self.hide_mixes = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Hide set recordings", variable=self.hide_mixes,
                        command=self.refresh).pack(side="right")
        ttk.Label(top, text="Show").pack(side="left")
        self.filter_var = tk.StringVar(value=LABELS["all"])
        box = ttk.Combobox(top, textvariable=self.filter_var, width=26, state="readonly",
                           values=[label for _, label, _ in FILTERS])
        box.pack(side="left", padx=(ui.GAP, 0))
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(top, text="Search").pack(side="left", padx=(ui.SECTION, 0))
        self.search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.search_var, width=24).pack(side="left",
                                                                    padx=(ui.GAP, 0))
        self.search_var.trace_add("write", lambda *a: self._debounce())

        # appears only while Discogs is being asked, or has answers waiting
        self.strip = ttk.Frame(self)
        self.strip_lab = ttk.Label(self.strip, style="Good.TLabel")
        self.strip_lab.pack(side="left")
        self.accept_btn = ttk.Button(self.strip, text="Accept suggestions",
                                     command=self.apply_suggested)
        self.accept_btn.pack(side="right")
        self.stop_btn = ttk.Button(self.strip, text="Stop", command=self.stop_lookup)
        self.stop_btn.pack(side="right", padx=(0, ui.GAP))

        # what the chosen list is and how to act on it (GUIDES)
        self.guide = ttk.Frame(self)
        self.guide_lab = ui.WrapLabel(self.guide)
        self.guide_lab.pack(side="left", fill="x", expand=True)
        self.leave_link = ui.link(self.guide, "Leave selected as they are",
                                  self.leave_title_artist)

        self.table, self.tv = ui.tree(self, COLUMNS, height=14)
        self.table.pack(fill="both", expand=True)
        for cid, heading, _ in COLUMNS:
            self.tv.heading(cid, command=lambda c=cid: self.sort_by(c))
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.tv.bind("<Double-Button-1>", lambda e: self.review_one_by_one())
        self.tv.bind("<Command-a>" if paths.IS_MAC else "<Control-a>",
                     lambda e: (self.select_all(), "break")[1])
        for seq in (("<Button-2>", "<Button-3>", "<Control-Button-1>") if paths.IS_MAC
                    else ("<Button-3>",)):
            self.tv.bind(seq, self._context_menu)
        self.menu = tk.Menu(self.tv, tearoff=0)
        self.menu.add_command(label="Edit details…", command=self.edit_details)
        self.menu.add_separator()
        self.menu.add_command(label="Swap artist and title…", command=self.swap_names)
        self.menu.add_command(label="Take artist and title from the title…",
                              command=self.credit_from_title)
        self.menu.add_command(label="Send to analysis…", command=self.stage)
        self.menu.add_command(label="Place one by one…", command=self.review_one_by_one)
        self.menu.add_separator()
        self.menu.add_command(label="Show in Finder" if paths.IS_MAC else "Show in folder",
                              command=self.reveal)
        self.menu.add_command(label="Copy track names", command=self.copy)

        left = self.bar.left
        self.count = ttk.Label(left, style="Muted.TLabel")
        self.count.pack(side="left")
        ttk.Label(left, text="Genre").pack(side="left", padx=(ui.SECTION, 0))
        self.genre_var = tk.StringVar()
        self.genre_box = ttk.Combobox(left, textvariable=self.genre_var, width=20)
        self.genre_box.pack(side="left", padx=(ui.GAP, 0))
        self.set_btn = ttk.Button(left, text="Set", command=self.apply_manual)
        self.set_btn.pack(side="left", padx=(ui.GAP, 0))
        self.bar.set_more([
            ("Select all", self.select_all),
            ("Edit details of selected…", self.edit_details),
            None,
            ("Look up selected on Discogs…", self.lookup),
            ("Use folder names as genres…", self.apply_from_folder),
            ("Ignore selected for genres", lambda: self.ignore_genre(True)),
            ("Stop ignoring selected for genres", lambda: self.ignore_genre(False)),
            None,
            ("Swap artist and title on selected…", self.swap_names),
            ("Take artist and title from the title on selected…", self.credit_from_title),
            ("Leave selected as they are (title names another artist)",
             self.leave_title_artist),
            ("Read artist and title from the filename…", self.names_from_file),
            None,
            ("Send selected to analysis…", self.stage),
            ("Place selected one by one…", self.review_one_by_one),
            ("Place a folder's tracks by hand…", lambda: self.app.review_folder()),
            None,
            ("Show in Finder" if paths.IS_MAC else "Show in folder", self.reveal),
            ("Copy track names", self.copy),
            None,
            ("Shrink lossless tracks to MP3 320…", self.shrink),
        ])

        self.rows, self.suggested, self._becomes = [], {}, {}
        self.sort_col, self.sort_desc = "artist", False
        self._stop = threading.Event()
        self._looking = False
        self._after = None
        self._sync()

    # -- listing -----------------------------------------------------------
    def filter_key(self):
        for k, label, _ in FILTERS:
            if label == self.filter_var.get():
                return k
        return "all"

    def _pred(self, key):
        """The test for the chosen filter, including the ones needing context."""
        if key == "swapped":
            suspect = {r.path for r in (self.app.health or {}).get("swapped", ())}
            return lambda r: r.path in suspect
        if key in ("repeat", "title_artist"):
            order = self._order()
            test = fixtags.artist_in_title if key == "repeat" else fixtags.names_other_artist
            return lambda r: test(r, order)
        return dict((k, fn) for k, _, fn in FILTERS)[key]

    def set_filter(self, key):
        self.filter_var.set(LABELS.get(key, LABELS["all"]))
        self.search_var.set("")
        self.refresh()

    def invalidate(self):
        vocab = sorted(organize.CANONICAL)
        self.genre_box.configure(values=vocab)
        self.refresh()

    def _debounce(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(250, self.refresh)

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, False
        self.refresh()

    def _sort_key(self):
        sug = self.suggested
        return {
            "artist": lambda r: ((r.artist or "~").lower(), (r.title or "").lower()),
            "title": lambda r: (r.title or "").lower(),
            "new_artist": lambda r: (self._becomes.get(r.path, ("~", ""))[0].lower(),
                                     self._becomes.get(r.path, ("", ""))[1].lower()),
            "new_title": lambda r: self._becomes.get(r.path, ("", "~"))[1].lower(),
            "key": _camelot,
            "bpm": lambda r: _num(r.bpm),
            "energy": lambda r: r.energy if r.energy is not None else -1,
            "genre": lambda r: ((r.genre or "~").lower(), (r.artist or "").lower()),
            "suggested": lambda r: (sug.get(r.path, (None,))[0] or "~").lower(),
            "folder": lambda r: r.rel.lower(),
        }[self.sort_col]

    def refresh(self):
        self._after = None
        keep = {self.rows[int(i)].path for i in self.tv.selection()
                if int(i) < len(self.rows)}
        self.tv.delete(*self.tv.get_children())
        self.rows = []
        recs = self.app.recs
        if recs:
            pred = self._pred(self.filter_key())
            q = self.search_var.get().strip().lower()
            hide = self.hide_mixes.get()
            rows = [r for r in recs
                    if not r.protected and pred(r) and not (hide and is_mix(r))
                    and (not q or q in f"{r.artist or ''} {r.title or ''} {r.rel}".lower())]
            self._becomes = self._after_fix(rows)
            rows.sort(key=self._sort_key(), reverse=self.sort_desc)
            self.rows = rows
            reselect = []
            for i, r in enumerate(rows):
                sug = self.suggested.get(r.path, (None, []))[0] or ""
                self.tv.insert("", "end", iid=str(i), values=(
                    r.artist or "—", r.title or os.path.basename(r.path),
                    *self._becomes.get(r.path, ("", "")),
                    r.camelot or r.key or "—", r.bpm or "—",
                    r.energy if r.energy is not None else "—",
                    r.genre or "—", sug, os.path.dirname(r.rel) or "(top level)"))
                if r.path in keep:
                    reselect.append(str(i))
            if reselect:
                self.tv.selection_set(reselect)
        shown = [c for c, _, _ in COLUMNS]
        if not any(self.suggested.get(r.path, (None,))[0] for r in self.rows):
            shown.remove("suggested")
        if self.filter_key() not in BECOMES:
            for c in NEW_COLUMNS:
                shown.remove(c)
        self.tv.configure(displaycolumns=shown)
        for cid, heading, _ in COLUMNS:
            arrow = (" ▼" if self.sort_desc else " ▲") if cid == self.sort_col else ""
            self.tv.heading(cid, text=heading + arrow)
        self._update_strip()
        self._update_guide()
        self._sync()

    def _after_fix(self, rows):
        """{path: (artist, title)} as the chosen list's fix would leave them."""
        key = self.filter_key()
        out = {}
        if key in ("title_artist", "repeat"):
            order = self._order()
            for r in rows:
                got = fixtags.title_credit(r, order)
                if got:
                    out[r.path] = (got[1] or r.artist or "—", got[2])
        elif key == "swapped":
            for r in rows:
                out[r.path] = (r.title or "—", r.artist or "—")
        return out

    def _update_guide(self):
        text = GUIDES.get(self.filter_key()) if self.rows else None
        if not text:
            self.guide.pack_forget()
            return
        self.guide_lab.configure(text=text)
        if self.filter_key() == "title_artist":
            self.leave_link.pack(side="right", anchor="n", padx=(ui.SECTION, 0))
        else:
            self.leave_link.pack_forget()
        if not self.guide.winfo_manager():
            self.guide.pack(fill="x", pady=(0, ui.GAP), before=self.table)

    def _sync(self):
        n = len(self.tv.selection())
        if self.app.recs:
            self.count.configure(text=ui.plural(len(self.rows), "track")
                                 + (f" · {n:,} selected" if n else ""))
        else:
            self.count.configure(text="")
        key = self.filter_key()
        if key == "key":
            self.bar.set_primary(f"Send {n:,} to analysis…" if n else "Send to analysis…",
                                 self.stage, state="normal" if n else "disabled")
        elif key == "title_artist":
            self.bar.set_primary(
                f"Use the title's artist for {ui.plural(n, 'track')}…" if n
                else "Select tracks to fix",
                self.credit_from_title, state="normal" if n else "disabled")
        elif key == "repeat":
            self.bar.set_primary(f"Fix all {len(self.rows):,} in Clean tags…",
                                 lambda: self.app.clean_tags("repeat"),
                                 state="normal" if self.rows else "disabled")
        elif key == "swapped":
            self.bar.set_primary(f"Swap {ui.plural(n, 'track')}…" if n
                                 else "Select tracks to swap",
                                 self.swap_names, state="normal" if n else "disabled")
        else:
            self.bar.set_primary("Place one by one…", self.review_one_by_one,
                                 state="normal" if self.rows else "disabled")
        self.set_btn.configure(state="normal" if n else "disabled")

    def select_all(self):
        self.tv.selection_set(self.tv.get_children())
        self._sync()

    def _selected(self):
        return [self.rows[int(i)] for i in self.tv.selection() if int(i) < len(self.rows)]

    def shrink(self):
        """Selected tracks if any, otherwise the whole collection."""
        self.app.screens["shrink"].open_for(self._selected() or None)

    def reveal(self):
        recs = self._selected()
        if not recs:
            messagebox.showinfo(APP, "Select a track first.")
            return
        paths.reveal(recs[0].path)

    def copy(self):
        recs = self._selected() or self.rows
        if not recs:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(r.display for r in recs))
        messagebox.showinfo(APP, f"Copied {ui.plural(len(recs), 'track name')}.")

    # -- analysis ----------------------------------------------------------
    def stage(self):
        recs = self._selected()
        if not recs:
            messagebox.showinfo(APP, "Select the tracks you want analysed first.")
            return
        if not messagebox.askyesno(
                APP, f"Send {ui.plural(len(recs), 'track')} to analysis?\n\n"
                     "They move into 'To Be Processed' and Sortero takes you to the "
                     "To do screen.\n\n"
                     "Then:\n"
                     "  1. Run 'To Be Processed' through your analysis tool.\n"
                     "  2. Save or move the results into 'Processed'.\n"
                     "  3. Come back to To do and press Sort them… to file them "
                     "back into your library.\n\n"
                     "If you use Platinum Notes, run it BEFORE analysing. It re-encodes "
                     "the audio.\n\nTheir playlists remember them, and this can be "
                     "undone from History."):
            return
        root = self.app.root_dir.get()
        all_recs = self.app.recs

        def work(progress, log):
            # all_recs matters: the folder's *other* tracks are what the
            # playlist is rebuilt from before these ones leave.
            return organize.stage_for_analysis(root, recs, all_recs=all_recs,
                                               log=log, progress=progress)

        def done(res):
            _, n = res
            self.app.show("todo")
            messagebox.showinfo(
                APP, f"Moved {ui.plural(n, 'track')} into 'To Be Processed'.\n\n"
                     "Next, run that folder through your analysis tool (Mixed In Key, "
                     "rekordbox, Mixxx) and save the results into 'Processed'. "
                     "Sortero doesn't move anything out of 'To Be Processed' itself.\n\n"
                     "Once they're in 'Processed', this To do screen shows a "
                     "Sort them… button that files them back into your library. "
                     "It also offers to clear out the originals left behind in "
                     "'To Be Processed'.\n\n"
                     "If you use Platinum Notes, run it FIRST. Running it after "
                     "analysis throws the analysis away.")
            self.app.changed()

        self.app.task.run(work, done, "Sending tracks to analysis")

    # -- genres ------------------------------------------------------------
    def apply_manual(self):
        recs = self._selected()
        genre = self.genre_var.get().strip()
        if not recs:
            messagebox.showinfo(APP, "Select some tracks first.")
            return
        if not genre:
            messagebox.showinfo(APP, "Pick or type a genre first.")
            return
        self._write([(r, genre) for r in recs],
                    f"Set the genre of {ui.plural(len(recs), 'track')} to '{genre}'?")

    def apply_from_folder(self):
        """Tracks already filed under a genre folder know their genre already."""
        recs = self._selected() or self.rows
        pairs = []
        for r in recs:
            d = os.path.dirname(r.rel)
            g = folders.genre_of_folder(d) if d else None
            if (g and g not in organize.RESERVED_TOP and g != organize.UNSORTED
                    and not folders.looks_like_release(g)
                    and (g in organize.CANONICAL or folders.is_genre_name(g))
                    and g != (r.genre or "").strip()):
                pairs.append((r, g))
        if not pairs:
            messagebox.showinfo(
                APP, "None of these sit in a folder named after a genre.\n\n"
                     "This copies the genre from the folder a track is already filed "
                     "in, so it can't help with tracks in Unsorted or in folders "
                     "named after a set or a release.")
            return
        self._write(pairs, f"Set {ui.plural(len(pairs), 'track')} to the genre of the "
                           "folder they're in?")

    def ignore_genre(self, on):
        """Some tracks never get a genre (intros, edits, samples): stop flagging them."""
        recs = self._selected()
        if not recs:
            messagebox.showinfo(APP, "Select some tracks first."
                                + ("" if on else "\n\nShow 'Ignored for genres' to see "
                                                 "the ones you've ignored."))
            return
        set_genre_ignored(recs, on)
        if self.app.health:
            self.app.health.update(library.genre_health(self.app.recs))
        self.refresh()
        messagebox.showinfo(
            APP, f"{ui.plural(len(recs), 'track')} "
                 + ("won't be flagged as needing a genre any more.\n\nShow 'Ignored for "
                    "genres' to find them again." if on else
                    "will be flagged again if they have no genre."))

    def review_one_by_one(self):
        recs = self._selected() or self.rows
        if not recs:
            messagebox.showinfo(APP, "Nothing to go through.")
            return
        review.ReviewDialog(self.app, self.app, recs,
                            on_close=lambda changed: self.app.changed(repair=True)
                            if changed else None)

    def apply_suggested(self):
        recs = self._selected() or self.rows
        pairs = [(r, self.suggested[r.path][0]) for r in recs
                 if self.suggested.get(r.path, (None,))[0]]
        if not pairs:
            messagebox.showinfo(APP, "No suggestions yet. Select tracks and choose "
                                     "More → Look up selected on Discogs.")
            return
        self._write(pairs, f"Accept Discogs' genre for {ui.plural(len(pairs), 'track')}?",
                    then=lambda done: [self.suggested.pop(r.path, None) for r, _ in done])

    # -- artist and title --------------------------------------------------
    def _context_menu(self, e):
        row = self.tv.identify_row(e.y)
        if not row:
            return
        # Right-clicking outside the selection acts on that one track, as in Finder.
        if row not in self.tv.selection():
            self.tv.selection_set(row)
            self.tv.focus(row)
            self._sync()
        n = len(self.tv.selection())
        self.menu.entryconfigure(0, label="Edit details…" if n == 1
                                 else f"Edit details of {n:,} tracks…")
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def edit_details(self):
        recs = [r for r in self._selected() if not r.protected]
        if not recs:
            messagebox.showinfo(APP, "Select a track first."
                                if not self._selected() else
                                "Tracks in 'To Be Processed', 'Processed' and "
                                "_Quarantine can't be edited here.")
            return
        EditDetails(self, recs, self._write_details)

    def _write_details(self, changes):
        root = self.app.root_dir.get()

        def work(progress, log):
            return fixtags.apply(root, changes, log=log, progress=progress,
                                 kind="edit-details")

        def done(res):
            _, n, failed = res
            if failed:
                messagebox.showinfo(APP, f"Updated {ui.plural(n, 'track')}.\n"
                                         f"{failed} couldn't be written.")
            self.app.changed()

        self.app.task.run(work, done, "Writing tags")

    def swap_names(self):
        """For tracks tagged the wrong way round: put each name in its own field."""
        recs = self._selected()
        if not recs:
            messagebox.showinfo(APP, "Select the tracks whose artist and title are the "
                                     "wrong way round first.\n\nShow 'Artist and title "
                                     "look swapped' to let Sortero find the likely ones, "
                                     "or sort by artist and pick them out yourself.")
            return
        changes = fixtags.swap(recs)
        if not changes:
            messagebox.showinfo(APP, "Nothing to swap: these tracks have no artist or "
                                     "title to move.")
            return
        self._write_names(changes,
                          f"Swap artist and title on {ui.plural(len(changes), 'track')}?",
                          "swap-names")

    def _order(self):
        order = settings.get("name_order")
        return order if order in NAME_ORDERS else ARTIST_TITLE

    def leave_title_artist(self):
        recs = self._selected()
        if not recs:
            messagebox.showinfo(APP, "Select the tracks that are right as they are first.")
            return
        fixtags.leave_title_artist(recs)
        self.refresh()
        self.app.refresh_todo()

    def credit_from_title(self):
        """For titles shaped "Artist - Title": the artist tag takes the credit
        and the title keeps the rest. Hraach - Una Mattina, tagged as by Dalal."""
        recs = self._selected()
        if not recs:
            messagebox.showinfo(APP, "Select the tracks whose title starts with the "
                                     "right artist first.\n\nShow 'Title names a "
                                     "different artist' to see the ones Sortero found.")
            return
        changes = fixtags.credit_from_title(recs, order=self._order())
        if not changes:
            messagebox.showinfo(APP, "Nothing to change: none of these titles start "
                                     "with an artist, like 'Hraach - Una Mattina'.")
            return
        self._write_names(changes,
                          f"Take the artist from the title of "
                          f"{ui.plural(len(changes), 'track')}?",
                          "credit-from-title")

    def names_from_file(self):
        """When the tags are a mess but the filenames are right."""
        recs = self._selected()
        if not recs:
            messagebox.showinfo(APP, "Select the tracks to re-read first.")
            return
        order = settings.get("name_order")
        order = order if order in NAME_ORDERS else ARTIST_TITLE
        changes = fixtags.from_filename(recs, order=order)
        if not changes:
            messagebox.showinfo(APP, "Nothing to change: these tags already match the "
                                     "filenames, or the filenames have no ' - ' to "
                                     "split on.")
            return
        self._write_names(
            changes, f"Take artist and title from the filename of "
                     f"{ui.plural(len(changes), 'track')}, read as "
                     f"{NAME_ORDER_LABELS[order]}?\n\n"
                     "Tidy up → Clean tags is where that order is set.",
            "names-from-filename")

    def _write_names(self, changes, question, kind):
        shown = fixtags.example_lines(changes, 4)
        more = (f"\n…and {len(changes) - len(shown):,} more."
                if len(changes) > len(shown) else "")
        if not messagebox.askyesno(APP, question + "\n\n" + "\n".join(shown) + more
                                        + "\n\nYou can undo this from History."):
            return
        root = self.app.root_dir.get()

        def work(progress, log):
            return fixtags.apply(root, changes, log=log, progress=progress, kind=kind)

        def done(res):
            _, n, failed = res
            messagebox.showinfo(APP, f"Updated {ui.plural(n, 'track')}."
                                     + (f"\n{failed} couldn't be written." if failed else ""))
            self.app.changed()

        self.app.task.run(work, done, "Writing tags")

    def _write(self, pairs, question, then=None):
        if not messagebox.askyesno(APP, question + "\n\nYou can undo this from History."):
            return
        root = self.app.root_dir.get()

        def work(progress, log):
            return genres.apply_genres(root, pairs, log=log, progress=progress)

        def done(res):
            _, n, failed = res
            messagebox.showinfo(APP, f"Updated {ui.plural(n, 'track')}."
                                     + (f"\n{failed} couldn't be written." if failed else ""))
            if then:
                then(pairs)
            self.app.changed()

        self.app.task.run(work, done, "Writing genres")

    # -- Discogs -----------------------------------------------------------
    def _update_strip(self):
        if self._looking:
            got = sum(1 for v in self.suggested.values() if v[0])
            self.strip_lab.configure(text=f"Asking Discogs… {ui.plural(got, 'suggestion')} "
                                          "so far")
            self.stop_btn.configure(state="normal")
            self.accept_btn.configure(state="disabled")
        else:
            live = {r.path for r in self.app.recs if needs_genre(r)}
            got = sum(1 for p, v in self.suggested.items() if v[0] and p in live)
            if not got:
                self.strip.pack_forget()
                return
            self.strip_lab.configure(
                text=f"Discogs suggested a genre for {ui.plural(got, 'track')}. Check the "
                     "'Discogs suggests' column, then accept.")
            self.stop_btn.configure(state="disabled")
            self.accept_btn.configure(state="normal")
        if not self.strip.winfo_ismapped():
            self.strip.pack(fill="x", pady=(0, ui.GAP), before=self.table)

    def stop_lookup(self):
        self._stop.set()
        self.strip_lab.configure(text="Stopping after the current track…")

    def _tick(self):
        """Refresh the table while a lookup runs, so results appear as they land."""
        if not self._looking:
            return
        self.refresh()
        self.after(4000, self._tick)

    def lookup(self):
        chosen = self._selected()
        recs = [r for r in chosen if genres.worth_asking(r)]
        skipped = len(chosen) - len(recs)
        if not recs:
            messagebox.showinfo(APP, "Select the tracks you want looked up."
                                     + (f"\n\n{skipped} have no artist or title to "
                                        "search with. Set those by hand instead."
                                        if skipped else ""))
            return
        cache = genres.load_cache()
        fresh = [r for r in recs if genres.key_for(r) not in cache]
        mins = genres.eta_minutes(len(fresh))
        if not messagebox.askyesno(
                APP, f"Look up {ui.plural(len(recs), 'track')} on Discogs?\n\n"
                     f"{len(recs) - len(fresh)} were looked up before and cost nothing; "
                     f"{len(fresh)} need asking, which takes about {mins} minute(s) "
                     "because Discogs limits free use.\n\n"
                     "Suggestions appear in the list as they arrive, and you can stop at "
                     "any point without losing what's been fetched."
                     + (f"\n\n{skipped} selected tracks have no artist or title and "
                        "were left out." if skipped else "")
                     + ("" if genres.token() else
                        "\n\nTip: a free Discogs token (Settings) makes this about 2.5× "
                        "faster.")
                     + "\n\nOnly artist and title are sent to Discogs."):
            return

        self._stop.clear()
        self._looking = True
        self._update_strip()
        self.after(2000, self._tick)
        results = self.suggested          # worker fills this in as it goes

        def work(progress, log):
            return genres.bulk_lookup(
                recs, progress=progress, log=log,
                on_result=lambda path, val: results.__setitem__(path, val),
                should_stop=self._stop.is_set)

        def done(found):
            self._looking = False
            got = sum(1 for r in recs if self.suggested.get(r.path, (None,))[0])
            self.refresh()
            messagebox.showinfo(
                APP, f"Looked up {ui.plural(len(found), 'track')}; {got} match a genre "
                     "Sortero recognises.\n\nCheck the 'Discogs suggests' column, then "
                     "press Accept suggestions.")

        self.app.task.run(work, done, "Asking Discogs")


class EditDetails(tk.Toplevel):
    """Change artist, title and album by hand, for one track or several at once.

    Only fields that end up different from what was shown are written, so with
    several tracks a field left blank keeps each track's own value.
    """
    FIELDS = (("artist", "Artist"), ("title", "Title"), ("album", "Album"))

    def __init__(self, screen, recs, on_save):
        super().__init__(screen)
        self.recs, self.on_save = recs, on_save
        self.single = len(recs) == 1
        self.title("Edit details" if self.single
                   else f"Edit details of {len(recs):,} tracks")
        self.transient(screen.winfo_toplevel())
        self.resizable(True, False)

        pad = ttk.Frame(self, padding=ui.PAD)
        pad.pack(fill="both", expand=True)
        pad.columnconfigure(1, weight=1)
        heading = (os.path.basename(recs[0].path) if self.single
                   else f"{len(recs):,} tracks selected")
        ttk.Label(pad, text=heading, font=ui.HEADING).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, ui.GAP))
        if not self.single:
            ttk.Label(pad, style="Muted.TLabel", wraplength=420, justify="left",
                      text="A field left blank keeps each track's own value."
                      ).grid(row=1, column=0, columnspan=2, sticky="w",
                             pady=(0, ui.GAP))

        self.shown, self.vars, first = {}, {}, None
        for i, (field, label) in enumerate(self.FIELDS, start=2):
            values = {self._current(r, field) or "" for r in recs}
            shown = values.pop() if len(values) == 1 else ""
            self.shown[field] = shown
            var = self.vars[field] = tk.StringVar(value=shown)
            ttk.Label(pad, text=label).grid(row=i, column=0, sticky="w",
                                            padx=(0, ui.GAP), pady=4)
            entry = ttk.Entry(pad, textvariable=var, width=48)
            entry.grid(row=i, column=1, sticky="ew", pady=4)
            first = first or entry

        buttons = ttk.Frame(pad)
        buttons.grid(row=9, column=0, columnspan=2, sticky="e", pady=(ui.SECTION, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", default="active",
                   command=self.save).pack(side="right", padx=(0, ui.GAP))
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        self.geometry(f"+{screen.winfo_rootx() + 80}+{screen.winfo_rooty() + 60}")
        self.grab_set()
        first.focus_set()
        first.select_range(0, "end")

    @staticmethod
    def _current(r, field):
        if field == "artist":
            return r.artist
        if field == "title":
            return r.title
        return r.album

    @staticmethod
    def _tagged(r, field):
        """What is on disk - a name read off the filename isn't tagged yet."""
        if field in ("artist", "title"):
            a, t = fixtags.tagged_names(r)
            return a if field == "artist" else t
        return r.album

    def save(self):
        edits = {}
        for field, _ in self.FIELDS:
            new = self.vars[field].get().strip()
            if new == self.shown[field].strip():
                continue
            if not new and not self.single:
                continue
            edits[field] = new or None
        changes = []
        for r in self.recs:
            ch = {f: (self._tagged(r, f), v) for f, v in edits.items()
                  if self._tagged(r, f) != v}
            if ch:
                changes.append((r, ch))
        self.destroy()
        if changes:
            self.on_save(changes)

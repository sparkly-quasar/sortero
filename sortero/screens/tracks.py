"""Library: every track in one list, with the tools for fixing what's missing.

This is where the old Needs Work and Genres tabs ended up. They were two views of
the same list, so they're one list with a filter now.
"""
import os, re, threading
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, organize, genres, folders, review, paths, pro
from .base import Screen, APP, needs_genre, is_mix

FILTERS = [
    ("all", "All tracks", lambda r: True),
    ("genre", "Needs a genre", needs_genre),
    ("key", "Not analysed yet (no key)", lambda r: not r.analyzed),
    ("energy", "No energy rating", lambda r: r.energy is None),
    ("artist", "No artist", lambda r: not r.artist),
    ("bpm", "No BPM", lambda r: not r.bpm),
    ("bitrate", "Low bitrate (under 192 kbps)",
     lambda r: 0 < (getattr(r, "bitrate", 0) or 0) < 192000),
]
LABELS = {k: label for k, label, _ in FILTERS}

COLUMNS = [("artist", "Artist", 160), ("title", "Title", 230), ("key", "Key", 46),
           ("bpm", "BPM", 46), ("energy", "Energy", 54), ("genre", "Genre", 130),
           ("suggested", "Discogs suggests", 140), ("folder", "Folder", 180)]


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
               "you select whole groups at once. Set a genre directly, or use More to "
               "copy genres from folder names or look them up on Discogs.\n\n"
               "Tracks with no key need your analysis tool. Show 'Not analysed yet', "
               "select them and send them to analysis: they move to 'To Be Processed', "
               "and once you've saved the results into 'Processed', To do offers to sort "
               "them back in. If you use Platinum Notes, run it before analysing, because "
               "it re-encodes the audio.\n\nEverything here can be undone from History.")

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, ui.GAP))
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
        self.hide_mixes = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Hide set recordings", variable=self.hide_mixes,
                        command=self.refresh).pack(side="right")

        # appears only while Discogs is being asked, or has answers waiting
        self.strip = ttk.Frame(self)
        self.strip_lab = ttk.Label(self.strip, style="Good.TLabel")
        self.strip_lab.pack(side="left")
        self.accept_btn = ttk.Button(self.strip, text="Accept suggestions",
                                     command=self.apply_suggested)
        self.accept_btn.pack(side="right")
        self.stop_btn = ttk.Button(self.strip, text="Stop", command=self.stop_lookup)
        self.stop_btn.pack(side="right", padx=(0, ui.GAP))

        self.table, self.tv = ui.tree(self, COLUMNS, height=14)
        self.table.pack(fill="both", expand=True)
        for cid, heading, _ in COLUMNS:
            self.tv.heading(cid, command=lambda c=cid: self.sort_by(c))
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.tv.bind("<Double-Button-1>", lambda e: self.review_one_by_one())
        self.tv.bind("<Command-a>" if paths.IS_MAC else "<Control-a>",
                     lambda e: (self.select_all(), "break")[1])

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
            None,
            ("Look up selected on Discogs…", self.lookup),
            ("Use folder names as genres…", self.apply_from_folder),
            None,
            ("Send selected to analysis…", self.stage),
            ("Place selected one by one…", self.review_one_by_one),
            ("Place a folder's tracks by hand…", lambda: self.app.review_folder()),
            None,
            ("Show in Finder" if paths.IS_MAC else "Show in folder", self.reveal),
            ("Copy track names", self.copy),
        ])

        self.rows, self.suggested = [], {}
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
            pred = dict((k, fn) for k, _, fn in FILTERS)[self.filter_key()]
            q = self.search_var.get().strip().lower()
            hide = self.hide_mixes.get()
            rows = [r for r in recs
                    if not r.protected and pred(r) and not (hide and is_mix(r))
                    and (not q or q in f"{r.artist or ''} {r.title or ''} {r.rel}".lower())]
            rows.sort(key=self._sort_key(), reverse=self.sort_desc)
            self.rows = rows
            reselect = []
            for i, r in enumerate(rows):
                sug = self.suggested.get(r.path, (None, []))[0] or ""
                self.tv.insert("", "end", iid=str(i), values=(
                    r.artist or "—", r.title or os.path.basename(r.path),
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
        self.tv.configure(displaycolumns=shown)
        for cid, heading, _ in COLUMNS:
            arrow = (" ▼" if self.sort_desc else " ▲") if cid == self.sort_col else ""
            self.tv.heading(cid, text=heading + arrow)
        self._update_strip()
        self._sync()

    def _sync(self):
        n = len(self.tv.selection())
        if self.app.recs:
            self.count.configure(text=ui.plural(len(self.rows), "track")
                                 + (f" · {n:,} selected" if n else ""))
        else:
            self.count.configure(text="")
        if self.filter_key() == "key":
            self.bar.set_primary(f"Send {n:,} to analysis…" if n else "Send to analysis…",
                                 self.stage, state="normal" if n else "disabled")
        else:
            self.bar.set_primary("Place one by one…", self.review_one_by_one,
                                 state="normal" if self.rows else "disabled")
        self.set_btn.configure(state="normal" if n else "disabled")

    def select_all(self):
        self.tv.selection_set(self.tv.get_children())
        self._sync()

    def _selected(self):
        return [self.rows[int(i)] for i in self.tv.selection() if int(i) < len(self.rows)]

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
        allowed = pro.allow(self.app, len(recs), "Sending tracks to analysis")
        if not allowed:
            return
        recs = recs[:allowed]
        if not messagebox.askyesno(
                APP, f"Send {ui.plural(len(recs), 'track')} to analysis?\n\n"
                     "They move into 'To Be Processed'. Run that folder through your "
                     "analysis tool and save the results into 'Processed'. To do will "
                     "then offer to sort them back into your library.\n\n"
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
            messagebox.showinfo(
                APP, f"Moved {ui.plural(n, 'track')} into 'To Be Processed'.\n\n"
                     "Next, run that folder through your analysis tool (Mixed In Key, "
                     "rekordbox, Mixxx) and save the results into 'Processed'.\n\n"
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
            if (g and g not in (organize.UNSORTED, organize.TRACKS_DIR)
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

    def _write(self, pairs, question, then=None):
        allowed = pro.allow(self.app, len(pairs), "Setting genres")
        if not allowed:
            return
        if allowed < len(pairs):
            pairs = pairs[:allowed]
            question = f"Write genres to the first {allowed} of these tracks?"
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
        allowed = pro.allow(self.app, len(recs), "Looking up on Discogs")
        if not allowed:
            return
        recs = recs[:allowed]
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

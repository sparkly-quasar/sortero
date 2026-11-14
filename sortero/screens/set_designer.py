"""Set designer: build a set one track at a time, with what fits next and why.

On the left, the set so far, each track with how well it blends from the one
before. On the right, what fits after the selected track (the last one when
nothing is selected), going the way you choose: any, build, hold or drop.
The scoring is nextup's: key, tempo and energy from the tags, plus a lift for
pairs your own playlists put next to each other. Search the library instead
to add any track, scored against the same track.

Order set puts the whole set in its smoothest order, or along an energy shape,
and Undo puts it back. Save as playlist writes it to _Playlists, where History
can undo it; Send to Gig prep does that and adds it to the gig set.

The set is remembered between launches, for the collection it was made in.
"""
import datetime, os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .. import journal, nextup, playlists, settings, ui
from ..common import fold
from ..gig import sources
from .base import Screen, APP

SET_COLUMNS = [("n", "#", 32), ("track", "Track", 150), ("key", "Key", 42),
               ("bpm", "BPM", 48), ("energy", "Energy", 56), ("blend", "Blend", 62)]
NEXT_COLUMNS = [("fit", "Fit", 38), ("track", "Track", 150), ("key", "Key", 42),
                ("bpm", "BPM", 48), ("energy", "Energy", 56), ("why", "Why", 150)]
DIRECTIONS = [("any", "Any"), ("build", "Build"), ("hold", "Hold"), ("drop", "Drop")]
SHAPE_NAMES = {label: key for key, label in nextup.SHAPES.items()}
SUGGESTIONS = 15
SEARCH_LIMIT = 200
SETTING = "set_designer"
SEND, START, CLEAR = "Send to Gig prep…", "Start from a playlist…", "Clear the set…"


def blend_word(f):
    """How a blend reads at a glance."""
    if f.clash:
        return "Clash"
    if f.score >= 0.8:
        return "Smooth"
    if f.score >= 0.6:
        return "OK"
    return "Rough"


def bpm_text(rec):
    v = nextup.bpm_of(rec)
    return "" if v is None else f"{round(v, 1):g}"


def minutes(recs):
    total = sum(r.duration or 0 for r in recs)
    if not total:
        return ""
    m = round(total / 60)
    if not m:
        return ""
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def matches(recs, query, skip=()):
    """Tracks whose artist, title or genre hold every word of the query."""
    words = fold(query).split()
    if not words:
        return []
    out = []
    for r in recs:
        if r.is_recording or os.path.normcase(r.path) in skip:
            continue
        hay = fold(" ".join(filter(None, (r.artist, r.title, r.genre,
                                          os.path.basename(r.path)))))
        if all(w in hay for w in words):
            out.append(r)
    return out


class SetDesignerScreen(Screen):
    title = "Set designer"
    summary = "Build a set one track at a time: Sortero suggests what fits next, and says why."
    details = ("Search for a track to start, or start from one of your playlists. "
               "On the right is what fits after the track selected in your set, or after "
               "the last one: the same key or one step round the Camelot wheel, a tempo "
               "within a nudge of the pitch fader (half and double time count), and energy "
               "going the way you choose. Tracks your own playlists put next to each other "
               "get a lift, and say so. Key clashes and tempo jumps are left out, unless "
               "you've paired them before.\n\n"
               "Double-click a suggestion, or press Return, to add it after the selected "
               "track. Type in Search to add any track instead; the best fits come first.\n\n"
               "Blend says how well each track follows the one before it. Order set puts the "
               "whole set in the order that flows best, or along a shape: Build up; Build, "
               "peak, land; or Waves. Undo puts it back.\n\n"
               "Save as playlist writes the set to _Playlists, and History can undo that. "
               "Send to Gig prep saves it and adds it to the end of your gig set.\n\n"
               "Suggestions come from key, BPM and energy tags, so they're only as good as "
               "your analysis: tracks without them score neither up nor down.")

    def build(self):
        self.set = []                   # the set, in order: library records
        self.rows = {}                  # suggestion iid -> record
        self.history = None             # nextup.History, read when first needed
        self._undo = None               # the order before Order set
        self._loaded_for = None         # the collection the remembered set was read for

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, ui.GAP))
        ttk.Label(top, text="Search").pack(side="left")
        self.search_var = tk.StringVar()
        self.search = ttk.Entry(top, textvariable=self.search_var, width=30)
        self.search.pack(side="left", padx=(ui.GAP, ui.SECTION))
        self.search_var.trace_add("write", lambda *a: self._debounce())
        self.search.bind("<Return>", lambda e: self.add_selected(first=True))
        self.search.bind("<Down>", lambda e: self._into_suggestions())
        self.direction = tk.StringVar(value="any")
        chips = ttk.Frame(top)
        chips.pack(side="right")
        ttk.Label(chips, text="Energy").pack(side="left", padx=(0, ui.GAP))
        for value, label in DIRECTIONS:
            ttk.Radiobutton(chips, text=label, value=value, variable=self.direction,
                            style="Chip.Toolbutton",
                            command=self.refresh_next).pack(side="left", padx=(0, 4))

        panes = self.panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        # Each table asks for the sum of its columns, and the pane gives the set
        # only what the suggestions leave, which cut the set's columns off. So
        # the two halves stay even as the window resizes, until you drag the
        # divider yourself.
        self._dragged = False
        panes.bind("<Configure>", self._even_split, add="+")
        panes.bind("<B1-Motion>", lambda e: setattr(self, "_dragged", True), add="+")
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        # ---- the set
        head = ttk.Frame(left)
        head.pack(fill="x", pady=(0, 2))
        ttk.Label(head, text="Your set", font=ui.STRONG).pack(side="left")
        self.set_label = ttk.Label(head, style="Muted.TLabel")
        self.set_label.pack(side="left", padx=(ui.GAP, 0))
        ui.link(head, START, self.start_from_playlist).pack(side="right", padx=(0, ui.GAP))
        f, self.set_tv = ui.tree(left, SET_COLUMNS, height=12)
        f.pack(fill="both", expand=True, padx=(0, ui.GAP))
        self.set_tv.tag_configure("weak", foreground=ui.c("warn"))
        self.set_tv.bind("<<TreeviewSelect>>", lambda e: self._set_selected())
        for seq in ("<Delete>", "<BackSpace>"):
            self.set_tv.bind(seq, lambda e: self.remove())
        self.set_tv.bind("<Alt-Up>", lambda e: self.move(-1) or "break")
        self.set_tv.bind("<Alt-Down>", lambda e: self.move(1) or "break")

        edit = ttk.Frame(left)
        edit.pack(fill="x", pady=(ui.GAP, 0), padx=(0, ui.GAP))
        self.up_btn = ttk.Button(edit, text="Move up", command=lambda: self.move(-1))
        self.down_btn = ttk.Button(edit, text="Move down", command=lambda: self.move(1))
        self.remove_btn = ttk.Button(edit, text="Remove", command=self.remove)
        for b in (self.up_btn, self.down_btn, self.remove_btn):
            b.pack(side="left", padx=(0, ui.GAP))

        order = ttk.Frame(left)
        order.pack(fill="x", pady=(ui.GAP, 0), padx=(0, ui.GAP))
        self.shape_var = tk.StringVar(value=nextup.SHAPES["smooth"])
        ttk.Combobox(order, textvariable=self.shape_var, state="readonly", width=17,
                     values=list(nextup.SHAPES.values())).pack(side="left")
        self.keep_first = tk.BooleanVar(value=True)
        ttk.Checkbutton(order, text="Keep the opener", variable=self.keep_first).pack(
            side="left", padx=(ui.GAP, 0))
        self.order_btn = ttk.Button(order, text="Order set", command=self.order_set)
        self.order_btn.pack(side="left", padx=(ui.GAP, 0))
        self.undo_link = ui.link(order, "Undo", self.undo_order)
        self.order_note = ttk.Label(left, style="Muted.TLabel")
        self.order_note.pack(fill="x", pady=(4, 0))

        # ---- what fits next
        self.next_label = ttk.Label(right, font=ui.STRONG)
        self.next_label.pack(fill="x", pady=(0, 2), padx=(ui.GAP, 0))
        f, self.next_tv = ui.tree(right, NEXT_COLUMNS, height=12, selectmode="browse")
        f.pack(fill="both", expand=True, padx=(ui.GAP, 0))
        self.next_tv.tag_configure("weak", foreground=ui.c("warn"))
        self.next_tv.bind("<<TreeviewSelect>>", lambda e: self._next_selected())
        self.next_tv.bind("<Double-1>", lambda e: self.add_selected())
        self.next_tv.bind("<Return>", lambda e: self.add_selected())
        add = ttk.Frame(right)
        add.pack(fill="x", pady=(ui.GAP, 0), padx=(ui.GAP, 0))
        self.add_btn = ttk.Button(add, text="Add to set", command=self.add_selected)
        self.add_btn.pack(side="left")
        self.why = ui.WrapLabel(right, style="Muted.TLabel")
        self.why.pack(fill="x", pady=(4, 0), padx=(ui.GAP, 0))

        self.bar.set_primary("Save as playlist…", self.save, state="disabled")
        self.bar.set_more([(SEND, self.send_to_gig), None,
                           (START, self.start_from_playlist), (CLEAR, self.clear)])
        self._after = None
        self._show_set()

    # ------------------------------------------------------------ lifecycle
    def shown(self):
        self._load()
        self._show_set()
        self.search.focus_set()

    def invalidate(self):
        # The collection was read again: point the set at the fresh records,
        # and read the playlists again next time they're needed.
        self.history = None
        self._loaded_for = None
        if self.app.current is self:
            self.shown()

    def _even_split(self, event):
        if not self._dragged:
            self.panes.sashpos(0, event.width // 2)

    def _root(self):
        return self.app.root_dir.get()

    def _by_path(self):
        return {os.path.normcase(r.path): r for r in self.app.recs or ()}

    def _load(self):
        root = self._root()
        if not self.app.recs or self._loaded_for == root:
            return
        self._loaded_for = root
        saved = settings.get(SETTING) or {}
        keep = self.set
        if saved.get("root") == root:
            keep = [os.path.normcase(p) for p in saved.get("paths") or ()]
        else:
            keep = [os.path.normcase(r.path) for r in keep]
        by_path = self._by_path()
        self.set = [by_path[p] for p in keep if p in by_path]

    def _remember(self):
        try:
            settings.set(SETTING, {"root": self._root(), "paths": [r.path for r in self.set]})
        except OSError:
            pass        # not being able to remember the set is no reason to stop

    def _history(self):
        if self.history is None:
            root = self._root()
            self.history = nextup.History.from_playlists(root) if root else nextup.History()
        return self.history

    # ------------------------------------------------------------ the set
    def _selected_index(self):
        sel = self.set_tv.selection()
        return int(sel[-1]) if sel else None

    def anchor(self):
        """The track suggestions follow: the one selected, else the last."""
        i = self._selected_index()
        if i is not None and i < len(self.set):
            return self.set[i]
        return self.set[-1] if self.set else None

    def _show_set(self, select=None):
        tv = self.set_tv
        tv.delete(*tv.get_children())
        h = self._history() if self.set else None
        blends = nextup.transitions(self.set, h)
        weak = 0
        for i, r in enumerate(self.set):
            word = blend_word(blends[i - 1]) if i else ""
            weak += word in ("Rough", "Clash")
            tv.insert("", "end", iid=str(i),
                      tags=("weak",) if word in ("Rough", "Clash") else (),
                      values=(i + 1, r.display, r.camelot or "", bpm_text(r),
                              r.energy if r.energy is not None else "", word))
        if self.set:
            parts = [ui.plural(len(self.set), "track")]
            if minutes(self.set):
                parts.append(minutes(self.set))
            if weak:
                parts.append(f"{weak} rough {'blend' if weak == 1 else 'blends'}")
            self.set_label.configure(text=", ".join(parts))
        else:
            self.set_label.configure(text="empty")
        if select is not None and 0 <= select < len(self.set):
            tv.selection_set(str(select))
            tv.focus(str(select))
            tv.see(str(select))
        self._sync()
        self.refresh_next()

    def _set_selected(self):
        self._sync()
        self.refresh_next()

    def _sync(self):
        i = self._selected_index()
        n = len(self.set)
        self.up_btn.configure(state="normal" if i else "disabled")
        self.down_btn.configure(state="normal" if i is not None and i < n - 1 else "disabled")
        self.remove_btn.configure(state="normal" if i is not None else "disabled")
        self.order_btn.configure(state="normal" if n >= 3 else "disabled")
        self.bar.set_primary("Save as playlist…", self.save,
                             state="normal" if n else "disabled")
        self.bar.enable(SEND, bool(n))
        self.bar.enable(CLEAR, bool(n))
        if self._undo is None:
            self.undo_link.pack_forget()

    def _changed(self, select=None, keep_undo=False):
        if not keep_undo:
            self._undo = None
            self.order_note.configure(text="")
        self._remember()
        self._show_set(select)

    def add(self, rec, after=None):
        """Put a track in the set after index `after` (the end when None)."""
        if any(os.path.normcase(r.path) == os.path.normcase(rec.path) for r in self.set):
            self.why.configure(text=f"“{rec.display}” is already in your set.")
            return
        at = len(self.set) if after is None else after + 1
        self.set.insert(at, rec)
        self._changed(select=at)

    def remove(self):
        sel = sorted((int(s) for s in self.set_tv.selection()), reverse=True)
        if not sel:
            return
        for i in sel:
            del self.set[i]
        self._changed(select=min(sel[-1], len(self.set) - 1) if self.set else None)

    def move(self, step):
        i = self._selected_index()
        j = None if i is None else i + step
        if j is None or not 0 <= j < len(self.set):
            return
        self.set[i], self.set[j] = self.set[j], self.set[i]
        self._changed(select=j)

    def clear(self):
        if not self.set or not messagebox.askyesno(
                APP, f"Clear the set? Its {ui.plural(len(self.set), 'track')} come out of "
                     "the set, not your collection. A set you saved as a playlist stays "
                     "saved."):
            return
        self.set = []
        self._changed()

    # ------------------------------------------------------------ ordering
    def order_set(self):
        if len(self.set) < 3:
            return
        shape_label = self.shape_var.get()
        shape = SHAPE_NAMES.get(shape_label, "smooth")
        before = list(self.set)
        start = before[0] if self.keep_first.get() else None
        history = self._history()

        def work(progress, log):
            return nextup.order(before, shape, start=start, history=history)

        def done(new):
            if new is None:
                return
            if [r.path for r in new] == [r.path for r in before]:
                self.order_note.configure(
                    text=f"Already in its best order for {shape_label.lower()}.")
                return
            self.set = new
            self._undo = before
            self.order_note.configure(text=f"Ordered: {shape_label.lower()}.")
            self.undo_link.pack(side="left", padx=(ui.GAP, 0))
            self._changed(keep_undo=True)

        self.app.task.run(work, done, "Ordering the set")

    def undo_order(self):
        if self._undo is None:
            return
        self.set, self._undo = self._undo, None
        self.order_note.configure(text="Back in the order you had.")
        self._changed(keep_undo=True)

    # ------------------------------------------------------------ what fits next
    def _debounce(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(200, self.refresh_next)

    def refresh_next(self):
        self._after = None
        tv = self.next_tv
        tv.delete(*tv.get_children())
        self.rows = {}
        if not self.app.recs:
            self.next_label.configure(text="What fits next")
            self.why.configure(text="Sortero hasn't read your collection yet.")
            self._next_selected()
            return
        anchor = self.anchor()
        query = self.search_var.get().strip()
        direction = None if self.direction.get() == "any" else self.direction.get()
        in_set = {os.path.normcase(r.path) for r in self.set}
        history = self._history() if anchor else None
        if query:
            found = matches(self.app.recs, query, in_set)
            if anchor:
                scored = [(r, nextup.fit(anchor, r, direction, history)) for r in found]
                scored.sort(key=lambda rf: (-rf[1].score, rf[0].display.casefold()))
            else:
                scored = [(r, None) for r in sorted(found, key=lambda r: r.display.casefold())]
            shown = scored[:SEARCH_LIMIT]
            more = f", the first {SEARCH_LIMIT} shown" if len(scored) > SEARCH_LIMIT else ""
            self.next_label.configure(
                text=f"{ui.plural(len(found), 'match', 'matches')} for “{query}”{more}"
                     + (f", best fit after “{anchor.display}” first" if anchor else ""))
        elif anchor:
            shown = nextup.suggest(anchor, self.app.recs, direction, history,
                                   exclude=self.set, n=SUGGESTIONS)
            self.next_label.configure(text=f"What fits after “{anchor.display}”")
        else:
            shown = []
            self.next_label.configure(text="What fits next")
        for i, (r, f) in enumerate(shown):
            iid = str(i)
            self.rows[iid] = (r, f)
            weak = f is not None and blend_word(f) in ("Rough", "Clash")
            tv.insert("", "end", iid=iid, tags=("weak",) if weak else (), values=(
                "" if f is None else round(f.score * 100), r.display, r.camelot or "",
                bpm_text(r), r.energy if r.energy is not None else "",
                "" if f is None else ", ".join(f.reasons)))
        if shown:
            tv.selection_set("0")
            tv.focus("0")
        self._next_selected(empty=self._empty_text(anchor, query))

    def _empty_text(self, anchor, query):
        if query:
            return f"Nothing in your collection matches “{query}”."
        if not anchor:
            return ("Search for a track to start your set, or start from one of your "
                    "playlists.")
        return (f"Nothing fits after “{anchor.display}” without a key clash or a tempo "
                "jump. Search to add any track; the closest fits come first.")

    def _next_selected(self, empty=""):
        sel = self.next_tv.selection()
        got = self.rows.get(sel[0]) if sel else None
        self.add_btn.configure(state="normal" if got else "disabled")
        if not got:
            self.why.configure(text=empty)
            return
        r, f = got
        where = self._selected_index()
        place = (f"after #{where + 1}" if where is not None and where < len(self.set) - 1
                 else "at the end")
        details = ", ".join(filter(None, (r.genre, r.camelot, bpm_text(r) and
                                          bpm_text(r) + " BPM",
                                          r.energy is not None and f"energy {r.energy}")))
        text = f"{r.display}" + (f" ({details})" if details else "")
        if f is not None:
            text += f". {'. '.join(f.reasons)}."
        self.why.configure(text=f"{text} Add puts it {place}.")

    def _into_suggestions(self):
        kids = self.next_tv.get_children()
        if kids:
            self.next_tv.focus_set()
            self.next_tv.selection_set(kids[0])
            self.next_tv.focus(kids[0])
        return "break"

    def add_selected(self, first=False):
        sel = self.next_tv.selection()
        if not sel and first:
            kids = self.next_tv.get_children()
            sel = kids[:1]
        got = self.rows.get(sel[0]) if sel else None
        if not got:
            return
        i = self._selected_index()
        if self.search_var.get():
            # Back to suggestions after what was added. Cleared first, so the
            # list add() draws is the suggestions, not an empty search.
            self.search_var.set("")
            if self._after:
                self.after_cancel(self._after)
                self._after = None
        self.add(got[0], after=i if i is not None and i < len(self.set) - 1 else None)
        return "break"

    # ------------------------------------------------------------ saving
    def save(self):
        """Write the set to _Playlists. Returns the playlist's name, or None."""
        if not self.set:
            return None
        root = self._root()
        if not root:
            self.app.require_root()
            return None
        name = simpledialog.askstring(
            APP, "Save the set as a playlist called:", parent=self,
            initialvalue=f"Set {datetime.date.today().isoformat()}")
        if not name or not name.strip():
            return None
        name = name.strip()
        target = os.path.join(playlists.playlist_dir(root), playlists.safe(name, 100) + ".m3u8")
        j = journal.Journal("save-set", root)
        if os.path.exists(target):
            if not messagebox.askyesno(
                    APP, f"There's already a playlist called “{name}”. Replace it with this "
                         "set?\n\nHistory can put the old one back."):
                return None
            try:
                with open(target, encoding="utf-8") as fh:
                    j.wrote(target, fh.read())
            except (OSError, UnicodeDecodeError) as e:
                messagebox.showwarning(APP, f"Sortero couldn't read the old playlist "
                                            f"({getattr(e, 'strerror', None) or e}), so it "
                                            "was left as it is.")
                return None
            fp = playlists.write(root, name, [r.path for r in self.set])
        else:
            fp = playlists.write(root, name, [r.path for r in self.set], journal=j)
        j.save()
        self.history = None         # the new playlist counts as history from now on
        saved = os.path.splitext(os.path.basename(fp))[0]
        self.app.log(f"set designer: saved {saved} ({len(self.set)} tracks)")
        self.order_note.configure(text=f"Saved as “{saved}” in _Playlists.")
        return saved

    def send_to_gig(self):
        name = self.save()
        if not name:
            return
        src = sources.Source(sources.SORTERO, playlists.playlist_dir(self._root()), name)
        added = self.app.screens["gig"].picker.take(src)
        said = ("and added it to the end of your gig set" if added else
                "and it's already in your gig set")
        if messagebox.askyesno(APP, f"Saved “{name}” {said}.\n\nOpen Gig prep now?"):
            self.app.show("gig")

    # ------------------------------------------------------------ from a playlist
    def start_from_playlist(self):
        if self.need_scan():
            return
        root = self._root()

        def work(progress, log):
            return sources.listed(root)

        def done(found):
            if found:
                PlaylistDialog(self, found, self._take_playlist)
            elif found is not None:
                messagebox.showinfo(APP, "Sortero can't see any playlists yet. Add some "
                                         "in Gig prep, or make one from Streaming "
                                         "playlists.")

        self.app.task.run(work, done, "Looking for playlists")

    def _take_playlist(self, src, replace):
        try:
            found = sources.tracks(src, self._root())
        except sources.SourceError as e:
            messagebox.showwarning(APP, f"Sortero can't read “{src.name}”: {e}")
            return
        by_path = self._by_path()
        recs, missing, seen = [], 0, set()
        have = set() if replace else {os.path.normcase(r.path) for r in self.set}
        for p in found:
            k = os.path.normcase(p)
            r = by_path.get(k)
            if r is None:
                missing += 1
            elif k not in seen and k not in have:
                seen.add(k)
                recs.append(r)
        self.set = recs if replace else self.set + recs
        self._changed(select=len(self.set) - 1 if self.set else None)
        note = f"From “{src.name}”: {ui.plural(len(recs), 'track')}"
        if missing:
            note += f", {missing} not in your collection left out"
        self.order_note.configure(text=note + ".")


class PlaylistDialog(tk.Toplevel):
    """Pick one playlist to start the set from, or to add to the end of it."""

    def __init__(self, screen, found, on_pick):
        super().__init__(screen)
        self.title(START.rstrip("…"))
        self.transient(screen.app)
        self.found, self.on_pick = list(found), on_pick
        frame = ttk.Frame(self, padding=ui.PAD)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Start from a playlist", style="Heading.TLabel").pack(anchor="w")
        ui.WrapLabel(frame, style="Muted.TLabel",
                     text="Its tracks come into the set in the playlist's order. Tracks "
                          "that aren't in your collection are left out.").pack(
            fill="x", pady=(2, 6))
        row = ttk.Frame(frame)
        row.pack(side="bottom", fill="x", pady=(ui.GAP, 0))
        f, self.tv = ui.tree(frame, [("name", "Playlist", 260), ("from", "From", 200)],
                             height=12, selectmode="browse")
        f.pack(fill="both", expand=True)
        for i, src in enumerate(self.found):
            self.tv.insert("", "end", iid=str(i), values=(src.name, src.where))
        self.tv.bind("<Double-1>", lambda e: self.pick(True))
        self.tv.bind("<Return>", lambda e: self.pick(True))
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right")
        self.add_btn = ttk.Button(row, text="Add to the end", command=lambda: self.pick(False))
        self.add_btn.pack(side="right", padx=(0, ui.GAP))
        self.start_btn = ttk.Button(row, text="Start from it", default="active",
                                    command=lambda: self.pick(True))
        self.start_btn.pack(side="right", padx=(0, ui.GAP))
        if not screen.set:
            self.add_btn.configure(state="disabled")
        if self.found:
            self.tv.selection_set("0")
            self.tv.focus("0")
        self.tv.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.geometry("560x440")

    def pick(self, replace):
        sel = self.tv.selection()
        if not sel:
            return
        src = self.found[int(sel[0])]
        self.destroy()
        self.on_pick(src, replace)

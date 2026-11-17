"""Set designer: build a set one track at a time, with what fits next and why.

On the left, the set so far, each track with how well it blends from the one
before. On the right, what fits after the selected track (the last one when
nothing is selected), going the way you choose: any, build, hold or drop.
The scoring is nextup's: key, tempo and energy from the tags, plus a lift for
pairs your own playlists put next to each other. Search the library instead
to add any track, scored against the same track.

Click a track in the set and the note under it says how it blends from the
one before, part by part, so a clash says whether it's the key or the tempo.
Find a bridge looks for one track, or two, to play between a pair that
doesn't blend.

Two ways to judge a set differently. Key matters less, for percussive,
drone-led music where keys rarely fight and analysers often guess them. And
a genre journey: the genres the set moves through, in order (minimal house,
then minimal techno, then hypnotic techno), read from the set or set by hand
in Stages…, so blends that move on a stage score up and ones that go back
score down.

Order set puts the whole set in its smoothest order, along an energy shape,
or along the genre journey, and Undo order puts it back. Save as playlist writes it to _Playlists, where History
can undo it; Send to Gig prep does that and adds it to the gig set.

Below both lists, a player: whatever you last picked in either list, from
the body of the track, with jumps to the intro and the outro, so a fit is
heard and not only read. Picking the next track while one plays plays that
one instead, so you can go down the suggestions listening.

The set is remembered between launches, for the collection it was made in.
"""
import collections, datetime, os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .. import journal, nextup, playlists, settings, tempo, ui
from ..common import fold
from ..gig import sources
from ..transport import Transport
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
OUTRO = 60.0        # how far from the end Outro starts: the last minute, where you mix out
BRIDGES = 12


def blend_word(f):
    """How a blend reads at a glance."""
    if f.clash:
        return "Clash"
    if f.score >= 0.8:
        return "Smooth"
    if f.score >= 0.6:
        return "OK"
    return "Rough"


def explain(f, a, b):
    """Why a blend scored as it did, part by part, in words."""
    bits = []
    k = f.parts.get("key")
    if k is None:
        bits.append("Key: not judged, one of them has no key tag")
    elif f.key_clash and not f.light_keys:
        bits.append(f"{k}: the keys are far apart on the Camelot wheel, so melodies and "
                    "basslines can fight while both play. In percussive, drone-led tracks "
                    "this often doesn't matter; try Key matters less")
    elif f.key_clash:
        bits.append(f"{k}, which counts for less here")
    else:
        bits.append(k)
    t = f.parts.get("tempo")
    if t is None:
        bits.append("Tempo: not judged, one of them has no BPM")
    elif f.tempo_jump:
        bits.append(f"{t}: more than {round(nextup.TEMPO_LIMIT * 100)}% apart, too far for "
                    "a nudge of the pitch fader. If it mixes fine for you, check both "
                    "BPM tags")
    else:
        bits.append(t)
    e = f.parts.get("energy")
    bits.append(e or "Energy: not judged, one of them has no energy rating")
    for part in ("genre", "history"):
        if f.parts.get(part):
            bits.append(f.parts[part])
    return ". ".join(bits) + "."


def causes(fits):
    """'7 key clashes, 2 tempo jumps' for the rough blends in a set: what made
    each one rough, even where your playlists vouch for the pair."""
    weak = [f for f in fits if blend_word(f) in ("Rough", "Clash")]
    keys = sum(1 for f in weak if f.key_clash and not f.light_keys)
    tempos = sum(1 for f in weak if f.tempo_jump)
    out = []
    if keys:
        out.append(f"{keys} key {'clash' if keys == 1 else 'clashes'}")
    if tempos:
        out.append(f"{tempos} tempo {'jump' if tempos == 1 else 'jumps'}")
    return ", ".join(out)


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


def undo_merge(before, now):
    """The order `before`, keeping what changed since: tracks taken out stay
    out, and a track added since goes after the one it follows now."""
    key = lambda r: os.path.normcase(r.path)
    present = {key(r) for r in now}
    out = [r for r in before if key(r) in present]
    had = {key(r) for r in out}
    for i, r in enumerate(now):
        if key(r) in had:
            continue
        prev = key(now[i - 1]) if i else None
        at = next((j + 1 for j, x in enumerate(out) if key(x) == prev), 0)
        out.insert(at, r)
        had.add(key(r))
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
               "Listen before you add: click a track in either list and the player below "
               "loads it, from the body of the track. Space plays and pauses. Intro, Body "
               "and Outro jump to the start, the middle and the last minute, so you can hear "
               "how one track ends and the next begins. While a track plays, picking another "
               "plays that one instead.\n\n"
               "Blend says how well each track follows the one before it. Click a track in "
               "your set to see why: the note under the set takes the blend into it part "
               "by part. Where two tracks don't blend, Find a bridge looks for one track, "
               "or two, to play in between.\n\n"
               "Key matters less is for percussive, drone-led music, where keys rarely "
               "fight and analysers often guess them: tempo and energy count for more, "
               "and unrelated keys alone don't make a clash.\n\n"
               "Genre journey is for a set that moves through genres, say minimal house, "
               "then minimal techno, then hypnotic techno. Sortero reads the stages from "
               "the order your set plays its genres; Stages… lets you set them, and put "
               "two genres on the same stage. Blends that move on a stage score up, "
               "going back scores down, and bridges come from the genres in between.\n\n"
               "Order set puts the whole set in the order that flows best, or along a "
               "shape: Build up; Build, peak, land; Waves; or Genre journey, which keeps "
               "each genre together in the journey's order. Undo order puts it back.\n\n"
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
        self.bridging = None            # index of the track a bridge leads into, when looking
        self.stages = None              # the journey set by hand, or None to read the set's
        self.fits = []                  # the blends in the set, as last shown

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
                            command=self._leave_bridges).pack(side="left", padx=(0, 4))

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(0, ui.GAP))
        self.light_keys = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Key matters less", variable=self.light_keys,
                        command=self._style_changed).pack(side="left")
        self.journey_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Genre journey", variable=self.journey_on,
                        command=self._style_changed).pack(side="left", padx=(ui.SECTION, 0))
        self.stages_link = ui.link(opts, "Stages…", self.edit_stages)
        self.stages_link.pack(side="left", padx=(ui.GAP, 0))
        self.journey_label = ttk.Label(opts, style="Muted.TLabel")
        self.journey_label.pack(side="left", fill="x", expand=True, padx=(ui.GAP, 0))

        # ---- listening, under both lists. Packed before the panes, which expand,
        # so the bar keeps its height in the smallest window.
        listen = ttk.Frame(self)
        listen.pack(side="bottom", fill="x", pady=(ui.GAP, 0))
        row = ttk.Frame(listen)
        row.pack(fill="x")
        jumps = ttk.Frame(row)
        jumps.pack(side="right")
        ttk.Label(jumps, text="Jump to", style="Muted.TLabel").pack(side="left")
        for label, where in (("Intro", "intro"), ("Body", "body"), ("Outro", "outro")):
            ui.link(jumps, label, lambda w=where: self.jump_to(w)).pack(side="left",
                                                                        padx=(ui.GAP, 0))
        self.listen_label = ttk.Label(row, style="Muted.TLabel")
        self.listen_label.pack(side="left", fill="x", expand=True)
        self.player = Transport(listen)
        self.player.tap_btn.pack_forget()       # tap tempo belongs to Fix wrong BPMs
        self.player.pack(fill="x", pady=(2, 0))
        self.listening = None                   # the record in the player
        # A list selecting a row by itself (after a refresh, an add, a move)
        # mustn't change what you're listening to: only your own pick does.
        self._auto = {}

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
        self._listens(self.set_tv)

        edit = ttk.Frame(left)
        edit.pack(fill="x", pady=(ui.GAP, 0), padx=(0, ui.GAP))
        self.up_btn = ttk.Button(edit, text="Move up", command=lambda: self.move(-1))
        self.down_btn = ttk.Button(edit, text="Move down", command=lambda: self.move(1))
        self.remove_btn = ttk.Button(edit, text="Remove", command=self.remove)
        for b in (self.up_btn, self.down_btn, self.remove_btn):
            b.pack(side="left", padx=(0, ui.GAP))
        self.bridge_btn = ttk.Button(edit, text="Find a bridge", command=self.find_bridge)
        self.bridge_btn.pack(side="right")
        self.blend_note = ui.WrapLabel(left, style="Muted.TLabel")
        self.blend_note.pack(fill="x", pady=(4, 0), padx=(0, ui.GAP))

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
        self.undo_btn = ttk.Button(order, text="Undo order", command=self.undo_order,
                                   state="disabled")
        self.undo_btn.pack(side="left", padx=(ui.GAP, 0))
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
        self._listens(self.next_tv)
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
        self._show_listening()
        self._show_set()

    # ------------------------------------------------------------ lifecycle
    def shown(self):
        self._load()
        self._show_set()
        self.search.focus_set()

    def hidden(self):
        self.player.stop()          # nothing keeps playing behind another screen

    def invalidate(self):
        # The collection was read again: point the set at the fresh records,
        # and read the playlists again next time they're needed.
        self.player.clear()
        self.listening = None
        self._show_listening()
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
        self.light_keys.set(bool(saved.get("light_keys")))
        self.journey_on.set(bool(saved.get("journey")))
        stages = saved.get("stages") if saved.get("root") == root else None
        self.stages = nextup.Journey.from_list(stages) if stages else None

    def _remember(self):
        try:
            settings.set(SETTING, {
                "root": self._root(), "paths": [r.path for r in self.set],
                "light_keys": self.light_keys.get(), "journey": self.journey_on.get(),
                "stages": self.stages.to_list() if self.stages else None})
        except OSError:
            pass        # not being able to remember the set is no reason to stop

    # ------------------------------------------------------------ how blends are judged
    def journey(self):
        """The genre journey: set by hand, or read from the set as it plays now."""
        return self.stages or nextup.Journey.infer(self.set)

    def style(self):
        return nextup.Style(light_keys=self.light_keys.get(),
                            journey=self.journey() if self.journey_on.get() else None)

    def _style_changed(self):
        self._remember()
        self._show_set(self._selected_index())

    def _show_journey(self):
        if not self.journey_on.get():
            self.journey_label.configure(text="")
            return
        j = self.journey()
        counts = collections.Counter(j.stage(r) for r in self.set)
        steps = []
        for stage in sorted(set(j.stages.values())):
            names = [n for n, s in j.items() if s == stage]
            steps.append(" + ".join(names) + (f" · {counts[stage]}" if counts[stage] else ""))
        text = " → ".join(steps) if steps else "No genre tags in the set yet."
        if not self.stages and steps:
            text += " (from your set)"
        self.journey_label.configure(text=text)

    def edit_stages(self):
        genres = {}
        for r in self.set:
            g = nextup.genre_of(r)
            if g:
                genres.setdefault(fold(g).strip(), g)
        current = self.journey()
        for name, _ in current.items():
            genres.setdefault(fold(name).strip(), name)
        if not genres:
            messagebox.showinfo(APP, "None of the tracks in your set has a genre tag, so "
                                     "there's no journey to set. Tidy up's genre tools can "
                                     "fill them in.")
            return
        StagesDialog(self, current, list(genres.values()), self._set_stages)

    def _set_stages(self, stages):
        self.stages = stages            # None: read them from the set again
        self.journey_on.set(True)
        self._style_changed()

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
        blends = self.fits = nextup.transitions(self.set, h, self.style())
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
                why = causes(blends)
                parts.append(f"{weak} rough {'blend' if weak == 1 else 'blends'}"
                             + (f" ({why})" if why else ""))
            self.set_label.configure(text=", ".join(parts))
        else:
            self.set_label.configure(text="empty")
        if select is not None and 0 <= select < len(self.set):
            self._select_quietly(tv, str(select))
        self._show_journey()
        self._sync()
        self._show_blend()
        self.refresh_next()

    def _set_selected(self):
        self.bridging = None
        self._sync()
        self._show_blend()
        self._picked(self.set_tv, lambda iid: self.set[int(iid)]
                     if int(iid) < len(self.set) else None)
        self.refresh_next()

    def _show_blend(self):
        """Under the set: how the selected track blends from the one before."""
        i = self._selected_index()
        if i is None or i >= len(self.set):
            self.blend_note.configure(
                text="Click a track to see how it blends from the one before." if
                len(self.set) > 1 else "")
            return
        if i == 0:
            self.blend_note.configure(text=f"#1 opens the set.")
            return
        f = self.fits[i - 1]
        word = blend_word(f)
        text = f"Into #{i + 1} from #{i}: {word}. {explain(f, self.set[i - 1], self.set[i])}"
        if word in ("Rough", "Clash"):
            text += " Find a bridge looks for a track to play between them."
        self.blend_note.configure(text=text)

    def _sync(self):
        i = self._selected_index()
        n = len(self.set)
        self.up_btn.configure(state="normal" if i else "disabled")
        self.down_btn.configure(state="normal" if i is not None and i < n - 1 else "disabled")
        self.remove_btn.configure(state="normal" if i is not None else "disabled")
        self.bridge_btn.configure(state="normal" if i and i < n else "disabled")
        self.order_btn.configure(state="normal" if n >= 3 else "disabled")
        self.bar.set_primary("Save as playlist…", self.save,
                             state="normal" if n else "disabled")
        self.bar.enable(SEND, bool(n))
        self.bar.enable(CLEAR, bool(n))
        self.undo_btn.configure(state="disabled" if self._undo is None else "normal")

    def _changed(self, select=None, keep_undo=True):
        # Undo order outlives later edits: undo_order keeps what was added or
        # taken out since. Only starting the set again forgets it.
        if not keep_undo:
            self._undo = None
        self.bridging = None
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
        self._changed(keep_undo=False)

    # ------------------------------------------------------------ ordering
    def order_set(self):
        if len(self.set) < 3:
            return
        shape_label = self.shape_var.get()
        shape = SHAPE_NAMES.get(shape_label, "smooth")
        before = list(self.set)
        start = before[0] if self.keep_first.get() else None
        history = self._history()
        style = self.style()
        if shape == "journey" and style.journey is None:
            style.journey = self.journey()

        def work(progress, log):
            return nextup.order(before, shape, start=start, history=history, style=style)

        def done(new):
            if new is None:
                return
            if [r.path for r in new] == [r.path for r in before]:
                self.order_note.configure(
                    text=f"Already in its best order for {shape_label.lower()}.")
                return
            self.set = new
            self._undo = before
            self._changed()
            self.order_note.configure(text=f"Ordered: {shape_label.lower()}. Undo order "
                                           "puts it back.")

        self.app.task.run(work, done, "Ordering the set")

    def undo_order(self):
        if self._undo is None:
            return
        self.set, self._undo = undo_merge(self._undo, self.set), None
        self._changed()
        self.order_note.configure(text="Back in the order you had before Order set.")

    # ------------------------------------------------------------ what fits next
    def _leave_bridges(self):
        self.bridging = None
        self.refresh_next()

    def _debounce(self):
        self.bridging = None
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(200, self.refresh_next)

    def refresh_next(self):
        self._after = None
        if self.bridging is not None:
            return          # the bridges stay up until you pick something else
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
        style = self.style()
        if query:
            found = matches(self.app.recs, query, in_set)
            if anchor:
                scored = [(r, nextup.fit(anchor, r, direction, history, style))
                          for r in found]
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
                                   exclude=self.set, n=SUGGESTIONS, style=style)
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
            self._select_quietly(tv, "0")
        self._next_selected(empty=self._empty_text(anchor, query))

    def _empty_text(self, anchor, query):
        if query:
            return f"Nothing in your collection matches “{query}”."
        if not anchor:
            return ("Search for a track to start your set, or start from one of your "
                    "playlists.")
        return (f"Nothing fits after “{anchor.display}” without a key clash or a tempo "
                "jump. Search to add any track; the closest fits come first.")

    def _row_rec(self, iid):
        """The track a suggestion row is: the first of a bridge."""
        got = self.rows.get(iid, (None,))[0]
        return got.tracks[0] if isinstance(got, nextup.Bridge) else got

    def _next_selected(self, empty=""):
        sel = self.next_tv.selection()
        got = self.rows.get(sel[0]) if sel else None
        self._picked(self.next_tv, self._row_rec)
        self.add_btn.configure(state="normal" if got else "disabled")
        if not got:
            self.why.configure(text=empty)
            return
        if isinstance(got[0], nextup.Bridge):
            self.why.configure(text=self._bridge_text(got[0]))
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
            self._by_hand(self.next_tv)
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
        if isinstance(got[0], nextup.Bridge):
            return self._add_bridge(got[0])
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

    # ------------------------------------------------------------ bridges
    def find_bridge(self):
        """Look for a track, or two, to play between the selected track and the
        one before it."""
        i = self._selected_index()
        if not i or i >= len(self.set) or not self.app.recs:
            return
        a, b = self.set[i - 1], self.set[i]
        history, style, recs, exclude = self._history(), self.style(), self.app.recs, list(self.set)

        def work(progress, log):
            return nextup.bridges(a, b, recs, history, style, exclude=exclude, n=BRIDGES)

        def done(found):
            if found is None or self._selected_index() != i:
                return
            self._show_bridges(i, found)

        self.app.task.run(work, done, "Looking for a bridge")

    def _show_bridges(self, i, found):
        self.bridging = i
        if self.search_var.get():
            self.search_var.set("")
            if self._after:
                self.after_cancel(self._after)
                self._after = None
        tv = self.next_tv
        tv.delete(*tv.get_children())
        self.rows = {}
        a, b = self.set[i - 1], self.set[i]
        self.next_label.configure(text=f"Bridges from #{i} to #{i + 1}, best first")
        for n, br in enumerate(found):
            iid = str(n)
            self.rows[iid] = (br, None)
            weakest = min(br.fits, key=lambda f: f.score)
            tv.insert("", "end", iid=iid, values=(
                min(100, round(br.score * 100)),
                " → ".join(r.display for r in br.tracks),
                " → ".join(r.camelot or "?" for r in br.tracks),
                " → ".join(bpm_text(r) or "?" for r in br.tracks),
                " → ".join("" if r.energy is None else str(r.energy) for r in br.tracks),
                ", ".join(weakest.reasons)))
        if found:
            self._select_quietly(tv, "0")
            self._next_selected()
        else:
            self.add_btn.configure(state="disabled")
            self.why.configure(
                text=f"Nothing in your collection gets from “{a.display}” to "
                     f"“{b.display}” in one or two tracks without a key clash or a tempo "
                     "jump. Bridges need a key and a BPM tag; Key matters less widens "
                     "the search.")

    def _bridge_text(self, br):
        i = self.bridging
        names = " then ".join(f"“{r.display}”" for r in br.tracks)
        ends = [self.set[i - 1]] + list(br.tracks) + [self.set[i]]
        steps = [f"{'. '.join(f.reasons)}" for f in br.fits]
        labels = [f"#{i} → {ends[1].display}"] + \
                 [f"→ {ends[k + 1].display}" for k in range(1, len(ends) - 1)]
        detail = " ".join(f"{lab}: {st}." for lab, st in zip(labels, steps))
        return f"Play {names} between #{i} and #{i + 1}. {detail} Add puts " \
               f"{'it' if len(br.tracks) == 1 else 'them'} in between."

    def _add_bridge(self, br):
        i = self.bridging
        have = {os.path.normcase(r.path) for r in self.set}
        tracks = [r for r in br.tracks if os.path.normcase(r.path) not in have]
        self.set[i:i] = tracks
        self._changed(select=i + len(tracks))
        self.order_note.configure(
            text=f"Bridged #{i} to #{i + len(tracks) + 1} with "
                 f"{ui.plural(len(tracks), 'track')}.")
        return "break"

    # ------------------------------------------------------------ listening
    def _listens(self, tv):
        """Your clicks and keys in a list pick what to listen to; Space plays."""
        # The keys the tables bind for themselves (ui.keyboard_ranges) are named
        # too: a binding for one key comes before <KeyPress>, which would miss them.
        for seq in ("<ButtonPress-1>", "<KeyPress>",
                    *(f"<KeyPress-{k}>" for k in ("Up", "Down", "Home", "End", "Prior",
                                                  "Next"))):
            tv.bind(seq, lambda e, t=tv: self._by_hand(t), add="+")
        tv.bind("<space>", lambda e: self.play_pause())

    def _by_hand(self, tv):
        self._auto.pop(str(tv), None)

    def _select_quietly(self, tv, iid):
        """Select a row without loading it into the player."""
        self._auto[str(tv)] = iid
        tv.selection_set(iid)
        tv.focus(iid)
        tv.see(iid)

    def _picked(self, tv, rec_of):
        """A row was selected: load it, unless the list chose it by itself."""
        sel = tv.selection()
        if len(sel) != 1 or self._auto.get(str(tv)) == sel[0]:
            return
        rec = rec_of(sel[0])
        if rec is not None:
            self.listen_to(rec)

    def listen_to(self, rec):
        """Put a track in the player, from the body of the track. If one is
        playing, this one plays instead; the same track carries on."""
        same = (self.player.path and
                os.path.normcase(self.player.path) == os.path.normcase(rec.path))
        self.listening = rec
        if not same:
            start, _ = tempo._span(rec.duration)
            self.player.load(rec.path, rec.duration, start)
        self._show_listening()

    def play_pause(self):
        if self.player.path:
            self.player.toggle()
        return "break"

    def jump_to(self, where):
        rec = self.listening
        if rec is None or not rec.duration:
            return
        d = rec.duration
        at = {"intro": 0.0, "body": tempo._span(d)[0],
              "outro": max(0.0, d - OUTRO)}[where]
        self.player.seek_to(at)

    def _show_listening(self):
        rec = self.listening
        if rec is None:
            self.listen_label.configure(
                text="Click a track in either list to listen to it. Space plays and pauses.")
            return
        details = [x for x in (rec.camelot, bpm_text(rec) and bpm_text(rec) + " BPM",
                               rec.energy is not None and f"energy {rec.energy}") if x]
        self.listen_label.configure(
            text=f"Listening to {rec.display}" + (f" ({', '.join(details)})" if details else ""))

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
        self._changed(select=len(self.set) - 1 if self.set else None, keep_undo=not replace)
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


class StagesDialog(tk.Toplevel):
    """Set the genre journey by hand: a stage for each genre in the set.
    Genres on the same stage are played together."""

    def __init__(self, screen, journey, genres, on_done):
        super().__init__(screen)
        self.title("Genre journey")
        self.transient(screen.app)
        self.on_done = on_done
        frame = ttk.Frame(self, padding=ui.PAD)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Genre journey", style="Heading.TLabel").pack(anchor="w")
        ui.WrapLabel(frame, style="Muted.TLabel",
                     text="The stages your set moves through, 1 first. Give two genres "
                          "the same number to play them together, or 0 to leave a genre "
                          "out of the journey.").pack(fill="x", pady=(2, 6))
        grid = ttk.Frame(frame)
        grid.pack(fill="both", expand=True)
        count = collections.Counter(fold(nextup.genre_of(r) or "").strip() for r in screen.set)
        stage_of = {fold(n).strip(): s for n, s in journey.items()}
        order = sorted(genres, key=lambda g: (stage_of.get(fold(g).strip(), 99), g.casefold()))
        top = max(len(order), 1)
        self.vars = []
        for row, g in enumerate(order):
            k = fold(g).strip()
            ttk.Label(grid, text=g).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Label(grid, text=ui.plural(count.get(k, 0), "track"),
                      style="Muted.TLabel").grid(row=row, column=1, sticky="w",
                                                 padx=(ui.GAP, ui.GAP))
            v = tk.IntVar(value=stage_of[k] + 1 if k in stage_of else 0)
            ttk.Spinbox(grid, from_=0, to=top, width=4, textvariable=v).grid(
                row=row, column=2, sticky="e")
            self.vars.append((g, v))
        grid.columnconfigure(0, weight=1)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(ui.GAP, 0))
        ttk.Button(row, text="Read it from the set", command=self.from_set).pack(side="left")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Use these stages", default="active",
                   command=self.done).pack(side="right", padx=(0, ui.GAP))
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.done())

    def stages(self):
        """The stages as entered, renumbered from 0 with no gaps."""
        picked = []
        for g, v in self.vars:
            try:
                n = int(v.get())
            except (tk.TclError, ValueError):
                n = 0
            if n > 0:
                picked.append((g, n))
        rank = {n: i for i, n in enumerate(sorted({n for _, n in picked}))}
        return nextup.Journey({g: rank[n] for g, n in picked})

    def done(self):
        j = self.stages()
        self.destroy()
        self.on_done(j if j.stages else None)

    def from_set(self):
        self.destroy()
        self.on_done(None)

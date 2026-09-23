"""Tidy up: tools for fixing a collection that already exists."""
import collections, os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from .. import ui, organize, dupes, fixtags, settings, bpmfix, mixxx, tempo
from ..transport import Transport
from ..common import (human_size, name_order_note, ARTIST_TITLE, NAME_ORDERS,
                      NAME_ORDER_LABELS)
from .base import Screen, APP, RESCAN, is_mix


class TidyUpScreen(Screen):
    title = "Tidy up"
    summary = ("Tools for fixing the collection you already have. Each one shows you "
               "what it will do before anything changes.")

    def build(self):
        self.bar.pack_forget()
        app = self.app
        for title, text, button, command in (
                ("Clean tags",
                 "Clear download-site spam from tags, fill in missing artists, put "
                 "artist and title the right way round, and make energy ratings "
                 "visible to DJ apps.", "Open", lambda: app.show("tags")),
                ("Fix wrong BPMs",
                 "Find tracks whose BPM is a half, two-thirds or three-quarters of the "
                 "real tempo, and correct them in the tags and in Mixxx.", "Open",
                 lambda: app.show("bpm")),
                ("Find duplicates",
                 "Find extra copies of the same track and set them aside. Nothing is "
                 "deleted.", "Open", lambda: app.show("dupes")),
                ("Place a folder's tracks by hand",
                 "Listen to each track in one folder and choose where it goes.",
                 "Choose folder…", app.review_folder),
                ("Flatten release folders",
                 "Lift tracks out of EP and album folders into the genre folder above.",
                 "Open…", app.open_flatten),
                ("Fix playlist links",
                 "Re-link playlist entries whose files have moved or changed format.",
                 "Check…", app.repair_playlists),
                ("Reorganise the whole collection",
                 "Move every track into one genre-folder layout, keeping your current "
                 "folders as playlists.", "Open", lambda: app.show("reorganise"))):
            ui.Card(self, title, text, button, command).pack(fill="x", pady=(0, ui.GAP))


class ToolScreen(Screen):
    nav = "tidy"
    crumb = ("Tidy up", "tidy")


# ------------------------------------------------------------------ tags
# The three jobs this screen can stage. They all preview into the same table,
# so everything the user reads on the way to committing has to say which one is
# pending: the line above the list, the button, and the question at the end.
JOBS = {
    "fixes": {
        "kind": "fixtags",
        "pending": "Cleaning up tags",
        "button": "Write to {n}…",
        "confirm": "Write cleaned-up tags to {n}?",
        "empty": "Nothing to fix. Your tags are already clean.",
    },
    "swap": {
        "kind": "swap-names",
        "pending": "Swapping artist and title on every track",
        "button": "Swap {n}…",
        "confirm": "Swap artist and title on {n}?",
        "empty": "Nothing to swap: no track has an artist or title tag to move.",
    },
    "names": {
        "kind": "names-from-filename",
        "pending": "Reading artist and title from the filenames",
        "button": "Write names to {n}…",
        "confirm": "Overwrite artist and title on {n} with what their filenames say?",
        "empty": "Nothing to change: the tags already match the filenames.",
    },
}


class CleanTagsScreen(ToolScreen):
    title = "Clean tags"
    summary = ("Fix common tag problems across your collection. Every change is listed "
               "before anything is written.")
    details = ("Download sites put their names into genre and comment tags, which then "
               "clutter your DJ software. Sortero can clear those, take a missing artist "
               "from the filename, tidy genre names, and copy Mixed In Key's energy rating "
               "into a field DJ apps display. Tick what you want, preview, then write.\n\n"
               "Sortero reads a filename like 'Deadmau5 - Strobe' as artist first. If "
               "yours are the other way round, say so under 'Filenames are' and the "
               "artist goes in the artist tag, not the title.\n\n"
               "Tags that went in backwards are a separate job, in the panel below. "
               "Sortero counts the tracks whose tags look swapped and leads with "
               "whichever is likelier to be what you want: picking out the ones it has "
               "spotted, or swapping the whole collection.\n\n"
               "Whichever job you run, the line above the list names it, so does the "
               "button, and so does the question before anything is written. You can "
               "undo any of it from History.")

    def build(self):
        box = ttk.Frame(self)
        box.pack(fill="x", pady=(0, ui.GAP))
        self.vars = {}
        for k in fixtags.FIXES:
            v = tk.BooleanVar(value=True)
            v.trace_add("write", lambda *a: self._reset(clear=True))
            self.vars[k] = v
            ttk.Checkbutton(box, text=fixtags.FIX_LABELS[k], variable=v).pack(anchor="w",
                                                                           pady=2)
        row = ttk.Frame(box)
        row.pack(anchor="w", fill="x", pady=(ui.GAP, 0))
        ttk.Label(row, text="Filenames are").pack(side="left")
        self.order_box = ttk.Combobox(row, state="readonly", width=16,
                                      values=[NAME_ORDER_LABELS[o] for o in NAME_ORDERS])
        self.order_box.set(NAME_ORDER_LABELS[self.order()])
        self.order_box.pack(side="left", padx=(ui.GAP, 0))
        self.order_box.bind("<<ComboboxSelected>>", lambda e: self._order_chosen())
        self.detected = ui.WrapLabel(row, style="Muted.TLabel")
        self.detected.pack(side="left", fill="x", expand=True, padx=(ui.GAP, 0))

        # The swap used to sit in the More menu, where nobody found it. It is a
        # job of its own, with its own scope, so it gets a panel of its own -
        # and the count decides which way round to offer it, because a handful
        # of backwards tracks and a wholly backwards collection want opposite
        # things.
        self.card_wrap = ttk.Frame(self)
        self.card_wrap.pack(fill="x", pady=(ui.SECTION, ui.SECTION))

        self.pending = ttk.Label(self)
        self.pending.pack(anchor="w")
        self.result = ttk.Label(self, style="Muted.TLabel")
        self.result.pack(anchor="w", pady=(0, 4))
        f, self.tv = ui.tree(self, [("track", "Track", 300), ("field", "Tag", 80),
                                    ("before", "Now", 240), ("after", "Becomes", 240)],
                             height=11)
        f.pack(fill="both", expand=True)
        self.bar.set_more([
            ("Read artist and title from the filenames again…", self.preview_from_names),
        ])
        self.changes = None
        self.job = "fixes"
        self._build_card()
        self._reset()

    def invalidate(self):
        self._show_detected()
        self._build_card()
        self._reset(clear=True)

    # -- the artist/title panel -------------------------------------------
    def _suspects(self):
        """Tracks Sortero can show evidence for, and the pool it judged them in."""
        h = self.app.health or {}
        suspects = [r for r in h.get("swapped", []) if not is_mix(r)]
        judged = [r for r in (self.app.recs or [])
                  if not r.protected and not is_mix(r)
                  and r.artist and r.title
                  and not r.artist_from_name and not r.title_from_name]
        return suspects, judged

    def _build_card(self):
        for w in self.card_wrap.winfo_children():
            w.destroy()
        suspects, judged = self._suspects()
        n = len(suspects)
        share = n / len(judged) if judged else 0.0
        title = "Artist and title the wrong way round"
        # Lead with the whole-collection swap only when the evidence is that the
        # whole collection is the problem. With a handful of suspects the
        # precise instrument is the right one to offer first.
        if n and share < 0.5:
            text = (f"{ui.plural(n, 'track')} here have the title sitting in the artist "
                    "tag. Check those and swap just them, or swap the whole collection "
                    "if it all went in backwards.")
            card = ui.Card(self.card_wrap, title, text,
                           f"Show the {n:,} tracks",
                           lambda: self.app.show_library("swapped"),
                           link_text="Swap every track's artist and title…",
                           link_command=self.preview_swap)
        else:
            if n:
                text = (f"{ui.plural(n, 'track')} of the {len(judged):,} Sortero can "
                        "check have the title sitting in the artist tag. If the whole "
                        "collection went in this way round, swap it in one go - you "
                        "will see every change before anything is written.")
            else:
                text = ("Nothing here looks backwards to Sortero. If you know your tags "
                        "went in the wrong way round anyway, swap them - you will see "
                        "every change before anything is written.")
            card = ui.Card(self.card_wrap, title, text,
                           "Swap every track…", self.preview_swap,
                           link_text=(f"Show the {n:,} it is sure about" if n else None),
                           link_command=(lambda: self.app.show_library("swapped"))
                           if n else None)
        card.pack(fill="x")

    # -- how filenames are read -------------------------------------------
    def order(self):
        o = settings.get("name_order")
        return o if o in NAME_ORDERS else ARTIST_TITLE

    def _order_chosen(self):
        chosen = next((o for o in NAME_ORDERS
                       if NAME_ORDER_LABELS[o] == self.order_box.get()), ARTIST_TITLE)
        if chosen == self.order():
            return
        settings.set("name_order", chosen)
        self._show_detected()
        self._reset(clear=True)
        # Tracks with no artist tag are shown under the name Sortero read off
        # the filename, so the whole app has to read them again the new way.
        if self.app.recs and not self.app.task.running:
            self.app.scan()

    def _show_detected(self):
        """Say what the collection itself suggests, when it suggests anything."""
        votes = (self.app.health or {}).get("name_order")
        note = name_order_note(votes)
        if not note:
            self.detected.configure(text="")
        elif votes["order"] == self.order():
            self.detected.configure(text="Matches how your filenames look.")
        else:
            self.detected.configure(text=note)

    # -- previewing --------------------------------------------------------
    def _reset(self, clear=False):
        self.changes = None
        self.job = "fixes"
        self.pending.configure(text="")
        if clear:
            self.tv.delete(*self.tv.get_children())
            self.result.configure(text="")
        self.bar.set_primary("Preview changes", self.preview)

    def _preview_fn(self, job):
        return {"fixes": self.preview, "swap": self.preview_swap,
                "names": self.preview_from_names}[job]

    def preview(self):
        if self.need_scan():
            return
        fixes = {k for k, v in self.vars.items() if v.get()}
        if not fixes:
            messagebox.showinfo(APP, "Tick at least one fix.")
            return
        order = self.order()

        def work(progress, log):
            return fixtags.plan(self.app.recs, fixes, order=order)

        self.app.task.run(work, lambda ch: self._show(ch, "fixes"), "Checking tags")

    def preview_swap(self):
        """Straight to the preview: staging a swap writes nothing, and a warning
        here is a click to get past rather than a fact to weigh. The weighing
        belongs at the write, where the list and the evidence are both in hand."""
        if self.need_scan():
            return

        def work(progress, log):
            return fixtags.swap([r for r in self.app.recs if not r.protected])

        self.app.task.run(work, lambda ch: self._show(ch, "swap"), "Reading tags")

    def preview_from_names(self):
        if self.need_scan():
            return
        order = self.order()

        def work(progress, log):
            return fixtags.from_filename([r for r in self.app.recs if not r.protected],
                                         order=order)

        self.app.task.run(work, lambda ch: self._show(ch, "names"), "Reading filenames")

    def _show(self, changes, job):
        spec = JOBS[job]
        self.job = job
        self.tv.delete(*self.tv.get_children())
        shown = 0
        for r, ch in changes:
            for field, (old, new) in ch.items():
                if shown >= 2000:
                    break
                self.tv.insert("", "end", values=(
                    os.path.basename(r.path), field,
                    "" if old is None else str(old)[:120],
                    "(cleared)" if new is None else str(new)[:120]))
                shown += 1
        if not changes:
            self.changes = None
            self.pending.configure(text="")
            self.result.configure(text=spec["empty"])
            # "again" has to mean this job again, not whichever ran first
            self.bar.set_primary("Preview again", self._preview_fn(job))
            return
        n = ui.plural(len(changes), "file")
        self.pending.configure(text=f"{spec['pending']} · {n}")
        c = fixtags.summarize(changes)
        self.result.configure(text=" · ".join(f"{v} {k}" for k, v in c.most_common()))
        self.changes = changes
        self.bar.set_primary(spec["button"].format(n=n), lambda: self.apply(job))

    def _evidence_note(self):
        """What the collection says about a swap - agreeing or arguing back.

        Friction that scales with the number of files punishes the person whose
        whole library really is backwards, who has the most files and the least
        doubt. What should scale is how much the evidence disagrees.
        """
        suspects, judged = self._suspects()
        n, total = len(suspects), len(self.changes)
        if not n:
            return ("Sortero cannot see a single track whose tags look backwards, so "
                    "this may not be what you want.")
        if n * 4 < total:
            return (f"Only {n:,} of them look backwards to Sortero, so this goes a good "
                    "deal further than what it can see.")
        return f"{n:,} of them look backwards to Sortero, so this fits."

    def apply(self, job):
        if not self.changes:
            return
        spec = JOBS[job]
        msg = spec["confirm"].format(n=ui.plural(len(self.changes), "file"))
        lines = fixtags.example_lines(self.changes)
        if lines:
            msg += "\n\n" + "\n".join(lines)
            if len(self.changes) > len(lines):
                msg += f"\n…and {len(self.changes) - len(lines):,} more."
        if job == "swap":
            msg += "\n\n" + self._evidence_note()
            msg += "\n\nYou can undo this from History, and swapping twice puts it back."
        else:
            msg += "\n\nYou can undo this from History."
        if not messagebox.askyesno(APP, msg):
            return
        root = self.app.root_dir.get()
        changes = self.changes

        def work(progress, log):
            return fixtags.apply(root, changes, log=log, progress=progress,
                                 kind=spec["kind"])

        def done(res):
            _, n, failed = res
            messagebox.showinfo(APP, f"Updated {ui.plural(n, 'file')}."
                                     + (f"\n{failed} couldn't be written." if failed else ""))
            self.app.changed()

        self.app.task.run(work, done, "Writing tags")


# ------------------------------------------------------------------ BPM
class FixBpmScreen(ToolScreen):
    title = "Fix wrong BPMs"
    summary = ("Find tracks tagged at a fraction of their real tempo, like a 140 techno "
               "track tagged 93, and correct them.")
    details = ("Beat detection often locks on to the wrong pulse: rolling percussion "
               "makes a 140 track read as 93.33, and a sparse one as 70. Sortero looks at "
               "tracks whose BPM is unusual for their genre, listens to a minute of each, "
               "and only suggests a fix when the audio clearly supports a whole multiple "
               "of the current value (x2, x3/2, x4/3). Genres without a typical tempo, "
               "like downtempo and breaks, are left alone.\n\n"
               "Mixxx keeps its own BPM and beatgrid and ignores the tag once it has "
               "analysed a track, so Sortero can correct those too while Mixxx is closed. "
               "It backs up Mixxx's library first and locks each corrected BPM so a "
               "re-analysis doesn't bring the wrong one back. You can undo it from History.")

    COLS = [("track", "Track", 300), ("genre", "Genre", 130), ("ratio", "Fix", 50),
            ("tag", "Tag", 110), ("mixxx", "Mixxx", 130)]

    def build(self):
        # Which part of the collection to check: "" for all of it, else a folder
        # relative to the collection root. Remembered between runs.
        self.folder = settings.get("bpm_folder") or ""
        self.scope = tk.StringVar(value="folder" if self.folder else "all")
        self.scope.trace_add("write", lambda *a: self._scope_changed())
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Check").pack(side="left", padx=(0, ui.GAP))
        ttk.Radiobutton(row, text="the whole collection", value="all",
                        variable=self.scope).pack(side="left", padx=(0, ui.GAP))
        self.folder_radio = ttk.Radiobutton(row, value="folder", variable=self.scope)
        self.folder_radio.pack(side="left")
        ttk.Button(row, text="Choose folder…", command=self.choose_folder
                   ).pack(side="left", padx=(ui.GAP, 0))
        self._label_folder()

        self.db = mixxx.find_db()
        self.use_mixxx = tk.BooleanVar(value=bool(self.db))
        if self.db:
            self.use_mixxx.trace_add("write", lambda *a: self._reset(clear=True))
            ttk.Checkbutton(self, text="Also correct Mixxx's library (quit Mixxx before "
                                       "fixing)", variable=self.use_mixxx
                            ).pack(anchor="w", pady=(0, ui.SECTION))
        self.result = ttk.Label(self, style="Muted.TLabel")
        self.result.pack(anchor="w", pady=(0, 4))
        f, self.tv = ui.tree(self, self.COLS, height=12)
        f.pack(fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._select())
        self.tv.bind("<Double-1>", lambda e: self.player.toggle())
        self.tv.bind("<KeyPress-t>", lambda e: self.player.tap())

        # Listen before you fix: select a row to load it, tap along to the kick.
        self.player = Transport(self, on_tap=self._tapped)
        self.player.pack(fill="x", pady=(ui.GAP, 2))
        self.listen = ttk.Label(self, style="Muted.TLabel",
                                text="Select a track to listen to it. Tap along to the kick "
                                     "(the Tap button, or T) to check the fix by ear.")
        self.listen.pack(anchor="w")
        self.current = None

        self.bar.set_more([("Leave out selected tracks", self.leave_out),
                           None,
                           ("Check again", self.preview)])
        self._reset()

    def invalidate(self):
        self._reset(clear=True)

    def hidden(self):
        self.player.stop()

    def _reset(self, clear=False):
        self.fixes = None
        if clear:
            self.tv.delete(*self.tv.get_children())
            self.result.configure(text="")
            self._select()
        self.bar.set_primary("Check BPMs", self.preview)
        self.bar.enable("Leave out selected tracks", False)

    def _label_folder(self):
        if self.folder:
            self.folder_radio.configure(text=f"only {self.folder}", state="normal")
        else:
            self.folder_radio.configure(text="one folder", state="disabled")

    def _scope_changed(self):
        if self.scope.get() == "folder" and not self.folder:
            self.choose_folder()
            return
        settings.set("bpm_folder", self.folder if self.scope.get() == "folder" else "")
        self._reset(clear=True)

    def choose_folder(self):
        root = self.app.require_root()
        if not root:
            return
        rootn = os.path.normpath(root)
        start = os.path.join(rootn, self.folder) if self.folder else rootn
        d = filedialog.askdirectory(title="Choose a folder to check",
                                    initialdir=start if os.path.isdir(start) else rootn)
        if not d:
            if not self.folder:
                self.scope.set("all")
            return
        # The picker can hand back ~/Dropbox/... for a collection stored as
        # ~/Library/CloudStorage/Dropbox/..., so compare real paths.
        real_root, real_d = os.path.realpath(rootn), os.path.realpath(d)
        if real_d == real_root:
            self.scope.set("all")
            return
        if not real_d.startswith(real_root + os.sep):
            messagebox.showinfo(APP, "Pick a folder inside your collection.")
            if not self.folder:
                self.scope.set("all")
            return
        self.folder = os.path.relpath(real_d, real_root)
        self._label_folder()
        if self.scope.get() != "folder":
            self.scope.set("folder")        # runs _scope_changed
        else:
            self._scope_changed()

    def _recs(self):
        """The tracks in scope: all of them, or those under the chosen folder."""
        if self.scope.get() != "folder" or not self.folder:
            return self.app.recs
        pre = self.folder + os.sep
        return [r for r in self.app.recs if r.rel.startswith(pre)]

    @staticmethod
    def _change(old, new):
        return f"{old} → {new}"

    # -- listening -------------------------------------------------------
    @staticmethod
    def _values(f):
        """(current, suggested) BPM - the tag's if it has one, else Mixxx's."""
        if f.tag:
            return bpmfix._num(f.tag[0]), bpmfix._num(f.tag[1])
        return f.mixxx["old_bpm"], f.mixxx["new_bpm"]

    def _select(self):
        sel = self.tv.selection()
        f = None
        if sel and self.fixes:
            i = self.tv.index(sel[0])
            f = self.fixes[i] if i < len(self.fixes) else None
        if f is self.current:
            return
        self.current = f
        if f is None:
            self.player.clear()
            return
        # Start where Sortero listened: past the intro, in the body of the track.
        start, _ = tempo._span(f.rec.duration)
        self.player.load(f.rec.path, f.rec.duration, start)

    def _tapped(self, bpm):
        f = self.current
        if f is None:
            return
        old, new = self._values(f)
        head = f"{os.path.basename(f.rec.path)}: {old:g} now, {new:g} suggested"
        if bpm is None:
            self.listen.configure(text=f"{head}. Tap along to the kick (Tap, or T) to check.")
            return
        self.listen.configure(text=f"{head} · you're tapping {bpm:.1f} · "
                                   + bpmfix.verdict(bpm, old, new))

    def _show(self):
        self.current = None
        self.player.clear()
        self.tv.delete(*self.tv.get_children())
        for f in self.fixes:
            m = f.mixxx
            self.tv.insert("", "end", values=(
                os.path.basename(f.rec.path), f.genre, bpmfix.RATIO_NAMES[f.ratio],
                self._change(*f.tag[:2]) if f.tag else "",
                self._change(f"{m['old_bpm']:.2f}", f"{m['new_bpm']:.2f}") if m else ""))
        n = len(self.fixes)
        if n:
            self.bar.set_primary(f"Fix {ui.plural(n, 'track')}…", self.apply)
        else:
            self.bar.set_primary("Check again", self.preview)
        self.bar.enable("Leave out selected tracks", bool(n))

    def preview(self):
        if self.need_scan():
            return
        use = self.use_mixxx.get()
        recs = self._recs()
        if not recs:
            messagebox.showinfo(APP, f"There are no tracks in {self.folder}. Choose another "
                                     "folder, or read your collection again if you've "
                                     f"added some ({RESCAN}).")
            return

        def work(progress, log):
            return bpmfix.plan(recs, use_mixxx=use, progress=progress, log=log)

        def done(res):
            self.fixes, st = res
            bits = [ui.plural(len(self.fixes), "track") + " to fix",
                    f"{st['checked']:,} with an unusual BPM listened to"]
            if st["unclear"]:
                bits.append(f"{st['unclear']:,} left alone because the audio wasn't clear-cut")
            if st["unreadable"]:
                bits.append(f"{st['unreadable']:,} couldn't be read")
            if st["mixxx_skipped"]:
                bits.append(f"{st['mixxx_skipped']:,} locked or hand-edited in Mixxx, "
                            "skipped there")
            self.result.configure(text=" · ".join(bits) if self.fixes else
                                  "No wrong BPMs found. " + " · ".join(bits[1:]))
            self._show()

        self.app.task.run(work, done, "Listening")

    def leave_out(self):
        sel = self.tv.selection()
        if not self.fixes or not sel:
            messagebox.showinfo(APP, "Select one or more rows in the list first.")
            return
        drop = {self.tv.index(i) for i in sel}
        self.fixes = [f for i, f in enumerate(self.fixes) if i not in drop]
        self._show()

    def apply(self):
        if not self.fixes:
            return
        in_mixxx = sum(1 for f in self.fixes if f.mixxx)
        if in_mixxx and mixxx.running():
            messagebox.showinfo(APP, "Quit Mixxx first, then try again.\n\nMixxx keeps its "
                                     "library in memory and would overwrite these changes "
                                     "when it closes.")
            return
        tags = sum(1 for f in self.fixes if f.tag)
        msg = f"Correct the BPM tag in {ui.plural(tags, 'file')}"
        if in_mixxx:
            msg += (f" and {ui.plural(in_mixxx, 'track')} in Mixxx's library "
                    "(backed up first)")
        if not messagebox.askyesno(APP, msg + "?\n\nYou can undo this from History."):
            return
        root = self.app.root_dir.get()
        fixes = self.fixes

        def work(progress, log):
            return bpmfix.apply(root, fixes, log=log, progress=progress)

        def done(res):
            _, failed = res
            messagebox.showinfo(APP, "BPMs fixed." + (
                f" {ui.plural(failed, 'file')} couldn't be written." if failed else ""))
            self.app.changed()

        self.app.task.run(work, done, "Fixing BPMs")


# ------------------------------------------------------------ duplicates
class DuplicatesScreen(ToolScreen):
    title = "Duplicates"
    summary = "Find extra copies of the same track and set them aside. Nothing is deleted."
    details = ("Exact copies have identical audio. Likely copies share artist, title and "
               "version and are the same length. Different remixes are never grouped "
               "together. The best copy stays (lossless and higher bitrate win) and the "
               "extras move to _Quarantine, so you can check them before deleting "
               "anything yourself. You can undo it from History.")

    def build(self):
        self.result = ttk.Label(self, style="Muted.TLabel")
        self.result.pack(anchor="w", pady=(0, 4))
        f, self.tv = ui.tree(self, [("group", "Group", 100), ("keep", "", 60),
                                    ("format", "Format", 70), ("length", "Length", 70),
                                    ("path", "File", 560)], height=15)
        f.pack(fill="both", expand=True)
        # The count of likely copies is printed above, so the way to act on it
        # belongs on the screen too. It used to be a menu item, which left the
        # screen saying "17 likely groups (1.2 GB)" over a button that offered
        # to search again.
        self.also = ttk.Button(self.bar.left, command=lambda: self.quarantine("all"))
        self.bar.set_more([("Search again", self.find)])
        self.found = None
        self._reset()

    def invalidate(self):
        self._reset()

    def _reset(self):
        self.found = None
        self.tv.delete(*self.tv.get_children())
        self.result.configure(text="")
        self.bar.set_primary("Find duplicates", self.find)
        self._show_also(0)

    def _show_also(self, n):
        """The 'and the likely ones too' button, only once there are any."""
        if n:
            self.also.configure(text=f"Include {ui.plural(n, 'likely copy', 'likely copies')} too…")
            if not self.also.winfo_ismapped():
                self.also.pack(side="left")
        else:
            self.also.pack_forget()

    def find(self):
        if self.need_scan():
            return

        def work(progress, log):
            return dupes.find(self.app.recs, progress=progress)

        def done(res):
            self.found = res
            self.tv.delete(*self.tv.get_children())
            root = self.app.root_dir.get()
            for kind in ("exact", "likely"):
                for gi, g in enumerate(res[kind], 1):
                    k = dupes.keeper(g)
                    for r in g:
                        self.tv.insert("", "end", values=(
                            f"{kind.capitalize()} {gi}", "keep" if r is k else "extra",
                            r.ext.lstrip("."), f"{(r.duration or 0)/60:.1f} min",
                            os.path.relpath(r.path, root)))
            ex, lk = res["exact"], res["likely"]
            self.result.configure(
                text=f"{ui.plural(len(ex), 'exact group')} ({human_size(dupes.reclaimable(ex))} "
                     f"to free) · {ui.plural(len(lk), 'likely group')} "
                     f"({human_size(dupes.reclaimable(lk))})")
            n = sum(len(g) - 1 for g in ex)
            nl = sum(len(g) - 1 for g in lk)
            if n:
                # Exact copies are the safe default; the likely ones ride along
                # on their own button rather than hiding in a menu.
                self.bar.set_primary(f"Set aside {ui.plural(n, 'exact copy', 'exact copies')}…",
                                     lambda: self.quarantine("exact"))
                self._show_also(nl)
            elif nl:
                # Nothing exact to offer, so the likely ones are the job. This
                # used to fall through to "Search again" with the real action
                # left in the menu.
                self.bar.set_primary(
                    f"Set aside {ui.plural(nl, 'likely copy', 'likely copies')}…",
                    lambda: self.quarantine("all"))
                self._show_also(0)
            else:
                self.bar.set_primary("Search again", self.find)
                self._show_also(0)

        self.app.task.run(work, done, "Comparing audio")

    def quarantine(self, which):
        if not self.found:
            return
        groups = self.found["exact"] + (self.found["likely"] if which == "all" else [])
        count = sum(len(g) - 1 for g in groups)
        if not count:
            return
        if not messagebox.askyesno(APP, f"Move {ui.plural(count, 'extra copy', 'extra copies')} "
                                        "to _Quarantine?\n\nNothing is deleted, and you "
                                        "can undo this from History."):
            return
        root = self.app.root_dir.get()

        def work(progress, log):
            return dupes.quarantine(root, groups, log=log)

        def done(res):
            _, n = res
            messagebox.showinfo(APP, f"Moved {ui.plural(n, 'file')} to _Quarantine.")
            self.app.changed()

        self.app.task.run(work, done, "Setting copies aside")


# ------------------------------------------------------------ reorganise
BROAD = "Broad: fewer, wider folders"
FINE = "Fine: keep subgenres apart"


class ReorganiseScreen(ToolScreen):
    title = "Reorganise the collection"
    summary = ("Move every track into one tidy genre-folder layout. The folders you "
               "curated become playlists.")
    details = ("Each track ends up as one file in a genre folder, straight inside your "
               "collection: <Genre>/Artist - Title. The folders you curated - Spotify "
               "and TIDAL imports, gig sets - are saved to _Playlists as playlists "
               "pointing at that one file, so a track in five playlists is still one "
               "file on disk. Genre folders are not turned into playlists: they are "
               "where a track lives, not a set you put together. Extra copies move to "
               "_Quarantine and are never deleted. 'To Be Processed' and 'Processed' "
               "are never touched.\n\n"
               "If Sortero filed this collection under a Tracks folder before, this is "
               "what moves it up and clears that folder away.\n\n"
               "Preview first. The whole move can be undone from History.")

    def build(self):
        opts = ttk.Frame(self)
        opts.pack(fill="x")
        self.consolidate = tk.BooleanVar(value=True)
        self.make_pl = tk.BooleanVar(value=True)
        self.route_unan = tk.BooleanVar(value=True)
        self.keep_sets = tk.BooleanVar(value=False)
        self.by_energy = tk.BooleanVar(value=bool(settings.get("split_by_energy")))
        for i, (v, text) in enumerate([
                (self.consolidate, "Keep one copy of each track (playlists follow it)"),
                (self.make_pl, "Save my current folders as playlists"),
                (self.route_unan, "Send tracks with no key to 'To Be Processed'"),
                (self.keep_sets, "Also keep gig sets as folders"),
                (self.by_energy, "Split genre folders by energy level")]):
            v.trace_add("write", lambda *a: self._reset())
            ttk.Checkbutton(opts, text=text, variable=v).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 28), pady=2)
        dr = ttk.Frame(opts)
        dr.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(dr, text="Genre folders").pack(side="left")
        self.detail_box = ttk.Combobox(dr, state="readonly", width=28, values=[BROAD, FINE])
        self.detail_box.set(FINE if settings.get("genre_detail") == "fine" else BROAD)
        self.detail_box.pack(side="left", padx=(ui.GAP, 0))
        self.detail_box.bind("<<ComboboxSelected>>", lambda e: self._reset())

        ttk.Label(self, text="Folders to include", style="Heading.TLabel").pack(
            anchor="w", pady=(ui.SECTION, 0))
        ttk.Label(self, text="Untick anything that should stay exactly where it is.",
                  style="Muted.TLabel").pack(anchor="w")
        self.folder_wrap = ttk.Frame(self)
        self.folder_wrap.pack(fill="x", pady=(4, 0))
        self.folder_vars = {}

        self.result = ui.WrapLabel(self, style="Muted.TLabel")
        self.result.pack(fill="x", pady=(ui.SECTION, 4))
        f, self.tv = ui.tree(self, [("from", "Now", 420), ("to", "Moves to", 420)], height=8)
        f.pack(fill="both", expand=True)
        self.bar.set_more([
            ("Leave selected tracks where they are", self.exclude_rows),
            ("Include everything again", self.clear_exclusions),
            None,
            ("Flatten release folders…", self.app.open_flatten),
        ])
        self.plan = None
        self.excluded = set()          # individual paths the user opted out of
        self._reset()

    def invalidate(self):
        self.excluded = set()
        self.tv.delete(*self.tv.get_children())
        self.result.configure(text="")
        self._build_folder_list()
        self._reset()

    def _reset(self):
        self.plan = None
        self.bar.set_primary("Preview the plan", self.preview)

    def _build_folder_list(self):
        for w in self.folder_wrap.winfo_children():
            w.destroy()
        if not self.app.recs:
            return
        counts = collections.Counter(r.top for r in self.app.recs if not r.protected)
        keep = dict(self.folder_vars)
        remembered = set(settings.get("excluded_folders") or [])
        self.folder_vars = {}
        for i, (name, n) in enumerate(sorted(counts.items(), key=lambda x: -x[1])):
            v = tk.BooleanVar(value=keep[name].get() if name in keep
                              else name not in remembered)
            v.trace_add("write", lambda *a: (self._remember_folders(), self._reset()))
            self.folder_vars[name] = v
            ttk.Checkbutton(self.folder_wrap, text=f"{name} ({n})", variable=v
                            ).grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 16))

    def _remember_folders(self):
        settings.set("excluded_folders",
                     sorted(n for n, v in self.folder_vars.items() if not v.get()))

    def _excluded_paths(self):
        """Paths to leave alone: whole unticked folders, plus individual rows."""
        off = {name for name, v in self.folder_vars.items() if not v.get()}
        out = set(self.excluded)
        if off:
            out |= {r.path for r in self.app.recs if r.top in off}
        return out

    def exclude_rows(self):
        if not self.plan:
            messagebox.showinfo(APP, "Preview the plan first, then select the tracks to "
                                     "leave where they are.")
            return
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo(APP, "Select one or more rows in the list first.")
            return
        moves = self.plan[0]
        for iid in sel:
            idx = self.tv.index(iid)
            if idx < len(moves):
                self.excluded.add(moves[idx][0].path)
        self.preview()

    def clear_exclusions(self):
        self.excluded = set()
        for v in self.folder_vars.values():
            v.set(True)
        self.preview()

    def preview(self):
        if self.need_scan():
            return
        root = self.app.root_dir.get()
        consolidate = self.consolidate.get()
        keep_sets = self.keep_sets.get()
        route = self.route_unan.get()
        exclude = self._excluded_paths()
        detail = "fine" if self.detail_box.get() == FINE else "broad"
        settings.set("genre_detail", detail)
        by_energy = self.by_energy.get()
        settings.set("split_by_energy", by_energy)
        recs = self.app.recs

        def work(progress, log):
            canonical = {}
            if consolidate:
                log("Finding duplicate copies…")
                found = dupes.find(recs, progress=progress)
                canonical = dupes.canonical_map(found)
                # never set aside a copy the user asked to leave alone
                canonical = {k: v for k, v in canonical.items()
                             if k not in exclude and v not in exclude}
                log(f"{len(canonical)} extra copies will collapse into "
                    f"{len(set(canonical.values()))} canonical files")
            return organize.plan(root, recs, keep_sets=keep_sets,
                                 route_unanalyzed=route, canonical=canonical,
                                 exclude=exclude, detail=detail, by_energy=by_energy)

        def done(res):
            moves, pls, st = res
            self.tv.delete(*self.tv.get_children())
            for r, d in moves[:2000]:
                self.tv.insert("", "end", values=(r.rel, os.path.relpath(d, root)))
            cats = collections.Counter(
                os.path.relpath(d, root).split(os.sep)[0] for _, d in moves)
            bits = [ui.plural(len(moves), "move"), ui.plural(len(pls), "playlist")]
            if st.get("deduped"):
                bits.append(ui.plural(st["deduped"], "extra copy", "extra copies")
                            + " set aside")
            if st.get("excluded"):
                bits.append(f"{st['excluded']} left where they are")
            if st.get("skipped_protected"):
                bits.append(f"{st['skipped_protected']} in Processed or To Be Processed, "
                            "untouched")
            where = ", ".join(f"{v} into {k}" for k, v in cats.most_common())
            self.result.configure(text=" · ".join(bits) + (f"  ({where})" if where else ""))
            if moves:
                self.plan = res
                self.bar.set_primary(f"Move {ui.plural(len(moves), 'file')}…", self.apply)
            else:
                self.bar.set_primary("Preview again", self.preview)

        self.app.task.run(work, done, "Planning")

    def apply(self):
        if not self.plan:
            return
        moves, pls, _ = self.plan
        keep_pl = self.make_pl.get()
        if not messagebox.askyesno(
                APP, f"Move {ui.plural(len(moves), 'file')} into the new layout?\n\n"
                     + (f"{ui.plural(len(pls), 'playlist')} will be saved to _Playlists "
                        "first.\n" if keep_pl and pls else "")
                     + "You can undo the whole move from History."):
            return
        root = self.app.root_dir.get()

        def work(progress, log):
            return organize.apply(root, moves, pls if keep_pl else {}, log=log,
                                  progress=progress)

        def done(path):
            messagebox.showinfo(APP, "Reorganised. You can undo it from History.")
            self.app.changed()

        self.app.task.run(work, done, "Moving files")

"""Tidy up: tools for fixing a collection that already exists."""
import collections, os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from .. import ui, organize, dupes, fixtags, settings, bpmfix, mixxx, tempo
from ..transport import Transport
from ..common import human_size
from .base import Screen, APP, RESCAN


class TidyUpScreen(Screen):
    title = "Tidy up"
    summary = ("Tools for fixing the collection you already have. Each one shows you "
               "what it will do before anything changes.")

    def build(self):
        self.bar.pack_forget()
        app = self.app
        for title, text, button, command in (
                ("Clean tags",
                 "Clear download-site spam from tags, fill in missing artists, and make "
                 "energy ratings visible to DJ apps.", "Open", lambda: app.show("tags")),
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
class CleanTagsScreen(ToolScreen):
    title = "Clean tags"
    summary = ("Fix common tag problems across your collection. Every change is listed "
               "before anything is written.")
    details = ("Download sites put their names into genre and comment tags, which then "
               "clutter your DJ software. Sortero can clear those, take a missing artist "
               "from the filename, tidy genre names, and copy Mixed In Key's energy rating "
               "into a field DJ apps display. Tick what you want, preview, then write. "
               "You can undo it from History.")

    def build(self):
        box = ttk.Frame(self)
        box.pack(fill="x", pady=(0, ui.SECTION))
        self.vars = {}
        for k in fixtags.FIXES:
            v = tk.BooleanVar(value=True)
            v.trace_add("write", lambda *a: self._reset(clear=True))
            self.vars[k] = v
            ttk.Checkbutton(box, text=fixtags.FIX_LABELS[k], variable=v).pack(anchor="w",
                                                                           pady=2)
        self.result = ttk.Label(self, style="Muted.TLabel")
        self.result.pack(anchor="w", pady=(0, 4))
        f, self.tv = ui.tree(self, [("track", "Track", 300), ("field", "Tag", 80),
                                    ("before", "Now", 240), ("after", "Becomes", 240)],
                             height=13)
        f.pack(fill="both", expand=True)
        self.changes = None
        self._reset()

    def invalidate(self):
        self._reset(clear=True)

    def _reset(self, clear=False):
        self.changes = None
        if clear:
            self.tv.delete(*self.tv.get_children())
            self.result.configure(text="")
        self.bar.set_primary("Preview changes", self.preview)

    def preview(self):
        if self.need_scan():
            return
        fixes = {k for k, v in self.vars.items() if v.get()}
        if not fixes:
            messagebox.showinfo(APP, "Tick at least one fix.")
            return

        def work(progress, log):
            return fixtags.plan(self.app.recs, fixes)

        def done(changes):
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
                self.result.configure(text="Nothing to fix. Your tags are already clean.")
                self.bar.set_primary("Preview again", self.preview)
                return
            c = fixtags.summarize(changes)
            self.result.configure(text=" · ".join(f"{v} {k}" for k, v in c.most_common()))
            self.changes = changes
            self.bar.set_primary(f"Write to {ui.plural(len(changes), 'file')}…", self.apply)

        self.app.task.run(work, done, "Checking tags")

    def apply(self):
        if not self.changes:
            return
        changes = self.changes
        if not messagebox.askyesno(APP, f"Write tags on {ui.plural(len(changes), 'file')}?"
                                        "\n\nYou can undo this from History."):
            return
        root = self.app.root_dir.get()

        def work(progress, log):
            return fixtags.apply(root, changes, log=log, progress=progress)

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
        self.bar.set_more([("Set aside exact and likely copies…",
                            lambda: self.quarantine("all")),
                           None,
                           ("Search again", self.find)])
        self.found = None
        self._reset()

    def invalidate(self):
        self._reset()

    def _reset(self):
        self.found = None
        self.tv.delete(*self.tv.get_children())
        self.result.configure(text="")
        self.bar.set_primary("Find duplicates", self.find)
        self.bar.enable("Set aside exact and likely copies…", False)

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
            if n:
                self.bar.set_primary(f"Set aside {ui.plural(n, 'exact copy', 'exact copies')}…",
                                     lambda: self.quarantine("exact"))
            else:
                self.bar.set_primary("Search again", self.find)
            self.bar.enable("Set aside exact and likely copies…", bool(ex or lk))

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
    summary = ("Move every track into one tidy genre-folder layout. Your current folders "
               "become playlists.")
    details = ("Each track ends up as one file in a genre folder. Every folder you have "
               "now, such as Spotify and TIDAL imports or gig sets, is saved to _Playlists "
               "as a playlist pointing at that one file, so a track in five playlists is "
               "still one file on disk. Extra copies move to _Quarantine and are never "
               "deleted. 'To Be Processed' and 'Processed' are never touched.\n\n"
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

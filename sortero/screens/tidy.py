"""Tidy up: tools for fixing a collection that already exists."""
import collections, os
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, organize, dupes, fixtags, settings
from ..common import (human_size, name_order_note, ARTIST_TITLE, NAME_ORDERS,
                      NAME_ORDER_LABELS)
from .base import Screen, APP


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
               "into a field DJ apps display. Tick what you want, preview, then write.\n\n"
               "Sortero reads a filename like 'Deadmau5 - Strobe' as artist first. If "
               "yours are the other way round, say so under 'Filenames are' and the "
               "artist goes in the artist tag, not the title. For tags that are already "
               "in backwards, More offers a straight swap - on everything here, or on "
               "just the tracks you pick in Library.\n\n"
               "You can undo any of it from History.")

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

        self.result = ttk.Label(self, style="Muted.TLabel")
        self.result.pack(anchor="w", pady=(0, 4))
        f, self.tv = ui.tree(self, [("track", "Track", 300), ("field", "Tag", 80),
                                    ("before", "Now", 240), ("after", "Becomes", 240)],
                             height=13)
        f.pack(fill="both", expand=True)
        self.bar.set_more([
            ("Swap artist and title on every track…", self.preview_swap),
            ("Read artist and title from the filenames again…", self.preview_from_names),
            None,
            ("Fix only some tracks…", lambda: self.app.show_library("swapped")),
        ])
        self.changes = None
        self._reset()

    def invalidate(self):
        self._show_detected()
        self._reset(clear=True)

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
        order = self.order()

        def work(progress, log):
            return fixtags.plan(self.app.recs, fixes, order=order)

        self.app.task.run(work, lambda changes: self._show(
            changes, "Nothing to fix. Your tags are already clean."), "Checking tags")

    def preview_swap(self):
        if self.need_scan():
            return
        if not messagebox.askyesno(
                APP, "Swap the artist and title tag of every track?\n\n"
                     "This is for a collection that went in backwards all the way "
                     "through. If only some tracks are wrong, use Library instead: "
                     "show 'Artist and title look swapped', pick the ones you mean, and "
                     "swap those.\n\nNothing is written until you've seen the list."):
            return

        def work(progress, log):
            return fixtags.swap([r for r in self.app.recs if not r.protected])

        self.app.task.run(work, lambda changes: self._show(
            changes, "Nothing to swap: no track has an artist or title to move.",
            kind="swap-names"), "Reading tags")

    def preview_from_names(self):
        if self.need_scan():
            return
        order = self.order()
        if not messagebox.askyesno(
                APP, "Read artist and title from the filenames again?\n\n"
                     f"Filenames are read as {NAME_ORDER_LABELS[order]}, which you can "
                     "change above. Tracks whose name has no ' - ' in it are left alone."
                     "\n\nThis overwrites the artist and title tags that are there "
                     "now, so check the list before writing."):
            return

        def work(progress, log):
            return fixtags.from_filename([r for r in self.app.recs if not r.protected],
                                         order=order)

        self.app.task.run(work, lambda changes: self._show(
            changes, "Nothing to change: the tags already match the filenames.",
            kind="names-from-filename"), "Reading filenames")

    def _show(self, changes, empty, kind="fixtags"):
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
            self.result.configure(text=empty)
            self.bar.set_primary("Preview again", self.preview)
            return
        c = fixtags.summarize(changes)
        self.result.configure(text=" · ".join(f"{v} {k}" for k, v in c.most_common()))
        self.changes = changes
        self.bar.set_primary(f"Write to {ui.plural(len(changes), 'file')}…",
                             lambda: self.apply(kind))

    def apply(self, kind="fixtags"):
        if not self.changes:
            return
        if not messagebox.askyesno(APP, f"Write tags on {ui.plural(len(self.changes), 'file')}?"
                                        "\n\nYou can undo this from History."):
            return
        root = self.app.root_dir.get()
        changes = self.changes

        def work(progress, log):
            return fixtags.apply(root, changes, log=log, progress=progress, kind=kind)

        def done(res):
            _, n, failed = res
            messagebox.showinfo(APP, f"Updated {ui.plural(n, 'file')}."
                                     + (f"\n{failed} couldn't be written." if failed else ""))
            self.app.changed()

        self.app.task.run(work, done, "Writing tags")


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

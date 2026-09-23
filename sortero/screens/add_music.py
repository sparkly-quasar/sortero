"""Add music: bring new tracks into the library."""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .. import ui, importer, playlists, review
from .base import Screen, APP

ACTIONS = {"sort": "File", "mix": "File as a mix", "to-process": "Needs analysing",
           "needs-folder": "Hold for you", "duplicate": "Already have"}
COUNTS = {"sort": "to file", "mix": "set recordings", "to-process": "need analysing",
          "needs-folder": "held back for you to place", "duplicate": "already in your library"}


class AddMusicScreen(Screen):
    title = "Add music"
    summary = "Bring new tracks into your library. Nothing moves until you press Add."
    details = ("Tracks that already have a key go straight into the matching genre "
               "folder. Tracks without one wait in 'To Be Processed' for your analysis "
               "tool. Tracks you already have are pointed out, not added twice.\n\n"
               "If a track has no genre to go on, Sortero can hold it back so you can "
               "listen and choose its folder yourself, instead of filing it into "
               "Unsorted. You can undo an import from History.")

    def build(self):
        src = ttk.Frame(self)
        src.pack(fill="x")
        ttk.Button(src, text="Choose a folder…", command=self.add_folder).pack(side="left")
        ttk.Button(src, text="Choose files…", command=self.add_files).pack(side="left",
                                                                         padx=(ui.GAP, 0))
        ui.link(src, "Already analysed? Sort the Processed folder…",
                lambda: self.app.open_processed()).pack(side="right")
        self.sources_lab = ttk.Label(self, style="Muted.TLabel")
        self.sources_lab.pack(anchor="w", pady=(6, 0))

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(ui.SECTION, 0))
        self.move_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Move the files (untick to copy them)",
                        variable=self.move_var).grid(row=0, column=0, sticky="w", pady=2)
        self.hold_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Hold back tracks with no genre so I can place them",
                        variable=self.hold_var, command=self._options_changed
                        ).grid(row=0, column=1, sticky="w", padx=(28, 0), pady=2)
        pl = ttk.Frame(opts)
        pl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(pl, text="Also add them to a playlist").pack(side="left")
        self.playlist_var = tk.StringVar()
        self.playlist_box = ttk.Combobox(pl, textvariable=self.playlist_var, width=34)
        self.playlist_box.pack(side="left", padx=(ui.GAP, 0))
        ttk.Label(pl, text="optional: pick one or type a new name",
                  style="Muted.TLabel").pack(side="left", padx=(ui.GAP, 0))

        self.result = ui.WrapLabel(self, style="Muted.TLabel")
        self.result.pack(fill="x", pady=(ui.SECTION, 4))
        f, self.tv = ui.tree(self, [("action", "What happens", 120), ("track", "Track", 280),
                                    ("dest", "Goes to", 280), ("why", "Why", 220)], height=11)
        f.pack(fill="both", expand=True)
        # Held-back tracks are the whole point of holding them back: when the
        # preview says some are waiting, say so where the buttons are, not in
        # a menu the user has no reason to open.
        self.place_btn = ttk.Button(self.bar.left, command=self.open_review)
        self.bar.set_more([("Clear the list", self.clear)])
        self.sources, self.results = [], None
        self._sync()

    def invalidate(self):
        root = self.app.root_dir.get()
        if root and os.path.isdir(root):
            self.playlist_box.configure(values=playlists.existing(root))

    def _sync(self):
        if self.sources:
            names = [os.path.basename(os.path.normpath(s)) for s in self.sources]
            shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
            self.sources_lab.configure(text=f"Chosen: {shown}")
        else:
            self.sources_lab.configure(text="Choose a folder of new music, or some files, "
                                            "to see where each track would go.")
        res = self.results or []
        n = sum(1 for x in res if x["dest"])
        if n:
            self.bar.set_primary(f"Add {ui.plural(n, 'track')}…", self.apply)
        else:
            self.bar.set_primary("Add tracks", self.apply, state="disabled")
        held = sum(1 for x in res if x["action"] == "needs-folder")
        if held:
            self.place_btn.configure(text=f"Place {ui.plural(held, 'held-back track')}…")
            if not self.place_btn.winfo_ismapped():
                self.place_btn.pack(side="left", padx=(0, ui.GAP))
        else:
            self.place_btn.pack_forget()
        self.bar.enable("Clear the list", bool(self.sources))

    def clear(self):
        self.sources, self.results = [], None
        self.tv.delete(*self.tv.get_children())
        self.result.configure(text="")
        self._sync()

    def add_files(self):
        fs = filedialog.askopenfilenames(title="Choose tracks to add")
        if fs:
            self.sources.extend(fs)
            self.preview()

    def add_folder(self):
        d = filedialog.askdirectory(title="Choose a folder of new music")
        if d:
            self.sources.append(d)
            self.preview()

    def _options_changed(self):
        if self.sources:
            self.preview()

    def preview(self):
        self._sync()
        if self.need_scan():
            return
        root = self.app.root_dir.get()
        srcs = list(self.sources)
        known = self.app.recs
        hold = self.hold_var.get()

        def work(progress, log):
            return importer.plan(root, srcs, known, progress=progress, hold_unsorted=hold)

        def done(res):
            self.results = res
            self.tv.delete(*self.tv.get_children())
            for x in res[:2000]:
                self.tv.insert("", "end", values=(
                    ACTIONS.get(x["action"], x["action"]), x["rec"].display[:90],
                    os.path.relpath(x["dest"], root) if x["dest"] else "—",
                    x["reason"]))
            c = importer.summarize(res)
            self.result.configure(
                text=f"{ui.plural(len(res), 'track')}: " +
                     ", ".join(f"{v} {COUNTS.get(k, k)}" for k, v in c.most_common())
                if res else "No audio files found there.")
            self._sync()

        self.app.task.run(work, done, "Reading new music")

    def apply(self):
        if not self.results:
            return
        todo = [x for x in self.results if x["dest"]]
        move = self.move_var.get()
        if not messagebox.askyesno(
                APP, f"{'Move' if move else 'Copy'} {ui.plural(len(todo), 'track')} into "
                     "your library?\n\nYou can undo this from History."):
            return
        root = self.app.root_dir.get()
        results = self.results
        plname = self.playlist_var.get().strip()

        def work(progress, log):
            path, n = importer.apply(root, results, move=move, log=log, progress=progress)
            added = 0
            if plname:
                # the importer may rename on collision, so use what landed on disk
                dests = [x["dest"] for x in results if x["dest"]]
                dests = [d for d in dests if os.path.exists(d)]
                _, added = playlists.append(root, plname, dests)
                log(f"added {added} tracks to playlist '{plname}'")
            return path, n, added

        def done(res):
            _, n, added = res
            msg = f"Added {ui.plural(n, 'track')}."
            if plname:
                msg += f"\n{added} went into the playlist '{plname}'."
            held = [x["rec"] for x in results if x["action"] == "needs-folder"]
            if held:
                msg += (f"\n\n{ui.plural(len(held), 'track')} had no genre to go on, so "
                        "they were left where they are for you to place.")
            messagebox.showinfo(APP, msg)
            self.clear()
            self.app.screens["history"].refresh()

            def after_scan():
                if held and messagebox.askyesno(
                        APP, f"Go through the {ui.plural(len(held), 'held-back track')} "
                             "now and choose a folder for each?"):
                    self._review(held)
                else:
                    self.app.offer_playlist_repair()

            self.app.scan(then=after_scan)

        self.app.task.run(work, done, "Adding music")

    def open_review(self):
        held = [x["rec"] for x in (self.results or []) if x["action"] == "needs-folder"]
        if not held:
            messagebox.showinfo(APP, "Nothing is being held back.")
            return
        self._review(held)

    def _review(self, recs):
        review.ReviewDialog(self.app, self.app, recs,
                            on_close=lambda changed: self.app.changed(repair=True))

"""Playlists: rebuild a streaming playlist from the files you already own."""
import collections, os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .. import ui, auth, playlists, organize
from .base import Screen, APP


class PlaylistsScreen(Screen):
    title = "Playlists"
    summary = "Rebuild a Spotify or TIDAL playlist from the files you already have."
    details = ("Paste a playlist link, or a tracklist with one track per line. Sortero "
               "matches each track to a file in your collection and saves the result in "
               "_Playlists as an .m3u8 playlist, which rekordbox, Serato and Mixxx can "
               "import.\n\nWithout a connected account, Spotify links only show the first "
               "50 tracks and TIDAL links don't work. Connect accounts in Settings. A "
               "pasted tracklist always works.")

    def build(self):
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Playlist link").pack(side="left")
        self.url = tk.StringVar()
        self.url.trace_add("write", lambda *a: self._reset())
        e = ttk.Entry(row, textvariable=self.url)
        e.pack(side="left", fill="x", expand=True, padx=(ui.GAP, 0))
        e.bind("<Return>", lambda ev: self.match())

        self.opts = ttk.Frame(self)
        self.opts.pack(fill="x", pady=(6, 0))
        self.paste_link = ui.link(self.opts, "Paste a tracklist instead", self.toggle_paste)
        self.paste_link.pack(side="left")
        ui.link(self.opts, "Accounts…", lambda: self.app.show("settings")).pack(side="right")
        self.acc_lab = ttk.Label(self.opts, style="Muted.TLabel")
        self.acc_lab.pack(side="right", padx=(0, ui.GAP))

        self.paste_frame = ttk.Frame(self)
        self.paste = tk.Text(self.paste_frame, height=6, font=ui.MONO, wrap="none")
        self.paste.pack(fill="x")
        self.paste.bind("<KeyRelease>", lambda e: self._reset())
        self.paste_open = False

        self.result = ui.WrapLabel(self, style="Muted.TLabel")
        self.result.pack(fill="x", pady=(ui.SECTION, 4))
        f, self.tv = ui.tree(self, [("how", "Match", 100), ("track", "In the playlist", 360),
                                    ("file", "Your file", 380)], height=12)
        f.pack(fill="both", expand=True)

        ttk.Label(self.bar.left, text="Save as").pack(side="left")
        self.name = tk.StringVar()
        ttk.Entry(self.bar.left, textvariable=self.name, width=30).pack(side="left",
                                                                       padx=(ui.GAP, 0))
        self.bar.set_more([
            ("Load a CSV file…", self.load_csv),
            None,
            ("Fix broken playlist links…", self.app.repair_playlists),
            ("Rebuild playlists from folders…", self.rebuild),
        ])
        self.results = None
        self._reset()

    def shown(self):
        self._refresh_accounts()

    def invalidate(self):
        self._reset()

    def _refresh_accounts(self):
        bits = []
        for pid in ("spotify", "tidal"):
            on = auth.is_connected(pid)
            bits.append(f"{auth.PROVIDERS[pid]['label']} {'connected' if on else 'not connected'}")
        self.acc_lab.configure(text=" · ".join(bits))

    def toggle_paste(self):
        self.paste_open = not self.paste_open
        if self.paste_open:
            self.paste_frame.pack(fill="x", pady=(ui.GAP, 0), after=self.opts)
            self.paste_link.configure(text="Hide the tracklist box")
            self.paste.focus_set()
        else:
            self.paste_frame.pack_forget()
            self.paste_link.configure(text="Paste a tracklist instead")
        self._reset()

    def _reset(self):
        self.results = None
        self.bar.set_primary("Find matches", self.match)

    def match(self):
        text = self.paste.get("1.0", "end").strip() if self.paste_open else ""
        if text:
            self._load(lambda log: playlists.from_text(text), "Matching tracks")
        elif self.url.get().strip():
            u = self.url.get().strip()
            self._load(lambda log: playlists.from_url(u, log=log), "Fetching playlist")
        else:
            messagebox.showinfo(APP, "Paste a playlist link, or a tracklist, first.")

    def load_csv(self):
        p = filedialog.askopenfilename(title="Choose a playlist CSV",
                                       filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if p:
            self._load(lambda log: playlists.from_csv(p), "Reading CSV")

    def _load(self, getter, label):
        if self.need_scan():
            return

        def work(progress, log):
            try:
                name, entries = getter(log)
            except (playlists.SourceError, auth.AuthError) as e:
                return ("error", str(e))
            return ("ok", name, playlists.match(entries, self.app.recs))

        def done(res):
            if res[0] == "error":
                messagebox.showwarning(APP, res[1])
                return
            _, name, results = res
            if name and not self.name.get().strip():
                self.name.set(name)
            root = self.app.root_dir.get()
            self.tv.delete(*self.tv.get_children())
            for x in results:
                got = os.path.relpath(x["rec"].path, root) if x["rec"] else "not in your collection"
                self.tv.insert("", "end", values=(
                    x["how"], f"{x['artist']} - {x['title']}"[:110], got))
            c = collections.Counter(x["how"] for x in results)
            found = sum(1 for x in results if x["rec"])
            notes = []
            if len(results) == playlists.EMBED_CAP:
                notes.append("Spotify only shows 50 tracks without an account. Connect "
                             "one in Settings, or paste the full tracklist")
            staged = sum(1 for r in self.app.recs if r.protected)
            if staged and found < len(results):
                notes.append(f"{staged} files still in Processed or To Be Processed "
                             "can't be matched until they're sorted")
            self.result.configure(
                text=f"Found {found} of {len(results)} tracks ("
                     + ", ".join(f"{v} {k}" for k, v in c.most_common()) + ")."
                     + ("  " + ". ".join(notes) + "." if notes else ""))
            self.results = results
            self.bar.set_primary("Save playlist…", self.create,
                                 state="normal" if found else "disabled")

        self.app.task.run(work, done, label)

    def create(self):
        if not self.results:
            return
        name = self.name.get().strip()
        if not name:
            messagebox.showinfo(APP, "Give the playlist a name first (Save as).")
            return
        found = [x["rec"].path for x in self.results if x["rec"]]
        if not found:
            messagebox.showinfo(APP, "None of these tracks are in your collection, so "
                                     "there's nothing to save.")
            return
        missing = len(self.results) - len(found)
        if not messagebox.askyesno(
                APP, f"Save '{name}' with {ui.plural(len(found), 'track')}?"
                     + (f"\n\n{missing} aren't in your collection and will be left out."
                        if missing else "")):
            return
        fp = playlists.write(self.app.root_dir.get(), name, found)
        messagebox.showinfo(APP, f"Saved {os.path.basename(fp)} in _Playlists.")
        self.app.log(f"playlist: {fp} ({len(found)} tracks, {missing} missing)")

    def rebuild(self):
        """Regenerate the folder-derived playlists against the library as it stands."""
        if self.need_scan():
            return
        if not messagebox.askyesno(
                APP, "Rebuild a playlist for each folder in your collection?\n\n"
                     "Playlists with the same names in _Playlists are overwritten."):
            return
        root = self.app.root_dir.get()
        recs = self.app.recs

        def work(progress, log):
            pls = organize.playlists_from_current(root, recs)
            return organize.write_playlists(root, pls)

        def done(written):
            messagebox.showinfo(APP, f"Saved {ui.plural(len(written), 'playlist')} in "
                                     "_Playlists.")

        self.app.task.run(work, done, "Rebuilding playlists")

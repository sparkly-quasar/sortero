"""Sortero - a desktop front end for organising a DJ collection."""
import os, sys, queue, threading, traceback, collections, subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import (library, organize, dupes, fixtags, importer, journal, playlists,
               auth, paths, settings, updates, wizard, session, updater, genres)
from .common import human_size

APP = "Sortero"
PREF = paths.prefs_file()

from .version import __version__


# ---------------------------------------------------------------- worker glue
class Task:
    """Runs work off the UI thread and marshals progress back via a queue."""

    def __init__(self, app):
        self.app = app
        self.q = queue.Queue()
        self.running = False

    def run(self, fn, done, label="Working"):
        if self.running:
            messagebox.showinfo(APP, "Another operation is still running.")
            return
        self.running = True
        self.app.set_status(f"{label}…")
        self.app.progress.configure(value=0, maximum=100)

        def progress(i, total):
            self.q.put(("progress", (i, total)))

        def log(msg):
            self.q.put(("log", msg))

        def worker():
            try:
                result = fn(progress, log)
                self.q.put(("done", result))
            except Exception:
                self.q.put(("error", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()
        self._poll(done, label)

    def _poll(self, done, label):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "progress":
                    i, total = payload
                    self.app.progress.configure(value=i, maximum=max(total, 1))
                    self.app.set_status(f"{label}… {i}/{total}")
                elif kind == "log":
                    self.app.log(payload)
                elif kind == "done":
                    self.running = False
                    self.app.progress.configure(value=0)
                    self.app.set_status("Ready")
                    done(payload)
                    return
                elif kind == "error":
                    self.running = False
                    self.app.progress.configure(value=0)
                    self.app.set_status("Failed")
                    self.app.log(payload)
                    messagebox.showerror(APP, payload.strip().splitlines()[-1])
                    return
        except queue.Empty:
            pass
        self.app.after(80, lambda: self._poll(done, label))


# ---------------------------------------------------------------------- app
class Sortero(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP)
        self.geometry("1080x720")
        self.minsize(940, 620)

        self.root_dir = tk.StringVar(value=self._load_pref())
        self.recs = []
        self.health = None
        self.task = Task(self)

        self._build_menu()
        self._build_header()
        self._build_tabs()
        self._build_banner()
        self._build_notice()
        self._build_footer()
        self.after(250, self._first_run)

    # -- prefs ------------------------------------------------------------
    def _load_pref(self):
        return settings.get("root") or ""

    def _save_pref(self):
        settings.set("root", self.root_dir.get())

    # -- menus / first run -------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self)

        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="Choose Collection Folder…", command=self.choose)
        filem.add_command(label="Rescan", accelerator="Cmd-R" if paths.IS_MAC else "Ctrl+R",
                          command=self.scan)
        filem.add_separator()
        filem.add_command(label="Open App Data Folder",
                          command=lambda: paths.reveal(paths.data_dir()))
        menubar.add_cascade(label="File", menu=filem)

        testm = tk.Menu(menubar, tearoff=0)
        testm.add_command(label="Start Testing Session…", command=self.testing_start)
        testm.add_separator()
        testm.add_command(label="Keep All Changes (delete backup)…",
                          command=self.testing_commit)
        testm.add_command(label="Undo Everything in This Session…",
                          command=self.testing_revert)
        testm.add_separator()
        testm.add_command(label="Save Backup As…", command=self.testing_export)
        testm.add_command(label="Load Backup and Undo…", command=self.testing_load)
        menubar.add_cascade(label="Testing", menu=testm)
        self.testm = testm

        helpm = tk.Menu(menubar, tearoff=0, name="help")
        helpm.add_command(label="Setup Wizard…", command=self.run_wizard)
        helpm.add_separator()
        helpm.add_command(label="Check for Updates…",
                          command=lambda: self.check_updates(quiet=False))
        self.auto_update = tk.BooleanVar(value=bool(settings.get("check_updates_on_launch")))
        helpm.add_checkbutton(label="Check Automatically on Launch",
                              variable=self.auto_update,
                              command=lambda: settings.set("check_updates_on_launch",
                                                           self.auto_update.get()))
        helpm.add_separator()
        helpm.add_command(label=f"Sortero {__version__}", state="disabled")
        menubar.add_cascade(label="Help", menu=helpm)

        self.config(menu=menubar)
        self.bind_all("<Command-r>" if paths.IS_MAC else "<Control-r>",
                      lambda e: self.scan())

    # -- testing mode ------------------------------------------------------
    def _build_banner(self):
        self.banner = tk.Frame(self, bg="#8a5a00")
        self.banner_label = tk.Label(self.banner, bg="#8a5a00", fg="white",
                                     font=("Helvetica", 12, "bold"), pady=6)
        self.banner_label.pack(side="left", padx=12)
        tk.Button(self.banner, text="Keep changes", command=self.testing_commit,
                  highlightbackground="#8a5a00").pack(side="right", padx=6, pady=4)
        tk.Button(self.banner, text="Undo everything", command=self.testing_revert,
                  highlightbackground="#8a5a00").pack(side="right", pady=4)
        self.refresh_banner()

    def _build_notice(self):
        """Shown when 'Processed' has analysed tracks waiting to be filed."""
        self.notice = tk.Frame(self, bg="#1d5c3a")
        self.notice_label = tk.Label(self.notice, bg="#1d5c3a", fg="white",
                                     font=("Helvetica", 12, "bold"), pady=6)
        self.notice_label.pack(side="left", padx=12)
        tk.Button(self.notice, text="File them now",
                  command=self._file_processed_now,
                  highlightbackground="#1d5c3a").pack(side="right", padx=10, pady=4)
        self.refresh_notice()

    def refresh_notice(self):
        d = self.root_dir.get()
        n = 0
        if d and os.path.isdir(d):
            folder = os.path.join(d, importer.PROCESSED)
            if os.path.isdir(folder):
                n = len(importer.gather([folder]))
        if not n:
            self.notice.pack_forget()
            return
        self.notice_label.configure(
            text=f"{n} analysed track{'s' if n != 1 else ''} in 'Processed' "
                 f"waiting to be filed into your library.")
        self.notice.pack(fill="x", before=self.nb)

    def offer_playlist_repair(self):
        """After filing, a returning track's old playlist entries are stale."""
        root = self.root_dir.get()
        if not root or not self.recs:
            return

        def work(progress, log):
            return playlists.repair(root, self.recs, dry=True, log=lambda m: None)

        def done(res):
            fixed, gone, _ = res
            if not fixed:
                return
            if messagebox.askyesno(
                    APP, f"{fixed} playlist entries point at tracks that came back "
                         "under a new name or format.\n\nRe-link them so your sets "
                         "and vibe playlists are whole again?"):
                def work2(progress, log):
                    return playlists.repair(root, self.recs, dry=False, log=log)

                def done2(r2):
                    messagebox.showinfo(APP, f"Re-linked {r2[0]} entries.")
                self.task.run(work2, done2, "Repairing playlists")

        self.task.run(work, done, "Checking playlists")

    def _file_processed_now(self):
        self.nb.select(self.tab_import)
        self.tab_import.intake_processed()

    def refresh_banner(self):
        sess = session.active()
        if not sess:
            self.banner.pack_forget()
            return
        s = session.summary(sess)
        self.banner_label.configure(
            text=f"TESTING MODE — {s['operations']} operations recorded "
                 f"({s['moves']} moves, {s['tags']} tag edits). "
                 f"Nothing is permanent until you keep it.")
        self.banner.pack(fill="x", before=self.nb)

    def testing_start(self):
        d = self.require_root()
        if not d:
            return
        if session.active():
            messagebox.showinfo(APP, "A testing session is already running.")
            return
        if not messagebox.askyesno(
                APP, "Start a testing session?\n\n"
                     "Everything you do from now on is recorded into one restore "
                     "point, saved continuously as a .bak file. You can undo the "
                     "whole lot in one go, or keep it all when you're happy."):
            return
        sess = session.start(d)
        session.export(sess)
        self.refresh_banner()
        self.log(f"testing session started: {sess['id']}")
        messagebox.showinfo(APP, "Testing mode is on.\n\nBackup: "
                                 f"{session.default_bak_path(sess)}")

    def testing_commit(self):
        sess = session.active()
        if not sess:
            messagebox.showinfo(APP, "No testing session is running.")
            return
        s = session.summary(sess)
        if not messagebox.askyesno(
                APP, f"Keep all {s['operations']} operations "
                     f"({s['moves']} moves, {s['tags']} tag edits)?\n\n"
                     "The combined backup file is deleted. Individual operations "
                     "stay in History and can still be undone one at a time."):
            return
        session.commit(sess, log=self.log)
        self.refresh_banner()
        self.tab_history.refresh()
        messagebox.showinfo(APP, "Changes kept. Testing mode is off.")

    def testing_revert(self):
        sess = session.active()
        if not sess:
            messagebox.showinfo(APP, "No testing session is running.")
            return
        s = session.summary(sess)
        if not messagebox.askyesno(
                APP, f"Undo everything in this session?\n\n"
                     f"{s['moves']} moves and {s['tags']} tag edits will be reversed, "
                     "putting your collection back as it was when testing started."):
            return

        def work(progress, log):
            return session.revert_active(log=log)

        def done(res):
            ok, fail = res
            self.refresh_banner()
            self.tab_history.refresh()
            messagebox.showinfo(APP, f"Reverted {ok} operations."
                                     + (f"\n{fail} failed." if fail else ""))
            self.scan()

        self.task.run(work, done, "Undoing session")

    def testing_export(self):
        sess = session.active()
        if not sess:
            messagebox.showinfo(APP, "No testing session is running.")
            return
        p = filedialog.asksaveasfilename(
            title="Save Sortero backup", defaultextension=".bak",
            initialfile=f"sortero-{sess['id']}.bak",
            filetypes=[("Sortero backup", "*.bak")])
        if p:
            session.export(sess, p)
            self.log(f"backup saved: {p}")
            messagebox.showinfo(APP, f"Backup saved to\n{p}")

    def testing_load(self):
        p = filedialog.askopenfilename(title="Load a Sortero backup",
                                       filetypes=[("Sortero backup", "*.bak"),
                                                  ("All files", "*")])
        if not p:
            return
        try:
            data = session.load(p)
        except Exception as e:
            messagebox.showerror(APP, str(e))
            return
        d = session.describe(data)
        import time as _t
        when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(d["started"])) if d["started"] else "unknown"
        if not messagebox.askyesno(
                APP, f"Undo everything in this backup?\n\n"
                     f"Recorded: {when}\nCollection: {d['root']}\n"
                     f"{d['operations']} operations — {d['moves']} moves, "
                     f"{d['tags']} tag edits.\n\n"
                     "Files are moved back and tags restored to their old values."):
            return

        def work(progress, log):
            return session.revert_backup(data, log=log)

        def done(res):
            ok, fail = res
            self.refresh_banner()
            self.tab_history.refresh()
            messagebox.showinfo(APP, f"Reverted {ok} operations."
                                     + (f"\n{fail} failed." if fail else ""))
            self.scan()

        self.task.run(work, done, "Restoring from backup")

    def _first_run(self):
        def after_wizard(root_dir):
            if root_dir:
                self.root_dir.set(root_dir)
                self.scan()
            self._maybe_auto_update()

        w = wizard.maybe_run(self, on_finish=after_wizard)
        if w is None:
            if self.root_dir.get() and os.path.isdir(self.root_dir.get()):
                self.scan()
            self._maybe_auto_update()

    def run_wizard(self):
        wizard.Wizard(self, on_finish=lambda d: (self.root_dir.set(d), self.scan())
                      if d else None)

    def _maybe_auto_update(self):
        if settings.get("check_updates_on_launch") and updates.due():
            self.after(2500, lambda: self.check_updates(quiet=True))

    def _offer_install(self, res):
        """Found a newer release - download, swap it in, and relaunch."""
        import webbrowser
        if not updater.running_frozen():
            if messagebox.askyesno(APP, res["message"] + "\n\nThis copy is running "
                                        "from source, so it can't replace itself. "
                                        "Open the download page?"):
                webbrowser.open(res["url"])
            return
        if not messagebox.askyesno(
                APP, res["message"] + "\n\nDownload it, install it and restart "
                     "Sortero now?\n\nAnything unsaved is finished first — this "
                     "only quits once the new version is ready."):
            return
        try:
            asset = updater.pick_asset(res.get("assets") or [])
        except updater.UpdateError as e:
            messagebox.showerror(APP, str(e))
            return

        def work(progress, log):
            log(f"downloading {asset['name']}…")
            new = updater.prepare(asset, progress=progress)
            log(f"unpacked to {new}")
            return new

        def done(new_path):
            try:
                updater.install(new_path)
            except updater.UpdateError as e:
                messagebox.showerror(APP, str(e))
                return
            messagebox.showinfo(APP, "Update ready. Sortero will close and reopen "
                                     "on the new version in a moment.")
            self.after(300, self.destroy)

        self.task.run(work, done, "Downloading update")

    def check_updates(self, quiet=True):
        """quiet=True only speaks up when there is actually an update."""
        def work(progress, log):
            return updates.check()

        def done(res):
            state = res["state"]
            if state == "update":
                self._offer_install(res)
            elif not quiet:
                if state == "private":
                    if messagebox.askyesno(APP, res["message"] + "\n\nOpen Releases now?"):
                        import webbrowser
                        webbrowser.open(res["url"])
                else:
                    messagebox.showinfo(APP, res["message"])
            else:
                self.log(f"update check: {res['message']}")

        if quiet and self.task.running:
            return
        self.task.run(work, done, "Checking for updates")

    # -- chrome -----------------------------------------------------------
    def _build_header(self):
        bar = ttk.Frame(self, padding=(12, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text="Sortero", font=("Helvetica", 17, "bold")).pack(side="left")
        ttk.Label(bar, text="  DJ collection organiser", foreground="#777").pack(side="left")
        ttk.Button(bar, text="Scan", command=self.scan).pack(side="right")
        ttk.Button(bar, text="Choose folder…", command=self.choose).pack(side="right", padx=6)
        path = ttk.Frame(self, padding=(12, 0))
        path.pack(fill="x")
        ttk.Entry(path, textvariable=self.root_dir).pack(fill="x")

    def _build_tabs(self):
        self.nb = ttk.Notebook(self, padding=(10, 8))
        self.nb.pack(fill="both", expand=True)
        self.tab_overview = OverviewTab(self.nb, self)
        self.tab_organize = OrganizeTab(self.nb, self)
        self.tab_tags = TagsTab(self.nb, self)
        self.tab_dupes = DupesTab(self.nb, self)
        self.tab_import = ImportTab(self.nb, self)
        self.tab_needs = NeedsWorkTab(self.nb, self)
        self.tab_genres = GenresTab(self.nb, self)
        self.tab_playlists = PlaylistTab(self.nb, self)
        self.tab_history = HistoryTab(self.nb, self)
        for t, n in [(self.tab_overview, "Overview"), (self.tab_organize, "Organise"),
                     (self.tab_tags, "Tags"), (self.tab_dupes, "Duplicates"),
                     (self.tab_import, "Import"), (self.tab_needs, "Needs Work"),
                     (self.tab_genres, "Genres"), (self.tab_playlists, "Playlists"),
                     (self.tab_history, "History")]:
            self.nb.add(t, text=n)

    def _build_footer(self):
        f = ttk.Frame(self, padding=(12, 6))
        f.pack(fill="x")
        self.progress = ttk.Progressbar(f, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.status = ttk.Label(f, text="Ready", width=34, anchor="e")
        self.status.pack(side="right")

    def set_status(self, s):
        self.status.configure(text=s)

    def log(self, msg):
        self.tab_history.append(str(msg))

    # -- actions ----------------------------------------------------------
    def choose(self):
        d = filedialog.askdirectory(title="Choose your DJ collection folder")
        if d:
            self.root_dir.set(d)
            self._save_pref()
            self.scan()

    def require_root(self):
        d = self.root_dir.get()
        if not d or not os.path.isdir(d):
            messagebox.showwarning(APP, "Choose your DJ collection folder first.")
            return None
        return d

    def scan(self):
        d = self.require_root()
        if not d:
            return
        self._save_pref()

        def work(progress, log):
            recs = library.scan(d, progress=progress)
            return recs, library.health(recs)

        def done(res):
            self.recs, self.health = res
            self.tab_overview.render(self.health)
            for t in (self.tab_organize, self.tab_tags, self.tab_dupes,
                      self.tab_import, self.tab_needs, self.tab_genres,
                      self.tab_playlists):
                t.invalidate()
            self.log(f"Scanned {len(self.recs)} files in {d}")
            self.refresh_banner()
            self.refresh_notice()

        self.task.run(work, done, "Scanning library")


# ------------------------------------------------------------------- tabs
class BaseTab(ttk.Frame):
    def __init__(self, nb, app):
        super().__init__(nb, padding=12)
        self.app = app
        self.build()

    def build(self):
        pass

    def invalidate(self):
        pass

    def need_scan(self):
        if not self.app.recs:
            messagebox.showinfo(APP, "Scan your collection first.")
            return True
        return False


def tree(parent, columns, widths, height=14):
    frame = ttk.Frame(parent)
    tv = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    for c, w in zip(columns, widths):
        tv.heading(c, text=c)
        tv.column(c, width=w, anchor="w", stretch=(w > 200))
    sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return frame, tv


class OverviewTab(BaseTab):
    def build(self):
        self.cards = ttk.Frame(self)
        self.cards.pack(fill="x", pady=(0, 12))
        self.stat_vars = {}
        for i, (key, label) in enumerate([
                ("total", "Tracks"), ("bytes", "Size"), ("analyzed", "Key + BPM"),
                ("genre", "Genre tagged"), ("energy", "Energy tagged"), ("protected", "Protected")]):
            card = ttk.Frame(self.cards, relief="solid", borderwidth=1, padding=10)
            card.grid(row=0, column=i, padx=4, sticky="ew")
            self.cards.columnconfigure(i, weight=1)
            v = tk.StringVar(value="—")
            self.stat_vars[key] = v
            ttk.Label(card, textvariable=v, font=("Helvetica", 16, "bold")).pack(anchor="w")
            ttk.Label(card, text=label, foreground="#666").pack(anchor="w")

        cols = ttk.Frame(self)
        cols.pack(fill="both", expand=True)
        left = ttk.LabelFrame(cols, text="Needs attention", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.issues = tk.Listbox(left, font=("Menlo", 11))
        self.issues.pack(fill="both", expand=True)
        right = ttk.LabelFrame(cols, text="Genres", padding=8)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.genres = tk.Listbox(right, font=("Menlo", 11))
        self.genres.pack(fill="both", expand=True)

    def render(self, h):
        self.stat_vars["total"].set(f"{h['total']:,}")
        self.stat_vars["bytes"].set(human_size(h["bytes"]))
        self.stat_vars["analyzed"].set(f"{h['pct_analyzed']:.0f}%")
        self.stat_vars["genre"].set(f"{h['pct_genre']:.0f}%")
        self.stat_vars["energy"].set(f"{h['pct_energy']:.0f}%")
        self.stat_vars["protected"].set(f"{h['protected']:,}")

        self.issues.delete(0, "end")
        for label, items, hint in [
            ("missing key", h["needs_analysis"], "run through your analysis tool"),
            ("missing genre", h["no_genre"], "Tags tab can normalise what exists"),
            ("missing artist", h["no_artist"], "Tags tab can infer from filename"),
            ("spam in genre", h["spam_genre"], "Tags tab clears these"),
            ("spam in comment", h["spam_comment"], "Tags tab clears these"),
            ("no energy rating", h["no_energy"], "Tags tab promotes MIK energy"),
        ]:
            self.issues.insert("end", f"{len(items):5}  {label:22} — {hint}")

        self.genres.delete(0, "end")
        for g, c in h["genres"].most_common(30):
            self.genres.insert("end", f"{c:5}  {g}")


class OrganizeTab(BaseTab):
    def build(self):
        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(0, 6))
        self.consolidate = tk.BooleanVar(value=True)
        self.keep_sets = tk.BooleanVar(value=False)
        self.route_unan = tk.BooleanVar(value=True)
        self.make_pl = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="One copy per track (playlists follow it)",
                        variable=self.consolidate).pack(side="left")
        ttk.Checkbutton(opts, text="Also keep gig sets as folders",
                        variable=self.keep_sets).pack(side="left", padx=12)
        opts2 = ttk.Frame(self)
        opts2.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(opts2, text="Send unanalysed tracks to 'To Be Processed'",
                        variable=self.route_unan).pack(side="left")
        ttk.Checkbutton(opts2, text="Save current folders as playlists",
                        variable=self.make_pl).pack(side="left", padx=12)
        ttk.Label(opts2, text="   Genre detail").pack(side="left")
        self.detail = tk.StringVar(value=settings.get("genre_detail") or "broad")
        box = ttk.Combobox(opts2, textvariable=self.detail, width=34, state="readonly",
                           values=["broad — fewer, wider folders",
                                   "fine — keep subgenres apart"])
        box.set("fine — keep subgenres apart" if self.detail.get() == "fine"
                else "broad — fewer, wider folders")
        box.pack(side="left", padx=6)
        self._detail_box = box

        ttk.Label(self, foreground="#666", wraplength=980, justify="left",
                  text="Each track ends up as one file at Tracks/<Genre>/Artist - Title. "
                       "Every folder you have now — the Spotify and Tidal vibe imports, the gig "
                       "sets — is written to _Playlists as an .m3u8 that points at that one file, "
                       "so a track curated into five vibes is five playlist entries and one file "
                       "on disk. Extra copies move to _Quarantine, never deleted. "
                       "'To Be Processed' and 'Processed' are never touched."
                  ).pack(anchor="w", pady=(0, 8))

        src = ttk.LabelFrame(self, text="Folders to reorganise", padding=8)
        src.pack(fill="x", pady=(0, 8))
        ttk.Label(src, foreground="#666",
                  text="Untick anything you want left exactly where it is."
                  ).pack(anchor="w")
        self.folder_wrap = ttk.Frame(src)
        self.folder_wrap.pack(fill="x", pady=(4, 0))
        self.folder_vars = {}

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Preview plan", command=self.preview).pack(side="left")
        self.apply_btn = ttk.Button(btns, text="Apply", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="Exclude selected rows",
                   command=self.exclude_rows).pack(side="left", padx=(12, 0))
        ttk.Button(btns, text="Clear exclusions",
                   command=self.clear_exclusions).pack(side="left", padx=6)

        self.summary = ttk.Label(self, text="", foreground="#444", wraplength=980,
                                 justify="left")
        self.summary.pack(anchor="w", pady=(0, 4))

        f, self.tv = tree(self, ("From", "To"), (480, 480), height=14)
        f.pack(fill="both", expand=True)
        self.plan = None
        self.excluded = set()          # individual paths the user opted out of

    def invalidate(self):
        self.plan = None
        self.apply_btn.configure(state="disabled")
        self.tv.delete(*self.tv.get_children())
        self.summary.configure(text="")
        self.excluded = set()
        self._build_folder_list()

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
            default = name not in remembered
            v = tk.BooleanVar(value=keep[name].get() if name in keep else default)
            v.trace_add("write", lambda *a: self._remember_folders())
            self.folder_vars[name] = v
            ttk.Checkbutton(self.folder_wrap, text=f"{name} ({n})", variable=v
                            ).grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 14))

    def _remember_folders(self):
        settings.set("excluded_folders",
                     sorted(n for n, v in self.folder_vars.items() if not v.get()))

    def _excluded_paths(self):
        """Paths to leave alone: whole unticked folders, plus individual rows."""
        off = {name for name, v in self.folder_vars.items() if not v.get()}
        paths = set(self.excluded)
        if off:
            paths |= {r.path for r in self.app.recs if r.top in off}
        return paths

    def exclude_rows(self):
        if not self.plan:
            messagebox.showinfo(APP, "Preview a plan first, then select rows to exclude.")
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
        detail = "fine" if self._detail_box.get().startswith("fine") else "broad"
        settings.set("genre_detail", detail)

        def work(progress, log):
            canonical = {}
            if consolidate:
                log("Finding duplicate copies…")
                found = dupes.find(self.app.recs, progress=progress)
                canonical = dupes.canonical_map(found)
                # never quarantine a copy the user asked to leave alone
                canonical = {k: v for k, v in canonical.items()
                             if k not in exclude and v not in exclude}
                log(f"{len(canonical)} extra copies will collapse into "
                    f"{len(set(canonical.values()))} canonical files")
            return organize.plan(root, self.app.recs, keep_sets=keep_sets,
                                 route_unanalyzed=route, canonical=canonical,
                                 exclude=exclude, detail=detail)

        def done(res):
            moves, pls, st = res
            self.plan = res
            self.tv.delete(*self.tv.get_children())
            for r, d in moves[:2000]:
                self.tv.insert("", "end", values=(r.rel, os.path.relpath(d, root)))
            cats = collections.Counter(
                os.path.relpath(d, root).split(os.sep)[0] for _, d in moves)
            bits = ", ".join(f"{v} → {k}/" for k, v in cats.most_common())
            extra = f" · {st['deduped']} duplicate copies quarantined" if st.get("deduped") else ""
            left = f" · {st['excluded']} left alone" if st.get("excluded") else ""
            self.summary.configure(
                text=f"{len(moves)} moves · {len(pls)} playlists · "
                     f"{st['skipped_protected']} protected{left}{extra} · {bits}")
            self.apply_btn.configure(state="normal" if moves else "disabled")

        self.app.task.run(work, done, "Planning")

    def apply(self):
        if not self.plan:
            return
        moves, pls, st = self.plan
        if not messagebox.askyesno(
                APP, f"Move {len(moves)} files into the new structure?\n\n"
                     f"{len(pls)} playlists will be written to _Playlists first.\n"
                     "This is fully reversible from the History tab."):
            return
        root = self.app.root_dir.get()
        keep_pl = self.make_pl.get()

        def work(progress, log):
            return organize.apply(root, moves, pls if keep_pl else {}, log=log, progress=progress)

        def done(path):
            messagebox.showinfo(APP, "Reorganisation complete.\nUndo is available in History.")
            self.app.tab_history.refresh()
            self.app.scan()

        self.app.task.run(work, done, "Moving files")


class TagsTab(BaseTab):
    def build(self):
        self.vars = {}
        box = ttk.LabelFrame(self, text="Fixes to apply", padding=10)
        box.pack(fill="x", pady=(0, 10))
        for k in fixtags.FIXES:
            v = tk.BooleanVar(value=True)
            self.vars[k] = v
            ttk.Checkbutton(box, text=fixtags.FIX_LABELS[k], variable=v).pack(anchor="w")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Preview changes", command=self.preview).pack(side="left")
        self.apply_btn = ttk.Button(btns, text="Apply", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left", padx=8)
        self.summary = ttk.Label(btns, text="", foreground="#444")
        self.summary.pack(side="left", padx=12)

        f, self.tv = tree(self, ("Track", "Field", "Before", "After"),
                          (330, 90, 260, 260), height=16)
        f.pack(fill="both", expand=True)
        self.changes = None

    def invalidate(self):
        self.changes = None
        self.apply_btn.configure(state="disabled")
        self.tv.delete(*self.tv.get_children())
        self.summary.configure(text="")

    def preview(self):
        if self.need_scan():
            return
        fixes = {k for k, v in self.vars.items() if v.get()}
        if not fixes:
            messagebox.showinfo(APP, "Select at least one fix.")
            return

        def work(progress, log):
            return fixtags.plan(self.app.recs, fixes)

        def done(changes):
            self.changes = changes
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
            c = fixtags.summarize(changes)
            self.summary.configure(text=" · ".join(f"{v} {k}" for k, v in c.most_common()))
            self.apply_btn.configure(state="normal" if changes else "disabled")

        self.app.task.run(work, done, "Checking tags")

    def apply(self):
        if not self.changes:
            return
        if not messagebox.askyesno(APP, f"Write tags on {len(self.changes)} files?\n"
                                        "This is reversible from the History tab."):
            return
        root = self.app.root_dir.get()
        changes = self.changes

        def work(progress, log):
            return fixtags.apply(root, changes, log=log, progress=progress)

        def done(res):
            path, n, failed = res
            messagebox.showinfo(APP, f"Updated {n} files." + (f"\n{failed} failed." if failed else ""))
            self.app.tab_history.refresh()
            self.app.scan()

        self.app.task.run(work, done, "Writing tags")


class DupesTab(BaseTab):
    def build(self):
        ttk.Label(self, foreground="#666", wraplength=980, justify="left",
                  text="Exact = identical audio. Likely = same artist, title and version with the "
                       "same length. Different remixes are never grouped together. Nothing is "
                       "deleted — extras move to _Quarantine so you can check them first."
                  ).pack(anchor="w", pady=(0, 8))
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Find duplicates", command=self.find).pack(side="left")
        self.q_exact = ttk.Button(btns, text="Quarantine exact matches",
                                  command=lambda: self.quarantine("exact"), state="disabled")
        self.q_exact.pack(side="left", padx=8)
        self.q_all = ttk.Button(btns, text="Quarantine exact + likely",
                                command=lambda: self.quarantine("all"), state="disabled")
        self.q_all.pack(side="left")
        self.summary = ttk.Label(btns, text="", foreground="#444")
        self.summary.pack(side="left", padx=12)

        f, self.tv = tree(self, ("Group", "Keep", "Format", "Length", "Path"),
                          (120, 60, 90, 70, 620), height=16)
        f.pack(fill="both", expand=True)
        self.found = None

    def invalidate(self):
        self.found = None
        for b in (self.q_exact, self.q_all):
            b.configure(state="disabled")
        self.tv.delete(*self.tv.get_children())
        self.summary.configure(text="")

    def find(self):
        if self.need_scan():
            return

        def work(progress, log):
            return dupes.find(self.app.recs, progress=progress)

        def done(res):
            self.found = res
            self.tv.delete(*self.tv.get_children())
            root = self.app.root_dir.get()
            n = 0
            for kind in ("exact", "likely"):
                for gi, g in enumerate(res[kind], 1):
                    k = dupes.keeper(g)
                    for r in g:
                        self.tv.insert("", "end", values=(
                            f"{kind} #{gi}", "KEEP" if r is k else "",
                            r.ext.lstrip("."), f"{(r.duration or 0)/60:.1f}m",
                            os.path.relpath(r.path, root)))
                        n += 1
            re_ = dupes.reclaimable(res["exact"])
            rl = dupes.reclaimable(res["likely"])
            self.summary.configure(
                text=f"exact: {len(res['exact'])} groups ({human_size(re_)}) · "
                     f"likely: {len(res['likely'])} groups ({human_size(rl)})")
            self.q_exact.configure(state="normal" if res["exact"] else "disabled")
            self.q_all.configure(state="normal" if (res["exact"] or res["likely"]) else "disabled")

        self.app.task.run(work, done, "Hashing audio")

    def quarantine(self, which):
        if not self.found:
            return
        groups = self.found["exact"] + (self.found["likely"] if which == "all" else [])
        count = sum(len(g) - 1 for g in groups)
        if not messagebox.askyesno(APP, f"Move {count} duplicate files to _Quarantine?\n\n"
                                        "Nothing is deleted. Reversible from History."):
            return
        root = self.app.root_dir.get()

        def work(progress, log):
            return dupes.quarantine(root, groups, log=log)

        def done(res):
            path, n = res
            messagebox.showinfo(APP, f"Moved {n} files to _Quarantine.")
            self.app.tab_history.refresh()
            self.app.scan()

        self.app.task.run(work, done, "Quarantining")


class ImportTab(BaseTab):
    def build(self):
        ttk.Label(self, foreground="#666", wraplength=980, justify="left",
                  text="Add new music. Tracks with key and BPM go straight to Tracks/<Genre>. "
                       "Anything missing analysis lands in 'To Be Processed' for your "
                       "analysis tool. Tracks already in the library are flagged, not copied."
                  ).pack(anchor="w", pady=(0, 8))
        pf = ttk.Frame(self)
        pf.pack(fill="x", pady=(0, 8))
        ttk.Button(pf, text="Sort the 'Processed' folder",
                   command=self.intake_processed).pack(side="left")
        ttk.Label(pf, foreground="#666",
                  text="  — file everything you have already run through "
                       "your analysis tool").pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Add files…", command=self.add_files).pack(side="left")
        ttk.Button(btns, text="Add folder…", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear", command=self.clear).pack(side="left")
        self.move_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="Move (uncheck to copy)", variable=self.move_var).pack(side="left", padx=12)
        self.apply_btn = ttk.Button(btns, text="Import", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left", padx=8)

        pl = ttk.Frame(self)
        pl.pack(fill="x", pady=(0, 8))
        ttk.Label(pl, text="Add imported tracks to playlist").pack(side="left")
        self.playlist_var = tk.StringVar()
        self.playlist_box = ttk.Combobox(pl, textvariable=self.playlist_var, width=44)
        self.playlist_box.pack(side="left", padx=6)
        ttk.Label(pl, foreground="#666",
                  text="optional — pick an existing playlist or type a new name").pack(side="left")

        self.summary = ttk.Label(self, text="", foreground="#444")
        self.summary.pack(anchor="w", pady=(0, 4))

        f, self.tv = tree(self, ("Action", "Track", "Destination", "Why"),
                          (100, 300, 330, 260), height=16)
        f.pack(fill="both", expand=True)
        self.sources, self.results = [], None
        self.exclude = None      # library folder being intaken, if any

    def invalidate(self):
        root = self.app.root_dir.get()
        if root and os.path.isdir(root):
            self.playlist_box.configure(values=playlists.existing(root))

    def clear(self):
        self.sources, self.results, self.exclude = [], None, None
        self.tv.delete(*self.tv.get_children())
        self.summary.configure(text="")
        self.apply_btn.configure(state="disabled")

    def add_files(self):
        fs = filedialog.askopenfilenames(title="Choose tracks to import")
        if fs:
            self.sources.extend(fs)
            self.exclude = None
            self.preview()

    def add_folder(self):
        d = filedialog.askdirectory(title="Choose a folder to import")
        if d:
            self.sources.append(d)
            self.exclude = None
            self.preview()

    def intake_processed(self):
        if self.need_scan():
            return
        root = self.app.root_dir.get()
        folder = os.path.join(root, importer.PROCESSED)
        if not os.path.isdir(folder):
            messagebox.showinfo(APP, f"No '{importer.PROCESSED}' folder in {root}.")
            return
        self.sources = [folder]
        self.exclude = importer.PROCESSED
        self.preview()

    def preview(self):
        if self.need_scan():
            return
        root = self.app.root_dir.get()
        srcs = list(self.sources)
        known = self.app.recs
        if self.exclude:
            known = importer.library_excluding(known, root, self.exclude)

        def work(progress, log):
            return importer.plan(root, srcs, known, progress=progress)

        def done(res):
            self.results = res
            self.tv.delete(*self.tv.get_children())
            for x in res[:2000]:
                self.tv.insert("", "end", values=(
                    x["action"], x["rec"].display[:90],
                    os.path.relpath(x["dest"], root) if x["dest"] else "—",
                    x["reason"]))
            c = importer.summarize(res)
            self.summary.configure(text=" · ".join(f"{v} {k}" for k, v in c.most_common()))
            self.apply_btn.configure(state="normal" if any(x["dest"] for x in res) else "disabled")

        self.app.task.run(work, done, "Reading new files")

    def apply(self):
        if not self.results:
            return
        todo = [x for x in self.results if x["dest"]]
        verb = "Move" if self.move_var.get() else "Copy"
        if not messagebox.askyesno(APP, f"{verb} {len(todo)} files into the library?"):
            return
        root = self.app.root_dir.get()
        results, move = self.results, self.move_var.get()
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
            path, n, added = res
            msg = f"Imported {n} files."
            if plname:
                msg += f"\nAdded {added} to '{plname}'."
            messagebox.showinfo(APP, msg)
            self.clear()
            self.app.tab_history.refresh()
            self.app.after(400, self.app.offer_playlist_repair)
            self.app.scan()

        self.app.task.run(work, done, "Importing")


class NeedsWorkTab(BaseTab):
    """Find and act on tracks whose metadata still needs outside help."""

    FILTERS = [
        ("Missing key — needs analysing", lambda r: not r.analyzed),
        ("Missing BPM (most DJ apps fill this in themselves)", lambda r: not r.bpm),
        ("No energy rating", lambda r: r.energy is None),
        ("Missing genre", lambda r: not r.genre),
        ("Missing artist", lambda r: not r.artist),
        ("Low bitrate (under 192 kbps)", lambda r: 0 < getattr(r, "bitrate", 0) < 192000),
    ]

    def build(self):
        ttk.Label(self, foreground="#666", wraplength=980, justify="left",
                  text="Everything here needs something Sortero can't work out itself. "
                       "Pick a filter, select the tracks you want, and stage them in "
                       "'To Be Processed'. Run that folder through your analysis tool — "
                       "Mixed In Key, rekordbox, Mixxx, or whatever you use — and when "
                       "the results land in 'Processed', the Import tab files them "
                       "automatically."
                  ).pack(anchor="w", pady=(0, 4))
        ttk.Label(self, foreground="#8a5a00", font=("Helvetica", 12, "bold"),
                  text="If you use Platinum Notes: run it BEFORE your key/BPM analysis"
                  ).pack(anchor="w", pady=(0, 2))
        ttk.Label(self, foreground="#666", wraplength=980, justify="left",
                  text="Platinum Notes re-encodes the audio, so analysing first and "
                       "mastering second leaves the tags describing a file that no "
                       "longer exists. Skip this if you don't use it — Sortero only "
                       "needs the key and BPM tags, whatever wrote them."
                  ).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Show").pack(side="left")
        self.filter_var = tk.StringVar(value=self.FILTERS[0][0])
        box = ttk.Combobox(row, textvariable=self.filter_var, width=52, state="readonly",
                           values=[f[0] for f in self.FILTERS])
        box.pack(side="left", padx=6)
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(row, text="Select all", command=self.select_all).pack(side="left", padx=6)
        ttk.Button(row, text="Reveal selected",
                   command=self.reveal).pack(side="left")
        self.count = ttk.Label(row, text="", foreground="#444")
        self.count.pack(side="left", padx=12)

        row0 = ttk.Frame(self)
        row0.pack(fill="x", pady=(0, 6))
        self.skip_mixes = tk.BooleanVar(value=True)
        ttk.Checkbutton(row0, text="Hide your set recordings "
                                   "(the Recorded Mixes folder, and anything over 20 minutes)",
                        variable=self.skip_mixes,
                        command=self.refresh).pack(side="left")

        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(0, 8))
        self.stage_btn = ttk.Button(row2, text="Stage selected in 'To Be Processed'",
                                    command=self.stage, state="disabled")
        self.stage_btn.pack(side="left")
        ttk.Button(row2, text="Copy list", command=self.copy).pack(side="left", padx=8)

        f, self.tv = tree(self, ("Track", "Key", "BPM", "Energy", "Genre", "Where"),
                          (330, 60, 60, 60, 150, 300), height=15)
        f.pack(fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.rows = []

    def invalidate(self):
        self.refresh()

    def _predicate(self):
        for label, fn in self.FILTERS:
            if label == self.filter_var.get():
                return fn
        return self.FILTERS[0][1]

    def refresh(self):
        self.tv.delete(*self.tv.get_children())
        self.rows = []
        if not self.app.recs:
            self.count.configure(text="")
            return
        pred = self._predicate()
        skip_mixes = self.skip_mixes.get()
        for r in self.app.recs:
            if r.protected or not pred(r):
                continue
            # Your own set recordings are never candidates for analysis.
            if skip_mixes and (r.is_recording or
                               (r.duration and r.duration >= organize.MIX_MIN_SECONDS)):
                continue
            self.rows.append(r)
            self.tv.insert("", "end", values=(
                r.display[:80], r.camelot or r.key or "—", r.bpm or "—",
                r.energy if r.energy is not None else "—",
                (r.genre or "—")[:28], os.path.dirname(r.rel) or "(root)"))
        self.count.configure(text=f"{len(self.rows)} tracks")
        self._sync()

    def _sync(self):
        n = len(self.tv.selection())
        self.stage_btn.configure(state="normal" if n else "disabled")
        if n:
            self.count.configure(text=f"{len(self.rows)} tracks · {n} selected")
        else:
            self.count.configure(text=f"{len(self.rows)} tracks")

    def select_all(self):
        self.tv.selection_set(self.tv.get_children())
        self._sync()

    def _selected_recs(self):
        return [self.rows[self.tv.index(i)] for i in self.tv.selection()]

    def reveal(self):
        recs = self._selected_recs()
        if not recs:
            messagebox.showinfo(APP, "Select a track first.")
            return
        paths.reveal(recs[0].path)

    def copy(self):
        recs = self._selected_recs() or self.rows
        if not recs:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(r.display for r in recs))
        messagebox.showinfo(APP, f"Copied {len(recs)} track names.")

    def stage(self):
        recs = self._selected_recs()
        if not recs:
            return
        if not messagebox.askyesno(
                APP, f"Move {len(recs)} tracks into 'To Be Processed'?\n\n"
                     "Run them through your analysis tool, save the results into "
                     "'Processed', then use Import → \"Sort the 'Processed' folder\".\n\n"
                     "If you use Platinum Notes, run it BEFORE analysing — it "
                     "re-encodes the audio.\n\n"
                     "Reversible from History."):
            return
        root = self.app.root_dir.get()

        all_recs = self.app.recs

        def work(progress, log):
            # all_recs matters: the folder's *other* tracks are what the
            # playlist is rebuilt from before these ones leave.
            return organize.stage_for_analysis(root, recs, all_recs=all_recs,
                                               log=log, progress=progress)

        def done(res):
            path, n = res
            messagebox.showinfo(
                APP, f"Staged {n} tracks in 'To Be Processed'.\n\n"
                     "Run that folder through your analysis tool — Mixed In Key, "
                     "rekordbox, Mixxx — and save the results into 'Processed'.\n\n"
                     "If you use Platinum Notes, run it FIRST. It re-encodes the "
                     "audio, so mastering after analysing discards the analysis.")
            self.app.tab_history.refresh()
            self.app.scan()

        self.app.task.run(work, done, "Staging tracks")


class GenresTab(BaseTab):
    """Assign genres in bulk, by hand or from Discogs."""

    def build(self):
        ttk.Label(self, foreground="#666", wraplength=980, justify="left",
                  text="Sortero can only infer a genre from what a file already "
                       "carries, and plenty carry nothing. Select tracks and set one "
                       "directly, or look them up on Discogs — its styles are the "
                       "subgenre detail you want. Sorting by artist or folder makes "
                       "whole groups selectable at once."
                  ).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Show").pack(side="left")
        self.filter_var = tk.StringVar(value="Missing or unusable genre")
        fb = ttk.Combobox(row, textvariable=self.filter_var, width=30, state="readonly",
                          values=["Missing or unusable genre", "Everything"])
        fb.pack(side="left", padx=6)
        fb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(row, text="Sort by").pack(side="left", padx=(10, 0))
        self.sort_var = tk.StringVar(value="Artist")
        sb = ttk.Combobox(row, textvariable=self.sort_var, width=14, state="readonly",
                          values=["Artist", "Folder", "Title"])
        sb.pack(side="left", padx=6)
        sb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(row, text="Select all", command=self.select_all).pack(side="left", padx=6)
        self.count = ttk.Label(row, text="", foreground="#444")
        self.count.pack(side="left", padx=10)

        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(0, 8))
        ttk.Label(row2, text="Set genre to").pack(side="left")
        self.genre_var = tk.StringVar()
        self.genre_box = ttk.Combobox(row2, textvariable=self.genre_var, width=30)
        self.genre_box.pack(side="left", padx=6)
        ttk.Button(row2, text="Apply to selected",
                   command=self.apply_manual).pack(side="left")
        ttk.Button(row2, text="Use folder name",
                   command=self.apply_from_folder).pack(side="left", padx=6)
        ttk.Button(row2, text="Look up selected on Discogs",
                   command=self.lookup).pack(side="left", padx=(16, 0))
        self.stop_btn = ttk.Button(row2, text="Stop", command=self.stop_lookup,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(row2, text="Accept suggestions",
                   command=self.apply_suggested).pack(side="left", padx=6)

        self.lookup_status = ttk.Label(self, text="", foreground="#1d5c3a",
                                       font=("Helvetica", 12, "bold"))
        self.lookup_status.pack(anchor="w", pady=(0, 4))

        tok = ttk.Frame(self)
        tok.pack(fill="x", pady=(0, 6))
        ttk.Label(tok, text="Discogs token (optional)").pack(side="left")
        self.token_var = tk.StringVar(value=settings.get("discogs_token") or "")
        ttk.Entry(tok, textvariable=self.token_var, width=34,
                  show="•").pack(side="left", padx=6)
        ttk.Button(tok, text="Save", command=self._save_token).pack(side="left")
        ttk.Label(tok, foreground="#666",
                  text="  — works without one; a free token from discogs.com/settings/"
                       "developer makes lookups about 2.5x faster"
                  ).pack(side="left")

        f, self.tv = tree(self, ("Track", "Artist", "Genre", "Suggested", "Where"),
                          (300, 190, 150, 170, 200), height=15)
        f.pack(fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.rows, self.suggested = [], {}
        self._stop = threading.Event()
        self._looking = False

    def invalidate(self):
        vocab = sorted({lab for _, lab in organize.GENRE_RULES} |
                       {lab for _, lab in organize.FINE_RULES})
        self.genre_box.configure(values=vocab)
        self.suggested = {}
        self.refresh()

    def _needs(self, r):
        return not organize.canon_genre(r.genre) and not (r.genre or "").strip()

    def refresh(self):
        self.tv.delete(*self.tv.get_children())
        self.rows = []
        if not self.app.recs:
            self.count.configure(text="")
            return
        only_missing = self.filter_var.get().startswith("Missing")
        rows = [r for r in self.app.recs
                if not r.protected and not r.is_recording
                and (self._needs(r) if only_missing else True)]
        keys = {"Artist": lambda r: (r.artist or "").lower(),
                "Folder": lambda r: r.rel.lower(),
                "Title": lambda r: (r.title or "").lower()}
        rows.sort(key=keys.get(self.sort_var.get(), keys["Artist"]))
        for r in rows:
            self.rows.append(r)
            sug = self.suggested.get(r.path, (None, []))[0] or ""
            self.tv.insert("", "end", values=(
                (r.title or os.path.basename(r.path))[:60], (r.artist or "")[:38],
                (r.genre or "—")[:26], sug[:28], os.path.dirname(r.rel)[:34] or "(root)"))
        self.count.configure(text=f"{len(self.rows)} tracks")
        self._sync()

    def _sync(self):
        n = len(self.tv.selection())
        self.count.configure(text=f"{len(self.rows)} tracks"
                                  + (f" · {n} selected" if n else ""))

    def select_all(self):
        self.tv.selection_set(self.tv.get_children())
        self._sync()

    def _selected(self):
        return [self.rows[self.tv.index(i)] for i in self.tv.selection()]

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
                    f"Set {len(recs)} tracks to '{genre}'?")

    def apply_from_folder(self):
        """Tracks already filed under Tracks/<Genre> know their genre already."""
        recs = self._selected() or self.rows
        pairs = []
        for r in recs:
            parts = r.rel.split(os.sep)
            if (len(parts) > 2 and parts[0] == organize.TRACKS_DIR
                    and parts[1] != organize.UNSORTED):
                pairs.append((r, parts[1]))
        if not pairs:
            messagebox.showinfo(
                APP, "None of these sit in a genre folder to copy from.\n\n"
                     "This fills the tag in from the folder a track is already "
                     "filed under — it doesn't help for anything in Unsorted.")
            return
        self._write(pairs, f"Set {len(pairs)} tracks to the genre of the folder "
                           "they're already in?")

    def apply_suggested(self):
        recs = self._selected() or self.rows
        pairs = [(r, self.suggested[r.path][0]) for r in recs
                 if self.suggested.get(r.path, (None,))[0]]
        if not pairs:
            messagebox.showinfo(APP, "No suggestions yet — run a Discogs lookup first.")
            return
        self._write(pairs, f"Accept {len(pairs)} suggested genres?")

    def _write(self, pairs, question):
        if not messagebox.askyesno(APP, question + "\n\nUndoable from History."):
            return
        root = self.app.root_dir.get()

        def work(progress, log):
            return genres.apply_genres(root, pairs, log=log, progress=progress)

        def done(res):
            _, n, failed = res
            messagebox.showinfo(APP, f"Updated {n} tracks."
                                     + (f"\n{failed} failed." if failed else ""))
            self.app.tab_history.refresh()
            self.app.scan()

        self.app.task.run(work, done, "Writing genres")

    def _save_token(self):
        settings.set("discogs_token", self.token_var.get().strip())
        messagebox.showinfo(APP, "Saved. Lookups will run at the faster rate."
                            if self.token_var.get().strip() else
                            "Cleared. Lookups will use the slower anonymous rate.")

    def stop_lookup(self):
        self._stop.set()
        self.lookup_status.configure(text="Stopping after the current track…")

    def _tick(self):
        """Refresh the table while a lookup runs, so results appear as they land."""
        if not self._looking:
            return
        self.refresh()
        self.after(4000, self._tick)

    def lookup(self):
        recs = [r for r in self._selected() if genres.worth_asking(r)]
        skipped = len(self._selected()) - len(recs)
        if not recs:
            messagebox.showinfo(APP, "Select the tracks you want looked up."
                                     + (f"\n\n{skipped} have no artist or title to "
                                        "search with — set those by hand instead."
                                        if skipped else ""))
            return
        cache = genres.load_cache()
        fresh = [r for r in recs if genres.key_for(r) not in cache]
        mins = genres.eta_minutes(len(fresh))
        if not messagebox.askyesno(
                APP, f"Look up {len(recs)} tracks on Discogs?\n\n"
                     f"{len(recs) - len(fresh)} are already cached and cost nothing; "
                     f"{len(fresh)} need asking, which takes about {mins} minute(s) "
                     "because Discogs rate-limits free use.\n\n"
                     "Results appear in the Suggested column as they arrive, and you "
                     "can Stop at any point — everything fetched is kept."
                     + (f"\n\n{skipped} selected tracks have no artist or title and "
                        "were left out." if skipped else "")
                     + "\n\nOnly artist and title are sent to Discogs."):
            return

        self._stop.clear()
        self._looking = True
        self.stop_btn.configure(state="normal")
        self.lookup_status.configure(text=f"Asking Discogs about {len(recs)} tracks…")
        self.after(2000, self._tick)
        results = self.suggested          # worker fills this in as it goes

        def work(progress, log):
            return genres.bulk_lookup(
                recs, progress=progress, log=log,
                on_result=lambda path, val: results.__setitem__(path, val),
                should_stop=self._stop.is_set)

        def done(found):
            self._looking = False
            self.stop_btn.configure(state="disabled")
            got = sum(1 for v in self.suggested.values() if v[0])
            self.lookup_status.configure(
                text=f"Discogs finished — {got} tracks have a suggested genre.")
            self.refresh()
            messagebox.showinfo(
                APP, f"Looked up {len(found)} tracks; {got} map to a genre Sortero "
                     "recognises.\n\nReview the Suggested column, then "
                     "'Accept suggestions'.")

        self.app.task.run(work, done, "Asking Discogs")


class PlaylistTab(BaseTab):
    """Rebuild a curated streaming playlist against the local collection."""

    def build(self):
        ttk.Label(self, foreground="#666", wraplength=980, justify="left",
                  text="Paste a Spotify or TIDAL playlist link. Sortero matches each "
                       "track to a file you already have and writes an .m3u8 you can "
                       "import into rekordbox or Mixxx. Connect an account below to "
                       "read full playlists of any length; without it, Spotify links "
                       "fall back to a public preview capped at 50 tracks and TIDAL "
                       "links need connecting. Pasting a tracklist always works."
                  ).pack(anchor="w", pady=(0, 8))

        conn = ttk.LabelFrame(self, text="Accounts", padding=8)
        conn.pack(fill="x", pady=(0, 8))
        ttk.Label(conn, foreground="#666", wraplength=960, justify="left",
                  text=f"One-time setup: create a free app at developer.tidal.com or "
                       f"developer.spotify.com/dashboard, add the redirect URI "
                       f"{auth.REDIRECT_URI} to it, and paste its Client ID here. You sign "
                       f"in on their website — Sortero never sees your password, and tokens "
                       f"are kept in your macOS Keychain."
                  ).pack(anchor="w", pady=(0, 6))
        self.client_ids, self.conn_labels = {}, {}
        for pid in ("tidal", "spotify"):
            cfg = auth.PROVIDERS[pid]
            r = ttk.Frame(conn)
            r.pack(fill="x", pady=2)
            ttk.Label(r, text=cfg["label"], width=9).pack(side="left")
            v = tk.StringVar()
            self.client_ids[pid] = v
            ttk.Entry(r, textvariable=v, width=42).pack(side="left", padx=4)
            ttk.Label(r, text="Client ID", foreground="#888").pack(side="left")
            ttk.Button(r, text=f"Connect {cfg['label']}…",
                       command=lambda p=pid: self.connect(p)).pack(side="left", padx=8)
            ttk.Button(r, text="Disconnect",
                       command=lambda p=pid: self.disconnect(p)).pack(side="left")
            lab = ttk.Label(r, text="", foreground="#666")
            lab.pack(side="left", padx=8)
            self.conn_labels[pid] = lab
        self._refresh_conn()

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Link").pack(side="left")
        self.url = tk.StringVar()
        ttk.Entry(row, textvariable=self.url).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Fetch", command=self.fetch_url).pack(side="left")
        ttk.Button(row, text="Load CSV…", command=self.load_csv).pack(side="left", padx=6)

        ttk.Label(self, text="…or paste a tracklist, one per line",
                  foreground="#666").pack(anchor="w")
        self.paste = tk.Text(self, height=5, font=("Menlo", 10), wrap="none")
        self.paste.pack(fill="x", pady=(2, 6))

        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(0, 8))
        ttk.Button(row2, text="Match pasted list", command=self.fetch_text).pack(side="left")
        ttk.Label(row2, text="   Playlist name").pack(side="left")
        self.name = tk.StringVar()
        ttk.Entry(row2, textvariable=self.name, width=32).pack(side="left", padx=6)
        self.create_btn = ttk.Button(row2, text="Create playlist",
                                     command=self.create, state="disabled")
        self.create_btn.pack(side="left", padx=6)
        ttk.Button(row2, text="Rebuild folder playlists",
                   command=self.rebuild).pack(side="right")
        ttk.Button(row2, text="Repair broken links",
                   command=self.repair).pack(side="right", padx=6)

        self.summary = ttk.Label(self, text="", foreground="#444")
        self.summary.pack(anchor="w", pady=(0, 4))
        f, self.tv = tree(self, ("Match", "From playlist", "Matched file"),
                          (110, 380, 470), height=13)
        f.pack(fill="both", expand=True)
        self.results = None

    def invalidate(self):
        self.results = None
        self.create_btn.configure(state="disabled")

    # -- accounts ----------------------------------------------------------
    def _refresh_conn(self):
        for pid, lab in self.conn_labels.items():
            tok = auth.load_tokens(pid)
            lab.configure(text="connected" if tok else "not connected",
                          foreground="#3a3" if tok else "#888")
            if tok and tok.get("client_id") and not self.client_ids[pid].get():
                self.client_ids[pid].set(tok["client_id"])

    def connect(self, pid):
        cid = self.client_ids[pid].get().strip()
        label = auth.PROVIDERS[pid]["label"]

        def work(progress, log):
            try:
                auth.connect(pid, cid, log=log)
                return ("ok", None)
            except auth.AuthError as e:
                return ("error", str(e))

        def done(res):
            if res[0] == "error":
                messagebox.showwarning(APP, res[1])
            else:
                messagebox.showinfo(APP, f"Connected to {label}.")
            self._refresh_conn()

        self.app.task.run(work, done, f"Waiting for {label} sign-in")

    def disconnect(self, pid):
        auth.forget(pid)
        self._refresh_conn()
        self.app.log(f"disconnected {auth.PROVIDERS[pid]['label']}")

    # -- loading -----------------------------------------------------------
    def _load(self, getter, label):
        if self.need_scan():
            return

        def work(progress, log):
            try:
                name, entries = getter(log)
            except playlists.SourceError as e:
                return ("error", str(e))
            except auth.AuthError as e:
                return ("error", str(e))
            return ("ok", name, playlists.match(entries, self.app.recs))

        def done(res):
            if res[0] == "error":
                messagebox.showwarning(APP, res[1])
                return
            _, name, results = res
            self.results = results
            if name and not self.name.get().strip():
                self.name.set(name)
            self.tv.delete(*self.tv.get_children())
            for x in results:
                got = os.path.relpath(x["rec"].path, self.app.root_dir.get()) if x["rec"] else ""
                self.tv.insert("", "end", values=(
                    x["how"], f"{x['artist']} - {x['title']}"[:110], got))
            c = collections.Counter(x["how"] for x in results)
            found = sum(1 for x in results if x["rec"])
            notes = []
            if len(results) == playlists.EMBED_CAP:
                notes.append("50 is Spotify's embed limit — paste the full list if it's longer")
            staged = sum(1 for r in self.app.recs if r.protected)
            if staged and found < len(results):
                notes.append(f"{staged} files are still in Processed/To Be Processed and "
                             "aren't matched — file them first")
            self.summary.configure(
                text=f"{found}/{len(results)} matched · " +
                     " · ".join(f"{v} {k}" for k, v in c.most_common()) +
                     ("   — " + "; ".join(notes) if notes else ""))
            self.create_btn.configure(state="normal" if found else "disabled")

        self.app.task.run(work, done, label)

    def fetch_url(self):
        u = self.url.get().strip()
        if not u:
            messagebox.showinfo(APP, "Paste a playlist link first.")
            return
        self._load(lambda log: playlists.from_url(u, log=log), "Fetching playlist")

    def fetch_text(self):
        t = self.paste.get("1.0", "end")
        if not t.strip():
            messagebox.showinfo(APP, "Paste a tracklist first.")
            return
        self._load(lambda log: playlists.from_text(t), "Matching tracks")

    def load_csv(self):
        p = filedialog.askopenfilename(title="Choose a playlist CSV",
                                       filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if p:
            self._load(lambda log: playlists.from_csv(p), "Reading CSV")

    # -- writing -----------------------------------------------------------
    def create(self):
        if not self.results:
            return
        name = self.name.get().strip()
        if not name:
            messagebox.showinfo(APP, "Give the playlist a name.")
            return
        paths = [x["rec"].path for x in self.results if x["rec"]]
        missing = len(self.results) - len(paths)
        if not messagebox.askyesno(
                APP, f"Write '{name}.m3u8' with {len(paths)} tracks?" +
                     (f"\n\n{missing} tracks aren't in your collection and will be left out."
                      if missing else "")):
            return
        fp = playlists.write(self.app.root_dir.get(), name, paths)
        messagebox.showinfo(APP, f"Wrote {os.path.basename(fp)} to _Playlists.")
        self.app.log(f"playlist: {fp} ({len(paths)} tracks, {missing} missing)")

    def repair(self):
        """Re-link playlist entries whose file moved or was re-encoded."""
        if self.need_scan():
            return
        root = self.app.root_dir.get()
        recs = self.app.recs

        def work(progress, log):
            return playlists.repair(root, recs, dry=True, log=log)

        def done(res):
            fixed, gone, per = res
            if not fixed and not gone:
                messagebox.showinfo(APP, "Every playlist entry points at a real file.")
                return
            if not messagebox.askyesno(
                    APP, f"{fixed + gone} playlist entries point at files that aren't "
                         f"there any more.\n\n{fixed} can be re-linked by matching "
                         f"artist and title.\n{gone} can't be matched and will be left "
                         "untouched rather than dropped.\n\nRe-link them?"):
                return

            def work2(progress, log):
                return playlists.repair(root, recs, dry=False, log=log)

            def done2(res2):
                f2, g2, _ = res2
                messagebox.showinfo(APP, f"Re-linked {f2} entries."
                                         + (f"\n{g2} still unmatched." if g2 else ""))
            self.app.task.run(work2, done2, "Repairing playlists")

        self.app.task.run(work, done, "Checking playlists")

    def rebuild(self):
        """Regenerate the folder-derived playlists against the library as it stands."""
        if self.need_scan():
            return
        if not messagebox.askyesno(APP, "Rebuild the folder playlists from the current "
                                        "library layout?\n\nExisting files with the same "
                                        "names are overwritten."):
            return
        root = self.app.root_dir.get()
        recs = self.app.recs

        def work(progress, log):
            pls = organize.playlists_from_current(root, recs)
            return organize.write_playlists(root, pls)

        def done(written):
            messagebox.showinfo(APP, f"Wrote {len(written)} playlists to _Playlists.")

        self.app.task.run(work, done, "Rebuilding playlists")


class HistoryTab(BaseTab):
    def build(self):
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(btns, text="Undo selected", command=self.undo).pack(side="left", padx=8)
        ttk.Button(btns, text="Reveal in Finder", command=self.reveal).pack(side="left")

        f, self.tv = tree(self, ("When", "Operation", "Items", "Folder"),
                          (170, 130, 80, 560), height=10)
        f.pack(fill="both", expand=True, pady=(0, 8))
        ttk.Label(self, text="Log", foreground="#666").pack(anchor="w")
        self.txt = tk.Text(self, height=10, font=("Menlo", 10), wrap="none")
        self.txt.pack(fill="both", expand=True)
        self.journals = []
        self.refresh()

    def refresh(self):
        self.journals = journal.list_journals()
        self.tv.delete(*self.tv.get_children())
        import time
        for d in self.journals:
            self.tv.insert("", "end", values=(
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.get("started", 0))),
                d.get("kind", "?"), len(d.get("entries", [])), d.get("root", "")))

    def append(self, msg):
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")

    def _selected(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo(APP, "Select an entry first.")
            return None
        return self.journals[self.tv.index(sel[0])]

    def undo(self):
        d = self._selected()
        if not d:
            return
        if not messagebox.askyesno(APP, f"Undo '{d['kind']}' with {len(d['entries'])} operations?"):
            return
        f = d["_file"]

        def work(progress, log):
            return journal.revert(f, log=log)

        def done(res):
            ok, fail = res
            messagebox.showinfo(APP, f"Reverted {ok} operations." + (f"\n{fail} failed." if fail else ""))
            try:
                os.remove(f)
            except OSError:
                pass
            self.refresh()
            self.app.scan()

        self.app.task.run(work, done, "Undoing")

    def reveal(self):
        d = self._selected()
        if d:
            paths.reveal(d["_file"])


def main():
    app = Sortero()
    app.mainloop()


if __name__ == "__main__":
    main()

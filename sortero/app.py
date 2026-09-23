"""Sortero - a desktop front end for organising a DJ collection."""
import os, queue, threading, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import (library, organize, journal, playlists, paths, settings, updates, wizard,
               session, updater, review, flatten, processed, importer, ui, licence,
               supporter)
from .screens.base import APP, RESCAN, changes
from .screens.todo import TodoScreen
from .screens.add_music import AddMusicScreen
from .screens.tracks import LibraryScreen
from .screens.playlist_builder import PlaylistsScreen
from .screens.tidy import TidyUpScreen, CleanTagsScreen, DuplicatesScreen, ReorganiseScreen
from .screens.history import HistoryScreen
from .screens.settings_screen import SettingsScreen
from .screens.pro_screen import ProScreen
from .version import __version__

NAV = [("todo", "To do"), ("add", "Add music"), ("library", "Library"),
       ("playlists", "Playlists"), ("tidy", "Tidy up"), ("history", "History")]
SCREENS = {"todo": TodoScreen, "add": AddMusicScreen, "library": LibraryScreen,
           "playlists": PlaylistsScreen, "tidy": TidyUpScreen, "tags": CleanTagsScreen,
           "dupes": DuplicatesScreen, "reorganise": ReorganiseScreen,
           "history": HistoryScreen, "settings": SettingsScreen, "pro": ProScreen}
MOD = "Command" if paths.IS_MAC else "Control"
ACCEL = "Cmd-" if paths.IS_MAC else "Ctrl+"


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
                    self.app.set_status(f"{label}… {i:,} of {total:,}")
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
                    self.app.set_status("Something went wrong")
                    self.app.log(payload)
                    self.app.refresh_todo()
                    messagebox.showerror(APP, payload.strip().splitlines()[-1])
                    return
        except queue.Empty:
            pass
        self.app.after(80, lambda: self._poll(done, label))


# ---------------------------------------------------------------------- app
class Sortero(tk.Tk):
    def __init__(self):
        super().__init__()
        ui.setup(self)
        self.title(APP)
        self.geometry("1120x740")
        self.minsize(980, 640)

        self.root_dir = tk.StringVar(value=settings.get("root") or "")
        self.recs = []
        self.health = None
        self.task = Task(self)
        self.auto_update = tk.BooleanVar(value=bool(settings.get("check_updates_on_launch")))
        self.current = None

        self._build_menu()
        self._build_sidebar()
        self._build_main()
        self.refresh_banner()
        self.show("todo")
        self.after(250, self._first_run)
        self.after(4000, self._maybe_check_licence)

    # -- window chrome -----------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self)

        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="Choose Collection Folder…", command=self.choose)
        filem.add_command(label="Read Collection Again", accelerator=ACCEL + "R",
                          command=self.scan)
        filem.add_separator()
        filem.add_command(label="Sort the Processed Folder…", command=self.open_processed)
        filem.add_command(label="Place a Folder's Tracks by Hand…", command=self.review_folder)
        filem.add_separator()
        filem.add_command(label="Open App Data Folder",
                          command=lambda: paths.reveal(paths.data_dir()))
        menubar.add_cascade(label="File", menu=filem)

        gom = tk.Menu(menubar, tearoff=0)
        for i, (key, label) in enumerate(NAV, 1):
            gom.add_command(label=label, accelerator=f"{ACCEL}{i}",
                            command=lambda k=key: self.show(k))
            self.bind_all(f"<{MOD}-Key-{i}>", lambda e, k=key: self.show(k))
        gom.add_separator()
        gom.add_command(label="Settings", accelerator=ACCEL + ",",
                        command=lambda: self.show("settings"))
        menubar.add_cascade(label="Go", menu=gom)

        netm = tk.Menu(menubar, tearoff=0)
        netm.add_command(label="Turn On Safety Net…", command=self.testing_start)
        netm.add_separator()
        netm.add_command(label="Keep All Changes…", command=self.testing_commit)
        netm.add_command(label="Undo Everything Since It Went On…",
                         command=self.testing_revert)
        netm.add_separator()
        netm.add_command(label="Save Backup As…", command=self.testing_export)
        netm.add_command(label="Load a Backup and Undo It…", command=self.testing_load)
        menubar.add_cascade(label="Safety Net", menu=netm)

        helpm = tk.Menu(menubar, tearoff=0, name="help")
        helpm.add_command(label="Setup Guide…", command=self.run_wizard)
        helpm.add_command(label="Support Sortero…", command=lambda: self.show("pro"))
        helpm.add_separator()
        helpm.add_command(label="Check for Updates…",
                          command=lambda: self.check_updates(quiet=False))
        helpm.add_separator()
        helpm.add_command(label=f"Sortero {__version__}", state="disabled")
        menubar.add_cascade(label="Help", menu=helpm)

        self.config(menu=menubar)
        self.bind_all(f"<{MOD}-r>", lambda e: self.scan())
        self.bind_all(f"<{MOD}-comma>", lambda e: self.show("settings"))
        if paths.IS_MAC:
            self.createcommand("tk::mac::ShowPreferences", lambda: self.show("settings"))

    def _build_sidebar(self):
        side = self.side = tk.Frame(self, width=200, highlightthickness=0, bd=0)
        ui.paint(side, bg="sidebar")
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Label(side, text=APP, font=ui.HEADING, anchor="w")
        ui.paint(brand, bg="sidebar", fg="text")
        brand.pack(fill="x", padx=20, pady=(20, 14))

        self.nav_rows = {}
        for key, label in NAV:
            self._nav_item(key, label)
        filler = tk.Frame(side)
        ui.paint(filler, bg="sidebar")
        filler.pack(fill="both", expand=True)
        self._nav_item("pro", "Support Sortero")
        self._nav_item("settings", "Settings")

        self.coll_lab = tk.Label(side, font=ui.SMALL, anchor="w", justify="left",
                                 wraplength=160)
        ui.paint(self.coll_lab, bg="sidebar", fg="muted")
        self.coll_lab.pack(fill="x", padx=20, pady=(8, 18))
        self.coll_lab.bind("<Button-1>", lambda e: self.show("settings"))
        self.root_dir.trace_add("write", lambda *a: self._show_collection())
        self._show_collection()
        ttk.Separator(self, orient="vertical").pack(side="left", fill="y")

    def _nav_item(self, key, label):
        row = tk.Frame(self.side, padx=12, pady=6)
        text = tk.Label(row, text=label, font=ui.BODY, anchor="w")
        badge = tk.Label(row, text="", font=ui.SMALL)
        text.pack(side="left", fill="x", expand=True)
        badge.pack(side="right")
        row.pack(fill="x", padx=8, pady=1)
        for w in (row, text, badge):
            w.bind("<Button-1>", lambda e, k=key: self.show(k))
        self.nav_rows[key] = (row, text, badge)
        self._paint_nav(key, False)

    def _paint_nav(self, key, on):
        row, text, badge = self.nav_rows[key]
        bg = "select" if on else "sidebar"
        ui.paint(row, bg=bg)
        ui.paint(text, bg=bg, fg="on_select" if on else "text")
        ui.paint(badge, bg=bg, fg="on_select" if on else "muted")

    def _show_collection(self):
        d = self.root_dir.get()
        self.coll_lab.configure(
            text=f"Collection\n{os.path.basename(os.path.normpath(d))}" if d
            else "No collection chosen")

    def _build_main(self):
        main = ttk.Frame(self)
        main.pack(side="left", fill="both", expand=True)

        foot = ttk.Frame(main, padding=(ui.PAD, 6, ui.PAD, 8))
        foot.pack(side="bottom", fill="x")
        ttk.Separator(main, orient="horizontal").pack(side="bottom", fill="x")
        self.status = ttk.Label(foot, text="Ready", style="Muted.TLabel")
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(foot, mode="determinate", length=260)
        self.progress.pack(side="right")

        self.banner = tk.Frame(main, padx=16, pady=8)
        ui.paint(self.banner, bg="banner")
        self.banner_label = tk.Label(self.banner, font=ui.HEADING, anchor="w")
        ui.paint(self.banner_label, bg="banner", fg="on_banner")
        self.banner_label.pack(side="left")
        for text, command, padx in (("Keep changes", self.testing_commit, (8, 0)),
                                    ("Undo everything", self.testing_revert, 0)):
            b = tk.Button(self.banner, text=text, command=command)
            ui.paint(b, highlightbackground="banner")
            b.pack(side="right", padx=padx)

        self.nudge = ttk.Frame(main, padding=(ui.PAD, ui.GAP, ui.PAD, 0))

        self.stack = ttk.Frame(main)
        self.stack.pack(fill="both", expand=True)
        self.screens = {key: cls(self.stack, self, key) for key, cls in SCREENS.items()}

    # -- navigation --------------------------------------------------------
    def show(self, key):
        scr = self.screens[key]
        if self.current is not scr:
            if self.current is not None:
                self.current.pack_forget()
                self._paint_nav(self.current.nav or self.current.key, False)
            scr.pack(fill="both", expand=True)
            self.current = scr
        self._paint_nav(scr.nav or scr.key, True)
        scr.shown()

    def show_library(self, filter_key):
        self.screens["library"].set_filter(filter_key)
        self.show("library")

    def set_badge(self, key, n):
        self.nav_rows[key][2].configure(text=str(n) if n else "")

    def refresh_todo(self):
        self.screens["todo"].refresh()

    def set_status(self, s):
        self.status.configure(text=s)

    def log(self, msg):
        self.screens["history"].append(str(msg))

    # -- collection --------------------------------------------------------
    def choose(self):
        d = filedialog.askdirectory(title="Choose your DJ collection folder")
        if d:
            self.root_dir.set(d)
            settings.set("root", d)
            self.recs, self.health = [], None
            self.scan()

    def require_root(self):
        d = self.root_dir.get()
        if not d or not os.path.isdir(d):
            messagebox.showwarning(APP, "Choose your DJ collection folder first "
                                        "(File → Choose Collection Folder).")
            return None
        return d

    def scan(self, then=None):
        d = self.require_root()
        if not d:
            return
        settings.set("root", d)

        def work(progress, log):
            recs = library.scan(d, progress=progress)
            return recs, library.health(recs)

        def done(res):
            self.recs, self.health = res
            for s in self.screens.values():
                s.invalidate()
            self.log(f"Read {len(self.recs)} files in {d}")
            self.refresh_banner()
            if then:
                then()

        self.task.run(work, done, "Reading your collection")
        if self.task.running and not self.recs:
            self.refresh_todo()

    def changed(self, repair=False):
        """After anything that moved or rewrote files: refresh History, then re-read."""
        self.screens["history"].refresh()
        self.refresh_banner()
        self.refresh_nudge()
        self.scan(then=self.offer_playlist_repair if repair else None)

    # -- shared actions ----------------------------------------------------
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

    def repair_playlists(self):
        """Re-link playlist entries whose file moved or was re-encoded."""
        root = self.require_root()
        if not root:
            return
        if not self.recs:
            messagebox.showinfo(APP, f"Sortero hasn't read your collection yet. Press "
                                     f"{RESCAN} to read it.")
            return
        recs = self.recs

        def work(progress, log):
            return playlists.repair(root, recs, dry=True, log=log)

        def done(res):
            fixed, gone, _ = res
            if not fixed and not gone:
                messagebox.showinfo(APP, "Every playlist entry points at a real file.")
                return
            if not messagebox.askyesno(
                    APP, f"{fixed + gone} playlist entries point at files that aren't "
                         f"there any more.\n\n{fixed} can be re-linked by matching artist "
                         f"and title.\n{gone} can't be matched and will be left as they "
                         "are rather than removed.\n\nRe-link them?"):
                return

            def work2(progress, log):
                return playlists.repair(root, recs, dry=False, log=log)

            def done2(res2):
                f2, g2, _ = res2
                messagebox.showinfo(APP, f"Re-linked {f2} entries."
                                         + (f"\n{g2} still unmatched." if g2 else ""))
            self.task.run(work2, done2, "Repairing playlists")

        self.task.run(work, done, "Checking playlists")

    def open_processed(self):
        """One window that deals with everything in Processed, leaving nothing stranded."""
        root = self.require_root()
        if not root:
            return
        if not os.path.isdir(os.path.join(root, importer.PROCESSED)):
            messagebox.showinfo(APP, f"There's no '{importer.PROCESSED}' folder in your "
                                     "collection.")
            return
        processed.ProcessedDialog(
            self, self, on_close=lambda changed: self.changed(repair=True)
            if changed else self.refresh_todo())

    def open_flatten(self):
        if self.require_root():
            flatten.FlattenDialog(self, self,
                                  on_close=lambda changed: self.changed() if changed else None)

    def review_folder(self):
        """Pick a folder in the collection and place each of its tracks by hand."""
        root = self.require_root()
        if not root:
            return
        rootn = os.path.normpath(root)
        d = filedialog.askdirectory(title="Choose a folder to go through track by track",
                                    initialdir=rootn)
        if not d:
            return
        # The picker can hand back ~/Dropbox/... while the collection is stored as
        # ~/Library/CloudStorage/Dropbox/... (or the reverse). Compare real paths,
        # then express the choice in the collection's own form, so every path in
        # the review stays consistent with the rest of the library.
        real_root, real_d = os.path.realpath(rootn), os.path.realpath(d)
        if real_d != real_root and real_d.startswith(real_root + os.sep):
            d = os.path.join(rootn, os.path.relpath(real_d, real_root))
        else:
            d = os.path.normpath(d)
        if d == rootn or not d.startswith(rootn + os.sep):
            messagebox.showinfo(
                APP, "Pick a folder inside your collection.\n\nFor music from somewhere "
                     "else, use Add music: it holds back anything it can't place, and "
                     "you can go through those one by one.")
            return
        rel = os.path.relpath(d, rootn)
        top = rel.split(os.sep)[0]
        if top in (organize.QUARANTINE, organize.PLAYLIST_DIR):
            messagebox.showinfo(APP, f"'{top}' isn't music waiting to be filed.")
            return

        def work(progress, log):
            recs = library.scan(d, progress=progress)
            for r in recs:                        # scanned from the folder; re-anchor
                r.rel = os.path.relpath(r.path, rootn)
                r.protected = library.is_protected(r.rel)
            return recs

        def done(recs):
            long_ = {id(r) for r in recs
                     if r.is_recording or (r.duration and r.duration >= organize.MIX_MIN_SECONDS)}
            tracks = [r for r in recs if id(r) not in long_]
            nested = [r for r in tracks if os.path.dirname(r.path) != d]
            if nested:
                ans = messagebox.askyesnocancel(
                    APP, f"'{rel}' has {len(tracks) - len(nested)} tracks of its own and "
                         f"{len(nested)} more in subfolders.\n\nInclude the subfolders too?")
                if ans is None:
                    return
                if not ans:
                    tracks = [r for r in tracks if os.path.dirname(r.path) == d]
            if not tracks:
                messagebox.showinfo(APP, f"There are no tracks to place in '{rel}'."
                                    + (" Only set recordings, which are left alone." if long_ else ""))
                return
            name = organize.safe(rel.replace(os.sep, " - "), 100)
            exists = os.path.exists(os.path.join(playlists.playlist_dir(rootn), name + ".m3u8"))
            ans = messagebox.askyesnocancel(
                APP, f"{len(tracks)} tracks in '{rel}'"
                     + (f" ({len(long_)} set recordings left out)" if long_ else "") + ".\n\n"
                     f"Save the folder as the playlist '{name}' first? As you file tracks "
                     "into genre folders the playlist follows them, so the set stays "
                     "together even once the folder is empty."
                     + ("\n\nA playlist with that name already exists and will be "
                        "replaced (undoable from History)." if exists else "")
                     + "\n\nYes saves it · No goes straight in · Cancel stops")
            if ans is None:
                return
            if ans:
                self._save_folder_playlist(rootn, name, tracks)
            review.ReviewDialog(
                self, self, tracks, exclude_folder=rel, context=rel,
                on_close=lambda changed: self.changed(repair=True) if changed else None)

        self.task.run(work, done, f"Reading '{rel}'")

    def _save_folder_playlist(self, root, name, recs):
        """Write the folder's current order out as a playlist, undoably."""
        fp = os.path.join(playlists.playlist_dir(root), organize.safe(name, 100) + ".m3u8")
        j = journal.Journal("save-playlist", root)
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as fh:
                j.wrote(fp, fh.read())
        else:
            j.created(fp)
        playlists.write(root, name, [r.path for r in recs])
        j.save()
        self.screens["history"].refresh()
        self.log(f"saved playlist {fp} ({len(recs)} tracks)")

    # -- safety net --------------------------------------------------------
    def refresh_banner(self):
        sess = session.active()
        if not sess:
            self.banner.pack_forget()
        else:
            s = session.summary(sess)
            self.banner_label.configure(
                text=f"Safety net on: {changes(s)} recorded. "
                     "Nothing is final until you keep it.")
            self.banner.pack(fill="x", before=self.stack)
        self.screens["history"].refresh_net()

    def refresh_nudge(self):
        """Every hundred tracks sorted without a licence, a card asking for support."""
        for w in self.nudge.winfo_children():
            w.destroy()
        n = supporter.due()
        if not n:
            self.nudge.pack_forget()
            return

        def close(then=None):
            supporter.dismiss()
            self.nudge.pack_forget()
            if then:
                then()

        ui.Card(self.nudge, f"You've sorted {n:,} tracks with Sortero",
                "Sortero is free and open source. If it's saving you time, a $15 "
                "Supporter licence helps keep it improving.",
                "Support Sortero…", lambda: close(lambda: self.show("pro")),
                link_text="Not now", link_command=close).pack(fill="x")
        self.nudge.pack(fill="x", before=self.stack)

    def testing_start(self):
        d = self.require_root()
        if not d:
            return
        if session.active():
            messagebox.showinfo(APP, "The safety net is already on.")
            return
        if not messagebox.askyesno(
                APP, "Turn on the safety net?\n\n"
                     "Everything you do from now on is recorded into one restore point, "
                     "saved as you go to a .bak backup file. When you're done, keep it "
                     "all or undo it in one go."):
            return
        sess = session.start(d)
        session.export(sess)
        self.refresh_banner()
        self.log(f"safety net on: {sess['id']}")
        messagebox.showinfo(APP, "The safety net is on.\n\nBackup file: "
                                 f"{session.default_bak_path(sess)}")

    def testing_commit(self):
        sess = session.active()
        if not sess:
            messagebox.showinfo(APP, "The safety net isn't on.")
            return
        s = session.summary(sess)
        if not messagebox.askyesno(
                APP, f"Keep all {changes(s)} "
                     f"({s['moves']} moves, {s['tags']} tag edits)?\n\n"
                     "The safety net turns off and its backup file is deleted. Each "
                     "operation stays in History and can still be undone on its own."):
            return
        session.commit(sess, log=self.log)
        self.refresh_banner()
        self.screens["history"].refresh()
        messagebox.showinfo(APP, "Changes kept. The safety net is off.")

    def testing_revert(self):
        sess = session.active()
        if not sess:
            messagebox.showinfo(APP, "The safety net isn't on.")
            return
        s = session.summary(sess)
        if not messagebox.askyesno(
                APP, "Undo everything since the safety net went on?\n\n"
                     f"{s['moves']} moves and {s['tags']} tag edits will be reversed, "
                     "putting your collection back the way it was."):
            return

        def work(progress, log):
            return session.revert_active(log=log)

        def done(res):
            ok, fail = res
            messagebox.showinfo(APP, f"Reversed {ui.plural(ok, 'change')}."
                                     + (f"\n{fail} couldn't be reversed." if fail else ""))
            self.changed()

        self.task.run(work, done, "Undoing everything")

    def testing_export(self):
        sess = session.active()
        if not sess:
            messagebox.showinfo(APP, "The safety net isn't on, so there's no backup to save.")
            return
        p = filedialog.asksaveasfilename(
            title="Save safety net backup", defaultextension=".bak",
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
                     f"{d['operations']} changes: {d['moves']} moves, "
                     f"{d['tags']} tag edits.\n\n"
                     "Files are moved back and tags restored to their old values."):
            return

        def work(progress, log):
            return session.revert_backup(data, log=log)

        def done(res):
            ok, fail = res
            messagebox.showinfo(APP, f"Reversed {ui.plural(ok, 'change')}."
                                     + (f"\n{fail} couldn't be reversed." if fail else ""))
            self.changed()

        self.task.run(work, done, "Restoring from backup")

    # -- first run / updates -----------------------------------------------
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

    def _maybe_check_licence(self):
        """Confirm a subscription every few days, quietly, off the UI thread."""
        if not licence.due():
            return
        box = {}

        def work():
            try:
                licence.refresh()
            except Exception as e:
                box["error"] = str(e)
            box["done"] = True

        def poll():
            if "done" not in box:
                self.after(500, poll)
                return
            if "error" in box:
                self.log(f"licence check: {box['error']}")
            self.screens["pro"].render()

        threading.Thread(target=work, daemon=True).start()
        self.after(500, poll)

    def _maybe_auto_update(self):
        if settings.get("check_updates_on_launch") and updates.due():
            self.after(2500, lambda: self.check_updates(quiet=True))

    def _offer_install(self, res):
        """Found a newer release. A supporter's copy downloads it, swaps it in and relaunches."""
        import webbrowser
        if not updater.running_frozen():
            if messagebox.askyesno(APP, res["message"] + "\n\nThis copy runs from source, "
                                        "so update it from GitHub. Open the project's "
                                        "releases?"):
                webbrowser.open(res.get("url") or updates.RELEASES_URL)
            return
        if not licence.status().pro:
            if messagebox.askyesno(APP, res["message"] + "\n\nOne-click updates come with "
                                        "a Supporter licence. Find out more?"):
                self.show("pro")
            return
        if not messagebox.askyesno(
                APP, res["message"] + "\n\nDownload it, install it and restart "
                     "Sortero now?\n\nAnything unsaved is finished first — this "
                     "only quits once the new version is ready."):
            return

        def work(progress, log):
            try:
                build = licence.latest_build()
            except licence.LicenceError as e:
                return ("error", str(e))
            log(f"downloading {build.get('name') or 'the update'}…")
            new = updater.prepare(build, progress=progress)
            log(f"unpacked to {new}")
            return ("ok", new)

        def done(out):
            if out[0] == "error":
                messagebox.showwarning(APP, out[1])
                return
            try:
                updater.install(out[1])
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
            if state == "update" and quiet and updater.running_frozen() \
                    and not licence.status().pro:
                # no nagging on launch: without a licence there's nothing to install
                self.log(f"update check: {res['message']}")
            elif state == "update":
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


def main():
    app = Sortero()
    app.mainloop()


if __name__ == "__main__":
    main()

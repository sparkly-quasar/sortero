"""Settings: the collection folder, updates, and the online services Sortero uses."""
import os, shutil
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, auth, settings, paths, session, updates
from ..common import human_size
from ..organize import QUARANTINE
from ..version import __version__
from .base import Screen, APP


class SettingsScreen(Screen):
    title = "Settings"
    summary = "Where your collection lives, updates, and the online services Sortero can use."

    def build(self):
        self.bar.pack_forget()
        app = self.app

        self._section("Collection folder", first=True)
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Button(row, text="Change…", command=app.choose).pack(side="right")
        ttk.Button(row, text="Show in Finder" if paths.IS_MAC else "Open folder",
                   command=self._reveal_root).pack(side="right", padx=(0, ui.GAP))
        ttk.Label(row, textvariable=app.root_dir).pack(side="left", fill="x", expand=True)

        # Quarantine lives inside the collection, so it sits here rather than
        # under its own heading: Settings doesn't scroll, and the window's
        # default height has no room for another section.
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(ui.GAP, 0))
        self.q_label = ttk.Label(row, style="Muted.TLabel")
        self.q_label.pack(side="left")
        self.q_empty = ttk.Button(row, text="Empty quarantine…", command=self.empty_quarantine)
        self.q_empty.pack(side="right")
        self.q_show = ttk.Button(row, text="Show quarantine",
                                 command=lambda: paths.reveal(self._quarantine()))
        self.q_show.pack(side="right", padx=(0, ui.GAP))

        self._section("Updates")
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Checkbutton(row, text="Check for updates when Sortero opens",
                        variable=app.auto_update,
                        command=lambda: settings.set("check_updates_on_launch",
                                                     app.auto_update.get())
                        ).pack(side="left")
        # Beside the other box rather than on a row of its own: see the note above.
        self.beta = tk.BooleanVar(value=bool(settings.get("updates_beta")))
        ttk.Checkbutton(row, text="Offer beta builds", variable=self.beta,
                        command=self._beta_changed).pack(side="left", padx=(ui.SECTION, 0))
        ttk.Button(row, text="Check now",
                   command=lambda: app.check_updates(quiet=False)).pack(side="right")
        ttk.Label(self, text=f"You're running Sortero {__version__}.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        self._section("Discogs")
        ui.WrapLabel(self, style="Muted.TLabel",
                     text="Discogs suggests genres in Library. It works without a token, "
                          "but a free personal token from discogs.com/settings/developer "
                          "makes lookups about 2.5× faster. Only artist and title are "
                          "ever sent.").pack(fill="x")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(ui.GAP, 0))
        ttk.Label(row, text="Token").pack(side="left")
        self.token_var = tk.StringVar(value=settings.get("discogs_token") or "")
        ttk.Entry(row, textvariable=self.token_var, width=40, show="•").pack(
            side="left", padx=(ui.GAP, 0))
        ttk.Button(row, text="Save", command=self._save_token).pack(side="left",
                                                                    padx=(ui.GAP, 0))
        self.token_status = ttk.Label(row, style="Muted.TLabel")
        self.token_status.pack(side="left", padx=(12, 0))

        self._section("Spotify and TIDAL")
        ui.WrapLabel(self, style="Muted.TLabel",
                     text="Connect an account to read whole playlists of any length. "
                          "One-time setup: create a free app at "
                          "developer.spotify.com/dashboard or developer.tidal.com, add "
                          f"{auth.REDIRECT_URI} as its redirect URI, and paste its Client "
                          "ID below. You sign in on their website, so Sortero never sees "
                          "your password. Tokens are kept in your system keychain."
                     ).pack(fill="x")
        grid = ttk.Frame(self)
        grid.pack(fill="x", pady=(ui.GAP, 0))
        ttk.Label(grid, text="Client ID", style="Small.TLabel").grid(row=0, column=1,
                                                                     sticky="w", padx=ui.GAP)
        self.client_ids, self.conn_labels = {}, {}
        for i, pid in enumerate(("spotify", "tidal"), start=1):
            cfg = auth.PROVIDERS[pid]
            ttk.Label(grid, text=cfg["label"], width=8).grid(row=i, column=0, sticky="w",
                                                             pady=3)
            v = tk.StringVar()
            self.client_ids[pid] = v
            ttk.Entry(grid, textvariable=v, width=34).grid(row=i, column=1, padx=ui.GAP)
            ttk.Button(grid, text="Connect…",
                       command=lambda p=pid: self.connect(p)).grid(row=i, column=2)
            ttk.Button(grid, text="Disconnect",
                       command=lambda p=pid: self.disconnect(p)).grid(row=i, column=3,
                                                                      padx=(ui.GAP, 0))
            lab = ttk.Label(grid, style="Muted.TLabel")
            lab.grid(row=i, column=4, sticky="w", padx=(12, 0))
            self.conn_labels[pid] = lab

        self._section("Setup")
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Button(row, text="Run the setup guide again…",
                   command=app.run_wizard).pack(side="left")
        ttk.Button(row, text="Open Sortero's data folder",
                   command=lambda: paths.reveal(paths.data_dir())).pack(side="left",
                                                                        padx=(ui.GAP, 0))
        self._refresh_conn()
        self._refresh_quarantine()

    def _beta_changed(self):
        on = self.beta.get()
        if on and not messagebox.askyesno(
                APP, "Beta builds have new features before they're released, for people "
                     "happy to try them and say what goes wrong. They can have bugs.\n\n"
                     "Sortero will offer each new beta as an update, and the final "
                     "release when it's out. Before a gig, bring a stick you've checked "
                     "with a release build too.\n\nOffer beta builds?", parent=self):
            self.beta.set(False)
            return
        settings.set("updates_beta", on)
        if not on and updates.is_prerelease(__version__):
            messagebox.showinfo(APP, f"You're on a beta ({__version__}). Sortero will "
                                     "offer the next release once it's newer than this "
                                     "beta.", parent=self)

    def _section(self, title, first=False):
        ttk.Label(self, text=title, style="Heading.TLabel").pack(
            anchor="w", pady=(0 if first else ui.SECTION + 4, 4))

    def shown(self):
        self._refresh_conn()
        self._refresh_quarantine()

    # -- quarantine --------------------------------------------------------
    def _quarantine(self):
        root = self.app.root_dir.get()
        return os.path.join(root, QUARANTINE) if root else None

    def _quarantine_size(self):
        """(files, bytes) in the quarantine folder; (0, 0) when there is none."""
        files = size = 0
        q = self._quarantine()
        if not q or not os.path.isdir(q):
            return 0, 0
        for dirpath, _, filenames in os.walk(q):
            for fn in filenames:
                if fn == ".DS_Store":
                    continue
                files += 1
                try:
                    size += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
        return files, size

    def _refresh_quarantine(self):
        files, size = self._quarantine_size()
        self.q_label.configure(
            text=f"{ui.plural(files, 'file')} ({human_size(size)}) set aside in {QUARANTINE}, "
                 "kept so History can undo." if files
            else f"{QUARANTINE} is empty. Sortero sets duplicates and replaced copies "
                 "aside there instead of deleting them.")
        state = "normal" if files else "disabled"
        self.q_empty.configure(state=state)
        self.q_show.configure(state=state)

    def empty_quarantine(self):
        q = self._quarantine()
        files, size = self._quarantine_size()
        if not files:
            self._refresh_quarantine()
            return
        if session.active():
            messagebox.showinfo(APP, "The setup's restore point is still open, and it may "
                                     f"need files in {QUARANTINE} to undo.\n\nKeep or "
                                     "undo the setup in History first, then empty it.")
            return
        if not messagebox.askyesno(
                APP, f"Permanently delete {ui.plural(files, 'file')} ({human_size(size)}) "
                     f"in {QUARANTINE}?\n\nThis can't be undone. Steps in History that "
                     "set these files aside won't be able to bring them back.",
                icon="warning", default="no"):
            return

        def work(progress, log):
            shutil.rmtree(q, ignore_errors=True)
            left = os.path.isdir(q)
            log(f"emptied {QUARANTINE}: {files} files, {human_size(size)}")
            return left

        def done(left):
            self._refresh_quarantine()
            if left:
                messagebox.showwarning(APP, f"Some files in {QUARANTINE} couldn't be "
                                            "deleted. They may be open in another app.")
            self.app.changed()

        self.app.task.run(work, done, "Emptying quarantine")

    def _reveal_root(self):
        d = self.app.require_root()
        if d:
            paths.reveal(d)

    def _save_token(self):
        tok = self.token_var.get().strip()
        settings.set("discogs_token", tok)
        self.token_status.configure(text="Saved. Lookups use the faster rate." if tok
                                    else "Cleared. Lookups use the slower rate.")

    def _refresh_conn(self):
        for pid, lab in self.conn_labels.items():
            tok = auth.load_tokens(pid)
            lab.configure(text="Connected" if tok else "Not connected",
                          style="Good.TLabel" if tok else "Muted.TLabel")
            if tok and tok.get("client_id") and not self.client_ids[pid].get():
                self.client_ids[pid].set(tok["client_id"])

    def connect(self, pid):
        cid = self.client_ids[pid].get().strip()
        label = auth.PROVIDERS[pid]["label"]
        if not cid:
            messagebox.showinfo(APP, f"Paste your {label} app's Client ID first.")
            return

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

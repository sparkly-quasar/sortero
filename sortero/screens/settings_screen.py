"""Settings: the collection folder, updates, and the online services Sortero uses."""
import os
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, auth, settings, paths
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

        self._section("Updates")
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Checkbutton(row, text="Check for updates when Sortero opens",
                        variable=app.auto_update,
                        command=lambda: settings.set("check_updates_on_launch",
                                                     app.auto_update.get())
                        ).pack(side="left")
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

    def _section(self, title, first=False):
        ttk.Label(self, text=title, style="Heading.TLabel").pack(
            anchor="w", pady=(0 if first else ui.SECTION + 4, 4))

    def shown(self):
        self._refresh_conn()

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

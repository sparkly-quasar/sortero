"""First-run setup wizard.

Runs once, then never again unless asked for from the Help menu. It gets the
user to a scanned library and explains the one thing that isn't obvious: the
loop between 'To Be Processed', Platinum Notes / Mixed In Key, and 'Processed'.
"""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import settings, library, auth
from .common import human_size

TITLE = "Welcome to Sortero"


class Wizard(tk.Toplevel):
    def __init__(self, app, on_finish=None):
        super().__init__(app)
        self.app = app
        self.on_finish = on_finish
        self.title(TITLE)
        self.geometry("720x520")
        self.minsize(660, 480)
        self.transient(app)
        self.resizable(True, True)

        self.root_dir = tk.StringVar(value=settings.get("root") or "")
        self.scan_summary = tk.StringVar(value="")
        self.recs = None
        self.step = 0

        self.body = ttk.Frame(self, padding=20)
        self.body.pack(fill="both", expand=True)

        nav = ttk.Frame(self, padding=(20, 0, 20, 16))
        nav.pack(fill="x")
        self.skip_btn = ttk.Button(nav, text="Skip setup", command=self.finish)
        self.skip_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next", command=self.next)
        self.next_btn.pack(side="right")
        self.back_btn = ttk.Button(nav, text="Back", command=self.back)
        self.back_btn.pack(side="right", padx=6)

        self.steps = [self._welcome, self._folder, self._scan, self._workflow, self._accounts]
        self.render()
        self.protocol("WM_DELETE_WINDOW", self.finish)
        self.grab_set()

    # -- chrome ------------------------------------------------------------
    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _heading(self, text, sub=None):
        ttk.Label(self.body, text=text, font=("Helvetica", 18, "bold")).pack(anchor="w")
        if sub:
            ttk.Label(self.body, text=sub, foreground="#666", wraplength=650,
                      justify="left").pack(anchor="w", pady=(6, 14))

    def render(self):
        self._clear()
        self.steps[self.step]()
        self.back_btn.configure(state="normal" if self.step else "disabled")
        last = self.step == len(self.steps) - 1
        self.next_btn.configure(text="Finish" if last else "Next")

    def back(self):
        if self.step:
            self.step -= 1
            self.render()

    def next(self):
        if self.step == 1 and not self._valid_folder():
            messagebox.showwarning(TITLE, "Choose the folder your music lives in first.")
            return
        if self.step == len(self.steps) - 1:
            self.finish()
            return
        self.step += 1
        self.render()
        if self.step == 2:
            self.after(150, self.do_scan)

    def finish(self):
        settings.set("setup_complete", True)
        if self._valid_folder():
            settings.set("root", self.root_dir.get())
        self.grab_release()
        self.destroy()
        if self.on_finish:
            self.on_finish(self.root_dir.get() if self._valid_folder() else None)

    # -- steps -------------------------------------------------------------
    def _welcome(self):
        self._heading("Welcome to Sortero",
                      "Sortero tidies a DJ collection into one copy of every track, "
                      "filed by genre, with your existing folders preserved as "
                      "playlists you can import into rekordbox or Mixxx.")
        for line in [
            "One file per track — no more copies scattered across folders.",
            "Your set and vibe folders become .m3u8 playlists pointing at that file.",
            "Key, BPM and energy get written where DJ software can sort on them.",
            "Nothing is ever deleted, and every action can be undone.",
        ]:
            ttk.Label(self.body, text="•  " + line, wraplength=640,
                      justify="left").pack(anchor="w", pady=3)
        ttk.Label(self.body, foreground="#666", wraplength=640, justify="left",
                  text="\nThis takes about a minute. You can skip it and set things "
                       "up yourself, and reopen it later from Help → Setup Wizard."
                  ).pack(anchor="w", pady=(10, 0))

    def _valid_folder(self):
        d = self.root_dir.get()
        return bool(d) and os.path.isdir(d)

    def _folder(self):
        self._heading("Where is your music?",
                      "Point Sortero at the folder holding your DJ collection. "
                      "It reads everything inside, but changes nothing until you "
                      "ask it to.")
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(0, 10))
        ttk.Entry(row, textvariable=self.root_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose…", command=self.choose).pack(side="left", padx=6)
        self.folder_note = ttk.Label(self.body, foreground="#666", wraplength=640,
                                     justify="left", text="")
        self.folder_note.pack(anchor="w")
        self._folder_note()

    def _folder_note(self):
        if not hasattr(self, "folder_note") or not self.folder_note.winfo_exists():
            return
        if not self._valid_folder():
            self.folder_note.configure(text="No folder chosen yet.")
            return
        d = self.root_dir.get()
        have = [n for n in ("To Be Processed", "Processed") if os.path.isdir(os.path.join(d, n))]
        msg = f"Found: {d}"
        if have:
            msg += ("\n\nSortero can see your " + " and ".join(f"'{h}'" for h in have) +
                    " folder. Those are treated as your Platinum Notes / Mixed In Key "
                    "staging area and are never reorganised.")
        else:
            msg += ("\n\nSortero will create 'To Be Processed' and 'Processed' folders "
                    "for the analysis workflow when it first needs them.")
        self.folder_note.configure(text=msg)

    def choose(self):
        d = filedialog.askdirectory(title="Choose your DJ collection folder", parent=self)
        if d:
            self.root_dir.set(d)
            self._folder_note()

    def _scan(self):
        self._heading("Reading your collection",
                      "Sortero only reads tags here — nothing is moved or changed.")
        self.progress = ttk.Progressbar(self.body, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 12))
        self.progress.start(12)
        ttk.Label(self.body, textvariable=self.scan_summary, justify="left",
                  wraplength=640, font=("Menlo", 11)).pack(anchor="w")

    def do_scan(self):
        import threading

        d = self.root_dir.get()
        result = {}

        def work():
            try:
                recs = library.scan(d)
                result["recs"], result["health"] = recs, library.health(recs)
            except Exception as e:
                result["error"] = str(e)

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                self.after(200, poll)
                return
            if not self.progress.winfo_exists():
                return
            self.progress.stop()
            self.progress.pack_forget()
            if "error" in result:
                self.scan_summary.set(f"Couldn't read that folder:\n{result['error']}")
                return
            self.recs = result["recs"]
            h = result["health"]
            self.scan_summary.set(
                f"{h['total']:,} tracks · {human_size(h['bytes'])}\n\n"
                f"{h['pct_analyzed']:.0f}%  have key and BPM\n"
                f"{h['pct_genre']:.0f}%  have a usable genre\n"
                f"{h['pct_energy']:.0f}%  have an energy rating\n\n"
                f"{len(h['needs_analysis'])} tracks need Platinum Notes / Mixed In Key.\n"
                f"{len(h['no_genre'])} need a genre — Sortero can infer most of those.")

        self.after(200, poll)

    def _workflow(self):
        self._heading("How the analysis loop works",
                      "Sortero works out genre, artist and title on its own. Key, BPM "
                      "and energy need Mixed In Key and Platinum Notes, so it hands "
                      "tracks to them and picks them back up afterwards.")
        for n, line in enumerate([
            "Needs Work — filter by what's missing and select the tracks.",
            "Stage them: they move into 'To Be Processed'.",
            "Run that folder through Platinum Notes and Mixed In Key, saving to 'Processed'.",
            "Import → \"Sort the 'Processed' folder\" files them by genre.",
        ], 1):
            ttk.Label(self.body, text=f"{n}.  {line}", wraplength=640,
                      justify="left").pack(anchor="w", pady=3)
        ttk.Label(self.body, foreground="#666", wraplength=640, justify="left",
                  text="\nIf a staged track came out of a set or vibe folder, Sortero "
                       "remembers and puts it back into those playlists when it "
                       "returns — even if the file comes back in a different format."
                  ).pack(anchor="w", pady=(10, 0))

    def _accounts(self):
        self._heading("Streaming playlists (optional)",
                      "If you curate playlists in Spotify or TIDAL, Sortero can rebuild "
                      "them against the files you already own and write an .m3u8 for "
                      "rekordbox or Mixxx.")
        for pid in ("tidal", "spotify"):
            cfg = auth.PROVIDERS[pid]
            state = "connected" if auth.is_connected(pid) else "not connected"
            ttk.Label(self.body, text=f"•  {cfg['label']}: {state}").pack(anchor="w", pady=2)
        ttk.Label(self.body, foreground="#666", wraplength=640, justify="left",
                  text="\nYou can connect these any time from the Playlists tab — it's "
                       "not needed to start. Pasting a tracklist as 'Artist - Title' "
                       "lines always works without an account.\n\n"
                       "That's everything. Click Finish to open your library."
                  ).pack(anchor="w", pady=(8, 0))


def maybe_run(app, on_finish=None):
    """Show the wizard only if setup hasn't been completed."""
    if settings.get("setup_complete"):
        return None
    return Wizard(app, on_finish=on_finish)

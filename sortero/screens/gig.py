"""Gig prep: a stick that plays on any Pioneer player, checked before you leave."""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .. import ui, paths, playlists, settings
from ..gig import checklist, convert, ffmpeg, profiles, verify
from ..gig.checklist import DONE, NEXT, WAITING, SOON
from .base import Screen, APP

TARGETS = [(convert.MP3, "MP3 320 kbps (smaller)"), (convert.AIFF, "AIFF 16-bit (lossless)")]
TONES = {DONE: "good", NEXT: None, WAITING: "faint", SOON: "faint"}


def _profile_label(p):
    return "Any Pioneer player (safest)" if p.key == profiles.UNIVERSAL.key else p.name


class GigScreen(Screen):
    title = "Gig prep"
    summary = "Get a USB stick that plays on any Pioneer player, and check it before you leave."
    details = ("Tick the playlists you're playing. Sortero checks every track against what "
               "Pioneer's own manuals say the players read, and makes a gig-safe copy of "
               "any track that won't play everywhere, in a folder next to your collection. "
               "Your originals are never changed.\n\n"
               "Then plug in the stick and check it: the file system, the rekordbox export "
               "if there is one, every format, and every track decoded from start to "
               "finish. It ends with GIG READY or NOT READY, and what to fix.")

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, ui.SECTION))
        ttk.Label(top, text="Players").pack(side="left", padx=(0, ui.GAP))
        self.profile_labels = {_profile_label(p): p.key for p in profiles.PROFILES.values()}
        self.profile_var = tk.StringVar(
            value=_profile_label(profiles.get(settings.get("gig_profile"))))
        box = ttk.Combobox(top, textvariable=self.profile_var, state="readonly", width=26,
                           values=list(self.profile_labels))
        box.pack(side="left", padx=(0, ui.SECTION))
        box.bind("<<ComboboxSelected>>", lambda e: self._options_changed())
        ttk.Label(top, text="Copies").pack(side="left", padx=(0, ui.GAP))
        self.target_labels = dict((label, key) for key, label in TARGETS)
        current = settings.get("gig_target")
        self.target_var = tk.StringVar(
            value=next((l for k, l in TARGETS if k == current), TARGETS[0][1]))
        box = ttk.Combobox(top, textvariable=self.target_var, state="readonly", width=24,
                           values=list(self.target_labels))
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e: self._options_changed())

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=320)
        left.pack(side="left", fill="y", padx=(0, ui.SECTION))
        left.pack_propagate(False)
        ttk.Label(left, text="Playlists for this gig", style="Heading.TLabel").pack(anchor="w")
        self.pick_note = ui.WrapLabel(left, style="Muted.TLabel",
                                      text="Click to tick. The numbers are your set order.")
        self.pick_note.pack(fill="x", pady=(2, 6))
        f, self.tv = ui.tree(left, [("n", "#", 36), ("name", "Playlist", 210),
                                    ("count", "Tracks", 60)], height=10, selectmode="none")
        f.pack(fill="both", expand=True)
        self.tv.tag_configure("chosen", font=ui.HEADING)
        self.tv.bind("<Button-1>", self._click)
        self.tv.bind("<space>", lambda e: self._toggle(self.tv.focus()))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        self.steps_frame = ttk.Frame(right)
        self.steps_frame.pack(fill="x")
        self.verdict = ttk.Label(right, style="Heading.TLabel")
        self.verdict.pack(anchor="w", pady=(ui.GAP, 2))
        box = ttk.Frame(right)
        box.pack(fill="both", expand=True)
        self.report = tk.Text(box, height=8, font=ui.MONO, wrap="word", relief="flat",
                              highlightthickness=1)
        ui.paint(self.report, bg="card", fg="text", highlightbackground="border")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.report.yview)
        self.report.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")             # first, so it always gets its width
        self.report.pack(side="left", fill="both", expand=True)
        self.report.configure(state="disabled")

        self.bar.set_more([("Choose where copies go…", self.choose_library),
                           ("Show the copies folder", self.show_library),
                           None,
                           ("Quick stick check (skip the decode test)",
                            lambda: self.check_stick(decode=False))])
        self.chosen = [p for p in settings.get("gig_playlists") or []]
        self.counts = {}
        self.state = checklist.State()
        self.loaded_root = None

    # ---------------------------------------------------------- playlists
    def shown(self):
        root = self.app.root_dir.get()
        if root != self.loaded_root:
            self.loaded_root = root
            self._load_playlists()
        self.refresh()

    def invalidate(self):
        self.loaded_root = None     # playlists may have changed; reload when next shown

    def _load_playlists(self):
        root = self.app.root_dir.get()
        names = playlists.existing(root) if root and os.path.isdir(root) else []
        self.counts = {}
        for n in names:
            try:
                self.counts[n] = len(playlists.entries(root, n))
            except OSError:
                self.counts[n] = 0
        self.chosen = [n for n in self.chosen if n in self.counts]
        if not names:
            self.pick_note.configure(text="No playlists yet. Make them on the Playlists "
                                          "screen, or Tidy up → Reorganise turns your "
                                          "folders into playlists.")
        self._fill_tree()

    def _fill_tree(self):
        self.tv.delete(*self.tv.get_children())
        for n in self.counts:
            pos = self.chosen.index(n) + 1 if n in self.chosen else ""
            self.tv.insert("", "end", iid=n, values=(pos, n, self.counts[n]),
                           tags=("chosen",) if pos else ())

    def _click(self, e):
        row = self.tv.identify_row(e.y)
        if row:
            self.tv.focus(row)
            self._toggle(row)
        return "break"

    def _toggle(self, name):
        if not name:
            return
        if name in self.chosen:
            self.chosen.remove(name)
        else:
            self.chosen.append(name)
        settings.set("gig_playlists", self.chosen)
        self._fill_tree()
        self._reset()

    # ------------------------------------------------------------ options
    @property
    def profile(self):
        return profiles.get(self.profile_labels.get(self.profile_var.get()))

    @property
    def target(self):
        return self.target_labels.get(self.target_var.get(), convert.MP3)

    def _options_changed(self):
        settings.set("gig_profile", self.profile.key)
        settings.set("gig_target", self.target)
        self._reset()

    def _reset(self):
        """Anything chosen differently means the tracks need checking again."""
        self.state.items, self.state.copied = None, False
        self.refresh()

    # ------------------------------------------------------------ drawing
    def refresh(self):
        st = self.state
        st.playlists = list(self.chosen)
        st.track_count = sum(self.counts.get(n, 0) for n in self.chosen)
        all_steps = checklist.steps(st)
        for w in self.steps_frame.winfo_children():
            w.destroy()
        actions = {"check": self.check_tracks, "copies": self.make_copies,
                   "verify": self.check_stick}
        for i, s in enumerate(all_steps, 1):
            ui.Card(self.steps_frame, f"{checklist.MARKS[s.status]}  {i}. {s.title}", s.text,
                    s.button, actions.get(s.key), tone=TONES[s.status]
                    ).pack(fill="x", pady=(0, 4))
        nxt = checklist.next_step(all_steps)
        if nxt:
            self.bar.set_primary(nxt.button, actions[nxt.key])
        elif not self.chosen:
            self.bar.set_primary("Check tracks", None, state="disabled")
        else:
            self.bar.set_primary("Check a stick…", self.check_stick)
        done = sum(1 for s in all_steps if s.status == DONE)
        self.header.set_summary(f"{done} of {len(all_steps) - 1} steps done." if done
                                else self.summary)

    def _show(self, lines, verdict=None, ready=None):
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", "\n".join(lines))
        self.report.configure(state="disabled")
        self.verdict.configure(text=verdict or "",
                               style=("Good.TLabel" if ready else "Bad.TLabel")
                               if verdict else "Heading.TLabel")
        if verdict:
            self.verdict.configure(font=ui.TITLE)

    def _need_ffmpeg(self, e):
        messagebox.showwarning(APP, str(e))

    # ------------------------------------------------------------- actions
    def check_tracks(self):
        root = self.app.require_root()
        if not root or not self.chosen:
            return
        try:
            lib = convert.library_dir(root, settings.get("gig_library"))
            ffmpeg.require("ffprobe")
        except (convert.LibraryError, ffmpeg.Missing) as e:
            messagebox.showwarning(APP, str(e))
            return
        names, profile, target = list(self.chosen), self.profile, self.target

        def work(progress, log):
            sources = convert.playlist_paths(root, names)
            return convert.plan(root, sources, lib, profile, target, progress)

        def done(items):
            self.state.items, self.state.copied = items, False
            self.state.report = None
            lines = [checklist.check_text(items), ""]
            for it in items:
                if it.status == convert.CONVERT:
                    lines.append(f"Needs a copy  {it.rel}: {it.why[0] if it.why else ''}")
                elif it.status == convert.FAILED:
                    lines.append(f"Can't use     {it.rel}: {' '.join(it.why)}")
            self._show(lines)
            self.refresh()

        self.app.task.run(work, done, "Checking tracks")

    def make_copies(self):
        items = self.state.items
        if not items:
            return
        profile = self.profile
        try:
            ffmpeg.require("ffmpeg")
            if any(it.target == convert.MP3 for it in items
                   if it.status == convert.CONVERT) and not ffmpeg.has_lame():
                raise ffmpeg.Missing("This ffmpeg can't write MP3 (it was built without "
                                     "LAME). Choose AIFF copies, or install a full ffmpeg.")
        except ffmpeg.Missing as e:
            self._need_ffmpeg(e)
            return

        def work(progress, log):
            return convert.run(items, profile, progress=progress, log=log)

        def done(result):
            self.state.copied = True
            self._show(convert.report_lines(result))
            self.refresh()

        self.app.task.run(work, done, "Making gig-safe copies")

    def check_stick(self, decode=True):
        start = "/Volumes" if paths.IS_MAC and os.path.isdir("/Volumes") else None
        stick = filedialog.askdirectory(title="Choose the USB stick", initialdir=start,
                                        mustexist=True)
        if not stick:
            return
        try:
            ffmpeg.require("ffprobe")
            ffmpeg.require("ffmpeg")
        except ffmpeg.Missing as e:
            self._need_ffmpeg(e)
            return
        profile = self.profile
        originals = [it.playable for it in self.state.items or [] if it.playable]

        def work(progress, log):
            return verify.verify(stick, profile, decode=decode, sources=originals or None,
                                 progress=progress)

        def done(report):
            self.state.report = report
            # Problems first, so what needs fixing is on screen without scrolling.
            self._show(report.lines(problems_first=True)[2:-2], report.verdict,
                       report.ready)
            self.refresh()

        self.app.task.run(work, done, "Checking the stick")

    def choose_library(self):
        root = self.app.require_root()
        if not root:
            return
        d = filedialog.askdirectory(title="Choose where gig-safe copies go")
        if not d:
            return
        try:
            convert.library_dir(root, d)
        except convert.LibraryError as e:
            messagebox.showwarning(APP, str(e))
            return
        settings.set("gig_library", d)
        self._reset()

    def show_library(self):
        root = self.app.require_root()
        if not root:
            return
        lib = convert.library_dir(root, settings.get("gig_library"))
        if os.path.isdir(lib):
            paths.reveal(lib)
        else:
            messagebox.showinfo(APP, f"No copies yet. They'll go in:\n\n{lib}")

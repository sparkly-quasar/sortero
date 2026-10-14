"""Gig prep: a stick that plays on any Pioneer player, checked before you leave."""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .. import mixxx, ui, paths, playlists, settings
from ..common import human_size
from ..gig import backup, checklist, convert, cues, ffmpeg, profiles, rbxml, verify
from ..gig.checklist import DONE, NEXT, WAITING
from .base import Screen, APP

TARGETS = [(convert.MP3, "MP3 320 kbps (smaller)"), (convert.AIFF, "AIFF 16-bit (lossless)")]
TONES = {DONE: "good", NEXT: None, WAITING: "faint"}


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
                           ("Use cue points from a rekordbox XML…", self.choose_cue_xml),
                           ("How to use the XML in rekordbox", self.show_how_to),
                           ("Cue timing on MP3 copies…", self.cue_timing),
                           None,
                           ("Quick stick check (skip the decode test)",
                            lambda: self.check_stick(decode=False)),
                           None,
                           ("Format a stick as FAT32…", self.format_stick)])
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
        self.state.backup, self.state.xml_path, self.state.xml_cues = None, "", {}
        self.refresh()

    # ------------------------------------------------------------ drawing
    def refresh(self):
        st = self.state
        st.playlists = list(self.chosen)
        st.track_count = sum(self.counts.get(n, 0) for n in self.chosen)
        all_steps = checklist.steps(st)
        nxt = checklist.next_step(all_steps)
        for w in self.steps_frame.winfo_children():
            w.destroy()
        actions = {"check": self.check_tracks, "copies": self.make_copies,
                   "xml": self.make_xml, "stick": self.write_stick,
                   "verify": self.check_stick}
        for i, s in enumerate(all_steps, 1):
            # Only the step the blue button does explains itself; the rest are one
            # line each, so the list stays short and the report below has room.
            ui.Card(self.steps_frame, f"{checklist.MARKS[s.status]}  {i}. {s.title}",
                    s.text if s is nxt else "", s.button, actions.get(s.key),
                    tone=TONES[s.status]).pack(fill="x", pady=(0, 4))
        if nxt:
            self.bar.set_primary(nxt.button, actions[nxt.key])
        elif not self.chosen:
            self.bar.set_primary("Check tracks", None, state="disabled")
        else:
            self.bar.set_primary("Check a stick…", self.check_stick)
        done = sum(1 for s in all_steps if s.status == DONE)
        self.header.set_summary(f"{done} of {len(all_steps)} steps done." if done
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

    # ----------------------------------------------------------- rekordbox
    def _xml_path(self, root):
        lib = convert.library_dir(root, settings.get("gig_library"))
        return lib, os.path.join(lib, rbxml.FILE_NAME)

    def make_xml(self):
        root = self.app.require_root()
        items = self.state.items
        if not root or not items:
            return
        lib, path = self._xml_path(root)
        names = list(self.chosen)
        source_xml = settings.get("gig_rekordbox_xml") or None
        if source_xml and not os.path.isfile(source_xml):
            source_xml = None
        db = mixxx.find_db()
        shift = bool(settings.get("gig_mp3_cue_shift"))

        def work(progress, log):
            finder = cues.Finder(source_xml, db, log=log)
            sets = [(n, playlists.entries(root, n)) for n in names]
            tree, counts = rbxml.build(items, sets, rbxml.gig_name(), finder, shift)
            os.makedirs(lib, exist_ok=True)
            return rbxml.write(path, tree), counts

        def done(res):
            out, counts = res
            self.state.xml_path, self.state.xml_cues = out, counts
            got = ", ".join(f"{n} from {src}" for src, n in counts.items())
            self._show([f"Saved “{os.path.basename(out)}” in the gig library.",
                        f"Cue points and beat grids: {got}." if got else
                        "No cue points or beat grids found for these tracks.", "",
                        "The steps for rekordbox are in their own window, to keep open "
                        "beside it. More → How to use the XML in rekordbox brings it "
                        "back."])
            self.refresh()
            self.show_how_to()

        self.app.task.run(work, done, "Making the rekordbox XML")

    def show_how_to(self):
        root = self.app.require_root()
        if not root:
            return
        path = self._xml_path(root)[1]
        win = getattr(self, "how_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        win = self.how_win = tk.Toplevel(self)
        win.title("Importing into rekordbox")
        win.transient(self.app)
        frame = ttk.Frame(win, padding=ui.PAD)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Into rekordbox, then onto the stick",
                  style="Heading.TLabel").pack(anchor="w", pady=(0, ui.GAP))
        txt = tk.Text(frame, width=72, height=16, font=ui.BODY, wrap="word", relief="flat",
                      highlightthickness=0)
        ui.paint(txt, bg="window", fg="text")
        txt.insert("1.0", "\n".join(rbxml.how_to(path)))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(ui.GAP, 0))

        def copy():
            win.clipboard_clear()
            win.clipboard_append(path)
            copied.configure(text="Copied.")
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(row, text="Copy the XML's location", command=copy).pack(side="right",
                                                                           padx=ui.GAP)
        copied = ttk.Label(row, style="Muted.TLabel")
        copied.pack(side="left")

    def choose_cue_xml(self):
        current = settings.get("gig_rekordbox_xml") or ""
        path = filedialog.askopenfilename(
            title="Choose your rekordbox collection XML",
            initialdir=os.path.dirname(current) or os.path.expanduser("~"),
            filetypes=[("rekordbox XML", "*.xml"), ("All files", "*")])
        if not path:
            return
        settings.set("gig_rekordbox_xml", path)
        messagebox.showinfo(APP, "Cue points and beat grids in that XML come first, then "
                                 "Mixxx's, then Mixed In Key's in the files.\n\nIn "
                                 "rekordbox, File > Export Collection in xml format "
                                 "writes a fresh one. Mixed In Key's rekordbox.xml works "
                                 "too.")
        self.state.xml_path = ""
        self.refresh()

    def cue_timing(self):
        on = bool(settings.get("gig_mp3_cue_shift"))
        msg = ("An MP3 copy can start 25 ms late in a player that ignores the MP3's "
               "gapless information, which would put every cue just before its beat.\n\n"
               "To check once: import one converted track with cue points into rekordbox "
               "and look at its first cue at full zoom. Is it about 25 ms (a sliver) "
               "BEFORE the kick it should sit on?\n\n"
               f"Cues on MP3 copies are now {'moved 25 ms later' if on else 'left as they are'}.")
        answer = messagebox.askyesnocancel(APP, msg + "\n\nYes: the cue sat early, move "
                                                      "them.  No: it sat on the kick.")
        if answer is None:
            return
        settings.set("gig_mp3_cue_shift", bool(answer))
        if self.state.xml_path:
            self.state.xml_path = ""
            self.refresh()

    def _choose_stick(self):
        start = "/Volumes" if paths.IS_MAC and os.path.isdir("/Volumes") else None
        return filedialog.askdirectory(title="Choose the USB stick", initialdir=start,
                                       mustexist=True)

    def write_stick(self):
        root = self.app.require_root()
        items = self.state.items
        if not root or not items:
            return
        sets, missing = backup.sets_from(items, root, self.chosen)
        stick = self._choose_stick()
        if not stick:
            return
        try:
            p = backup.plan(stick, sets)
        except backup.BackupError as e:
            messagebox.showwarning(APP, str(e))
            return
        name = os.path.basename(os.path.normpath(stick)) or stick
        free = backup.free_space(stick)
        if p.bytes > free:
            messagebox.showwarning(APP, f"{name} needs {human_size(p.bytes)} free and has "
                                        f"{human_size(free)}. Free some space, or use a "
                                        "bigger stick.")
            return
        lines = [f"Write {ui.plural(len(sets), 'playlist')} to {name} as folders?",
                 ""]
        lines += [f"    {folder}" for folder in p.folders]
        lines.append("")
        if p.copy:
            lines.append(f"{ui.plural(len(p.copy), 'track')} to copy, "
                         f"{human_size(p.bytes)}.")
        if p.keep:
            lines.append(f"{len(p.keep):,} already there from last time.")
        if p.remove:
            lines.append(f"{len(p.remove):,} left from an earlier backup will be removed.")
        if p.blocked or missing:
            lines.append(f"{len(p.blocked) + len(missing):,} can't go on; the report will "
                         "say why.")
        lines.append("\nFiles Sortero didn't put on the stick are never touched. "
                     "Afterwards the stick is checked.")
        if not messagebox.askyesno(APP, "\n".join(lines)):
            return

        def work(progress, log):
            return backup.run(p, progress=progress, log=log)

        def done(res):
            res.skipped += [(f"{pl}: {os.path.basename(src)}", "it has no playable copy "
                             "(see Check the tracks)") for pl, src in missing]
            self.state.backup, self.state.stick_name = res, name
            self._show(res.lines())
            self.refresh()
            self._verify(stick, decode=True)

        self.app.task.run(work, done, "Writing the stick")

    def check_stick(self, decode=True):
        stick = self._choose_stick()
        if stick:
            self._verify(stick, decode)

    def _verify(self, stick, decode=True):
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
            lines = report.lines(problems_first=True)[2:-2]
            stick = next((c for c in report.checks if c.title == "Stick"), None)
            if stick and stick.level == verify.FAIL:
                from ..gig import formatting
                if formatting.available():
                    lines += ["", "More → Format a stick as FAT32… can fix the stick "
                                  "itself. It erases everything on it."]
            self._show(lines, report.verdict, report.ready)
            self.refresh()

        self.app.task.run(work, done, "Checking the stick")

    def format_stick(self):
        from . import format_stick
        root = self.app.require_root()
        if not root:
            return
        keep = [root, paths.data_dir()]
        try:
            keep.append(convert.library_dir(root, settings.get("gig_library")))
        except convert.LibraryError:
            pass

        def done(label, output):
            self._show([f"Formatted as FAT32 on one MBR partition, named {label}.", "",
                        "Next: put the gig on it (steps 4 and 5), then check it."])

        format_stick.open_for(self.app, keep, done)

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

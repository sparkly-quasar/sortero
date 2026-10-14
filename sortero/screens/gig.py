"""Gig prep: a stick that plays on any Pioneer or Denon player, checked before you leave."""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .. import mixxx, ui, paths, settings
from ..common import human_size
from ..gig import backup, checklist, convert, cues, ffmpeg, profiles, rbxml, sources, verify
from ..gig.checklist import DONE, NEXT, WAITING
from .base import Screen, APP

TARGETS = [(convert.MP3, "MP3 320 kbps (smaller)"), (convert.AIFF, "AIFF 16-bit (lossless)")]
TONES = {DONE: "good", NEXT: None, WAITING: "faint"}
# Headings in the playlist list, once there's more than Sortero's own to show
GROUPS = [(sources.SORTERO, "In your collection"), (sources.M3U, "Playlist files"),
          (sources.FOLDER, "Folders of tracks"), (sources.REKORDBOX, "From rekordbox"),
          (sources.MIXXX, "From Mixxx")]


def _short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home + os.sep) else path


def _profile_label(p):
    return f"{p.name} (safest)" if p.key == profiles.EVERY.key else p.name


class GigScreen(Screen):
    title = "Gig prep"
    summary = ("Get a USB stick that plays on any Pioneer or Denon player, and check it "
               "before you leave.")
    details = ("Tick the playlists you're playing. Sortero checks every track against what "
               "Pioneer's and Denon's own manuals say the players read, and makes a "
               "gig-safe copy of any track that won't play everywhere, in a folder next to "
               "your collection. Your originals are never changed.\n\n"
               "Then plug in the stick and check it: the file system, the rekordbox or "
               "Engine DJ library if there is one, every format, and every track decoded "
               "from start to finish. It ends with GIG READY or NOT READY, and what to fix.")

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, ui.SECTION))
        ttk.Label(top, text="Players").pack(side="left", padx=(0, ui.GAP))
        self.profile_labels = {_profile_label(p): p.key for p in profiles.PROFILES.values()}
        self.profile_var = tk.StringVar(
            value=_profile_label(profiles.get(settings.get("gig_profile"))))
        box = ttk.Combobox(top, textvariable=self.profile_var, state="readonly", width=34,
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
        # Below the list, packed first so the list can't squeeze it out
        under = ttk.Frame(left)
        under.pack(side="bottom", fill="x", pady=(6, 0))
        self.add_btn = ttk.Menubutton(under, text="Add playlists…")
        menu = tk.Menu(self.add_btn, tearoff=0)
        menu.add_command(label="Playlist files (.m3u, .m3u8)…", command=self.add_m3u_files)
        menu.add_command(label="Every playlist file in a folder…", command=self.add_m3u_folder)
        menu.add_command(label="A folder of tracks…", command=self.add_track_folder)
        menu.add_separator()
        menu.add_command(label="From a rekordbox XML…", command=self.add_rekordbox)
        menu.add_command(label="From Mixxx…", command=self.add_mixxx)
        self.add_btn.configure(menu=menu)
        self.add_btn.pack(anchor="w")
        self.where_note = ui.WrapLabel(under, style="Muted.TLabel", text="")
        self.where_note.pack(fill="x", pady=(4, 0))
        f, self.tv = ui.tree(left, [("n", "#", 36), ("name", "Playlist", 210),
                                    ("count", "Tracks", 60)], height=10, selectmode="none")
        f.pack(fill="both", expand=True)
        self.tv.tag_configure("chosen", font=ui.HEADING)
        self.tv.bind("<Button-1>", self._click)
        self.tv.bind("<space>", lambda e: self._toggle(self.tv.focus()))
        # right-click; on a Mac also the two-finger click (Button-2) and Control-click
        for seq in ["<Button-3>"] + (["<Button-2>", "<Control-Button-1>"]
                                     if paths.IS_MAC else []):
            self.tv.bind(seq, self._context)
        for seq in ("<Delete>", "<BackSpace>"):
            self.tv.bind(seq, lambda e: self.remove(self.tv.focus()))

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
                           ("How to get the XML onto the stick", self.show_how_to),
                           ("Cue timing on MP3 copies…", self.cue_timing),
                           None,
                           ("Quick stick check (skip the decode test)",
                            lambda: self.check_stick(decode=False)),
                           None,
                           ("Format a stick as FAT32…", self.format_stick)])
        self.chosen = [p for p in settings.get("gig_playlists") or []]   # Source keys
        self.sources = {}           # key -> Source, in the order they're listed
        self.counts = {}            # key -> tracks, or None when it can't be read
        self.state = checklist.State()
        self.loaded_root = None

    # ---------------------------------------------------------- playlists
    def shown(self):
        # Read every time: a playlist file, rekordbox export or Mixxx crate may
        # have changed in the app that made it since Gig prep last looked.
        self.loaded_root = self.app.root_dir.get()
        self._load_playlists()
        self.refresh()

    def invalidate(self):
        self.loaded_root = None     # playlists may have changed; reload when next shown

    def _load_playlists(self):
        root = self.app.root_dir.get()
        self.sources = {s.key: s for s in sources.listed(root)}
        self.counts = {}
        for key, src in self.sources.items():
            try:
                self.counts[key] = len(sources.tracks(src, root))
            except sources.SourceError:
                self.counts[key] = None
        self.chosen = [k for k in self.chosen if k in self.sources]
        if not self.sources:
            self.pick_note.configure(text="No playlists yet. Add the ones you already have "
                                          "below, make them on the Playlists screen, or "
                                          "let Tidy up → Reorganise turn your folders "
                                          "into playlists.")
        else:
            self.pick_note.configure(text="Click to tick. The numbers are your set order.")
        self._fill_tree()

    def _fill_tree(self):
        self.tv.delete(*self.tv.get_children())
        self.tv.tag_configure("group", foreground=ui.c("faint"))
        self.tv.tag_configure("unreadable", foreground=ui.c("faint"))
        kinds = {s.kind for s in self.sources.values()}
        grouped = kinds - {sources.SORTERO}       # headings only once there's a mix
        for kind, heading in GROUPS:
            rows = [k for k, s in self.sources.items() if s.kind == kind]
            if not rows:
                continue
            if grouped:
                self.tv.insert("", "end", iid="group:" + kind, values=("", heading, ""),
                               tags=("group",))
            for key in rows:
                src, n = self.sources[key], self.counts.get(key)
                pos = self.chosen.index(key) + 1 if key in self.chosen else ""
                tags = ("chosen",) if pos else ("unreadable",) if n is None else ()
                self.tv.insert("", "end", iid=key,
                               values=(pos, src.name, "?" if n is None else n), tags=tags)

    def _click(self, e):
        row = self.tv.identify_row(e.y)
        if row and row in self.sources:
            self.tv.focus(row)
            self._toggle(row)
        return "break"

    def _toggle(self, key):
        if key not in self.sources:
            return
        src = self.sources[key]
        self._say_where(src)
        if key in self.chosen:
            self.chosen.remove(key)
        elif self.counts.get(key) is None:
            try:
                sources.tracks(src, self.app.root_dir.get())
            except sources.SourceError as e:
                messagebox.showwarning(APP, f"Sortero can't read “{src.name}”.\n\n{e}")
                return
            self._load_playlists()      # it's back: count it, then tick it
            self.chosen.append(key)
        else:
            self.chosen.append(key)
        settings.set("gig_playlists", self.chosen)
        self._fill_tree()
        self._reset()

    def _say_where(self, src):
        if src.kind == sources.SORTERO:
            text = "In your collection's playlists."
        else:
            text = f"From {_short(src.where)}. Right-click to remove it from this list."
        self.where_note.configure(text=text)

    # ------------------------------------------------- playlists from elsewhere
    def _context(self, e):
        row = self.tv.identify_row(e.y)
        src = self.sources.get(row)
        if not src or src.kind == sources.SORTERO:
            return "break"
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"Remove “{src.name}” from this list",
                         command=lambda: self.remove(row))
        menu.tk_popup(e.x_root, e.y_root)
        return "break"

    def remove(self, key):
        """Take an added playlist off the list. The playlist itself is untouched."""
        src = self.sources.get(key)
        if not src or src.kind == sources.SORTERO:
            return
        sources.forget(key)
        if key in self.chosen:
            self.chosen.remove(key)
            settings.set("gig_playlists", self.chosen)
            self._reset()
        self.where_note.configure(text=f"Removed “{src.name}” from the list. "
                                       "The playlist itself wasn't touched.")
        self._load_playlists()

    def _add(self, new, tick=True, note=""):
        fresh = sources.remember(new)
        if tick:
            for s in new:
                if s.key not in self.chosen:
                    self.chosen.append(s.key)
            settings.set("gig_playlists", self.chosen)
        self._load_playlists()
        if tick and new:
            self._reset()
        again = len(new) - len(fresh)
        lines = [f"Added {ui.plural(len(fresh), 'playlist')}"
                 + (f" ({again:,} already on the list)" if again else "") + "."]
        one = len(new) == 1
        if tick:
            lines.append(f"{'It is' if one else 'They are'} ticked, at the end of the set order.")
        else:
            lines.append(f"Tick {'it' if one else 'the ones you are playing'} to use "
                         f"{'it' if one else 'them'}.")
        lines.append(f"Sortero only reads {'it' if one else 'them'}, again each time, so "
                     "changes you make in the app they came from are picked up.")
        if note:
            lines += ["", note]
        self._show(lines)

    def _start_dir(self, *candidates):
        for c in candidates:
            if c and os.path.isdir(c):
                return c
        return self.app.root_dir.get() or os.path.expanduser("~")

    def add_m3u_files(self):
        picked = filedialog.askopenfilenames(
            title="Choose playlist files", initialdir=self._start_dir(),
            filetypes=[("Playlists", "*.m3u *.m3u8 *.M3U *.M3U8"), ("All files", "*")])
        if picked:
            self._add(sources.m3u_sources(picked))

    def add_m3u_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of playlist files",
                                         initialdir=self._start_dir(), mustexist=True)
        if not folder:
            return
        found = sources.find_m3u(folder)
        if not found:
            messagebox.showinfo(APP, f"There are no .m3u or .m3u8 files in "
                                     f"{os.path.basename(folder) or folder}, or the "
                                     "folders inside it.")
            return
        self._add(sources.m3u_sources(found), tick=False)

    def add_track_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of tracks",
                                         initialdir=self._start_dir(), mustexist=True)
        if not folder:
            return
        try:
            n = len(sources.folder_tracks(folder))
        except OSError as e:
            messagebox.showwarning(APP, f"Sortero couldn't read that folder ({e}).")
            return
        if not n:
            messagebox.showinfo(APP, f"There are no audio files in "
                                     f"{os.path.basename(folder) or folder}.")
            return
        self._add([sources.folder_source(folder)],
                  note="A folder plays in filename order, so number the files "
                       "(01, 02, …) to set the order.")

    def add_rekordbox(self):
        current = settings.get("gig_rekordbox_xml") or ""
        path = filedialog.askopenfilename(
            title="Choose a rekordbox collection XML",
            initialdir=self._start_dir(os.path.dirname(current)),
            filetypes=[("rekordbox XML", "*.xml"), ("All files", "*")])
        if not path:
            return

        def work(progress, log):
            try:
                found = sources.rekordbox_sources(path)
                return found, {s.key: len(sources.tracks(s)) for s in found}
            except sources.SourceError as e:
                return e

        def done(res):
            if isinstance(res, sources.SourceError):
                self._cant_read(res)
                return
            found, counts = res
            if not found:
                messagebox.showinfo(APP, "That XML has no playlists in it. In rekordbox, "
                                         "File > Export Collection in xml format writes "
                                         "one with all of them.")
                return

            def chosen(picked):
                note = ""
                if not settings.get("gig_rekordbox_xml"):
                    settings.set("gig_rekordbox_xml", path)
                    note = ("Its cue points and beat grids will go into the XML Gig prep "
                            "makes, too.")
                self._add(picked, note=note)
            PickDialog(self, f"Playlists in {os.path.basename(path)}", found, counts,
                       chosen)

        self.app.task.run(work, done, "Reading the rekordbox XML")

    def add_mixxx(self):
        db = mixxx.find_db()
        if not db:
            messagebox.showinfo(APP, "Sortero couldn't find Mixxx's library on this "
                                     "computer. Open Mixxx once, then try again.")
            return
        try:
            found = sources.mixxx_sources(db)
            counts = {s.key: len(sources.tracks(s)) for s in found}
        except sources.SourceError as e:
            self._cant_read(e)
            return
        if not found:
            messagebox.showinfo(APP, "Mixxx has no playlists or crates yet.")
            return
        PickDialog(self, "Playlists and crates in Mixxx", found, counts, self._add)

    def _cant_read(self, e):
        messagebox.showwarning(APP, str(e))

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
        self.state.sets = None
        self.refresh()

    # ------------------------------------------------------------ drawing
    def refresh(self):
        st = self.state
        st.playlists = [self.sources[k].name for k in self.chosen if k in self.sources]
        st.track_count = sum(self.counts.get(k) or 0 for k in self.chosen)
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
        chosen = [self.sources[k] for k in self.chosen if k in self.sources]
        profile, target = self.profile, self.target

        def work(progress, log):
            unreadable = []
            # Read once here, and every later step uses the same snapshot, so the
            # copies, the XML and the stick all match what was checked.
            sets = sources.resolve(chosen, root, log=unreadable.append)
            items = convert.plan(root, convert.playlist_paths(sets), lib, profile, target,
                                 progress)
            return sets, items, unreadable

        def done(res):
            sets, items, unreadable = res
            self.state.items, self.state.copied = items, False
            self.state.sets = sets
            self.state.report = None
            lines = [checklist.check_text(items), ""] + unreadable
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
        sets = list(self.state.sets or [])
        source_xml = settings.get("gig_rekordbox_xml") or None
        if source_xml and not os.path.isfile(source_xml):
            source_xml = None
        db = mixxx.find_db()
        shift = bool(settings.get("gig_mp3_cue_shift"))

        def work(progress, log):
            finder = cues.Finder(source_xml, db, log=log)
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
                        f"The steps for {self._apps()} are in their own window, to keep "
                        "open beside it. More → How to get the XML onto the stick brings "
                        "it back."])
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
        win.title("Putting the gig on the stick")
        win.transient(self.app)
        frame = ttk.Frame(win, padding=ui.PAD)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Into {self._apps()}, then onto the stick",
                  style="Heading.TLabel").pack(anchor="w", pady=(0, ui.GAP))
        row = ttk.Frame(frame)
        row.pack(side="bottom", fill="x", pady=(ui.GAP, 0))
        box = ttk.Frame(frame)
        box.pack(fill="both", expand=True)
        txt = tk.Text(box, width=74, height=22, font=ui.BODY, wrap="word", relief="flat",
                      highlightthickness=0)
        ui.paint(txt, bg="window", fg="text")
        sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.tag_configure("head", font=ui.HEADING)
        profile = self.profile
        sections = []
        if profiles.reads_rekordbox(profile):
            sections.append(("For Pioneer players: rekordbox", rbxml.how_to(path)))
        if profiles.reads_engine(profile):
            sections.append(("For Denon players: Engine DJ", rbxml.engine_how_to(path)))
        for i, (head, lines) in enumerate(sections):
            if len(sections) > 1:
                txt.insert("end", ("\n" if i else "") + head + "\n", "head")
            txt.insert("end", "\n".join(lines) + "\n")
        txt.configure(state="disabled")
        txt.pack(side="left", fill="both", expand=True)

        def copy():
            win.clipboard_clear()
            win.clipboard_append(path)
            copied.configure(text="Copied.")
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(row, text="Copy the XML's location", command=copy).pack(side="right",
                                                                           padx=ui.GAP)
        copied = ttk.Label(row, style="Muted.TLabel")
        copied.pack(side="left")

    def _apps(self):
        """The apps the chosen players' libraries come from: "rekordbox and Engine DJ"."""
        apps = [name for reads, name in ((profiles.reads_rekordbox, "rekordbox"),
                                         (profiles.reads_engine, "Engine DJ"))
                if reads(self.profile)]
        return " and ".join(apps) or "rekordbox"

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
        sets, missing = backup.sets_from(items, self.state.sets or [])
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


class PickDialog(tk.Toplevel):
    """Tick playlists from a rekordbox XML or Mixxx to add to Gig prep's list."""

    def __init__(self, screen, title, found, counts, on_add):
        super().__init__(screen)
        self.title(title)
        self.transient(screen.app)
        self.on_add, self.found = on_add, {s.key: s for s in found}
        have = set(screen.sources)
        self.picked = []
        frame = ttk.Frame(self, padding=ui.PAD)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="Heading.TLabel").pack(anchor="w")
        ui.WrapLabel(frame, style="Muted.TLabel",
                     text="Click the ones you're playing. They'll be ticked for this gig, "
                          "in the order you click them.").pack(fill="x", pady=(2, 6))
        row = ttk.Frame(frame)
        row.pack(side="bottom", fill="x", pady=(ui.GAP, 0))
        self.add_btn = ttk.Button(row, text="Add", default="active", command=self._done,
                                  state="disabled")
        self.add_btn.pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right",
                                                                  padx=(0, ui.GAP))
        f, self.tv = ui.tree(frame, [("tick", "", 30), ("name", "Playlist", 260),
                                     ("where", "In", 200), ("count", "Tracks", 60)],
                             height=14, selectmode="none")
        f.pack(fill="both", expand=True)
        self.tv.tag_configure("picked", font=ui.HEADING)
        self.tv.tag_configure("have", foreground=ui.c("faint"))
        for key, src in self.found.items():
            if src.kind == sources.REKORDBOX:
                where = " › ".join(src.ref[:-1])
            else:
                where = "Crate" if src.ref[0] == "crate" else "Playlist"
            already = key in have
            self.tv.insert("", "end", iid=key, values=(
                "✓" if already else "", src.name, where,
                counts.get(key, "")), tags=("have",) if already else ())
        self.have = have
        self.tv.bind("<Button-1>", self._click)
        self.geometry("640x480")
        self.grab_set()

    def _click(self, e):
        key = self.tv.identify_row(e.y)
        if not key or key in self.have:
            return "break"
        if key in self.picked:
            self.picked.remove(key)
        else:
            self.picked.append(key)
        for k in self.found:
            if k not in self.have:
                pos = self.picked.index(k) + 1 if k in self.picked else ""
                self.tv.set(k, "tick", pos)
                self.tv.item(k, tags=("picked",) if pos else ())
        n = len(self.picked)
        self.add_btn.configure(text=f"Add {ui.plural(n, 'playlist')}" if n else "Add",
                               state="normal" if n else "disabled")
        return "break"

    def _done(self):
        picked = [self.found[k] for k in self.picked]
        self.destroy()
        self.on_add(picked)

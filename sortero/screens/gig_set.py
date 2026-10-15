"""Gig prep's first step: the playlists for this gig, in play order.

Two lists side by side. On the left, every playlist Gig prep knows about, with
a tick box: Sortero's own, and the ones added from elsewhere (gig/sources.py),
which have an × to take them off the list. Watched folders list every
playlist file in them each time, under a heading of their own; their × stops
watching the folder. On the right, the set: the ticked
ones in play order, with ↑ and ↓ to move them. Add playlists… and Clear set
sit in the step's own header. Clearing can be undone, and so can taking a
playlist off the list.
"""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .. import mixxx, paths, settings, ui
from ..gig import sources
from .base import APP

# Headings in the playlist list, once there's more than Sortero's own to show
GROUPS = [(sources.SORTERO, "In your collection"), (sources.WATCHED, ""),
          (sources.M3U, "Playlist files"),
          (sources.FOLDER, "Folders of tracks"), (sources.REKORDBOX, "From rekordbox"),
          (sources.MIXXX, "From Mixxx")]
TICK, NO_TICK, REMOVE = "☑", "☐", "×"


def short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home + os.sep) else path


class SetPicker(tk.Frame):
    """The body of step 1. `screen` is told when the set changes (set_changed)
    and keeps the rest of the gig's progress (snapshot/restore) for undo."""

    def __init__(self, parent, screen, tools):
        super().__init__(parent)
        ui.paint(self, bg="card")
        self.screen, self.app = screen, screen.app
        self.chosen = list(settings.get("gig_playlists") or [])     # Source keys
        self.sources, self.counts = {}, {}
        self._undo = None

        # The step's header: Add playlists… and Clear set
        self.clear_btn = ttk.Button(tools, text="Clear set", command=self.clear)
        self.clear_btn.pack(side="right", padx=(ui.GAP, 0))
        add = ttk.Menubutton(tools, text="Add playlists…")
        menu = tk.Menu(add, tearoff=0)
        menu.add_command(label="Playlist files (.m3u, .m3u8)…", command=self.add_m3u_files)
        menu.add_command(label="Watch a folder of playlist files…", command=self.add_m3u_folder)
        menu.add_command(label="A folder of tracks…", command=self.add_track_folder)
        menu.add_separator()
        menu.add_command(label="From a rekordbox XML…", command=self.add_rekordbox)
        menu.add_command(label="From Mixxx…", command=self.add_mixxx)
        add.configure(menu=menu)
        add.pack(side="right")

        # A note under both lists, for where a playlist comes from, and Undo
        note = tk.Frame(self)
        ui.paint(note, bg="card")
        note.pack(side="bottom", fill="x", pady=(6, 0))
        self.note = tk.Label(note, font=ui.BODY, anchor="w", justify="left")
        ui.paint(self.note, bg="card", fg="muted")
        self.note.pack(side="left")
        self.undo_link = tk.Label(note, text="Undo", font=ui.BODY,
                                  cursor="pointinghand" if paths.IS_MAC else "hand2")
        ui.paint(self.undo_link, bg="card", fg="link")
        self.undo_link.bind("<Button-1>", lambda e: self.undo())

        lists = tk.Frame(self)
        ui.paint(lists, bg="card")
        lists.pack(fill="both", expand=True)
        lists.columnconfigure(0, weight=3)
        lists.columnconfigure(1, weight=2)
        lists.rowconfigure(1, weight=1)

        self.all_label = tk.Label(lists, text="Playlists", font=ui.SMALL, anchor="w")
        ui.paint(self.all_label, bg="card", fg="muted")
        self.all_label.grid(row=0, column=0, sticky="w")
        f, self.tv = ui.tree(lists, [("tick", "", 34), ("name", "Playlist", 230),
                                     ("count", "Tracks", 60), ("x", "", 30)],
                             height=4, selectmode="none")
        f.grid(row=1, column=0, sticky="nsew", padx=(0, ui.SECTION))
        self.tv.tag_configure("chosen", font=ui.HEADING)
        self.tv.bind("<Button-1>", self._click)
        self.tv.bind("<space>", lambda e: self.toggle(self.tv.focus()))
        # right-click; on a Mac also the two-finger click (Button-2) and Control-click
        for seq in ["<Button-3>"] + (["<Button-2>", "<Control-Button-1>"]
                                     if paths.IS_MAC else []):
            self.tv.bind(seq, self._context)
        for seq in ("<Delete>", "<BackSpace>"):
            self.tv.bind(seq, lambda e: self.remove(self.tv.focus()))

        lab = tk.Label(lists, text="Your set, in play order", font=ui.SMALL, anchor="w")
        ui.paint(lab, bg="card", fg="muted")
        lab.grid(row=0, column=1, sticky="w")
        right = tk.Frame(lists)
        ui.paint(right, bg="card")
        right.grid(row=1, column=1, sticky="nsew")
        moves = tk.Frame(right)
        ui.paint(moves, bg="card")
        moves.pack(side="right", fill="y", padx=(4, 0))
        for text, step in (("↑", -1), ("↓", 1)):
            ttk.Button(moves, text=text, width=3,
                       command=lambda s=step: self.move(s)).pack(pady=(0, 4))
        ttk.Button(moves, text=REMOVE, width=3,
                   command=lambda: self.toggle(self.set_tv.focus())).pack()
        f, self.set_tv = ui.tree(right, [("n", "#", 34), ("name", "Playlist", 170),
                                         ("count", "Tracks", 60)],
                                 height=4, selectmode="browse")
        f.pack(side="left", fill="both", expand=True)
        for seq in ("<Alt-Up>", "<Option-Up>" if paths.IS_MAC else "<Control-Up>"):
            self.set_tv.bind(seq, lambda e: self.move(-1) or "break")
        for seq in ("<Alt-Down>", "<Option-Down>" if paths.IS_MAC else "<Control-Down>"):
            self.set_tv.bind(seq, lambda e: self.move(1) or "break")

    # ------------------------------------------------------------ reading
    def load(self):
        """Read every playlist again: one may have changed in the app that made it."""
        root = self.app.root_dir.get()
        self.sources = {s.key: s for s in sources.listed(root)}
        self.counts = {}
        for key, src in self.sources.items():
            try:
                self.counts[key] = len(sources.tracks(src, root))
            except sources.SourceError:
                self.counts[key] = None
        self.chosen = [k for k in self.chosen if k in self.sources]
        self._fill()
        gone = sources.missing_watched()
        if gone:
            self._say(f"Can't see {short(gone[0])}"
                      + (f" or {ui.plural(len(gone) - 1, 'other watched folder')}"
                         if len(gone) > 1 else "")
                      + ". If it's on a drive, plug it in, and its playlists come back.")
        elif not self.sources:
            self._say("No playlists yet. Add the ones you already have with Add "
                      "playlists…, make them on the Playlists screen, or let Tidy up → "
                      "Reorganise turn your folders into playlists.")

    def chosen_sources(self):
        return [self.sources[k] for k in self.chosen if k in self.sources]

    def names(self):
        return [s.name for s in self.chosen_sources()]

    def track_count(self):
        return sum(self.counts.get(k) or 0 for k in self.chosen)

    # ------------------------------------------------------------ drawing
    def _fill(self):
        """Rebuild the left list; only needed when the playlists themselves change."""
        y = self.tv.yview()[0]
        self.tv.delete(*self.tv.get_children())
        self.tv.tag_configure("group", foreground=ui.c("faint"))
        self.tv.tag_configure("unreadable", foreground=ui.c("faint"))
        grouped = {s.kind for s in self.sources.values()} - {sources.SORTERO}
        for kind, heading in GROUPS:
            if kind == sources.WATCHED:
                self._fill_watched()
                continue
            rows = [k for k, s in self.sources.items() if s.kind == kind]
            if not rows:
                continue
            if grouped:
                self.tv.insert("", "end", iid="group:" + kind, values=("", heading, "", ""),
                               tags=("group",))
            for key in rows:
                self.tv.insert("", "end", iid=key)
                self._row(key)
        self.tv.yview_moveto(y)
        self._fill_set()

    def _fill_watched(self):
        """One heading per watched folder, so it's clear where each came from."""
        for folder in sources.watched():
            rows = [k for k, s in self.sources.items()
                    if s.kind == sources.WATCHED and s.folder == folder]
            if not rows:
                continue
            self.tv.insert("", "end", iid="group:watch:" + folder,
                           values=("", f"Watching {short(folder)}", "", ""),
                           tags=("group",))
            for key in rows:
                self.tv.insert("", "end", iid=key)
                self._row(key)

    def _row(self, key):
        src, n = self.sources[key], self.counts.get(key)
        on = key in self.chosen
        self.tv.item(key, values=(TICK if on else NO_TICK, src.name, "?" if n is None else n,
                                  "" if src.kind == sources.SORTERO else REMOVE),
                     tags=("chosen",) if on else ("unreadable",) if n is None else ())

    def _fill_set(self, focus=None):
        self.set_tv.delete(*self.set_tv.get_children())
        for i, key in enumerate(self.chosen, 1):
            n = self.counts.get(key)
            self.set_tv.insert("", "end", iid=key, values=(i, self.sources[key].name,
                                                           "?" if n is None else n))
        if focus in self.chosen:
            self.set_tv.selection_set(focus)
            self.set_tv.focus(focus)
            self.set_tv.see(focus)
        self.clear_btn.configure(state="normal" if self.chosen else "disabled")

    def _say(self, text, undo=False):
        self.note.configure(text=text)
        if undo:
            self.undo_link.pack(side="left", padx=(ui.GAP, 0))
        else:
            self.undo_link.pack_forget()

    def _where(self, src):
        if src.kind == sources.SORTERO:
            return "In your collection's playlists."
        if src.kind == sources.WATCHED:
            return (f"From {short(src.where)}. Its folder is watched, so playlists "
                    "you save there show up here by themselves.")
        return f"From {short(src.where)}."

    # ------------------------------------------------------------ changing the set
    def _changed(self, reason, undoable=None):
        settings.set("gig_playlists", self.chosen)
        self._undo = undoable
        self.screen.set_changed(reason)

    def _click(self, e):
        row = self.tv.identify_row(e.y)
        if not row or row not in self.sources:
            return "break"
        self.tv.focus(row)
        if self.tv.identify_column(e.x) == "#4" and self.sources[row].kind != sources.SORTERO:
            self.remove(row)
        else:
            self.toggle(row)
        return "break"

    def toggle(self, key):
        if key not in self.sources:
            return
        src = self.sources[key]
        if key in self.chosen:
            self.chosen.remove(key)
        else:
            if self.counts.get(key) is None:
                try:
                    self.counts[key] = len(sources.tracks(src, self.app.root_dir.get()))
                except sources.SourceError as e:
                    messagebox.showwarning(APP, f"Sortero can't read “{src.name}”.\n\n{e}")
                    return
            self.chosen.append(key)
        self._row(key)
        self._fill_set(focus=key)
        self._say(self._where(src))
        self._changed("You changed the set")

    def move(self, step):
        key = self.set_tv.focus()
        if key not in self.chosen:
            return
        i = self.chosen.index(key)
        j = i + step
        if not 0 <= j < len(self.chosen):
            return
        self.chosen[i], self.chosen[j] = self.chosen[j], self.chosen[i]
        self._fill_set(focus=key)
        self._changed("You changed the play order")

    def clear(self):
        if not self.chosen:
            return
        n = len(self.chosen)
        snapshot = (list(self.chosen), self.screen.snapshot())
        self.chosen = []
        self._fill()
        self._changed(None, undoable=snapshot)
        self._say(f"Cleared {ui.plural(n, 'playlist')} from the set.", undo=True)

    def remove(self, key):
        """Take an added playlist off the list. The playlist itself is untouched."""
        src = self.sources.get(key)
        if not src or src.kind == sources.SORTERO:
            return
        if src.kind == sources.WATCHED:
            self.unwatch(src.folder)
            return
        snapshot = (list(self.chosen), self.screen.snapshot(), src)
        sources.forget(key)
        was_chosen = key in self.chosen
        if was_chosen:
            self.chosen.remove(key)
        self.load()
        self._say(f"Took “{src.name}” off the list. The playlist itself wasn't touched.",
                  undo=True)
        self._undo = snapshot
        if was_chosen:
            self._changed(None, undoable=snapshot)

    def unwatch(self, folder):
        """Stop watching a folder: its playlists leave the list, files untouched."""
        if folder not in sources.watched():
            return
        snapshot = (list(self.chosen), self.screen.snapshot(), folder)
        sources.unwatch(folder)
        before = list(self.chosen)
        self.load()
        self._say(f"Stopped watching {short(folder)}. Its playlists weren't touched.",
                  undo=True)
        self._undo = snapshot
        if self.chosen != before:
            self._changed(None, undoable=snapshot)

    def undo(self):
        if not self._undo:
            return
        chosen, state, *src = self._undo
        for item in src:
            if isinstance(item, str):
                sources.watch(item)             # a folder that was being watched
            else:
                sources.remember([item])
        self._undo = None
        self.chosen = chosen
        settings.set("gig_playlists", self.chosen)
        self.load()
        self._say("Put back as it was.")
        self.screen.restore(state)

    def forget_undo(self):
        """Something else changed, so an undo would bring back stale progress."""
        if self._undo:
            self._undo = None
            self._say("")

    def _context(self, e):
        row = self.tv.identify_row(e.y)
        src = self.sources.get(row)
        if not src or src.kind == sources.SORTERO:
            return "break"
        menu = tk.Menu(self, tearoff=0)
        if src.kind == sources.WATCHED:
            menu.add_command(label=f"Stop watching {os.path.basename(src.folder) or src.folder}",
                             command=lambda: self.unwatch(src.folder))
        else:
            menu.add_command(label=f"Take “{src.name}” off the list",
                             command=lambda: self.remove(row))
        menu.tk_popup(e.x_root, e.y_root)
        return "break"

    # ------------------------------------------------- playlists from elsewhere
    def _add(self, new, tick=True, note=""):
        fresh = sources.remember(new)
        if tick:
            for s in new:
                if s.key not in self.chosen:
                    self.chosen.append(s.key)
        self.load()
        again = len(new) - len(fresh)
        one = len(new) == 1
        text = (f"Added {ui.plural(len(fresh), 'playlist')}"
                + (f" ({again:,} already on the list)" if again else "") + ". ")
        text += (f"{'It is' if one else 'They are'} at the end of your set."
                 if tick else f"Tick {'it' if one else 'the ones you are playing'}.")
        if note:
            text += " " + note
        self._say(text)
        if tick and new:
            self._changed("You changed the set")

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
        """Watch a folder: every playlist file in it is listed, now and later."""
        folder = filedialog.askdirectory(title="Choose a folder of playlist files to watch",
                                         initialdir=self._start_dir(), mustexist=True)
        if not folder:
            return
        folder = os.path.abspath(folder)
        if not sources.watch(folder):
            self._say(f"{short(folder)} is already being watched.")
            return
        self.load()
        n = sum(1 for s in self.sources.values()
                if s.kind == sources.WATCHED and s.folder == folder)
        self._say(f"Watching {short(folder)}: "
                  + (f"{ui.plural(n, 'playlist')} there now. " if n else
                     "no playlist files there yet. ")
                  + "Any .m3u or .m3u8 saved there later shows up here by itself. "
                    "Tick the ones you're playing.")

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
                  note="It plays in filename order, so number the files (01, 02, …).")

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
                messagebox.showwarning(APP, str(res))
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
                    note = "Its cue points will go into the gig's XML too."
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
            messagebox.showwarning(APP, str(e))
            return
        if not found:
            messagebox.showinfo(APP, "Mixxx has no playlists or crates yet.")
            return
        PickDialog(self, "Playlists and crates in Mixxx", found, counts, self._add)


class PickDialog(tk.Toplevel):
    """Tick playlists from a rekordbox XML or Mixxx to add to Gig prep's list."""

    def __init__(self, picker, title, found, counts, on_add):
        super().__init__(picker)
        self.title(title)
        self.transient(picker.app)
        self.on_add, self.found = on_add, {s.key: s for s in found}
        have = set(picker.sources)
        self.picked = []
        frame = ttk.Frame(self, padding=ui.PAD)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="Heading.TLabel").pack(anchor="w")
        ui.WrapLabel(frame, style="Muted.TLabel",
                     text="Click the ones you're playing. They go into your set in the "
                          "order you click them.").pack(fill="x", pady=(2, 6))
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

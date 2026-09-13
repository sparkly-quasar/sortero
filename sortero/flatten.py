"""Pull tracks out of release folders into the genre folder above them.

A hand-built library tends to end up as Genre/Subgenre/Some EP/track, and for
DJing the release folder is just one more click. This lifts the tracks into the
genre folder and removes the emptied release folder. Filenames are kept; a clash
gets a number rather than overwriting anything. The release name can be kept in
the Album tag, so nothing about where a track came from is lost.
"""
import os, shutil, threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import folders, playlists
from .common import AUDIO_EXTS
from .journal import Journal, prune_empty
from .library import PROTECTED
from .organize import TRACKS_DIR
from .tagio import Track

TITLE = "Flatten release folders"
SUGGEST_MAX = 60        # bigger than a release; probably a label or artist archive


def home_of(parts):
    """The genre folder (as path parts) that a folder belongs under."""
    if parts[0] == TRACKS_DIR:
        home = parts[:2]
    elif len(parts) >= 2 and not folders.looks_like_release(parts[1]):
        home = parts[:2]
    else:
        home = parts[:1]
    # An energy subfolder is part of the layout, so it is the home, not a release:
    # flattening it would silently undo the energy split.
    if len(parts) > len(home) and folders.is_energy_dir(parts[len(home)]):
        home = parts[:len(home) + 1]
    return home


def candidates(root):
    """Folders holding tracks below their genre folder.

    [{rel, target, tracks, suggested}] - tracks counts only files directly in
    the folder; a disc subfolder is its own candidate and lifts to the same place.
    """
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith((".", "_"))]
        rel = os.path.relpath(dp, root)
        if rel == ".":
            dn[:] = [d for d in dn if d not in folders.NOT_A_HOME]
            continue
        parts = rel.split(os.sep)
        home = home_of(parts)
        if parts == home:
            continue
        n = sum(1 for f in fn if os.path.splitext(f)[1].lower() in AUDIO_EXTS)
        if not n:
            continue
        out.append({"rel": rel, "target": os.path.join(*home), "tracks": n,
                    "suggested": n <= SUGGEST_MAX})
    out.sort(key=lambda c: c["rel"].lower())
    return out


def plan_moves(root, chosen, cands):
    """[(src, dest, release folder name)] with collision-safe destination names."""
    by_rel = {c["rel"]: c for c in cands}
    taken = {}
    moves = []
    for rel in sorted(chosen):
        c = by_rel.get(rel)
        if not c:
            continue
        src_dir = os.path.join(root, rel)
        dst_dir = os.path.join(root, c["target"])
        if dst_dir not in taken:
            try:
                taken[dst_dir] = {f.lower() for f in os.listdir(dst_dir)}
            except OSError:
                taken[dst_dir] = set()
        names = taken[dst_dir]
        try:
            files = sorted(os.listdir(src_dir))
        except OSError:
            continue
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in AUDIO_EXTS:
                continue
            name, k = f, 2
            while name.lower() in names:
                name = f"{stem} ({k}){ext}"
                k += 1
            names.add(name.lower())
            moves.append((os.path.join(src_dir, f), os.path.join(dst_dir, name),
                          os.path.basename(rel)))
    return moves


def apply_moves(root, moves, write_album=True, log=print, progress=None):
    """Returns (journal, moved, failed, album tags written, folders kept)."""
    j = Journal("flatten", root)
    remap = {}
    moved = failed = albums = 0
    total = len(moves) or 1
    for i, (src, dst, release) in enumerate(moves):
        if progress and i % 10 == 0:
            progress(i, total)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            j.moved(src, dst)
            remap[src] = dst
            moved += 1
            if write_album:
                t = Track(dst)
                if t.ok and not (t.get("album") or "").strip():
                    t.set("album", release)
                    if t.save():
                        j.tagged(dst, {"album": {"old": None, "new": release}})
                        albums += 1
        except Exception as e:
            failed += 1
            log(f"  ! {os.path.basename(src)}: {e}")
    if remap:
        playlists.remap(root, remap, journal=j)
    src_dirs = {os.path.dirname(s) for s, _, _ in moves}
    prune_empty(root, keep=PROTECTED)
    kept = sum(1 for d in src_dirs if os.path.isdir(d))
    if progress:
        progress(total, total)
    path = j.save()
    log(f"flattened {moved} tracks ({failed} failed) | journal: {path}")
    return path, moved, failed, albums, kept


class FlattenDialog(tk.Toplevel):
    def __init__(self, parent, app, on_close=None):
        super().__init__(parent)
        self.app, self._parent, self.on_close = app, parent, on_close
        self.root_dir = app.root_dir.get()
        self.changed = False
        self.busy = False
        self.cands, self.chosen = [], set()

        self.title(TITLE)
        self.geometry("980x660")
        self.minsize(820, 520)
        self.transient(parent)

        pad = ttk.Frame(self, padding=16)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text=TITLE, font=("Helvetica", 17, "bold")).pack(anchor="w")
        ttk.Label(pad, foreground="#666", wraplength=930, justify="left",
                  text="Tracks inside a release folder — an EP or album sitting within a "
                       "genre folder — move up into that genre folder, and the emptied "
                       "release folder is removed. Filenames are kept; a clash gets a "
                       "number instead of overwriting. Folders that look like a single "
                       f"release ({SUGGEST_MAX} tracks or fewer) are ticked for you; big "
                       "archives such as a whole label are left unticked. Click the "
                       "Flatten column, or select rows and press space, to change it. "
                       "Undoable from History."
                  ).pack(anchor="w", pady=(4, 10))

        row = ttk.Frame(pad)
        row.pack(fill="x", pady=(0, 6))
        ttk.Button(row, text="Tick suggested", command=self.tick_suggested).pack(side="left")
        ttk.Button(row, text="Tick all", command=lambda: self.tick(True)).pack(side="left", padx=6)
        ttk.Button(row, text="Untick all", command=lambda: self.tick(False)).pack(side="left")
        self.album_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, variable=self.album_var,
                        text="Keep the release name in the Album tag when a track has none"
                        ).pack(side="left", padx=16)

        f = ttk.Frame(pad)
        f.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(f, columns=("pick", "folder", "tracks", "into"),
                               show="headings", selectmode="extended")
        for col, text, width, stretch in (("pick", "Flatten", 70, False),
                                          ("folder", "Release folder", 460, True),
                                          ("tracks", "Tracks", 70, False),
                                          ("into", "Moves into", 300, True)):
            self.tv.heading(col, text=text)
            self.tv.column(col, width=width, stretch=stretch,
                           anchor="center" if col in ("pick", "tracks") else "w")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv.bind("<ButtonRelease-1>", self._click)
        self.tv.bind("<space>", lambda e: self.toggle_selected())

        self.progress = ttk.Progressbar(pad)
        self.progress.pack(fill="x", pady=(8, 0))
        bottom = ttk.Frame(pad)
        bottom.pack(fill="x", pady=(8, 0))
        self.summary = ttk.Label(bottom, foreground="#444")
        self.summary.pack(side="left")
        ttk.Button(bottom, text="Close", command=self.close).pack(side="right")
        self.apply_btn = ttk.Button(bottom, text="Flatten", command=self.apply)
        self.apply_btn.pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.load()
        self.grab_set()

    def load(self):
        self.cands = candidates(self.root_dir)
        self.chosen = {c["rel"] for c in self.cands if c["suggested"]}
        self.tv.delete(*self.tv.get_children())
        for c in self.cands:
            self.tv.insert("", "end", iid=c["rel"], values=(
                "✓" if c["rel"] in self.chosen else "", c["rel"], c["tracks"], c["target"]))
        self._summary()

    def _summary(self):
        if not self.cands:
            self.summary.configure(text="No release folders inside your genre folders.")
            self.apply_btn.configure(state="disabled")
            return
        tracks = sum(c["tracks"] for c in self.cands if c["rel"] in self.chosen)
        self.summary.configure(text=f"{len(self.chosen)} of {len(self.cands)} folders "
                                    f"ticked · {tracks} tracks would move")
        self.apply_btn.configure(state="normal" if self.chosen and not self.busy
                                 else "disabled")

    def _set(self, rel, on):
        if on:
            self.chosen.add(rel)
        else:
            self.chosen.discard(rel)
        if self.tv.exists(rel):
            self.tv.set(rel, "pick", "✓" if on else "")

    def _click(self, e):
        if self.tv.identify_region(e.x, e.y) != "cell" or self.tv.identify_column(e.x) != "#1":
            return
        rel = self.tv.identify_row(e.y)
        if rel:
            self._set(rel, rel not in self.chosen)
            self._summary()

    def toggle_selected(self):
        sel = self.tv.selection()
        if sel:
            on = any(r not in self.chosen for r in sel)
            for r in sel:
                self._set(r, on)
            self._summary()
        return "break"

    def tick(self, on):
        for c in self.cands:
            self._set(c["rel"], on)
        self._summary()

    def tick_suggested(self):
        for c in self.cands:
            self._set(c["rel"], c["suggested"])
        self._summary()

    def apply(self):
        if self.busy or not self.chosen:
            return
        moves = plan_moves(self.root_dir, self.chosen, self.cands)
        if not moves:
            messagebox.showinfo(TITLE, "Those folders have no tracks left to move.", parent=self)
            return
        renamed = sum(1 for s, d, _ in moves if os.path.basename(s) != os.path.basename(d))
        if not messagebox.askyesno(
                TITLE, f"Move {len(moves)} tracks out of {len(self.chosen)} release folders?"
                       + (f"\n\n{renamed} share a name with a track already in the genre "
                          "folder and will have a number added." if renamed else "")
                       + "\n\nUndoable from History.", parent=self):
            return
        self.busy = True
        self.apply_btn.configure(state="disabled")
        self.progress.configure(maximum=len(moves), value=0)
        write_album = self.album_var.get()
        box = {}

        def progress(i, total):
            box["p"] = (i, total)

        def work():
            try:
                box["res"] = apply_moves(self.root_dir, moves, write_album,
                                         log=lambda m: None, progress=progress)
            except Exception as e:
                box["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if "p" in box:
                self.progress.configure(value=box["p"][0])
            if t.is_alive():
                self.after(150, poll)
                return
            self.busy = False
            self.progress.configure(value=0)
            if "err" in box:
                messagebox.showerror(TITLE, str(box["err"]), parent=self)
                self._summary()
                return
            _, moved, failed, albums, kept = box["res"]
            self.changed = self.changed or bool(moved)
            msg = f"Moved {moved} tracks."
            if albums:
                msg += f"\nKept the release name in {albums} empty Album tags."
            if kept:
                msg += (f"\n{kept} folders were kept because they still hold other "
                        "files, such as artwork.")
            if failed:
                msg += f"\n{failed} couldn't be moved."
            messagebox.showinfo(TITLE, msg, parent=self)
            self.load()

        self.after(150, poll)

    def close(self):
        if self.busy:
            return
        try:
            self.grab_release()
        except tk.TclError:
            pass
        parent = self._parent
        self.destroy()
        if parent is not self.app:
            try:
                parent.grab_set()
            except tk.TclError:
                pass
        if self.on_close:
            self.on_close(self.changed)

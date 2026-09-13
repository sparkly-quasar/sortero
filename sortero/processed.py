"""Sort the Processed folder - every track, with nothing left stranded.

Filing Processed used to leave tracks behind with no way forward, and the
biggest group was invisible: Platinum Notes writes a *new* file and leaves the
original where it was, so the analysed version came back looking like a
duplicate of its own original and was simply refused. It would have sat in
Processed forever.

This shows every group with its own way out:

  ready to file          filed by genre (or back to To Be Processed if still
                         unanalysed)
  no genre to go on      placed by hand, one at a time
  already in the library replace the old copy, keep it, or keep both
"""
import collections, os, shutil, threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import importer, library, membership, playlists, review, ui
from .dupes import ident, _sig
from .journal import Journal, prune_empty
from .library import PROTECTED
from .organize import QUARANTINE, target_filename
from .tagio import Track

TITLE = "Sort the Processed folder"
LOSSLESS = {".flac", ".wav", ".aiff", ".aif"}
ACTIONS = {"replace": "Replace library copy", "keep": "Keep library copy",
           "both": "Keep both"}
CYCLE = ["replace", "keep", "both"]
CARRY_TAGS = ("genre", "album", "grouping")


def _clock(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def _unique(path):
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        n += 1
        path = f"{stem} ({n}){ext}"
    return path


# ------------------------------------------------------------------ planning
def default_action(new, old, identical):
    """What to do with a fresh copy of a track already in the library, and why."""
    if identical:
        return "keep", "identical audio - nothing new"
    if new.duration and old.duration and abs(new.duration - old.duration) > 3:
        return "both", (f"different length ({_clock(old.duration)} vs "
                        f"{_clock(new.duration)}) - may be another edit")
    if new.ext in LOSSLESS and old.ext not in LOSSLESS:
        return "replace", f"new copy is lossless ({new.ext[1:]} vs {old.ext[1:]})"
    if new.key and not old.key:
        return "replace", "new copy has key data"
    return "replace", "the version you just processed"


def plan(root, recs, progress=None):
    """Returns (importer results, duplicate rows).

    Duplicates are matched against the library proper only - not staging or
    quarantine - so 'replace' never touches a file that isn't really filed.
    """
    folder = os.path.join(root, importer.PROCESSED)
    known = [r for r in importer.library_excluding(recs, root, importer.PROCESSED)
             if not r.protected]
    res = importer.plan(root, [folder], known, progress=progress, hold_unsorted=True)

    by_id = collections.defaultdict(list)
    by_size = collections.defaultdict(list)
    for r in known:
        by_id[ident(r)].append(r)
        if r.size:
            by_size[r.size].append(r)

    dups = []
    for x in res:
        if x["action"] != "duplicate":
            continue
        new, old, identical = x["rec"], None, False
        if new.size and by_size.get(new.size):
            sig = _sig(new.path, new.size)
            for c in by_size[new.size]:
                if _sig(c.path, c.size) == sig:
                    old, identical = c, True
                    break
        if old is None:
            cands = by_id.get(ident(new)) or []
            old = cands[0] if cands else None
        if old is None:
            continue
        action, why = default_action(new, old, identical)
        dups.append({"new": new, "old": old, "identical": identical,
                     "action": action, "why": why})
    return res, dups


# ----------------------------------------------------------------- resolving
def resolve(root, rows, log=print, progress=None):
    """rows: [(new Rec, library Rec, action)]. Returns (journal, Counter, failed).

    replace  the new copy moves into the old copy's folder, playlists that
             pointed at the old copy follow it, and the old copy goes to
             _Quarantine/replaced
    keep     the new copy goes to _Quarantine/duplicates
    both     the new copy is filed next to the old one
    Nothing is deleted. Tags the library copy had and the new copy lacks
    (genre, album, energy) are carried over.
    """
    j = Journal("resolve-duplicates", root)
    qdir = os.path.join(root, QUARANTINE)
    remap, restored = {}, []
    counts, failed = collections.Counter(), 0
    total = len(rows) or 1
    for i, (new, old, action) in enumerate(rows):
        if progress and i % 5 == 0:
            progress(i, total)
        if not os.path.exists(new.path):
            failed += 1
            continue
        try:
            if action == "keep":
                dest = _unique(os.path.join(qdir, "duplicates", os.path.basename(new.path)))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(new.path, dest)
                j.moved(new.path, dest)
                counts[action] += 1
                continue

            if old is None or not os.path.exists(old.path):
                failed += 1
                continue
            source_of_tags = old.path
            if action == "replace":
                gone = _unique(os.path.join(
                    qdir, "replaced", os.path.relpath(old.path, root).replace(os.sep, "__")))
                os.makedirs(os.path.dirname(gone), exist_ok=True)
                shutil.move(old.path, gone)
                j.moved(old.path, gone)
                source_of_tags = gone

            dest = _unique(os.path.join(os.path.dirname(old.path), target_filename(new)))
            shutil.move(new.path, dest)
            j.moved(new.path, dest)

            t_old, t = Track(source_of_tags), Track(dest)
            if t_old.ok and t.ok:
                changes = {}
                for field in CARRY_TAGS:
                    value = t_old.get(field)
                    if value and not (t.get(field) or "").strip():
                        t.set(field, value)
                        changes[field] = {"old": None, "new": value}
                if changes and t.save():
                    j.tagged(dest, changes)

            if action == "replace":
                remap[old.path] = dest
            owed = membership.claim(new)
            for name in owed:
                review._append_playlist(root, name, dest, j)
            if owed or membership.claim_genre(new):
                restored.append(new)
            counts[action] += 1
        except Exception as e:
            failed += 1
            log(f"  ! {os.path.basename(new.path)}: {e}")
    if remap:
        playlists.remap(root, remap, journal=j)
    if restored:
        membership.release(restored)
    prune_empty(root, keep=PROTECTED)
    if progress:
        progress(total, total)
    path = j.save()
    log(f"resolved {sum(counts.values())} duplicates ({failed} failed) | journal: {path}")
    return path, counts, failed


# -------------------------------------------------------------------- dialog
class ProcessedDialog(tk.Toplevel):
    def __init__(self, parent, app, on_close=None):
        super().__init__(parent)
        self.app, self._parent, self.on_close = app, parent, on_close
        self.root_dir = app.root_dir.get()
        self.changed = False
        self.busy = False
        self._first = True
        self.res, self.dups, self._btns = [], [], []
        self.dtv = None

        self.title(TITLE)
        self.geometry("1020x780")
        self.minsize(880, 620)
        self.transient(parent)

        pad = ttk.Frame(self, padding=16)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text=TITLE, font=ui.TITLE).pack(anchor="w")
        self.status = ttk.Label(pad, style="Muted.TLabel", wraplength=960, justify="left",
                                text="Reading Processed…")
        self.status.pack(anchor="w", pady=(2, 6))
        self.progress = ttk.Progressbar(pad)
        self.progress.pack(fill="x", pady=(0, 8))
        self.body = ttk.Frame(pad)
        self.body.pack(fill="both", expand=True)
        bottom = ttk.Frame(pad)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Close", command=self.close).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.grab_set()
        self.after(50, self.refresh)

    # -- plumbing ----------------------------------------------------------
    def _run(self, fn, done, label):
        if self.busy:
            return
        self.busy = True
        self.status.configure(text=label + "…")
        for b in self._btns:
            try:
                b.configure(state="disabled")
            except tk.TclError:
                pass
        box = {}

        def progress(i, total):
            box["p"] = (i, total)

        def work():
            try:
                box["res"] = fn(progress)
            except Exception as e:
                box["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if "p" in box:
                i, total = box["p"]
                self.progress.configure(maximum=max(total, 1), value=i)
            if t.is_alive():
                self.after(150, poll)
                return
            self.busy = False
            self.progress.configure(value=0)
            if "err" in box:
                messagebox.showerror(TITLE, str(box["err"]), parent=self)
                self.render()
                return
            done(box.get("res"))

        self.after(150, poll)

    def refresh(self):
        # The first look can use the scan the main window already has; after
        # anything moves, read the library again.
        cached = self.app.recs if (self._first and self.app.recs) else None
        self._first = False
        root = self.root_dir

        def work(progress):
            recs = cached if cached is not None else library.scan(root, progress=progress)
            return plan(root, recs, progress=progress)

        def done(out):
            self.res, self.dups = out
            self.render()

        self._run(work, done, "Reading Processed")

    # -- layout ------------------------------------------------------------
    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        self._btns, self.dtv = [], None
        res = self.res
        if not res:
            self.status.configure(text="Processed is empty — everything in it has been dealt with.")
            ttk.Label(self.body, text="Nothing left to sort.",
                      font=ui.HEADING).pack(anchor="w", pady=20)
            return
        ready = [x for x in res if x["action"] in ("sort", "mix", "to-process")]
        held = [x["rec"] for x in res if x["action"] == "needs-folder"]
        self.status.configure(
            text=f"{len(res)} tracks in Processed. Each group below has its own way out, "
                 "and nothing moves until you press that group's button. Every step is "
                 "undoable from History.")

        f1 = ttk.LabelFrame(self.body, text=f"Ready to file — {len(ready)}", padding=10)
        f1.pack(fill="x", pady=(0, 8))
        if ready:
            where = collections.Counter(
                os.path.relpath(os.path.dirname(x["dest"]), self.root_dir) for x in ready)
            lines = [f"{v:4}  →  {k}" for k, v in where.most_common(8)]
            if len(where) > 8:
                lines.append(f"      … and {len(where) - 8} more folders")
            ttk.Label(f1, font=ui.MONO, justify="left",
                      text="\n".join(lines)).pack(anchor="w")
            b = ttk.Button(f1, text=f"File {len(ready)} track{'s' if len(ready) != 1 else ''}",
                           command=lambda: self.file_ready(ready))
            b.pack(anchor="w", pady=(8, 0))
            self._btns.append(b)
        else:
            ttk.Label(f1, style="Muted.TLabel", text="None — nothing Sortero can file on its own."
                      ).pack(anchor="w")

        f2 = ttk.LabelFrame(self.body, text=f"Choose a folder by hand — {len(held)}", padding=10)
        f2.pack(fill="x", pady=(0, 8))
        if held:
            row = ttk.Frame(f2)
            row.pack(fill="x")
            b = ttk.Button(row, text="Go through them one by one…",
                           command=lambda: self.review(held))
            b.pack(side="left")
            self._btns.append(b)
            ttk.Label(row, style="Muted.TLabel",
                      text="   No genre to go on — listen and pick a folder for each."
                      ).pack(side="left")
        else:
            ttk.Label(f2, style="Muted.TLabel", text="None.").pack(anchor="w")

        f3 = ttk.LabelFrame(self.body, text=f"Already in your library — {len(self.dups)}",
                            padding=10)
        f3.pack(fill="both", expand=True)
        if not self.dups:
            ttk.Label(f3, style="Muted.TLabel", text="None.").pack(anchor="w")
            return
        ttk.Label(f3, style="Muted.TLabel", wraplength=940, justify="left",
                  text="Fresh copies of tracks you already have — usually because the "
                       "analysis tool wrote a new file and left the original. Replace puts "
                       "the new copy where the old one lives, playlists follow it, and the "
                       "old copy is set aside in _Quarantine. Keep library copy sets the new "
                       "one aside instead. Keep both files the new copy next to the old. "
                       "Click an Action to change it."
                  ).pack(anchor="w", pady=(0, 6))
        tf = ttk.Frame(f3)
        tf.pack(fill="both", expand=True)
        self.dtv = ttk.Treeview(tf, columns=("action", "new", "old", "why"),
                                show="headings", height=9)
        for col, text, width, stretch in (("action", "Action", 170, False),
                                          ("new", "New copy in Processed", 300, True),
                                          ("old", "Already in library", 300, True),
                                          ("why", "Why", 230, True)):
            self.dtv.heading(col, text=text)
            self.dtv.column(col, width=width, stretch=stretch, anchor="w")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.dtv.yview)
        self.dtv.configure(yscrollcommand=sb.set)
        self.dtv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for i, d in enumerate(self.dups):
            self.dtv.insert("", "end", iid=str(i), values=(
                ACTIONS[d["action"]], os.path.basename(d["new"].path), d["old"].rel, d["why"]))
        self.dtv.bind("<ButtonRelease-1>", self._cycle)

        row = ttk.Frame(f3)
        row.pack(fill="x", pady=(8, 0))
        for label, action in (("Replace all", "replace"), ("Keep library copy for all", "keep"),
                              ("Keep both for all", "both")):
            b = ttk.Button(row, text=label, command=lambda a=action: self.set_all(a))
            b.pack(side="left", padx=(0, 6))
            self._btns.append(b)
        b = ttk.Button(row, text=f"Sort out {len(self.dups)}", command=self.resolve_dups)
        b.pack(side="right")
        self._btns.append(b)

    def _cycle(self, e):
        if self.busy or self.dtv is None:
            return
        if self.dtv.identify_region(e.x, e.y) != "cell" or self.dtv.identify_column(e.x) != "#1":
            return
        row = self.dtv.identify_row(e.y)
        if not row:
            return
        d = self.dups[int(row)]
        d["action"] = CYCLE[(CYCLE.index(d["action"]) + 1) % len(CYCLE)]
        self.dtv.set(row, "action", ACTIONS[d["action"]])

    def set_all(self, action):
        for i, d in enumerate(self.dups):
            d["action"] = action
            if self.dtv is not None:
                self.dtv.set(str(i), "action", ACTIONS[action])

    # -- actions -----------------------------------------------------------
    def _after_action(self, message):
        self.changed = True
        self.status.configure(text=message)
        self.refresh()

    def file_ready(self, ready):
        back = sum(1 for x in ready if x["action"] == "to-process")
        if not messagebox.askyesno(
                TITLE, f"File {len(ready)} tracks now?"
                       + (f"\n\n{back} still have no key and go back to 'To Be Processed'."
                          if back else "") + "\n\nUndoable from History.", parent=self):
            return
        root = self.root_dir
        self._run(lambda progress: importer.apply(root, ready, log=lambda m: None,
                                                  progress=progress),
                  lambda out: self._after_action(f"Filed {out[1]} tracks."),
                  "Filing tracks")

    def review(self, held):
        review.ReviewDialog(self, self.app, held, context="Processed",
                            on_close=self._child_closed)

    def _child_closed(self, changed):
        try:
            self.grab_set()
        except tk.TclError:
            return
        if changed:
            self.changed = True
            self.refresh()

    def resolve_dups(self):
        rows = [(d["new"], d["old"], d["action"]) for d in self.dups]
        c = collections.Counter(a for _, _, a in rows)
        parts = [f"{c[a]} × {ACTIONS[a].lower()}" for a in CYCLE if c[a]]
        if not messagebox.askyesno(
                TITLE, "Sort out these tracks?\n\n" + "\n".join(parts)
                       + "\n\nNothing is deleted — set-aside copies go to _Quarantine. "
                         "Undoable from History.", parent=self):
            return
        root = self.root_dir
        self._run(lambda progress: resolve(root, rows, log=lambda m: None, progress=progress),
                  lambda out: self._after_action(
                      f"Sorted out {sum(out[1].values())} tracks"
                      + (f"; {out[2]} couldn't be moved" if out[2] else "") + "."),
                  "Sorting out duplicates")

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

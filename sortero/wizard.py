"""First-run setup wizard.

This is a working wizard, not a tour: each step previews a real operation
against the real library and offers to run it. It runs once, then never again
unless asked for from the Help menu.

Every step is skippable, and the whole run is wrapped in a testing session by
default, so a nervous first pass can be undone in one go afterwards.
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import settings, library, auth, organize, fixtags, dupes, session, importer
from .common import human_size

TITLE = "Welcome to Sortero"


class Wizard(tk.Toplevel):
    def __init__(self, app, on_finish=None):
        super().__init__(app)
        self.app = app
        self.on_finish = on_finish
        self.title(TITLE)
        self.geometry("760x600")
        self.minsize(700, 560)
        self.transient(app)

        self.root_dir = tk.StringVar(value=settings.get("root") or "")
        self.use_testing = tk.BooleanVar(value=True)
        self.recs = None
        self.health = None
        self.busy = False
        self.step = 0

        self.body = ttk.Frame(self, padding=20)
        self.body.pack(fill="both", expand=True)

        foot = ttk.Frame(self, padding=(20, 0, 20, 14))
        foot.pack(fill="x")
        self.progress = ttk.Progressbar(foot, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 8))
        self.status = ttk.Label(foot, text="", foreground="#666")
        self.status.pack(anchor="w")

        nav = ttk.Frame(self, padding=(20, 0, 20, 16))
        nav.pack(fill="x")
        self.skip_btn = ttk.Button(nav, text="Skip setup", command=self.finish)
        self.skip_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next", command=self.next)
        self.next_btn.pack(side="right")
        self.back_btn = ttk.Button(nav, text="Back", command=self.back)
        self.back_btn.pack(side="right", padx=6)

        self.steps = [
            self._welcome, self._folder, self._scan,
            self._file_processed,
            self._tags, self._duplicates, self._organise, self._analysis,
            self._done,
        ]
        self.render()
        self.protocol("WM_DELETE_WINDOW", self.finish)
        self.grab_set()

    # ------------------------------------------------------------ plumbing
    def _run(self, fn, done, label="Working"):
        """Run work off the UI thread with the wizard's own progress bar."""
        if self.busy:
            return
        self.busy = True
        self._nav_state("disabled")
        self.status.configure(text=f"{label}…")
        box = {}

        def progress(i, total):
            box["p"] = (i, total)

        def worker():
            try:
                box["result"] = fn(progress)
            except Exception as e:                       # surfaced, not swallowed
                box["error"] = e

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        def poll():
            if "p" in box:
                i, total = box["p"]
                self.progress.configure(value=i, maximum=max(total, 1))
                self.status.configure(text=f"{label}… {i}/{total}")
            if t.is_alive():
                self.after(120, poll)
                return
            self.busy = False
            self.progress.configure(value=0)
            self._nav_state("normal")
            self.status.configure(text="")
            if "error" in box:
                messagebox.showerror(TITLE, str(box["error"]), parent=self)
                return
            done(box.get("result"))

        self.after(120, poll)

    def _nav_state(self, state):
        for b in (self.next_btn, self.back_btn, self.skip_btn):
            b.configure(state=state)
        if state == "normal":
            self.back_btn.configure(state="normal" if self.step else "disabled")

    def rescan(self, then=None):
        d = self.root_dir.get()

        def work(progress):
            recs = library.scan(d, progress=progress)
            return recs, library.health(recs)

        def done(res):
            self.recs, self.health = res
            if then:
                then()

        self._run(work, done, "Reading your collection")

    # ------------------------------------------------------------- chrome
    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _heading(self, text, sub=None):
        ttk.Label(self.body, text=text, font=("Helvetica", 18, "bold")).pack(anchor="w")
        if sub:
            ttk.Label(self.body, text=sub, foreground="#666", wraplength=690,
                      justify="left").pack(anchor="w", pady=(6, 12))

    def _action_row(self, label, command):
        row = ttk.Frame(self.body)
        row.pack(anchor="w", pady=(12, 0))
        btn = ttk.Button(row, text=label, command=command)
        btn.pack(side="left")
        note = ttk.Label(row, foreground="#666", wraplength=430, justify="left")
        note.pack(side="left", padx=10)
        return btn, note

    def _mono(self, text):
        lab = ttk.Label(self.body, text=text, font=("Menlo", 11), justify="left")
        lab.pack(anchor="w", pady=(4, 0))
        return lab

    def render(self):
        self._clear()
        self.steps[self.step]()
        self._nav_state("normal")
        last = self.step == len(self.steps) - 1
        self.next_btn.configure(text="Finish" if last else "Next")

    def back(self):
        if self.step and not self.busy:
            self.step -= 1
            self.render()

    def next(self):
        if self.busy:
            return
        if self.step == 1 and not self._valid_folder():
            messagebox.showwarning(TITLE, "Choose the folder your music lives in first.",
                                   parent=self)
            return
        if self.step == len(self.steps) - 1:
            self.finish()
            return
        self.step += 1
        self.render()
        if self.step == 2:
            self.after(120, lambda: self.rescan(self._render_scan))

    def finish(self):
        if self.busy:
            return
        settings.set("setup_complete", True)
        if self._valid_folder():
            settings.set("root", self.root_dir.get())
        self.grab_release()
        self.destroy()
        if self.on_finish:
            self.on_finish(self.root_dir.get() if self._valid_folder() else None)

    # -------------------------------------------------------------- steps
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
            ttk.Label(self.body, text="•  " + line, wraplength=690,
                      justify="left").pack(anchor="w", pady=3)

        ttk.Checkbutton(
            self.body, variable=self.use_testing,
            text="Record everything I do here as one undoable restore point"
        ).pack(anchor="w", pady=(16, 0))
        ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                  text="Recommended for a first run. You can undo the whole setup in "
                       "one go afterwards, or keep it, from the Testing menu."
                  ).pack(anchor="w", padx=22)

        ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                  text="\nEvery step previews what it would do before it does anything, "
                       "and every step can be skipped."
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
        self.folder_note = ttk.Label(self.body, foreground="#666", wraplength=690,
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
        have = [n for n in ("To Be Processed", "Processed")
                if os.path.isdir(os.path.join(d, n))]
        msg = f"Found: {d}"
        if have:
            msg += ("\n\nSortero can see your " + " and ".join(f"'{h}'" for h in have) +
                    " folder. Those are your analysis staging area and are never "
                    "reorganised.")
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
        self.scan_box = ttk.Frame(self.body)
        self.scan_box.pack(anchor="w", fill="x")

    def _render_scan(self):
        if not self.scan_box.winfo_exists():
            return
        for w in self.scan_box.winfo_children():
            w.destroy()
        h = self.health
        if not h:
            return
        if self.use_testing.get() and not session.active():
            try:
                sess = session.start(self.root_dir.get(), "Setup wizard")
                session.export(sess)
                self.app.refresh_banner()
            except Exception:
                pass
        text = (f"{h['total']:,} tracks · {human_size(h['bytes'])}\n\n"
                f"{h['pct_analyzed']:.0f}%  have key and BPM\n"
                f"{h['pct_genre']:.0f}%  have a usable genre\n"
                f"{h['pct_energy']:.0f}%  have an energy rating\n\n"
                f"{len(h['needs_analysis'])} tracks need analysing for key and BPM\n"
                f"{len(h['no_genre'])} need a genre — most can be inferred")
        ttk.Label(self.scan_box, text=text, font=("Menlo", 11),
                  justify="left").pack(anchor="w")
        ttk.Label(self.scan_box, foreground="#666", wraplength=690, justify="left",
                  text="\nThe next steps will offer to fix these. Press Next."
                  ).pack(anchor="w")

    # -- step: file anything already analysed --------------------------------
    def _file_processed(self):
        self._heading("Anything already analysed?",
                      "If you've run tracks through your analysis tool and saved them "
                      "into 'Processed', they get filed by genre now — before anything "
                      "else moves, so they land in the right place.")
        root = self.root_dir.get()
        folder = os.path.join(root, importer.PROCESSED)
        if not os.path.isdir(folder) or not importer.gather([folder]):
            self._mono("'Processed' is empty — nothing waiting to be filed.")
            ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                      text="\nThat's expected on a first run. Later steps will stage "
                           "tracks that need analysing; once you've run them, come back "
                           "to this step (Help → Setup Wizard) or use Import → \"Sort "
                           "the 'Processed' folder\"."
                      ).pack(anchor="w", pady=(8, 0))
            return

        self.proc_note = self._mono("Checking…")
        self.proc_btn, _ = self._action_row("File them into the library",
                                            self._apply_processed)
        self.proc_btn.configure(state="disabled")

        def work(progress):
            known = importer.library_excluding(self.recs or [], root, importer.PROCESSED)
            return importer.plan(root, [folder], known, progress=progress)

        def done(res):
            self._proc_results = res
            c = importer.summarize(res)
            filed = sum(1 for x in res if x["dest"])
            if not filed:
                self.proc_note.configure(
                    text="Nothing new to file — " +
                         ", ".join(f"{v} {k}" for k, v in c.most_common()))
                return
            self.proc_note.configure(
                text=f"{filed} tracks ready to file:\n" +
                     "\n".join(f"  {v:5}  {k}" for k, v in c.most_common()))
            self.proc_btn.configure(state="normal")

        self._run(work, done, "Reading 'Processed'")

    def _apply_processed(self):
        res = getattr(self, "_proc_results", None)
        if not res:
            return
        root = self.root_dir.get()

        def work(progress):
            return importer.apply(root, res, log=lambda m: None, progress=progress)

        def done(out):
            _, n = out
            self.proc_btn.configure(state="disabled")
            self.proc_note.configure(text=f"Done — {n} tracks filed into the library.")
            self.app.refresh_banner()
            self.rescan()

        self._run(work, done, "Filing analysed tracks")

    # -- step: tags ---------------------------------------------------------
    def _tags(self):
        self._heading("Clean up the tags",
                      "Strips download-site spam out of Genre and Comment, fills in "
                      "missing Artist/Title from filenames, tidies Genre into a "
                      "consistent set, and moves Mixed In Key's energy rating into a "
                      "field your DJ software can actually sort on.")
        if not self.recs:
            ttk.Label(self.body, text="Nothing scanned yet.").pack(anchor="w")
            return
        self.tags_note = self._mono("Checking…")
        self.tags_btn, _ = self._action_row("Apply these fixes", self._apply_tags)
        self.tags_btn.configure(state="disabled")

        def work(progress):
            return fixtags.plan(self.recs, set(fixtags.FIXES))

        def done(changes):
            self._tag_changes = changes
            if not changes:
                self.tags_note.configure(text="Nothing to fix here — tags look fine.")
                return
            c = fixtags.summarize(changes)
            lines = "\n".join(f"  {v:5}  {k}" for k, v in c.most_common())
            self.tags_note.configure(text=f"{len(changes)} files would change:\n{lines}")
            self.tags_btn.configure(state="normal")

        self._run(work, done, "Checking tags")

    def _apply_tags(self):
        changes = getattr(self, "_tag_changes", None)
        if not changes:
            return
        root = self.root_dir.get()

        def work(progress):
            return fixtags.apply(root, changes, log=lambda m: None, progress=progress)

        def done(res):
            _, n, failed = res
            self.tags_btn.configure(state="disabled")
            self.tags_note.configure(
                text=f"Done — {n} files updated" + (f", {failed} failed." if failed else "."))
            self.app.refresh_banner()
            self.rescan()

        self._run(work, done, "Writing tags")

    # -- step: duplicates ---------------------------------------------------
    def _duplicates(self):
        self._heading("Collapse duplicate copies",
                      "Finds tracks you have more than once. Different remixes are "
                      "never treated as duplicates. Extra copies move to _Quarantine "
                      "— nothing is deleted.")
        if not self.recs:
            ttk.Label(self.body, text="Nothing scanned yet.").pack(anchor="w")
            return
        self.dup_note = self._mono("Scanning for duplicates…")
        self.dup_btn, _ = self._action_row("Move extra copies to _Quarantine",
                                           self._apply_dupes)
        self.dup_btn.configure(state="disabled")

        def work(progress):
            return dupes.find(self.recs, progress=progress)

        def done(found):
            self._found = found
            groups = found["exact"] + found["likely"]
            extra = sum(len(g) - 1 for g in groups)
            if not extra:
                self.dup_note.configure(text="No duplicates found.")
                return
            self.dup_note.configure(
                text=f"{len(found['exact'])} groups with identical audio\n"
                     f"{len(found['likely'])} groups that look like the same track\n"
                     f"{extra} extra files · {human_size(dupes.reclaimable(groups))} reclaimable")
            self.dup_btn.configure(state="normal")

        self._run(work, done, "Hashing audio")

    def _apply_dupes(self):
        found = getattr(self, "_found", None)
        if not found:
            return
        root = self.root_dir.get()
        groups = found["exact"] + found["likely"]

        def work(progress):
            return dupes.quarantine(root, groups, log=lambda m: None)

        def done(res):
            _, n = res
            self.dup_btn.configure(state="disabled")
            self.dup_note.configure(text=f"Done — {n} extra copies moved to _Quarantine.")
            self.app.refresh_banner()
            self.rescan()

        self._run(work, done, "Quarantining duplicates")

    # -- step: organise -----------------------------------------------------
    def _organise(self):
        self._heading("File everything by genre",
                      "Moves each track to Tracks/<Genre>/Artist - Title, and writes "
                      "every folder you have now — vibe imports, gig sets — to "
                      "_Playlists as an .m3u8 pointing at that one file. Your "
                      "staging folders are left alone.")
        if not self.recs:
            ttk.Label(self.body, text="Nothing scanned yet.").pack(anchor="w")
            return
        self.org_note = self._mono("Working out the plan…")
        self.org_btn, _ = self._action_row("Organise my collection", self._apply_organise)
        self.org_btn.configure(state="disabled")

        root = self.root_dir.get()

        def work(progress):
            canon = dupes.canonical_map(dupes.find(self.recs, progress=progress))
            return organize.plan(root, self.recs, keep_sets=False,
                                 route_unanalyzed=False, canonical=canon)

        def done(res):
            self._org_plan = res
            moves, pls, st = res
            if not moves:
                self.org_note.configure(text="Everything is already in place.")
                return
            import collections
            cats = collections.Counter(
                os.path.relpath(d, root).split(os.sep)[0] for _, d in moves)
            lines = "\n".join(f"  {v:5}  → {k}/" for k, v in cats.most_common())
            self.org_note.configure(
                text=f"{len(moves)} files would move:\n{lines}\n"
                     f"  {len(pls):5}  playlists written to _Playlists")
            self.org_btn.configure(state="normal")

        self._run(work, done, "Planning the move")

    def _apply_organise(self):
        plan = getattr(self, "_org_plan", None)
        if not plan:
            return
        moves, pls, _ = plan
        root = self.root_dir.get()
        if not messagebox.askyesno(
                TITLE, f"Move {len(moves)} files into the new structure?\n\n"
                       f"{len(pls)} playlists are written first, so nothing you "
                       "curated is lost.", parent=self):
            return

        def work(progress):
            return organize.apply(root, moves, pls, log=lambda m: None, progress=progress)

        def done(_):
            self.org_btn.configure(state="disabled")
            self.org_note.configure(text=f"Done — {len(moves)} files filed, "
                                         f"{len(pls)} playlists written.")
            self.app.refresh_banner()
            self.rescan()

        self._run(work, done, "Moving files")

    # -- step: analysis -----------------------------------------------------
    def _analysis(self):
        self._heading("Tracks that need Mixed In Key",
                      "Sortero works out genre, artist and title itself, but key, BPM "
                      "and energy come from an analysis tool — Mixed In Key, rekordbox, "
                      "Mixxx, whichever you use. Sortero can gather the tracks that need "
                      "it into 'To Be Processed' ready to run.")
        ttk.Label(self.body, foreground="#8a5a00", font=("Helvetica", 13, "bold"),
                  text="If you use Platinum Notes: run it BEFORE analysing"
                  ).pack(anchor="w", pady=(0, 2))
        ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                  text="It re-encodes the audio, so mastering after analysing leaves the "
                       "tags describing a file that no longer exists. Ignore this if you "
                       "don't use it — Sortero only needs the key and BPM tags, whatever "
                       "wrote them."
                  ).pack(anchor="w", pady=(0, 10))
        if not self.recs:
            ttk.Label(self.body, text="Nothing scanned yet.").pack(anchor="w")
            return
        pending = [r for r in self.recs
                   if not r.protected and not r.analyzed and not r.is_recording
                   and not (r.duration and r.duration >= organize.MIX_MIN_SECONDS)]
        self._pending = pending
        self._mono(f"{len(pending)} tracks are missing key or BPM.\n"
                   "(Your own set recordings are excluded.)")
        self.an_btn, _ = self._action_row("Stage them in 'To Be Processed'",
                                          self._apply_stage)
        if not pending:
            self.an_btn.configure(state="disabled")
        ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                  text="\nAfterwards: analyse them, save the results into 'Processed', "
                       "then use Import → \"Sort the 'Processed' folder\". Tracks staged "
                       "out of a set rejoin its playlist automatically when they come "
                       "back."
                  ).pack(anchor="w", pady=(10, 0))

    def _apply_stage(self):
        pending = getattr(self, "_pending", None)
        if not pending:
            return
        root = self.root_dir.get()
        all_recs = self.recs

        def work(progress):
            return organize.stage_for_analysis(root, pending, all_recs=all_recs,
                                               log=lambda m: None, progress=progress)

        def done(res):
            _, n = res
            self.an_btn.configure(state="disabled")
            messagebox.showinfo(TITLE, f"Staged {n} tracks in 'To Be Processed'.",
                                parent=self)
            self.app.refresh_banner()
            self.rescan()

        self._run(work, done, "Staging tracks")

    # -- step: done ---------------------------------------------------------
    def _done(self):
        self._heading("You're set up",
                      "Everything from here on lives in the tabs behind this window.")
        for pid in ("tidal", "spotify"):
            cfg = auth.PROVIDERS[pid]
            state = "connected" if auth.is_connected(pid) else "not connected"
            ttk.Label(self.body, text=f"•  {cfg['label']}: {state}").pack(anchor="w", pady=2)
        ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                  text="Connect these from the Playlists tab to rebuild a Spotify or "
                       "TIDAL playlist against the files you own. Pasting a tracklist "
                       "works without an account."
                  ).pack(anchor="w", pady=(4, 12))

        if session.active():
            s = session.summary()
            ttk.Label(self.body, font=("Menlo", 11), justify="left",
                      text=f"Testing session is recording:\n"
                           f"  {s['moves']} moves, {s['tags']} tag edits"
                      ).pack(anchor="w")
            ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                      text="Use the Testing menu to keep it all, or undo the entire "
                           "setup in one go. Until you choose, the restore point stays."
                      ).pack(anchor="w", pady=(4, 0))
        staged = 0
        try:
            folder = os.path.join(self.root_dir.get(), importer.TO_PROCESS)
            staged = len(importer.gather([folder])) if os.path.isdir(folder) else 0
        except Exception:
            pass
        if staged:
            ttk.Label(self.body, foreground="#8a5a00", font=("Helvetica", 13, "bold"),
                      text=f"{staged} tracks are waiting in 'To Be Processed'"
                      ).pack(anchor="w", pady=(14, 2))
            ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                      text="Run them through your analysis tool and save the results "
                           "into 'Processed'. Then come back — Sortero shows a bar "
                           "offering to file them, or use Import → \"Sort the "
                           "'Processed' folder\". The wizard's first steps will also "
                           "pick them up if you run it again."
                      ).pack(anchor="w")
        ttk.Label(self.body, foreground="#666", wraplength=690, justify="left",
                  text="\nReopen this wizard any time from Help → Setup Wizard."
                  ).pack(anchor="w", pady=(10, 0))


def maybe_run(app, on_finish=None):
    """Show the wizard only if setup hasn't been completed."""
    if settings.get("setup_complete"):
        return None
    return Wizard(app, on_finish=on_finish)

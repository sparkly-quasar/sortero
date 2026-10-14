"""Shrink to MP3: lossless tracks become MP3 320, tags kept, originals archived.

Opened from Library's More menu, for the tracks selected there or the whole
collection. It lists what would change and how much space it saves before
anything happens. Afterwards the originals sit in an archive outside the
collection; this screen lists those archives and deletes one when asked.
"""
import os
from tkinter import ttk, filedialog, messagebox, simpledialog

from .. import mixxx, settings, shrink, ui
from ..common import human_size
from ..gig import ffmpeg
from .base import Screen, APP
from .gig_set import short

FORMATS = {"flac": "FLAC", "alac": "Apple Lossless", "wav": "WAV", "aiff": "AIFF"}


def _format(info):
    name = FORMATS.get(info.format, info.format.upper())
    depth = f"{info.depth}-bit " if info.depth else ""
    return f"{name}, {depth}{info.rate / 1000:g} kHz"


class ShrinkScreen(Screen):
    title = "Shrink to MP3"
    crumb = ("Library", "library")
    nav = "library"
    summary = ("Lossless tracks become MP3 320, with every tag kept. The originals go to "
               "an archive you can delete once you're happy.")
    details = ("FLAC, WAV, AIFF and Apple Lossless tracks, at any sample rate or bit depth, "
               "become MP3 320 kbps at 44.1 kHz, beside where they were and under the same "
               "name. Every tag comes across: artist, title, key, BPM, energy, comments, "
               "Mixed In Key's cue points and beat grid, and the artwork as it is. MP3s, "
               "and other lossy files like AAC, are left alone: encoding them again would "
               "lose quality and often make them bigger.\n\n"
               "Each MP3 is checked before its original moves: it has to decode start to "
               "finish, be as long as the original, and read back the same artist, title, "
               "key and BPM. Your Sortero playlists follow the MP3s, and so does Mixxx's "
               "library, cues and beat grid included, if Mixxx is closed.\n\n"
               "rekordbox's library still points at the originals: in rekordbox, the "
               "tracks show as missing until you relocate them to the MP3s, and "
               "relocating keeps what rekordbox knows about each track. Check one track's "
               "cues afterwards: an MP3 can start a few milliseconds later than its "
               "original in some players. Mixed In Key's cue points travel inside the "
               "files either way.\n\n"
               "It's one entry in History, so it can all be undone, until you delete the "
               "archived originals.")

    def build(self):
        info = ttk.Frame(self)
        info.pack(fill="x", pady=(0, ui.GAP))
        self.scope_label = ttk.Label(info)
        self.scope_label.pack(side="left")
        self.whole_link = ui.link(info, "Use the whole collection", self.use_whole)
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.archive_label = ttk.Label(row, style="Muted.TLabel")
        self.archive_label.pack(side="left")
        ui.link(row, "Change…", self.choose_archive).pack(side="left", padx=(ui.GAP, 0))
        self.mixxx_label = ui.WrapLabel(self, style="Muted.TLabel")
        self.mixxx_label.pack(fill="x", pady=(2, ui.SECTION))

        self.result = ui.WrapLabel(self, style="Heading.TLabel")
        self.result.pack(fill="x")
        self.result_note = ui.WrapLabel(self, style="Muted.TLabel")
        self.result_note.pack(fill="x", pady=(2, ui.GAP))

        self.archives_frame = ttk.Frame(self)
        self.archives_frame.pack(side="bottom", fill="x", pady=(ui.GAP, 0))
        f, self.tv = ui.tree(self, [("track", "Track", 380), ("format", "Now", 170),
                                    ("size", "Size now", 90), ("after", "As MP3", 110),
                                    ("note", "", 220)], height=10, selectmode="none")
        f.pack(fill="both", expand=True)
        self.tv.tag_configure("skip", foreground=ui.c("faint"))
        self.tv.tag_configure("failed", foreground=ui.c("warn"))
        self.bar.set_more([("Show the archive folder", self.show_archive)])
        self.recs = None            # chosen tracks; None means the whole collection
        self.items = []
        self.planned_for = None

    # ------------------------------------------------------------ opening
    def open_for(self, recs=None):
        """Called by Library: the selected tracks, or None for the whole collection."""
        self.recs = list(recs) if recs else None
        self.items, self.planned_for = [], None
        self.result.configure(text="")
        self.result_note.configure(text="")
        self.app.show(self.key)

    def use_whole(self):
        self.open_for(None)

    def shown(self):
        root = self.app.root_dir.get()
        n = len(self.recs) if self.recs else 0
        self.scope_label.configure(text=f"From the {ui.plural(n, 'track')} you selected."
                                   if self.recs else "From the whole collection.")
        if self.recs:
            self.whole_link.pack(side="left", padx=(ui.GAP, 0))
        else:
            self.whole_link.pack_forget()
        try:
            self.archive_label.configure(text="Originals go to "
                                         + short(self._archive(root)))
        except shrink.ArchiveError as e:
            self.archive_label.configure(text=str(e))
        db = mixxx.find_db()
        self.mixxx_label.configure(text=(
            "Mixxx's library will be pointed at the MP3s, cues and beat grids kept. "
            "Quit Mixxx before you start." if db else
            "Mixxx isn't on this computer, so there's no Mixxx library to update."))
        self._draw_archives()
        key = (root, tuple(r.path for r in self.recs) if self.recs else None)
        if self.planned_for != key and root:
            self.planned_for = key
            self.preview()
        else:
            self._sync()

    # ------------------------------------------------------------ preview
    def _archive(self, root):
        return shrink.archive_dir(root, settings.get("shrink_archive"))

    def preview(self):
        root = self.app.require_root()
        if not root or self.need_scan():
            return
        try:
            archive = self._archive(root)
            ffmpeg.require("ffprobe")
        except (shrink.ArchiveError, ffmpeg.Missing) as e:
            messagebox.showwarning(APP, str(e))
            return
        recs = self.recs or self.app.recs

        def work(progress, log):
            return shrink.plan(root, recs, archive, progress)

        def done(items):
            self.items = items
            self._fill()

        self.result.configure(text="Finding lossless tracks…")
        self.app.task.run(work, done, "Finding lossless tracks")

    def _fill(self):
        self.tv.delete(*self.tv.get_children())
        for i, it in enumerate(sorted(self.items, key=lambda x: (x.status != shrink.CONVERT,
                                                                 x.rel.lower()))):
            note = {shrink.SKIP: it.why, shrink.FAILED: it.why,
                    shrink.DONE: "Done"}.get(it.status, "")
            tags = {shrink.SKIP: ("skip",), shrink.FAILED: ("failed",)}.get(it.status, ())
            self.tv.insert("", "end", iid=str(i), values=(
                it.rel, _format(it.info), human_size(it.size),
                f"about {human_size(it.estimate)}" if it.status == shrink.CONVERT else "",
                note), tags=tags)
        n, now, after = shrink.totals(self.items)
        skipped = sum(1 for it in self.items if it.status == shrink.SKIP)
        if not self.items:
            self.result.configure(text="No lossless tracks here. Nothing to shrink.")
            self.result_note.configure(text="")
        elif n:
            self.result.configure(text=f"{ui.plural(n, 'lossless track')}: "
                                       f"{human_size(now)} now, about {human_size(after)} "
                                       f"as MP3. That saves about {human_size(now - after)}.")
            self.result_note.configure(
                text=(f"{skipped:,} more can't be done; the list says why. " if skipped else "")
                + "Nothing changes until you start.")
        else:
            self.result.configure(text="None of these can be shrunk; the list says why.")
            self.result_note.configure(text="")
        self._sync()

    def _sync(self):
        n = shrink.totals(self.items)[0]
        self.bar.set_primary(f"Convert {ui.plural(n, 'track')}…" if n else "Convert",
                             self.start, state="normal" if n else "disabled")

    # ------------------------------------------------------------ converting
    def start(self):
        root = self.app.require_root()
        n, now, after = shrink.totals(self.items)
        if not root or not n:
            return
        archive = self._archive(root)
        db = mixxx.find_db()
        if db and mixxx.running():
            if not messagebox.askyesno(APP, "Mixxx is open, so its library can't be "
                                            "updated: it would put the old paths back when "
                                            "it closes.\n\nQuit Mixxx first, or go ahead "
                                            "and leave Mixxx pointing at the originals?"):
                return
            db = None
        msg = (f"Convert {ui.plural(n, 'track')} to MP3 320?\n\n"
               f"They take {human_size(now)} now and about {human_size(after)} as MP3.\n\n"
               f"Each original moves to {short(archive)} once its MP3 has passed its "
               "check. Your Sortero playlists follow"
               + (", and so does Mixxx." if db else ".")
               + "\n\nrekordbox will show these tracks as missing until you relocate them "
                 "to the MP3s in rekordbox.\n\nAll of it can be undone from History.")
        if not messagebox.askyesno(APP, msg):
            return
        items = self.items

        def work(progress, log):
            return shrink.run(root, items, archive, mixxx_db=db, progress=progress, log=log)

        def done(res):
            self._fill()
            done_n, failed = len(res.done), len(res.failed)
            self.result.configure(text=f"Converted {ui.plural(done_n, 'track')}, saving "
                                       f"{human_size(max(res.saved, 0))}." if done_n else
                                  "Nothing was converted.")
            notes = []
            if failed:
                notes.append(f"{ui.plural(failed, 'track')} couldn't be converted and "
                             "were left as they were; the list says why.")
            if res.playlists:
                notes.append(f"{ui.plural(res.playlists, 'playlist entry', 'playlist entries')}"
                             " now point at the MP3s.")
            if res.mixxx:
                notes.append(f"{ui.plural(res.mixxx, 'track')} relinked in Mixxx.")
            notes += res.mixxx_notes[:3]
            if done_n:
                notes.append("In rekordbox, relocate the missing tracks to the MP3s. The "
                             "originals are in the archive below until you delete them.")
            self.result_note.configure(text=" ".join(notes))
            self.items = [it for it in self.items if it.status != shrink.DONE]
            self.planned_for = None
            self._draw_archives()
            self._sync()
            self.app.changed()

        self.app.task.run(work, done, "Converting to MP3")

    # ------------------------------------------------------------ archives
    def _draw_archives(self):
        for w in self.archives_frame.winfo_children():
            w.destroy()
        from ..gig.checklist import when
        for arc in shrink.archives()[:3]:
            ui.Card(self.archives_frame,
                    f"Archived originals: {ui.plural(len(arc.files), 'file')}, "
                    f"{human_size(arc.bytes)}",
                    f"Moved to {short(arc.folder)} ({when(arc.when)}). Their MP3s are in "
                    "your collection. Delete the originals once you're happy with the "
                    "MP3s; after that, this can't be undone.",
                    "Delete them…", lambda a=arc: self.delete(a)).pack(fill="x", pady=(0, 4))

    def delete(self, arc):
        typed = simpledialog.askstring(
            APP, f"Delete {ui.plural(len(arc.files), 'original')} ({human_size(arc.bytes)}) "
                 f"from {short(arc.folder)} for good?\n\nThis can't be undone, and the "
                 "History entry for it can't be undone after this either. An original "
                 "whose MP3 is missing is kept.\n\nType DELETE to delete them.",
            parent=self)
        if (typed or "").strip().upper() != "DELETE":
            return

        def work(progress, log):
            return shrink.delete_archive(arc, log=log)

        def done(res):
            deleted, kept, freed = res
            self.result.configure(text=f"Deleted {ui.plural(deleted, 'original')}, freeing "
                                       f"{human_size(freed)}.")
            self.result_note.configure(text=(f"{ui.plural(kept, 'original')} kept, because "
                                             "its MP3 wasn't there." if kept else ""))
            self._draw_archives()
            self.app.screens["history"].refresh()

        self.app.task.run(work, done, "Deleting archived originals")

    def choose_archive(self):
        root = self.app.require_root()
        if not root:
            return
        d = filedialog.askdirectory(title="Choose where the originals go")
        if not d:
            return
        try:
            shrink.archive_dir(root, d)
        except shrink.ArchiveError as e:
            messagebox.showwarning(APP, str(e))
            return
        settings.set("shrink_archive", d)
        self.planned_for = None
        self.shown()

    def show_archive(self):
        from .. import paths
        root = self.app.require_root()
        if not root:
            return
        arc = self._archive(root)
        if os.path.isdir(arc):
            paths.reveal(arc)
        else:
            messagebox.showinfo(APP, f"Nothing archived yet. Originals will go to:\n\n{arc}")

"""Your playlists: every playlist Sortero can see, in one list.

Sortero's own (_Playlists), playlist files anywhere in the collection, the
watched folders' and whatever was added in Gig prep (gig/sources.py). Each
shows how many tracks it has and how many of those files aren't there.
Nothing here changes a playlist by itself. More holds the two tools that
write Sortero's own: Fix broken links and Rebuild playlists from folders.
"""
import os
from tkinter import messagebox

from .. import paths, ui
from ..gig import sources
from .base import Screen, APP

KINDS = {sources.SORTERO: "Sortero", sources.COLLECTION: "In your collection",
         sources.WATCHED: "Watched folder", sources.M3U: "Playlist file",
         sources.FOLDER: "Folder of tracks", sources.REKORDBOX: "rekordbox",
         sources.MIXXX: "Mixxx"}


def kind_label(src):
    return KINDS.get(src.kind) or src.kind.capitalize()


def where_label(src, root):
    """Where it lives, as short as it can be said."""
    if src.kind == sources.SORTERO:
        return "_Playlists"
    path = src.where
    if root and path.startswith(os.path.join(root, "")):
        return os.path.relpath(path, root)
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home + os.sep) else path


class YourPlaylistsScreen(Screen):
    title = "Your playlists"
    summary = "Every playlist Sortero can see, and whether its tracks are all there."
    details = ("Listed here: Sortero's own playlists in _Playlists, any .m3u or .m3u8 "
               "file inside your collection, and the playlists you've added in Gig prep "
               "from other apps, such as rekordbox or Mixxx, or from watched folders. "
               "Add more from Gig prep with Add playlists….\n\n"
               "Missing counts tracks whose file isn't where the playlist says. Fix "
               "broken links re-links Sortero's own playlists by matching artist and "
               "title, and Rebuild playlists from folders makes one for each folder in "
               "your collection. Playlists from other apps are only read, never changed.")

    def build(self):
        self.result = ui.WrapLabel(self, style="Muted.TLabel")
        self.result.pack(fill="x", pady=(0, 4))
        f, self.tv = ui.tree(self, [("name", "Playlist", 260), ("n", "Tracks", 70),
                                    ("missing", "Missing", 70), ("kind", "From", 140),
                                    ("where", "Where", 300)], height=14,
                             selectmode="browse")
        f.pack(fill="both", expand=True)
        self.tv.tag_configure("unreadable", foreground=ui.c("faint"))
        self.tv.tag_configure("broken", foreground=ui.c("warn"))
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.tv.bind("<Double-1>", lambda e: self.reveal())
        self.bar.set_primary("Show in folder", self.reveal, state="disabled")
        self.bar.set_more([
            ("Fix broken links…", self.app.repair_playlists),
            ("Rebuild playlists from folders…", self.app.rebuild_folder_playlists),
            None,
            ("Open Gig prep", lambda: self.app.show("gig")),
            ("Refresh", self.refresh),
        ])
        self.rows = {}

    def shown(self):
        # Read again each visit: a playlist may have changed in the app that made it
        self.refresh()

    def refresh(self):
        root = self.app.root_dir.get()
        if self.app.task.running:
            self.result.configure(text="Waiting for Sortero to finish what it's doing…")
            return

        def work(progress, log):
            return sources.survey(root, progress)

        def done(rows):
            self._fill(rows, root)

        self.result.configure(text="Looking for playlists…")
        self.app.task.run(work, done, "Looking for playlists")

    def _fill(self, rows, root):
        self.tv.delete(*self.tv.get_children())
        self.rows = {}
        for src, n, missing in rows:
            self.rows[src.key] = src
            tags = (("unreadable",) if n is None else ("broken",) if missing else ())
            self.tv.insert("", "end", iid=src.key, tags=tags, values=(
                src.name, "can't read" if n is None else f"{n:,}",
                f"{missing:,}" if missing else "", kind_label(src),
                where_label(src, root)))
        broken = sum(1 for _, n, m in rows if m)
        if not rows:
            text = ("No playlists yet. Make one from Streaming playlists, let Tidy up → "
                    "Reorganise turn your folders into playlists, or add ones you "
                    "already have in Gig prep.")
        else:
            text = (f"{ui.plural(len(rows), 'playlist')}"
                    + (f", {broken} with missing tracks." if broken else
                       ", every track where it should be."))
        self.result.configure(text=text)
        self._sync()

    def _selected(self):
        sel = self.tv.selection()
        return self.rows.get(sel[0]) if sel else None

    def _sync(self):
        self.bar.set_primary("Show in folder", self.reveal,
                             state="normal" if self._selected() else "disabled")

    def reveal(self):
        src = self._selected()
        if not src:
            return
        path = sources.file_of(src)
        if not os.path.exists(path):
            messagebox.showinfo(APP, f"{path} isn't there any more.")
            return
        paths.reveal(path)

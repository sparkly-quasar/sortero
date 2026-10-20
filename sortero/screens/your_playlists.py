"""Your playlists: every playlist Sortero can see, in one list.

Sortero's own (_Playlists), playlist files anywhere in the collection, the
watched folders' and whatever was added in Gig prep (gig/sources.py). Each
shows how many tracks it has and how many of those files aren't there; the
one selected lists its tracks underneath, missing ones marked.

Any of them can go into Gig prep's set. Only Sortero's own can be renamed or
deleted (set aside in _Quarantine, and History brings it back): the rest
belong to other apps and are only read, though one that was added can be
taken off the list again. More also holds the two tools that write Sortero's
own: Fix broken links and Rebuild playlists from folders.
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .. import journal, paths, playlists, ui
from ..gig import sources
from .base import Screen, APP

KINDS = {sources.SORTERO: "Sortero", sources.COLLECTION: "In your collection",
         sources.WATCHED: "Watched folder", sources.M3U: "Playlist file",
         sources.FOLDER: "Folder of tracks", sources.REKORDBOX: "rekordbox",
         sources.MIXXX: "Mixxx", sources.ITUNES: "iTunes or Music"}
SHOW, RENAME, DELETE, REMOVE = ("Show in folder", "Rename…", "Delete…",
                                "Take off the list…")


def kind_label(src):
    return KINDS.get(src.kind) or src.kind.capitalize()


def short(path, root):
    """A path as short as it can be said: from the collection, or from home."""
    if root and path.startswith(os.path.join(root, "")):
        return os.path.relpath(path, root)
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home + os.sep) else path


def where_label(src, root):
    if src.kind == sources.SORTERO:
        return "_Playlists"
    return short(src.where, root)


class YourPlaylistsScreen(Screen):
    title = "Your playlists"
    summary = "Every playlist Sortero can see, what's in it, and whether its tracks are there."
    details = ("Listed here: Sortero's own playlists in _Playlists, any .m3u or .m3u8 "
               "file inside your collection, and the playlists you've added in Gig prep "
               "from other apps, such as rekordbox or Mixxx, or from watched folders. "
               "Add more from Gig prep with Add playlists….\n\n"
               "Select one to see its tracks; missing ones are files that aren't where "
               "the playlist says. Add to gig set puts it at the end of Gig prep's set.\n\n"
               "Sortero's own playlists can be renamed or deleted. A deleted one is set "
               "aside in _Quarantine, and History can bring it back. Playlists from other "
               "apps are only read, never changed, but one you added can be taken off "
               "the list. Fix broken links re-links Sortero's own by matching artist and "
               "title, and Rebuild playlists from folders makes one for each folder.")

    def build(self):
        self.result = ui.WrapLabel(self, style="Muted.TLabel")
        self.result.pack(fill="x", pady=(0, 4))
        f, self.tv = ui.tree(self, [("name", "Playlist", 260), ("n", "Tracks", 70),
                                    ("missing", "Missing", 70), ("kind", "From", 140),
                                    ("where", "Where", 300)], height=8,
                             selectmode="browse")
        f.pack(fill="both", expand=True)
        self.tv.tag_configure("unreadable", foreground=ui.c("faint"))
        self.tv.tag_configure("broken", foreground=ui.c("warn"))
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.tv.bind("<Double-1>", lambda e: self.reveal())
        for seq in ("<Delete>", "<BackSpace>"):
            self.tv.bind(seq, lambda e: self.delete_or_remove())
        # right-click; on a Mac also the two-finger click (Button-2) and Control-click
        for seq in ["<Button-3>"] + (["<Button-2>", "<Control-Button-1>"]
                                     if paths.IS_MAC else []):
            self.tv.bind(seq, self._context)

        self.tracks_label = ttk.Label(self, style="Muted.TLabel")
        self.tracks_label.pack(fill="x", pady=(ui.GAP, 2))
        f, self.tracks_tv = ui.tree(self, [("n", "#", 40), ("track", "Track", 320),
                                           ("file", "File", 420)], height=7)
        f.pack(fill="both", expand=True)
        self.tracks_tv.tag_configure("broken", foreground=ui.c("warn"))

        self.bar.set_primary("Add to gig set", self.add_to_gig, state="disabled")
        self.bar.set_more([
            (SHOW, self.reveal),
            (RENAME, self.rename),
            (DELETE, self.delete),
            (REMOVE, self.remove),
            None,
            ("Fix broken links…", self.app.repair_playlists),
            ("Rebuild playlists from folders…", self.app.rebuild_folder_playlists),
            None,
            ("Open Gig prep", lambda: self.app.show("gig")),
            ("Refresh", self.refresh),
        ])
        self.rows, self.counts = {}, {}
        self._by_path = None
        self._sync()

    def shown(self):
        # Read again each visit: a playlist may have changed in the app that made it
        self.refresh()

    def invalidate(self):
        self._by_path = None        # the collection was read again

    def refresh(self, select=None):
        root = self.app.root_dir.get()
        select = select or self._selected_key()
        if self.app.task.running:
            # Gig prep checks a set it was just handed, so this is often brief
            self.result.configure(text="Waiting for Sortero to finish what it's doing…")
            if self.app.current is self:
                self.after(300, lambda: self.refresh(select))
            return

        def work(progress, log):
            return sources.survey(root, progress)

        def done(rows):
            self._fill(rows, root, select)

        self.result.configure(text="Looking for playlists…")
        self.app.task.run(work, done, "Looking for playlists")

    def _fill(self, rows, root, select=None):
        self.tv.delete(*self.tv.get_children())
        self.rows, self.counts = {}, {}
        for src, n, missing in rows:
            self.rows[src.key] = src
            self.counts[src.key] = n
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
        if select in self.rows:
            self.tv.selection_set(select)
            self.tv.focus(select)
            self.tv.see(select)
        self._sync()

    # ------------------------------------------------------------ the selection
    def _selected_key(self):
        sel = self.tv.selection()
        return sel[0] if sel and sel[0] in self.rows else None

    def _selected(self):
        return self.rows.get(self._selected_key())

    def _sync(self):
        src = self._selected()
        own = bool(src) and src.kind == sources.SORTERO
        self.bar.set_primary("Add to gig set", self.add_to_gig,
                             state="normal" if src and self.counts.get(src.key) else
                             "disabled")
        self.bar.enable(SHOW, bool(src))
        self.bar.enable(RENAME, own)
        self.bar.enable(DELETE, own)
        self.bar.enable(REMOVE, bool(src) and sources.removable(src))
        self._show_tracks(src)

    def _name_of(self, path):
        """A track as the Library shows it, or its filename if Sortero hasn't read it."""
        if self._by_path is None:
            self._by_path = {os.path.normcase(r.path): r for r in self.app.recs or ()}
        rec = self._by_path.get(os.path.normcase(path))
        return rec.display if rec else os.path.splitext(os.path.basename(path))[0]

    def _show_tracks(self, src):
        self.tracks_tv.delete(*self.tracks_tv.get_children())
        if not src:
            self.tracks_label.configure(text="Select a playlist to see its tracks.")
            return
        root = self.app.root_dir.get()
        try:
            found = sources.tracks(src, root)
        except sources.SourceError as e:
            self.tracks_label.configure(text=f"Sortero can't read “{src.name}”: {e}")
            return
        missing = 0
        for i, p in enumerate(found, 1):
            there = os.path.exists(p)
            missing += not there
            self.tracks_tv.insert("", "end", iid=str(i), tags=() if there else ("broken",),
                                  values=(i, self._name_of(p),
                                          short(p, root) + ("" if there else "  (missing)")))
        self.tracks_label.configure(
            text=f"In “{src.name}”: {ui.plural(len(found), 'track')}"
                 + (f", {missing:,} missing" if missing else ""))

    def _context(self, e):
        row = self.tv.identify_row(e.y)
        if row not in self.rows:
            return "break"
        self.tv.selection_set(row)
        self.tv.focus(row)
        src = self.rows[row]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Add to gig set", command=self.add_to_gig,
                         state="normal" if self.counts.get(row) else "disabled")
        menu.add_command(label=SHOW, command=self.reveal)
        if src.kind == sources.SORTERO:
            menu.add_separator()
            menu.add_command(label=RENAME, command=self.rename)
            menu.add_command(label=DELETE, command=self.delete)
        elif sources.removable(src):
            menu.add_separator()
            menu.add_command(label=REMOVE, command=self.remove)
        menu.tk_popup(e.x_root, e.y_root)
        return "break"

    # ------------------------------------------------------------ actions
    @property
    def gig(self):
        return self.app.screens["gig"].picker

    def add_to_gig(self):
        src = self._selected()
        if not src:
            return
        if self.gig.take(src):
            self.result.configure(text=f"Added “{src.name}” to the end of your gig set.")
        else:
            self.result.configure(text=f"“{src.name}” is already in your gig set.")

    def reveal(self):
        src = self._selected()
        if not src:
            return
        path = sources.file_of(src)
        if not os.path.exists(path):
            messagebox.showinfo(APP, f"{path} isn't there any more.")
            return
        paths.reveal(path)

    def rename(self):
        src = self._selected()
        if not src or src.kind != sources.SORTERO:
            return
        new = simpledialog.askstring(APP, f"Rename “{src.name}” to:",
                                     initialvalue=src.name, parent=self)
        if not new or not new.strip() or new.strip() == src.name:
            return
        root = self.app.root_dir.get()
        j = journal.Journal("rename-playlist", root)
        try:
            name = playlists.rename(root, src.name, new, journal=j)
        except FileExistsError:
            messagebox.showinfo(APP, f"There's already a playlist called “{new.strip()}”.")
            return
        except OSError as e:
            messagebox.showwarning(APP, f"Sortero couldn't rename it ({e.strerror or e}).")
            return
        j.save()
        self.app.log(f"playlist: renamed {src.name} to {name}")
        new_src = sources.Source(sources.SORTERO, src.path, name)
        self.gig.renamed(src.key, new_src.key)
        self.refresh(select=new_src.key)

    def delete(self):
        src = self._selected()
        if not src or src.kind != sources.SORTERO:
            return
        if not messagebox.askyesno(
                APP, f"Delete the playlist “{src.name}”?\n\nIts tracks stay where they "
                     "are. The playlist is set aside in _Quarantine, and History can "
                     "bring it back."):
            return
        root = self.app.root_dir.get()
        j = journal.Journal("delete-playlist", root)
        try:
            playlists.delete(root, src.name, journal=j)
        except OSError as e:
            messagebox.showwarning(APP, f"Sortero couldn't delete it ({e.strerror or e}).")
            return
        j.save()
        self.app.log(f"playlist: deleted {src.name}")
        self.gig.dropped(src.key)
        self.refresh()

    def remove(self):
        src = self._selected()
        if not src or not sources.removable(src):
            return
        if not messagebox.askyesno(
                APP, f"Take “{src.name}” off Sortero's list?\n\nThe playlist itself isn't "
                     f"touched; it stays in {kind_label(src)}. Add it again from Gig prep "
                     "with Add playlists…."):
            return
        sources.forget(src.key)
        self.gig.dropped(src.key)
        self.refresh()

    def delete_or_remove(self):
        src = self._selected()
        if src and src.kind == sources.SORTERO:
            self.delete()
        elif src and sources.removable(src):
            self.remove()

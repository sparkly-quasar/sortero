"""To do: the home screen. One card per job worth doing, most useful first."""
import os
from tkinter import ttk, messagebox

from .. import ui, importer, paths, review, settings, headers
from ..common import human_size, NAME_ORDERS, NAME_ORDER_LABELS, ARTIST_TITLE
from .base import Screen, APP, needs_genre, is_mix, in_unsorted


def count_in(root, folder):
    p = os.path.join(root or "", folder)
    return len(importer.gather([p])) if root and os.path.isdir(p) else 0


class TodoScreen(Screen):
    title = "To do"
    summary = "What needs your attention, most useful first."
    details = ("Sortero reads your collection and lists the jobs worth doing. Each card "
               "takes you to the right place with one button. Nothing changes until you "
               "confirm, and everything can be undone from History.")

    def build(self):
        self.bar.pack_forget()
        self.stats = ttk.Label(self, style="Muted.TLabel")
        self.stats.pack(fill="x", pady=(0, ui.SECTION))
        self.cards = ttk.Frame(self)
        self.cards.pack(fill="both", expand=True)
        self.jobs = 0

    def shown(self):
        self.refresh()

    def invalidate(self):
        self.refresh()

    def _card(self, *a, **kw):
        ui.Card(self.cards, *a, **kw).pack(fill="x", pady=(0, ui.GAP))

    def refresh(self):
        for w in self.cards.winfo_children():
            w.destroy()
        app = self.app
        root = app.root_dir.get()
        self.jobs = 0

        if not root or not os.path.isdir(root):
            self.stats.configure(text="")
            self._card("Choose your collection folder",
                       "Sortero needs to know where your DJ music lives. It reads "
                       "everything inside and changes nothing until you ask.",
                       "Choose folder…", app.choose,
                       link_text="Or walk through the setup guide", link_command=app.run_wizard)
            self._done(1)
            return

        if not app.recs or app.health is None:
            self.stats.configure(text="")
            if app.task.running:
                self._card("Reading your collection…",
                           "This takes a minute for a big library. The progress bar "
                           "at the bottom shows how far it's got.")
            else:
                self._card("Your collection hasn't been read yet",
                           "Sortero reads the tags of every track to work out what "
                           "needs doing.", "Read it now", app.scan)
            self._done(0)
            return

        h = app.health
        self.stats.configure(text="   ·   ".join([
            ui.plural(h["total"], "file"), human_size(h["bytes"]),
            f"{h['pct_analyzed']:.0f}% analysed",
            f"{h['pct_genre']:.0f}% have a genre",
            f"{h['pct_energy']:.0f}% have an energy rating"]))
        live = [r for r in app.recs if not r.protected and not is_mix(r)]

        # First, because a file a player refuses is worse than one badly tagged.
        damaged = h.get("stray_id3", [])
        if damaged:
            self._job(f"{ui.plural(len(damaged), 'AIFF or WAV file')} with a damaged header",
                      "An older Sortero wrote their tags in front of the audio instead of "
                      "inside the file. Forgiving players skip over it; stricter ones, "
                      "possibly CDJs among them, may refuse to play the file.",
                      "Repair…", lambda: self._repair(damaged), tone="warn")

        n = count_in(root, importer.PROCESSED)
        if n:
            self._job(f"{ui.plural(n, 'analysed track')} waiting in Processed",
                      "They've been through your analysis tool. Sort them into your "
                      "genre folders.", "Sort them…", app.open_processed, tone="good")

        unsorted = [r for r in live if in_unsorted(r)]
        if unsorted:
            self._job(f"{ui.plural(len(unsorted), 'track')} in Unsorted",
                      "Sortero couldn't tell their genre. Listen to each one and "
                      "choose its folder.", "Place them…", lambda: self._place(unsorted))

        genre = [r for r in live if needs_genre(r)]
        if genre:
            self._job(f"{ui.plural(len(genre), 'track')} with no genre",
                      "Set genres for many tracks at once, copy them from folder names, "
                      "or look them up on Discogs.", "Show them",
                      lambda: app.show_library("genre"))

        key = [r for r in live if not r.analyzed]
        if key:
            self._job(f"{ui.plural(len(key), 'track')} not analysed yet",
                      "They have no key. Choose the ones you want and send them to your "
                      "analysis tool.", "Show them", lambda: app.show_library("key"))

        swapped = [r for r in h.get("swapped", []) if not is_mix(r)]
        if swapped:
            self._job(f"{ui.plural(len(swapped), 'track')} with artist and title "
                      "the wrong way round",
                      "Their artist tag holds the title and the other way about. Check "
                      "the list and swap the ones that are really wrong.",
                      "Show them", lambda: app.show_library("swapped"))

        votes = h.get("name_order") or {}
        saved = settings.get("name_order")
        saved = saved if saved in NAME_ORDERS else ARTIST_TITLE
        if votes.get("sure") and votes["order"] != saved:
            self._job(f"Your filenames look like {NAME_ORDER_LABELS[votes['order']]}",
                      "Sortero is reading them the other way round, so names it takes "
                      "from a filename land in the wrong tag. Set the order in Clean "
                      "tags and it will read them your way.",
                      "Clean tags…", lambda: app.clean_tags("artist"))

        spam = len(h["spam_genre"]) + len(h["spam_comment"])
        if spam:
            self._job(f"{ui.plural(spam, 'tag')} full of download-site spam",
                      "Clean tags clears it. You'll see every change before anything "
                      "is written.", "Clean tags…", lambda: app.clean_tags("spam"))

        waiting = count_in(root, importer.TO_PROCESS)
        if waiting:
            folder = os.path.join(root, importer.TO_PROCESS)
            self._card(f"{ui.plural(waiting, 'track')} waiting in To Be Processed",
                       "Run them through your analysis tool and save the results into "
                       "Processed. They'll show up here, ready to sort.",
                       "Show folder", lambda: paths.reveal(folder))

        if not self.jobs:
            self._card("Nothing needs doing",
                       "Your collection is in good shape. Add new music, or look in "
                       "Tidy up for more tools.", "Add music", lambda: app.show("add"))
        self._done(self.jobs)

    def _job(self, *a, **kw):
        self.jobs += 1
        self._card(*a, **kw)

    def _done(self, n):
        self.app.set_badge("todo", n)

    def _repair(self, recs):
        root = self.app.root_dir.get()
        paths_ = [r.path for r in recs]
        names = [os.path.relpath(p, root) for p in paths_[:5]]
        more = len(paths_) - len(names)
        msg = (f"Repair {ui.plural(len(paths_), 'file')}?\n\n" + "\n".join(names)
               + (f"\n…and {more:,} more." if more else "")
               + "\n\nEach file's tags move inside it, where rekordbox and Mixed In Key "
                 "keep them. The audio isn't touched. The damaged original is set aside "
                 f"in _Quarantine/{headers.QUARANTINE_SUB}, and you can undo this from "
                 "History.")
        if not messagebox.askyesno(APP, msg):
            return

        def work(progress, log):
            return headers.repair(root, paths_, log=log, progress=progress)

        def done(res):
            _, n, failed = res
            messagebox.showinfo(APP, f"Repaired {ui.plural(n, 'file')}."
                                + (f"\n{failed} couldn't be repaired. History → Show log "
                                   "says why." if failed else ""))
            self.app.changed()

        self.app.task.run(work, done, "Repairing headers")

    def _place(self, recs):
        review.ReviewDialog(self.app, self.app, recs,
                            on_close=lambda changed: self.app.changed(repair=True)
                            if changed else None)

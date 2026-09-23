"""History: undo anything Sortero did, and the safety net."""
import datetime, os
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, journal, session, paths
from .base import Screen, APP, changes

KINDS = {
    "organize": "Reorganised the collection",
    "import": "Added music",
    "fixtags": "Cleaned tags",
    "fix-bpm": "Fixed BPMs",
    "dedupe": "Set duplicates aside",
    "flatten": "Flattened release folders",
    "set-genre": "Set genres",
    "stage-for-analysis": "Sent tracks to analysis",
    "choose-folder": "Placed tracks by hand",
    "resolve-duplicates": "Sorted duplicates from Processed",
    "save-playlist": "Saved a folder as a playlist",
}


def label(kind):
    return KINDS.get(kind) or (kind or "Unknown").replace("-", " ").capitalize()


def when(ts):
    if not ts:
        return "—"
    dt = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    clock = dt.strftime("%H:%M")
    if dt.date() == today:
        return f"Today, {clock}"
    if dt.date() == today - datetime.timedelta(days=1):
        return f"Yesterday, {clock}"
    if dt.year == today.year:
        return f"{dt.day} {dt.strftime('%b')}, {clock}"
    return f"{dt.day} {dt.strftime('%b %Y')}, {clock}"


class HistoryScreen(Screen):
    title = "History"
    summary = "Every change Sortero has made. Select one to undo it."
    details = ("Each row is one operation, such as adding music or a batch of tag edits. "
               "Undo puts files back where they were and restores old tag values.\n\n"
               "The safety net goes further. Turn it on before a big session and "
               "everything you do is recorded into one restore point, also saved as a "
               ".bak backup file. When you're done, keep it all or undo it in one go.")

    def build(self):
        self.net = ttk.Frame(self)
        self.net.pack(fill="x", pady=(0, ui.SECTION))
        self.table, self.tv = ui.tree(self, [("when", "When", 170),
                                             ("what", "What happened", 400),
                                             ("n", "Changes", 90)],
                                      height=11, selectmode="browse")
        self.table.pack(fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.log_frame = ttk.Frame(self)
        self.txt = tk.Text(self.log_frame, height=8, font=ui.MONO, wrap="none",
                           highlightthickness=0)
        self.txt.pack(fill="both", expand=True)
        self.log_link = ui.link(self.bar.left, "Show log", self.toggle_log)
        self.log_link.pack(side="left")
        self.bar.set_more([
            ("Show the record file", self.reveal),
            None,
            ("Save a safety net backup as…", self.app.testing_export),
            ("Load a backup and undo it…", self.app.testing_load),
            None,
            ("Refresh", self.refresh),
        ])
        self.journals = []
        self.refresh()
        self.refresh_net()

    def shown(self):
        self.refresh()
        self.refresh_net()

    def toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
            self.log_link.configure(text="Show log")
        else:
            self.log_frame.pack(fill="both", expand=True, pady=(ui.GAP, 0), after=self.table)
            self.log_link.configure(text="Hide log")
            self.txt.see("end")

    def refresh_net(self):
        for w in self.net.winfo_children():
            w.destroy()
        sess = session.active()
        if sess:
            s = session.summary(sess)
            ui.Card(self.net, "Safety net is on",
                    f"{changes(s)} recorded ({s['moves']} moves, "
                    f"{s['tags']} tag edits). Keep them, or undo everything since the "
                    "safety net went on.",
                    "Undo everything…", self.app.testing_revert, tone="warn",
                    link_text="Keep all changes and turn it off",
                    link_command=self.app.testing_commit).pack(fill="x")
        else:
            ui.Card(self.net, "Safety net is off",
                    "Turn it on before a big change. Everything you do is recorded into "
                    "one restore point you can undo in one go.",
                    "Turn on…", self.app.testing_start).pack(fill="x")

    def refresh(self):
        self.journals = journal.list_journals()
        self.tv.delete(*self.tv.get_children())
        for i, d in enumerate(self.journals):
            self.tv.insert("", "end", iid=str(i), values=(
                when(d.get("started")), label(d.get("kind")), len(d.get("entries", []))))
        self._sync()

    def _sync(self):
        self.bar.set_primary("Undo…", self.undo,
                             state="normal" if self.tv.selection() else "disabled")

    def append(self, msg):
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")

    def _selected(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo(APP, "Select an entry first.")
            return None
        return self.journals[int(sel[0])]

    def undo(self):
        d = self._selected()
        if not d:
            return
        n = len(d.get("entries", []))
        if not messagebox.askyesno(
                APP, f"Undo '{label(d.get('kind'))}' from {when(d.get('started'))}?\n\n"
                     f"{ui.plural(n, 'change')} will be reversed."):
            return
        f = d["_file"]

        def work(progress, log):
            return journal.revert(f, log=log)

        def done(res):
            ok, fail = res
            messagebox.showinfo(APP, f"Reversed {ui.plural(ok, 'change')}."
                                     + (f"\n{fail} couldn't be reversed." if fail else ""))
            try:
                os.remove(f)
            except OSError:
                pass
            self.app.changed()

        self.app.task.run(work, done, "Undoing")

    def reveal(self):
        d = self._selected()
        if d:
            paths.reveal(d["_file"])

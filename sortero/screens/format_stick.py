"""The Format a stick window: pick a stick, name it, type its current name to erase it."""
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui
from ..common import human_size
from ..gig import formatting as F
from .base import APP

INSTRUCTIONS_ELSEWHERE = (
    "Sortero can't format sticks on this system. Use your system's disk tool and "
    "choose:\n\n"
    "  • partition table: MBR (also called DOS or Master Boot Record)\n"
    "  • one partition\n"
    "  • file system: FAT32\n\n"
    "Then check the stick in Gig prep.")


class FormatDialog(tk.Toplevel):
    def __init__(self, app, sticks, refused, keep_paths, on_done=None):
        super().__init__(app)
        self.app, self.sticks, self.keep_paths, self.on_done = app, sticks, keep_paths, on_done
        self.title("Format a stick")
        self.transient(app)
        self.geometry("720x520")
        body = ttk.Frame(self, padding=ui.PAD)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Format a stick as FAT32", style="Title.TLabel").pack(anchor="w")
        ui.WrapLabel(body, style="Muted.TLabel",
                     text="One FAT32 partition on an MBR map: FAT32 is the one file system "
                          "every Pioneer and Denon player reads. Everything on the stick you choose is erased, "
                          "and that can't be undone.").pack(fill="x", pady=(2, ui.SECTION))

        f, self.tv = ui.tree(body, [("name", "Name", 140), ("disk", "Disk", 90),
                                    ("size", "Size", 90), ("media", "Stick", 260)],
                             height=4, selectmode="browse")
        f.pack(fill="x")
        for d in sticks:
            self.tv.insert("", "end", iid=d.id, values=(d.name, d.device,
                                                         human_size(d.size), d.media))
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._update())
        hidden = "Internal disks are never listed"
        if refused:
            hidden += (f", nor {ui.plural(len(refused), 'other external disk')} ("
                       + "; ".join(f"{d.name}: {why}" for d, why in refused) + ")")
        ui.WrapLabel(body, style="Small.TLabel", text=hidden + ".").pack(fill="x", pady=(4, 0))
        if not sticks:
            ui.WrapLabel(body, text="No stick that can be formatted is plugged in. Plug it "
                                    "in, wait for it to appear on your computer, and open this "
                                    "again.").pack(fill="x", pady=(ui.SECTION, 0))

        form = ttk.Frame(body)
        form.pack(fill="x", pady=(ui.SECTION, 0))
        ttk.Label(form, text="New name").grid(row=0, column=0, sticky="w", padx=(0, ui.GAP))
        self.new_name = tk.StringVar(value="GIG")
        ttk.Entry(form, textvariable=self.new_name, width=14).grid(row=0, column=1, sticky="w")
        self.confirm_label = ttk.Label(form, text="Type the stick's current name to confirm")
        self.confirm_label.grid(row=1, column=0, sticky="w", padx=(0, ui.GAP),
                                pady=(ui.GAP, 0))
        self.typed = tk.StringVar()
        ttk.Entry(form, textvariable=self.typed, width=24).grid(row=1, column=1, sticky="w",
                                                                pady=(ui.GAP, 0))
        for v in (self.new_name, self.typed):
            v.trace_add("write", lambda *a: self._update())

        self.note = ui.WrapLabel(body, style="Warn.TLabel")
        self.note.pack(fill="x", pady=(ui.SECTION, 0))
        self.cmd = ttk.Label(body, style="Small.TLabel", font=ui.MONO, wraplength=660)
        self.cmd.pack(anchor="w", pady=(4, 0))

        row = ttk.Frame(body)
        row.pack(side="bottom", fill="x")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right")
        self.go = ttk.Button(row, text="Erase", command=self.erase, state="disabled")
        self.go.pack(side="right", padx=ui.GAP)
        if len(sticks) == 1:
            self.tv.selection_set(sticks[0].id)
        self._update()
        self.grab_set()

    @property
    def disk(self):
        sel = self.tv.selection()
        return next((d for d in self.sticks if d.id == sel[0]), None) if sel else None

    def _update(self):
        d = self.disk
        problems = []
        try:
            name = F.label(self.new_name.get())
        except F.FormatError as e:
            name, problems = None, [str(e)]
        if d:
            self.confirm_label.configure(text=f"Type “{d.name}” to confirm")
            if d.size > F.BIG:
                problems.append(f"{d.name} is {human_size(d.size)}, bigger than most USB "
                                "sticks. Make sure it's the stick.")
            self.cmd.configure(text=F.plan(d, name) if name else "")
        else:
            self.cmd.configure(text="")
        self.note.configure(text=" ".join(problems))
        ready = bool(d and name and self.typed.get().strip() == d.name)
        self.go.configure(state="normal" if ready else "disabled")

    def erase(self):
        d, typed, new = self.disk, self.typed.get(), self.new_name.get()
        if not d:
            return
        self.go.configure(state="disabled")
        keep = self.keep_paths

        def work(progress, log):
            try:
                return True, F.erase_stick(d, new, typed, keep)
            except F.FormatError as e:
                return False, str(e)

        def done(res):
            ok, output = res
            if not ok:
                messagebox.showerror(APP, output, parent=self)
                self._update()
                return
            self.destroy()
            label = F.label(new)
            messagebox.showinfo(APP, f"{d.device} is now FAT32 on one MBR partition, "
                                     f"named {label}.")
            if self.on_done:
                self.on_done(label, output)

        self.app.task.run(work, done, "Formatting the stick")


def open_for(app, keep_paths, on_done=None):
    """Find the sticks that may be erased, then show the window. Elsewhere, explain."""
    if not F.available():
        messagebox.showinfo(APP, INSTRUCTIONS_ELSEWHERE)
        return

    def work(progress, log):
        try:
            return F.find(keep_paths), None
        except F.FormatError as e:
            return None, str(e)

    def done(res):
        found, error = res
        if error:
            # Not knowing which disk holds what is reason enough to offer none.
            messagebox.showwarning(APP, "Sortero couldn't tell which disks hold your "
                                        f"system and your music ({error}), so it won't "
                                        "offer any to erase.")
            return
        FormatDialog(app, *found, keep_paths, on_done)

    app.task.run(work, done, "Looking for sticks")

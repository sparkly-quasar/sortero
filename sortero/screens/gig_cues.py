"""The Cue points window: where Gig prep finds each track's cues and beat grid, and
the one adjustment MP3 copies can need. Opened from the main stick step."""
import os
import tkinter as tk
from tkinter import ttk, filedialog

from .. import mixxx, settings, ui

EXPORT_HOW = ("rekordbox keeps cues in its own database, which Sortero can't read. In "
              "rekordbox choose File > Export Collection in xml format, and add that file "
              "here. Export it again whenever you change cues in rekordbox. Mixed In Key's "
              "own rekordbox.xml export works too.")
SHIFT_HOW = ("Some players start an MP3 about 25 ms late, which puts every cue a sliver "
             "before its beat. Leave this off unless you've seen that. To check once: "
             "import a converted track with cues into rekordbox and zoom all the way in on "
             "its first cue. If it sits just before the kick instead of on it, tick this.")


class CueDialog(tk.Toplevel):
    def __init__(self, app, mp3_copies, on_change):
        super().__init__(app)
        self.on_change = on_change
        self.title("Cue points and beat grids")
        self.transient(app)
        self.geometry("620x560")
        body = ttk.Frame(self, padding=ui.PAD)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Cue points and beat grids", style="Title.TLabel").pack(
            anchor="w")
        ui.WrapLabel(body, style="Muted.TLabel",
                     text="For each track Sortero looks in these places, in this order, "
                          "and uses the first that has them.").pack(fill="x",
                                                                    pady=(2, ui.SECTION))

        ttk.Label(body, text="1. Your rekordbox collection", style="Heading.TLabel").pack(
            anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(4, 0))
        self.xml_label = ttk.Label(row)
        self.xml_label.pack(side="left", fill="x", expand=True)
        self.remove = ttk.Button(row, text="Remove", command=self._remove)
        self.choose = ttk.Button(row, command=self._choose)
        self.choose.pack(side="right")
        ui.WrapLabel(body, style="Small.TLabel", text=EXPORT_HOW).pack(fill="x", pady=(4, 0))

        ttk.Label(body, text="2. Mixed In Key", style="Heading.TLabel").pack(
            anchor="w", pady=(ui.SECTION, 0))
        ttk.Label(body, text="Read from the tags Mixed In Key writes into each file.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        ttk.Label(body, text="3. Mixxx", style="Heading.TLabel").pack(
            anchor="w", pady=(ui.SECTION, 0))
        ttk.Label(body, text="Mixxx's library is on this computer." if mixxx.find_db()
                  else "Mixxx's library isn't on this computer.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        if mp3_copies:
            ttk.Label(body, text="MP3 copies", style="Heading.TLabel").pack(
                anchor="w", pady=(ui.SECTION + 4, 0))
            self.shift = tk.BooleanVar(value=bool(settings.get("gig_mp3_cue_shift")))
            ttk.Checkbutton(body, text="Move cues 25 ms later on MP3 copies",
                            variable=self.shift, command=self._shift_changed).pack(
                anchor="w", pady=(4, 0))
            ui.WrapLabel(body, style="Small.TLabel", text=SHIFT_HOW).pack(fill="x",
                                                                         pady=(4, 0))

        ttk.Button(body, text="Done", command=self.destroy, default="active").pack(
            side="bottom", anchor="e")
        self._draw()

    def _draw(self):
        path = settings.get("gig_rekordbox_xml")
        if path and os.path.isfile(path):
            self.xml_label.configure(text=os.path.basename(path))
            self.choose.configure(text="Change…")
            self.remove.pack(side="right", padx=(0, ui.GAP))
        else:
            self.xml_label.configure(text="Not added.")
            self.choose.configure(text="Add the file…")
            self.remove.pack_forget()

    def _choose(self):
        current = settings.get("gig_rekordbox_xml") or ""
        path = filedialog.askopenfilename(
            parent=self, title="Choose your rekordbox collection XML",
            initialdir=os.path.dirname(current) or os.path.expanduser("~"),
            filetypes=[("rekordbox XML", "*.xml"), ("All files", "*")])
        if path:
            settings.set("gig_rekordbox_xml", path)
            self._draw()
            self.on_change(recount=True)

    def _remove(self):
        settings.set("gig_rekordbox_xml", None)
        self._draw()
        self.on_change(recount=True)

    def _shift_changed(self):
        settings.set("gig_mp3_cue_shift", bool(self.shift.get()))
        self.on_change(recount=False)


def open_for(app, mp3_copies, on_change):
    return CueDialog(app, mp3_copies, on_change)

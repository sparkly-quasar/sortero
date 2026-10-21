"""Gig prep: a stick that plays on any Pioneer or Denon player, checked before you leave.

One column of five steps (gig/checklist.py). Only the open step shows its
content and its one button; done steps are a tick and a line, steps not
reached yet just a title. Each step's results stay inside that step.
"""
import dataclasses, hashlib, json, os, time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .. import mixxx, ui, paths, settings
from ..common import human_size
from ..gig import (backup, checklist, convert, cues, disks, ffmpeg, profiles, rbxml,
                   usbexport, verify)
from ..gig.checklist import DONE, NEXT, WAITING
from .base import Screen, APP
from .gig_set import SetPicker, short

TARGETS = [(convert.MP3, "MP3 320 kbps (smaller)"), (convert.AIFF, "AIFF 16-bit (lossless)")]
TONES = {DONE: "good", NEXT: "text", WAITING: "faint"}
# How a check's result reads in a step: calm marks, no shouting
MARK = {verify.FAIL: "✗", verify.WARN: "!", verify.PASS: "✓"}


def _profile_label(p):
    return f"{p.name} (safest)" if p.key == profiles.EVERY.key else p.name


class Findings(tk.Frame):
    """What a check found, problems first. Passes fold into one line with Show."""

    def __init__(self, parent, height=4):
        super().__init__(parent)
        ui.paint(self, bg="card")
        self.txt = tk.Text(self, height=height, font=ui.BODY, wrap="word", relief="flat",
                           highlightthickness=0, padx=0, pady=2, cursor="arrow")
        ui.paint(self.txt, bg="card", fg="text")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_bind("link", "<Button-1>", lambda e: self._toggle())
        self.txt.tag_bind("link", "<Enter>", lambda e: self.txt.configure(cursor="hand2"))
        self.txt.tag_bind("link", "<Leave>", lambda e: self.txt.configure(cursor="arrow"))
        self.rows, self.open = [], False

    def show(self, rows):
        """rows: [(level, line, [details])], in any order."""
        rank = {verify.FAIL: 0, verify.WARN: 1, verify.PASS: 2}
        self.rows, self.open = sorted(rows, key=lambda r: rank[r[0]]), False
        self._draw()

    def show_report(self, report, extra=()):
        self.show([(c.level, f"{c.title}: {c.summary}", c.details[:verify.SHOWN])
                   for c in report.checks] + list(extra))

    def _toggle(self):
        self.open = not self.open
        self._draw()

    def _draw(self):
        t = self.txt
        for tag, role in (("fail", "warn"), ("warn", "muted"), ("pass", "good"),
                          ("detail", "muted"), ("link", "link")):
            t.tag_configure(tag, foreground=ui.c(role))     # follows light and dark
        t.configure(state="normal")
        t.delete("1.0", "end")
        passed = [r for r in self.rows if r[0] == verify.PASS]
        for level, line, details in self.rows:
            if level == verify.PASS and not self.open and len(passed) > 1:
                continue
            t.insert("end", f"{MARK[level]}  ", level)
            t.insert("end", line + "\n")
            for d in details:
                t.insert("end", f"     {d}\n", "detail")
        if len(passed) > 1:
            if not self.open:
                t.insert("end", f"{MARK[verify.PASS]}  ", verify.PASS)
                t.insert("end", f"{ui.plural(len(passed), 'check')} passed  ")
            t.insert("end", "Hide" if self.open else "Show", "link")
        t.configure(state="disabled")


def _links(parent):
    row = tk.Frame(parent)
    ui.paint(row, bg="card")
    return row


def _link(parent, text, command):
    lk = tk.Label(parent, text=text, font=ui.BODY,
                  cursor="pointinghand" if paths.IS_MAC else "hand2")
    ui.paint(lk, bg="card", fg="link")
    lk.bind("<Button-1>", lambda e: command())
    return lk


def _button(parent, text, command):
    """An action: a real button, so it can't be mistaken for a highlighted line."""
    return ttk.Button(parent, text=text, command=command)


def _heading(parent, text):
    """A small heading inside a step, so its parts read as groups."""
    return _label(parent, text, tone="text", font=ui.STRONG)


def _label(parent, text="", tone="muted", font=None):
    lab = tk.Label(parent, text=text, font=font or ui.BODY, anchor="w", justify="left")
    ui.paint(lab, bg="card", fg=tone)
    lab.bind("<Configure>", lambda e: lab.configure(wraplength=max(e.width - 4, 200)))
    return lab


class GigScreen(Screen):
    title = "Gig prep"
    summary = ("Get a USB stick that plays on any Pioneer or Denon player, and check it "
               "before you leave.")
    details = ("Five steps, one at a time. Choose your set; Sortero checks every track "
               "against what Pioneer's and Denon's own manuals say the players read, and "
               "makes a gig-safe copy of any that won't play everywhere, in a folder next "
               "to your collection. Your originals are never changed.\n\n"
               "Then the main stick: the set goes into rekordbox or Engine DJ and onto the "
               "stick, and Sortero checks it: the file system, the library, every format, "
               "every track decoded from start to finish. A backup stick of plain folders "
               "is recommended. It ends with GIG READY.")

    def build(self):
        self.bar.pack_forget()          # each step carries its own button
        self.notice = ui.WrapLabel(self, style="Warn.TLabel", text="")
        self.steps_frame = ttk.Frame(self)
        self.steps_frame.pack(fill="both", expand=True)
        self.cards = {}
        for key in checklist.KEYS:
            card = ui.StepCard(self.steps_frame, on_open=lambda k=key: self.open(k))
            card.pack(fill="x", pady=(0, 6))
            self.cards[key] = card

        # 1. Your set
        set_card = self.cards["set"]
        self.set_tools = tk.Frame(set_card.tools)
        ui.paint(self.set_tools, bg="card")
        self.picker = SetPicker(set_card.body, self, self.set_tools)
        self.picker.pack(fill="both", expand=True)
        self.change = {k: self.cards[k].tool("Change" if k == "set" else "Show",
                                             lambda k=k: self.open(k))
                       for k in checklist.KEYS}

        self._build_tracks(self.cards["tracks"].body)
        self._build_stick(self.cards["main"].body, "main")
        self._build_stick(self.cards["backup"].body, "backup")
        self._build_ready(self.cards["ready"].body)

        self.state = checklist.State()
        self.open_key = None
        self.reports = {}               # "main" / "backup" -> the last verify.Report
        self.auto_checked = None        # the set the tracks were last checked for, unasked
        self.unreadable = []            # playlists that couldn't be read at the last check
        self.notice_text = ""
        self.loaded_root = None

    # ------------------------------------------------------------ step bodies
    def _build_tracks(self, body):
        line = _links(body)
        line.pack(fill="x")
        self.for_label = _label(line)
        self.for_label.pack(side="left")
        _link(line, "Change", self._toggle_options).pack(side="left", padx=(ui.GAP, 0))

        self.options = _links(body)     # shown by Change
        ttk.Label(self.options, text="Players").pack(side="left", padx=(0, ui.GAP))
        self.profile_labels = {_profile_label(p): p.key for p in profiles.PROFILES.values()}
        self.profile_var = tk.StringVar(
            value=_profile_label(profiles.get(settings.get("gig_profile"))))
        box = ttk.Combobox(self.options, textvariable=self.profile_var, state="readonly",
                           width=34, values=list(self.profile_labels))
        box.pack(side="left", padx=(0, ui.SECTION))
        box.bind("<<ComboboxSelected>>", lambda e: self._options_changed())
        ttk.Label(self.options, text="Copies").pack(side="left", padx=(0, ui.GAP))
        self.target_labels = dict((label, key) for key, label in TARGETS)
        current = settings.get("gig_target")
        self.target_var = tk.StringVar(
            value=next((l for k, l in TARGETS if k == current), TARGETS[0][1]))
        box = ttk.Combobox(self.options, textvariable=self.target_var, state="readonly",
                           width=24, values=list(self.target_labels))
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e: self._options_changed())

        foot = _links(body)
        foot.pack(side="bottom", fill="x", pady=(6, 0))
        self.lib_label = _label(foot)
        self.lib_label.pack(side="left")
        _link(foot, "Show", self.show_library).pack(side="left", padx=(ui.GAP, 0))
        _link(foot, "Change…", self.choose_library).pack(side="left", padx=(ui.GAP, 0))
        f, self.track_tv = ui.tree(body, [("what", "", 120), ("track", "Track", 380),
                                          ("why", "Why", 360)], height=3, selectmode="none")
        self.track_box = f

    def _build_stick(self, body, which):
        top = _links(body)
        top.pack(fill="x")
        widgets = {"top": top}
        if which == "main":
            self.xml_label = _label(top)
            self.xml_label.pack(side="left", fill="x", expand=True)
            self.copy_link = _button(top, "Copy the file's location", self._copy_xml)
            self.again_row = _links(body)
            _button(self.again_row, "Write the main stick again…",
                    self.write_main).pack(side="left")
            self.cue_row = _links(body)
            _heading(self.cue_row, "Cue points and beat grids").pack(fill="x")
            self.cue_count = _label(self.cue_row)       # what was found for these tracks
            self.cue_label = _label(self.cue_row)       # where from; wraps: it can be long
            self.cue_label.pack(fill="x")

            cue_links = _links(self.cue_row)
            cue_links.pack(fill="x", pady=(4, 0))
            self.cue_change = _button(cue_links, "", self.choose_cue_xml)
            self.cue_change.pack(side="left")
            self.cue_timing_link = _button(cue_links, "Cue timing on MP3 copies…",
                                           self.cue_timing)

            # The other ways through this step, each with what it's for
            self.other_row = _links(body)
            _heading(self.other_row, "Other ways").pack(fill="x")
            self.alt_row = self._way(self.other_row, "Use rekordbox or Engine DJ…",
                                     self.make_xml, "Sortero makes a playlists file for "
                                     "the app, and shows the steps to put it on the stick.")
            self.existing_row = self._way(self.other_row, "Check a stick you already made…",
                                          self.check_main, "Made with rekordbox, Engine DJ "
                                          "or Sortero. Nothing on it is changed.")
            self.skip_row = self._way(self.other_row, "Skip the main stick…",
                                      self.skip_main, "Make only the folders stick. The "
                                      "players browse folders: no playlists, cue points or "
                                      "beat grids.")
            how = tk.Frame(body)
            ui.paint(how, bg="card")
            self.how_txt = tk.Text(how, height=5, font=ui.BODY, wrap="word", relief="flat",
                                   highlightthickness=0, padx=0)
            ui.paint(self.how_txt, bg="card", fg="text")
            self.how_txt.tag_configure("head", font=ui.HEADING)
            sb = ttk.Scrollbar(how, orient="vertical", command=self.how_txt.yview)
            self.how_txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.how_txt.pack(side="left", fill="both", expand=True)
            self.how = how
            self.how_toggle = _link(top, "", self._toggle_how)
            self.how_open = True
        else:
            self.backup_label = _label(top)
            self.backup_label.pack(side="left")
            # One USB port, or a hub that's full: the main stick has to come out first.
            self.eject_row = _links(body)
            self.eject_label = _label(self.eject_row)
            self.eject_label.pack(side="left")
            _button(self.eject_row, "Eject the main stick", self.eject_main).pack(
                side="left", padx=(ui.GAP, 0))
        tools = _links(body)
        tools.pack(side="bottom", fill="x", pady=(6, 0))
        widgets["format"] = ttk.Button(tools, text="Format a stick as FAT32…",
                                       command=self.format_stick)
        widgets["format_link"] = _button(tools, "New stick? Format it as FAT32…",
                                         self.format_stick)
        widgets["quick"] = _button(tools, "Faster check (doesn't play every track)…",
                                   lambda: self.check_main(decode=False) if which == "main"
                                   else self.check_backup(decode=False))
        widgets["findings"] = Findings(body)
        widgets["tools"] = tools
        setattr(self, f"{which}_w", widgets)

    @staticmethod
    def _way(parent, text, command, why):
        row = _links(parent)
        _button(row, text, command).pack(side="left", anchor="n")
        _label(row, why).pack(side="left", fill="x", expand=True, padx=(ui.GAP, 0))
        return row

    def _build_ready(self, body):
        self.ready_big = tk.Label(body, text="GIG READY", font=ui.TITLE, anchor="w")
        ui.paint(self.ready_big, bg="card", fg="good")
        self.ready_big.pack(fill="x")
        self.ready_lines = _label(body, tone="text")
        self.ready_lines.pack(fill="x", pady=(4, 0))
        links = _links(body)
        links.pack(fill="x", pady=(ui.GAP, 0))
        self.backup_open = _button(links, "Make a backup stick…",
                                   lambda: self.open("backup"))
        self.ready_check = _button(links, "Check the main stick again…", self.check_main)
        self.ready_clear = _button(links, "Clear the set for the next gig", self.picker.clear)

    # ------------------------------------------------------------ the set
    def shown(self):
        # Read every time: a playlist file, rekordbox export or Mixxx crate may
        # have changed in the app that made it since Gig prep last looked.
        self.loaded_root = self.app.root_dir.get()
        self.picker.load()
        self._recall()
        self.refresh()

    def invalidate(self):
        self.loaded_root = None

    def set_changed(self, reason):
        """The picker changed the set. Later steps start again, and say why."""
        had = self._has_progress()
        self._reset()
        self.notice_text = (f"{reason}, so later steps start again."
                            if reason and had else "")
        self.refresh()

    def snapshot(self):
        return dataclasses.replace(self.state), self.open_key, dict(self.reports)

    def restore(self, snap):
        self.state, self.open_key, self.reports = (dataclasses.replace(snap[0]), snap[1],
                                                   dict(snap[2]))
        self.notice_text = ""
        self.refresh()

    def _has_progress(self):
        st = self.state
        return bool(st.items is not None or st.main or st.backup_check or st.earlier
                    or st.skip_main)

    def _reset(self):
        st = self.state
        st.items, st.copied, st.sets, st.xml_path, st.xml_cues = None, False, None, "", {}
        st.xml_tracks = 0
        st.main, st.backup, st.backup_check, st.earlier = None, None, None, None
        st.skip_main = False
        self.reports = {}

    # --------------------------------------------- remembering a checked gig
    def _signature(self):
        key = json.dumps([self.picker.chosen, self.profile.key, self.target,
                          self.picker.track_count()])
        return hashlib.sha1(key.encode()).hexdigest()

    def _remember(self):
        st = self.state
        settings.set("gig_progress", {
            "sig": self._signature(), "when": time.time(),
            "main": st.main.to_dict() if st.main else None,
            "backup": st.backup_check.to_dict() if st.backup_check else None,
            "skip_main": st.skip_main})

    def _recall(self):
        """Sticks checked in an earlier session, for this same set, still count."""
        st = self.state
        if self._has_progress():
            return
        saved = settings.get("gig_progress") or {}
        if not saved or saved.get("sig") != self._signature():
            return
        if saved.get("main"):
            st.main = checklist.StickCheck.from_dict(saved["main"])
        if saved.get("backup"):
            st.backup_check = checklist.StickCheck.from_dict(saved["backup"])
        st.skip_main = bool(saved.get("skip_main"))
        if st.main or st.backup_check:
            st.earlier = saved.get("when")

    # ------------------------------------------------------------ options
    @property
    def profile(self):
        return profiles.get(self.profile_labels.get(self.profile_var.get()))

    @property
    def target(self):
        return self.target_labels.get(self.target_var.get(), convert.MP3)

    def _toggle_options(self):
        if self.options.winfo_ismapped():
            self.options.pack_forget()
        else:
            self.options.pack(fill="x", pady=(4, 0), after=self.for_label.master)

    def _options_changed(self):
        settings.set("gig_profile", self.profile.key)
        settings.set("gig_target", self.target)
        self.picker.forget_undo()
        self.set_changed("You changed the players or the copies")

    def _apps(self):
        """Where the main stick's library comes from: "rekordbox / Engine DJ"."""
        apps = [name for reads, name in ((profiles.reads_rekordbox, "rekordbox"),
                                         (profiles.reads_engine, "Engine DJ"))
                if reads(self.profile)]
        return " / ".join(apps) or "rekordbox"

    # ------------------------------------------------------------ drawing
    def open(self, key):
        steps = {s.key: s for s in checklist.steps(self.state)}
        if steps[key].status == WAITING:
            return
        self.open_key = key
        self.refresh()

    def refresh(self):
        st = self.state
        st.playlists, st.track_count = self.picker.names(), self.picker.track_count()
        st.apps = self._apps()
        st.direct = profiles.reads_rekordbox(self.profile)
        all_steps = checklist.steps(st)
        by_key = {s.key: s for s in all_steps}
        if self.open_key is None or by_key[self.open_key].status == WAITING:
            self.open_key = checklist.current(all_steps)
        if st.skip_main:
            main_action = self.unskip_main
        elif st.main or st.earlier:
            main_action = self.check_main
        elif st.direct:
            main_action = self.write_main
        else:
            main_action = self.check_main if st.xml_path else self.make_xml
        actions = {"tracks": self.make_copies if st.items else self.check_tracks,
                   "main": main_action,
                   "backup": self.write_backup}
        for i, s in enumerate(all_steps, 1):
            card, is_open = self.cards[s.key], s.key == self.open_key
            text = s.text if s.status != WAITING else ""
            button, command, enabled = s.button, actions.get(s.key), True
            if s.key == "set":
                button, command = "Next: check the tracks", self.start_tracks
                enabled = bool(self.picker.chosen)
            if s.key == "ready":
                text = "" if is_open else text.split("\n")[0]
            chk = st.main if s.key == "main" else st.backup_check if s.key == "backup" else None
            if is_open and chk and s.key in self.reports and not chk.ready:
                text = chk.verdict
            if s.key == "set" and (st.items is not None or st.earlier):
                button, command = "Done", self._advance
            card.update(f"{checklist.MARKS[s.status]}   {i}   {s.title}", text,
                        tone=TONES[s.status],
                        text_tone="warn" if text.startswith("Not ready yet") else "muted",
                        button=button, command=command, open=is_open, enabled=enabled)
            link = self.change[s.key]
            if is_open or s.status == WAITING or s.key == "ready":
                link.pack_forget()
            else:
                link.pack(side="right")
        if self.open_key == "set":
            self.set_tools.pack(side="right")
        else:
            self.set_tools.pack_forget()
        self._draw_tracks()
        self._draw_stick("main")
        self._draw_stick("backup")
        self._draw_ready(by_key["ready"])
        # Only a step with a list or findings to show takes the spare room; the
        # others stay their own height, so their buttons sit right under them.
        for key, card in self.cards.items():
            grows = key == self.open_key and self._grows(key)
            card.pack_configure(fill="both" if grows else "x", expand=grows)
        if self.notice_text:
            self.notice.configure(text=self.notice_text)
            self.notice.pack(fill="x", pady=(0, ui.GAP), before=self.steps_frame)
        else:
            self.notice.pack_forget()
        n = checklist.KEYS.index(self.open_key) + 1
        if checklist.ready(st):
            self.header.set_summary("Ready to leave." if not st.earlier else
                                    f"Ready to leave. Checked {checklist.when(st.earlier)}.")
        else:
            self.header.set_summary(f"Step {n} of {len(checklist.KEYS)}: "
                                    f"{by_key[self.open_key].title}.")
        self._maybe_auto_check()

    def _grows(self, key):
        if key == "set":
            return True
        if key == "tracks":
            return bool(self.track_box.winfo_manager())
        if key == "main" and self.how.winfo_manager():
            return True
        w = getattr(self, f"{key}_w", None)
        return bool(w and w["findings"].winfo_manager())

    def _draw_tracks(self):
        st = self.state
        self.for_label.configure(text=f"For {profiles.where(self.profile)} · copies as "
                                      f"{self.target_var.get().split(' (')[0]}")
        try:
            lib = convert.library_dir(self.app.root_dir.get(), settings.get("gig_library"))
            self.lib_label.configure(text=f"Copies go to {short(lib)}")
        except convert.LibraryError:
            self.lib_label.configure(text="")
        rows = []
        for it in st.items or []:
            if it.status == convert.CONVERT:
                rows.append(("Needs a copy", it.rel, it.why[0] if it.why else ""))
            elif it.status == convert.FAILED:
                rows.append(("Can't use", it.rel, " ".join(it.why)))
        rows += [("Can't read", "", u) for u in getattr(self, "unreadable", [])]
        self.track_tv.delete(*self.track_tv.get_children())
        for r in rows:
            self.track_tv.insert("", "end", values=r)
        if rows:
            self.track_box.pack(fill="both", expand=True, pady=(6, 0))
        else:
            self.track_box.pack_forget()

    def _draw_stick(self, which):
        st, w = self.state, getattr(self, f"{which}_w")
        report = self.reports.get(which)
        skipped = which == "main" and st.skip_main
        order = [(w["top"], True)]          # the body's parts, top to bottom: (part, shown)
        if which == "main" and not skipped:
            have_xml = bool(st.xml_path)
            self.xml_label.configure(text="Playlists file ready."
                                     if have_xml and not st.direct else "")
            counted = checklist.cue_summary(st.xml_cues, st.xml_tracks)
            self.cue_count.configure(text=counted)
            if counted:                         # none until the tracks are checked
                self.cue_count.pack(fill="x", before=self.cue_label)
            else:
                self.cue_count.pack_forget()
            src = settings.get("gig_rekordbox_xml")
            src = src if src and os.path.isfile(src) else None
            self.cue_label.configure(text=checklist.cue_sources(
                os.path.basename(src) if src else None, mixxx.find_db() is not None))
            self.cue_change.configure(text="Change rekordbox file…" if src else
                                      "Add cues from rekordbox…")
            if self.target == convert.MP3:
                self.cue_timing_link.pack(side="left", padx=(ui.GAP, 0))
            else:
                self.cue_timing_link.pack_forget()
            self._fill_how()
            show_how = (have_xml or st.earlier) and (self.how_open or not report)
            if have_xml or st.earlier:
                self.how_toggle.configure(text="Hide the steps" if show_how else
                                          "Show the steps")
                self.how_toggle.pack(side="right")
                self.copy_link.pack(side="right", padx=(0, ui.SECTION))
            else:
                self.how_toggle.pack_forget()
                self.copy_link.pack_forget()
            ways = [(self.alt_row, st.direct and not have_xml),
                    # once the playlists file's made, checking is the step's own button
                    (self.existing_row, not (st.main or st.earlier)
                     and (st.direct or not have_xml)),
                    (self.skip_row, not (st.main and st.main.ready))]
            for row, shown in ways:
                if shown:
                    row.pack(fill="x", pady=(4, 0))
                else:
                    row.pack_forget()
            order += [(self.again_row, st.direct and bool(st.main or st.earlier)),
                      (self.how, show_how), (self.cue_row, True),
                      (self.other_row, any(shown for _, shown in ways))]
        elif which == "main":
            self.how_toggle.pack_forget()
            self.copy_link.pack_forget()
            self.xml_label.configure(text="")
            order += [(self.again_row, False), (self.how, False), (self.cue_row, False),
                      (self.other_row, False)]
        else:
            mount = self._main_mount()
            if mount:
                name = os.path.basename(os.path.normpath(mount)) or mount
                self.eject_label.configure(text=f"The main stick “{name}” is still plugged "
                                                "in. Only one USB port?")
            order.append((self.eject_row, bool(mount)))
            b = st.backup
            self.backup_label.configure(text=(
                f"Written to {st.backup_check.name if st.backup_check else 'the stick'}: "
                f"{ui.plural(len(b.written) + len(b.unchanged), 'track')}." if b else
                "Each playlist becomes a numbered folder (01 Warmup/01 Artist - "
                "Title.mp3), so a player browsing folders, or a laptop, finds the next "
                "track fast."))
        f = w["findings"]
        if report and not skipped:
            extra = []
            if which == "backup" and st.backup and st.backup.skipped:
                extra.append((verify.WARN, f"{ui.plural(len(st.backup.skipped), 'track')} "
                              "weren't written", [f"{a}: {b}" for a, b in
                                                  st.backup.skipped[:verify.SHOWN]]))
            f.show_report(report, extra)
        order += [(f, bool(report) and not skipped), (w["tools"], not skipped)]
        # Packed afresh in this order, so a part that comes back lands in its place
        # and the first one showing has no gap above it.
        top_used = which == "backup" or bool(self.xml_label.cget("text")
                                             or self.how_toggle.winfo_manager())
        order[0] = (w["top"], top_used)
        for part, _ in order:
            part.pack_forget()
        if not any(shown for _, shown in order):
            # An emptied frame keeps its last size in Tk unless told otherwise
            w["top"].master.configure(height=1)
        first = True
        for part, shown in order:
            if not shown:
                continue
            gap = 0 if first else ui.SECTION if part in (getattr(self, "cue_row", None),
                                                         getattr(self, "other_row", None),
                                                         w["tools"]) else 6
            first = False
            if part is w["tools"]:
                part.pack(side="bottom", fill="x", pady=(gap, 0))
            elif part is f:
                part.pack(fill="both", expand=True, pady=(gap, 0))
            elif which == "main" and part is self.how:
                part.pack(fill="both", expand=not report, pady=(gap, 0))
            else:
                part.pack(fill="x", pady=(gap, 0))
        from ..gig import formatting
        stick_failed = bool(report and any(c.title == "Stick" and c.level == verify.FAIL
                                           for c in report.checks))
        if formatting.available() and stick_failed:
            w["format"].pack(side="left")
            w["format_link"].pack_forget()
        elif formatting.available():
            w["format"].pack_forget()
            w["format_link"].pack(side="left")
        else:
            w["format"].pack_forget()
            w["format_link"].pack_forget()
        # Checking quickly is for a stick that's been checked once already
        if (st.main or st.earlier) if which == "main" else st.backup_check:
            w["quick"].pack(side="right")
        else:
            w["quick"].pack_forget()

    def _fill_how(self):
        path = self._xml_path()
        t = self.how_txt
        t.configure(state="normal")
        t.delete("1.0", "end")
        if path:
            sections = []
            if profiles.reads_rekordbox(self.profile):
                sections.append(("rekordbox (Pioneer players)", rbxml.how_to(path)))
            if profiles.reads_engine(self.profile):
                sections.append(("Engine DJ (Denon players)", rbxml.engine_how_to(path)))
            for i, (head, lines) in enumerate(sections):
                if len(sections) > 1:
                    t.insert("end", ("\n" if i else "") + head + "\n", "head")
                t.insert("end", "\n".join(lines) + "\n")
        t.configure(state="disabled")

    def _copy_xml(self):
        self.clipboard_clear()
        self.clipboard_append(self._xml_path())
        self.copy_link.configure(text="Copied")
        self.after(2000, lambda: self.copy_link.configure(text="Copy the file's location"))

    def _toggle_how(self):
        self.how_open = not self.how_toggle.cget("text").startswith("Hide")
        self.refresh()

    def _draw_ready(self, step):
        lines = step.text.split("\n")
        self.ready_lines.configure(text="\n".join(lines[1:]))
        b = self.state.backup_check
        shown = [(self.backup_open, not (b and b.ready)),
                 (self.ready_check, not self.state.skip_main), (self.ready_clear, True)]
        for button, _ in shown:
            button.pack_forget()
        for i, button in enumerate(button for button, show in shown if show):
            button.pack(side="left", padx=(ui.GAP if i else 0, 0))

    def _main_mount(self):
        """Where the main stick is mounted right now, or None."""
        st, report = self.state, self.reports.get("main")
        path = report.stick if report else (os.path.join("/Volumes", st.main.name)
                                            if paths.IS_MAC and st.main else None)
        return path if path and os.path.ismount(path) else None

    def eject_main(self):
        mount = self._main_mount()
        if not mount:
            self.refresh()
            return
        name = os.path.basename(os.path.normpath(mount)) or mount
        why = disks.eject(mount)
        if why:
            messagebox.showwarning(APP, f"Couldn't eject “{name}”: {why}")
        else:
            self.app.set_status(f"Ejected “{name}”. Unplug it and plug in the backup stick.")
        self.refresh()

    def _maybe_auto_check(self):
        """Opening the tracks step checks them straight away; nothing to decide there."""
        st = self.state
        sig = self._signature()
        if (self.open_key == "tracks" and st.items is None and not st.earlier
                and self.picker.chosen and not self.app.task.running
                and self.auto_checked != sig):
            self.auto_checked = sig
            self.after_idle(self.check_tracks)

    def _need_ffmpeg(self, e):
        messagebox.showwarning(APP, str(e))

    def _advance(self, keep_notice=False):
        """After an action: open whatever's next."""
        self.open_key = None
        if not keep_notice:
            self.notice_text = ""
        self.refresh()

    # ------------------------------------------------------------- actions
    def start_tracks(self):
        if not self.picker.chosen:
            return
        self.picker.forget_undo()
        self.state.earlier = None       # asked to check again, so check
        self.auto_checked = None
        self.open_key = "tracks"
        self.refresh()

    def check_tracks(self, then=None):
        """Check tonight's tracks; `then` runs once they're checked (write_main uses it)."""
        root = self.app.require_root()
        if not root:
            return
        if not self.picker.chosen:
            if then:
                messagebox.showinfo(APP, "Choose tonight's playlists in step 1 first.")
                self.open("set")
            return
        try:
            lib = convert.library_dir(root, settings.get("gig_library"))
            ffmpeg.require("ffprobe")
        except (convert.LibraryError, ffmpeg.Missing) as e:
            messagebox.showwarning(APP, str(e))
            return
        chosen = self.picker.chosen_sources()
        profile, target = self.profile, self.target
        survey = self._cue_survey()
        from ..gig import sources

        def work(progress, log):
            unreadable = []
            # Read once here, and every later step uses the same snapshot, so the
            # copies, the XML and the stick all match what was checked.
            sets = sources.resolve(chosen, root, log=unreadable.append)
            items = convert.plan(root, convert.playlist_paths(sets), lib, profile, target,
                                 progress)
            return sets, items, unreadable, survey(items, log)

        def done(res):
            st = self.state
            sets, items, self.unreadable, (st.xml_cues, st.xml_tracks) = res
            st.items, st.copied, st.sets, st.earlier = items, False, sets, None
            self._advance(keep_notice=True)     # this check ran by itself; say why
            if then:
                then()

        self.app.task.run(work, done, "Checking tracks")

    def make_copies(self, then=None):
        items = self.state.items
        if not items:
            return
        profile = self.profile
        try:
            ffmpeg.require("ffmpeg")
            if any(it.target == convert.MP3 for it in items
                   if it.status == convert.CONVERT) and not ffmpeg.has_lame():
                raise ffmpeg.Missing("This ffmpeg can't write MP3 (it was built without "
                                     "LAME). Choose AIFF copies, or install a full ffmpeg.")
        except ffmpeg.Missing as e:
            self._need_ffmpeg(e)
            return

        def work(progress, log):
            return convert.run(items, profile, progress=progress, log=log)

        def done(result):
            self.state.copied = True
            self._advance()
            if then and not convert.counts(items)[convert.CONVERT]:
                then()

        self.app.task.run(work, done, "Making gig-safe copies")

    # ----------------------------------------------------------- main stick
    def _xml_path(self):
        try:
            lib = convert.library_dir(self.app.root_dir.get(), settings.get("gig_library"))
        except convert.LibraryError:
            return ""
        return os.path.join(lib, rbxml.FILE_NAME)

    def make_xml(self):
        root = self.app.require_root()
        st = self.state
        if not root:
            return
        if st.items is None:                    # after a restart: check them first
            self.check_tracks(then=self.make_xml)
            return
        if not st.items:
            messagebox.showinfo(APP, "Tonight's playlists have no tracks Sortero can use. "
                                     "Check them in step 1.")
            return
        if convert.counts(st.items)[convert.CONVERT]:
            # A track waiting for its copy has nothing to play yet, and would be
            # left out of the file, so the copies come first, as for the stick.
            self.make_copies(then=self.make_xml)
            return
        work = self._xml_work(self._xml_path(), st.items, list(st.sets or []))

        def done(res):
            st.xml_path, st.xml_cues, st.xml_tracks = res
            self.how_open = True
            self.open_key = "main"
            self.notice_text = ""
            self.refresh()

        self.app.task.run(work, done, "Making the playlists file")

    @staticmethod
    def _cue_places():
        """(the chosen rekordbox XML if it's still there, Mixxx's library if found)."""
        source_xml = settings.get("gig_rekordbox_xml") or None
        if source_xml and not os.path.isfile(source_xml):
            source_xml = None
        return source_xml, mixxx.find_db()

    def _cue_survey(self):
        """A function for a task: (cues.survey counts, tracks) for these items."""
        source_xml, db = self._cue_places()

        def survey(items, log):
            originals = [it.src for it in items if it.status != convert.FAILED]
            finder = cues.Finder(source_xml, db, log=log)
            return cues.survey(originals, finder), len({cues.key(p) for p in originals})
        return survey

    def _recount_cues(self):
        items = self.state.items
        if not items:
            return
        survey = self._cue_survey()

        def done(res):
            self.state.xml_cues, self.state.xml_tracks = res
            self.refresh()
        self.app.task.run(lambda progress, log: survey(items, log), done,
                          "Looking for cue points")

    def _xml_work(self, path, items, sets):
        """A task that writes the playlists file: (path, cue sources, tracks in it)."""
        source_xml, db = self._cue_places()
        shift = bool(settings.get("gig_mp3_cue_shift"))

        def work(progress, log):
            finder = cues.Finder(source_xml, db, log=log)
            tree, counts = rbxml.build(items, sets, rbxml.gig_name(), finder, shift)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            total = int(tree.getroot().find("COLLECTION").get("Entries"))
            return rbxml.write(path, tree), counts, total
        return work

    def choose_cue_xml(self):
        current = settings.get("gig_rekordbox_xml") or ""
        if not messagebox.askokcancel(
                APP, "Set cue points or beat grids in rekordbox? Sortero can use them too.\n\n"
                     "rekordbox keeps them in its own database, which Sortero can't read, "
                     "so export them to a file first: in rekordbox, choose File > Export "
                     "Collection in xml format. Then choose that file here.\n\n"
                     "For a track that has cue points there, Sortero uses those instead of "
                     "Mixxx's or Mixed In Key's. Mixed In Key's own rekordbox.xml export "
                     "works too.\n\nExport again whenever you change cues in rekordbox."):
            return
        path = filedialog.askopenfilename(
            title="Choose your rekordbox collection XML",
            initialdir=os.path.dirname(current) or os.path.expanduser("~"),
            filetypes=[("rekordbox XML", "*.xml"), ("All files", "*")])
        if not path:
            return
        settings.set("gig_rekordbox_xml", path)
        if self.state.xml_path:
            self.state.xml_path = ""        # make it again with these cues
        self.refresh()
        self._recount_cues()

    def cue_timing(self):
        on = bool(settings.get("gig_mp3_cue_shift"))
        msg = ("An MP3 copy can start 25 ms late in a player that ignores the MP3's "
               "gapless information, which would put every cue just before its beat.\n\n"
               "To check once: import one converted track with cue points into rekordbox "
               "and look at its first cue at full zoom. Is it about 25 ms (a sliver) "
               "BEFORE the kick it should sit on?\n\n"
               f"Cues on MP3 copies are now {'moved 25 ms later' if on else 'left as they are'}.")
        answer = messagebox.askyesnocancel(APP, msg + "\n\nYes: the cue sat early, move "
                                                      "them.  No: it sat on the kick.")
        if answer is None:
            return
        settings.set("gig_mp3_cue_shift", bool(answer))
        if self.state.xml_path:
            self.state.xml_path = ""
            self.refresh()

    def _choose_stick(self, which):
        start = "/Volumes" if paths.IS_MAC and os.path.isdir("/Volumes") else None
        return filedialog.askdirectory(title=f"Choose the {which} stick", initialdir=start,
                                       mustexist=True)

    def check_main(self, decode=True):
        stick = self._choose_stick("main")
        if stick:
            sets = self.state.sets
            self._verify(stick, verify.MAIN, decode,
                         rbxml.playlist_names(sets) if sets else None)

    def write_main(self):
        """Write the main stick: copy the set and write its libraries (usbexport), then check it.

        The libraries are written from the playlists file, so it's made first if it
        isn't there yet."""
        st = self.state
        if not self.app.require_root():
            return
        if st.items is None:
            # After a restart only a summary of the last gig is kept, so check the
            # tracks again first, then carry on from here.
            self.check_tracks(then=self.write_main)
            return
        if not st.items:
            messagebox.showinfo(APP, "Tonight's playlists have no tracks Sortero can use. "
                                     "Check them in step 1.")
            return
        if convert.counts(st.items)[convert.CONVERT]:
            # After a restart the copies are usually made already: this finds and
            # reuses them in a moment (convert.run's cache), then carries on.
            self.make_copies(then=self.write_main)
            return
        sets = st.sets
        # Made again every time from the tracks as they are now: one made earlier
        # may predate copies made since, and leaves those tracks off the stick.
        path = self._xml_path() or st.xml_path
        make_xml = self._xml_work(path, st.items, sets or [])
        if not self._short_ok(sets or []):
            return
        stick = self._choose_stick("main")
        if not stick:
            return
        why = usbexport.foreign(stick)
        if why:
            messagebox.showwarning(APP, f"Sortero won't write to this stick: {why}.\n\n"
                                        "Use another stick, or format this one first.")
            return
        if not messagebox.askokcancel(
                APP, "Sortero will copy tonight's tracks to the stick and write the rekordbox "
                     "libraries Pioneer players read, with your playlists, beat grids, cue "
                     "points, waveforms and artwork. Then it checks the stick.\n\n"
                     "This is new: it hasn't been confirmed working on a player yet, so "
                     "bring a second stick you trust."):
            return

        def work(progress, log):
            made = make_xml(progress, log) if make_xml else None
            try:
                return made, usbexport.write(stick, path, progress=progress, log=log), None
            except (usbexport.StickError, OSError) as e:
                return made, None, str(e)

        def done(res):
            made, result, error = res
            if made:
                st.xml_path, st.xml_cues, st.xml_tracks = made
            if error:
                messagebox.showerror(APP, error)
                self.refresh()
                return
            if result.missing:
                messagebox.showwarning(
                    APP, f"{ui.plural(len(result.missing), 'track')} in the playlists file "
                         "couldn't be found on this computer, so they're not on the stick:\n\n"
                         + "\n".join(t for t, _ in result.missing[:10]))
            if result.no_waveform:
                messagebox.showwarning(
                    APP, f"{ui.plural(len(result.no_waveform), 'track')} couldn't be decoded "
                         "for a waveform, so the players will show none for "
                         f"{'it' if len(result.no_waveform) == 1 else 'them'} (the grid and "
                         "cues are there):\n\n"
                         + "\n".join(f"{t}: {why}" for t, why in result.no_waveform[:10]))
            self._verify(stick, verify.MAIN, True,
                         rbxml.playlist_names(sets) if sets else None)

        self.app.task.run(work, done, "Writing the main stick")

    def skip_main(self):
        """No main stick tonight: the folders stick becomes the one to make."""
        if not messagebox.askokcancel(
                APP, "Skip the main stick and make only the folders stick?\n\n"
                     "The players browse it by folder: each playlist is a numbered folder, "
                     "its tracks numbered in set order. There are no playlists, cue points "
                     "or beat grids on the players.\n\n"
                     "You can make a main stick later: open step 3 again."):
            return
        st = self.state
        st.skip_main, st.main = True, None
        self.reports.pop("main", None)
        self._remember()
        self._advance()

    def unskip_main(self):
        self.state.skip_main = False
        self._remember()
        self.open_key = "main"
        self.refresh()

    def _short_ok(self, sets):
        """Say which playlists would reach the stick missing tracks, before any
        are written. An empty playlist on a player looks like a broken stick."""
        lacking = rbxml.short(self.state.items, sets)
        if not lacking:
            return True
        lines = [f"    {name}: " + ("none of its " if not have else f"{have} of ")
                 + ui.plural(total, "track") for name, have, total in lacking[:12]]
        if len(lacking) > 12:
            lines.append(f"    …and {len(lacking) - 12} more")
        empty = sum(1 for _, have, _ in lacking if not have)
        head = (f"{ui.plural(len(lacking), 'playlist')} of {len(sets)} would go on the stick "
                "missing tracks" + (f", {empty} of them empty" if empty else "") + ":")
        return messagebox.askyesno(
            APP, head + "\n\n" + "\n".join(lines) + "\n\nSortero can't use those tracks. "
                 "Step 2 says why for each: usually the playlist points at a file that "
                 "isn't there any more, as an old playlist from another computer or "
                 "drive does.\n\nWrite the stick anyway, without them?",
            icon="warning", default="no")

    def check_backup(self, decode=True):
        stick = self._choose_stick("backup")
        if stick:
            self._verify(stick, verify.BACKUP, decode)

    def _verify(self, stick, role, decode=True, playlists=None):
        try:
            ffmpeg.require("ffprobe")
            ffmpeg.require("ffmpeg")
        except ffmpeg.Missing as e:
            self._need_ffmpeg(e)
            return
        profile = self.profile
        originals = [it.playable for it in self.state.items or [] if it.playable]

        def work(progress, log):
            return verify.verify(stick, profile, decode=decode, sources=originals or None,
                                 progress=progress, role=role, playlists=playlists)

        def done(report):
            name = os.path.basename(os.path.normpath(stick)) or stick
            if role == verify.MAIN:
                what = " and ".join(report.libraries)
                summary = (f"{what} {'library' if len(report.libraries) == 1 else 'libraries'}, "
                           if what else "") + ui.plural(report.audio, "track")
            else:
                summary = ui.plural(report.audio, "track") + " in folders"
            chk = checklist.StickCheck(name, time.time(), report.ready, report.calm_verdict,
                                       summary, report.first_problem)
            self.reports[role] = report
            self.how_open = False           # the findings need the room now
            if role == verify.MAIN:
                self.state.main = chk
            else:
                self.state.backup_check = chk
            self._remember()
            # Stay put while there's something to read; otherwise on to what's next
            if report.ready and not report.warnings:
                self._advance()
            else:
                self.open_key = role
                self.notice_text = ""
                self.refresh()

        self.app.task.run(work, done, f"Checking the {role} stick")

    # ---------------------------------------------------------- backup stick
    def write_backup(self):
        root = self.app.require_root()
        items = self.state.items
        if not root or not items:
            if self.state.earlier:
                messagebox.showinfo(APP, "Check the tracks again first (step 2): the "
                                         "backup is written from the checked tracks.")
            return
        sets, missing = backup.sets_from(items, self.state.sets or [])
        stick = self._choose_stick("backup")
        if not stick:
            return
        try:
            p = backup.plan(stick, sets)
        except backup.BackupError as e:
            messagebox.showwarning(APP, str(e))
            return
        name = os.path.basename(os.path.normpath(stick)) or stick
        free = backup.free_space(stick)
        if p.bytes > free:
            messagebox.showwarning(APP, f"{name} needs {human_size(p.bytes)} free and has "
                                        f"{human_size(free)}. Free some space, or use a "
                                        "bigger stick.")
            return
        lines = [f"Write {ui.plural(len(sets), 'playlist')} to {name} as folders?", ""]
        lines += [f"    {folder}" for folder in p.folders]
        lines.append("")
        if p.copy:
            lines.append(f"{ui.plural(len(p.copy), 'track')} to copy, "
                         f"{human_size(p.bytes)}.")
        if p.keep:
            lines.append(f"{len(p.keep):,} already there from last time.")
        if p.remove:
            lines.append(f"{len(p.remove):,} left from an earlier backup will be removed.")
        if p.blocked or missing:
            lines.append(f"{len(p.blocked) + len(missing):,} can't go on; the step will "
                         "say why.")
        lines.append("\nFiles Sortero didn't put on the stick are never touched. "
                     "Afterwards the stick is checked.")
        if not messagebox.askyesno(APP, "\n".join(lines)):
            return

        def work(progress, log):
            return backup.run(p, progress=progress, log=log)

        def done(res):
            res.skipped += [(f"{pl}: {os.path.basename(src)}", "it has no playable copy")
                            for pl, src in missing]
            self.state.backup = res
            self._verify(stick, verify.BACKUP, decode=True)

        self.app.task.run(work, done, "Writing the backup stick")

    # ---------------------------------------------------------------- tools
    def format_stick(self):
        from . import format_stick
        root = self.app.require_root()
        if not root:
            return
        keep = [root, paths.data_dir()]
        try:
            keep.append(convert.library_dir(root, settings.get("gig_library")))
        except convert.LibraryError:
            pass

        def done(label, output):
            self.notice_text = (f"Formatted as FAT32 on one MBR partition, named {label}. "
                                "Now put the set on it, then check it.")
            self.refresh()

        format_stick.open_for(self.app, keep, done)

    def choose_library(self):
        root = self.app.require_root()
        if not root:
            return
        d = filedialog.askdirectory(title="Choose where gig-safe copies go")
        if not d:
            return
        try:
            convert.library_dir(root, d)
        except convert.LibraryError as e:
            messagebox.showwarning(APP, str(e))
            return
        settings.set("gig_library", d)
        self.picker.forget_undo()
        self.set_changed("You moved the copies folder")

    def show_library(self):
        root = self.app.require_root()
        if not root:
            return
        lib = convert.library_dir(root, settings.get("gig_library"))
        if os.path.isdir(lib):
            paths.reveal(lib)
        else:
            messagebox.showinfo(APP, f"No copies yet. They'll go in:\n\n{lib}")

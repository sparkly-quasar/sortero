"""Place tracks one at a time.

Where there is genuinely nothing to infer a genre from, the honest answer is a
person deciding - so make deciding fast. One track at a time, every hint on
screen (tag, where it was staged from, Discogs, key and energy), a preview
button, recent choices on the number keys, and the real folders of *this*
collection to choose from rather than a fixed vocabulary.

Choices are saved as you go, so closing half way loses nothing. Nothing moves
until you say so, and the move is journalled like any other.
"""
import json, os, re, shutil, subprocess, threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import paths, settings, organize, playlists, membership, genres, folders, preview, ui
from .journal import Journal, prune_empty
from .library import PROTECTED
from .organize import TRACKS_DIR, target_filename, safe
from .tagio import Track

TITLE = "Choose a folder for each track"
DECISIONS = "review-decisions.json"


# ------------------------------------------------------------ persistence
def _decisions_file():
    return os.path.join(paths.data_dir(), DECISIONS)


def load_decisions():
    try:
        with open(_decisions_file()) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_decisions(d):
    with open(_decisions_file(), "w") as fh:
        json.dump(d, fh, indent=1)


# -------------------------------------------------------------- applying
def _unique(path):
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        n += 1
        path = f"{stem} ({n}){ext}"
    return path


def _append_playlist(root, name, dest, journal):
    fp = os.path.join(playlists.playlist_dir(root), safe(name, 100) + ".m3u8")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as fh:
            journal.wrote(fp, fh.read())
    else:
        journal.created(fp)
    playlists.append(root, name, [dest])


def inside(root, path):
    return os.path.normpath(path).startswith(os.path.normpath(root) + os.sep)


def apply_decisions(root, items, log=print, progress=None):
    """items: [(Rec, folder relative to root)]. Returns (journal, moved, failed).

    Each track moves into its folder, takes the folder's name as its genre,
    rejoins any playlists it was staged out of, and every playlist entry that
    pointed at its old location follows it.
    """
    j = Journal("choose-folder", root)
    remap, restored = {}, []
    moved = failed = 0
    total = len(items) or 1
    for i, (rec, rel) in enumerate(items):
        if progress and i % 5 == 0:
            progress(i, total)
        dest_dir = os.path.normpath(os.path.join(root, rel))
        if not os.path.exists(rec.path) or not inside(root, dest_dir):
            failed += 1
            continue
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, target_filename(rec))
            if os.path.normpath(dest) != os.path.normpath(rec.path):
                dest = _unique(dest)
                shutil.move(rec.path, dest)
                j.moved(rec.path, dest)
                remap[rec.path] = dest
            genre = (folders.genre_of_folder(os.path.relpath(dest_dir, root))
                     or os.path.basename(dest_dir))
            t = Track(dest)
            if t.ok and (t.get("genre") or "") != genre:
                old = t.get("genre")
                t.set("genre", genre)
                if t.save():
                    j.tagged(dest, {"genre": {"old": old, "new": genre}})
            owed = membership.claim(rec)
            for name in owed:
                _append_playlist(root, name, dest, j)
            if owed or membership.claim_genre(rec):
                restored.append(rec)
            moved += 1
        except Exception as e:
            failed += 1
            log(f"  ! {os.path.basename(rec.path)}: {e}")
    if remap:
        n = playlists.remap(root, remap, journal=j)
        if n:
            log(f"updated {n} playlist entries")
    if restored:
        membership.release(restored)
    prune_empty(root, keep=PROTECTED)
    if progress:
        progress(total, total)
    path = j.save()
    log(f"placed {moved} tracks ({failed} failed) | journal: {path}")
    return path, moved, failed


# ---------------------------------------------------------------- dialog
class ReviewDialog(tk.Toplevel):
    def __init__(self, parent, app, recs, on_close=None, exclude_folder=None, context=None):
        super().__init__(parent)
        self.app, self._parent, self.on_close = app, parent, on_close
        self.root_dir = app.root_dir.get()
        self.recs = [r for r in recs if os.path.exists(r.path)]
        saved = load_decisions()
        self.decisions = {r.path: saved[r.path] for r in self.recs if r.path in saved}
        self.cache = genres.load_cache()
        self.detail = settings.get("genre_detail") or "broad"
        self.choices = folders.homes(self.root_dir)
        if exclude_folder:
            # the folder being reviewed is where these tracks are *leaving* -
            # filing one back into it would tag its genre as the folder's name
            gone = os.path.normpath(exclude_folder)
            self.choices = [c for c in self.choices if os.path.normpath(c[0]) != gone]
        known = {rel for rel, _ in self.choices}
        for rel in self.decisions.values():
            if rel not in known:
                self.choices.append((rel, 0))
                known.add(rel)
        self.recent = []
        self.i = 0
        self.player = None              # fallback: afplay / default app, no seeking
        self.preview = preview.Player()
        self._seekable = False
        self._dragging = False
        self._tick_gen = 0
        self.busy = False
        self.changed = False
        self._close_after_apply = False

        self.title(f"{TITLE} — {context}" if context else TITLE)
        self.geometry("940x700")
        self.minsize(820, 600)
        self.transient(parent)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        if not self.recs:
            self.after(50, self._nothing)
            return
        self.show()
        self.grab_set()
        self.filter_entry.focus_set()

    # -- layout ------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=(16, 12, 16, 4))
        top.pack(fill="x")
        self.pos_lab = ttk.Label(top, font=ui.HEADING)
        self.pos_lab.pack(side="left")
        self.count_lab = ttk.Label(top, style="Muted.TLabel")
        self.count_lab.pack(side="right")
        self.bar = ttk.Progressbar(self, maximum=max(len(self.recs), 1))
        self.bar.pack(fill="x", padx=16)

        info = ttk.Frame(self, padding=(16, 10))
        info.pack(fill="x")
        self.title_lab = ttk.Label(info, font=ui.TITLE,
                                   wraplength=890, justify="left")
        self.title_lab.pack(anchor="w")
        self.meta_lab = ttk.Label(info, font=ui.MONO, justify="left")
        self.meta_lab.pack(anchor="w", pady=(4, 0))
        self.hint_lab = ttk.Label(info, style="Muted.TLabel", wraplength=890, justify="left")
        self.hint_lab.pack(anchor="w", pady=(4, 0))

        tr = ttk.Frame(self, padding=(16, 0, 16, 6))
        tr.pack(fill="x")
        self.play_btn = ttk.Button(tr, text="▶ Play", width=9, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.back15_btn = ttk.Button(tr, text="−15s", width=5, command=lambda: self.jump(-15))
        self.back15_btn.pack(side="left", padx=(6, 0))
        self.fwd15_btn = ttk.Button(tr, text="+15s", width=5, command=lambda: self.jump(15))
        self.fwd15_btn.pack(side="left", padx=(2, 8))
        self.elapsed_lab = ttk.Label(tr, text="0:00", font=ui.MONO, width=6, anchor="e")
        self.elapsed_lab.pack(side="left")
        # the total sits at the far right and keeps its width; the slider then
        # expands into whatever is left between the two times
        self.total_lab = ttk.Label(tr, text="0:00", font=ui.MONO, width=6, anchor="w")
        self.total_lab.pack(side="right")
        self.scrub_var = tk.DoubleVar(value=0.0)
        self.scrub = ttk.Scale(tr, from_=0, to=1, orient="horizontal",
                               variable=self.scrub_var, command=self._scrub_moved)
        self.scrub.pack(side="left", fill="x", expand=True, padx=8)
        self.scrub.bind("<ButtonPress-1>", self._scrub_start)
        self.scrub.bind("<ButtonRelease-1>", self._scrub_end)

        act = ttk.Frame(self, padding=(16, 0, 16, 8))
        act.pack(fill="x")
        ttk.Button(act, text="Show file",
                   command=lambda: paths.reveal(self.current.path)).pack(side="left")
        self.discogs_btn = ttk.Button(act, text="Ask Discogs", command=self.ask_discogs)
        self.discogs_btn.pack(side="left")
        self.sugg_btn = ttk.Button(act, text="No suggestion", command=self.use_suggestion,
                                   state="disabled")
        self.sugg_btn.pack(side="left", padx=(14, 0))
        self.status_lab = ttk.Label(act, style="Good.TLabel")
        self.status_lab.pack(side="left", padx=10)

        body = ttk.Frame(self, padding=(16, 0))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Folder — type to filter, or type a new name").pack(anchor="w")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(left, textvariable=self.filter_var)
        self.filter_entry.pack(fill="x", pady=(2, 4))
        self.filter_var.trace_add("write", lambda *a: self.refill())
        self.filter_entry.bind("<Key>", self._entry_key)
        self.filter_entry.bind("<Down>", self._to_list)
        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=True)
        self.lb = tk.Listbox(lf, font=ui.MONO, activestyle="none",
                             exportselection=False)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")          # before the list: see ui.tree
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", lambda e: self.assign())
        self.lb_values = []

        right = ttk.Frame(body, padding=(16, 0, 0, 0))
        right.pack(side="left", fill="y")
        ttk.Label(right, text="Recent — press 1–9").pack(anchor="w")
        self.quick = ttk.Frame(right)
        self.quick.pack(anchor="w", fill="x", pady=(4, 0))

        nav = ttk.Frame(self, padding=(16, 10))
        nav.pack(fill="x")
        self.back_btn = ttk.Button(nav, text="← Back", command=self.back)
        self.back_btn.pack(side="left")
        self.skip_btn = ttk.Button(nav, text="Skip", command=self.skip)
        self.skip_btn.pack(side="left", padx=6)
        self.clear_btn = ttk.Button(nav, text="Clear choice", command=self.clear_choice)
        self.clear_btn.pack(side="left")
        self.assign_btn = ttk.Button(nav, text="File here  ↵", command=self.assign)
        self.assign_btn.pack(side="left", padx=(16, 0))
        ttk.Button(nav, text="Close", command=self.close).pack(side="right")
        self.apply_btn = ttk.Button(nav, text="Move 0 tracks now", command=self.apply)
        self.apply_btn.pack(side="right", padx=8)

        self.bind("<Return>", lambda e: self.assign())
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Key>", self._key)

    # -- keys --------------------------------------------------------------
    def _entry_key(self, e):
        if e.char and e.char in "123456789" and not self.filter_var.get():
            self._quick_pick(int(e.char) - 1)
            return "break"

    def _key(self, e):
        if self.busy or self.focus_get() is self.filter_entry:
            return
        if e.char and e.char in "123456789":
            self._quick_pick(int(e.char) - 1)
        elif e.keysym == "Right":
            self.skip()
        elif e.keysym == "Left":
            self.back()
        elif e.keysym == "space":
            self.toggle_play()

    def _to_list(self, e):
        if self.lb.size():
            self.lb.focus_set()
            if not self.lb.curselection():
                self.lb.selection_set(0)
        return "break"

    def _quick_pick(self, idx):
        if 0 <= idx < len(self.recent):
            self.assign(self.recent[idx])

    # -- state -------------------------------------------------------------
    @property
    def current(self):
        return self.recs[self.i]

    def _new_rel(self, text):
        parts = [safe(p, 60) for p in re.split(r"[\\/]+", (text or "").strip()) if p.strip()]
        if not parts:
            return None
        if (len(parts) == 1 and not os.path.isdir(os.path.join(self.root_dir, parts[0]))
                and folders.uses_tracks_layout(self.root_dir, self.choices)):
            parts = [TRACKS_DIR] + parts
        return os.path.join(*parts)

    def _home_for(self, genre):
        p = folders.existing_home(self.root_dir, genre, self.choices)
        return os.path.relpath(p, self.root_dir) if p else self._new_rel(genre)

    def _suggest(self, r):
        for v in self.cache.get(genres.key_for(r)) or []:
            g = organize.canon_genre(v, detail=self.detail) or organize.canon_genre(v)
            if g:
                return g, "Discogs"
        g = organize.canon_genre(r.genre, strict=True, detail=self.detail)
        if g:
            return g, "genre tag"
        g = membership.claim_genre(r)
        if g:
            return g, "where it came from"
        return None, None

    def _persist(self, removed=()):
        allp = load_decisions()
        mine = {r.path for r in self.recs} | set(removed)
        for p in list(allp):
            if p in mine and p not in self.decisions:
                allp.pop(p)
        allp.update(self.decisions)
        save_decisions(allp)

    def _counts(self):
        n = len(self.decisions)
        self.count_lab.configure(text=f"{n} chosen · {len(self.recs) - n} to go")
        self.apply_btn.configure(text=f"Move {n} track{'s' if n != 1 else ''} now",
                                 state="normal" if n and not self.busy else "disabled")

    # -- display -----------------------------------------------------------
    def show(self):
        self.stop_play()
        self._prepare_transport()
        r = self.current
        self.pos_lab.configure(text=f"Track {self.i + 1} of {len(self.recs)}")
        self.bar.configure(maximum=max(len(self.recs), 1), value=self.i)
        self._counts()
        self.title_lab.configure(text=r.display)

        bits = []
        if r.camelot or r.key:
            bits.append(f"key {r.camelot or r.key}")
        if r.energy is not None:
            bits.append(f"energy {r.energy}")
        if r.bpm:
            bits.append(f"{r.bpm} bpm")
        if r.duration:
            bits.append(f"{int(r.duration // 60)}:{int(r.duration % 60):02d}")
        bits.append(os.path.basename(r.path))
        self.meta_lab.configure(text="  ·  ".join(bits))

        hints = []
        if r.genre:
            hints.append(f"Genre tag: {r.genre}")
        if r.album:
            hints.append(f"Album: {r.album}")
        owed = membership.claim(r)
        if owed:
            hints.append("Was in: " + ", ".join(owed[:4]))
        raw = self.cache.get(genres.key_for(r))
        if raw:
            hints.append("Discogs: " + ", ".join(raw[:5]))
        elif raw == []:
            hints.append("Discogs: no match")
        self.hint_lab.configure(text="   ·   ".join(hints)
                                or "Nothing in the file to go on — trust your ears.")

        sugg, src = self._suggest(r)
        self._sugg = sugg
        if sugg:
            self.sugg_btn.configure(text=f"Use suggestion: {sugg} ({src})", state="normal")
        else:
            self.sugg_btn.configure(text="No suggestion", state="disabled")

        decided = self.decisions.get(r.path)
        self.filter_var.set("")
        self.refill(preselect=decided or (self._home_for(sugg) if sugg else None))
        self.status_lab.configure(text=f"Chosen: {decided}" if decided else "")
        self.back_btn.configure(state="normal" if self.i else "disabled")
        self.clear_btn.configure(state="normal" if decided else "disabled")
        self._render_quick()

    def refill(self, preselect=None):
        text = self.filter_var.get().strip()
        low = text.lower()
        self.lb.delete(0, "end")
        self.lb_values = []
        if text and not any(rel.lower() == low or os.path.basename(rel).lower() == low
                            for rel, _ in self.choices):
            new_rel = self._new_rel(text)
            if new_rel:
                self.lb.insert("end", f"+ New folder: {new_rel}")
                self.lb_values.append(new_rel)
        items = [c for c in self.choices if low in c[0].lower()] if low else self.choices
        for rel, n in items:
            self.lb.insert("end", f"{rel}  ({n})" if n else rel)
            self.lb_values.append(rel)
        target = preselect if preselect is not None else (self.lb_values[0]
                                                            if text and self.lb_values else None)
        if target is not None and target not in self.lb_values and not text:
            self.lb.insert(0, f"+ New folder: {target}")
            self.lb_values.insert(0, target)
        if target in self.lb_values:
            k = self.lb_values.index(target)
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(k)
            self.lb.see(k)

    def _render_quick(self):
        for w in self.quick.winfo_children():
            w.destroy()
        for k, rel in enumerate(self.recent[:9], 1):
            ttk.Button(self.quick, text=f"{k}   {rel}",
                       command=lambda r=rel: self.assign(r)).pack(fill="x", pady=1)

    # -- actions -----------------------------------------------------------
    def _selected(self):
        sel = self.lb.curselection()
        if sel:
            return self.lb_values[sel[0]]
        return self._new_rel(self.filter_var.get())

    def assign(self, rel=None):
        if self.busy or not self.recs:
            return
        rel = rel or self._selected()
        if not rel:
            self.bell()
            return
        self.decisions[self.current.path] = rel
        if rel not in {c for c, _ in self.choices}:
            self.choices.append((rel, 0))
        if rel in self.recent:
            self.recent.remove(rel)
        self.recent.insert(0, rel)
        del self.recent[9:]
        self._persist()
        self._advance()

    def use_suggestion(self):
        if self._sugg:
            self.assign(self._home_for(self._sugg))

    def skip(self):
        if not self.busy:
            self._advance()

    def back(self):
        if self.busy or not self.i:
            return
        self.i -= 1
        self.show()

    def clear_choice(self):
        if self.busy:
            return
        self.decisions.pop(self.current.path, None)
        self._persist()
        self.show()

    def _advance(self):
        if self.i < len(self.recs) - 1:
            self.i += 1
            self.show()
            return
        self._counts()
        n = len(self.decisions)
        if n and messagebox.askyesno(
                TITLE, f"That's the last one. Move the {n} tracks you've placed now?",
                parent=self):
            self.apply(confirm=False)
        else:
            self.show()

    # -- audio preview -----------------------------------------------------
    @staticmethod
    def _clock(sec):
        sec = max(0, int(sec or 0))
        return f"{sec // 60}:{sec % 60:02d}"

    def _prepare_transport(self):
        """Reset the scrub bar for the current track."""
        r = self.current
        dur = float(r.duration or 0)
        self._seekable = bool(dur) and preview.can_seek(r.path)
        state = "normal" if self._seekable else "disabled"
        self.scrub.configure(to=max(dur, 1.0), state=state)
        self.back15_btn.configure(state=state)
        self.fwd15_btn.configure(state=state)
        self.scrub_var.set(0.0)
        self.elapsed_lab.configure(text="0:00")
        self.total_lab.configure(text=self._clock(dur) if dur else "-:--")

    def toggle_play(self):
        if not self._seekable:
            return self._fallback_play()
        p = self.preview
        try:
            if p.active and not p.paused:
                p.pause()
                self.play_btn.configure(text="▶ Play")
                return
            if p.active:
                p.resume()
            else:
                p.load(self.current.path, self.current.duration)
                p.play(at=self.scrub_var.get())
        except Exception as e:
            p.stop()
            self._seekable = False
            self.scrub.configure(state="disabled")
            self.status_lab.configure(text=f"Can't scrub this file ({e}) - playing from the start.")
            return self._fallback_play()
        self.play_btn.configure(text="❚❚ Pause")
        self._tick_gen += 1
        self._tick_player(self._tick_gen)

    def _tick_player(self, gen):
        p = self.preview
        if gen != self._tick_gen or not p.active:
            return
        try:
            if p.finished():
                p.stop()
                self.play_btn.configure(text="▶ Play")
                self.scrub_var.set(0.0)
                self.elapsed_lab.configure(text="0:00")
                return
            if not self._dragging:
                pos = p.position()
                self.scrub_var.set(pos)
                self.elapsed_lab.configure(text=self._clock(pos))
        except tk.TclError:
            return
        if not p.paused:
            self.after(200, lambda: self._tick_player(gen))

    def _scrub_start(self, e):
        if self._seekable:
            self._dragging = True

    def _scrub_moved(self, value):
        self.elapsed_lab.configure(text=self._clock(float(value)))

    def _scrub_end(self, e):
        if not self._seekable:
            return
        self._dragging = False
        self.after_idle(self._commit_scrub)      # let the scale settle on its final value

    def _commit_scrub(self):
        pos = self.scrub_var.get()
        if self.preview.active:
            try:
                self.preview.seek(pos)
            except Exception as e:
                self.status_lab.configure(text=f"Couldn't jump there: {e}")
        self.elapsed_lab.configure(text=self._clock(pos))

    def jump(self, delta):
        if not self._seekable:
            return
        p = self.preview
        base = p.position() if p.active else self.scrub_var.get()
        pos = min(max(0.0, base + delta), float(self.current.duration or 0))
        self.scrub_var.set(pos)
        self.elapsed_lab.configure(text=self._clock(pos))
        if p.active:
            p.seek(pos)

    def _fallback_play(self):
        """No seeking available: play from the start with what the OS provides."""
        if self.player and self.player.poll() is None:
            self.stop_play()
            return
        path = self.current.path
        if os.path.splitext(path)[1].lower() in (".m4a", ".mp4", ".aac"):
            self.status_lab.configure(text="Scrubbing isn't available for AAC files - "
                                           "playing from the start.")
        try:
            if paths.IS_MAC:
                self.player = subprocess.Popen(["afplay", path])
                self.play_btn.configure(text="■ Stop")
                self._watch_play()
            elif paths.IS_WIN:
                os.startfile(path)          # no dependable quiet player; use the default app
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror(TITLE, f"Couldn't play it: {e}", parent=self)

    def _watch_play(self):
        if self.player and self.player.poll() is None:
            self.after(500, self._watch_play)
        else:
            try:
                self.play_btn.configure(text="▶ Play")
            except tk.TclError:
                pass

    def stop_play(self):
        self._tick_gen += 1
        try:
            self.preview.stop()
        except Exception:
            pass
        if self.player and self.player.poll() is None:
            self.player.terminate()
        self.player = None
        try:
            self.play_btn.configure(text="▶ Play")
        except tk.TclError:
            pass

    # -- discogs -----------------------------------------------------------
    def ask_discogs(self):
        if self.busy:
            return
        r = self.current
        if not genres.worth_asking(r):
            self.status_lab.configure(text="No artist and title to search with.")
            return
        self.discogs_btn.configure(state="disabled")
        self.status_lab.configure(text="Asking Discogs…")
        box = {}

        def work():
            try:
                box["res"] = genres.lookup(r, self.cache)
                genres.save_cache(self.cache)
            except Exception as e:
                box["err"] = str(e)

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                self.after(200, poll)
                return
            try:
                self.discogs_btn.configure(state="normal")
            except tk.TclError:
                return
            if "err" in box:
                self.status_lab.configure(text=f"Discogs: {box['err']}")
                return
            if self.recs and self.current is r:
                self.show()
                if not box["res"][1]:
                    self.status_lab.configure(text="Discogs had nothing for this one.")

        self.after(200, poll)

    # -- applying ----------------------------------------------------------
    def _nav(self, state):
        for b in (self.back_btn, self.skip_btn, self.clear_btn, self.assign_btn,
                  self.apply_btn, self.discogs_btn, self.sugg_btn):
            b.configure(state=state)

    def apply(self, confirm=True):
        items = [(r, self.decisions[r.path]) for r in self.recs if r.path in self.decisions]
        if not items or self.busy:
            return
        if confirm and not messagebox.askyesno(
                TITLE, f"Move {len(items)} tracks into the folders you chose?\n\n"
                       "Each takes its folder's name as its genre, rejoins any "
                       "playlists it was staged out of, and the move is undoable "
                       "from History.", parent=self):
            self._close_after_apply = False
            return
        self.stop_play()
        self.busy = True
        self._nav("disabled")
        self.bar.configure(maximum=len(items), value=0)
        box = {}

        def progress(i, total):
            box["p"] = (i, total)

        def work():
            try:
                box["res"] = apply_decisions(self.root_dir, items, log=lambda m: None,
                                             progress=progress)
            except Exception as e:
                box["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if "p" in box:
                self.bar.configure(value=box["p"][0])
            if t.is_alive():
                self.after(150, poll)
                return
            self.busy = False
            self._nav("normal")
            if "err" in box:
                messagebox.showerror(TITLE, str(box["err"]), parent=self)
                self.show()
                return
            _, moved, failed = box["res"]
            self.changed = self.changed or bool(moved)
            done = {r.path for r, _ in items}
            self.recs = [r for r in self.recs if r.path not in done]
            for p in done:
                self.decisions.pop(p, None)
            self._persist(removed=done)
            messagebox.showinfo(TITLE, f"Moved {moved} tracks."
                                + (f"\n{failed} couldn't be moved." if failed else ""),
                                parent=self)
            if not self.recs or self._close_after_apply:
                self._finish()
                return
            self.i = min(self.i, len(self.recs) - 1)
            self.show()

        self.after(150, poll)

    def _nothing(self):
        messagebox.showinfo(TITLE, "Nothing to place — those files have moved.", parent=self)
        self._finish()

    def close(self):
        if self.busy:
            return
        pending = len(self.decisions)
        if pending:
            ans = messagebox.askyesnocancel(
                TITLE, f"You've chosen folders for {pending} tracks but not moved them.\n\n"
                       "Move them now? No keeps your choices for next time.", parent=self)
            if ans is None:
                return
            if ans:
                self._close_after_apply = True
                self.apply(confirm=False)
                return
        self._finish()

    def _finish(self):
        self.stop_play()
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

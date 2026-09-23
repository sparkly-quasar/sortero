"""A compact player bar: play/pause, ±15s, a scrub bar, and tap tempo.

Built on preview.Player. Files it can't seek (AAC) fall back to playing from
the start with whatever the OS provides.
"""
import os, subprocess, time
import tkinter as tk
from tkinter import ttk

from . import paths, preview, ui

TAP_RESET = 2.0         # a pause this long starts a new tap run
TAP_KEEP = 16           # tempo from the most recent taps only


def clock(sec):
    sec = max(0, int(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


class Transport(ttk.Frame):
    """load(path, duration, start) a track; the bar handles the rest.

    on_tap(bpm or None) reports the tempo tapped so far.
    """

    def __init__(self, parent, on_tap=None):
        super().__init__(parent)
        self.on_tap = on_tap
        self.path, self.duration = None, 0.0
        self.player = preview.Player()
        self.proc = None                # fallback player, no seeking
        self._seekable = False
        self._dragging = False
        self._gen = 0
        self._taps = []

        self.play_btn = ttk.Button(self, text="▶ Play", width=9, command=self.toggle,
                                   state="disabled")
        self.play_btn.pack(side="left")
        self.back_btn = ttk.Button(self, text="−15s", width=5, command=lambda: self.jump(-15))
        self.back_btn.pack(side="left", padx=(6, 0))
        self.fwd_btn = ttk.Button(self, text="+15s", width=5, command=lambda: self.jump(15))
        self.fwd_btn.pack(side="left", padx=(2, 8))
        self.elapsed = ttk.Label(self, text="0:00", font=ui.MONO, width=6, anchor="e")
        self.elapsed.pack(side="left")
        self.pos = tk.DoubleVar(value=0.0)
        self.scrub = ttk.Scale(self, from_=0, to=1, orient="horizontal", variable=self.pos,
                               command=lambda v: self.elapsed.configure(text=clock(float(v))))
        self.scrub.pack(side="left", fill="x", expand=True, padx=8)
        self.scrub.bind("<ButtonPress-1>", self._drag_start)
        self.scrub.bind("<ButtonRelease-1>", self._drag_end)
        self.total = ttk.Label(self, text="-:--", font=ui.MONO, width=6, anchor="w")
        self.total.pack(side="left")
        self.tap_btn = ttk.Button(self, text="Tap", width=6, command=self.tap,
                                  state="disabled")
        self.tap_btn.pack(side="left", padx=(8, 0))
        self._enable(False)

    # -- loading -----------------------------------------------------------
    def load(self, path, duration, start=0.0):
        """Queue a track, positioned at start seconds. Doesn't play it yet."""
        was_playing = self.playing
        self.stop()
        self.path, self.duration = path, float(duration or 0)
        self._seekable = bool(self.duration) and preview.can_seek(path)
        self.scrub.configure(to=max(self.duration, 1.0))
        self._enable(self._seekable)
        self.play_btn.configure(state="normal")
        self.tap_btn.configure(state="normal")
        start = min(max(0.0, start), self.duration) if self._seekable else 0.0
        self.pos.set(start)
        self.elapsed.configure(text=clock(start))
        self.total.configure(text=clock(self.duration) if self.duration else "-:--")
        self.reset_taps()
        if was_playing:
            self.toggle()

    def clear(self):
        self.stop()
        self.path = None
        self.play_btn.configure(state="disabled")
        self.tap_btn.configure(state="disabled")
        self._enable(False)
        self.pos.set(0.0)
        self.elapsed.configure(text="0:00")
        self.total.configure(text="-:--")
        self.reset_taps()

    def _enable(self, on):
        state = "normal" if on else "disabled"
        for w in (self.scrub, self.back_btn, self.fwd_btn):
            w.configure(state=state)

    # -- playing -----------------------------------------------------------
    @property
    def playing(self):
        p = self.player
        return (p.active and not p.paused) or bool(self.proc and self.proc.poll() is None)

    def toggle(self):
        if not self.path:
            return
        if not self._seekable:
            return self._fallback()
        p = self.player
        try:
            if p.active and not p.paused:
                p.pause()
                self.play_btn.configure(text="▶ Play")
                return
            if p.active:
                p.resume()
            else:
                p.load(self.path, self.duration)
                p.play(at=self.pos.get())
        except Exception:
            p.stop()
            self._seekable = False
            self._enable(False)
            return self._fallback()
        self.play_btn.configure(text="❚❚ Pause")
        self._gen += 1
        self._tick(self._gen)

    def _tick(self, gen):
        p = self.player
        if gen != self._gen or not p.active:
            return
        try:
            if p.finished():
                p.stop()
                self.play_btn.configure(text="▶ Play")
                self.pos.set(0.0)
                self.elapsed.configure(text="0:00")
                return
            if not self._dragging:
                t = p.position()
                self.pos.set(t)
                self.elapsed.configure(text=clock(t))
        except tk.TclError:
            return
        if not p.paused:
            self.after(200, lambda: self._tick(gen))

    def jump(self, delta):
        if not self._seekable:
            return
        p = self.player
        base = p.position() if p.active else self.pos.get()
        t = min(max(0.0, base + delta), self.duration)
        self.pos.set(t)
        self.elapsed.configure(text=clock(t))
        if p.active:
            p.seek(t)

    def _drag_start(self, e):
        if self._seekable:
            self._dragging = True

    def _drag_end(self, e):
        if not self._seekable:
            return
        self._dragging = False
        self.after_idle(self._commit)       # let the scale settle on its final value

    def _commit(self):
        t = self.pos.get()
        if self.player.active:
            try:
                self.player.seek(t)
            except Exception:
                pass
        self.elapsed.configure(text=clock(t))

    def _fallback(self):
        """No seeking available: play from the start with what the OS provides."""
        if self.proc and self.proc.poll() is None:
            self.stop()
            return
        try:
            if paths.IS_MAC:
                self.proc = subprocess.Popen(["afplay", self.path])
                self.play_btn.configure(text="■ Stop")
                self._watch()
            elif paths.IS_WIN:
                os.startfile(self.path)
            else:
                subprocess.Popen(["xdg-open", self.path])
        except Exception:
            pass

    def _watch(self):
        if self.proc and self.proc.poll() is None:
            self.after(500, self._watch)
        else:
            try:
                self.play_btn.configure(text="▶ Play")
            except tk.TclError:
                pass

    def stop(self):
        self._gen += 1
        try:
            self.player.stop()
        except Exception:
            pass
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        try:
            self.play_btn.configure(text="▶ Play")
        except tk.TclError:
            pass

    # -- tap tempo ---------------------------------------------------------
    def tap(self):
        now = time.monotonic()
        if self._taps and now - self._taps[-1] > TAP_RESET:
            self._taps = []
        self._taps = (self._taps + [now])[-TAP_KEEP:]
        if self.on_tap:
            self.on_tap(self.tapped())

    def tapped(self):
        """BPM from the taps so far, or None until there are enough."""
        t = self._taps
        if len(t) < 4:
            return None
        return 60.0 * (len(t) - 1) / (t[-1] - t[0])

    def reset_taps(self):
        self._taps = []
        if self.on_tap:
            self.on_tap(None)

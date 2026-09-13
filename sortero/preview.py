"""Seekable audio preview.

afplay and the default-app route can only play a file from the start, which is
no use for a track with a two-minute intro. pygame-ce's mixer can start at any
position and seek mid-track, and it ships genuinely universal2 builds, so it
survives the universal Mac release.

It can't decode AAC (.m4a). For those - and if the mixer can't start at all, say
on a machine with no audio device - the caller falls back to playing from the
start the old way. The mixer is only initialised the first time something is
actually previewed.
"""
import os, time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

SEEKABLE = {".mp3", ".flac", ".wav", ".ogg", ".aif", ".aiff"}
DEFAULT_VOLUME = 1.0
# get_busy() can read False for an instant while the decoder spins up.
STARTUP_GRACE = 0.5

_pygame = None
_ready = None


def _mixer():
    global _pygame, _ready
    if _ready is None:
        try:
            import pygame
            pygame.mixer.init()
            _pygame, _ready = pygame, True
        except Exception:
            _ready = False
    return _pygame.mixer if _ready else None


def can_seek(path):
    return os.path.splitext(path or "")[1].lower() in SEEKABLE and _mixer() is not None


class Player:
    """One track at a time. Position is tracked here, because the mixer's own
    clock counts from the last play() call and knows nothing about seeks."""

    def __init__(self):
        self.path = None
        self.duration = 0.0
        self.active = False
        self.paused = False
        self._offset = 0.0
        self._t0 = None

    def load(self, path, duration):
        self.stop()
        m = _mixer()
        if m is None:
            raise RuntimeError("audio preview isn't available on this machine")
        m.music.load(path)
        m.music.set_volume(DEFAULT_VOLUME)
        self.path, self.duration = path, float(duration or 0)

    def play(self, at=None):
        if at is not None:
            self._offset = self._clamp(at)
        _mixer().music.play(start=self._offset)
        self._t0 = time.monotonic()
        self.active, self.paused = True, False

    def pause(self):
        if self.active and not self.paused:
            _mixer().music.pause()
            self._offset = self.position()
            self._t0 = None
            self.paused = True

    def resume(self):
        if self.active and self.paused:
            _mixer().music.unpause()
            self._t0 = time.monotonic()
            self.paused = False

    def seek(self, sec):
        sec = self._clamp(sec)
        if not self.active:
            self._offset = sec
            return
        was_paused = self.paused
        self.play(at=sec)
        if was_paused:
            self.pause()
            self._offset = sec

    def position(self):
        if self.active and not self.paused and self._t0 is not None:
            return self._clamp(self._offset + time.monotonic() - self._t0)
        return self._offset

    def finished(self):
        if not self.active or self.paused or self._t0 is None:
            return False
        if time.monotonic() - self._t0 < STARTUP_GRACE:
            return False
        m = _mixer()
        return m is not None and not m.music.get_busy()

    def stop(self):
        m = _mixer() if self.path else None
        if m is not None:
            try:
                m.music.stop()
                m.music.unload()
            except Exception:
                pass
        self.path = None
        self.active = self.paused = False
        self._offset, self._t0 = 0.0, None

    def _clamp(self, sec):
        sec = max(0.0, float(sec or 0))
        return min(sec, self.duration) if self.duration else sec

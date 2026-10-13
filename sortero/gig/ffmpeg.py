"""Finding ffmpeg and ffprobe, and what the copy we found can do.

Release builds carry their own copy in an "ffmpeg" folder next to the app, so
nobody has to install anything; running from source falls back to whatever is
on the PATH.
"""
import functools, os, shutil, subprocess, sys

# A console window flashes up for every call on Windows unless told not to.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Missing(Exception):
    pass


def _bundled(name):
    exe = name + (".exe" if os.name == "nt" else "")
    for base in (getattr(sys, "_MEIPASS", None),
                 os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None):
        if base:
            p = os.path.join(base, "ffmpeg", exe)
            if os.path.isfile(p):
                return p
    return None


@functools.lru_cache(maxsize=None)
def find(name="ffmpeg"):
    """Path to ffmpeg or ffprobe, or None."""
    return _bundled(name) or shutil.which(name)


def require(name="ffmpeg"):
    p = find(name)
    if not p:
        raise Missing(f"Sortero couldn't find {name}, which it needs to read and "
                      "convert audio. Install ffmpeg (brew install ffmpeg on a Mac) "
                      "and open Sortero again.")
    return p


def run(args, **kw):
    """subprocess.run without a console window, capturing output as bytes."""
    kw.setdefault("capture_output", True)
    return subprocess.run(args, creationflags=_NO_WINDOW, **kw)


@functools.lru_cache(maxsize=None)
def _buildconf():
    r = run([require("ffmpeg"), "-hide_banner", "-buildconf"])
    return r.stdout.decode("utf-8", "replace")


def has_soxr():
    """Can this ffmpeg use the SoX resampler? Without it, ffmpeg's own is used."""
    return "--enable-libsoxr" in _buildconf()


def has_lame():
    return "--enable-libmp3lame" in _buildconf()

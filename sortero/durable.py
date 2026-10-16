"""Writing a file so a crash or a pulled cable leaves the old version or the new one, never half.

A rename only replaces the file safely once the new data is on the device, so
the data is flushed before the rename and the folder after it. On macOS a plain
fsync() leaves the data in the drive's cache; F_FULLFSYNC pushes it through, but
exFAT doesn't implement it, and there fsync() is the best that can be done (as
SQLite does).
"""
import json, os, sys


def flush(fh):
    """Push an open file's data to the device."""
    fh.flush()
    if sys.platform == "darwin":
        import fcntl
        try:
            fcntl.fcntl(fh.fileno(), fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass                # not implemented on this file system
    os.fsync(fh.fileno())


def flush_dir(folder):
    """Make a rename inside `folder` stick. Windows can't open a folder, and doesn't need to."""
    if os.name == "nt":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_json(path, data):
    """Replace `path` with `data` as JSON, all or nothing."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
            flush(fh)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    flush_dir(os.path.dirname(os.path.abspath(path)))
    return path

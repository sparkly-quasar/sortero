"""Undo journal. Every mutating operation records what it did, so it can be reversed."""
import json, os, shutil, time, uuid

from . import paths


def _ensure():
    return paths.journals_dir()


class Journal:
    """A single reversible batch of operations."""

    def __init__(self, kind, root):
        self.id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.kind = kind
        self.root = root
        self.entries = []
        self.started = time.time()

    # -- record -------------------------------------------------------------
    def moved(self, src, dst):
        self.entries.append({"op": "move", "src": src, "dst": dst})

    def tagged(self, path, changes):
        """changes: {field: {"old": ..., "new": ...}}"""
        self.entries.append({"op": "tag", "path": path, "changes": changes})

    def created(self, path):
        self.entries.append({"op": "create", "path": path})

    # -- persist ------------------------------------------------------------
    @property
    def path(self):
        return os.path.join(paths.journals_dir(), f"{self.id}-{self.kind}.json")

    def save(self):
        if not self.entries:
            return None
        _ensure()
        with open(self.path, "w") as fh:
            json.dump({"id": self.id, "kind": self.kind, "root": self.root,
                       "started": self.started, "finished": time.time(),
                       "entries": self.entries}, fh, indent=1)
        # If testing mode is running, this operation joins its restore point.
        try:
            from . import session
            session.record(self.path)
            if session.active():
                session.export()
        except Exception:
            pass
        return self.path


def list_journals():
    _ensure()
    out = []
    d = paths.journals_dir()
    for fn in sorted(os.listdir(d), reverse=True):
        if not fn.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(d, fn)))
            rec["_file"] = os.path.join(d, fn)
            out.append(rec)
        except Exception:
            continue
    return out


def revert(journal_file, log=print):
    """Undo a journal, newest entry first."""
    from .tagio import Track
    d = json.load(open(journal_file))
    entries = d["entries"]
    ok = fail = 0
    for e in reversed(entries):
        try:
            if e["op"] == "move":
                src, dst = e["src"], e["dst"]
                if os.path.exists(dst):
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    if os.path.exists(src):
                        log(f"  skip (original exists): {src}")
                        continue
                    shutil.move(dst, src)
                    ok += 1
            elif e["op"] == "tag":
                t = Track(e["path"])
                if t.ok:
                    for field, v in e["changes"].items():
                        t.set(field, v["old"])
                    ok += 1 if t.save() else 0
            elif e["op"] == "create":
                p = e["path"]
                if os.path.isfile(p):
                    os.remove(p); ok += 1
                elif os.path.isdir(p) and not os.listdir(p):
                    os.rmdir(p); ok += 1
        except Exception as ex:
            fail += 1
            log(f"  ! {ex}")
    # prune directories left empty by the revert, but never the protected ones
    root = d.get("root")
    if root and os.path.isdir(root):
        from .library import PROTECTED
        prune_empty(root, keep=PROTECTED)
    log(f"reverted {ok} operations ({fail} failed)")
    return ok, fail


def prune_empty(root, keep=()):
    """Remove empty directories under root, bottom-up.

    `keep` names folders that are never removed - including anything nested
    inside them, so a protected staging area keeps its own subfolders.
    """
    keep = set(keep)
    removed = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        rel = os.path.relpath(dirpath, root)
        if keep.intersection(rel.split(os.sep)):
            continue
        try:
            entries = [e for e in os.listdir(dirpath) if e not in (".DS_Store",)]
            if not entries:
                ds = os.path.join(dirpath, ".DS_Store")
                if os.path.exists(ds):
                    os.remove(ds)
                os.rmdir(dirpath)
                removed.append(dirpath)
        except OSError:
            pass
    return removed

"""Testing mode: a restore point spanning many operations.

Normal use already journals every operation individually, so anything can be
undone one step at a time. Testing mode groups a whole run of work into a single
session, so a first big reorganisation can be rolled back wholesale rather than
step by step.

The session can be exported as a portable `.bak` file. That file is the complete
log — every move and every tag change, with the old values — so it can undo the
work even on a different machine or after reinstalling. It holds no audio: the
operations it reverses never delete anything, they only move files and rewrite
tags, so the log alone is enough to put everything back.
"""
import json, os, time, uuid

from . import paths, settings, journal

ACTIVE_KEY = "active_session"
SESSIONS = "sessions"
BAK_VERSION = 1


def _dir():
    d = os.path.join(paths.data_dir(), SESSIONS)
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ lifecycle
def active():
    """The active session dict, or None."""
    s = settings.get(ACTIVE_KEY)
    return s if isinstance(s, dict) and s.get("id") else None


def start(root, label=None):
    if active():
        raise RuntimeError("A testing session is already running.")
    sess = {
        "id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
        "root": root,
        "label": label or "Testing session",
        "started": time.time(),
        "journals": [],
    }
    settings.set(ACTIVE_KEY, sess)
    return sess


def record(journal_path):
    """Attach a freshly written journal to the active session."""
    sess = active()
    if not sess or not journal_path:
        return
    if journal_path not in sess["journals"]:
        sess["journals"].append(journal_path)
        settings.set(ACTIVE_KEY, sess)


def stop():
    settings.set(ACTIVE_KEY, None)


def summary(sess=None):
    """Counts of what a session has done so far."""
    sess = sess or active()
    if not sess:
        return {"operations": 0, "moves": 0, "tags": 0, "created": 0}
    moves = tags = created = 0
    for jp in sess["journals"]:
        for e in _entries(jp):
            op = e.get("op")
            moves += op == "move"
            tags += op == "tag"
            created += op == "create"
    return {"operations": len(sess["journals"]),
            "moves": moves, "tags": tags, "created": created}


def _entries(journal_path):
    try:
        with open(journal_path) as fh:
            return json.load(fh).get("entries", [])
    except (OSError, json.JSONDecodeError):
        return []


# --------------------------------------------------------------------- backup
def default_bak_path(sess):
    return os.path.join(_dir(), f"sortero-{sess['id']}.bak")


def export(sess=None, path=None):
    """Write the session's full log to a portable .bak file."""
    sess = sess or active()
    if not sess:
        raise RuntimeError("No testing session to export.")
    path = path or default_bak_path(sess)
    blocks = []
    for jp in sess["journals"]:
        try:
            with open(jp) as fh:
                blocks.append(json.load(fh))
        except (OSError, json.JSONDecodeError):
            continue
    payload = {
        "format": "sortero-backup",
        "version": BAK_VERSION,
        "session": {k: sess[k] for k in ("id", "root", "label", "started")},
        "exported": time.time(),
        "journals": blocks,
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)
    return path


def load(path):
    with open(path) as fh:
        data = json.load(fh)
    if data.get("format") != "sortero-backup":
        raise ValueError("That isn't a Sortero backup file.")
    if data.get("version", 0) > BAK_VERSION:
        raise ValueError("That backup was written by a newer version of Sortero.")
    return data


def describe(data):
    moves = tags = created = 0
    for b in data.get("journals", []):
        for e in b.get("entries", []):
            op = e.get("op")
            moves += op == "move"
            tags += op == "tag"
            created += op == "create"
    sess = data.get("session", {})
    return {"label": sess.get("label", "session"), "root": sess.get("root", ""),
            "started": sess.get("started", 0),
            "operations": len(data.get("journals", [])),
            "moves": moves, "tags": tags, "created": created}


def revert_backup(data, log=print):
    """Undo everything in a loaded backup, newest operation first."""
    blocks = list(data.get("journals", []))
    ok = fail = 0
    tmp_dir = os.path.join(_dir(), "_restore")
    os.makedirs(tmp_dir, exist_ok=True)
    for b in reversed(blocks):
        tmp = os.path.join(tmp_dir, f"{b.get('id', uuid.uuid4().hex)}.json")
        with open(tmp, "w") as fh:
            json.dump(b, fh)
        try:
            o, f = journal.revert(tmp, log=log)
            ok += o
            fail += f
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    log(f"restore complete: {ok} operations reversed, {fail} failed")
    return ok, fail


def revert_active(log=print):
    sess = active()
    if not sess:
        raise RuntimeError("No testing session running.")
    ok, fail = revert_backup({"journals": [
        _block(jp) for jp in sess["journals"] if _block(jp)]}, log=log)
    discard(sess)
    return ok, fail


def _block(jp):
    try:
        with open(jp) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def commit(sess=None, log=print):
    """Keep the changes: delete the session's backup and close it.

    Individual operations stay in History and remain undoable one at a time -
    only the combined restore point goes away.
    """
    sess = sess or active()
    if not sess:
        raise RuntimeError("No testing session running.")
    bak = default_bak_path(sess)
    if os.path.exists(bak):
        os.remove(bak)
        log(f"deleted backup {os.path.basename(bak)}")
    stop()
    log(f"'{sess['label']}' made permanent")
    return True


def discard(sess=None):
    """Close the session and remove its journals and backup (post-revert)."""
    sess = sess or active()
    if not sess:
        return
    for jp in sess["journals"]:
        try:
            os.remove(jp)
        except OSError:
            pass
    bak = default_bak_path(sess)
    try:
        os.remove(bak)
    except OSError:
        pass
    stop()

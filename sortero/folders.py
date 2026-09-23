"""The folders a track can actually live in - this collection's, not a template.

Sortero's own layout is <Genre>/ at the top of the collection, and people
organise the same way by hand: House/, Techno/Hypnotic Techno/, with release
folders underneath. Anything that files a track should offer, and prefer, the
folders that already exist - including the Tracks/<Genre> of a collection
Sortero filed before it stopped nesting them.
"""
import os, re

from .common import AUDIO_EXTS
from .library import PROTECTED, RECORDING_DIRS
from .organize import RESERVED_TOP, TRACKS_DIR, UNSORTED, canon_genre, genre_dir, safe

# Top-level folders that hold something other than genres.
NOT_A_HOME = (set(RESERVED_TOP) | set(PROTECTED) | set(RECORDING_DIRS)
              | {"Sets", "Albums", "Compilations"}) - {TRACKS_DIR}

# Folder names that read like a single release rather than a genre.
RELEASE_HINT = re.compile(
    r"(?i)(\bEP\b|\bLP\b|\bvol\.?\s*\d|\bVA\b|\bdemos?\b|\bsessions\b|\bremixes\b|"
    r"\balbum\b|\[[^\]]+\]|\b(19|20)\d{2}\b|\s[-–]\s)")

# Words that only ever describe a genre. A folder named entirely from these
# ("Hypnotic Techno", "Dub Techno") is a genre folder; one with anything else in
# it ("Lez Dance", "smooth vibes") is somebody's curation, and must not swallow
# every track that happens to share a genre word with it.
GENRE_WORDS = set(
    "techno house tech deep hypnotic dub minimal peak time hard melodic afro "
    "organic downtempo ambient drone progressive prog disco nu indie dance funky "
    "soulful jackin bass breaks breakbeat trance psy electronica electronic idm "
    "chill lofi lo fi hip hop pop jazz soul funk and garage dubstep raw industrial "
    "driving lounge electro future acid vocal club".split())


# The optional energy split files <Genre>/Energy 6/... - those folders are part
# of the layout: never a release to dissolve, and never a genre name.
ENERGY_DIR = re.compile(r"^Energy \d{1,2}$")


def looks_like_release(name):
    return bool(RELEASE_HINT.search(name or ""))


def is_energy_dir(name):
    return bool(ENERGY_DIR.match(name or ""))


def genre_of_folder(rel):
    """The genre a folder stands for: its own name, skipping energy subfolders."""
    for part in reversed(os.path.normpath(rel or "").split(os.sep)):
        if part and part != "." and not is_energy_dir(part):
            return part
    return None


def is_genre_name(name):
    toks = re.findall(r"[a-z]+", (name or "").lower())
    return bool(toks) and all(t in GENRE_WORDS for t in toks)


def audio_count(folder):
    try:
        return sum(1 for f in os.listdir(folder)
                   if os.path.splitext(f)[1].lower() in AUDIO_EXTS)
    except OSError:
        return 0


def _dirs(path):
    try:
        return sorted(d for d in os.listdir(path)
                      if not d.startswith((".", "_"))
                      and os.path.isdir(os.path.join(path, d)))
    except OSError:
        return []


def _has_audio(folder):
    """Any audio anywhere beneath - stops at the first hit."""
    for _, _, filenames in os.walk(folder):
        if any(os.path.splitext(f)[1].lower() in AUDIO_EXTS for f in filenames):
            return True
    return False


def homes(root):
    """[(relative folder, tracks directly inside)] at genre and subgenre level.

    Release folders below that are not offered as places to file a track, and
    nor is anything with no music in it at all (a folder of controller scripts).
    """
    out = []
    if not root or not os.path.isdir(root):
        return out
    for top in _dirs(root):
        if top in NOT_A_HOME:
            continue
        tp = os.path.join(root, top)
        if top == TRACKS_DIR:
            for g in _dirs(tp):
                if g != UNSORTED:
                    out.append((os.path.join(top, g), audio_count(os.path.join(tp, g))))
            continue
        if not _has_audio(tp):
            continue
        out.append((top, audio_count(tp)))
        for sub in _dirs(tp):
            sp = os.path.join(tp, sub)
            if not looks_like_release(sub) and _has_audio(sp):
                out.append((os.path.join(top, sub), audio_count(sp)))
    return out


def uses_tracks_layout(root, choices=None):
    """Is this collection still filed the old way, under Tracks/?

    Only when that folder is really there. Genres go at the top of the
    collection now, so a fresh one is filed that way and so is a library
    already organised as House/, Techno/ ... A collection Sortero nested
    earlier is left as it is until a Reorganise moves it, rather than being
    split across both layouts in the meantime.
    """
    return os.path.isdir(os.path.join(root, TRACKS_DIR))


def new_home(root, genre, choices=None):
    """Where a genre folder that doesn't exist yet should be created."""
    if uses_tracks_layout(root, choices):
        return os.path.join(root, TRACKS_DIR, safe(genre, 60))
    # at the top level the name has to keep clear of the reserved folders
    return os.path.join(root, genre_dir(genre))


def existing_home(root, genre, choices=None):
    """Absolute path of an existing folder that already means this genre, or None."""
    if not genre or genre == UNSORTED:
        return None
    choices = homes(root) if choices is None else choices
    want = genre.strip().lower()
    # shallowest first, then biggest: 'Techno' before 'Techno/Hypnotic Techno'
    ordered = sorted(choices, key=lambda c: (c[0].count(os.sep), -c[1]))
    for rel, _ in ordered:
        if os.path.basename(rel).lower() == want:
            return os.path.join(root, rel)
    for detail in ("fine", "broad"):
        target = canon_genre(genre, detail=detail)
        if not target:
            continue
        for rel, _ in ordered:
            leaf = os.path.basename(rel)
            if is_genre_name(leaf) and canon_genre(leaf, strict=True, detail=detail) == target:
                return os.path.join(root, rel)
    return None

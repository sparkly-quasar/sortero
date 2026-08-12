"""Shared helpers for the DJ library toolkit."""
import os, re, json

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".ogg"}


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

# --- Camelot wheel -----------------------------------------------------------
CAMELOT = {
    "abm": "1A", "g#m": "1A", "b": "1B",
    "ebm": "2A", "d#m": "2A", "f#": "2B", "gb": "2B",
    "bbm": "3A", "a#m": "3A", "db": "3B", "c#": "3B",
    "fm": "4A", "ab": "4B", "g#": "4B",
    "cm": "5A", "eb": "5B", "d#": "5B",
    "gm": "6A", "bb": "6B", "a#": "6B",
    "dm": "7A", "f": "7B",
    "am": "8A", "c": "8B",
    "em": "9A", "g": "9B",
    "bm": "10A", "d": "10B",
    "f#m": "11A", "gbm": "11A", "a": "11B",
    "dbm": "12A", "c#m": "12A", "e": "12B",
}


def to_camelot(musical):
    return CAMELOT.get((musical or "").strip().lower().replace("min", "m"))


# --- junk / spam patterns ----------------------------------------------------
SPAM_PATTERNS = [
    r"(?i)^\s*(https?://)?(www\.)?[\w-]+\.(com|net|org|ru|me|cc|to)\s*/?\s*$",
    r"(?i)djsoundtop|electronicfresh|zippyshare|datpiff|soundcloud\.com",
    r"(?i)«?\s*fax\s*\+?[\d\s\-]+»?",
    r"^\s*\*+\s*$",
    r"(?i)^\s*(encoded by|created by|ripped by|lame|flac frontend).*$",
    r"(?i)^\s*visit\s+https?://",
    r"(?i)^\s*(unknown|none|n/a|other|misc)\s*$",
]
SPAM_RE = [re.compile(p) for p in SPAM_PATTERNS]


def is_spam(value):
    v = (value or "").strip()
    if not v:
        return False
    return any(p.search(v) for p in SPAM_RE)


# --- Mixed In Key comment parsing -------------------------------------------
# Matches "Cm - Energy 6", "A#m - Energy 5", "8A - Energy 7"
MIK_RE = re.compile(
    r"^\s*(?P<key>(?:[A-G][#b]?m?)|(?:1[0-2]|[1-9])[AB])\s*[-–]\s*Energy\s*(?P<energy>\d+)\s*$",
    re.I)
MIK_KEY_ONLY = re.compile(r"^\s*(?P<key>(?:[A-G][#b]?m?)|(?:1[0-2]|[1-9])[AB])\s*$")


def parse_mik(comment):
    """Return (musical_or_camelot_key, energy_int) from a MIK-style comment."""
    if not comment:
        return None, None
    m = MIK_RE.match(comment.strip())
    if m:
        return m.group("key"), int(m.group("energy"))
    m = MIK_KEY_ONLY.match(comment.strip())
    if m:
        return m.group("key"), None
    return None, None


# --- filename parsing --------------------------------------------------------
def clean_stem(name):
    s = os.path.splitext(os.path.basename(name))[0]
    s = re.sub(r"_PN$", "", s)                 # Platinum Notes suffix
    s = re.sub(r"^\d{1,3}[\s._)-]+", "", s)    # leading track number
    s = re.sub(r"(?i)\s*[\[(]?(official|lyrics?)\s*(music\s*)?(video|audio)[\])]?", "", s)
    s = re.sub(r"(?i)\s*[\[(]?\s*(hq|hd|free\s*download|out\s*now)\s*[\])]?", "", s)
    s = re.sub(r"\s*[-–]\s*\d{4}\s*$", "", s)  # trailing year
    s = re.sub(r"[_]+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip(" -–_")


def split_artist_title(stem):
    """Best-effort 'Artist - Title' split. Returns (artist, title) or (None, stem)."""
    for sep in (" - ", " – ", " -", "- "):
        if sep in stem:
            a, _, t = stem.partition(sep)
            a, t = a.strip(" -–"), t.strip(" -–")
            if a and t and len(a) < 80:
                return a, t
    return None, stem


def walk_audio(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("._"):
                continue
            if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def load_journal(path):
    return json.load(open(path)) if os.path.exists(path) else []


def append_journal(path, entries):
    data = load_journal(path)
    data.extend(entries)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1)

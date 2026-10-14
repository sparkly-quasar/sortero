"""A rekordbox XML of the gig: the playlists, in set order, pointing at gig-safe files.

rekordbox reads it as its "rekordbox xml" library; from there the playlists
are dragged into rekordbox and exported to the stick (HOW_TO). The layout
follows rekordbox's own xml_format_list.pdf
(https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf):

  DJ_PLAYLISTS
    PRODUCT
    COLLECTION Entries=n
      TRACK TrackID Name Artist ... Location AverageBpm Tonality
        TEMPO Inizio Bpm Metro Battito
        POSITION_MARK Name Type Start End Num
    PLAYLISTS
      NODE Type=0 Name=ROOT
        NODE Type=0 Name=<the gig>
          NODE Type=1 Name="01 Warmup" KeyType=0 Entries=n
            TRACK Key=<TrackID>

Locations are file://localhost URIs. Numbers use a dot, whatever the locale.

A copy converted to MP3 starts 1105 samples later for a decoder that ignores
the LAME header's gapless information (measured: encoder delay 576 plus
decoder delay 529, 25.06 ms at 44.1 kHz) and exactly on time for one that
reads it. Which kind rekordbox is, isn't documented, so the shift is a
setting (gig_mp3_cue_shift), off until a check in rekordbox says otherwise.
"""
import os, time, urllib.parse
import xml.etree.ElementTree as ET

from . import convert, cues
from ..tagio import Track
from ..version import __version__

FILE_NAME = "Sortero gig.xml"
MP3_COPY_DELAY = 1105 / 44100


def location(path):
    """A local path as rekordbox writes it: file://localhost/... with %-escapes."""
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):                 # C:/Music/... on Windows
        p = "/" + p
    return "file://localhost" + urllib.parse.quote(p, safe="/:")


def _num(x, places=2):
    return f"{x:.{places}f}"


def _bpm(value):
    try:
        bpm = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return bpm if 20 <= bpm <= 400 else None


def _track_attrs(track_id, path, info):
    t = Track(path)
    get = (lambda f: (t.get(f) or "").strip()) if t.ok else (lambda f: "")
    attrs = {"TrackID": str(track_id), "Name": get("title") or os.path.splitext(
                 os.path.basename(path))[0],
             "Artist": get("artist"), "Album": get("album"), "Genre": get("genre"),
             "Grouping": get("grouping"), "Comments": get("comment"),
             "Tonality": get("key"), "Location": location(path)}
    try:
        attrs["Size"] = str(os.path.getsize(path))
    except OSError:
        pass
    if info is not None:
        duration = info.extras.get("duration") or 0
        if duration:
            attrs["TotalTime"] = str(int(duration))
        if info.rate:
            attrs["SampleRate"] = str(info.rate)
        if info.kbps:
            attrs["BitRate"] = str(info.kbps)
    bpm = _bpm(get("bpm"))
    if bpm:
        attrs["AverageBpm"] = _num(bpm)
    return {k: v for k, v in attrs.items() if v != ""}


def build(items, sets, gig_name, finder=None, shift_mp3_copies=False):
    """The XML tree, and how many tracks got marks from which source.

    `sets` is [(playlist name, [original paths in order])]; `items` the
    checked tracks, which say what file plays for each original.
    """
    by_src = {cues.key(it.src): it for it in items if it.playable}
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="Sortero", Version=__version__, Company="Sortero")
    collection = ET.SubElement(root, "COLLECTION")
    ids, counts = {}, {}

    def track_id(it):
        k = cues.key(it.playable)
        if k in ids:
            return ids[k]
        tid = len(ids) + 1
        ids[k] = tid
        el = ET.SubElement(collection, "TRACK", _track_attrs(tid, it.playable, it.info))
        marks = finder.find(it.src) if finder else None
        if marks:
            is_mp3_copy = (it.status in (convert.CONVERTED, convert.REUSED)
                           and it.target == convert.MP3)
            if is_mp3_copy and shift_mp3_copies:
                marks = marks.shifted(MP3_COPY_DELAY)
            counts[marks.source] = counts.get(marks.source, 0) + 1
            if marks.grids and "AverageBpm" not in el.attrib:
                el.set("AverageBpm", _num(marks.grids[0].bpm))
            for g in marks.grids:
                ET.SubElement(el, "TEMPO", Inizio=_num(g.start, 3), Bpm=_num(g.bpm),
                              Metro=g.meter, Battito=str(g.beat))
            for m in marks.marks:
                attrs = {"Name": m.name, "Type": str(m.type), "Start": _num(m.start, 3),
                         "Num": str(m.num)}
                if m.end is not None:
                    attrs["End"] = _num(m.end, 3)
                ET.SubElement(el, "POSITION_MARK", attrs)
        return tid

    playlists = ET.SubElement(root, "PLAYLISTS")
    top = ET.SubElement(playlists, "NODE", Type="0", Name="ROOT", Count="1")
    folder = ET.SubElement(top, "NODE", Type="0", Name=gig_name, Count=str(len(sets)))
    width = max(2, len(str(len(sets))))
    for i, (name, originals) in enumerate(sets, 1):
        keys = [track_id(by_src[cues.key(src)]) for src in originals
                if cues.key(src) in by_src]
        node = ET.SubElement(folder, "NODE", Type="1", Name=f"{i:0{width}d} {name}",
                             KeyType="0", Entries=str(len(keys)))
        for tid in keys:
            ET.SubElement(node, "TRACK", Key=str(tid))
    collection.set("Entries", str(len(ids)))
    return ET.ElementTree(root), counts


def write(path, tree):
    """Write it, replacing the last one only once the new one is complete."""
    ET.indent(tree, space="  ")
    tmp = path + ".tmp"
    tree.write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, path)
    return path


def gig_name():
    return time.strftime("Sortero gig %Y-%m-%d")


def how_to(xml_path):
    """Steps for rekordbox, from its 7.2.18 manual (pp. 19, 46, 108 and 246:
    https://cdn.rekordbox.com/files/20260807093645/rekordbox7.2.18_manual_EN.pdf)
    and AlphaTheta's USB notice (profiles.LIBRARY_NOTICE). Short enough to
    follow with rekordbox open beside Sortero."""
    return [
        "In rekordbox (once):",
        "  1. Preferences > View > Layout: tick “rekordbox xml” under Media Browser.",
        "  2. Preferences > Advanced > Database > rekordbox xml > Imported Library:",
        f"     choose {xml_path}",
        "",
        "Each gig:",
        "  3. In the tree view, double-click “rekordbox xml”, then “Playlists”.",
        "     (Click the refresh icon if it shows an older gig.)",
        "  4. Drag this gig's playlists onto “Playlists”. rekordbox adds the tracks",
        "     and analyses any it hasn't seen.",
        "  5. Drag those playlists onto the stick under “Devices”. Use rekordbox",
        "     7.2.11 or later: it writes both the Device Library older players read",
        "     and the OneLibrary the newest ones (CDJ-3000X, OPUS-QUAD) read.",
        "  6. Come back here and check the stick.",
    ]

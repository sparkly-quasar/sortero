"""Carry every tag from an original onto its gig-safe copy.

The copy is always MP3 or AIFF, so it always gets ID3 (in a chunk, for AIFF).
An ID3 original hands its frames over as they are. FLAC, Ogg and M4A tags are
translated, field by field, and anything without a standard ID3 home goes
into a TXXX frame under its own name rather than being dropped.

Mixed In Key keeps its analysis - cue points, beat grid, key, energy - as
base64 JSON. In an MP3 that is a GEOB frame per kind (CuePoints, BeatGrid,
Key, Energy). Those blobs are recognised by what they contain, not what the
field is called, so they land in the same GEOB frames whatever format the
original was.

Artwork: Pioneer players show embedded JPEG up to 800 x 800 (see profiles).
Anything larger, or not JPEG, is converted, so the older players show it too.
"""
import base64, binascii, json, os, struct, tempfile

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (ID3, APIC, COMM, GEOB, TALB, TBPM, TCOM, TCON, TCOP, TDRC, TIT1,
                         TIT2, TIT3, TKEY, TPE1, TPE2, TPE4, TPOS, TPUB, TRCK, TSRC, TXXX,
                         UFID, ID3NoHeaderError, PictureType)
from mutagen.mp4 import MP4, MP4Cover

from . import ffmpeg

# Frames that describe the old encode, not the music: wrong on the new file.
DROP = {"TSSE", "TENC", "TFLT", "MLLT", "TLEN"}

VORBIS = {"TITLE": TIT2, "ARTIST": TPE1, "ALBUM": TALB, "GENRE": TCON, "DATE": TDRC,
          "YEAR": TDRC, "TRACKNUMBER": TRCK, "DISCNUMBER": TPOS, "ALBUMARTIST": TPE2,
          "BPM": TBPM, "INITIALKEY": TKEY, "GROUPING": TIT1, "COMPOSER": TCOM,
          "LABEL": TPUB, "ORGANIZATION": TPUB, "PUBLISHER": TPUB, "ISRC": TSRC,
          "COPYRIGHT": TCOP, "SUBTITLE": TIT3, "REMIXER": TPE4}
VORBIS_COMMENT = {"COMMENT", "DESCRIPTION"}
VORBIS_SKIP = {"METADATA_BLOCK_PICTURE", "COVERART", "COVERARTMIME", "ENCODER",
               "ENCODED-BY", "ENCODEDBY"}

# MusicBrainz and AcoustID tags go into an MP3 under the names MusicBrainz
# Picard gives them, from its tag mapping table (picard-docs,
# appendices/tag_mapping.rst; also at
# https://picard-docs.musicbrainz.org/downloads/MusicBrainz_Picard_Tag_Map.html),
# so Picard, Navidrome and anything else that reads them finds them. Vorbis
# MUSICBRAINZ_TRACKID is the *recording* id, which ID3 keeps in a UFID frame.
# Tags whose Picard name is the Vorbis key itself (BARCODE, CATALOGNUMBER,
# REPLAYGAIN_*, ...) already come out right as TXXX:<key>.
MB_UFID_OWNER = "http://musicbrainz.org"
VORBIS_UFID = "MUSICBRAINZ_TRACKID"
MP4_UFID = "MusicBrainz Track Id"
PICARD_TXXX = {
    "ACOUSTID_ID": "Acoustid Id",
    "ACOUSTID_FINGERPRINT": "Acoustid Fingerprint",
    "MUSICBRAINZ_ARTISTID": "MusicBrainz Artist Id",
    "MUSICBRAINZ_ALBUMARTISTID": "MusicBrainz Album Artist Id",
    "MUSICBRAINZ_ALBUMID": "MusicBrainz Album Id",
    "MUSICBRAINZ_RELEASEGROUPID": "MusicBrainz Release Group Id",
    "MUSICBRAINZ_RELEASETRACKID": "MusicBrainz Release Track Id",
    "MUSICBRAINZ_WORKID": "MusicBrainz Work Id",
    "MUSICBRAINZ_DISCID": "MusicBrainz Disc Id",
    "MUSICBRAINZ_ORIGINALARTISTID": "MusicBrainz Original Artist Id",
    "MUSICBRAINZ_ORIGINALALBUMID": "MusicBrainz Original Album Id",
    "MUSICBRAINZ_TRMID": "MusicBrainz TRM Id",
    "MUSICIP_PUID": "MusicIP PUID",
    "RELEASECOUNTRY": "MusicBrainz Album Release Country",
    "RELEASESTATUS": "MusicBrainz Album Status",
    "RELEASETYPE": "MusicBrainz Album Type",
    "WRITER": "Writer",
}


def _recording_id(value):
    return UFID(owner=MB_UFID_OWNER, data=value.strip().encode("ascii", "ignore"))


MP4_TEXT = {"\xa9nam": TIT2, "\xa9ART": TPE1, "\xa9alb": TALB, "\xa9gen": TCON,
            "\xa9day": TDRC, "aART": TPE2, "\xa9grp": TIT1, "\xa9wrt": TCOM, "cprt": TCOP}
MP4_FREEFORM = {"initialkey": TKEY, "label": TPUB, "isrc": TSRC}
MP4_SKIP = {"\xa9too", "covr", "\xa9cmt", "trkn", "disk", "tmpo"}

# What a Mixed In Key blob holds -> the GEOB description it goes under.
MIK_KINDS = (("cues", "CuePoints"), ("beats", "BeatGrid"),
             ("energyLevel", "Energy"), ("key", "Key"))


def _text(cls, value):
    return cls(encoding=3, text=[str(value)])


def mik_frame(value):
    """A GEOB frame for a Mixed In Key blob, or None if value isn't one."""
    raw = value.encode("ascii", "ignore") if isinstance(value, str) else bytes(value)
    try:
        # MIK wraps its base64 at 72 characters, newline-terminated. Look past
        # that to recognise it, but keep the bytes exactly as MIK wrote them.
        data = json.loads(base64.b64decode(b"".join(raw.split()), validate=True))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(data, dict) or data.get("source") != "mixedinkey":
        return None
    for key, desc in MIK_KINDS:
        if key in data:
            return GEOB(encoding=3, mime="application/json", filename="", desc=desc,
                        data=raw)
    return None


# ------------------------------------------------------------ reading tags
def _id3_of(path):
    f = None
    try:
        f = mutagen.File(path)
    except Exception:
        pass
    if f is not None and isinstance(getattr(f, "tags", None), ID3):
        return f.tags
    try:
        return ID3(path)        # mp3 mutagen couldn't place, or a stray front tag
    except (ID3NoHeaderError, Exception):
        return None


def _from_vorbis(tags, pictures):
    out, art = [], None
    for key in sorted({k.upper() for k in tags.keys()}):
        values = [v for v in tags.get(key, []) if v.strip()]
        if not values or key in VORBIS_SKIP:
            continue
        geob = mik_frame(values[0])
        if geob:
            out.append(geob)
        elif key in VORBIS:
            out.append(VORBIS[key](encoding=3, text=values))
        elif key == VORBIS_UFID:
            out.append(_recording_id(values[0]))
        elif key in PICARD_TXXX:
            out.append(TXXX(encoding=3, desc=PICARD_TXXX[key], text=values))
        elif key in VORBIS_COMMENT:
            out.append(COMM(encoding=3, lang="eng", desc="", text=values))
        elif key in ("ENERGYLEVEL", "ENERGY") and values[0].strip().isdigit():
            out.append(TXXX(encoding=3, desc="EnergyLevel", text=values))
        else:
            out.append(TXXX(encoding=3, desc=key, text=values))
    for p in pictures:
        if art is None or p.type == PictureType.COVER_FRONT:
            art = (p.mime, p.data)
    return out, art


def _ogg_pictures(tags):
    out = []
    for b64 in tags.get("METADATA_BLOCK_PICTURE", []):
        try:
            out.append(Picture(base64.b64decode(b64)))
        except Exception:
            pass
    return out


def _from_mp4(tags):
    out, art = [], None
    for key, values in tags.items():
        if key in MP4_SKIP or not values:
            continue
        if key in MP4_TEXT:
            out.append(MP4_TEXT[key](encoding=3, text=[str(v) for v in values]))
        elif key.startswith("----:"):
            name = key.split(":", 2)[-1]
            text = values[0].decode("utf-8", "replace")
            geob = mik_frame(text)
            if geob:
                out.append(geob)
            elif name == MP4_UFID:
                out.append(_recording_id(text))
            elif name.lower() in MP4_FREEFORM:
                out.append(_text(MP4_FREEFORM[name.lower()], text))
            elif name.lower() in ("energylevel", "energy") and text.strip().isdigit():
                out.append(TXXX(encoding=3, desc="EnergyLevel", text=[text]))
            else:
                out.append(TXXX(encoding=3, desc=name, text=[text]))
    if tags.get("\xa9cmt"):
        out.append(COMM(encoding=3, lang="eng", desc="", text=[str(tags["\xa9cmt"][0])]))
    if tags.get("tmpo"):
        out.append(_text(TBPM, tags["tmpo"][0]))
    if tags.get("trkn"):
        n, total = tags["trkn"][0]
        out.append(_text(TRCK, f"{n}/{total}" if total else n))
    if tags.get("disk"):
        n, total = tags["disk"][0]
        out.append(_text(TPOS, f"{n}/{total}" if total else n))
    if tags.get("covr"):
        c = tags["covr"][0]
        art = ("image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg", bytes(c))
    return out, art


def read(path):
    """(frames, artwork) for any format: ID3 frames to write, and (mime, bytes) or None."""
    try:
        f = mutagen.File(path)
    except Exception:
        f = None
    if isinstance(f, FLAC):
        return _from_vorbis(f.tags or {}, f.pictures)
    if f is not None and f.__class__.__name__ in ("OggVorbis", "OggOpus", "OggFLAC"):
        return _from_vorbis(f.tags or {}, _ogg_pictures(f.tags or {}))
    if isinstance(f, MP4):
        return _from_mp4(f.tags or {})
    tags = _id3_of(path)
    if tags is None:
        return [], None
    frames, art = [], None
    for fr in tags.values():
        if fr.FrameID in DROP:
            continue
        if fr.FrameID == "APIC":
            if art is None or fr.type == PictureType.COVER_FRONT:
                art = (fr.mime, fr.data)
            continue
        frames.append(fr)
    return frames, art


# ----------------------------------------------------------------- artwork
def image_size(data):
    """(width, height, kind) of a JPEG or PNG, read from its header. None if unknown."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return w, h, "png"
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h, "jpeg"
            i += 2 + seg
    return None


def fit_artwork(mime, data, max_px):
    """(mime, data, note): JPEG no larger than max_px, and what was done, if anything."""
    size = image_size(data)
    if size and size[2] == "jpeg" and max(size[:2]) <= max_px:
        return "image/jpeg", data, None
    with tempfile.TemporaryDirectory(prefix="sortero-art-") as d:
        src, dst = os.path.join(d, "in.img"), os.path.join(d, "out.jpg")
        with open(src, "wb") as fh:
            fh.write(data)
        scale = (f"scale='min({max_px},iw)':'min({max_px},ih)'"
                 ":force_original_aspect_ratio=decrease")
        r = ffmpeg.run([ffmpeg.require("ffmpeg"), "-v", "error", "-y", "-i", src,
                        "-vf", scale, "-pix_fmt", "yuvj420p", "-q:v", "3", "-frames:v", "1",
                        "-c:v", "mjpeg", "-f", "image2", dst])
        if r.returncode != 0 or not os.path.exists(dst):
            return None, None, "Artwork couldn't be read, so the copy has none."
        with open(dst, "rb") as fh:
            out = fh.read()
    new = image_size(out)
    if size and new and size[:2] != new[:2]:
        note = (f"Artwork shrunk from {size[0]}×{size[1]} to {new[0]}×{new[1]} so "
                "every player shows it.")
    else:
        note = "Artwork converted to JPEG so every player shows it."
    return "image/jpeg", out, note


# ----------------------------------------------------------------- writing
def write(dst, frames, art=None):
    """Put frames (and artwork) on an MP3 or AIFF that has no tags yet."""
    if dst.lower().endswith((".aif", ".aiff")):
        f = AIFF(dst)
        if f.tags is None:
            f.add_tags()
        tags = f.tags
    else:
        f, tags = None, ID3()
    for fr in frames:
        tags.add(fr)
    if art:
        tags.add(APIC(encoding=3, mime=art[0], type=PictureType.COVER_FRONT, desc="",
                      data=art[1]))
    # Saving as v2.3 doesn't translate v2.4-only frames by itself (TDRC and
    # friends); without this they'd be written in a form v2.3 readers skip.
    tags.update_to_v23()
    if f is not None:
        f.save(v2_version=3)
    else:
        tags.save(dst, v2_version=3)


def copy(src, dst, max_art_px=800):
    """Copy every tag from src onto dst. Returns notes worth telling the user.

    max_art_px=None keeps the artwork exactly as it is (a library file, not a
    copy for older players)."""
    frames, art = read(src)
    notes = []
    if art and max_art_px is not None:
        mime, data, note = fit_artwork(art[0], art[1], max_art_px)
        art = (mime, data) if data else None
        if note:
            notes.append(note)
    write(dst, frames, art)
    return notes

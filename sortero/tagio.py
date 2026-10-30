"""Format-agnostic read/write for the tag fields DJ software actually reads."""
import base64, binascii, json, os

import mutagen
from mutagen.id3 import ID3, TKEY, TIT1, TCON, TPE1, TIT2, TBPM, TALB, COMM, ID3NoHeaderError
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

from . import headers

# logical field -> per-format tag name
ID3_FRAMES = {"key": TKEY, "grouping": TIT1, "genre": TCON,
              "artist": TPE1, "title": TIT2, "bpm": TBPM, "album": TALB}
# Mixed In Key writes its 1-10 rating to ENERGYLEVEL. Note it also writes a
# base64 JSON blob to plain KEY/ENERGY - read INITIALKEY, never KEY.
VORBIS_KEYS = {"key": "INITIALKEY", "grouping": "GROUPING", "genre": "GENRE",
               "artist": "ARTIST", "title": "TITLE", "bpm": "BPM",
               "comment": "COMMENT", "energylevel": "ENERGYLEVEL", "album": "ALBUM"}
MP4_KEYS = {"grouping": "\xa9grp", "genre": "\xa9gen", "artist": "\xa9ART",
            "title": "\xa9nam", "comment": "\xa9cmt", "album": "\xa9alb"}
MP4_KEY_ATOM = "----:com.apple.iTunes:initialkey"
MP4_ENERGY_ATOM = "----:com.apple.iTunes:EnergyLevel"
ID3_ENERGY_DESCS = ("energylevel", "energy")
# Mixed In Key's beat grid: base64 JSON holding its measured "tempo". It is
# there even when Mixed In Key leaves the BPM tag itself unwritten.
MIK_GRID_VORBIS = "BEATGRID"
MIK_GRID_MP4 = "----:com.mixedinkey.mixedinkey:beatgrid"


class Track:
    """Thin wrapper exposing key/grouping/genre/artist/title/bpm/comment."""

    def __init__(self, path):
        self.path = path
        self.kind = None
        self.audio = None
        self._iff = None        # an AIFF or WAV file, which holds its ID3 tag in a chunk
        # bytes of ID3 tag an older Sortero glued in front of an AIFF/WAV header
        self.stray_id3 = headers.stray_id3(path)
        try:
            a = mutagen.File(path)
        except Exception:
            a = None
        self._mf = a            # kept so length/bitrate don't reopen the file
        if isinstance(a, (FLAC, OggVorbis, OggOpus)):
            self.kind, self.audio = "vorbis", a
        elif isinstance(a, MP4):
            self.kind, self.audio = "mp4", a
        elif isinstance(a, (AIFF, WAVE)):
            # The tag lives in a chunk inside the file, where rekordbox and Mixed
            # In Key write it. Saving a bare ID3 to the path instead glues it in
            # front of the header (see headers.py), so go through the file.
            if a.tags is None:
                a.add_tags()
            self.kind, self.audio, self._iff = "id3", a.tags, a
        elif (isinstance(a, MP3) or self.stray_id3
              or os.path.splitext(path)[1].lower() == ".mp3"):
            # mp3, whose ID3 tag really does go in front. An AIFF/WAV already
            # damaged this way is read and written through its stray tag until
            # headers.repair moves that into the chunk: rewriting it in place
            # makes nothing worse. Anything else gets no ID3 tag at all, since
            # writing one would damage it in the same way.
            try:
                self.audio = ID3(path)
            except ID3NoHeaderError:
                self.audio = ID3()
            except Exception:
                self.audio = None
            self.kind = "id3"

    @property
    def ok(self):
        return self.audio is not None

    @property
    def bitrate(self):
        """Nominal bitrate in bits/sec, or 0 when unavailable."""
        try:
            return int(getattr(self._mf.info, "bitrate", 0) or 0)
        except Exception:
            return 0

    @property
    def length(self):
        """Duration in seconds, or 0.0 when unavailable."""
        try:
            return float(self._mf.info.length)
        except Exception:
            return 0.0

    # -- read ---------------------------------------------------------------
    def get(self, field):
        if not self.ok:
            return None
        try:
            if self.kind == "id3":
                if field == "comment":
                    for f in self.audio.getall("COMM"):
                        if f.text and f.text[0].strip():
                            return f.text[0]
                    return None
                if field == "energylevel":
                    for f in self.audio.getall("TXXX"):
                        if str(getattr(f, "desc", "")).lower() in ID3_ENERGY_DESCS:
                            v = str(f.text[0]) if f.text else ""
                            if v.strip().isdigit():
                                return v.strip()
                    return None
                frame = ID3_FRAMES.get(field)
                if not frame:
                    return None
                vals = self.audio.getall(frame.__name__)
                return str(vals[0].text[0]) if vals and vals[0].text else None
            if self.kind == "vorbis":
                v = self.audio.get(VORBIS_KEYS.get(field, field.upper()))
                return v[0] if v else None
            if self.kind == "mp4":
                if field == "key":
                    v = self.audio.get(MP4_KEY_ATOM)
                    return v[0].decode("utf-8", "ignore") if v else None
                if field == "energylevel":
                    v = self.audio.get(MP4_ENERGY_ATOM)
                    return v[0].decode("utf-8", "ignore") if v else None
                if field == "bpm":
                    v = self.audio.get("tmpo")
                    return str(v[0]) if v else None
                v = self.audio.get(MP4_KEYS.get(field))
                return v[0] if v else None
        except Exception:
            return None
        return None

    def mik_bpm(self):
        """The tempo in Mixed In Key's beat grid, written the way a BPM tag is, or None.

        Rounded to a whole BPM when it's within a twentieth of one (MIK measures
        140 as 140.999), otherwise to two places, so 93.33 stays 93.33.
        """
        if not self.ok:
            return None
        raw = None
        try:
            if self.kind == "id3":
                for f in self.audio.getall("GEOB"):
                    if f.desc == "BeatGrid":
                        raw = f.data
                        break
            elif self.kind == "vorbis":
                v = self.audio.get(MIK_GRID_VORBIS)
                raw = v[0].encode("ascii", "ignore") if v else None
            elif self.kind == "mp4":
                v = self.audio.get(MIK_GRID_MP4)
                raw = bytes(v[0]) if v else None
            if not raw:
                return None
            grid = json.loads(base64.b64decode(b"".join(raw.split())))
            tempo = float(grid.get("tempo") or 0) if isinstance(grid, dict) else 0
        except (binascii.Error, ValueError, TypeError, AttributeError):
            return None
        if not 20 <= tempo <= 400:
            return None
        if abs(tempo - round(tempo)) < 0.05:
            return str(int(round(tempo)))
        return f"{tempo:.2f}".rstrip("0").rstrip(".")

    # -- write --------------------------------------------------------------
    def set(self, field, value):
        if not self.ok:
            return
        if self.kind == "id3":
            if field == "comment":
                self.audio.delall("COMM")
                if value:
                    self.audio.add(COMM(encoding=3, lang="eng", desc="", text=[value]))
                return
            frame = ID3_FRAMES.get(field)
            if not frame:
                return
            self.audio.delall(frame.__name__)
            if value:
                self.audio.add(frame(encoding=3, text=[str(value)]))
        elif self.kind == "vorbis":
            k = VORBIS_KEYS.get(field, field.upper())
            if value:
                self.audio[k] = [str(value)]
            elif k in self.audio:
                del self.audio[k]
        elif self.kind == "mp4":
            if field == "key":
                if value:
                    from mutagen.mp4 import MP4FreeForm
                    self.audio[MP4_KEY_ATOM] = [MP4FreeForm(str(value).encode())]
                elif MP4_KEY_ATOM in self.audio:
                    del self.audio[MP4_KEY_ATOM]
                return
            if field == "bpm":
                if value:
                    self.audio["tmpo"] = [int(round(float(value)))]
                elif "tmpo" in self.audio:
                    del self.audio["tmpo"]
                return
            k = MP4_KEYS.get(field)
            if not k:
                return
            if value:
                self.audio[k] = [str(value)]
            elif k in self.audio:
                del self.audio[k]

    def save(self):
        if not self.ok:
            return False
        try:
            if self.kind == "id3" and self._iff is not None:
                self._iff.save(v2_version=3)              # v2.3 = best DJ-app compat
            elif self.kind == "id3":
                self.audio.save(self.path, v2_version=3)
            else:
                self.audio.save()
            return True
        except Exception:
            return False

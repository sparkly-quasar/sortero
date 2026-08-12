"""Format-agnostic read/write for the tag fields DJ software actually reads."""
import mutagen
from mutagen.id3 import ID3, TKEY, TIT1, TCON, TPE1, TIT2, TBPM, COMM, ID3NoHeaderError
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4

# logical field -> per-format tag name
ID3_FRAMES = {"key": TKEY, "grouping": TIT1, "genre": TCON,
              "artist": TPE1, "title": TIT2, "bpm": TBPM}
VORBIS_KEYS = {"key": "INITIALKEY", "grouping": "GROUPING", "genre": "GENRE",
               "artist": "ARTIST", "title": "TITLE", "bpm": "BPM",
               "comment": "COMMENT"}
MP4_KEYS = {"grouping": "\xa9grp", "genre": "\xa9gen", "artist": "\xa9ART",
            "title": "\xa9nam", "comment": "\xa9cmt"}
MP4_KEY_ATOM = "----:com.apple.iTunes:initialkey"


class Track:
    """Thin wrapper exposing key/grouping/genre/artist/title/bpm/comment."""

    def __init__(self, path):
        self.path = path
        self.kind = None
        self.audio = None
        try:
            a = mutagen.File(path)
        except Exception:
            a = None
        if isinstance(a, (FLAC, OggVorbis)):
            self.kind, self.audio = "vorbis", a
        elif isinstance(a, MP4):
            self.kind, self.audio = "mp4", a
        else:
            # mp3 / aiff / wav -> ID3 sidecar
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
    def length(self):
        """Duration in seconds, or 0.0 when unavailable."""
        try:
            if self.kind in ("vorbis", "mp4") and self.audio is not None:
                return float(self.audio.info.length)
            import mutagen
            a = mutagen.File(self.path)
            return float(a.info.length) if a is not None else 0.0
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
                if field == "bpm":
                    v = self.audio.get("tmpo")
                    return str(v[0]) if v else None
                v = self.audio.get(MP4_KEYS.get(field))
                return v[0] if v else None
        except Exception:
            return None
        return None

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
                    self.audio["tmpo"] = [int(float(value))]
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
            if self.kind == "id3":
                self.audio.save(self.path, v2_version=3)  # v2.3 = best DJ-app compat
            else:
                self.audio.save()
            return True
        except Exception:
            return False

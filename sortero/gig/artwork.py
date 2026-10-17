"""Track artwork on the main stick, where a Pioneer player shows it beside the track.

rekordbox writes each image four times under PIONEER/Artwork, twenty images
to a five-digit folder (ids 1-19 in 00001, 20-39 in 00002, and so on):

  a<id>.jpg    80 x 80      b<id>.jpg    the same bytes
  a<id>_m.jpg  240 x 240    b<id>_m.jpg  the same bytes

export.pdb's artwork table and OneLibrary's image table name the a<id>.jpg
file, and a track names its image by id. All of this is as rbxport found it
on rekordbox's own exports (crates/rbl-export/src/lib.rs, artwork_path).

The picture is the one embedded in the audio file (tagcopy.read), scaled by
ffmpeg. The ffmpeg Sortero carries has the scale filter but no crop or pad,
so a cover that isn't square is stretched to fit, as the players want a
square; almost every cover is square already. Tracks with the same picture
share one image.
"""
import hashlib, os, tempfile

from . import ffmpeg, tagcopy

SMALL, MEDIUM = 80, 240
PER_FOLDER = 20


def path(image_id, prefix="a", medium=False):
    folder = image_id // PER_FOLDER + 1
    return f"/PIONEER/Artwork/{folder:05d}/{prefix}{image_id}{'_m' if medium else ''}.jpg"


def render(data, px):
    """A px x px JPEG of an image, or None if ffmpeg can't read it."""
    try:
        exe = ffmpeg.require("ffmpeg")
    except ffmpeg.Missing:
        return None
    with tempfile.TemporaryDirectory(prefix="sortero-art-") as d:
        src, dst = os.path.join(d, "in.img"), os.path.join(d, "out.jpg")
        with open(src, "wb") as fh:
            fh.write(data)
        r = ffmpeg.run([exe, "-v", "error", "-nostdin", "-y", "-i", src,
                        "-vf", f"scale={px}:{px}", "-pix_fmt", "yuvj420p", "-q:v", "3",
                        "-frames:v", "1", "-c:v", "mjpeg", "-f", "image2", dst])
        if r.returncode != 0 or not os.path.exists(dst):
            return None
        with open(dst, "rb") as fh:
            return fh.read()


class Collector:
    """The images a gig's tracks carry, one id per distinct picture."""

    def __init__(self):
        self.ids = {}           # digest of the embedded picture -> image id
        self.files = {}         # stick path -> JPEG bytes

    def add(self, audio_path):
        """The image id for this track's embedded artwork, or 0 if it has none."""
        try:
            _, art = tagcopy.read(audio_path)
        except Exception:
            return 0
        if not art or not art[1]:
            return 0
        digest = hashlib.sha1(art[1]).hexdigest()
        if digest in self.ids:
            return self.ids[digest]
        small, medium = render(art[1], SMALL), render(art[1], MEDIUM)
        if not small or not medium:
            return 0
        image_id = len(self.ids) + 1
        self.ids[digest] = image_id
        for prefix in "ab":
            self.files[path(image_id, prefix)] = small
            self.files[path(image_id, prefix, True)] = medium
        return image_id

    def table(self):
        """[(image id, the path the databases name)]."""
        return [(i, path(i)) for i in sorted(self.ids.values())]

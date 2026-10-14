#!/usr/bin/env python3
"""Take the README's screenshots: every main screen, on a made-up collection.

    xvfb-run -s "-screen 0 1200x800x24" python tools/screenshots.py docs/screenshots

Needs ffmpeg (a full one, for making the sample audio) and ImageMagick's
`import`. Nothing touches your own collection or settings: the sample
collection, Sortero's settings and the "stick" all live in a temporary
folder. Artists and titles are made up.

The stick Gig prep checks is a plain folder, so there's no real file system
to read; the script reports it as FAT32 on one MBR partition, which is what
a stick Sortero formatted would be, so the screenshot shows a stick that
passes.
"""
import base64, json, os, shutil, subprocess, sys, tempfile, zlib

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
HOME = tempfile.mkdtemp(prefix="sortero-shots-")
os.environ.update(HOME=HOME, XDG_CONFIG_HOME=f"{HOME}/.config",
                  XDG_DATA_HOME=f"{HOME}/.local/share",
                  PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mutagen.id3 import COMM, GEOB, ID3, TBPM, TCON, TIT2, TKEY, TPE1, TXXX  # noqa: E402

ROOT = os.path.join(HOME, "Music", "DJ Collection")
STICK = os.path.join(HOME, "GIG")

# (artist, title, genre or None, camelot key or None, bpm, energy or None, folders)
TRACKS = [
    ("Ana Lusk", "Saltmarsh", "Deep House", "9B", 121, 9, ["Spotify - Late Night Techno"]),
    ("Ana Lusk", "Harbour", "Deep House", "1B", 120, 5, ["Ana Lusk - Saltmarsh LP"]),
    ("Ana Lusk", "Slow Tide", "Deep House", "1B", 120, 5, ["Ana Lusk - Saltmarsh LP"]),
    ("Ana Lusk", "Tidewater", None, "4B", 118, 4, ["Downloads 2024", "Tidal - Sunday Deep"]),
    ("Dagny Holt", "Fracture Line", "Techno (Hypnotic)", "6A", 128, 7,
     ["Beatport Haul", "Spotify - Late Night Techno"]),
    ("Dagny Holt", "Pressure Drop", "Techno (Hypnotic)", "5A", 129, 8, ["Beatport Haul"]),
    ("Halden Voss", "Hydraulic", "Techno (Peak Time)", "3A", 136, 7, ["Tidal - Warmup Grooves"]),
    ("Halden Voss", "Subgrid", "Techno (Peak Time)", "5A", 133, 8,
     ["Spotify - Late Night Techno", "Tidal - Warmup Grooves"]),
    ("Ines Calder", "Microfiche", "Minimal / Deep Tech", "4A", 126, 3, ["Tidal - Warmup Grooves"]),
    ("Ines Calder", "Soft Machine", None, "7A", 125, 3, ["Downloads 2024"]),
    ("Juno Farrow", "Carousel", "Tech House", "3A", 126, None, ["Tidal - Sunday Deep"]),
    ("Juno Farrow", "Glasshouse", "Tech House", "8A", 124, 7, ["Spotify - Late Night Techno"]),
    ("Kepler Vale", "Ninth Hour", "Techno (Hypnotic)", "8A", 140, 6, ["Beatport Haul"]),
    ("Kepler Vale", "Lowlight", "Techno (Hypnotic)", None, 138, None, ["To sort"]),
    ("Mara Okonkwo", "Paper Moon", "Melodic House & Techno", "10A", 122, 6, ["Beatport Haul"]),
    ("Mara Okonkwo", "Static Bloom", None, None, 123, None, ["To sort"]),
    ("Rue Okafor", "Tidewater Dub", "Deep House", "2A", 119, 4, ["Tidal - Sunday Deep"]),
    ("Sable Achterberg", "Night Bus", "Tech House", "11B", 127, 6, ["Downloads 2024"]),
    ("Teo Marchetti", "Hollow", "Minimal / Deep Tech", "6B", 127, 5, ["Beatport Haul"]),
    ("Teo Marchetti", "Glass Floor", "Minimal / Deep Tech", "6A", 128, 6, ["Beatport Haul"]),
    ("Wren Ishida", "Lantern", "Techno (Peak Time)", "10A", 135, 8, ["Beatport Haul"]),
]
# Title - Artist, the wrong way round, and tags that say so
SWAPPED = [("Wren Ishida", "Undertow", "Techno (Peak Time)", "9A", 134, 8, ["Downloads 2024"])]
# What a gig needs copies of: a hi-res WAV and a FLAC. And one AIFF whose
# header an old Sortero damaged, for To do to offer to repair.
NEEDS_COPY = {("Kepler Vale", "Ninth Hour"): ("flac", 96000),
              ("Mara Okonkwo", "Paper Moon"): ("wav", 96000)}
DAMAGED = {("Ines Calder", "Soft Machine")}
PLAYLISTS = {"Warmup": [("Ana Lusk", "Harbour"), ("Ines Calder", "Microfiche"),
                        ("Rue Okafor", "Tidewater Dub"), ("Juno Farrow", "Carousel")],
             "Peak": [("Dagny Holt", "Fracture Line"), ("Kepler Vale", "Ninth Hour"),
                      ("Halden Voss", "Subgrid"), ("Mara Okonkwo", "Paper Moon")],
             "Closing": [("Ana Lusk", "Slow Tide"), ("Teo Marchetti", "Hollow")]}


def mik(obj):
    b64 = base64.b64encode(json.dumps({**obj, "source": "mixedinkey"}).encode()).decode()
    return "".join(b64[i:i + 72] + "\n" for i in range(0, len(b64), 72)).encode()


def make_audio(path, kind="mp3", rate=44100, tone=440):
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "320k"], "flac": ["-c:a", "flac"],
             "wav": ["-c:a", "pcm_s24le"], "aiff": ["-c:a", "pcm_s16be"]}[kind]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={tone}:sample_rate={rate}:duration=3",
                    "-ac", "2", *codec, path], check=True)


def tag(path, artist, title, genre, key, bpm, energy):
    frames = [TPE1(encoding=3, text=[artist]), TIT2(encoding=3, text=[title]),
              TBPM(encoding=3, text=[str(bpm)])]
    if genre:
        frames.append(TCON(encoding=3, text=[genre]))
    if key:
        frames.append(TKEY(encoding=3, text=[key]))
    if energy:
        frames += [TXXX(encoding=3, desc="EnergyLevel", text=[str(energy)]),
                   COMM(encoding=3, lang="eng", desc="", text=[f"{key} - Energy {energy}"]),
                   GEOB(encoding=3, mime="application/json", desc="CuePoints", filename="",
                        data=mik({"algorithm": 14, "cues": [
                            {"name": f"Energy {energy}", "time": 15.0},
                            {"name": f"Energy {energy + 1}", "time": 1500.0}]}))]
    if path.endswith(".flac"):
        from mutagen.flac import FLAC
        f = FLAC(path)
        f["ARTIST"], f["TITLE"], f["BPM"] = artist, title, str(bpm)
        if genre:
            f["GENRE"] = genre
        if key:
            f["INITIALKEY"] = key
        f.save()
        return
    if path.endswith((".wav", ".aiff")):
        import mutagen
        f = mutagen.File(path)
        f.add_tags()
        for fr in frames:
            f.tags.add(fr)
        f.save(v2_version=3)
        return
    t = ID3()
    for fr in frames:
        t.add(fr)
    t.save(path, v2_version=3)


def build_collection():
    where = {}
    swapped = {(a, t) for a, t, *_ in SWAPPED}
    for artist, title, genre, key, bpm, energy, folders in TRACKS + SWAPPED:
        kind, rate = NEEDS_COPY.get((artist, title), ("mp3", 44100))
        if (artist, title) in DAMAGED:
            kind = "aiff"
        # the same tone for every copy of a track, so Duplicates finds exact ones
        tone = 200 + zlib.crc32(f"{artist}{title}".encode()) % 700
        for folder in folders:
            os.makedirs(os.path.join(ROOT, folder), exist_ok=True)
            if (artist, title) in swapped:     # "Title - Artist", tagged to match
                name, a, t = f"{title} - {artist}", title, artist
            else:
                name, a, t = f"{artist} - {title}", artist, title
            path = os.path.join(ROOT, folder, f"{name}.{kind}")
            make_audio(path, kind, rate, tone)
            tag(path, a, t, genre, key, bpm, energy)
            if (artist, title) in DAMAGED:     # what the old tag writer did
                stray = ID3()
                stray.add(TKEY(encoding=3, text=[key]))
                stray.save(path, v2_version=3)
            where.setdefault((artist, title), path)
    junk = os.path.join(ROOT, "Downloads 2024", "Sable Achterberg - Night Bus.mp3")
    t = ID3(junk)
    t.delall("COMM")
    t.add(COMM(encoding=3, lang="eng", desc="", text=["www.djsoundtop.com"]))
    t.save(junk, v2_version=3)
    pl = os.path.join(ROOT, "_Playlists")
    os.makedirs(pl, exist_ok=True)
    for name, tracks in PLAYLISTS.items():
        with open(os.path.join(pl, f"{name}.m3u8"), "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n")
            for k in tracks:
                fh.write(os.path.relpath(where[k], pl) + "\n")
    os.makedirs(STICK)


def main():
    if not shutil.which("ffmpeg") or not shutil.which("import"):
        sys.exit("Needs ffmpeg and ImageMagick's import on the PATH.")
    build_collection()
    from sortero import settings
    settings.set("root", ROOT)
    settings.set("setup_complete", True)
    settings.set("check_updates_on_launch", False)
    settings.set("gig_playlists", ["Warmup", "Peak"])

    from tkinter import messagebox, filedialog
    messagebox.askyesno = lambda *a, **k: True
    messagebox.showinfo = lambda *a, **k: None
    filedialog.askdirectory = lambda **k: STICK
    from sortero.gig import disks, profiles
    disks.volume = lambda mount: disks.Volume(mount, profiles.FAT32, disks.MBR, 1)

    from sortero.app import Sortero
    app = Sortero()
    app._maybe_check_licence = lambda: None
    os.makedirs(OUT, exist_ok=True)

    def shot(name):
        app.update_idletasks()
        app.update()
        subprocess.run(["import", "-window", app.wm_frame(), os.path.join(OUT, name + ".png")],
                       check=True)
        print("saved", name)

    def idle(then, need=lambda: True):
        if app.task.running or not need():
            app.after(250, lambda: idle(then, need))
        else:
            app.after(600, then)

    steps = []

    def step(fn):
        steps.append(fn)
        return fn

    def go():
        if steps:
            steps.pop(0)()
        else:
            app.destroy()

    @step
    def todo():
        app.show("todo")
        app.after(800, lambda: (shot("todo"), go()))

    @step
    def library():
        app.show("library")
        app.after(800, lambda: (shot("library"), go()))

    @step
    def tidy():
        app.show("tidy")
        app.after(800, lambda: (shot("tidy-up"), go()))

    @step
    def tags():
        app.show("tags")
        app.screens["tags"].preview()
        idle(lambda: (shot("clean-tags"), go()))

    @step
    def dupes():
        app.show("dupes")
        app.screens["dupes"].find()
        idle(lambda: (shot("duplicates"), go()))

    @step
    def reorganise():
        app.show("reorganise")
        app.screens["reorganise"].preview()
        idle(lambda: (shot("reorganise"), go()))

    @step
    def gig():
        app.show("gig")
        g = app.screens["gig"]
        chain = [g.check_tracks, g.make_copies, g.make_xml,
                 lambda: (getattr(g, "how_win", None) and g.how_win.destroy(), g.write_stick())]

        def next_action():
            if chain:
                chain.pop(0)()
                idle(next_action, lambda: not app.task.running)
            else:
                idle(lambda: (shot("gig-prep"), go()), lambda: g.state.report is not None)
        app.after(800, next_action)

    idle(go, lambda: app.health is not None)
    app.mainloop()
    shutil.rmtree(HOME, ignore_errors=True)


if __name__ == "__main__":
    main()

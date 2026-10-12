"""What each Pioneer DJ / AlphaTheta player can read off a USB stick.

Every limit here is copied from the maker's own documentation and cited where
it is used. Nothing is filled in from memory: a manual that couldn't be found
leaves its field empty, and the code treats empty as "not known", never as
"anything goes".

Sources were retrieved in October 2026. Manuals live at
https://downloads.support.alphatheta.com/manuals/... (the links on each
player's pioneerdj.com product page, with ".pdf" added). Page numbers are the
PDF's own pages. For the CDJ-900NXS and XDJ-1000MK2 only the quick start guide
is linked from Pioneer's site, so their format tables come from copies of the
full Pioneer manual hosted by retailers; those are marked as such.

What the manuals settle, and what they don't:

* File systems: every player lists FAT16 and FAT32. HFS+ is missing on the
  original XDJ-RX, and exFAT appears only on the newest players (XDJ-RX3,
  OPUS-QUAD, CDJ-3000X); the CDJ-3000's own manual lists no exFAT, and the
  XDJ-XZ's says outright that exFAT isn't supported. FAT16 and FAT32 are all
  that every player here reads, and FAT16 tops out at a few gigabytes, so a
  gig stick is FAT32.
* Partition maps: no manual mentions MBR or GUID. MBR is the common
  recommendation, but it is not a documented requirement, so a GUID stick is
  a warning here, never a failure.
* MP3: MPEG-2 Layer 3 (16-24 kHz) is listed by the older players and not by
  the newer ones. Profiles list MPEG-1 rates only, so such a file is converted
  rather than gambled on.
"""
from dataclasses import dataclass, field

# --------------------------------------------------------------- vocabulary
FAIL, WARN = "fail", "warn"

FAT16, FAT32, EXFAT, HFS, NTFS = "FAT16", "FAT32", "exFAT", "HFS+", "NTFS"

# What a player does with a stick that has more than one partition.
FIRST_ONLY = "first-only"   # "only the first partition can be used"
MAY_FAIL = "may-fail"       # "the device may not be recognized"
UNKNOWN = "unknown"         # the manual doesn't say
_PARTITION_RISK = {FIRST_ONLY: 0, UNKNOWN: 1, MAY_FAIL: 2}

R_44_48 = frozenset({44100, 48000})
R_32_48 = frozenset({32000, 44100, 48000})
R_AAC_ALL = frozenset({16000, 22050, 24000, 32000, 44100, 48000})
R_UP_TO_96 = frozenset({44100, 48000, 88200, 96000})
D_16_24 = frozenset({16, 24})

FORMAT_NAMES = {"mp3": "MP3", "aac": "AAC", "wav": "WAV", "aiff": "AIFF",
                "flac": "FLAC", "alac": "Apple Lossless"}


@dataclass(frozen=True)
class Format:
    """One kind of file a player plays, and the limits it plays it within."""
    exts: frozenset
    rates: frozenset                    # Hz
    depths: frozenset = frozenset()     # bits; empty for lossy formats
    kbps: tuple = None                  # (lowest, highest), lossy formats only


def mp3(rates=R_44_48):
    # MPEG-1 Layer 3, 32-320 kbps, CBR or VBR, in every manual that has a table.
    return Format(frozenset({".mp3"}), rates, kbps=(32, 320))


def aac(rates=R_44_48):
    # MPEG-4 and MPEG-2 AAC LC, 16-320 kbps.
    return Format(frozenset({".m4a", ".aac", ".mp4"}), rates, kbps=(16, 320))


def pcm(ext, rates=R_44_48):
    return Format(frozenset(ext), rates, D_16_24)


def lossless(rates=R_44_48):
    """WAV, AIFF, FLAC and Apple Lossless at the same limits, as newer manuals list them."""
    return {"wav": pcm({".wav"}, rates), "aiff": pcm({".aif", ".aiff"}, rates),
            "flac": Format(frozenset({".flac", ".fla"}), rates, D_16_24),
            "alac": Format(frozenset({".m4a"}), rates, D_16_24)}


@dataclass(frozen=True)
class DeckProfile:
    key: str
    name: str
    file_systems: frozenset
    formats: dict = None            # None: no format table could be found
    partitions: str = UNKNOWN
    folder_depth: int = None        # deepest folder level whose files still play
    artwork_px: int = None          # larger embedded artwork isn't displayed
    sources: tuple = ()
    notes: tuple = ()

    @property
    def formats_known(self):
        return self.formats is not None


# --------------------------------------------------------------- the players
_DL = "https://downloads.support.alphatheta.com/manuals"

# Original CDJ-2000 and CDJ-900 share a manual layout. "FAT" in these two
# manuals is read as FAT16: the newer manuals for the same generation say FAT16.
_CDJ_2000_ERA = {"mp3": mp3(R_32_48), "aac": aac(R_AAC_ALL),
                 "wav": pcm({".wav"}), "aiff": pcm({".aif", ".aiff"})}

PLAYERS = (
    DeckProfile(
        "cdj-900", "CDJ-900", frozenset({FAT16, FAT32, HFS}), _CDJ_2000_ERA,
        partitions=MAY_FAIL, folder_depth=8,
        sources=(f"{_DL}/dj-players/CDJ-900/CDJ-900_DRB1484A_manual.pdf p.7",)),
    DeckProfile(
        "cdj-2000", "CDJ-2000", frozenset({FAT16, FAT32, HFS}), _CDJ_2000_ERA,
        partitions=MAY_FAIL, folder_depth=8, artwork_px=800,
        sources=(f"{_DL}/dj-players/CDJ-2000/CDJ-2000_DRB1475_manual.pdf pp.6-7",)),
    DeckProfile(
        "cdj-900nxs", "CDJ-900NXS", frozenset({FAT16, FAT32, HFS}), _CDJ_2000_ERA,
        partitions=FIRST_ONLY, folder_depth=8, artwork_px=800,
        sources=(f"{_DL}/dj-players/CDJ-900NXS/CDJ-900NXS_DRH1229_quickstart-manual.pdf p.4",
                 "https://www.fullcompass.com/common/files/37530-CDJ900NXSUserManual.pdf "
                 "pp.5-6 (Pioneer's full manual, retailer copy)")),
    DeckProfile(
        "cdj-2000nxs", "CDJ-2000NXS", frozenset({FAT16, FAT32, HFS}), _CDJ_2000_ERA,
        partitions=FIRST_ONLY, folder_depth=8, artwork_px=800,
        sources=(f"{_DL}/dj-players/CDJ-2000NXS/CDJ-2000NXS_DRI1052_manual.pdf pp.6-7",)),
    DeckProfile(
        "cdj-2000nxs2", "CDJ-2000NXS2", frozenset({FAT16, FAT32, HFS}),
        {"mp3": mp3(R_32_48), "aac": aac(R_AAC_ALL), **lossless(R_UP_TO_96)},
        partitions=FIRST_ONLY, folder_depth=8, artwork_px=800,
        sources=(f"{_DL}/dj-players/CDJ-2000NXS2/CDJ-2000NXS2_DRI1290A_manual.pdf pp.6-7",)),
    DeckProfile(
        "cdj-3000", "CDJ-3000", frozenset({FAT16, FAT32, HFS}),
        {"mp3": mp3(), "aac": aac(), **lossless(R_UP_TO_96)},
        folder_depth=8,
        sources=(f"{_DL}/dj-players/CDJ-3000/CDJ-3000_DRI1586A_manual.pdf pp.11, 13",),
        notes=("The manual lists FAT16, FAT32 and HFS+ only. Some shops list exFAT; "
               "a later firmware may have added it, but it isn't documented.",)),
    DeckProfile(
        "cdj-3000x", "CDJ-3000X", frozenset({FAT16, FAT32, EXFAT, HFS}),
        sources=("https://alphatheta.com/en/product/player/cdj-3000x/black/ (Specs)",),
        notes=("AlphaTheta's page lists MP3, AAC, WAV, AIFF, FLAC and ALAC but no "
               "sample rates or bit depths, so files aren't checked against it.",)),
    DeckProfile(
        "xdj-700", "XDJ-700", frozenset({FAT16, FAT32, HFS}),
        sources=(f"{_DL}/dj-players/XDJ-700/XDJ-700_DRH1384D_quickstart-manual.pdf p.5",)),
    DeckProfile(
        "xdj-1000", "XDJ-1000", frozenset({FAT16, FAT32, HFS}),
        sources=(f"{_DL}/dj-players/XDJ-1000/XDJ-1000_DRH1279A_quickstart-manual.pdf p.4",)),
    DeckProfile(
        "xdj-1000mk2", "XDJ-1000MK2", frozenset({FAT16, FAT32, HFS}),
        {"mp3": mp3(R_32_48), "aac": aac(R_AAC_ALL), **lossless()},
        partitions=FIRST_ONLY, folder_depth=8, artwork_px=800,
        sources=(f"{_DL}/dj-players/XDJ-1000MK2/XDJ-1000MK2_DRH1671C_quickstart-manual.pdf p.6",
                 "https://rent.djcenter.ee/images/kasutusjuhendid/Pioneer-XDJ-1000MK2.pdf "
                 "p.6 (Pioneer's full manual, retailer copy)")),
    DeckProfile(
        "xdj-rx", "XDJ-RX", frozenset({FAT16, FAT32}),
        sources=(f"{_DL}/all-in-one-dj-systems/XDJ-RX/XDJ-RX_DRH1293A_quickstart-manual.pdf p.5",),
        notes=("No HFS+: a Mac-formatted stick won't read.",)),
    DeckProfile(
        "xdj-rx2", "XDJ-RX2", frozenset({FAT16, FAT32, HFS}),
        sources=(f"{_DL}/all-in-one-dj-systems/XDJ-RX2/XDJ-RX2_DRH1448A_quickstart-manual.pdf p.6",)),
    DeckProfile(
        "xdj-rx3", "XDJ-RX3", frozenset({FAT16, FAT32, EXFAT, HFS}),
        # The table has no Apple Lossless row.
        {"mp3": mp3(R_32_48), "aac": aac(R_32_48),
         **{k: v for k, v in lossless().items() if k != "alac"}},
        partitions=FIRST_ONLY, folder_depth=8, artwork_px=800,
        sources=(f"{_DL}/all-in-one-dj-systems/XDJ-RX3/XDJ-RX3_DRI1702C_manual.pdf pp.9-10",)),
    DeckProfile(
        "xdj-xz", "XDJ-XZ", frozenset({FAT16, FAT32, HFS}),
        sources=(f"{_DL}/all-in-one-dj-systems/XDJ-XZ/XDJ-XZ_DRH1643D_quickstart-manual.pdf p.5",),
        notes=("The guide says outright that exFAT isn't supported.",)),
    DeckProfile(
        "xdj-rr", "XDJ-RR", frozenset({FAT16, FAT32, HFS}),
        sources=(f"{_DL}/all-in-one-dj-systems/XDJ-RR/XDJ-RR_DRH1516E_quickstart-manual.pdf p.5",)),
    DeckProfile(
        "opus-quad", "OPUS-QUAD", frozenset({FAT16, FAT32, EXFAT, HFS}),
        {"mp3": mp3(), "aac": aac(), **lossless(R_UP_TO_96)},
        partitions=FIRST_ONLY, folder_depth=8, artwork_px=800,
        sources=(f"{_DL}/all-in-one-dj-systems/OPUS-QUAD/OPUS-QUAD_DRI1795D_manual.pdf pp.9-10",)),
)


# ---------------------------------------------------------------- universal
def _both(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _meet(a, b):
    """The limits two players share for one format, or None if they share nothing."""
    exts, rates, depths = a.exts & b.exts, a.rates & b.rates, a.depths & b.depths
    kbps = None
    if a.kbps and b.kbps:
        kbps = (max(a.kbps[0], b.kbps[0]), min(a.kbps[1], b.kbps[1]))
        if kbps[0] > kbps[1]:
            return None
    if not exts or not rates or (a.depths and not depths):
        return None
    return Format(exts, rates, depths, kbps)


def intersect(players, key="universal", name="Any Pioneer player"):
    """A profile that only allows what every one of these players allows.

    Players whose format table couldn't be found leave the formats alone and
    are named in the notes; their file systems, which every manual lists, still
    count.
    """
    players = list(players)
    fs = frozenset.intersection(*(p.file_systems for p in players))
    formats = None
    for p in players:
        if not p.formats_known:
            continue
        if formats is None:
            formats = dict(p.formats)
            continue
        formats = {k: f for k, f in ((k, _meet(formats[k], p.formats[k]))
                                     for k in formats.keys() & p.formats.keys()) if f}
    partitions = max((p.partitions for p in players), key=_PARTITION_RISK.get)
    depth = art = None
    for p in players:
        depth, art = _both(depth, p.folder_depth), _both(art, p.artwork_px)
    unknown = [p.name for p in players if not p.formats_known]
    notes = ()
    if unknown:
        notes = (f"Format limits come from the manuals that list them. No format "
                 f"table could be found for {', '.join(unknown)}.",)
    return DeckProfile(key, name, fs, formats, partitions, depth, art,
                       sources=tuple(s for p in players for s in p.sources), notes=notes)


UNIVERSAL = intersect(PLAYERS)
PROFILES = {p.key: p for p in (UNIVERSAL,) + PLAYERS}
DEFAULT = UNIVERSAL.key


def get(key):
    return PROFILES.get(key) or UNIVERSAL


# ------------------------------------------------------------------ checking
@dataclass(frozen=True)
class Problem:
    level: str      # FAIL: the manual rules it out. WARN: a risk, or not known.
    text: str


@dataclass
class AudioInfo:
    """What a file is, as far as a player cares. Filled in by probing the file."""
    format: str                 # "mp3", "aac", "wav", "aiff", "flac", "alac", or another name
    ext: str                    # ".mp3", lowercase
    rate: int                   # Hz
    depth: int = None           # bits, for uncompressed and lossless audio
    kbps: int = None            # for lossy audio
    aac_profile: str = None     # "LC", "HE-AAC", ...
    extras: dict = field(default_factory=dict)


def _khz(hz):
    return f"{hz / 1000:g} kHz"


def _list(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


def check_audio(profile, info):
    """Problems a player would have with this file. Empty when it plays."""
    who = profile.name
    if not profile.formats_known:
        return [Problem(WARN, f"{who}'s manual couldn't be found, so Sortero can't "
                              "say whether it plays this file.")]
    fmt = profile.formats.get(info.format)
    label = FORMAT_NAMES.get(info.format, info.format.upper())
    if fmt is None:
        return [Problem(FAIL, f"{who} doesn't play {label} files.")]
    out = []
    if info.ext.lower() not in fmt.exts:
        out.append(Problem(FAIL, f"{who} only reads {label} files named "
                                 f"{_list(sorted(fmt.exts))}, not {info.ext}."))
    if info.rate not in fmt.rates:
        out.append(Problem(FAIL, f"{who} doesn't play {label} at {_khz(info.rate)} "
                                 f"(only {_list(_khz(r) for r in sorted(fmt.rates))})."))
    if fmt.depths and info.depth not in fmt.depths:
        depth = f"{info.depth}-bit" if info.depth else "this bit depth"
        out.append(Problem(FAIL, f"{who} doesn't play {depth} {label} "
                                 f"(only {_list(f'{d}-bit' for d in sorted(fmt.depths))})."))
    if fmt.kbps and info.kbps and not fmt.kbps[0] <= info.kbps <= fmt.kbps[1]:
        out.append(Problem(FAIL, f"{who} plays {label} at {fmt.kbps[0]}-{fmt.kbps[1]} kbps, "
                                 f"not {info.kbps} kbps."))
    if info.format == "aac" and info.aac_profile and info.aac_profile.upper() != "LC":
        out.append(Problem(FAIL, f"{who} only plays AAC-LC, not {info.aac_profile}."))
    return out


def check_volume(profile, file_system, partitions=1, scheme=None):
    """Problems a player would have with the stick itself.

    `file_system` is one of FAT16, FAT32, EXFAT, HFS, NTFS; `scheme` is "MBR",
    "GPT" or None when unknown.
    """
    who = profile.name
    out = []
    if file_system not in profile.file_systems:
        out.append(Problem(FAIL, f"{who} can't read {file_system}. Format the stick "
                                 f"as {FAT32}."))
    if partitions > 1:
        if profile.partitions == MAY_FAIL:
            out.append(Problem(FAIL, f"{who} may not recognise a stick with more than "
                                     "one partition."))
        elif profile.partitions == FIRST_ONLY:
            out.append(Problem(WARN, f"{who} only reads the first partition."))
        else:
            out.append(Problem(WARN, "The stick has more than one partition, and not "
                                     "every player says what it does with that."))
    if scheme == "GPT":
        out.append(Problem(WARN, "The stick uses a GUID partition map. Pioneer's manuals "
                                 "don't mention partition maps; MBR is the safe, usual "
                                 "choice."))
    return out

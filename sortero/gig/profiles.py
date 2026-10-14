"""What each Pioneer DJ / AlphaTheta and Denon DJ player can read off a USB stick.

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

Denon DJ's players run Engine OS. Their current guides (Engine DJ 5.0, from
https://cdn.inmusicbrands.com/Software/ENDJ5/, retrieved October 2026; see
docs/research/denon-engine-dj.md) all carry the same table: exFAT and FAT32
(the SC6000 and SC6000M also HFS+), and MP3, AAC/M4A, MP4, ALAC, FLAC, Ogg
Vorbis, WAV and AIFF, with limits for only some of them. A limit the table
doesn't give is None here ("not stated"), so it's neither checked nor assumed;
only Pioneer's own limits apply when the two are combined. FAT32 is the one
file system both makers' players all read.
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

# The library a rekordbox USB export carries for players to browse. Older
# players read the Device Library (export.pdb); the newest read only OneLibrary
# (exportLibrary.db, formerly "Device Library Plus"). rekordbox 7.2.11 and
# later writes both. Source: AlphaTheta, "Important Notice for Customers Using
# USB Devices with Our DJ Equipment" (18 March 2026), LIBRARY_NOTICE.
DEVICE_LIBRARY, ONE_LIBRARY = "Device Library", "OneLibrary"
REKORDBOX_LIBRARIES = frozenset({DEVICE_LIBRARY, ONE_LIBRARY})
# Denon's players read an Engine database, written to the stick by Engine DJ
# (Engine DJ User Guide v5.0.0 p. 5); without one they browse it as folders.
ENGINE_LIBRARY = "Engine Library"
LIBRARY_NOTICE = ("https://alphatheta.com/en/information/"
                  "important-notice-for-customers-using-usb-devices-with-our-dj-equipment/")

R_44_48 = frozenset({44100, 48000})
R_32_48 = frozenset({32000, 44100, 48000})
R_AAC_ALL = frozenset({16000, 22050, 24000, 32000, 44100, 48000})
R_UP_TO_96 = frozenset({44100, 48000, 88200, 96000})
R_UP_TO_192 = frozenset({44100, 48000, 88200, 96000, 176400, 192000})
D_16_24 = frozenset({16, 24})
D_16_32 = frozenset({16, 24, 32})

FORMAT_NAMES = {"mp3": "MP3", "aac": "AAC", "wav": "WAV", "aiff": "AIFF",
                "flac": "FLAC", "alac": "Apple Lossless", "vorbis": "Ogg Vorbis"}


@dataclass(frozen=True)
class Format:
    """One kind of file a player plays, and the limits it plays it within.

    None for rates, depths or kbps means the maker doesn't state a limit: it
    isn't checked. An empty depths means bit depth doesn't apply (lossy)."""
    exts: frozenset
    rates: frozenset = None             # Hz
    depths: frozenset = frozenset()     # bits
    kbps: tuple = None                  # (lowest, highest), lossy formats only
    lc_only: bool = False               # AAC: only the LC profile is listed


def mp3(rates=R_44_48):
    # MPEG-1 Layer 3, 32-320 kbps, CBR or VBR, in every manual that has a table.
    return Format(frozenset({".mp3"}), rates, kbps=(32, 320))


def aac(rates=R_44_48):
    # MPEG-4 and MPEG-2 AAC LC, 16-320 kbps.
    return Format(frozenset({".m4a", ".aac", ".mp4"}), rates, kbps=(16, 320), lc_only=True)


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
    libraries: frozenset = frozenset({DEVICE_LIBRARY})  # the stick libraries it reads
    brand: str = "Pioneer"
    group: str = None               # for a group profile, what one of them is called

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
        sources=("https://alphatheta.com/en/product/player/cdj-3000x/black/ (Specs)",
                 LIBRARY_NOTICE),
        libraries=frozenset({ONE_LIBRARY}),
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
        sources=(f"{_DL}/all-in-one-dj-systems/OPUS-QUAD/OPUS-QUAD_DRI1795D_manual.pdf pp.9-10",
                 LIBRARY_NOTICE),
        libraries=frozenset({ONE_LIBRARY})),
)

# Denon DJ. Every current guide carries the same table (see the module notes);
# none states a folder depth, file name, partition or artwork limit.
_ENDJ5 = "https://cdn.inmusicbrands.com/Software/ENDJ5"
_SC_GUIDE = (f"{_ENDJ5}/SC6000%20PRIME%2C%20SC6000M%20PRIME%2C%20SC5000%20PRIME%2C%20"
             "SC5000M%20PRIME%20-%20User%20Guide%20-%20v5.0.0.pdf")
_PRIME_GUIDE = (f"{_ENDJ5}/PRIME%204%2C%20PRIME%204%2B%2C%20PRIME%202%2C%20PRIME%20GO%2C%20"
                "PRIME%20GO%2B%2C%20SC%20LIVE%204%2C%20SC%20LIVE%202%20-%20User%20Guide"
                "%20-%20v5.0.0.pdf")
_G2_GUIDE = ("https://cdn.inmusicbrands.com/denondj/Prime4G2/PRIME%204%20G2%20-%20"
             "User%20Guide%20-%20v5.1.0%201.pdf")
ENGINE_FORMATS = {
    "mp3": Format(frozenset({".mp3"}), kbps=(32, 320)),
    "aac": Format(frozenset({".m4a", ".mp4"})),        # "AAC/M4A" and "MP4"
    "alac": Format(frozenset({".m4a"}), depths=None),
    "flac": Format(frozenset({".flac"}), depths=None),
    "vorbis": Format(frozenset({".ogg"})),
    "wav": Format(frozenset({".wav"}), R_UP_TO_192, D_16_32),
    "aiff": Format(frozenset({".aif", ".aiff"}), R_UP_TO_192, D_16_32),
}
_ENGINE_NOTE = ("Denon's guide gives no sample rates for MP3, AAC, ALAC, FLAC or Ogg "
                "Vorbis, and no folder depth, file name, partition or artwork limits, "
                "so those aren't checked.")


def _denon(key, name, guide, page, hfs=False):
    return DeckProfile(
        key, name, frozenset({EXFAT, FAT32} | ({HFS} if hfs else set())), ENGINE_FORMATS,
        sources=(f"{guide} p.{page}",), notes=(_ENGINE_NOTE,),
        libraries=frozenset({ENGINE_LIBRARY}), brand="Denon")


DENON_PLAYERS = (
    _denon("sc6000", "SC6000 / SC6000M PRIME", _SC_GUIDE, 9, hfs=True),
    _denon("sc5000", "SC5000 / SC5000M PRIME", _SC_GUIDE, 9),
    _denon("prime-4", "PRIME 4 / PRIME 4+", _PRIME_GUIDE, 5),
    _denon("prime-4-g2", "PRIME 4 G2", _G2_GUIDE, 5),
    _denon("prime-2", "PRIME 2", _PRIME_GUIDE, 5),
    _denon("prime-go", "PRIME GO / PRIME GO+", _PRIME_GUIDE, 5),
    _denon("sc-live-4", "SC LIVE 4", _PRIME_GUIDE, 5),
    _denon("sc-live-2", "SC LIVE 2", _PRIME_GUIDE, 5),
)


# ---------------------------------------------------------------- universal
def _both(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _shared(a, b):
    """Two stated limits meet; a limit nobody states (None) gives way to one that is."""
    if a is None:
        return b
    if b is None:
        return a
    return a & b


def _meet(a, b):
    """The limits two players share for one format, or None if they share nothing."""
    exts, rates = a.exts & b.exts, _shared(a.rates, b.rates)
    depths = _shared(a.depths, b.depths)
    kbps = a.kbps or b.kbps
    if a.kbps and b.kbps:
        kbps = (max(a.kbps[0], b.kbps[0]), min(a.kbps[1], b.kbps[1]))
        if kbps[0] > kbps[1]:
            return None
    if not exts or rates == frozenset() or (depths == frozenset() and (a.depths or b.depths)):
        return None
    return Format(exts, rates, depths, kbps, a.lc_only or b.lc_only)


def intersect(players, key="universal", name="Any Pioneer player", group="Pioneer player"):
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
    # A stick for all of them has to carry every library any of them reads.
    libraries = frozenset().union(*(p.libraries for p in players))
    sources = tuple(dict.fromkeys(s for p in players for s in p.sources))
    notes += tuple(dict.fromkeys(n for p in players if p.brand == "Denon" for n in p.notes))
    brands = {p.brand for p in players}
    return DeckProfile(key, name, fs, formats, partitions, depth, art,
                       sources=sources, notes=notes, libraries=libraries,
                       brand=brands.pop() if len(brands) == 1 else "", group=group)


UNIVERSAL = intersect(PLAYERS)
ANY_DENON = intersect(DENON_PLAYERS, "denon", "Any Denon player", "Denon player")
EVERY = intersect(PLAYERS + DENON_PLAYERS, "pioneer-denon", "Any Pioneer or Denon player",
                  "Pioneer or Denon player")
PROFILES = {p.key: p for p in (EVERY, UNIVERSAL, ANY_DENON) + PLAYERS + DENON_PLAYERS}
DEFAULT = EVERY.key


def get(key):
    return PROFILES.get(key) or EVERY


def reads_engine(profile):
    return ENGINE_LIBRARY in profile.libraries


def reads_rekordbox(profile):
    return bool(profile.libraries & REKORDBOX_LIBRARIES)


def where(profile):
    """How a sentence names the players: "every Pioneer player" or "the CDJ-2000"."""
    return f"every {profile.group}" if profile.group else f"the {profile.name}"


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


def _say(profile, one, every):
    """A sentence about one player ("The CDJ-2000 doesn't ..."), or about all of
    them ("Not every {a} ...", "{All} only ..."), named for the group."""
    if profile.group:
        a = profile.group
        return every.format(a=a, All=a.replace(" or ", " and ") + "s")
    return one.format(who=f"The {profile.name}")


def check_audio(profile, info):
    """Problems a player would have with this file. Empty when it plays."""
    if not profile.formats_known:
        return [Problem(WARN, f"The {profile.name}'s manual couldn't be found, so Sortero "
                              "can't say whether it plays this file.")]
    fmt = profile.formats.get(info.format)
    label = FORMAT_NAMES.get(info.format, info.format.upper())
    if fmt is None:
        return [Problem(FAIL, _say(profile, f"{{who}} doesn't play {label} files.",
                                   f"Not every {{a}} plays {label} files."))]
    out = []
    if info.ext.lower() not in fmt.exts:
        named = f"{label} files named {_list(sorted(fmt.exts))}, not {info.ext}"
        out.append(Problem(FAIL, _say(profile, f"{{who}} only reads {named}.",
                                      f"{{All}} only read {named}.")))
    if fmt.rates is not None and info.rate not in fmt.rates:
        ok = _list(_khz(r) for r in sorted(fmt.rates))
        out.append(Problem(FAIL, _say(
            profile, f"{{who}} doesn't play {label} at {_khz(info.rate)} (only {ok}).",
            f"Not every {{a}} plays {label} at {_khz(info.rate)} "
            f"(they all play {ok}).")))
    if fmt.depths and info.depth not in fmt.depths:
        depth = f"{info.depth}-bit" if info.depth else "this bit depth of"
        ok = _list(f"{d}-bit" for d in sorted(fmt.depths))
        out.append(Problem(FAIL, _say(
            profile, f"{{who}} doesn't play {depth} {label} (only {ok}).",
            f"Not every {{a}} plays {depth} {label} (they all play {ok}).")))
    if fmt.kbps and info.kbps and not fmt.kbps[0] <= info.kbps <= fmt.kbps[1]:
        span = f"{label} at {fmt.kbps[0]}-{fmt.kbps[1]} kbps, not {info.kbps} kbps"
        out.append(Problem(FAIL, _say(profile, f"{{who}} plays {span}.",
                                      f"{{All}} play {span}.")))
    if (info.format == "aac" and fmt.lc_only and info.aac_profile
            and info.aac_profile.upper() != "LC"):
        out.append(Problem(FAIL, _say(
            profile, f"{{who}} only plays AAC-LC, not {info.aac_profile}.",
            f"Not every {{a}} plays {info.aac_profile} (only AAC-LC).")))
    return out


def check_volume(profile, file_system, partitions=1, scheme=None):
    """Problems a player would have with the stick itself.

    `file_system` is one of FAT16, FAT32, EXFAT, HFS, NTFS; `scheme` is "MBR",
    "GPT" or None when unknown.
    """
    out = []
    if file_system not in profile.file_systems:
        out.append(Problem(FAIL, _say(
            profile, f"{{who}} can't read {file_system}. Format the stick as {FAT32}.",
            f"Not every {{a}} can read {file_system}. Format the stick as {FAT32}.")))
    if partitions > 1:
        if profile.partitions == MAY_FAIL:
            out.append(Problem(FAIL, _say(
                profile, "{who} may not recognise a stick with more than one partition.",
                "Older players (CDJ-900, CDJ-2000) may not recognise a stick with more "
                "than one partition.")))
        elif profile.partitions == FIRST_ONLY:
            out.append(Problem(WARN, _say(profile, "{who} only reads the first partition.",
                                          "Players only read the first partition.")))
        else:
            out.append(Problem(WARN, "The stick has more than one partition, and not "
                                     "every player says what it does with that."))
    if scheme == "GPT":
        out.append(Problem(WARN, "The stick uses a GUID partition map. The players' manuals "
                                 "don't mention partition maps; MBR is the safe, usual "
                                 "choice."))
    return out

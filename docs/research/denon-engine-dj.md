# Denon DJ (Engine DJ) players: what Gig prep would need

Research for adding Denon players to Gig prep's **Players** menu, the way the
Pioneer players are there now. Everything below comes from Denon DJ's own
current user guides (the Engine DJ 5.0 generation, downloaded 14 October
2026 from denondj.com/downloads and enginedj.com/downloads). Anything the
guides don't say is listed under *Not documented*, not guessed. Page numbers are the
PDF's own page numbers, which run one or two ahead of the printed ones.

## Sources

| Guide | Covers | File |
|---|---|---|
| SC6000 PRIME, SC6000M PRIME, SC5000 PRIME, SC5000M PRIME, User Guide v5.0.0 (109 pp., modified 7 May 2026) | the club media players | `https://cdn.inmusicbrands.com/Software/ENDJ5/SC6000%20PRIME%2C%20SC6000M%20PRIME%2C%20SC5000%20PRIME%2C%20SC5000M%20PRIME%20-%20User%20Guide%20-%20v5.0.0.pdf` |
| PRIME 4, PRIME 4+, PRIME 2, PRIME GO, PRIME GO+, SC LIVE 4, SC LIVE 2, User Guide v5.0.0 (79 pp., 27 August 2026) | the standalone all-in-one units | `https://cdn.inmusicbrands.com/Software/ENDJ5/PRIME%204%2C%20PRIME%204%2B%2C%20PRIME%202%2C%20PRIME%20GO%2C%20PRIME%20GO%2B%2C%20SC%20LIVE%204%2C%20SC%20LIVE%202%20-%20User%20Guide%20-%20v5.0.0.pdf` |
| PRIME 4 G2, User Guide v5.1.0 (66 pp., 6 May 2026) | the second-generation PRIME 4 | `https://cdn.inmusicbrands.com/denondj/Prime4G2/PRIME%204%20G2%20-%20User%20Guide%20-%20v5.1.0%201.pdf` |
| Engine DJ (desktop software), User Guide v5.0.0 (53 pp., 27 April 2026) | preparing a stick on a computer | `https://cdn.inmusicbrands.com/Software/ENDJ5/Engine%20DJ%20-%20User%20Guide%20-%20v5.0.0.pdf` |
| RANE SYSTEM ONE, User Guide v5.0.0; Numark Mixstream Pro, Pro+, Pro Go, User Guide v5.0.0 | other Engine OS hardware, same platform | linked from enginedj.com/downloads |

## What the players read

**File systems.** Every guide lists *exFAT (recommended), FAT32*. The SC6000
and SC6000M PRIME also read **HFS+**; the SC5000 and SC5000M, and every
all-in-one unit, don't.
(SC guide p. 9 "Devices & File Analysis", spec table p. 64; PRIME guide p. 5;
PRIME 4 G2 guide p. 5; SYSTEM ONE p. 6; Mixstream p. 5.)

**Formats.** The same table in every guide:

| Format | Limits stated |
|---|---|
| MP3 | 32–320 kbps |
| AAC/M4A, MP4 | none stated |
| ALAC | none stated |
| FLAC | none stated |
| Ogg Vorbis | none stated |
| WAV | 44.1–192 kHz, 16–32-bit |
| AIF/AIFF | 44.1–192 kHz, 16–32-bit |

That is a superset of what *Any Pioneer player* allows (MP3 and AAC at 44.1
or 48 kHz, WAV and AIFF at 16 or 24-bit and 44.1 or 48 kHz). Every file a
Pioneer-safe stick holds is in Denon's table, including Gig prep's copies
(MP3 320 CBR at 44.1 kHz, 16-bit/44.1 kHz AIFF). The desktop guide adds
"VBR/CBR" to MP3 for the software (p. 15); the player guides don't mention
VBR either way.

**Browsing without a library.** Library View has a **Folder** option: "browse
the list of all files on a USB drive or SD card" (SC guide p. 31; PRIME guide
p. 41; the PRIME 4 G2 guide, p. 21, says "the list of supported files"). Tracks that
haven't been analysed play straight away and are analysed on load (SC guide
p. 9). So Sortero's **backup stick**, plain numbered folders, is browsable on
Denon players as it is, provided the stick is exFAT or FAT32.

**Playlists, cues and grids** come from an **Engine database** on the drive
("Your Engine OS hardware searches for music using the Engine database that
is created on the drive", desktop guide p. 5), kept in an `Engine Library`
folder at the drive's root (PRIME guide p. 43). Engine DJ desktop writes it:

- **It imports a rekordbox XML.** "Before proceeding, make sure you have
  exported your library from Rekordbox as an XML file", then Import →
  Rekordbox → Select XML → *Manually Select* to pick playlists. It brings in
  "Rekordbox hot cues, memory loops and beat grids", with a choice of
  importing memory cues as memory or hot cues. *Rekordbox Library Integration*
  must be on in Library Preferences. (Desktop guide pp. 30–31.)
- **Sync Manager → Export to Drive** puts the chosen playlists, their tracks
  and performance data on the stick (pp. 32–33).
- It also reads Serato and Traktor libraries (pp. 28–29), and exports
  playlists as CSV, M3U or JSON (p. 37), but has no M3U *import* listed.

**Version caveat.** Engine DJ 5.0.0 (4.5.0 for SYSTEM ONE) changes library
management in a way "not fully backward compatible with Engine OS v4.3.4 and
older": artwork may not show on those players (desktop guide p. 4). A stick
exported from Engine DJ 5 for a venue running old firmware is worth a warning,
not a failure.

**Drive notes.** An Engine database needs about 500 MB free or more; for an
Engine drive Denon recommends exFAT, "but FAT32 is also supported"
(desktop guide p. 18).

## Not documented

None of these appear in any of the guides, so Gig prep shouldn't claim them
either way. The stick check should say "not stated by Denon" rather than pass
or fail:

- MP3 and AAC sample rates (the table gives only bit rates for MP3).
- A folder depth limit, a file-name length limit, or a track count limit.
- Partition scheme (MBR or GPT) and whether more than one partition works.
- An artwork size limit.
- Whether the players read `.m3u` playlists on a stick (only Engine
  playlists are described).
- How Engine places rekordbox cue times on MP3s, i.e. whether the 25 ms MP3
  encoder delay question that applies to rekordbox applies here too.

## What this means for Gig prep

1. **Profiles.** Add the Denon players to `sortero/gig/profiles.py`, each with
   its guide and page, as the Pioneer ones are: SC6000 / SC6000M (exFAT,
   FAT32, HFS+), SC5000 / SC5000M, PRIME 4 / 4+ / 4 G2 / 2 / GO / GO+, and
   SC LIVE 4 / 2 (exFAT, FAT32). MP3 and AAC need a way to say "sample rate
   not stated" rather than a made-up range.
2. **Players menu.** Add *Any Denon player* (their intersection), and
   *Any Pioneer or Denon player*: FAT32 only (the one file system both
   families read) with Pioneer's format limits, which already sit inside
   Denon's. The default stays *Any Pioneer player* unless you say otherwise.
3. **The stick check** learns the Engine database the way it knows rekordbox's:
   an `Engine Library` folder means Denon players will show playlists; none
   means they'll browse it as folders, which still passes. exFAT passes for
   Denon-only profiles and fails for anything that includes Pioneer.
4. **A Denon how-to** next to the rekordbox one: Sortero's gig XML into Engine
   DJ (Import → Rekordbox → Select XML → Manually Select), then Sync Manager →
   Export to Drive. Because Engine DJ reads rekordbox XML, the XML Gig prep
   already makes serves both, so there is nothing new to write.
5. **Wording.** Gig prep can say "Pioneer and Denon players" once 1–3 land.
   Serato and Traktor are laptop software: the honest line is that the
   backup stick works in any DJ software on a laptop, as the README already
   says for Traktor.

Needs checking on real gear before release: a FAT32 stick written by Gig prep
browsing in Folder view on an SC6000, and Sortero's XML imported into Engine
DJ with its cues landing on the beat.

# Could Sortero write an Engine DJ library itself?

Short answer: yes, and it's a smaller job than the rekordbox writer was. An
Engine library is a plain (unencrypted) SQLite file whose schema is published
in full by an LGPL project, the per-track data is five small binary blobs with
known layouts, and the players draw their own detailed waveforms. Sortero
already has everything that goes into it: the plan from the gig XML, the audio
on the stick under `Contents/`, cue points and grids (cues.py), and three-band
waveform columns (waveform.py). The open questions are which schema to write
for which firmware, and how it behaves on a real player.

This follows on from [denon-engine-dj.md](denon-engine-dj.md), which covers
what the players read, and [writing-export-pdb.md](writing-export-pdb.md).
Researched 21 October 2026.

## Sources

| Source | What it gives | Licence |
|---|---|---|
| libdjinterop, github.com/xsco/libdjinterop (commit 597be49, 14 Oct 2026) | every Engine schema from 1.6.0 to 3.0.2 as exact DDL (`src/djinterop/engine/schema/`), and encoders for each blob (`src/djinterop/engine/v2/*_blob.cpp`). Mixxx's "Export to Engine DJ" is built on it. | LGPL-3.0 |
| rb2engine 0.5.0 (PyPI, 1 Aug 2026) | a Python converter from a rekordbox USB export to an Engine library on the same stick; blob codecs checked byte-for-byte against databases Engine wrote | MIT |
| PenBridge, github.com/athousanddetails/penbridge | a macOS app doing the same; maps schemas to firmware | MIT |
| Engine DJ KB article 69000834165, "Support for Third-Party Database Tools" | Denon's own rules for third-party tools | — |
| Mixxx wiki, "Engine library format" | the old 1.x layout only (m.db + p.db); out of date | — |

Sortero is GPL-3.0-or-later, so porting from any of these is fine, as
devicesql.py was ported from openboxxx.

## What's on the stick

```
Engine Library/
  Database2/m.db        the library: tracks, playlists, performance data
  Database2/hm.db ...   history and others, which Engine creates; leave alone
Contents/Artist/Album/file.mp3   the audio (Sortero's rekordbox layout already)
```

`Track.path` is relative to the `Engine Library` folder, so a track Sortero
puts at `/Contents/A/B/x.mp3` is `../Contents/A/B/x.mp3`. That means **one
stick can carry both libraries over one copy of the audio**: export.pdb and
OneLibrary for Pioneer, m.db for Denon. rb2engine and PenBridge both do
exactly this, which is some evidence the players accept it.

Schema 1.x (`Engine Library/m.db` and `p.db` side by side) is gone; Engine OS
4.0 stopped reading it. Ignore it.

## Which schema

From libdjinterop's `to_application_version_string` and PenBridge's table:

| Schema | Written by | Players that read it |
|---|---|---|
| 2.21.0 | Engine DJ 3.1–3.4 | Engine OS 3.1 to 5.x (as a legacy format) |
| 3.0.0 | Engine DJ 4.1–4.2.1 | 4.1+ |
| 3.0.1 | Engine DJ 4.3.x | 4.3+ |
| 3.0.2 | Engine DJ 4.5 / 5.0.x | 4.5+ (adds `Track.albumArtSourceHash`) |

Denon's article says Engine refuses a database whose schema differs from what
it expects, so the DDL has to be copied exactly, triggers and views included,
not written from a description. libdjinterop's `create()` for each version is
that copy. libdjinterop says it's tested up to Engine OS 4.3.3; 3.0.2 against
Engine 5 is PenBridge's claim, not a tested one.

**Recommendation:** write 3.0.2 by default (current firmware, which the
desktop guide says is what Engine DJ 5 writes), and offer 2.21.0 in the
Players menu as "older Denon firmware". Never upgrade or touch an m.db Sortero
didn't write: Engine migrates 3.0.1 to 3.0.2 itself and has no way back.

## The tables Sortero would fill

Schema 3.0.x, from libdjinterop's DDL:

- **Information**: one row: a random `uuid` (the library's identity; keep it
  across rewrites so the player's history stays attached, as rb2engine does),
  the schema version, `currentPlayedIndiciator` (sic; libdjinterop uses a
  random int64, its meaning is unknown).
- **Track**: title, artist, album, genre, comment, label, composer, remixer,
  year, `length` (seconds), `bpm` (integer) and `bpmAnalyzed` (real), `key`
  (0–23, see below), `rating`, `path`, `filename`, `fileType`, `fileBytes`,
  `bitrate`, `dateAdded`, `isAnalyzed`, `isAvailable`, `albumArtId`, and
  `originDatabaseUuid` + `originTrackId` (set them to this library's uuid and
  the track's own id; triggers fix them up otherwise). Unique on `path`.
- **PerformanceData**: one row per track, **created by a trigger** when the
  Track row goes in; Sortero then UPDATEs its blobs. In 2.x it's a view over
  Track columns instead, so the two need different writers.
- **Playlist**: `title`, `parentListId` (0 for the top), `nextListId`,
  `isPersisted` = 1, `isExplicitlyExported` = 1. Siblings are a **linked
  list**: each playlist names the one after it, the last names 0, and triggers
  rewrite `nextListId` on insert. Insert siblings last-first, each with
  `nextListId` = the one inserted before it, and the triggers stay out of the
  way. Titles must be unique within a parent (Sortero has to suffix duplicates,
  as rb2engine does). There's no separate folder type: a playlist with children
  is a folder.
- **PlaylistEntity**: `listId`, `trackId`, `databaseUuid` (this library's),
  `nextEntityId` (again a linked list, 0 at the end), `membershipReference` 0.
  Unique on (list, track): **a track can be in a playlist only once**, so a set
  that repeats a track loses the repeat. Gig prep should say so.
- **AlbumArt**: row 1 is the "no art" row the schema inserts. libdjinterop
  doesn't write artwork; see open questions.

Smartlist, Pack, PreparelistEntity: leave empty.

## The performance data blobs

From libdjinterop's encoders. "Compressed" means Qt's qCompress: a 4-byte
big-endian uncompressed length, then zlib. Positions are in **samples** at the
track's sample rate, not seconds, so Sortero needs each track's sample rate
and sample count (probe.py / ffmpeg already has both).

| Blob | Layout | Compressed |
|---|---|---|
| `trackData` | sample rate (f64 BE), samples (i64 BE), key (i32 BE), average loudness low, mid, high (3 × f64 BE) | yes |
| `beatData` | sample rate (f64 BE), samples (f64 BE), "set" (u8 = 1), then a *default* and an *adjusted* grid, each a count (i64 **BE**) and markers of offset (f64 **LE**), beat number (i64 LE), beats to next marker (i32 LE), unknown (i32 LE) | yes |
| `quickCues` | count (i64 BE, always 8), then per pad: label length (u8, 0 = empty), label, offset (f64 BE, -1 = empty), colour A R G B; then main cue (f64 BE), "main cue adjusted" (u8), default main cue (f64 BE) | yes |
| `loops` | count (i64 **LE**, 8), then per loop: label length, label, start, end (f64 LE), start set, end set (u8), colour ARGB | **no** |
| `overviewWaveFormData` | points (i64 BE) twice, samples per point (f64 BE), then each point's low, mid, high (3 × u8), then the maximum point (3 × u8) | yes |

The mixed byte order within one blob is real, not a typo: libdjinterop and
rb2engine both encode it that way and round-trip Engine's own files.

There's no high-resolution waveform column in schema 2.x/3.x any more: Engine
OS draws the scrolling waveform itself when a track loads. The overview is
what shows on the track list and the strip, and Sortero's waveform columns
(lows, mids, highs per 1/150 s) resample straight into it.

Grid markers follow libdjinterop's convention: the first marker sits at beat
-4 (four beats before the first downbeat, extrapolated) and the last past the
final beat, so one constant-tempo grid is two markers. A rekordbox XML with
several TEMPO entries becomes one marker per tempo change.

**Key** is libdjinterop's `musical_key` order, round the circle of fifths:
0 C, 1 Am, 2 G, 3 Em, 4 D, 5 Bm, 6 A, 7 F♯m, 8 E, 9 D♭m, 10 B, 11 A♭m,
12 F♯, 13 E♭m, 14 D♭, 15 B♭m, 16 A♭, 17 Fm, 18 E♭, 19 Cm, 20 B♭, 21 Gm,
22 F, 23 Dm. Sortero's keys are in tags in whatever notation; it needs a
mapping from those (and from Camelot/Open Key) to these numbers.

## Rules Denon sets

From the KB article, which is the only thing Denon publishes:

- Don't change the schema of any database in `Database2`; Engine refuses it.
- Don't write while Engine DJ desktop has the library open (corruption).
- Third-party data goes in a separate database in a **subfolder** of
  `Database2`, attaching `m.db` read-only. That's where Sortero's marker
  belongs, rather than a file of its own invention at the root.
- A track's stable identity is `Track.(originDatabaseUuid, originTrackId)`; a
  playlist's is its path of titles from the root.

## How it would fit into Sortero

Mirroring the rekordbox writer:

1. **`sortero/gig/engine.py`**: `build(path, tracks, playlists, schema)`
   taking the same `devicesql.Track` / `Playlist` lists usbexport.plan()
   makes, writing to a temp file and `os.replace`-ing it in (as onelibrary.py
   does). The DDL for 3.0.2 and 2.21.0 transcribed from libdjinterop, nothing
   hand-written. Python's own `sqlite3` is enough; no new dependency.
2. **Blob encoders** next to it, each tested by round-trip against blobs from
   a real Engine-written m.db kept in `tests/fixtures`, the way
   `rekordcrate-demo` backs the pdb tests. rb2engine's tests are a model.
3. **usbexport.write()** gains the Engine library as a third output when the
   chosen profile `reads_engine`, reusing the audio it already copied and the
   waveform columns it already computed. `foreign()` learns to refuse an
   `Engine Library` Sortero didn't write.
4. **Times to samples**: cues and grids are seconds in Sortero; multiply by
   the track's real sample rate. Memory cues have nowhere to go except spare
   hot-cue pads (rb2engine fills pads beyond the hot cues with them); more
   than 8 are dropped and reported.
5. **The stick check** (verify.py) reads m.db back and checks every track's
   path resolves, the same way it checks export.pdb.
6. **Gig prep wording**: Denon players stop needing Engine DJ on the
   computer; the Engine DJ how-to (rbxml.engine_how_to) stays as the fallback.

Rough size: the rekordbox writer is devicesql + anlz + onelibrary + waveform,
about 1,000 lines. An Engine writer is DDL (long but transcribed), five
encoders of 20–40 lines each, and a build function like onelibrary.build:
perhaps 500 lines plus tests, with no audio analysis work left to do.

## Open questions, to answer on real gear

- Does an SC6000 / PRIME 4 on Engine OS 4.x and 5.x accept a 3.0.2 m.db that
  Engine DJ desktop didn't write, and does Engine DJ desktop open it?
- Do cues placed from rekordbox times land on the beat on MP3s, or is there an
  encoder-delay offset as with some rekordbox/Serato conversions?
- **Artwork** in Engine 5: the 3.0.2 schema adds `albumArtSourceHash`, the
  desktop guide warns artwork may not show on firmware 4.3.4 and older, and
  rb2engine mentions an `Engine Library/Artwork/` folder. It isn't known
  whether art now lives in files or still in AlbumArt blobs. Ship without art
  first.
- What `currentPlayedIndiciator`, the loudness doubles in trackData and the
  unknown grid marker field must be for the player to be happy (libdjinterop
  writes a random number, zeroes and zeroes, and works on 4.3.3).
- Whether the players need hm.db and the other Database2 files present, or
  create them.
- **A cheaper first test**: Denon players have imported rekordbox sticks on
  the player since SC5000 firmware 1.0.3 (2017; forum reports on SC6000 are
  mixed, and the v5 guides don't mention it). If a stick Sortero already
  writes is picked up that way, Denon support works today for what export.pdb
  carries, and an Engine writer becomes an improvement rather than a
  requirement. Worth trying before writing any code.
  One first-hand report (October 2026): a rekordbox-written stick played on a
  Denon touch-screen player with Layer A/B buttons, most likely an SC5000
  (the SC6000 has no layers). Firmware unknown, and it's not yet known what
  came across (playlists, cues, grids) or whether a Sortero-written stick
  does the same.

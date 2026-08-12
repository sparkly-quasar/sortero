# Sortero

A macOS app for getting a DJ collection under control: one canonical copy of
every track, playlists that preserve your curation, clean tags, and an intake
lane for new music.

![tabs: Overview, Organise, Tags, Duplicates, Import, History](build/icon_1024.png)

## The idea

The consensus among working DJs is that the *filesystem* should be shallow and
predictable, and the *software* should do the organising — crates and playlists
reference a track, they don't copy it. Sortero applies that:

- **One file per track**, at `Tracks/<Genre>/Artist - Title.ext`
- **Playlists as pointers.** Every folder you have today — Spotify/Tidal vibe
  imports, gig sets — becomes an `.m3u8` in `_Playlists/` pointing at that one
  file. A track curated into five vibes is five playlist entries and one file.
- **Key, BPM and energy live in tags**, where rekordbox and Mixxx can sort them.
- **Nothing is deleted.** Extra copies go to `_Quarantine/`. Every operation is
  journalled and reversible from the History tab.

## Layout it produces

```
DJ Collection/
  Tracks/<Genre>/Artist - Title.ext    canonical home for every track
  Sets/<Set Name>/                     optional: gig folders kept as folders
  Albums/<Album>/                      full releases, left intact
  Mixes/                               recordings over 20 minutes
  _Playlists/*.m3u8                    every folder you had, as a playlist
  _Quarantine/                         duplicates and rejects, never deleted
  To Be Processed/                     your Platinum Notes / Mixed In Key stage
  Processed/                           analysed and ready to file
```

`To Be Processed` and `Processed` are never reorganised.

## Tabs

| Tab | What it does |
|---|---|
| **Overview** | Track count, size, and what share has key+BPM, genre and energy. Lists what needs attention. |
| **Organise** | Previews every move before anything happens. Collapses duplicates, writes playlists, files tracks by genre. |
| **Tags** | Strips download-site spam from Genre/Comment, infers missing Artist/Title from filenames, normalises Genre, and promotes Mixed In Key energy into the sortable Grouping field. |
| **Duplicates** | Exact (identical audio) and Likely (same artist/title/version, same length). Different remixes are never grouped. Extras move to `_Quarantine`. |
| **Import** | Add files or folders. Analysed tracks go straight to `Tracks/<Genre>`; anything missing key/BPM lands in `To Be Processed`. Tracks already in the library are flagged, not copied. **"Sort the 'Processed' folder"** files everything you've already run through PN and MIK. |
| **History** | Every operation, with one-click undo. |

## Why energy ends up in Grouping

Mixed In Key writes `Cm - Energy 6` into the **comment** field. The key half
usually also reaches `TKEY`, but the energy half is stranded somewhere no DJ
software can sort on. Sortero copies it to **Grouping** as `5A - Energy 6`
(Camelot + energy), which rekordbox and Mixxx expose as a sortable column.

## Running it

Build the app, then open `Sortero.app` and point it at your collection.

### Building from source

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -U pip mutagen pyinstaller
./build/build_app.sh          # -> build/dist/Sortero.app
```

Requires Python 3.12 with Tk (`brew install python-tk@3.12`).

### Running without building

```bash
./.venv/bin/python run.py
```

## Safety

- Dry-run previews on every destructive tab; nothing moves until you confirm.
- Journals in `~/Library/Application Support/Sortero/journals/`.
- Duplicate removal is quarantine-only — Sortero never calls `unlink` on your music.
- Back up before the first big reorganisation anyway.

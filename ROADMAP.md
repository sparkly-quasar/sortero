# Roadmap

Features planned for Sortero but not built yet. Nothing here has a date.

## After the gig: bring the stick's changes home

Plug the main stick back in after a gig, and Sortero picks up what happened on
the players:

- **History.** The tracks you played, in order, as a playlist named for the
  gig (`History 2026-10-19 (Gig name)`) in `_Playlists/`.
- **Track changes.** Hot cues and memory cues you set on the player, and beat
  grids you adjusted, copied back to the track in your collection.

Then all of it goes back to rekordbox as a rekordbox XML you import, the same
way the set reached the stick. Sortero can't write rekordbox's own library,
which is encrypted, so the XML is the way in. Mixxx and the track's own tags
get the cue changes too.

This is only needed for a stick Sortero wrote. When rekordbox wrote the stick,
rekordbox already imports its history when you plug it back in.

**What's in place.** Sortero already reads the history tables in
`export.pdb`, and the OneLibrary it writes has history tables too. Since
Sortero wrote the stick, it knows which track in your collection each one on
the stick is. It already writes rekordbox XML for Gig prep.

**What's still unknown:**

- Which players save history, cues and grid changes to the stick, and where:
  `export.pdb`, the analysis files, or OneLibrary.
- Whether rekordbox's XML import replaces cues on a track already in its
  collection, or only adds new tracks.
- Why a stick Sortero wrote came back from an XDJ-RX2 with no history. The
  player opened it twice and rewrote `export.pdb`, but left the history tables
  and the analysis files empty. Either nothing was played long enough to count,
  or the player won't record history on Sortero's stick (a rekordbox export
  fills three tables that Sortero leaves empty). The second would be a bug in
  the stick writer, to fix before this.

**To find out,** play 3 or 4 tracks for over a minute each and set one hot cue,
then compare the stick before and after: once on a stick Sortero wrote, once on
one rekordbox wrote, on an older player and a CDJ-3000 or newer.

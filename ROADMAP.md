# Roadmap

Features planned for Sortero but not built yet. Nothing here has a date.

## Ongoing: easier to use, smoother to work with

Not one feature but steady work in every release: clearer screens and wording,
fewer steps between your music and a gig-ready stick, and less to keep track of
along the way.

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
- Whether players record history and cue changes on a stick Sortero wrote the
  same way they do on one rekordbox wrote. If they don't, the stick writer gets
  fixed first.

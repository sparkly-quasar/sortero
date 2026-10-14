# Could Sortero write a rekordbox USB export itself?

Short answer: possible in principle, not worth the risk now. Gig prep goes
through rekordbox instead (the rekordbox XML, then rekordbox's own export), and
the stick check reads what rekordbox wrote.

## What a player-ready stick holds

- `PIONEER/rekordbox/export.pdb`, the **Device Library**: a DeviceSQL database
  every player up to the CDJ-3000 reads. Sortero already reads it, with a
  parser generated from Deep Symmetry's crate-digger description
  (`sortero/vendor/rekordbox_pdb.py`).
- `PIONEER/rekordbox/exportLibrary.db`, **OneLibrary** (formerly Device Library
  Plus): an SQLite database encrypted with SQLCipher. The newest players
  (CDJ-3000X, OPUS-QUAD, OMNIS-DUO, XDJ-AZ) read only this, per AlphaTheta's
  USB notice of March 2026.
- `PIONEER/USBANLZ/.../ANLZ0000.DAT`, `.EXT` (and `.2EX` on newer exports): per
  track analysis files holding waveforms, the beat grid, cue points and phrase
  data. Without them a player loads the track but shows no waveform, grid or
  cues, which takes away most of the point of a rekordbox stick.

## Risks of writing them

1. **The formats are reverse-engineered.** crate-digger documents export.pdb
   well enough to read it, but several header and page fields are still
   unnamed or only partly understood. Reading tolerates that; writing has to get
   every field right, and the failure mode is a player that rejects the stick,
   shows a partial library or crashes mid-set. That's the failure Gig prep
   exists to prevent.
2. **OneLibrary is encrypted.** Writing it means using a key taken from
   rekordbox. That's a legal and ethical line (circumventing a vendor's
   encryption) Sortero shouldn't cross, and the key can change in any update.
   Without OneLibrary, the newest players wouldn't see the playlists at all.
3. **ANLZ files need real audio analysis.** Waveforms and beat grids in
   Pioneer's formats would have to be computed, not just copied. Sortero has
   cue points and grids from rekordbox, Mixxx and Mixed In Key, but waveforms
   are another project.
4. **Firmware moves.** AlphaTheta changed which library the CDJ-3000 reads
   first in firmware 3.30. A home-made writer would have to keep up with every
   firmware and rekordbox release, with no documentation and no way to test
   without the hardware.
5. **Testing.** Proving a written stick works means trying it on real players,
   across generations. The reading side can be tested against real exports;
   the writing side can't be tested here at all.

## If it's ever revisited

- Start with export.pdb only, for older players, and keep rekordbox's export as
  the route for anything newer.
- Round-trip every write through the existing reader, and compare against a
  rekordbox export of the same playlists, field by field.
- Test on real hardware (CDJ-2000, NXS2, CDJ-3000 at least) before anyone takes
  such a stick to a gig.
- Check whether an open-source writer already exists and is used in practice
  before starting one; at the time of writing, none was found that this note
  could vouch for.

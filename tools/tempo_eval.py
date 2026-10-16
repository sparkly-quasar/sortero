#!/usr/bin/env python3
"""Score Fix BPMs against tracks whose true tempo you know, before and after a change.

    python tools/tempo_eval.py tracks.csv

tracks.csv has a header and one row per track:

    path,genre,tag,true_bpm
    /Music/Techno/Artist - Title.mp3,Techno,93.33,140
    /Music/House/Other - Track.flac,House,124,124

`genre` is one of bpmfix.RANGES (or "lo-hi", e.g. 115-160), `tag` is the BPM
the file says, `true_bpm` the tempo you'd count. Include tracks that are
right as they are, and slow tracks filed under a fast genre: what matters most
is the fixes that would be wrong.

Each track is judged twice, by the rule as it was before the spectral-line and
steady-tempo checks ("before") and as it is now ("after"), and every outcome is
counted:

  fixed      the tag was wrong and the fix lands within 1.5% of the true tempo
  broke      a fix was made and it's wrong
  missed     the tag was wrong and was left alone
  left       the tag was right and was left alone

The idea comes from rbxport's analysis "golden playlist": judge a new rule by
what it fixes and what it breaks, on the same tracks every time. Nothing is
written to any file.
"""
import argparse, csv, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sortero import bpmfix, tempo  # noqa: E402

CLOSE = 0.015


def bounds(genre):
    if genre in bpmfix.RANGES:
        return bpmfix.RANGES[genre]
    lo, _, hi = genre.partition("-")
    return float(lo), float(hi)


def before(prof, bpm, lo, hi):
    """judge() without the checks this tool was written to measure."""
    ratio, steady = bpmfix.LINE_RATIO, bpmfix.steady
    bpmfix.LINE_RATIO, bpmfix.steady = 0.0, lambda *a: True
    try:
        return bpmfix.judge(prof, bpm, lo, hi)
    finally:
        bpmfix.LINE_RATIO, bpmfix.steady = ratio, steady


def outcome(tag, truth, k):
    wrong = abs(tag - truth) > truth * CLOSE
    if k is None:
        return "missed" if wrong else "left"
    return "fixed" if abs(tag * k - truth) <= truth * CLOSE else "broke"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv")
    args = ap.parse_args()
    counts = {"before": Counter(), "after": Counter()}
    with open(args.csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tag, truth = float(row["tag"]), float(row["true_bpm"])
            lo, hi = bounds(row["genre"])
            try:
                prof = tempo.profile(row["path"])
            except (tempo.DecodeError, OSError) as e:
                print(f"skip  {row['path']}: {e}")
                continue
            a, b = before(prof, tag, lo, hi), bpmfix.judge(prof, tag, lo, hi)
            oa, ob = outcome(tag, truth, a), outcome(tag, truth, b)
            counts["before"][oa] += 1
            counts["after"][ob] += 1
            mark = "   " if oa == ob else ">> "
            print(f"{mark}{os.path.basename(row['path'])}: tag {tag:g}, true {truth:g}, "
                  f"before {oa}, after {ob}")
    print()
    for name, c in counts.items():
        print(f"{name:6s}  fixed {c['fixed']}  broke {c['broke']}  missed {c['missed']}  "
              f"left {c['left']}")


if __name__ == "__main__":
    main()

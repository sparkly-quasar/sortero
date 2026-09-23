"""A gentle reminder to buy a Supporter licence, every hundred tracks sorted.

Nothing is ever held back or slowed down: without a licence Sortero only
counts the tracks it has moved or retagged, and after each hundred shows a
card that can be dismissed. Licence holders never see it.
"""
import os

from . import licence, settings
from .common import AUDIO_EXTS

EVERY = 100


def tracks_in(entries):
    """How many distinct tracks a journal moved or retagged."""
    moved = {e["dst"] for e in entries if e.get("op") == "move"}
    tagged = {e["path"] for e in entries if e.get("op") == "tag"} - moved
    return sum(1 for p in moved | tagged if os.path.splitext(p)[1].lower() in AUDIO_EXTS)


def count(entries):
    n = tracks_in(entries)
    if n:
        settings.set("tracks_sorted", int(settings.get("tracks_sorted") or 0) + n)


def due():
    """The number of tracks sorted if a reminder is owed, else 0."""
    total = int(settings.get("tracks_sorted") or 0)
    shown = int(settings.get("supporter_reminded_at") or 0)
    if total // EVERY <= shown // EVERY or licence.status().pro:
        return 0
    return total


def dismiss():
    settings.set("supporter_reminded_at", int(settings.get("tracks_sorted") or 0))

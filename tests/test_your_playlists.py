"""Your playlists: sorting the list by a column heading."""
import unittest

from sortero.gig import sources as S

try:
    from sortero.screens import your_playlists as Y
except ImportError:                 # no Tk on this Python
    Y = None


def row(kind, name, n, missing=0, path="", ref=()):
    return (S.Source(kind, path or f"/m/{name}.m3u8", name, ref), n, missing)


@unittest.skipIf(Y is None, "Tk isn't available")
class Sorting(unittest.TestCase):
    def setUp(self):
        self.rows = [row(S.SORTERO, "Warmup", 4, path="/c/_Playlists"),
                     row(S.M3U, "peak 10", 12, 1),
                     row(S.SORTERO, "Peak 9", 4, 3, path="/c/_Playlists"),
                     row(S.FOLDER, "Gone", None),
                     row(S.MIXXX, "afterparty", 7, ref=("playlist", 1))]

    def names(self, col, desc=False):
        return [r[0].name for r in Y.sort_rows(self.rows, col, desc, "/c")]

    def test_found_order_until_a_heading_is_clicked(self):
        self.assertEqual(self.names(None), ["Warmup", "peak 10", "Peak 9", "Gone",
                                            "afterparty"])

    def test_by_name_ignoring_case_with_numbers_in_order(self):
        self.assertEqual(self.names("name"), ["afterparty", "Gone", "Peak 9", "peak 10",
                                              "Warmup"])
        self.assertEqual(self.names("name", True), ["Warmup", "peak 10", "Peak 9", "Gone",
                                                    "afterparty"])

    def test_by_tracks_with_unreadable_last_either_way(self):
        self.assertEqual(self.names("n"), ["Peak 9", "Warmup", "afterparty", "peak 10",
                                           "Gone"])
        self.assertEqual(self.names("n", True), ["peak 10", "afterparty", "Peak 9",
                                                 "Warmup", "Gone"])

    def test_by_missing_ties_by_name(self):
        self.assertEqual(self.names("missing", True)[:2], ["Peak 9", "peak 10"])
        self.assertEqual(self.names("missing")[:3], ["afterparty", "Gone", "Warmup"])

    def test_by_where_it_comes_from(self):
        self.assertEqual(self.names("kind"), ["Gone", "afterparty", "peak 10", "Peak 9",
                                              "Warmup"])

    def test_by_where_it_lives(self):
        # "/m/…", then "_Playlists", then "Mixxx playlist"
        self.assertEqual(self.names("where"), ["Gone", "peak 10", "Peak 9", "Warmup",
                                               "afterparty"])

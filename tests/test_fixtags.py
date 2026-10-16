"""Clean tags: the artist's name repeated at the start of the title."""
import unicodedata, unittest

from sortero import fixtags, settings
from sortero.library import Rec


def rec(artist, title, **kw):
    return Rec(path=f"/m/Techno/{artist} - {title}.mp3", rel=f"Techno/{artist} - {title}.mp3",
               artist=artist, title=title, **kw)


class RepeatedArtist(unittest.TestCase):
    def test_taken_off_the_front(self):
        self.assertEqual(fixtags.repeats_artist(rec("Nora En Pure", "Nora En Pure - Sphinx")),
                         "Sphinx")

    def test_case_accents_spacing_and_dashes_dont_matter(self):
        self.assertEqual(fixtags.repeats_artist(
            rec("Röyksopp", "ROYKSOPP  – What Else Is There? (Trentemøller Remix)")),
            "What Else Is There? (Trentemøller Remix)")

    def test_only_the_first_dash_splits(self):
        self.assertEqual(fixtags.repeats_artist(
            rec("Nora En Pure", "Nora En Pure - Sphinx - Extended Mix")),
            "Sphinx - Extended Mix")

    def test_a_remix_after_the_dash_is_left_alone(self):
        for artist, title in (("Andy Bach", "Ipanema - Extended Mix"),
                              ("Paul & Shark, CINTHIE", "Blue Doves - CINTHIE Remix"),
                              ("Nora En Pure", "Nora En Pure"),
                              ("Nora En Pure", "Nora En Pure - ")):
            with self.subTest(title=title):
                self.assertIsNone(fixtags.repeats_artist(rec(artist, title)))

    def test_names_read_off_the_filename_dont_count(self):
        r = rec("Nora En Pure", "Nora En Pure - Sphinx", artist_from_name=True)
        self.assertIsNone(fixtags.repeats_artist(r))

    def test_planned_as_a_title_change(self):
        r = rec("Nora En Pure", "Nora En Pure - Sphinx")
        self.assertEqual(fixtags.plan([r], {"repeat"}),
                         [(r, {"title": ("Nora En Pure - Sphinx", "Sphinx")})])
        self.assertEqual(fixtags.plan([r], {"spam"}), [])

    def test_staging_folders_are_left_alone(self):
        r = rec("Nora En Pure", "Nora En Pure - Sphinx", protected=True)
        self.assertEqual(fixtags.plan([r], {"repeat"}), [])


class Spelling(unittest.TestCase):
    def test_the_accented_spelling_wins_even_when_rarer(self):
        recs = [rec("Christopher Schwarzwalder", "A"), rec("Christopher Schwarzwalder", "B"),
                rec("Christopher Schwarzwalder", "C"), rec("Christopher Schwarzwälder", "D")]
        ch = fixtags.plan(recs, {"spelling"})
        self.assertEqual([c["artist"] for _, c in ch],
                         [("Christopher Schwarzwalder", "Christopher Schwarzwälder")] * 3)

    def test_otherwise_the_commoner_spelling(self):
        recs = [rec("Rene Wise", "A"), rec("Rene Wise", "B"), rec("rene wise", "C")]
        self.assertEqual(fixtags.plan(recs, {"spelling"}),
                         [(recs[2], {"artist": ("rene wise", "Rene Wise")})])

    def test_each_name_in_a_list_on_its_own(self):
        recs = [rec("Röyksopp", "A"), rec("Royksopp & Robyn", "B"),
                rec("Robyn, Royksopp feat. Someone", "C")]
        got = [c["artist"][1] for _, c in fixtags.plan(recs, {"spelling"})]
        self.assertEqual(got, ["Röyksopp & Robyn", "Robyn, Röyksopp feat. Someone"])

    def test_punctuation_and_spacing(self):
        recs = [rec("Mr. Dello", "A"), rec("Mr. Dello", "B"), rec("Mr Dello", "C")]
        self.assertEqual(fixtags.plan(recs, {"spelling"})[0][1]["artist"][1], "Mr. Dello")

    def test_hidden_encoding_differences_are_tidied(self):
        nfd = unicodedata.normalize("NFD", "Ezaüf")
        recs = [rec("Ezaüf", "A"), rec(nfd, "B")]
        self.assertEqual(fixtags.plan(recs, {"spelling"}), [(recs[1], {"artist": (nfd, "Ezaüf")})])
        self.assertEqual(fixtags.spelled_differently(recs), [])

    def test_one_spelling_is_left_alone(self):
        recs = [rec("Nora En Pure", "A"), rec("Nora En Pure, Purple Disco Machine", "B")]
        self.assertEqual(fixtags.plan(recs, {"spelling"}), [])

    def test_brackets_around_a_credit_are_left_alone(self):
        recs = [rec("Save Your Atoll", "A"), rec("Save Your Atoll", "B"),
                rec("John Plaza (Remixes by Kontinum and Save Your Atoll)", "C"),
                rec("Mira (Berlin)", "D"), rec("mira (berlin)", "E"), rec("Mira (Berlin)", "F")]
        got = [c["artist"] for _, c in fixtags.plan(recs, {"spelling"})]
        self.assertEqual(got, [("mira (berlin)", "Mira (Berlin)")])

    def test_placeholders_are_left_alone(self):
        recs = [rec("Unknown", "A"), rec("Unknown", "B"), rec("[unknown]", "C"),
                rec("VA", "D"), rec("Various Artists", "E")]
        self.assertEqual(fixtags.plan(recs, {"spelling"}), [])

    def test_listed_for_to_do(self):
        recs = [rec("Rebūke", "A"), rec("Rebuke", "B"), rec("REBUKE", "C")]
        self.assertEqual(fixtags.spelled_differently(recs), [("Rebūke", ["REBUKE", "Rebuke"])])


class YourSpellings(unittest.TestCase):
    """What the user says beats what Sortero guesses."""

    def setUp(self):
        settings.set(fixtags.CHOICES, {})
        self.addCleanup(settings.set, fixtags.CHOICES, {})
        self.recs = [rec("Godo", "A"), rec("_GODO", "B"), rec("_GODO", "C"),
                     rec("_GODO", "E"), rec("Godo & Someone", "D")]

    def artists(self):
        return [c["artist"][1] for _, c in fixtags.plan(self.recs, {"spelling"})]

    def test_the_guess_by_default(self):
        self.assertEqual(self.artists(), ["_GODO", "_GODO & Someone"])

    def test_a_chosen_spelling_is_used_everywhere(self):
        fixtags.choose_spelling("godo", "Godo")
        self.assertEqual(self.artists(), ["Godo", "Godo", "Godo"])

    def test_a_spelling_of_your_own(self):
        fixtags.choose_spelling("godo", "  GODO ")
        self.assertEqual(self.artists(), ["GODO", "GODO", "GODO", "GODO", "GODO & Someone"])

    def test_left_alone_is_neither_changed_nor_flagged(self):
        fixtags.choose_spelling("godo", fixtags.LEAVE)
        self.assertEqual(self.artists(), [])
        self.assertEqual(fixtags.spelled_differently(self.recs), [])
        self.assertEqual(fixtags.spelling_review(self.recs)[0]["chosen"], fixtags.LEAVE)

    def test_back_to_the_guess(self):
        fixtags.choose_spelling("godo", "Godo")
        fixtags.choose_spelling("godo", None)
        self.assertEqual(fixtags.spelling_choices(), {})
        self.assertEqual(self.artists(), ["_GODO", "_GODO & Someone"])

    def test_review_lists_counts_guess_and_choice(self):
        fixtags.choose_spelling("godo", "Godo")
        row = fixtags.spelling_review(self.recs)[0]
        self.assertEqual((row["key"], row["guess"], row["chosen"]), ("godo", "_GODO", "Godo"))
        self.assertEqual(dict(row["spellings"]), {"_GODO": 3, "Godo": 2})
        self.assertEqual(fixtags.spelled_differently(self.recs), [("Godo", ["_GODO"])])


def filed(name, artist, title):
    """A track whose filename is `name`, tagged with this artist and title."""
    from sortero.library import Rec
    return Rec(path=f"/m/Sultry/{name}.mp3", rel=f"Sultry/{name}.mp3",
               artist=artist, title=title)


class CreditInTitle(unittest.TestCase):
    def test_the_four_kinds(self):
        cases = [
            (filed("Nora En Pure - Sphinx", "Nora En Pure", "Nora En Pure - Sphinx"),
             (fixtags.REPEAT, None, "Sphinx")),
            (filed("015. Djuma Soundsystem & Westerby - Koma Kobache_PN",
                   "Djuma Soundsystem", "Djuma Soundsystem & Westerby - Koma Kobache"),
             (fixtags.MORE, "Djuma Soundsystem & Westerby", "Koma Kobache")),
            (filed("025. John Digweed & Nick Muir - Mantis_PN", None,
                   "John Digweed & Nick Muir - Mantis"),
             (fixtags.MISSING, "John Digweed & Nick Muir", "Mantis")),
            (filed("040. Hraach - Una Mattina (Original Mix)_PN", "Dalal",
                   "Hraach - Una Mattina (Original Mix)"),
             (fixtags.OTHER, "Hraach", "Una Mattina (Original Mix)")),
        ]
        for r, want in cases:
            with self.subTest(title=r.title):
                self.assertEqual(fixtags.title_credit(r), want)

    def test_left_alone(self):
        for r in (
            # Spotify-style version after the dash: the filename names the artist
            filed("Andy Bach - Ipanema - Extended Mix", "Andy Bach", "Ipanema - Extended Mix"),
            # "Song - Artist": the tag is right
            filed("OH OHH - Deborah De Luca", "Deborah de Luca", "OH OHH - Deborah De Luca"),
            # a different artist with no filename to back it up
            filed("Track 01", "Dalal", "Hraach - Una Mattina"),
            # no dash at all
            filed("Satori - Umama", "Satori", "Umama (Riva Starr Remix)"),
        ):
            with self.subTest(title=r.title):
                self.assertIsNone(fixtags.title_credit(r))

    def test_the_fix_does_the_safe_ones_only(self):
        recs = [filed("Djuma Soundsystem & Westerby - Koma Kobache", "Djuma Soundsystem",
                      "Djuma Soundsystem & Westerby - Koma Kobache"),
                filed("John Digweed - Mantis", None, "John Digweed - Mantis"),
                filed("Hraach - Una Mattina", "Dalal", "Hraach - Una Mattina")]
        got = [c for _, c in fixtags.plan(recs, {"repeat"})]
        self.assertEqual(got, [
            {"title": ("Djuma Soundsystem & Westerby - Koma Kobache", "Koma Kobache"),
             "artist": ("Djuma Soundsystem", "Djuma Soundsystem & Westerby")},
            {"title": ("John Digweed - Mantis", "Mantis"),
             "artist": (None, "John Digweed")}])

    def test_with_the_filename_fix_too(self):
        r = filed("JOHN DIGWEED - Mantis", None, "John Digweed - Mantis")
        self.assertEqual(fixtags.plan([r], {"artist", "repeat"}),
                         [(r, {"artist": (None, "John Digweed"),
                               "title": ("John Digweed - Mantis", "Mantis")})])

    def test_left_alone_stops_being_flagged(self):
        settings.set(fixtags.LEFT_ALONE, [])
        self.addCleanup(fixtags.leave_title_artist, [], True)
        self.addCleanup(settings.set, fixtags.LEFT_ALONE, [])
        r = filed("Dj Dark & Mentol - Say It Right", "Nelly Furtado",
                  "Dj Dark & Mentol - Say It Right")
        self.assertTrue(fixtags.names_other_artist(r))
        fixtags.leave_title_artist([r])
        self.assertFalse(fixtags.names_other_artist(r))
        self.assertEqual(settings.get(fixtags.LEFT_ALONE), ["dj dark & mentol - say it right.mp3"])
        fixtags.leave_title_artist([r], on=False)
        self.assertTrue(fixtags.names_other_artist(r))

    def test_picked_tracks_take_the_credit_from_the_title(self):
        r = filed("Hraach - Una Mattina", "Dalal", "Hraach - Una Mattina")
        self.assertEqual(fixtags.credit_from_title([r]),
                         [(r, {"title": ("Hraach - Una Mattina", "Una Mattina"),
                               "artist": ("Dalal", "Hraach")})])
        self.assertTrue(fixtags.names_other_artist(r))
        self.assertFalse(fixtags.artist_in_title(r))


if __name__ == "__main__":
    unittest.main()

"""Clean tags: the artist's name repeated at the start of the title."""
import unicodedata, unittest

from sortero import fixtags
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


if __name__ == "__main__":
    unittest.main()

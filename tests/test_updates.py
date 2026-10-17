"""Which release counts as newer, and which one each update channel offers."""
import unittest
from unittest import mock

from sortero import updates as U


class Versions(unittest.TestCase):
    def test_order(self):
        newest_first = ["v1.0.0", "1.0.0-rc.1", "1.0.0-beta.10", "1.0.0-beta.2",
                        "1.0.0-beta", "1.0.0-alpha.3", "0.9.9", "0.9.0", "nonsense"]
        self.assertEqual(sorted(newest_first, key=U.parse, reverse=True), newest_first)

    def test_a_release_is_newer_than_its_betas(self):
        self.assertTrue(U.is_newer("v0.34.0", "0.34.0-beta.3"))
        self.assertFalse(U.is_newer("v0.34.0-beta.3", "0.34.0"))
        self.assertTrue(U.is_newer("v0.34.0-beta.1", "0.33.0"))
        self.assertFalse(U.is_newer("v0.33.1", "0.34.0-beta.1"))

    def test_is_prerelease(self):
        self.assertTrue(U.is_prerelease("v0.34.0-beta.1"))
        self.assertTrue(U.is_prerelease("0.34.0rc1"))
        self.assertFalse(U.is_prerelease("v0.34.0"))


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        import json
        return json.dumps(self.body).encode()


RELEASES = [{"tag_name": "v0.34.0-beta.2", "prerelease": True, "html_url": "b2"},
            {"tag_name": "v0.34.0-beta.3", "prerelease": True, "draft": True, "html_url": "d"},
            {"tag_name": "v0.33.0", "html_url": "r"}]


class Channels(unittest.TestCase):
    def check(self, include_beta, local, answer):
        asked = []

        def urlopen(req, timeout):
            asked.append(req.full_url)
            return Response(answer)
        with mock.patch.object(U.net, "urlopen", urlopen), \
                mock.patch.object(U, "__version__", local), \
                mock.patch.object(U.settings, "set"):
            res = U.check(token="", include_beta=include_beta)
        return res, asked[0]

    def test_stable_asks_for_the_latest_release(self):
        res, url = self.check(False, "0.33.0", RELEASES[2])
        self.assertTrue(url.endswith("/releases/latest"))
        self.assertEqual(res["state"], "up-to-date")

    def test_beta_offers_the_newest_published_beta(self):
        res, url = self.check(True, "0.33.0", RELEASES)
        self.assertIn("/releases?", url)
        self.assertEqual((res["state"], res["latest"]), ("update", "v0.34.0-beta.2"))
        self.assertIn("It's a beta", res["message"])

    def test_a_beta_tester_gets_the_final_release(self):
        final = [{"tag_name": "v0.34.0", "html_url": "f"}] + RELEASES
        res, _ = self.check(True, "0.34.0-beta.2", final)
        self.assertEqual((res["state"], res["latest"]), ("update", "v0.34.0"))
        self.assertNotIn("It's a beta", res["message"])


if __name__ == "__main__":
    unittest.main()

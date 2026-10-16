"""Undo journals are written all or nothing, so a crash can't cost a batch its undo."""
import json, os, unittest
from unittest import mock

from sortero import durable, journal
from tests.test_tagio import TempDir


class WriteJson(TempDir):
    def test_replaces_the_file_and_leaves_no_temp(self):
        p = self.path("j.json")
        durable.write_json(p, {"a": 1})
        durable.write_json(p, {"a": 2})
        with open(p) as fh:
            self.assertEqual(json.load(fh), {"a": 2})
        self.assertEqual(os.listdir(self.dir), ["j.json"])

    def test_a_failed_write_keeps_the_old_file(self):
        p = self.path("j.json")
        durable.write_json(p, {"a": 1})
        with mock.patch.object(durable, "flush", side_effect=OSError("cable pulled")):
            with self.assertRaises(OSError):
                durable.write_json(p, {"a": 2})
        with open(p) as fh:
            self.assertEqual(json.load(fh), {"a": 1})
        self.assertEqual(os.listdir(self.dir), ["j.json"])

    def test_flushes_before_renaming(self):
        order = []
        real_replace = os.replace
        with mock.patch.object(durable, "flush", side_effect=lambda fh: order.append("flush")), \
             mock.patch.object(durable.os, "replace",
                               side_effect=lambda a, b: (order.append("replace"),
                                                         real_replace(a, b))):
            durable.write_json(self.path("j.json"), {})
        self.assertEqual(order, ["flush", "replace"])


class JournalFiles(TempDir):
    def batch(self):
        j = journal.Journal("test", self.dir)
        j.created(self.path("x"))
        return j

    def test_save_and_lock_round_trip(self):
        p = self.batch().save()
        journal.lock(p, "the archive was deleted")
        with open(p) as fh:
            d = json.load(fh)
        self.assertEqual(d["locked"], "the archive was deleted")
        self.assertEqual(len(d["entries"]), 1)
        self.assertFalse(os.path.exists(p + ".tmp"))

    def test_a_failed_lock_leaves_the_batch_undoable(self):
        p = self.batch().save()
        with mock.patch.object(durable, "flush", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                journal.lock(p, "gone")
        with open(p) as fh:
            self.assertNotIn("locked", json.load(fh))


if __name__ == "__main__":
    unittest.main()

"""Formatting a stick: every rule that keeps the wrong disk from being erased."""
import plistlib, unittest
from unittest import mock

from sortero.gig import formatting as F

GB = 1000 ** 3


def stick(disk_id="disk4", name="GIG", size=32 * GB, media="SanDisk Ultra", **over):
    info = {"DeviceIdentifier": disk_id, "WholeDisk": True, "Internal": False,
            "RemovableMedia": True, "Ejectable": True, "VirtualOrPhysical": "Physical",
            "MediaName": media, "TotalSize": size, "BusProtocol": "USB"}
    info.update(over)
    listing = {"AllDisksAndPartitions": [{"DeviceIdentifier": disk_id, "Partitions": [
        {"DeviceIdentifier": f"{disk_id}s1", "VolumeName": name, "Content": "DOS_FAT_32"}]}]}
    return info, listing


class FakeDiskutil:
    """Answers diskutil the way a Mac with these disks would, and records erasures."""

    def __init__(self, disks, boot="disk1", folders=None):
        self.disks = dict(disks)            # disk id -> (info, listing)
        self.boot = boot
        self.folders = folders or {}        # folder -> the volume it's on (disk5s2)
        self.erased = []

    def device_of(self, path):
        return self.folders.get(path)

    def __call__(self, *args):
        # Like the real diskutil, -plist only works straight after the verb.
        if "-plist" in args[2:]:
            raise F.FormatError("Usage:  diskutil " + args[0])
        args = [a for a in args if a != "-plist"]
        if args[0] == "eraseDisk":
            self.erased.append(args)
            return b"Finished erase on disk4"
        if args[:3] == ["list", "external", "physical"]:
            whole = [d for d, (info, _) in self.disks.items() if not info.get("Internal")]
            return plistlib.dumps({"WholeDisks": whole})
        if args[0] == "list":
            return plistlib.dumps(self.disks[args[1]][1])
        if args[0] == "info":
            target = args[1]
            if target == "/":
                return plistlib.dumps({"ParentWholeDisk": "disk3",
                                       "APFSPhysicalStores": [
                                           {"APFSPhysicalStore": f"{self.boot}s2"}]})
            if target.startswith("/dev/"):
                return plistlib.dumps({"ParentWholeDisk": F.whole_disk(target[5:])})
            if target in self.disks:
                return plistlib.dumps(self.disks[target][0])
            # Like the real one: a folder isn't a disk or a mount point.
            raise F.FormatError(f"Could not find disk: {target}")
        raise AssertionError(args)


class Candidates(unittest.TestCase):
    def test_a_usb_stick_is_offered(self):
        fake = FakeDiskutil({"disk4": stick()})
        ok, refused = F.candidates([], run=fake)
        self.assertEqual([d.id for d in ok], ["disk4"])
        self.assertEqual(ok[0].name, "GIG")
        self.assertEqual(refused, [])

    def test_the_disk_holding_the_collection_is_never_offered(self):
        music = stick("disk5", "MUSIC", 2000 * GB, "WD Elements")
        fake = FakeDiskutil({"disk4": stick(), "disk5": music},
                            folders={"/Volumes/MUSIC/DJ Collection": "disk5s2"})
        with mock.patch.object(F, "device_of", fake.device_of):
            ok, refused = F.candidates(["/Volumes/MUSIC/DJ Collection"], run=fake,
                                       exists=lambda p: True)
        self.assertEqual([d.id for d in ok], ["disk4"])
        self.assertIn("collection", refused[0][1])

    def test_a_collection_in_a_folder_on_the_startup_disk(self):
        # Dropbox, iCloud or just ~/Music: diskutil can't take the folder itself, so
        # it's found by the volume it's on, which here is the startup disk.
        home = "/Users/dj/Library/CloudStorage/Dropbox/Music/DJ Collection"
        fake = FakeDiskutil({"disk4": stick()}, folders={home: "disk1s5"})
        with mock.patch.object(F, "device_of", fake.device_of):
            ok, refused = F.candidates([home], run=fake, exists=lambda p: True)
            self.assertEqual(F.physical_disks_of(home, run=fake), {"disk1"})
        self.assertEqual([d.id for d in ok], ["disk4"])

    def test_a_folder_on_a_network_share_holds_no_disk_here(self):
        fake = FakeDiskutil({"disk4": stick()})
        with mock.patch.object(F, "device_of", lambda p: None):
            ok, _ = F.candidates(["/Volumes/NAS/Music"], run=fake, exists=lambda p: True)
        self.assertEqual([d.id for d in ok], ["disk4"])

    def test_diskutils_plist_error_is_shown_as_its_message(self):
        out = plistlib.dumps({"Error": True, "ErrorMessage": "Could not find disk: /x"})
        self.assertEqual(F._why(out), "Could not find disk: /x")
        self.assertEqual(F._why(b"Usage: diskutil info\n"), "Usage: diskutil info")

    def test_an_ejected_music_drive_still_plugged_in_is_never_offered(self):
        # Ejected in Finder, cable still in: the folder isn't there, but the
        # disk is, still carrying its volume's name.
        music = stick("disk5", "MUSIC", 2000 * GB, "WD Elements")
        fake = FakeDiskutil({"disk4": stick(), "disk5": music})
        ok, refused = F.candidates(["/Volumes/MUSIC/DJ Collection"], run=fake,
                                   exists=lambda p: False)
        self.assertEqual([d.id for d in ok], ["disk4"])
        self.assertIn("collection", refused[0][1])

    def test_booted_from_an_external_disk(self):
        fake = FakeDiskutil({"disk4": stick(), "disk6": stick("disk6", "BOOT")}, boot="disk6")
        ok, refused = F.candidates([], run=fake)
        self.assertEqual([d.id for d in ok], ["disk4"])
        self.assertIn("startup", refused[0][1])

    def test_disk_images_and_fixed_disks_are_refused(self):
        fake = FakeDiskutil({"disk4": stick(VirtualOrPhysical="Virtual"),
                             "disk7": stick("disk7", "SSD", RemovableMedia=False,
                                            Ejectable=False, Removable=False)})
        ok, refused = F.candidates([], run=fake)
        self.assertEqual(ok, [])
        self.assertEqual(sorted(r for _, r in refused),
                         ["it isn't a physical disk (a disk image, say)", "it isn't removable"])

    def test_if_the_startup_disk_cant_be_found_nothing_is_offered(self):
        def broken(*args):
            if args[0] == "info" and args[-1] == "/":
                return plistlib.dumps({})
            return FakeDiskutil({"disk4": stick()})(*args)
        with self.assertRaises(F.FormatError):
            F.candidates([], run=broken)

    def test_efi_partition_names_are_not_the_stick_name(self):
        info, listing = stick()
        listing["AllDisksAndPartitions"][0]["Partitions"].insert(
            0, {"VolumeName": "EFI", "Content": "EFI"})
        self.assertEqual(F.disk_from(info, listing).name, "GIG")


class Erase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDiskutil({"disk4": stick()})
        [self.disk], _ = F.candidates([], run=self.fake)

    def test_runs_the_exact_command(self):
        F.erase(self.disk, "gig night", "GIG", [], run=self.fake)
        self.assertEqual(self.fake.erased,
                         [["eraseDisk", "FAT32", "GIG NIGHT", "MBRFormat", "/dev/disk4"]])

    def test_the_name_must_be_typed_exactly(self):
        for typed in ("", "gig", "GI", "disk4"):
            with self.assertRaises(F.FormatError):
                F.erase(self.disk, "GIG", typed, [], run=self.fake)
        self.assertEqual(self.fake.erased, [])

    def test_a_different_stick_under_the_same_number_is_not_erased(self):
        self.fake.disks["disk4"] = stick(media="Some Other Stick", size=64 * GB)
        with self.assertRaises(F.FormatError) as e:
            F.erase(self.disk, "GIG", "GIG", [], run=self.fake)
        self.assertIn("isn't the stick you chose", str(e.exception))
        self.assertEqual(self.fake.erased, [])

    def test_rules_are_checked_again_at_the_last_moment(self):
        # the collection was moved onto the stick after the list was drawn
        self.fake.folders["/Volumes/GIG/DJ Collection"] = "disk4s1"
        with self.assertRaises(F.FormatError), \
                mock.patch.object(F, "device_of", self.fake.device_of):
            F.erase(self.disk, "GIG", "GIG", ["/Volumes/GIG/DJ Collection"], run=self.fake,
                    exists=lambda p: True)
        self.assertEqual(self.fake.erased, [])

    def test_bad_names(self):
        for bad in ("", "A NAME TOO LONG", "GIG/2", "GIG:"):
            with self.assertRaises(F.FormatError):
                F.label(bad)
        self.assertEqual(F.label("  gig 1 "), "GIG 1")


class Lookups(unittest.TestCase):
    def test_whole_disk(self):
        self.assertEqual(F.whole_disk("disk4s1"), "disk4")
        self.assertEqual(F.whole_disk("disk12"), "disk12")
        self.assertIsNone(F.whole_disk(""))

    def test_apfs_folders_resolve_to_their_physical_disk(self):
        def run(*args):
            return plistlib.dumps({"ParentWholeDisk": "disk9",
                                   "APFSPhysicalStores": [{"APFSPhysicalStore": "disk5s2"}]})
        self.assertEqual(F.physical_disks_of("/Volumes/X", run=run), {"disk5"})

    def test_plist_comes_straight_after_the_verb(self):
        # diskutil info / -plist prints its usage; diskutil info -plist / works.
        seen = []
        def run(*args):
            seen.append(list(args))
            return plistlib.dumps({})
        F.plist("info", "/", run=run)
        F.plist("list", "external", "physical", run=run)
        self.assertEqual(seen, [["info", "-plist", "/"],
                                ["list", "-plist", "external", "physical"]])


if __name__ == "__main__":
    unittest.main()

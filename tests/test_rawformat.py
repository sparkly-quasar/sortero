"""Formatting on Linux and Windows: the FAT32 Sortero writes itself, and every rule that
keeps the wrong disk from being erased."""
import json, os, shutil, struct, subprocess, unittest

from sortero.gig import fat32, formatting as F, rawformat as R
from tests.test_tagio import TempDir

GB = 1000 ** 3
MB = 1024 ** 2
HAVE_FSCK = bool(shutil.which("fsck.fat"))


def image(path, size, label="GIG"):
    with open(path, "wb") as fh:
        fh.truncate(size)
    with open(path, "r+b") as fh:
        return fat32.write(fh, size, label, volume_id=0x1234ABCD, disk_id=0xCAFE, now=0)


class Layout(unittest.TestCase):
    def test_cluster_sizes_follow_windows(self):
        for size, kb in ((4 * GB, 4), (16 * GB, 8), (32 * GB, 16), (64 * GB, 32),
                         (256 * GB, 32), (1000 * GB, 32)):
            with self.subTest(size=size):
                self.assertEqual(fat32.plan(size).cluster_bytes, kb * 1024)

    def test_data_starts_on_a_cluster_boundary(self):
        for size in (64 * MB, 8 * GB, 128 * GB):
            lay = fat32.plan(size)
            self.assertEqual(lay.data_start % lay.per_cluster, 0)
            self.assertGreaterEqual(lay.fat_sectors * 512 // 4, lay.clusters + 2)

    def test_too_small_and_odd_sectors_are_refused(self):
        with self.assertRaises(fat32.Fat32Error):
            fat32.plan(16 * MB)
        with self.assertRaises(fat32.Fat32Error):
            fat32.plan(64 * GB, sector=4096)


class Image(TempDir):
    def test_structures(self):
        p = self.path("stick.img")
        lay = image(p, 64 * MB, "GIG")
        with open(p, "rb") as fh:
            data = fh.read(lay.part_start * 512 + lay.data_start * 512 + 512)
        mbr = data[:512]
        self.assertEqual(mbr[510:], b"\x55\xaa")
        self.assertEqual(mbr[446 + 4], 0x0C)
        self.assertEqual(struct.unpack_from("<II", mbr, 446 + 8), (2048, lay.part_sectors))
        base = 2048 * 512
        boot = data[base:base + 512]
        self.assertEqual(boot[82:90], b"FAT32   ")
        self.assertEqual(boot[71:82], b"GIG        ")
        self.assertEqual(data[base + 6 * 512:base + 7 * 512], boot)     # the backup
        info = data[base + 512:base + 1024]
        self.assertEqual(struct.unpack_from("<I", info, 0)[0], 0x41615252)
        self.assertEqual(struct.unpack_from("<I", info, 488)[0], lay.clusters - 1)
        root = data[base + lay.data_start * 512:]
        self.assertEqual(root[:11], b"GIG        ")
        self.assertEqual(root[11], 0x08)

    def test_old_partition_maps_are_cleared(self):
        p = self.path("stick.img")
        with open(p, "wb") as fh:
            fh.write(b"\xff" * (64 * MB))           # a disk full of old data
        with open(p, "r+b") as fh:
            lay = fat32.write(fh, 64 * MB, "GIG")
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(2048 * 512)[512:], bytes(2047 * 512))
            fh.seek(64 * MB - MB)
            self.assertEqual(fh.read(), bytes(MB))   # where a GPT keeps its backup
        self.assertGreater(lay.clusters, 0)

    def test_every_write_is_whole_sectors(self):
        class Strict:
            pos = 0

            def seek(s, n):
                self.assertEqual(n % 512, 0)
                s.pos = n

            def write(s, b):
                self.assertEqual(len(b) % 512, 0)

            def flush(s):
                pass

        fat32.write(Strict(), 64 * GB, "GIG")

    @unittest.skipUnless(HAVE_FSCK, "needs fsck.fat (dosfstools)")
    def test_fsck_finds_nothing_wrong(self):
        p = self.path("stick.img")
        lay = image(p, 300 * MB)
        part = self.path("part.img")
        with open(p, "rb") as fi, open(part, "wb") as fo:
            fi.seek(lay.part_start * 512)
            shutil.copyfileobj(fi, fo)
        r = subprocess.run(["fsck.fat", "-n", "-v", part], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("4096 bytes per cluster", r.stdout)


# ------------------------------------------------------------------- Linux
def node(name, size=32 * GB, tran="usb", rm=True, model="SanDisk Ultra", serial="S1",
         children=(), **over):
    n = {"name": name, "path": f"/dev/{name}", "type": "disk", "rm": rm, "hotplug": rm,
         "tran": tran, "size": size, "model": model, "serial": serial, "label": None,
         "mountpoints": [None], "ro": False, "log-sec": 512, "children": list(children)}
    n.update(over)
    return n


def part(name, label=None, mount=None):
    return {"name": name, "path": f"/dev/{name}", "type": "part", "label": label,
            "mountpoints": [mount], "rm": False, "tran": None, "size": 1}


class Result:
    def __init__(self, out=b"", code=0):
        self.stdout, self.stderr, self.returncode = out, b"", code


class FakeLinux:
    """Answers lsblk, findmnt and umount the way a Linux desktop with these disks would."""

    def __init__(self, disks, mounts=None, old_lsblk=False):
        self.disks = disks
        self.mounts = {"/": "/dev/nvme0n1p2", "/boot": "/dev/nvme0n1p1", **(mounts or {})}
        self.old = old_lsblk
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "lsblk":
            if self.old and "MOUNTPOINTS" in args[4]:
                return Result(code=1)
            if "-s" in args:
                dev = args[-1]
                for d in self.disks:
                    for p in R._walk(d):
                        if p["path"] == dev:
                            return Result(json.dumps({"blockdevices": [
                                {"name": p["name"], "type": p["type"], "children": [
                                    {"name": d["name"], "type": "disk"}]}]}).encode())
                return Result(code=32)
            return Result(json.dumps({"blockdevices": self.disks}).encode())
        if args[0] == "findmnt":
            target = args[-1]
            best = max((m for m in self.mounts if target == m or target.startswith(
                m.rstrip("/") + "/")), key=len, default=None)
            return Result((self.mounts[best] + "\n").encode()) if best else Result(code=1)
        if args[0] == "umount":
            return Result()
        raise AssertionError(args)


def laptop(*extra):
    system = node("nvme0n1", 512 * GB, tran="nvme", rm=False, model="Samsung SSD",
                  children=[part("nvme0n1p1", mount="/boot"), part("nvme0n1p2", mount="/")])
    return [system, *extra]


def exists_except(*missing):
    return lambda p: p not in missing


class LinuxCandidates(unittest.TestCase):
    def find(self, fake, keep=(), exists=lambda p: True):
        disks, k = R.linux_disks(R._lsblk(run=fake)), R.linux_protected(
            list(keep), run=fake, exists=exists)
        ok = [d for d in disks if not R.why_not(d, k)]
        return ok, k, disks

    def test_a_usb_stick_is_offered(self):
        fake = FakeLinux(laptop(node("sdb", children=[part("sdb1", "GIG", "/media/dj/GIG")])))
        ok, _, _ = self.find(fake)
        self.assertEqual([d.id for d in ok], ["sdb"])
        self.assertEqual(ok[0].name, "GIG")
        self.assertEqual(ok[0].device, "/dev/sdb")
        self.assertEqual(ok[0].mounted, [("/dev/sdb1", "/media/dj/GIG")])

    def test_the_system_disk_is_never_offered(self):
        ok, keep, disks = self.find(FakeLinux(laptop()))
        self.assertEqual(ok, [])
        self.assertIn("nvme0n1", keep.disks)

    def test_the_drive_holding_the_collection_is_never_offered(self):
        music = node("sdc", 2000 * GB, rm=False, model="WD Elements",
                     children=[part("sdc1", "MUSIC", "/media/dj/MUSIC")])
        fake = FakeLinux(laptop(node("sdb", children=[part("sdb1", "GIG")]), music),
                         mounts={"/media/dj/MUSIC": "/dev/sdc1"})
        ok, keep, disks = self.find(fake, ["/media/dj/MUSIC/DJ Collection"])
        self.assertEqual([d.id for d in ok], ["sdb"])
        sdc = next(d for d in disks if d.id == "sdc")
        self.assertIn("collection", R.why_not(sdc, keep))

    def test_an_unmounted_music_drive_still_plugged_in_is_never_offered(self):
        music = node("sdc", 2000 * GB, children=[part("sdc1", "MUSIC")])
        fake = FakeLinux(laptop(music))
        ok, _, _ = self.find(fake, ["/run/media/dj/MUSIC/DJ Collection"],
                             exists=exists_except("/run/media/dj/MUSIC/DJ Collection"))
        self.assertEqual(ok, [])

    def test_disk_images_internal_and_read_only_disks_are_refused(self):
        fake = FakeLinux(laptop(node("sda", tran="sata", rm=False),
                                node("zram0", tran=None, rm=False),
                                node("sdd", ro=True)))
        ok, keep, disks = self.find(fake)
        self.assertEqual(ok, [])
        why = {d.id: R.why_not(d, keep) for d in disks}
        self.assertIn("USB", why["sda"])
        self.assertIn("physical", why["zram0"])
        self.assertIn("read-only", why["sdd"])

    def test_4k_sector_sticks_are_refused(self):
        ok, keep, disks = self.find(FakeLinux(laptop(node("sdb", **{"log-sec": 4096}))))
        self.assertEqual(ok, [])
        self.assertIn("4096", R.why_not(disks[-1], keep))

    def test_older_lsblk(self):
        fake = FakeLinux(laptop(node("sdb")), old_lsblk=True)
        ok, _, _ = self.find(fake)
        self.assertEqual([d.id for d in ok], ["sdb"])

    def test_if_the_system_disk_cant_be_found_nothing_is_offered(self):
        fake = FakeLinux(laptop(node("sdb")), mounts={"/": "rpool/ROOT/ubuntu"})
        with self.assertRaises(F.FormatError):
            self.find(fake)

    def test_network_shares_are_not_disks(self):
        fake = FakeLinux(laptop(node("sdb")), mounts={"/mnt/nas": "nas:/music"})
        ok, _, _ = self.find(fake, ["/mnt/nas/DJ Collection"])
        self.assertEqual([d.id for d in ok], ["sdb"])


# ----------------------------------------------------------------- Windows
def win(number, bus="USB", name="SanDisk Ultra", size=32 * GB, letters=(("E", "GIG"),),
        **over):
    e = {"number": number, "name": name, "serial": f"SN{number}", "size": size, "bus": bus,
         "boot": False, "system": False, "readonly": False, "offline": False, "sector": 512,
         "volumes": [{"letter": l, "label": lab} for l, lab in letters]}
    e.update(over)
    return e


class WindowsCandidates(unittest.TestCase):
    def pc(self, *extra):
        return R.windows_disks([win(0, "NVMe", "Samsung SSD", 512 * GB, (("C", "Windows"),),
                                    boot=True, system=True), *extra])

    def test_a_usb_stick_is_offered(self):
        disks = self.pc(win(2))
        keep = R.windows_protected([], disks, exists=lambda p: True)
        ok = [d for d in disks if not R.why_not(d, keep)]
        self.assertEqual([(d.id, d.device, d.name) for d in ok], [("disk2", "Disk 2", "GIG")])

    def test_the_system_disk_and_the_collection_drive_are_never_offered(self):
        disks = self.pc(win(2), win(3, size=2000 * GB, letters=(("M", "MUSIC"),)))
        keep = R.windows_protected([r"M:\DJ Collection"], disks, exists=lambda p: True)
        why = {d.id: R.why_not(d, keep) for d in disks}
        self.assertIn("Windows runs from it", why["disk0"])
        self.assertIn("collection", why["disk3"])
        self.assertIsNone(why["disk2"])

    def test_one_volume_comes_back_without_a_list(self):
        e = win(2)
        e["volumes"] = e["volumes"][0]
        (d,) = R.windows_disks(e)
        self.assertEqual(d.letters, {"E"})
        self.assertEqual(d.volumes, ["GIG"])

    def test_no_drive_letter(self):
        (d,) = R.windows_disks([win(2, letters=(("\x00", ""),))])
        self.assertEqual(d.letters, set())
        self.assertEqual(d.name, "disk2")

    def test_a_drive_letter_on_no_disk_stops_everything(self):
        disks = self.pc(win(2))
        with self.assertRaises(F.FormatError):
            R.windows_protected([r"S:\Music"], disks, exists=lambda p: True,
                                drive_type=lambda letter: 3)

    def test_network_drives_are_not_disks(self):
        disks = self.pc(win(2))
        keep = R.windows_protected([r"Z:\Music", r"\\nas\music"], disks,
                                   exists=lambda p: True, drive_type=lambda letter: 4)
        self.assertNotIn("disk2", keep.disks)

    def test_sd_cards_count_but_fixed_disks_do_not(self):
        disks = self.pc(win(2, bus="SD"), win(3, bus="SATA"))
        keep = R.windows_protected([], disks, exists=lambda p: True)
        why = {d.id: R.why_not(d, keep) for d in disks}
        self.assertIsNone(why["disk2"])
        self.assertIn("USB", why["disk3"])


# --------------------------------------------------- erasing, end to end
class Erase(TempDir):
    """The unprivileged side and the helper together, with the helper run in-process."""

    def setUp(self):
        super().setUp()
        self.fake = FakeLinux(laptop(node("sdb", children=[part("sdb1", "GIG", "/media/dj/GIG")])))
        self.written = []

    def stick(self):
        ok, _, _ = LinuxCandidates.find(None, self.fake)
        return ok[0]

    def elevate(self, argv):
        self.assertIn(R.HELPER_ARG, argv)
        return R.helper(argv[argv.index(R.HELPER_ARG):], run=self.fake, write=self.write)

    def write(self, disk, new_label, run):
        self.written.append((disk.dev, new_label))
        return fat32.plan(disk.size)

    def erase(self, disk, typed="GIG", label="gig"):
        return R.erase(disk, label, typed, [], run=self.fake, exists=lambda p: True,
                       elevate=self.elevate)

    def test_erases_the_chosen_stick(self):
        report = self.erase(self.stick())
        self.assertEqual(self.written, [("/dev/sdb", "GIG")])
        self.assertIn("16 KB clusters", report)

    def test_the_name_must_be_typed_exactly(self):
        with self.assertRaises(F.FormatError):
            self.erase(self.stick(), typed="gig")
        self.assertEqual(self.written, [])

    def test_a_different_stick_on_the_same_device_is_not_erased(self):
        chosen = self.stick()
        self.fake.disks[1]["serial"] = "SOMEONE ELSE"
        with self.assertRaises(F.FormatError):
            self.erase(chosen)
        self.assertEqual(self.written, [])

    def test_the_helper_checks_again_itself(self):
        chosen = self.stick()

        def swap_then_elevate(argv):
            self.fake.disks[1]["size"] = 64 * GB          # swapped after the first check
            return self.elevate(argv)

        with self.assertRaises(F.FormatError) as e:
            R.erase(chosen, "GIG", "GIG", [], run=self.fake, exists=lambda p: True,
                    elevate=swap_then_elevate)
        self.assertIn("isn't there any more", str(e.exception))
        self.assertEqual(self.written, [])

    def test_saying_no_to_the_password_prompt(self):
        def refuse(argv):
            raise F.FormatError("Administrator rights weren't given. Nothing was erased.")

        with self.assertRaises(F.FormatError):
            R.erase(self.stick(), "GIG", "GIG", [], run=self.fake, exists=lambda p: True,
                    elevate=refuse)

    def test_a_helper_that_dies_is_reported(self):
        with self.assertRaises(F.FormatError) as e:
            R.erase(self.stick(), "GIG", "GIG", [], run=self.fake, exists=lambda p: True,
                    elevate=lambda argv: 1)
        self.assertIn("without saying why", str(e.exception))

    def test_the_job_folder_is_removed(self):
        seen = []

        def elevate(argv):
            seen.append(argv[-1])
            return self.elevate(argv)

        R.erase(self.stick(), "GIG", "GIG", [], run=self.fake, exists=lambda p: True,
                elevate=elevate)
        self.assertFalse(os.path.exists(os.path.dirname(seen[0])))


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0 and shutil.which("losetup")
                     and HAVE_FSCK, "needs root, losetup and fsck.fat")
class LoopDevice(TempDir):
    """The real Linux write path, against a loop device standing in for a stick."""

    def test_writes_a_stick_fsck_accepts(self):
        img = self.path("stick.img")
        with open(img, "wb") as fh:
            fh.truncate(300 * MB)
        r = subprocess.run(["losetup", "-f", "--show", img], capture_output=True, text=True)
        if r.returncode:
            self.skipTest("no loop device available")
        dev = r.stdout.strip()
        self.addCleanup(subprocess.run, ["losetup", "-d", dev])
        disk = F.Disk(os.path.basename(dev), dev=dev, size=300 * MB)
        R._linux_write(disk, "GIG")
        r = subprocess.run(["losetup", "-f", "--show", "-o", str(2048 * 512), img],
                           capture_output=True, text=True)
        part_dev = r.stdout.strip()
        self.addCleanup(subprocess.run, ["losetup", "-d", part_dev])
        r = subprocess.run(["fsck.fat", "-n", part_dev], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()

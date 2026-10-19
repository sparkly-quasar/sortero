"""Erase a USB stick as FAT32 on one MBR partition: every Pioneer and Denon player reads FAT32.

find(), erase_stick() and plan() work on every platform. On Linux and Windows
they hand over to rawformat.py, which keeps the same rules; the rest of this
module is macOS, through diskutil:

    diskutil eraseDisk FAT32 <NAME> MBRFormat /dev/diskN

Erasing is the one thing in Sortero that can't be undone, so the rules are
strict, and each is checked twice: when the list of sticks is drawn, and
again a moment before erasing.

  * Only whole, physical, external disks that are removable or ejectable.
  * Never the startup disk, or any disk holding your collection, your gig
    library or Sortero's own data, even if it is external: plenty of DJ
    collections live on an external drive.
  * If any of that can't be worked out, nothing is offered at all.
  * You type the stick's name to confirm, and it has to match exactly.
  * The disk is read again just before erasing; if it isn't the one you
    confirmed (another stick plugged in under the same number, say), nothing
    happens.

diskutil's plist keys used here: WholeDisks (diskutil list), and from
diskutil info: DeviceIdentifier, WholeDisk, Internal, RemovableMedia,
Removable, Ejectable, VirtualOrPhysical, MediaName, TotalSize/Size,
BusProtocol, ParentWholeDisk, APFSPhysicalStores, VolumeName, MountPoint.
"""
import os, plistlib, re, subprocess, sys
from dataclasses import dataclass, field

LABEL_MAX = 11                       # FAT volume labels are 11 characters
LABEL_OK = re.compile(r"^[A-Z0-9 _-]{1,11}$")
BIG = 512 * 1000 ** 3                # bigger than most sticks: worth a second look
DISK_ID = re.compile(r"^disk\d+$")


class FormatError(Exception):
    pass


def available():
    from . import rawformat
    return sys.platform == "darwin" or rawformat.supported()


def find(keep_paths):
    """(sticks that may be erased, [(other external disk, why not)]) on this computer."""
    if sys.platform == "darwin":
        return candidates(keep_paths)
    from . import rawformat
    return rawformat.candidates(keep_paths)


def erase_stick(disk, new_label, typed, keep_paths):
    if sys.platform == "darwin":
        return erase(disk, new_label, typed, keep_paths)
    from . import rawformat
    return rawformat.erase(disk, new_label, typed, keep_paths)


def plan(disk, new_label):
    """What the window says will happen."""
    if sys.platform == "darwin":
        return "Sortero will run:  " + " ".join(command(disk, new_label))
    from . import rawformat
    return rawformat.plan_text(disk, new_label)


def _diskutil(*args):
    r = subprocess.run(["diskutil", *args], capture_output=True)
    if r.returncode != 0:
        raise FormatError(_why(r.stderr or r.stdout) or "diskutil failed")
    return r.stdout


def _why(output):
    """diskutil's error: with -plist it comes as a plist, so take its message out."""
    try:
        message = plistlib.loads(output).get("ErrorMessage")
        if message:
            return str(message)
    except Exception:
        pass
    return output.decode("utf-8", "replace").strip()


def plist(verb, *args, run=_diskutil):
    """diskutil <verb> -plist <args>: -plist must come straight after the verb, or
    diskutil prints its usage instead."""
    return plistlib.loads(run(verb, "-plist", *args))


@dataclass
class Disk:
    id: str                         # disk4
    media: str = ""                 # what the stick calls itself: "SanDisk Ultra"
    size: int = 0
    protocol: str = ""
    internal: bool = True
    removable: bool = False
    physical: bool = False
    whole: bool = False
    volumes: list = field(default_factory=list)     # names of the volumes on it
    dev: str = ""                   # Linux and Windows: /dev/sdb, Disk 2
    serial: str = ""
    sector: int = 512               # logical sector size
    readonly: bool = False
    system: bool = False            # Windows runs from it
    offline: bool = False
    number: int = None              # Windows' disk number
    letters: set = field(default_factory=set)       # Windows drive letters on it
    mounted: list = field(default_factory=list)     # Linux: (device, mount point)

    @property
    def name(self):
        """What you type to confirm: its volume's name, or its disk number."""
        return self.volumes[0] if self.volumes else self.id

    @property
    def device(self):
        return self.dev or f"/dev/{self.id}"

    @property
    def identity(self):
        return (self.id, self.media, self.size, self.serial)


def disk_from(info, listing=None):
    d = Disk(info.get("DeviceIdentifier", ""))
    d.media = info.get("MediaName") or ""
    d.size = int(info.get("TotalSize") or info.get("Size") or 0)
    d.protocol = info.get("BusProtocol") or ""
    d.internal = info.get("Internal", True) is not False
    d.removable = bool(info.get("RemovableMedia") or info.get("Removable")
                       or info.get("Ejectable"))
    d.physical = info.get("VirtualOrPhysical") == "Physical"
    d.whole = bool(info.get("WholeDisk"))
    for entry in (listing or {}).get("AllDisksAndPartitions", []):
        if entry.get("DeviceIdentifier") != d.id:
            continue
        for part in entry.get("Partitions", []) + [entry]:
            name = part.get("VolumeName")
            if name and name not in d.volumes and part.get("Content") != "EFI":
                d.volumes.append(name)
    return d


def whole_disk(identifier):
    """disk4s1 -> disk4."""
    m = re.match(r"^(disk\d+)", identifier or "")
    return m.group(1) if m else None


def device_of(path):
    """The /dev node of the volume a file or folder is on (disk3s5), or None when it
    isn't on a local disk (a network share). diskutil info only takes disks and mount
    points, so a music folder inside a volume is found by the volume's device number."""
    try:
        dev = os.stat(path).st_dev
        names = os.listdir("/dev")
    except OSError:
        return None
    for name in names:
        if re.match(r"^disk\d+(s\d+)*$", name):
            try:
                if os.stat(f"/dev/{name}").st_rdev == dev:
                    return name
            except OSError:
                continue
    return None


def physical_disks_of(path, run=_diskutil):
    """The physical whole disks a folder lives on: through APFS, to the disk itself.
    Empty for a folder that isn't on a local disk, which nothing here could erase."""
    try:
        info = plist("info", path, run=run)         # a mount point: /, /Volumes/Music
    except FormatError:
        node = device_of(path)                      # a folder inside one
        if node is None and path != "/":
            return set()
        info = plist("info", f"/dev/{node}", run=run) if node else {}
    stores = [s.get("APFSPhysicalStore") for s in info.get("APFSPhysicalStores") or []]
    disks = {whole_disk(s) for s in stores if s} or {whole_disk(info.get("ParentWholeDisk"))}
    disks.discard(None)
    if not disks:
        raise FormatError(f"couldn't tell which disk {path} is on")
    return disks


@dataclass
class Keep:
    """Disks never offered: by disk number, and by volume name for folders on a
    volume that isn't mounted right now (a music drive ejected but still plugged
    in still shows up as a disk, with its volume's name)."""
    disks: set = field(default_factory=set)
    volumes: set = field(default_factory=set)

    def holds(self, disk):
        return disk.id in self.disks or bool(self.volumes & set(disk.volumes))


def protected(paths, run=_diskutil, exists=os.path.exists):
    """What is never offered: the startup disk, and wherever these folders live."""
    keep = Keep(physical_disks_of("/", run=run))
    for p in paths:
        if not p:
            continue
        if exists(p):
            keep.disks |= physical_disks_of(p, run=run)
        else:
            parts = os.path.abspath(p).split(os.sep)
            if len(parts) > 2 and parts[1] == "Volumes":
                keep.volumes.add(parts[2])
    return keep


def why_not(disk, keep):
    """None if this disk may be erased, else the reason it may not."""
    if not DISK_ID.match(disk.id) or not disk.whole:
        return "it isn't a whole disk"
    if disk.internal:
        return "it's an internal disk"
    if not disk.physical:
        return "it isn't a physical disk (a disk image, say)"
    if not disk.removable:
        return "it isn't removable"
    if keep.holds(disk):
        return "it holds your startup system, your collection or your gig library"
    return None


def candidates(keep_paths, run=_diskutil, exists=os.path.exists):
    """(sticks that may be erased, [(other external disk, why not)])."""
    keep = protected(keep_paths, run=run, exists=exists)    # raises if it can't tell
    ok, refused = [], []
    for disk_id in plist("list", "external", "physical", run=run).get("WholeDisks", []):
        disk = disk_from(plist("info", disk_id, run=run), plist("list", disk_id, run=run))
        reason = why_not(disk, keep)
        (refused.append((disk, reason)) if reason else ok.append(disk))
    return ok, refused


def label(name):
    """A FAT32 volume label from what was typed: up to 11 of A-Z, 0-9, space, _ and -."""
    v = (name or "").strip().upper()
    if not LABEL_OK.match(v):
        raise FormatError("Use up to 11 letters, numbers, spaces, - or _ (FAT32 names "
                          "are short and upper case), e.g. GIG.")
    return v


def command(disk, new_label):
    return ["diskutil", "eraseDisk", "FAT32", label(new_label), "MBRFormat", f"/dev/{disk.id}"]


def erase(disk, new_label, typed, keep_paths, run=_diskutil, exists=os.path.exists):
    """Erase `disk` as FAT32 + MBR, after checking every rule again. Returns diskutil's output."""
    if typed.strip() != disk.name:
        raise FormatError(f"The name typed doesn't match “{disk.name}”. Nothing was erased.")
    args = command(disk, new_label)
    keep = protected(keep_paths, run=run, exists=exists)
    now = disk_from(plist("info", disk.id, run=run), plist("list", disk.id, run=run))
    if now.identity != disk.identity:
        raise FormatError(f"/dev/{disk.id} isn't the stick you chose any more. Nothing was "
                          "erased. Open this again to see what's plugged in now.")
    reason = why_not(now, keep)
    if reason:
        raise FormatError(f"Not erasing {disk.name}: {reason}.")
    return run(*args[1:]).decode("utf-8", "replace")


def describe(disk):
    from ..common import human_size
    vols = ", ".join(disk.volumes) if disk.volumes else "no volumes"
    return f"{disk.name}  ·  /dev/{disk.id}  ·  {human_size(disk.size)}  ·  {disk.media or '?'}" \
           f"  ·  {vols}"

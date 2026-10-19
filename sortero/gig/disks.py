"""What a mounted stick is: file system, partition map, partitions, and whether it's removable.

Each platform has its own tool, and each tool's output is parsed by a plain
function, so the parsing can be tested without the platform:

  macOS    diskutil info -plist / diskutil list -plist
  Linux    lsblk -J
  Windows  Get-Volume, Get-Partition and Get-Disk, through PowerShell

Anything the tool doesn't say is left as None, and the checks treat None as
"couldn't tell", never as "fine".
"""
import json, os, plistlib, sys
from dataclasses import dataclass

from . import ffmpeg
from .profiles import EXFAT, FAT16, FAT32, HFS, NTFS

MBR, GPT = "MBR", "GPT"


@dataclass
class Volume:
    mount: str
    file_system: str = None     # FAT16, FAT32, exFAT, HFS+, NTFS, or the tool's own name
    scheme: str = None          # MBR, GPT, or None when unknown or unpartitioned
    partitions: int = None      # on the whole disk
    device: str = None          # disk4s1, /dev/sdb1, E:
    disk: str = None            # the whole disk: disk4, /dev/sdb, 2
    name: str = None
    size: int = None            # bytes, whole disk
    removable: bool = None
    internal: bool = None
    error: str = None           # why the rest is unknown


FAT = "FAT"      # a FAT volume whose version the tool didn't say
_FAT_NAMES = {"vfat", "msdos", "fat", "fat12", "fat16", "fat32", "ms-dos", "ms-dos fat12",
              "ms-dos fat16", "ms-dos fat32", "ms-dos (fat)", "ms-dos (fat12)",
              "ms-dos (fat16)", "ms-dos (fat32)"}
_OTHER = {"exfat": EXFAT, "hfs": HFS, "hfsplus": HFS, "hfs+": HFS, "mac os extended": HFS,
          "mac os extended (journaled)": HFS, "ntfs": NTFS, "ntfs3": NTFS, "ntfs-3g": NTFS}


def normal_fs(raw, version=None):
    """One spelling for each file system, whichever tool named it."""
    s = (raw or "").strip().lower()
    if s in _FAT_NAMES:
        tag = s + " " + (version or "").lower()
        if "32" in tag:
            return FAT32
        if "16" in tag or "12" in tag:
            return FAT16
        return FAT
    return _OTHER.get(s, raw or None)


# ------------------------------------------------------------------- macOS
MAC_SCHEMES = {"FDisk_partition_scheme": MBR, "GUID_partition_scheme": GPT}


def parse_diskutil(volume_info, disk_info, disk_list, mount):
    """From the three plists: diskutil info <mount>, info <disk>, list <disk>."""
    v = Volume(mount)
    name = volume_info.get("FilesystemName") or volume_info.get("FilesystemType")
    v.file_system = normal_fs(name)
    v.device = volume_info.get("DeviceIdentifier")
    v.disk = volume_info.get("ParentWholeDisk")
    v.name = volume_info.get("VolumeName") or None
    v.scheme = MAC_SCHEMES.get(disk_info.get("Content"))
    v.size = disk_info.get("TotalSize") or disk_info.get("Size")
    v.internal = disk_info.get("Internal")
    v.removable = bool(disk_info.get("RemovableMedia") or disk_info.get("Removable")
                       or disk_info.get("Ejectable")) if disk_info else None
    for d in disk_list.get("AllDisksAndPartitions", []):
        if d.get("DeviceIdentifier") == v.disk:
            v.partitions = len(d.get("Partitions", [])) or 1
    return v


def _mac(mount):
    def plist(verb, *args):
        # -plist straight after the verb: anywhere else, diskutil prints its usage.
        r = ffmpeg.run(["diskutil", verb, "-plist", *args])
        return plistlib.loads(r.stdout) if r.returncode == 0 and r.stdout else {}
    info = plist("info", mount)
    disk = info.get("ParentWholeDisk")
    return parse_diskutil(info, plist("info", disk) if disk else {},
                          plist("list", disk) if disk else {}, mount)


# ------------------------------------------------------------------- Linux
LINUX_SCHEMES = {"dos": MBR, "gpt": GPT}


def _flat(devices, parent=None):
    for d in devices or []:
        yield d, parent
        yield from _flat(d.get("children"), d)


def parse_lsblk(data, mount):
    """From lsblk -J -b -o NAME,PATH,FSTYPE,FSVER,PTTYPE,MOUNTPOINTS,RM,HOTPLUG,SIZE,TYPE,..."""
    v = Volume(mount)
    target = os.path.normpath(mount)
    for dev, parent in _flat(data.get("blockdevices")):
        points = dev.get("mountpoints") or [dev.get("mountpoint")]
        if target not in [os.path.normpath(p) for p in points if p]:
            continue
        whole = parent or dev
        v.file_system = normal_fs(dev.get("fstype"), dev.get("fsver"))
        v.device = dev.get("path") or dev.get("name")
        v.disk = whole.get("path") or whole.get("name")
        v.name = dev.get("label")
        v.scheme = LINUX_SCHEMES.get(dev.get("pttype") or whole.get("pttype"))
        v.partitions = (sum(1 for c in whole.get("children", []) if c.get("type") == "part")
                        if parent else 1)
        v.size = whole.get("size")
        v.removable = bool(whole.get("rm") or whole.get("hotplug")
                           or whole.get("tran") == "usb")
        v.internal = not v.removable
        return v
    v.error = "lsblk doesn't list anything mounted there"
    return v


def _linux(mount):
    r = ffmpeg.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,FSVER,PTTYPE,MOUNTPOINTS,"
                    "RM,HOTPLUG,SIZE,TYPE,LABEL,TRAN"])
    if r.returncode != 0:     # lsblk older than 2.37 has MOUNTPOINT, not MOUNTPOINTS
        r = ffmpeg.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,FSVER,PTTYPE,MOUNTPOINT,"
                        "RM,HOTPLUG,SIZE,TYPE,LABEL,TRAN"])
    if r.returncode != 0 or not r.stdout:
        return Volume(mount, error="lsblk isn't available")
    return parse_lsblk(json.loads(r.stdout), mount)


# ----------------------------------------------------------------- Windows
WIN_SCRIPT = (
    "$p = Get-Partition -DriveLetter {letter}; $v = Get-Volume -DriveLetter {letter}; "
    "$d = Get-Disk -Number $p.DiskNumber; "
    "[pscustomobject]@{{FileSystem=$v.FileSystem; Label=$v.FileSystemLabel; "
    "PartitionStyle=[string]$d.PartitionStyle; Partitions=$d.NumberOfPartitions; "
    "BusType=[string]$d.BusType; Name=$d.FriendlyName; Size=$d.Size; "
    "IsSystem=$d.IsSystem; IsBoot=$d.IsBoot; Disk=$p.DiskNumber}} | ConvertTo-Json")


def parse_windows(data, mount):
    v = Volume(mount)
    # Windows calls FAT16 plain "FAT", and FAT32 "FAT32".
    fs = data.get("FileSystem")
    v.file_system = FAT16 if fs == "FAT" else normal_fs(fs)
    v.name = data.get("Label") or None
    style = (data.get("PartitionStyle") or "").upper()
    v.scheme = style if style in (MBR, GPT) else None
    v.partitions = data.get("Partitions")
    v.device = os.path.splitdrive(mount)[0] or mount
    v.disk = str(data.get("Disk")) if data.get("Disk") is not None else None
    v.size = data.get("Size")
    v.removable = data.get("BusType") in ("USB", "SD", "MMC")
    v.internal = bool(data.get("IsSystem") or data.get("IsBoot")) or not v.removable
    return v


def _windows(mount):
    letter = os.path.splitdrive(os.path.abspath(mount))[0].rstrip(":")
    if not letter:
        return Volume(mount, error="it isn't a drive letter")
    r = ffmpeg.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    WIN_SCRIPT.format(letter=letter)])
    if r.returncode != 0 or not r.stdout:
        return Volume(mount, error="Windows didn't describe the drive")
    return parse_windows(json.loads(r.stdout), mount)


def volume(mount):
    """Describe the stick mounted at this path."""
    try:
        if sys.platform == "darwin":
            return _mac(mount)
        if os.name == "nt":
            return _windows(mount)
        return _linux(mount)
    except Exception as e:
        return Volume(mount, error=str(e))

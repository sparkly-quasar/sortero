"""Lay out a whole stick as one FAT32 partition on an MBR map, writing the bytes ourselves.

macOS's diskutil makes FAT32 at any size, but Windows' own tools (Format-Volume,
format, diskpart) refuse FAT32 above 32 GB, and most gig sticks are bigger than
that. Linux needs dosfstools, which isn't always installed. So on those two,
Sortero writes the file system itself, from Microsoft's FAT specification
(fatgen103, "FAT32 File System Specification", 2000):

  * MBR in sector 0, one partition of type 0x0C (FAT32, LBA) starting at 1 MiB,
    to the end of the disk (MBR stops at 2 TiB, far beyond any stick).
  * Cluster sizes from the specification's table, as Windows picks them:
    4 KB up to 8 GB, 8 KB to 16 GB, 16 KB to 32 GB, 32 KB above.
  * Two FATs, a backup boot sector at 6, FSInfo at 1 (and its backup at 7),
    the root folder in cluster 2 holding the volume label.
  * The data area starts on a cluster boundary, so clusters line up with the
    stick's flash pages.

Everything is written in whole 512-byte sectors at sector-aligned offsets,
because Windows refuses anything else on a raw disk. Only 512-byte sectors
are supported: USB sticks with any other size are vanishingly rare, and are
refused rather than guessed at.
"""
import os, struct, time
from dataclasses import dataclass

SECTOR = 512
PART_START = 2048                   # sectors: 1 MiB, where every modern tool starts
RESERVED = 32
FATS = 2
MIN_CLUSTERS = 65525                # fewer, and it's FAT16 by definition
MAX_CLUSTERS = 0x0FFFFFF5
MAX_SECTORS = 0xFFFFFFFF            # MBR's limit: 2 TiB
CHUNK = 1 << 20

# fatgen103's table: (partition size in sectors up to, sectors per cluster)
CLUSTERS = ((532480, 1), (16777216, 8), (33554432, 16), (67108864, 32), (MAX_SECTORS + 1, 64))


class Fat32Error(Exception):
    pass


@dataclass
class Layout:
    disk_sectors: int
    part_start: int
    part_sectors: int
    per_cluster: int                # sectors per cluster
    reserved: int
    fat_sectors: int                # one FAT
    clusters: int

    @property
    def data_start(self):           # sectors from the start of the partition
        return self.reserved + FATS * self.fat_sectors

    @property
    def cluster_bytes(self):
        return self.per_cluster * SECTOR


def plan(disk_bytes, sector=SECTOR):
    """Where everything goes on a disk this big."""
    if sector != SECTOR:
        raise Fat32Error(f"this stick uses {sector}-byte sectors; Sortero only formats "
                         "sticks with 512-byte sectors")
    disk = disk_bytes // SECTOR
    part = min(disk - PART_START, MAX_SECTORS)
    per = next(spc for limit, spc in CLUSTERS if part <= limit)
    reserved = RESERVED
    # fatgen103's FAT size: a slight overestimate, which only leaves a few entries spare.
    fat = -(-(part - reserved) // ((256 * per + FATS) // 2))
    reserved += -(reserved + FATS * fat) % per
    clusters = (part - reserved - FATS * fat) // per if part > 0 else 0
    if clusters < MIN_CLUSTERS:
        raise Fat32Error("the stick is too small for FAT32")
    if clusters >= MAX_CLUSTERS or fat * SECTOR // 4 < clusters + 2:
        raise Fat32Error("the stick is too big for FAT32")
    return Layout(disk, PART_START, part, per, reserved, fat, clusters)


def _chs(lba):
    """Cylinder/head/sector for an MBR entry, on the usual 255-head, 63-sector geometry."""
    c, rest = divmod(lba, 255 * 63)
    if c > 1023:
        return bytes((254, 0xFF, 0xFF))
    h, s = divmod(rest, 63)
    return bytes((h, ((c >> 2) & 0xC0) | (s + 1), c & 0xFF))


def mbr(lay, disk_id):
    entry = (b"\x00" + _chs(lay.part_start) + b"\x0c"
             + _chs(lay.part_start + lay.part_sectors - 1)
             + struct.pack("<II", lay.part_start, lay.part_sectors))
    s = bytearray(SECTOR)
    s[440:444] = struct.pack("<I", disk_id)
    s[446:462] = entry
    s[510:512] = b"\x55\xaa"
    return bytes(s)


def _label11(label):
    return label.encode("ascii").ljust(11, b" ")[:11]


def boot_sector(lay, label, volume_id):
    s = bytearray(SECTOR)
    s[0:3] = b"\xeb\x58\x90"
    s[3:11] = b"MSWIN4.1"           # what the specification recommends for compatibility
    struct.pack_into("<HBHBHHBHHHII", s, 11,
                     SECTOR, lay.per_cluster, lay.reserved, FATS,
                     0, 0,          # no fixed root folder, no 16-bit sector count
                     0xF8, 0,       # fixed media, no 16-bit FAT size
                     63, 255, lay.part_start, lay.part_sectors)
    struct.pack_into("<IHHIHH", s, 36, lay.fat_sectors, 0, 0, 2, 1, 6)
    struct.pack_into("<BBBI", s, 64, 0x80, 0, 0x29, volume_id)
    s[71:82] = _label11(label)
    s[82:90] = b"FAT32   "
    # No boot code: a player never boots from it. Booting it anyway just halts.
    s[90:92] = b"\xeb\xfe"
    s[510:512] = b"\x55\xaa"
    return bytes(s)


def fsinfo(lay):
    s = bytearray(SECTOR)
    struct.pack_into("<I", s, 0, 0x41615252)
    struct.pack_into("<III", s, 484, 0x61417272, lay.clusters - 1, 3)
    struct.pack_into("<I", s, 508, 0xAA550000)
    return bytes(s)


def fat_head():
    """The first sector of each FAT: two reserved entries, then the root folder's one cluster."""
    s = bytearray(SECTOR)
    struct.pack_into("<III", s, 0, 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFFF)
    return bytes(s)


def root_head(label, now):
    t = time.localtime(now)
    s = bytearray(SECTOR)
    s[0:11] = _label11(label)
    s[11] = 0x08                    # volume label
    year = min(max(t.tm_year, 1980), 2107)     # all a FAT date can hold
    struct.pack_into("<HH", s, 22, (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2),
                     ((year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday)
    return bytes(s)


def _zero(f, offset, length, progress=None, done=0, total=0):
    z = bytes(CHUNK)
    end = offset + length
    while offset < end:
        n = min(CHUNK, end - offset)
        f.seek(offset)
        f.write(z[:n])
        offset += n
        done += n
        if progress:
            progress(done, total)
    return done


def write(f, disk_bytes, label, volume_id=None, disk_id=None, now=None, progress=None):
    """Write the whole layout to `f`, a disk opened for reading and writing. Returns the Layout.

    Everything a player or computer might still recognise is cleared first:
    the old partition map, the first MiB of the disk, and the last MiB, where
    a GPT keeps its backup copy.
    """
    lay = plan(disk_bytes)
    now = time.time() if now is None else now
    volume_id = int(now) & 0xFFFFFFFF if volume_id is None else volume_id
    disk_id = int.from_bytes(os.urandom(4), "little") if disk_id is None else disk_id
    base = lay.part_start * SECTOR
    tail = min(CHUNK, (lay.disk_sectors - lay.part_start) * SECTOR)
    total = base + lay.data_start * SECTOR + lay.cluster_bytes + tail
    done = _zero(f, 0, base, progress, 0, total)
    done = _zero(f, (lay.disk_sectors * SECTOR) - tail, tail, progress, done, total)
    done = _zero(f, base, lay.data_start * SECTOR + lay.cluster_bytes, progress, done, total)

    def put(sector, data):
        f.seek(base + sector * SECTOR)
        f.write(data)

    for i in range(FATS):
        put(lay.reserved + i * lay.fat_sectors, fat_head())
    put(lay.data_start, root_head(label, now))
    boot, info = boot_sector(lay, label, volume_id), fsinfo(lay)
    for at in (0, 6):
        put(at, boot)
        put(at + 1, info)
    f.seek(0)
    f.write(mbr(lay, disk_id))
    f.flush()
    if progress:
        progress(total, total)
    return lay

"""Erase a stick as FAT32 on Linux and Windows, writing the file system ourselves (see fat32.py).

The rules are the ones formatting.py keeps on macOS, checked twice: when the
list of sticks is drawn, and again a moment before erasing, this time inside
the helper that holds administrator rights.

  * Only whole, physical USB disks (or ones the system calls removable).
  * Never the disk the system runs from, or any disk holding your collection,
    your gig library, Sortero's own data or your home folder.
  * If any of that can't be worked out, nothing is offered at all.
  * You type the stick's name to confirm, and the disk is read again just
    before erasing: if it isn't the one you confirmed, nothing happens.

Erasing a whole disk needs administrator rights, so Sortero starts a copy of
itself with them, through polkit's pkexec on Linux and the UAC prompt on
Windows. That copy is handed a job file naming the disk, what it looked like
and the folders to protect, checks everything again, and writes its answer
next to the job file. Nothing else runs with those rights.

  Linux    lsblk -J lists disks; findmnt and lsblk -s find the disk under a folder.
           The helper unmounts the stick, opens it exclusively and writes it.
  Windows  Get-Disk / Get-Partition / Get-Volume through PowerShell.
           The helper clears the disk with Clear-Disk, writes it through
           \\\\.\\PhysicalDriveN, then has Windows read the new layout.
"""
import json, ntpath, os, re, shutil, subprocess, sys, tempfile

from . import fat32, ffmpeg
from .formatting import Disk, FormatError, Keep, label

HELPER_ARG = "--erase-stick"
LINUX_STICK_BUSES = {"usb"}
WINDOWS_STICK_BUSES = {"USB", "SD", "MMC"}
VIRTUAL = ("loop", "ram", "zram", "dm-", "md", "nbd", "sr")


def supported():
    return sys.platform.startswith("linux") or os.name == "nt"


def _bool(v):
    return v in (True, 1, "1", "true", "True")


# ------------------------------------------------------------------- Linux
LSBLK = "NAME,PATH,TYPE,RM,HOTPLUG,TRAN,SIZE,MODEL,SERIAL,LABEL,{mounts},RO,LOG-SEC"


def _lsblk(*args, run=ffmpeg.run):
    for mounts in ("MOUNTPOINTS", "MOUNTPOINT"):    # lsblk older than 2.37 has only the second
        r = run(["lsblk", "-J", "-b", "-o", LSBLK.format(mounts=mounts), *args])
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout)
    raise FormatError("lsblk couldn't list the disks")


def _mounts(node):
    m = node.get("mountpoints", node.get("mountpoint"))
    return [p for p in (m if isinstance(m, list) else [m]) if p]


def _walk(node):
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def linux_disk(node):
    """A Disk from one of lsblk's top-level entries."""
    name = node.get("name") or ""
    parts = list(_walk(node))[1:]
    d = Disk(name, dev=node.get("path") or f"/dev/{name}")
    d.media = (node.get("model") or "").strip()
    d.serial = (node.get("serial") or "").strip()
    d.size = int(node.get("size") or 0)
    d.protocol = (node.get("tran") or "").lower()
    d.removable = _bool(node.get("rm")) or _bool(node.get("hotplug"))
    d.internal = d.protocol not in LINUX_STICK_BUSES and not d.removable
    d.whole = node.get("type") == "disk"
    d.physical = d.whole and not name.startswith(VIRTUAL)
    d.sector = int(node.get("log-sec") or 512)
    d.readonly = _bool(node.get("ro"))
    d.mounted = [(p.get("path"), m) for p in [node] + parts for m in _mounts(p)]
    for p in parts:
        if p.get("label") and p["label"] not in d.volumes:
            d.volumes.append(p["label"])
    return d


def linux_disks(listing):
    return [linux_disk(n) for n in listing.get("blockdevices") or [] if n.get("type") == "disk"]


def _findmnt(path, run=ffmpeg.run):
    r = run(["findmnt", "-n", "-v", "-o", "SOURCE", "--target", path])
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""


def linux_disks_of(path, run=ffmpeg.run):
    """The whole disks a folder lives on, through partitions, LVM and encryption.
    None if it isn't on a local disk at all (a network share, say)."""
    src = _findmnt(path, run=run)
    if not src:
        raise FormatError(f"couldn't tell which disk {path} is on")
    if not src.startswith("/dev/"):
        return None
    found = {n["name"] for top in _lsblk("-s", src, run=run).get("blockdevices") or []
             for n in _walk(top) if n.get("type") == "disk"}
    if not found:
        raise FormatError(f"couldn't tell which disk {path} is on")
    return found


MEDIA_DIRS = re.compile(r"^/(?:run/)?media/[^/]+/([^/]+)|^/mnt/([^/]+)")


def linux_protected(paths, run=ffmpeg.run, exists=os.path.exists):
    keep = Keep(set())
    system = [p for p in ("/", "/boot", "/boot/efi") if exists(p)]
    for p in system:
        disks = linux_disks_of(p, run=run)
        if disks is None:
            raise FormatError(f"couldn't tell which disk {p} is on")
        keep.disks |= disks
    for p in paths:
        if not p:
            continue
        if exists(p):
            keep.disks |= linux_disks_of(p, run=run) or set()
        else:
            m = MEDIA_DIRS.match(os.path.abspath(p))
            if m:
                keep.volumes.add(m.group(1) or m.group(2))
    return keep


# ----------------------------------------------------------------- Windows
WIN_LIST = r"""
$ErrorActionPreference = 'Stop'
$out = @(Get-Disk | ForEach-Object {
  $d = $_
  $vols = @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue |
    ForEach-Object {
      $v = $_ | Get-Volume -ErrorAction SilentlyContinue
      [pscustomobject]@{ letter = [string]$_.DriveLetter;
                         label = $(if ($v) { $v.FileSystemLabel } else { '' }) } })
  [pscustomobject]@{ number = $d.Number; name = $d.FriendlyName; serial = $d.SerialNumber;
    size = $d.Size; bus = [string]$d.BusType; boot = $d.IsBoot; system = $d.IsSystem;
    readonly = $d.IsReadOnly; offline = $d.IsOffline; sector = $d.LogicalSectorSize;
    volumes = $vols }
})
ConvertTo-Json -InputObject $out -Depth 4 -Compress
"""


def _powershell(script, run=ffmpeg.run):
    # stdin closed: PowerShell started with an open input pipe can wait on it forever.
    r = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script], stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise FormatError((r.stderr or r.stdout).decode("utf-8", "replace").strip()
                          or "PowerShell failed")
    return r.stdout.decode("utf-8", "replace")


def windows_disk(entry):
    n = int(entry.get("number"))
    d = Disk(f"disk{n}", dev=f"Disk {n}")
    d.number = n
    d.media = (entry.get("name") or "").strip()
    d.serial = (entry.get("serial") or "").strip()
    d.size = int(entry.get("size") or 0)
    d.protocol = entry.get("bus") or ""
    d.removable = d.protocol in WINDOWS_STICK_BUSES
    d.internal = not d.removable
    d.whole = d.physical = True
    d.system = bool(entry.get("boot") or entry.get("system"))
    d.readonly = bool(entry.get("readonly"))
    d.offline = bool(entry.get("offline"))
    d.sector = int(entry.get("sector") or 512)
    vols = entry.get("volumes") or []
    vols = [vols] if isinstance(vols, dict) else vols      # one volume comes back bare
    d.letters = {(v.get("letter") or "").strip("\x00 ").upper() for v in vols} - {""}
    d.volumes = [v["label"] for v in vols if v.get("label")]
    return d


def windows_disks(data):
    data = [data] if isinstance(data, dict) else (data or [])
    return [windows_disk(e) for e in data]


def _drive_type(letter):
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\")


def windows_protected(paths, disks, exists=os.path.exists, drive_type=None):
    """What is never offered: the disks Windows runs from, and wherever these folders live."""
    drive_type = drive_type or _drive_type
    keep = Keep({d.id for d in disks if d.system})
    system_drive = os.environ.get("SystemDrive", "C:")
    for p in [system_drive + "\\"] + [p for p in paths if p]:
        letter = ntpath.splitdrive(p)[0].rstrip(":").upper()
        if len(letter) != 1:
            continue                    # \\server\share: not on a disk of this computer
        owners = {d.id for d in disks if letter in d.letters}
        if owners:
            keep.disks |= owners
        elif exists(p) and drive_type(letter) != 4:     # 4: a network drive
            raise FormatError(f"couldn't tell which disk {letter}: is on")
    return keep


# ------------------------------------------------------------------ shared
def why_not(disk, keep):
    """None if this disk may be erased, else the reason it may not."""
    if not disk.whole:
        return "it isn't a whole disk"
    if not disk.physical:
        return "it isn't a physical disk (a disk image, say)"
    if disk.system:
        return "Windows runs from it"
    if disk.internal:
        return "it isn't a USB stick"
    if disk.readonly:
        return "it's read-only (check the lock switch, if it has one)"
    if disk.offline:
        return "Windows has it offline"
    if disk.sector != fat32.SECTOR:
        return f"it uses {disk.sector}-byte sectors, which Sortero can't format"
    if keep.holds(disk):
        return "it holds your system, your collection or your gig library"
    return None


def list_disks(keep_paths, run=ffmpeg.run, exists=os.path.exists):
    """(every disk, what to keep)."""
    if os.name == "nt":
        disks = windows_disks(json.loads(_powershell(WIN_LIST, run=run) or "[]"))
        return disks, windows_protected(keep_paths, disks, exists=exists)
    disks = linux_disks(_lsblk(run=run))
    return disks, linux_protected(keep_paths, run=run, exists=exists)


def candidates(keep_paths, run=ffmpeg.run, exists=os.path.exists):
    """(sticks that may be erased, [(other external disk, why not)])."""
    disks, keep = list_disks(keep_paths, run=run, exists=exists)
    ok, refused = [], []
    for d in disks:
        reason = why_not(d, keep)
        if not reason:
            ok.append(d)
        elif not d.internal or keep.holds(d):
            refused.append((d, reason))
    return ok, refused


def check(disk, keep_paths, run=ffmpeg.run, exists=os.path.exists):
    """The disk as it is now, if it's still the one chosen and still allowed. Raises otherwise."""
    disks, keep = list_disks(keep_paths, run=run, exists=exists)
    now = next((d for d in disks if d.id == disk.id), None)
    if not now or now.identity != disk.identity:
        raise FormatError(f"{disk.device} isn't the stick you chose any more. Nothing was "
                          "erased. Open this again to see what's plugged in now.")
    reason = why_not(now, keep)
    if reason:
        raise FormatError(f"Not erasing {disk.name}: {reason}.")
    return now


def plan_text(disk, new_label):
    return (f"Sortero will erase {disk.device} and write one FAT32 partition named "
            f"{new_label}, asking for administrator rights first.")


# ------------------------------------------------- the unprivileged side
def _helper_argv(job):
    if getattr(sys, "frozen", False):
        return [sys.executable, HELPER_ARG, job]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    boot = (f"import sys; sys.path.insert(0, {root!r}); "
            "from sortero.gig import rawformat; sys.exit(rawformat.helper(sys.argv[1:]))")
    return [sys.executable, "-c", boot, HELPER_ARG, job]


def _elevate_linux(argv):
    if os.geteuid() == 0:
        return subprocess.run(argv, capture_output=True).returncode
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise FormatError("Erasing a stick needs administrator rights, which Sortero asks "
                          "for through polkit's pkexec, and it isn't installed. Install "
                          "polkit (the package is usually called pkexec or polkit), or use "
                          "your system's disk tool. Nothing was erased.")
    code = subprocess.run([pkexec, *argv], capture_output=True).returncode
    if code in (126, 127):
        raise FormatError("Administrator rights weren't given. Nothing was erased.")
    return code


def _elevate_windows(argv):
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040             # SEE_MASK_NOCLOSEPROCESS: keep a handle to wait on
    info.lpVerb = "runas"
    info.lpFile = argv[0]
    info.lpParameters = subprocess.list2cmdline(argv[1:])
    info.nShow = 0                      # SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.get_last_error() == 1223:     # ERROR_CANCELLED: No on the UAC prompt
            raise FormatError("Administrator rights weren't given. Nothing was erased.")
        raise FormatError("Windows couldn't start the formatter with administrator rights.")
    kernel32 = ctypes.windll.kernel32
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return code.value


def erase(disk, new_label, typed, keep_paths, run=ffmpeg.run, exists=os.path.exists,
          elevate=None):
    """Erase `disk` as FAT32 + MBR, after checking every rule again. Returns a short report."""
    if typed.strip() != disk.name:
        raise FormatError(f"The name typed doesn't match “{disk.name}”. Nothing was erased.")
    new_label = label(new_label)
    check(disk, keep_paths, run=run, exists=exists)
    home = os.path.expanduser("~")
    # A private folder: the helper's answer is written as root on Linux, and only
    # a folder of our own lets us remove it afterwards.
    folder = tempfile.mkdtemp(prefix="sortero-erase-")
    job = os.path.join(folder, "job.json")
    out = job + ".out"
    try:
        with open(job, "w", encoding="utf-8") as fh:
            json.dump({"id": disk.id, "identity": list(disk.identity), "label": new_label,
                       "keep": [p for p in keep_paths if p] + [home]}, fh)
        elevate = elevate or (_elevate_windows if os.name == "nt" else _elevate_linux)
        code = elevate(_helper_argv(job))
        try:
            with open(out, encoding="utf-8") as fh:
                result = json.load(fh)
        except (OSError, ValueError):
            raise FormatError(f"The formatter stopped without saying why (exit code {code}). "
                              f"Check {disk.device} in Gig prep before using it.")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if not result.get("ok"):
        raise FormatError(result.get("error") or "Formatting failed.")
    return result.get("report", "")


# --------------------------------------------- the side with admin rights
def _linux_write(disk, new_label, run=ffmpeg.run):
    for dev, mount in disk.mounted:
        r = run(["umount", dev])
        if r.returncode != 0:
            raise FormatError(f"{mount} is in use, so the stick can't be unmounted. Close "
                              "anything using it and try again. Nothing was erased.")
    try:
        fd = os.open(disk.dev, os.O_RDWR | os.O_EXCL)
    except OSError as e:
        raise FormatError(f"{disk.dev} is in use ({e.strerror}). Nothing was erased.")
    with os.fdopen(fd, "r+b", buffering=0) as f:
        lay = fat32.write(f, disk.size, new_label)
        os.fsync(f.fileno())
    _reread_linux(disk.dev)
    return lay


def _reread_linux(dev):
    """Have the kernel read the new partition map, so the new volume appears without replugging."""
    import fcntl
    try:
        fd = os.open(dev, os.O_RDONLY)
        try:
            fcntl.ioctl(fd, 0x125F)     # BLKRRPART
        finally:
            os.close(fd)
    except OSError:
        pass                            # udev rereads it on close anyway


WIN_CLEAR = ("$ErrorActionPreference = 'Stop'; $d = Get-Disk -Number {n}; "
             "if ([string]$d.PartitionStyle -ne 'RAW') {{ "
             "Clear-Disk -Number {n} -RemoveData -RemoveOEM -Confirm:$false }}")
WIN_FINISH = ("Update-Disk -Number {n}; Start-Sleep -Seconds 2; "
              "$p = Get-Partition -DiskNumber {n} -ErrorAction SilentlyContinue | "
              "Select-Object -First 1; "
              "if ($p -and -not [string]$p.DriveLetter.ToString().Trim([char]0)) {{ "
              "$p | Add-PartitionAccessPath -AssignDriveLetter -ErrorAction SilentlyContinue }}")


def _windows_write(disk, new_label, run=ffmpeg.run):
    _powershell(WIN_CLEAR.format(n=disk.number), run=run)
    with open(rf"\\.\PhysicalDrive{disk.number}", "r+b", buffering=0) as f:
        lay = fat32.write(f, disk.size, new_label)
        os.fsync(f.fileno())
    try:
        _powershell(WIN_FINISH.format(n=disk.number), run=run)
    except FormatError:
        pass                            # written; Windows will see it when replugged
    return lay


def helper(argv, run=ffmpeg.run, write=None):
    """The copy of Sortero started with administrator rights. Exit code 0 on success."""
    args = [a for a in argv if a != HELPER_ARG]
    if len(args) != 1:
        return 2
    job_path = args[0]
    try:
        with open(job_path, encoding="utf-8") as fh:
            job = json.load(fh)
        chosen = Disk(job["id"])
        disks, keep = list_disks(job.get("keep") or [], run=run)
        now = next((d for d in disks if d.id == chosen.id), None)
        if not now or now.identity != tuple(job["identity"]):
            raise FormatError("The stick you chose isn't there any more. Nothing was erased.")
        reason = why_not(now, keep)
        if reason:
            raise FormatError(f"Not erasing {now.name}: {reason}.")
        new_label = label(job["label"])
        write = write or (_windows_write if os.name == "nt" else _linux_write)
        lay = write(now, new_label, run=run)
        result = {"ok": True, "report": f"{now.device}: one FAT32 partition, "
                                        f"{lay.cluster_bytes // 1024} KB clusters, "
                                        f"named {new_label}."}
    except FormatError as e:
        result = {"ok": False, "error": str(e)}
    except Exception as e:                  # anything else: say so rather than vanish
        result = {"ok": False, "error": f"Formatting failed: {e}"}
    try:
        with open(job_path + ".out", "w", encoding="utf-8") as fh:
            json.dump(result, fh)
    except OSError:
        return 3
    return 0 if result["ok"] else 1

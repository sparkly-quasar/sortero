#!/usr/bin/env python3
"""Format a virtual disk on Windows the way Sortero formats a stick, and check Windows reads it.

    python tools/format_windows_check.py

For CI (.github/workflows/format-windows.yml): needs Windows and administrator
rights, which GitHub's Windows runners have. It makes a 300 MB virtual disk
with diskpart and attaches it with Mount-DiskImage (an attach from diskpart
lasts only as long as diskpart runs), then runs the same steps the elevated helper runs on a real
stick (rawformat._windows_write: Clear-Disk, the raw write through
\\\\.\\PhysicalDriveN, Update-Disk), and checks Windows mounts FAT32 named GIG,
chkdsk finds nothing wrong, and a file can be written and read back.

It runs twice: first on a brand-new disk, then again on the formatted one, so
Clear-Disk's path is taken too. The virtual disk is never offered in Sortero
itself (it isn't a USB stick), which is checked as well.
"""
import json, os, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sortero.gig import rawformat as R  # noqa: E402

SIZE = 300 * 1024 ** 2


def diskpart(lines):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("\n".join(lines) + "\n")
    r = subprocess.run(["diskpart", "/s", fh.name], capture_output=True, text=True)
    print(r.stdout)
    if r.returncode:
        sys.exit(f"diskpart failed: {r.stdout}{r.stderr}")


def the_disk():
    disks = R.windows_disks(json.loads(R._powershell(R.WIN_LIST)))
    found = [d for d in disks if d.size == SIZE and "Virtual" in d.protocol]
    if len(found) != 1:
        sys.exit(f"expected one {SIZE}-byte virtual disk, found {[vars(d) for d in disks]}")
    return found[0]


def volume(number):
    for _ in range(30):
        out = R._powershell(
            f"Get-Partition -DiskNumber {number} -ErrorAction SilentlyContinue | Get-Volume "
            "-ErrorAction SilentlyContinue | Select-Object DriveLetter, FileSystem, "
            "FileSystemLabel | ConvertTo-Json -Compress").strip()
        if out:
            v = json.loads(out)
            letter = str(v.get("DriveLetter") or "").strip("\x00 ")
            if v.get("FileSystem") and letter:
                return v, letter
        time.sleep(1)
    sys.exit("Windows didn't mount the new volume")


def check(round_):
    d = the_disk()
    keep = R.windows_protected([], [d])
    reason = R.why_not(d, keep)
    assert reason and "USB" in reason, f"a virtual disk must never be offered: {reason}"
    print(f"round {round_}: Disk {d.number}, {d.size} bytes, {d.protocol}")
    print("  clearing and writing")
    lay = R._windows_write(d, "GIG")
    print(f"  wrote {lay}")
    v, letter = volume(d.number)
    print(f"  Windows sees {v} on {letter}:")
    assert v["FileSystem"] == "FAT32", v
    assert v["FileSystemLabel"] == "GIG", v
    print("  chkdsk")
    r = subprocess.run(["chkdsk", f"{letter}:"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    print(r.stdout[-800:])
    assert r.returncode == 0, f"chkdsk exit {r.returncode}"
    p = f"{letter}:\\hello.txt"
    with open(p, "w") as fh:
        fh.write("played on a CDJ one day")
    with open(p) as fh:
        assert fh.read() == "played on a CDJ one day"


def main():
    if os.name != "nt":
        sys.exit("Windows only")
    vhd = os.path.join(tempfile.gettempdir(), "sortero-stick.vhdx")
    if os.path.exists(vhd):
        os.remove(vhd)
    diskpart([f'create vdisk file="{vhd}" maximum={SIZE // 1024 ** 2} type=expandable'])
    print("attaching")
    R._powershell(f"Mount-DiskImage -ImagePath '{vhd}' -NoDriveLetter | Out-Null")
    try:
        check(1)
        check(2)
    finally:
        try:
            R._powershell(f"Dismount-DiskImage -ImagePath '{vhd}' | Out-Null")
        except Exception as e:          # never hide why the check itself failed
            print(f"(couldn't detach the virtual disk: {e})")
    print("OK: Windows mounts, checks and writes the stick Sortero formats")


if __name__ == "__main__":
    main()

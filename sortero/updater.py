"""Download a new release and swap it in, then relaunch.

A running application cannot reliably delete itself, so the actual swap is done
by a small helper script: Sortero launches it detached, quits, and the helper
waits for the process to disappear before moving anything.

The old copy is kept aside until the new one is in place, and restored if the
move fails - a failed update must never leave the user with no application.
"""
import os, re, shutil, subprocess, sys, tempfile, time, urllib.request, zipfile

from . import net, paths
from .version import __version__

ASSET_PATTERNS = {
    "darwin": re.compile(r"(?i)macos"),
    "win32": re.compile(r"(?i)windows"),
    "linux": re.compile(r"(?i)linux"),
}


class UpdateError(Exception):
    pass


# ---------------------------------------------------------------- discovery
def running_frozen():
    return bool(getattr(sys, "frozen", False))


def installed_path():
    """The thing that would be replaced: the .app bundle, or the program dir."""
    if not running_frozen():
        return None
    exe = os.path.realpath(sys.executable)
    if paths.IS_MAC:
        # .../Sortero.app/Contents/MacOS/Sortero
        parts = exe.split(os.sep)
        for i in range(len(parts) - 1, -1, -1):
            if parts[i].endswith(".app"):
                return os.sep.join(parts[:i + 1])
        return None
    return os.path.dirname(exe)


def pick_asset(assets):
    pat = ASSET_PATTERNS.get(sys.platform, ASSET_PATTERNS["linux"])
    for a in assets:
        name = a.get("name", "")
        if name.endswith(".zip") and pat.search(name):
            return a
    raise UpdateError("That release has no build for this platform.")


def writable(target):
    """Can we actually replace it? /Applications may be protected by macOS."""
    parent = os.path.dirname(target)
    try:
        probe = os.path.join(parent, f".sortero-write-test-{os.getpid()}")
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- download
def download(url, dest_dir, progress=None, timeout=120):
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "update.zip")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Sortero/{__version__}",
        "Accept": "application/octet-stream"})
    with net.urlopen(req, timeout=timeout) as r, open(dest, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if progress and total:
                progress(got, total)
    if os.path.getsize(dest) < 1_000_000:
        raise UpdateError("The download looks truncated; not installing it.")
    return dest


def extract(zip_path, into):
    os.makedirs(into, exist_ok=True)
    if paths.IS_MAC:
        # ditto preserves bundle structure and symlinks that zipfile mangles
        subprocess.run(["ditto", "-x", "-k", zip_path, into], check=True,
                       capture_output=True)
    else:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(into)

    for dirpath, dirnames, filenames in os.walk(into):
        if paths.IS_MAC:
            for d in dirnames:
                if d == "Sortero.app":
                    return os.path.join(dirpath, d)
        else:
            for f in filenames:
                if f in ("Sortero", "Sortero.exe"):
                    return dirpath
    raise UpdateError("Couldn't find Sortero inside the downloaded archive.")


# ------------------------------------------------------------------- swap
MAC_SCRIPT = r"""#!/bin/bash
# Sortero self-update helper. Waits for the app to quit, swaps it, relaunches.
set -u
APP="$1"; NEW="$2"; PID="$3"; LOG="$4"
exec >>"$LOG" 2>&1
echo "waiting for pid $PID"
for _ in $(seq 1 200); do kill -0 "$PID" 2>/dev/null || break; sleep 0.3; done
sleep 0.5
OLD="${APP}.old-$$"
if ! mv "$APP" "$OLD"; then echo "could not move old app"; open "$APP"; exit 1; fi
if ! mv "$NEW" "$APP"; then
  echo "install failed - restoring previous version"
  mv "$OLD" "$APP"; open "$APP"; exit 1
fi
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
codesign --force --sign - "$APP" 2>/dev/null
rm -rf "$OLD"
echo "installed; relaunching"
open "$APP"
"""

WIN_SCRIPT = r"""@echo off
rem Sortero self-update helper. Waits for the app to quit, swaps it, relaunches.
rem Runs from its own folder: Windows won't rename a folder that is any
rem process's current directory, and Sortero's usually is the one being replaced.
rem ping, not timeout, for the pauses: timeout quits at once without a console.
setlocal
cd /d "%~dp0"
set "APP=%~1"
set "NEW=%~2"
set "PID=%~3"
set "LOG=%~4"
>>"%LOG%" echo waiting for pid %PID%
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto gone
ping -n 2 127.0.0.1 >nul
goto wait
:gone
rem A virus scanner or Explorer can hold the old folder a little longer.
set TRIES=0
:swap
ping -n 2 127.0.0.1 >nul
if exist "%APP%.old" rmdir /s /q "%APP%.old" >nul 2>&1
move "%APP%" "%APP%.old" >nul 2>&1
if not exist "%APP%" goto aside
set /a TRIES+=1
if %TRIES% lss 30 goto swap
>>"%LOG%" echo could not move the old copy aside; leaving it as it was
goto launch
:aside
move "%NEW%" "%APP%" >nul 2>&1
if exist "%APP%\Sortero.exe" goto installed
>>"%LOG%" echo install failed - restoring previous version
if exist "%APP%" rmdir /s /q "%APP%" >nul 2>&1
move "%APP%.old" "%APP%" >nul 2>&1
goto launch
:installed
rmdir /s /q "%APP%.old" >nul 2>&1
>>"%LOG%" echo installed; relaunching
:launch
start "" "%APP%\Sortero.exe"
"""

LINUX_SCRIPT = r"""#!/bin/bash
set -u
APP="$1"; NEW="$2"; PID="$3"; LOG="$4"
exec >>"$LOG" 2>&1
for _ in $(seq 1 200); do kill -0 "$PID" 2>/dev/null || break; sleep 0.3; done
sleep 0.5
OLD="${APP}.old-$$"
if ! mv "$APP" "$OLD"; then exit 1; fi
if ! mv "$NEW" "$APP"; then mv "$OLD" "$APP"; "$APP/Sortero" & exit 1; fi
rm -rf "$OLD"
"$APP/Sortero" &
"""


def log_path():
    return os.path.join(paths.data_dir(), "update.log")


def check(target=None):
    """The folder or bundle an update would replace, or UpdateError saying why it can't be."""
    target = target or installed_path()
    if not target:
        raise UpdateError("Sortero is running from source, so it can't replace "
                          "itself. Pull the latest code instead.")
    if not writable(target):
        raise UpdateError(
            f"Can't write to {os.path.dirname(target)}.\n\n"
            "macOS asks permission before one app may modify another in "
            "/Applications. Grant Sortero access under System Settings → "
            "Privacy & Security → App Management, or move Sortero somewhere "
            "like your home folder and try again.")
    return target


def _beside(new_path, target):
    """Windows' move can't take a folder to another drive, and the download sits
    on the system drive, so a copy kept elsewhere gets the new one put next to it."""
    if os.path.splitdrive(os.path.abspath(new_path))[0].lower() == \
            os.path.splitdrive(os.path.abspath(target))[0].lower():
        return new_path
    staged = target + ".new"
    shutil.rmtree(staged, ignore_errors=True)
    try:
        shutil.copytree(new_path, staged)
    except OSError as e:
        shutil.rmtree(staged, ignore_errors=True)
        raise UpdateError(f"Couldn't put the new version next to this one: {e}")
    return staged


def install(new_path, target=None):
    """Hand off to the helper and return. The caller must quit immediately."""
    target = check(target)
    tmp = tempfile.mkdtemp(prefix="sortero-update-")
    flags = 0
    if paths.IS_WIN:
        new_path = _beside(new_path, target)
        script = os.path.join(tmp, "swap.bat")
        body = WIN_SCRIPT
        args = ["cmd", "/c", script, target, new_path, str(os.getpid()), log_path()]
        # no console window over Sortero's last message
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        script = os.path.join(tmp, "swap.sh")
        body = MAC_SCRIPT if paths.IS_MAC else LINUX_SCRIPT
        args = ["/bin/bash", script, target, new_path, str(os.getpid()), log_path()]
    with open(script, "w") as fh:
        fh.write(body)
    os.chmod(script, 0o755)

    # Started in its own folder, never the one it is about to move
    subprocess.Popen(args, cwd=tmp, start_new_session=True, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return script


def prepare(asset, progress=None):
    """Download and unpack; returns the path to the new application."""
    tmp = tempfile.mkdtemp(prefix="sortero-dl-")
    # a GitHub asset, or a Sortero Pro build from licence.latest_build()
    url = asset.get("browser_download_url") or asset.get("url")
    zip_path = download(url, tmp, progress=progress)
    return extract(zip_path, os.path.join(tmp, "unpacked"))

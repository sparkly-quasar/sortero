#!/usr/bin/env python3
"""Build the Sortero desktop app for the platform you run this on.

    python build/build_app.py [--universal2]

macOS   -> build/dist/Sortero.app   (+ a .zip you can hand to someone else)
Windows -> build/dist/Sortero/Sortero.exe
Linux   -> build/dist/Sortero/Sortero

--universal2 (macOS) builds an Intel + Apple Silicon binary. It requires a
universal2 Python - the python.org installer ships one; Homebrew's does not.
The script checks first and tells you rather than failing deep inside
PyInstaller.
"""
import argparse, os, shutil, subprocess, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(BUILD, "dist")
WORK = os.path.join(BUILD, "work")
IS_MAC, IS_WIN = sys.platform == "darwin", os.name == "nt"


def venv_python():
    for cand in (os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
                 os.path.join(ROOT, ".venv", "bin", "python")):
        if os.path.exists(cand):
            return cand
    return sys.executable


def mac_arches(py):
    try:
        out = subprocess.run(["lipo", "-archs", os.path.realpath(py)],
                             capture_output=True, text=True).stdout
        return out.split()
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universal2", action="store_true",
                    help="macOS: build a universal (Intel + Apple Silicon) binary")
    args = ap.parse_args()

    py = venv_python()
    print(f"==> python: {py}")

    print("==> generating icons")
    subprocess.run([py, os.path.join(BUILD, "gen_icon.py")], check=True)

    icon = None
    if IS_MAC:
        icon = os.path.join(BUILD, "Sortero.icns")
    elif IS_WIN:
        icon = os.path.join(BUILD, "Sortero.ico")

    for d in (DIST, WORK):
        shutil.rmtree(d, ignore_errors=True)

    cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", "Sortero",
           "--distpath", DIST, "--workpath", WORK, "--specpath", BUILD,
           "--hidden-import", "mutagen", "--collect-submodules", "mutagen",
           "--hidden-import", "keyring", "--collect-submodules", "keyring"]
    if icon and os.path.exists(icon):
        cmd += ["--icon", icon]
    if IS_MAC:
        cmd += ["--osx-bundle-identifier", "com.sortero.app"]
        if args.universal2:
            arches = mac_arches(py)
            if "x86_64" not in arches or "arm64" not in arches:
                sys.exit(
                    f"\nCannot build universal2: this Python is {'/'.join(arches) or 'single-arch'}.\n"
                    "PyInstaller can only produce a universal2 binary from a universal2 Python.\n"
                    "Install one from https://www.python.org/downloads/macos/ (the installer is\n"
                    "universal2), recreate the venv with it, then re-run with --universal2.\n")
            cmd += ["--target-arch", "universal2"]
    cmd.append(os.path.join(ROOT, "run.py"))

    print("==> building")
    subprocess.run(cmd, check=True, cwd=ROOT)

    if IS_MAC:
        app = os.path.join(DIST, "Sortero.app")
        if not os.path.isdir(app):
            sys.exit("build failed: no Sortero.app")
        # Ad-hoc signature so Gatekeeper will run it locally.
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app],
                       capture_output=True)
        arch = "-".join(mac_arches(os.path.join(app, "Contents", "MacOS", "Sortero"))) or "unknown"
        zip_path = os.path.join(DIST, f"Sortero-macOS-{arch}.zip")
        print("==> zipping (ditto preserves the bundle)")
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        app, zip_path], check=True)
        print(f"\nbuilt {app}\nzip:  {zip_path}  ({os.path.getsize(zip_path)/1e6:.1f} MB)")
    else:
        target = os.path.join(DIST, "Sortero")
        plat = "windows" if IS_WIN else "linux"
        zip_path = os.path.join(DIST, f"Sortero-{plat}-x86_64.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, _, files in os.walk(target):
                for f in files:
                    fp = os.path.join(dirpath, f)
                    z.write(fp, os.path.relpath(fp, DIST))
        print(f"\nbuilt {target}\nzip:  {zip_path}")


if __name__ == "__main__":
    main()

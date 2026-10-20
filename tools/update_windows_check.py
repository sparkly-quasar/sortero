#!/usr/bin/env python3
"""Run Sortero's Windows self-update helper for real, and check it swapped the copy in place.

    python tools/update_windows_check.py

For CI (.github/workflows/update-windows.yml). It sets up the two things a
tester's update tripped over in v0.35.1 and before:

  * The app quits from inside its own folder, as it does when started by
    double-clicking: Sortero's working directory is the folder being replaced,
    and the helper used to inherit it, so Windows wouldn't move it and the new
    version landed inside the old one as a subfolder.
  * The download sits in %TEMP% on C: while the copy being replaced can be on
    another drive. GitHub's runners check out on D:, so that path is taken too.

A stand-in "old Sortero" (a small Python process started in the old folder)
calls updater.install() and quits, exactly as the app does. Then this waits
for the helper's log, and checks the folder now holds the new version, with
no nested copy and no .old left behind.

Then the repair, for copies an older updater already left stuck: the new
version inside the old folder, at <old>\\Sortero. A stand-in started from
there, as a double-click would start it, calls updater.finish_stuck() and
quits, and the same checks follow.
"""
import os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from sortero import updater  # noqa: E402

STAND_IN = r"""
import sys
sys.path.insert(0, {here!r})
from sortero import updater
{call}
"""


def fake_build(folder, version):
    os.makedirs(folder)
    # any small program will do as Sortero.exe: the helper starts it at the end
    shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "whoami.exe"),
                os.path.join(folder, "Sortero.exe"))
    with open(os.path.join(folder, "VERSION"), "w") as fh:
        fh.write(version)


def swap(title, app, call, cwd):
    """Run `call` from a stand-in started in `cwd`, then check `app` holds the new version."""
    print(f"\n== {title}")
    log = updater.log_path()
    if os.path.exists(log):
        os.remove(log)
    r = subprocess.run([sys.executable, "-c", STAND_IN.format(here=HERE, call=call)],
                       cwd=cwd)
    if r.returncode:
        return [f"{call} failed"]

    deadline = time.time() + 90
    text = ""
    while time.time() < deadline:
        text = open(log).read() if os.path.exists(log) else ""
        if "relaunching" in text or "restoring" in text or "leaving it" in text:
            break
        time.sleep(1)
    print("helper log:\n" + text)

    problems = []
    if "installed; relaunching" not in text:
        problems.append("the helper didn't report installing")
    got = open(os.path.join(app, "VERSION")).read() if os.path.exists(
        os.path.join(app, "VERSION")) else None
    if got != "new":
        problems.append(f"the folder holds the {got!r} version, not the new one")
    if os.path.isdir(os.path.join(app, "Sortero")):
        problems.append("a copy is still inside the folder as a subfolder")
    for leftover in (app + ".old", app + ".new"):
        if os.path.exists(leftover):
            problems.append(f"{leftover} was left behind")
    return [f"{title}: {p}" for p in problems]


def main():
    if os.name != "nt":
        sys.exit("this check needs Windows")
    problems = []

    work = tempfile.mkdtemp(prefix="update-check-", dir=HERE)   # D: on GitHub's runners
    app = os.path.join(work, "Sortero")
    fake_build(app, "old")
    new = os.path.join(tempfile.mkdtemp(prefix="sortero-dl-"), "unpacked", "Sortero")
    fake_build(new, "new")
    # The stand-in runs with the old folder as its working directory
    problems += swap("update", app, f"updater.install({new!r}, target={app!r})", cwd=app)

    work = tempfile.mkdtemp(prefix="stuck-check-", dir=HERE)
    app = os.path.join(work, "Sortero")
    fake_build(app, "old")
    nested = os.path.join(app, "Sortero")      # where an older updater left it
    fake_build(nested, "new")
    problems += swap("finishing a stuck update", app,
                     f"updater.finish_stuck({nested!r}, parent={app!r})", cwd=nested)

    if problems:
        sys.exit("FAILED:\n  " + "\n  ".join(problems))
    print("\nOK: both replaced the folder in place")


if __name__ == "__main__":
    main()

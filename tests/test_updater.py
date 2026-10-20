"""Swapping a new build in: the helper's working directory, and a real run of the Linux helper.

The Windows helper runs for real in CI (tools/update_windows_check.py)."""
import os, subprocess, sys, tempfile, unittest
from unittest import mock

from sortero import updater as U


def build(folder, version):
    os.makedirs(folder)
    exe = os.path.join(folder, "Sortero")
    with open(exe, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(exe, 0o755)
    with open(os.path.join(folder, "VERSION"), "w") as fh:
        fh.write(version)


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = os.path.join(self.tmp, "Sortero")
        build(self.app, "old")

    def test_the_helper_never_starts_in_the_folder_it_replaces(self):
        # Windows won't move a folder that is a process's working directory, and
        # Sortero's own usually is the one being replaced.
        with mock.patch.object(U.subprocess, "Popen") as popen:
            script = U.install(os.path.join(self.tmp, "new"), target=self.app)
        cwd = popen.call_args.kwargs["cwd"]
        self.assertEqual(cwd, os.path.dirname(script))
        self.assertFalse(os.path.abspath(cwd).startswith(os.path.abspath(self.app) + os.sep))

    def test_nothing_starts_when_the_copy_cant_be_replaced(self):
        with mock.patch.object(U, "writable", return_value=False), \
                mock.patch.object(U.subprocess, "Popen") as popen:
            with self.assertRaises(U.UpdateError):
                U.install(os.path.join(self.tmp, "new"), target=self.app)
        popen.assert_not_called()

    def test_running_from_source_is_refused(self):
        with mock.patch.object(U, "installed_path", return_value=None):
            with self.assertRaises(U.UpdateError):
                U.check()

    @unittest.skipUnless(sys.platform.startswith("linux"), "the Linux helper")
    def test_the_linux_helper_swaps_the_folder_in_place(self):
        new = os.path.join(self.tmp, "dl", "unpacked", "Sortero")
        build(new, "new")
        # the app, about to quit: started from a shell that exits at once, so
        # it isn't left as this process's unreaped child once it has quit
        pid = subprocess.run(["sh", "-c", "sleep 0.5 >/dev/null & echo $!"],
                             capture_output=True, text=True).stdout.strip()
        log = os.path.join(self.tmp, "update.log")
        script = os.path.join(self.tmp, "swap.sh")
        with open(script, "w") as fh:
            fh.write(U.LINUX_SCRIPT)
        r = subprocess.run(["/bin/bash", script, self.app, new, pid, log],
                           cwd=self.app, timeout=30)
        self.assertEqual(r.returncode, 0)
        with open(os.path.join(self.app, "VERSION")) as fh:
            self.assertEqual(fh.read(), "new")
        self.assertFalse(os.path.exists(os.path.join(self.app, "Sortero", "VERSION")))
        self.assertEqual([n for n in os.listdir(self.tmp) if ".old" in n], [])


class WindowsHelper(unittest.TestCase):
    """What can be said about swap.bat without Windows to run it on."""

    @staticmethod
    def commands():
        return "\n".join(l for l in U.WIN_SCRIPT.lower().splitlines()
                         if not l.startswith("rem "))

    def test_pauses_dont_use_timeout(self):
        # timeout exits at once in a process with no console input, so the wait
        # for Sortero to quit would spin and the swap would start too early
        self.assertNotIn("timeout", self.commands())

    def test_it_moves_out_of_the_folder_it_replaces(self):
        self.assertIn('cd /d "%~dp0"', U.WIN_SCRIPT)

    def test_no_variables_read_inside_blocks(self):
        # cmd expands %VAR% when a ( ) block is read, not as it runs
        self.assertNotIn("(", self.commands())

    def test_a_download_on_another_drive_is_put_beside_the_copy(self):
        tmp = tempfile.mkdtemp()
        new, app = os.path.join(tmp, "new"), os.path.join(tmp, "app")
        build(new, "new")
        drives = {os.path.abspath(new): "C:", os.path.abspath(app): "D:"}
        with mock.patch.object(U.os.path, "splitdrive",
                               side_effect=lambda p: (drives.get(p, ""), p)):
            staged = U._beside(new, app)
        self.assertEqual(staged, app + ".new")
        self.assertTrue(os.path.isfile(os.path.join(staged, "VERSION")))
        self.assertEqual(U._beside(new, os.path.join(tmp, "elsewhere")), new)


if __name__ == "__main__":
    unittest.main()

"""Sortero's tests. Run from the repository root:

    python -m unittest discover tests

Everything Sortero would write to its app-data folder (settings, journals) goes
to a throwaway folder instead, so running the tests never touches a real
collection's history.
"""
import atexit, os, shutil, tempfile

from sortero import paths

_APPDATA = tempfile.mkdtemp(prefix="sortero-tests-")
paths._base = lambda kind: os.path.join(_APPDATA, kind)
atexit.register(shutil.rmtree, _APPDATA, ignore_errors=True)

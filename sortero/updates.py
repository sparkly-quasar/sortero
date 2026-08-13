"""Check GitHub Releases for a newer Sortero.

While the repo is private the releases API needs a token, so a plain check gets
a 404. That is reported as a clear, actionable state rather than a scary error:
either make the repo public, or paste a token with `repo` scope into Settings.
"""
import json, re, time, urllib.error, urllib.request

from . import settings, net
from .version import __version__

REPO = "sparkly-quasar/sortero"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = 24 * 3600


def parse(v):
    """'v1.2.3' -> (1, 2, 3); unknown shapes sort lowest."""
    nums = re.findall(r"\d+", (v or "").strip().lstrip("vV"))
    return tuple(int(n) for n in nums[:3]) or (0,)


def is_newer(remote, local=__version__):
    return parse(remote) > parse(local)


def check(token=None, timeout=15):
    """Return a dict describing the outcome.

    {"state": "up-to-date"|"update"|"private"|"error",
     "latest": str|None, "url": str, "message": str}
    """
    token = (token or settings.get("github_token") or "").strip()
    req = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Sortero/{__version__}"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with net.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return {"state": "private", "latest": None, "url": RELEASES_URL,
                    "message": (
                        "Couldn't read the release list. The repository is private, "
                        "so checking needs either the repo made public, or a GitHub "
                        "token with 'repo' scope added in Settings.\n\n"
                        "You can always open the Releases page in a browser, where "
                        "your normal GitHub login applies.")}
        return {"state": "error", "latest": None, "url": RELEASES_URL,
                "message": f"GitHub returned {e.code}."}
    except Exception as e:
        hint = net.describe_ssl_error(e)
        return {"state": "error", "latest": None, "url": RELEASES_URL,
                "message": hint or f"Couldn't reach GitHub: {e}"}

    settings.set("last_update_check", time.time())
    latest = data.get("tag_name") or data.get("name") or ""
    url = data.get("html_url") or RELEASES_URL
    if is_newer(latest):
        return {"state": "update", "latest": latest, "url": url,
                "message": f"Sortero {latest} is available. You have {__version__}."}
    return {"state": "up-to-date", "latest": latest, "url": url,
            "message": f"Sortero {__version__} is the latest version."}


def due():
    return time.time() - float(settings.get("last_update_check", 0) or 0) > CHECK_INTERVAL

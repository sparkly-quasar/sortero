"""Check GitHub Releases for a newer Sortero.

Two channels. Stable asks GitHub for the latest release, which never includes
a pre-release. Beta (Settings: "Offer beta builds", saved as updates_beta)
looks through recent releases, pre-releases too, and offers the newest, so
a beta tester still gets the final release once it's out: 0.34.0 is newer
than 0.34.0-beta.2. A tag with a suffix (v0.34.0-beta.1) is what the release
workflow publishes as a pre-release.

While the repo is private the releases API needs a token, so a plain check gets
a 404. That is reported as a clear, actionable state rather than a scary error:
either make the repo public, or paste a token with `repo` scope into Settings.
"""
import json, re, time, urllib.error, urllib.request

from . import settings, net
from .version import __version__

REPO = "sparkly-quasar/sortero"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
LIST_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_URL = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = 24 * 3600


VERSION = re.compile(r"(\d+(?:\.\d+){0,2})(?:[-.]?(alpha|beta|rc|a|b)\.?(\d*))?", re.I)


STAGES = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}


def parse(v):
    """'v1.2.3' -> (1, 2, 3, 1, 0, 0), 'v1.2.3-beta.4' -> (1, 2, 3, 0, 1, 4): a
    pre-release sorts before its release, alpha before beta before rc. Unknown
    shapes sort lowest."""
    m = VERSION.match((v or "").strip().lstrip("vV"))
    if not m:
        return (0,)
    nums = [int(n) for n in m.group(1).split(".")] + [0, 0]
    if m.group(2):
        return (*nums[:3], 0, STAGES[m.group(2).lower()], int(m.group(3) or 0))
    return (*nums[:3], 1, 0, 0)


def is_prerelease(v):
    p = parse(v)
    return len(p) == 6 and p[3] == 0


def is_newer(remote, local=None):
    # Looked up when called, not when defined, so the tests can stand in a version.
    return parse(remote) > parse(local or __version__)


BETA_ABOUT = ("Beta builds have new features before they're released, for people happy "
              "to try them and say what goes wrong. They can have bugs.\n\n"
              "Sortero will offer each new beta as an update, and the final release "
              "when it's out. Before a gig, bring a stick you've checked with a release "
              "build too.")


def beta():
    return bool(settings.get("updates_beta"))


def beta_undecided():
    """Never asked about betas: Sortero asks once, on a launch after updating."""
    return settings.get("updates_beta") is None


def _newest(releases):
    """The newest published release in GitHub's list, pre-releases included."""
    published = [r for r in releases if isinstance(r, dict) and not r.get("draft")]
    return max(published, key=lambda r: parse(r.get("tag_name") or r.get("name")),
               default={})


def check(token=None, timeout=15, include_beta=None):
    """Return a dict describing the outcome.

    {"state": "up-to-date"|"update"|"private"|"error",
     "latest": str|None, "url": str, "message": str}
    """
    include_beta = beta() if include_beta is None else include_beta
    token = (token or settings.get("github_token") or "").strip()
    req = urllib.request.Request(LIST_API if include_beta else API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Sortero/{__version__}"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with net.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        if include_beta:
            data = _newest(data if isinstance(data, list) else [])
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
    assets = data.get("assets") or []
    if is_newer(latest):
        kind = (" It's a beta: new features to try before they're released, and it "
                "may have bugs.") if is_prerelease(latest) else ""
        return {"state": "update", "latest": latest, "url": url, "assets": assets,
                "notes": (data.get("body") or "").strip(),
                "message": f"Sortero {latest} is available. You have {__version__}.{kind}"}
    return {"state": "up-to-date", "latest": latest, "url": url, "assets": assets,
            "message": f"Sortero {__version__} is the latest version."}


def due():
    return time.time() - float(settings.get("last_update_check", 0) or 0) > CHECK_INTERVAL

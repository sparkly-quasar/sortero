"""Sortero Pro licences.

Sortero is free and open source; Sortero Pro is the ready-to-run app and its
one-click updates. A licence key is what the updater presents to the Sortero
server to get a new build.

A licence key is a small signed statement: what kind of licence it is and the
Stripe purchase or subscription it came from. Sortero checks the signature
against PUBLIC_KEY, so a one-time or gift licence needs no network, and nobody
can make a key without the private signing key, which never ships with the app.

Subscription keys are confirmed with the Sortero server every few days. The
server answers with its own signed note saying how long the subscription is paid
up for, so a faked answer doesn't work either, and Sortero keeps working offline
for a grace period past that date.

    SRT1.<payload>.<signature>   licence key         {"v", "k", "id", "t"}
    SRS1.<payload>.<signature>   subscription status {"v", "id", "until", "active"}

Each signature covers a label plus the payload bytes, so a status can never be
passed off as a key. server/worker.js makes both; tools/licence_admin.py makes
gift keys.
"""
import base64, json, sys, time, urllib.error, urllib.request

from . import ed25519, net, settings, store

PUBLIC_KEY = "b395b5d7b45fb5f3259b7221625e8081a88021677d18a868fa45415877bdb73d"          # hex; tools/licence_admin.py genkey --install fills it in
GRACE = 14 * 86400       # how long a subscription keeps working without a check
CHECK_EVERY = 3 * 86400
KEY_PREFIX, STATUS_PREFIX = "SRT1", "SRS1"
KEY_LABEL, STATUS_LABEL = b"sortero-licence:", b"sortero-status:"
KINDS = {"life": "one-time licence", "sub": "subscription", "gift": "gift licence"}
BUY_PREFIXES = ("https://buy.stripe.com/",)


class LicenceError(Exception):
    pass


def _b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def normalise(text):
    """Keys get pasted with line breaks and spaces in them; none are meaningful."""
    return "".join((text or "").split())


def seal(seed, payload, prefix=KEY_PREFIX, label=KEY_LABEL):
    """Sign a payload. Only the author's tool and the tests hold a seed."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return f"{prefix}.{_b64e(raw)}.{_b64e(ed25519.sign(seed, label + raw))}"


def _open(token, prefix, label):
    """A signed token's payload, or None if it isn't genuine."""
    try:
        head, body, sig = normalise(token).split(".")
        if head != prefix or not PUBLIC_KEY:
            return None
        raw = _b64d(body)
        if not ed25519.verify(bytes.fromhex(PUBLIC_KEY), label + raw, _b64d(sig)):
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def read_key(key):
    data = _open(key or "", KEY_PREFIX, KEY_LABEL)
    if not data or data.get("v") != 1 or data.get("k") not in KINDS or not data.get("id"):
        return None
    return data


class Status:
    def __init__(self, pro, kind=None, until=None, note=""):
        self.pro, self.kind, self.until, self.note = pro, kind, until, note

    def __repr__(self):
        return f"Status(pro={self.pro}, kind={self.kind}, until={self.until}, note={self.note!r})"


def status(now=None):
    now = time.time() if now is None else now
    key = settings.get("licence_key")
    if not key:
        return Status(False)
    lic = read_key(key)
    if not lic:
        return Status(False, note="The saved licence key isn't valid.")
    if lic["k"] != "sub":
        return Status(True, lic["k"])

    unconfirmed = ("Sortero couldn't confirm your subscription recently. Connect to "
                   "the internet and choose Check subscription now.")
    st = _open(settings.get("licence_status") or "", STATUS_PREFIX, STATUS_LABEL)
    if st and st.get("id") == lic["id"]:
        until = float(st.get("until") or 0)
        if not st.get("active"):
            if now < until:            # cancelled, but paid up until then
                return Status(True, "sub", until)
            return Status(False, "sub", until, "Your subscription has ended.")
        if now < until + GRACE:
            return Status(True, "sub", until)
        return Status(False, "sub", until, unconfirmed)
    # never confirmed: a fresh purchase gets the grace period to reach the server
    if now < float(lic.get("t") or 0) + GRACE:
        return Status(True, "sub")
    return Status(False, "sub", None, unconfirmed)


def activate(key, check=True):
    key = normalise(key)
    lic = read_key(key)
    if not lic:
        raise LicenceError("That isn't a valid Sortero licence key. Copy the whole key, "
                           "starting with SRT1.")
    settings.set("licence_key", key)
    settings.set("licence_status", "")
    settings.set("licence_checked", 0)
    if lic["k"] == "sub" and check:
        try:
            refresh()
        except LicenceError:
            pass                        # the grace period covers an offline activation
    return status()


def deactivate():
    for k, v in (("licence_key", ""), ("licence_status", ""), ("licence_checked", 0)):
        settings.set(k, v)


def due():
    lic = read_key(settings.get("licence_key") or "")
    return bool(lic and lic["k"] == "sub"
                and time.time() - float(settings.get("licence_checked") or 0) > CHECK_EVERY)


def _post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json",
                                          "User-Agent": "Sortero"})
    with net.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def refresh(timeout=15):
    """Ask the server whether the subscription is paid up, and remember the answer."""
    key = settings.get("licence_key") or ""
    lic = read_key(key)
    if not lic or lic["k"] != "sub":
        return status()
    if not store.SERVER:
        raise LicenceError("This copy of Sortero doesn't know where to check subscriptions.")
    try:
        body = _post(store.SERVER.rstrip("/") + "/check", {"key": key}, timeout)
    except Exception as e:
        raise LicenceError(f"Couldn't reach the Sortero server ({e}).")
    token = body.get("status") if isinstance(body, dict) else None
    st = _open(token or "", STATUS_PREFIX, STATUS_LABEL)
    if not st or st.get("id") != lic["id"]:
        raise LicenceError("The server's answer couldn't be verified.")
    settings.set("licence_status", token)
    settings.set("licence_checked", time.time())
    return status()


def platform():
    return {"darwin": "mac", "win32": "windows"}.get(sys.platform, "linux")


def latest_build(timeout=20):
    """The newest ready-to-run build for this computer.

    {"version", "notes", "name", "size", "url"}; the url is a download link that
    expires within minutes, so use it straight away.
    """
    key = settings.get("licence_key") or ""
    if not read_key(key):
        raise LicenceError("One-click updates come with Sortero Pro. Add your licence "
                           "key first.")
    if not store.SERVER:
        raise LicenceError("This copy of Sortero doesn't know where to get updates.")
    try:
        body = _post(store.SERVER.rstrip("/") + "/update",
                     {"key": key, "platform": platform()}, timeout)
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read().decode()).get("error")
        except Exception:
            reason = None
        raise LicenceError(reason or f"The Sortero server answered {e.code}.")
    except Exception as e:
        raise LicenceError(f"Couldn't reach the Sortero server ({e}).")
    url = body.get("url") if isinstance(body, dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise LicenceError("The Sortero server didn't send a download.")
    return body


def downloads_page():
    return store.SERVER.rstrip("/") + "/downloads" if store.SERVER else ""


def buy_url_ok(url):
    return bool(url) and url.startswith(BUY_PREFIXES)

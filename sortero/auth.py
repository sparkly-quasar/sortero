"""OAuth 2.0 (Authorization Code + PKCE) for TIDAL and Spotify.

You log in on the provider's own website - Sortero never sees your password.
It only ever holds the tokens the provider hands back, and those live in the
macOS Keychain, not in a file in the repo.

PKCE is designed for public clients, so there is no client *secret* to protect;
the client ID alone is not sensitive.

Setup, once per provider:
  TIDAL   - https://developer.tidal.com  -> create an app
  Spotify - https://developer.spotify.com/dashboard -> create an app
  Add this exact redirect URI to the app:  http://127.0.0.1:8899/callback
  Copy the Client ID into Sortero.
"""
import base64, hashlib, http.server, json, os, secrets, socket, stat
import subprocess, threading, time, urllib.parse, urllib.request, urllib.error, webbrowser

from . import paths

REDIRECT_PORT = 8899
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
KEYCHAIN_ACCOUNT = "sortero"

try:                                   # optional; present in the packaged builds
    import keyring
except Exception:                      # pragma: no cover
    keyring = None

PROVIDERS = {
    "tidal": {
        "label": "TIDAL",
        "authorize": "https://login.tidal.com/authorize",
        "token": "https://auth.tidal.com/v1/oauth2/token",
        "api": "https://openapi.tidal.com/v2",
        "scopes": "playlists.read collection.read user.read",
        "console": "https://developer.tidal.com",
    },
    "spotify": {
        "label": "Spotify",
        "authorize": "https://accounts.spotify.com/authorize",
        "token": "https://accounts.spotify.com/api/token",
        "api": "https://api.spotify.com/v1",
        "scopes": "playlist-read-private playlist-read-collaborative",
        "console": "https://developer.spotify.com/dashboard",
    },
}


class AuthError(Exception):
    pass


# ------------------------------------------------------------------ keychain
def _service(provider):
    return f"sortero-{provider}"


def _fallback_file(provider):
    return os.path.join(paths.config_dir(), f"tokens-{provider}.json")


def save_tokens(provider, data):
    """Prefer the OS credential store; fall back to a user-only file."""
    blob = json.dumps(data)
    if keyring is not None:
        try:
            keyring.set_password(_service(provider), KEYCHAIN_ACCOUNT, blob)
            return
        except Exception:
            pass
    if paths.IS_MAC:
        r = subprocess.run(
            ["security", "add-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", _service(provider), "-w", blob, "-U"], capture_output=True)
        if r.returncode == 0:
            return
    fp = _fallback_file(provider)
    with open(fp, "w") as fh:
        fh.write(blob)
    try:
        os.chmod(fp, stat.S_IRUSR | stat.S_IWUSR)      # 0600
    except OSError:
        pass


def load_tokens(provider):
    if keyring is not None:
        try:
            blob = keyring.get_password(_service(provider), KEYCHAIN_ACCOUNT)
            if blob:
                return json.loads(blob)
        except Exception:
            pass
    if paths.IS_MAC:
        r = subprocess.run(
            ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", _service(provider), "-w"], capture_output=True, text=True)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout.strip())
            except json.JSONDecodeError:
                pass
    try:
        with open(_fallback_file(provider)) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def forget(provider):
    if keyring is not None:
        try:
            keyring.delete_password(_service(provider), KEYCHAIN_ACCOUNT)
        except Exception:
            pass
    if paths.IS_MAC:
        subprocess.run(["security", "delete-generic-password", "-a", KEYCHAIN_ACCOUNT,
                        "-s", _service(provider)], capture_output=True)
    try:
        os.remove(_fallback_file(provider))
    except OSError:
        pass


def is_connected(provider):
    return load_tokens(provider) is not None


# ---------------------------------------------------------------------- PKCE
def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class _Catcher(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        q = urllib.parse.urlparse(self.path)
        if not q.path.startswith("/callback"):
            self.send_response(404); self.end_headers(); return
        params = urllib.parse.parse_qs(q.query)
        type(self).result = {k: v[0] for k, v in params.items()}
        ok = "code" in params
        body = (b"<html><body style='font:16px -apple-system;padding:3em'>"
                + (b"<h2>Connected.</h2><p>You can close this tab and go back to Sortero.</p>"
                   if ok else
                   b"<h2>Authorisation failed.</h2><p>Go back to Sortero and try again.</p>")
                + b"</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _CatchServer(http.server.HTTPServer):
    # so a retry right after a failed attempt doesn't hit TIME_WAIT
    allow_reuse_address = True


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise AuthError(f"{e.code} from {urllib.parse.urlparse(url).netloc}: {detail}")


def connect(provider, client_id, timeout=180, log=print):
    """Run the browser login. Returns the token dict, and stores it in Keychain."""
    if provider not in PROVIDERS:
        raise AuthError(f"unknown provider {provider}")
    cfg = PROVIDERS[provider]
    client_id = (client_id or "").strip()
    if not client_id:
        raise AuthError(f"Enter your {cfg['label']} Client ID first "
                        f"(create an app at {cfg['console']}).")

    # fail early if the port is taken, rather than after the browser opens
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", REDIRECT_PORT))
        except OSError:
            raise AuthError(f"Port {REDIRECT_PORT} is in use. Close whatever is "
                            f"using it and try again.")

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": cfg["scopes"],
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    url = cfg["authorize"] + "?" + urllib.parse.urlencode(params)

    _Catcher.result = None
    server = _CatchServer(("127.0.0.1", REDIRECT_PORT), _Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"Opening {cfg['label']} sign-in in your browser…")
    webbrowser.open(url)

    deadline = time.time() + timeout
    try:
        while _Catcher.result is None and time.time() < deadline:
            time.sleep(0.25)
    finally:
        # shutdown() only stops serve_forever; without server_close() the socket
        # stays bound and a retry fails with "port in use".
        server.shutdown()
        server.server_close()

    res = _Catcher.result
    if not res:
        raise AuthError("Timed out waiting for the browser sign-in.")
    if res.get("state") != state:
        raise AuthError("State mismatch - aborting for safety.")
    if "code" not in res:
        raise AuthError(f"{cfg['label']} said: {res.get('error_description') or res.get('error')}")

    tok = _post_form(cfg["token"], {
        "grant_type": "authorization_code",
        "code": res["code"],
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
    })
    tok["client_id"] = client_id
    tok["obtained_at"] = time.time()
    save_tokens(provider, tok)
    log(f"Connected to {cfg['label']}.")
    return tok


def access_token(provider, log=print):
    """A currently-valid access token, refreshing when needed."""
    tok = load_tokens(provider)
    if not tok:
        raise AuthError(f"Not connected to {PROVIDERS[provider]['label']} yet.")
    age = time.time() - tok.get("obtained_at", 0)
    if age < tok.get("expires_in", 3600) - 120:
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise AuthError(f"{PROVIDERS[provider]['label']} session expired - reconnect.")
    new = _post_form(PROVIDERS[provider]["token"], {
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": tok["client_id"],
    })
    new.setdefault("refresh_token", tok["refresh_token"])
    new["client_id"] = tok["client_id"]
    new["obtained_at"] = time.time()
    save_tokens(provider, new)
    return new["access_token"]


def api_get(provider, path, params=None, log=print):
    cfg = PROVIDERS[provider]
    url = path if path.startswith("http") else cfg["api"] + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token(provider, log)}",
        "Accept": "application/vnd.api+json, application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AuthError(f"{cfg['label']} API {e.code}: "
                        f"{e.read().decode('utf-8','replace')[:300]}")

#!/usr/bin/env python3
"""Make and check Sortero Pro licence keys. For Sortero's author only.

    python tools/licence_admin.py genkey [--install]   create the signing key, once
    python tools/licence_admin.py gift [--note TEXT]   a Pro key that needs no payment
    python tools/licence_admin.py show KEY             what a key says, and if it's genuine

The signing key is the one real secret: anyone who has it can make licence keys.
It lives outside the repository, by default in ~/.config/sortero/, and the
server needs a copy (see server/README.md). Back it up somewhere safe, such as a
password manager. If it's lost, existing keys keep working but no new ones can
be made, and changing it means every existing key stops working.
"""
import argparse, os, re, secrets, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sortero import ed25519, licence  # noqa: E402

DEFAULT_KEY = os.path.expanduser("~/.config/sortero/licence-signing-key")


def load_seed(path):
    try:
        with open(path) as fh:
            seed = bytes.fromhex(fh.read().strip())
    except FileNotFoundError:
        sys.exit(f"No signing key at {path}. Run 'genkey' first, or pass --key.")
    if len(seed) != 32:
        sys.exit(f"{path} doesn't hold a valid signing key.")
    return seed


def install(public_hex):
    """Write the public key into the app and the server config."""
    for rel, pattern, line in (
            ("sortero/licence.py", r'^PUBLIC_KEY = "[0-9a-f]*"', f'PUBLIC_KEY = "{public_hex}"'),
            ("server/wrangler.toml", r'^LICENCE_PUBLIC_KEY = "[0-9a-f]*"',
             f'LICENCE_PUBLIC_KEY = "{public_hex}"')):
        path = os.path.join(ROOT, rel)
        with open(path) as fh:
            text = fh.read()
        new, n = re.subn(pattern, line, text, count=1, flags=re.M)
        if n != 1:
            sys.exit(f"Couldn't find where the public key goes in {rel}.")
        with open(path, "w") as fh:
            fh.write(new)
        print(f"  wrote the public key into {rel}")


def cmd_genkey(args):
    path = args.key
    if os.path.exists(path):
        sys.exit(f"{path} already exists. Not replacing it: every key made with it "
                 "would stop working.")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    seed = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(seed.hex() + "\n")
    public_hex = ed25519.public_key(seed).hex()
    print(f"Signing key saved to {path}, readable only by you. Back it up somewhere safe.")
    print(f"Public key: {public_hex}")
    if args.install:
        install(public_hex)
    else:
        print("Run again with --install to write the public key into the app and server.")


def cmd_gift(args):
    seed = load_seed(args.key)
    public_hex = ed25519.public_key(seed).hex()
    if public_hex != licence.PUBLIC_KEY:
        sys.exit("This signing key doesn't match PUBLIC_KEY in sortero/licence.py, so "
                 "keys made with it wouldn't work in the app.")
    payload = {"v": 1, "k": "gift", "id": "gift-" + secrets.token_hex(6),
               "t": int(time.time())}
    if args.note:
        payload["n"] = args.note[:60]
    print(licence.seal(seed, payload))


def cmd_show(args):
    data = licence.read_key(args.licence)
    if not data:
        sys.exit("Not a genuine Sortero licence key (or PUBLIC_KEY isn't set).")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("t") or 0))
    print(f"genuine {licence.KINDS[data['k']]}: id {data['id']}, made {when}"
          + (f", note: {data['n']}" if data.get("n") else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key", default=DEFAULT_KEY, help="signing key file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("genkey", help="create the signing key")
    g.add_argument("--install", action="store_true",
                   help="write the public key into sortero/licence.py and server/wrangler.toml")
    g = sub.add_parser("gift", help="make a Pro key that needs no payment")
    g.add_argument("--note", help="who it's for, stored in the key")
    g = sub.add_parser("show", help="check a key")
    g.add_argument("licence")
    args = ap.parse_args()
    {"genkey": cmd_genkey, "gift": cmd_gift, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    main()

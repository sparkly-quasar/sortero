#!/usr/bin/env python3
"""Generate Sortero app icons with no third-party imaging dependencies.

Produces, next to this file:
  icon_1024.png   - source art (all platforms, and the Linux icon)
  Sortero.icns    - macOS   (needs iconutil/sips, so macOS only)
  Sortero.ico     - Windows (written here in pure Python)

The mark is a rounded tile with three sorted bars, which reads as both 'sort'
and 'levels'. It is drawn parametrically, so every size is rendered at its own
resolution rather than resampled.
"""
import math, os, struct, subprocess, sys, zlib

BG_TOP = (28, 30, 38)
BG_BOT = (16, 17, 22)
BARS = [
    (0.30, 0.42, (255, 92, 122)),    # y-centre frac, width frac, colour
    (0.50, 0.62, (255, 176, 74)),
    (0.70, 0.80, (86, 214, 178)),
]
CORNER_F, BAR_H_F, BAR_X_F = 0.22, 0.105, 0.13
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def _rounded_alpha(x, y, w, h, r, samples=2):
    acc = 0.0
    step = 1.0 / samples
    for i in range(samples):
        for j in range(samples):
            px, py = x + (i + 0.5) * step, y + (j + 0.5) * step
            if px < 0 or py < 0 or px > w or py > h:
                continue
            cx = min(max(px, r), w - r)
            cy = min(max(py, r), h - r)
            d = math.hypot(px - cx, py - cy)
            acc += 1.0 if d <= r else max(0.0, 1.0 - (d - r))
    return acc / (samples * samples)


def render(S):
    """Raw PNG scanlines (filter byte + RGBA) for an S x S icon."""
    corner, bar_h, bar_x = CORNER_F * S, BAR_H_F * S, BAR_X_F * S
    rows = []
    for y in range(S):
        t = y / max(S - 1, 1)
        bg = tuple(int(round(a + (b - a) * t)) for a, b in zip(BG_TOP, BG_BOT))
        row = bytearray([0])
        for x in range(S):
            a = _rounded_alpha(x, y, S, S, corner)
            if a <= 0:
                row += b"\0\0\0\0"
                continue
            px = bg
            for cy_frac, w_frac, colour in BARS:
                cy = cy_frac * S
                if abs(y - cy) <= bar_h / 2 + 1:
                    bx1 = bar_x + w_frac * (S - 2 * bar_x)
                    ba = _rounded_alpha(x - bar_x, y - (cy - bar_h / 2),
                                        bx1 - bar_x, bar_h, bar_h / 2)
                    if ba > 0:
                        px = tuple(int(round(d + (s - d) * ba))
                                   for d, s in zip(px, colour))
            row += bytes(px) + bytes((int(round(a * 255)),))
        rows.append(bytes(row))
    return b"".join(rows)


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_bytes(S, raw=None):
    raw = render(S) if raw is None else raw
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def write_png(path, S):
    data = png_bytes(S)
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def write_ico(path, sizes=ICO_SIZES):
    """ICO with PNG-compressed entries (supported since Windows Vista)."""
    images = [(s, png_bytes(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as fh:
        fh.write(header + entries + blobs)


def write_icns(out_dir, master_png):
    """macOS .icns via the system iconutil."""
    iconset = os.path.join(out_dir, "Sortero.iconset")
    os.makedirs(iconset, exist_ok=True)
    for size in ICNS_SIZES:
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = size * scale
            if px > 1024:
                continue
            subprocess.run(["sips", "-z", str(px), str(px), master_png, "--out",
                            os.path.join(iconset, f"icon_{size}x{size}{suffix}.png")],
                           check=True, capture_output=True)
    icns = os.path.join(out_dir, "Sortero.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    return icns


def main():
    out = os.path.dirname(os.path.abspath(__file__))
    master = os.path.join(out, "icon_1024.png")
    print("rendering master…")
    write_png(master, 1024)
    print("  ->", master)

    print("rendering .ico…")
    write_ico(os.path.join(out, "Sortero.ico"))
    print("  ->", os.path.join(out, "Sortero.ico"))

    if sys.platform == "darwin":
        print("rendering .icns…")
        print("  ->", write_icns(out, master))
    else:
        print("skipping .icns (macOS only)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate Sortero.icns with no third-party imaging dependencies.

Draws a rounded-square tile with three sorted bars (short -> long), which reads
as both 'sort' and 'levels'. Writes a PNG by hand, then hands off to sips and
iconutil to produce the .icns.
"""
import math, os, struct, subprocess, sys, zlib

S = 1024
BG_TOP = (28, 30, 38)
BG_BOT = (16, 17, 22)
BARS = [
    (0.30, 0.42, (255, 92, 122)),    # y-centre, width fraction, colour
    (0.50, 0.62, (255, 176, 74)),
    (0.70, 0.80, (86, 214, 178)),
]
CORNER = 0.22 * S
BAR_H = 0.105 * S
BAR_X = 0.13 * S


def rounded_alpha(x, y, w, h, r):
    """Coverage 0..1 of a rounded rect, sampled 2x2 for smooth edges."""
    acc = 0.0
    for dx in (0.25, 0.75):
        for dy in (0.25, 0.75):
            px, py = x + dx, y + dy
            cx = min(max(px, r), w - r)
            cy = min(max(py, r), h - r)
            d = math.hypot(px - cx, py - cy)
            if px < 0 or py < 0 or px > w or py > h:
                continue
            acc += 1.0 if d <= r else max(0.0, 1.0 - (d - r))
    return acc / 4.0


def blend(dst, src, a):
    return tuple(int(round(d + (s - d) * a)) for d, s in zip(dst, src))


def render():
    rows = []
    for y in range(S):
        t = y / (S - 1)
        bg = tuple(int(round(a + (b - a) * t)) for a, b in zip(BG_TOP, BG_BOT))
        row = bytearray()
        row.append(0)                                   # PNG filter: none
        for x in range(S):
            a = rounded_alpha(x, y, S, S, CORNER)
            if a <= 0:
                row += bytes((0, 0, 0, 0))
                continue
            px = bg
            for cy_frac, w_frac, colour in BARS:
                cy = cy_frac * S
                if abs(y - cy) <= BAR_H / 2:
                    bx0, bx1 = BAR_X, BAR_X + w_frac * (S - 2 * BAR_X)
                    br = BAR_H / 2
                    ba = rounded_alpha(x - bx0, y - (cy - BAR_H / 2),
                                       bx1 - bx0, BAR_H, br)
                    if ba > 0:
                        px = blend(px, colour, ba)
            row += bytes(px) + bytes((int(round(a * 255)),))
        rows.append(bytes(row))
    return b"".join(rows)


def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path, raw):
    ihdr = struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0)   # 8-bit RGBA
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def main():
    out = os.path.dirname(os.path.abspath(__file__))
    master = os.path.join(out, "icon_1024.png")
    print("rendering…")
    write_png(master, render())

    iconset = os.path.join(out, "Sortero.iconset")
    os.makedirs(iconset, exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512, 1024):
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = size * scale
            if px > 1024:
                continue
            name = f"icon_{size}x{size}{suffix}.png"
            subprocess.run(["sips", "-z", str(px), str(px), master,
                            "--out", os.path.join(iconset, name)],
                           check=True, capture_output=True)
    icns = os.path.join(out, "Sortero.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print("wrote", icns)


if __name__ == "__main__":
    main()

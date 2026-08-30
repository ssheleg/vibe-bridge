#!/usr/bin/env python3
"""Draw the app icon — a span between two piers, on the workbench accent.

Stdlib only, deliberately: the icon is a build input, and a build input that
needs Pillow is a dependency the shipped app pays for and never uses.

The mark is a suspension bridge: a deck, two towers, and the cable sagging
between them. Read at 16px before anything else was decided — the first
attempt put an arch ABOVE the piers and came out as a gateway, and a mark
that has to be explained is not working. The consent story is told by the
panel, not by a sixteen-pixel glyph; an earlier version notched a gap into
the deck to mean "the gate" and it simply read as damage.

Palette is `docs/design/ui.md` (style pack: workbench) — `--accent` #2f6feb,
white deck. No new colour is invented here.

    python scripts/make_icon.py        # → packaging/vibe-bridge.icns + PNGs
"""
from __future__ import annotations

import math
import pathlib
import struct
import subprocess
import sys
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging"

ACCENT = (0x2F, 0x6F, 0xEB)          # --accent, workbench
ACCENT_TOP = (0x5B, 0x93, 0xFF)      # lighter end of the tile gradient
DECK = (0xFF, 0xFF, 0xFF)

SS = 4                                # supersampling factor
SIZES = (16, 32, 64, 128, 180, 192, 256, 512, 1024)
#: The PWA serves these from the package itself, so the phone's home-screen
#: tile is the same mark as the app icon rather than a flat placeholder.
WEBUI_SIZES = (180, 192, 512)


def _rounded_rect_mask(n: int, inset: float, radius: float):
    """macOS-ish squircle coverage mask, 0.0–1.0 per pixel."""
    lo, hi = inset, n - inset
    mask = [[0.0] * n for _ in range(n)]
    for y in range(n):
        for x in range(n):
            px, py = x + 0.5, y + 0.5
            if not (lo <= px <= hi and lo <= py <= hi):
                continue
            # distance into the corner arcs
            dx = max(lo + radius - px, px - (hi - radius), 0.0)
            dy = max(lo + radius - py, py - (hi - radius), 0.0)
            mask[y][x] = 1.0 if math.hypot(dx, dy) <= radius else 0.0
    return mask


def _draw(n: int) -> list[list[tuple[int, int, int, int]]]:
    """One RGBA frame at `n` px, already supersampled by the caller."""
    inset = n * 0.085
    radius = n * 0.225
    tile = _rounded_rect_mask(n, inset, radius)

    # Everything proportional, so 16px and 1024px are the same drawing.
    stroke = n * 0.070
    deck_y = n * 0.630                # roadway
    deck_x0, deck_x1 = n * 0.150, n * 0.850
    tower_x = (n * 0.330, n * 0.670)
    tower_top = n * 0.280
    mid = n * 0.500
    half = tower_x[1] - mid
    sag = n * 0.150                   # cable dip at mid-span

    px: list[list[tuple[int, int, int, int]]] = []
    for y in range(n):
        row = []
        for x in range(n):
            cover = tile[y][x]
            if cover <= 0.0:
                row.append((0, 0, 0, 0))
                continue

            t = y / max(n - 1, 1)     # vertical gradient across the tile
            bg = tuple(int(ACCENT_TOP[i] + (ACCENT[i] - ACCENT_TOP[i]) * t)
                       for i in range(3))

            cx, cy = x + 0.5, y + 0.5
            ink = False

            # the deck
            if deck_x0 <= cx <= deck_x1 and abs(cy - deck_y) <= stroke / 2:
                ink = True

            # the towers, standing on the deck
            for tx in tower_x:
                if (abs(cx - tx) <= stroke / 2
                        and tower_top <= cy <= deck_y + stroke / 2):
                    ink = True

            # main cable: sagging between the towers…
            if tower_x[0] <= cx <= tower_x[1]:
                cable = tower_top + sag * (1 - ((cx - mid) / half) ** 2)
                if abs(cy - cable) <= stroke / 2:
                    ink = True
            # …and running straight from each tower top down to the deck ends
            for tx, ex in ((tower_x[0], deck_x0), (tower_x[1], deck_x1)):
                lo, hi = min(tx, ex), max(tx, ex)
                if lo <= cx <= hi:
                    k = (cx - tx) / (ex - tx)
                    stay = tower_top + (deck_y - tower_top) * k
                    if abs(cy - stay) <= stroke / 2:
                        ink = True

            colour = DECK if ink else bg
            row.append((*colour, int(round(255 * cover))))
        px.append(row)
    return px


def _downsample(src, n: int, factor: int):
    out = []
    for y in range(n):
        row = []
        for x in range(n):
            r = g = b = a = 0
            for dy in range(factor):
                for dx in range(factor):
                    pr, pg, pb, pa = src[y * factor + dy][x * factor + dx]
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            if a == 0:
                row.append((0, 0, 0, 0))
            else:
                row.append((r // a, g // a, b // a, a // (factor * factor)))
        out.append(row)
    return out


def _png(pixels, n: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(
        b"\x00" + b"".join(bytes(p) for p in row) for row in pixels)
    ihdr = struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)     # 6 = RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> int:
    OUT.mkdir(exist_ok=True)
    iconset = OUT / "vibe-bridge.iconset"
    iconset.mkdir(exist_ok=True)

    rendered: dict[int, bytes] = {}
    for n in SIZES:
        pixels = _downsample(_draw(n * SS), n, SS)
        rendered[n] = _png(pixels, n)
        (OUT / f"vibe-bridge-{n}.png").write_bytes(rendered[n])
        print(f"  {n}×{n}")

    (OUT / "vibe-bridge.png").write_bytes(rendered[512])

    webui = ROOT / "vibebridge" / "webui"
    for n in WEBUI_SIZES:
        (webui / f"icon-{n}.png").write_bytes(rendered[n])
    print(f"  → PWA-плитки в {webui.relative_to(ROOT)}")

    # .iconset names macOS expects, including the @2x pairs.
    for base in (16, 32, 128, 256, 512):
        (iconset / f"icon_{base}x{base}.png").write_bytes(rendered[base])
        double = base * 2
        if double in rendered:
            (iconset / f"icon_{base}x{base}@2x.png").write_bytes(
                rendered[double])

    icns = OUT / "vibe-bridge.icns"
    result = subprocess.run(["iconutil", "-c", "icns", str(iconset),
                             "-o", str(icns)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"iconutil: {result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"→ {icns} ({icns.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

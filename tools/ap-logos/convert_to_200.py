#!/usr/bin/env python3
"""Crop and resize logos to 200x200 with white background.

Usage:
    # Convert all sports logos
    python convert_to_200.py

    # Convert a single logo
    python convert_to_200.py --logo nhl/nhl_tor.png

    # Convert all logos in one league
    python convert_to_200.py --league nba

    # Custom size
    python convert_to_200.py --size 128

    # Custom input/output dirs
    python convert_to_200.py --input output/logos --output output/logos_200
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


LEAGUES = ["nba", "nfl", "nhl", "mlb"]


def crop_and_resize(src: Path, dst: Path, size: int = 200) -> None:
    """Tight-crop a logo, fit into size x size with white background."""
    img = Image.open(src).convert("RGBA")

    # Alpha-based bounding box
    bbox = img.getbbox()
    if not bbox:
        print(f"  SKIP {src.name}: empty image")
        return

    # Content-based crop: skip transparent + near-white pixels
    pixels = img.load()
    w, h = img.size
    min_x, min_y, max_x, max_y = w, h, 0, 0

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a < 10 or (r > 245 and g > 245 and b > 245):
                continue
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

    if max_x <= min_x or max_y <= min_y:
        min_x, min_y, max_x, max_y = bbox

    # Union with alpha bbox so nothing gets clipped
    bx0, by0, bx1, by1 = bbox
    min_x = max(0, min(min_x, bx0))
    min_y = max(0, min(min_y, by0))
    max_x = min(w, max(max_x + 1, bx1))
    max_y = min(h, max(max_y + 1, by1))

    cropped = img.crop((min_x, min_y, max_x, max_y))

    # Fit into target size with ~4% padding, no stretching
    cw, ch = cropped.size
    usable = int(size * 0.96)
    scale = min(usable / cw, usable / ch)
    new_w = round(cw * scale)
    new_h = round(ch * scale)
    resized = cropped.resize((new_w, new_h), Image.LANCZOS)

    # White background, centered
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    offset_x = (size - new_w) // 2
    offset_y = (size - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y), resized)

    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(dst, "PNG")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop and resize logos to square with white bg")
    parser.add_argument("--input", default="output/logos", help="Source logos directory")
    parser.add_argument("--output", default="output/logos_200", help="Output directory")
    parser.add_argument("--size", type=int, default=200, help="Target size in pixels (default: 200)")
    parser.add_argument("--league", choices=LEAGUES, help="Process only this league")
    parser.add_argument("--logo", help="Process a single logo (e.g. nba/nba_atl.png)")
    args = parser.parse_args()

    input_base = Path(args.input)
    output_base = Path(args.output)

    # Single logo mode
    if args.logo:
        src = input_base / args.logo
        dst = output_base / args.logo
        if not src.exists():
            print(f"Not found: {src}")
            return
        crop_and_resize(src, dst, args.size)
        print(f"  {src.name} -> {dst}")
        return

    # Batch mode
    leagues = [args.league] if args.league else LEAGUES
    count = 0

    for league in leagues:
        src_dir = input_base / league
        if not src_dir.exists():
            print(f"  SKIP {league}/: directory not found")
            continue
        for src in sorted(src_dir.glob("*.png")):
            dst = output_base / league / src.name
            crop_and_resize(src, dst, args.size)
            count += 1

    print(f"Processed {count} logos -> {output_base}/")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render decoded DST stitch paths to a bitmap for visual regression checks."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))

from server import inspect_dst  # noqa: E402


def render(dst_path: Path, output: Path, *, pixels_per_mm: int = 16) -> None:
    operations = inspect_dst(dst_path.read_bytes())
    points = [operation for operation in operations if operation["kind"] in {"stitch", "jump"}]
    if not points:
        raise ValueError("DST contains no stitchable points")
    margin = 4 * pixels_per_mm
    minimum_x = min(point["x"] for point in points)
    maximum_x = max(point["x"] for point in points)
    minimum_y = min(point["y"] for point in points)
    maximum_y = max(point["y"] for point in points)
    image = Image.new("RGB", (
        round((maximum_x - minimum_x) * pixels_per_mm + margin * 2),
        round((maximum_y - minimum_y) * pixels_per_mm + margin * 2),
    ), "white")
    draw = ImageDraw.Draw(image)

    def project(point: dict) -> tuple[float, float]:
        return (
            margin + (point["x"] - minimum_x) * pixels_per_mm,
            margin + (point["y"] - minimum_y) * pixels_per_mm,
        )

    previous = None
    for operation in operations:
        if operation["kind"] == "color":
            previous = None
        elif operation["kind"] == "jump":
            previous = operation
        elif operation["kind"] == "stitch":
            if previous:
                # Approximate 40 wt thread coverage in physical space. This is
                # intentionally distinct from the UI's 2 px display stroke.
                draw.line((*project(previous), *project(operation)), fill="black", width=max(2, round(pixels_per_mm * 0.42)))
            previous = operation
    image.save(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dst", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pixels-per-mm", type=int, default=16)
    options = parser.parse_args()
    render(options.dst, options.output, pixels_per_mm=options.pixels_per_mm)

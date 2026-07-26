#!/usr/bin/env python3
"""Generate repeatable DST and annotated decision-bracket fixture artifacts."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE = ROOT / "prototype"
sys.path.insert(0, str(PROTOTYPE))

from digitizer import digitize_payload  # noqa: E402
from drc_policy import evaluate_drc  # noqa: E402
from server import as_dst, inspect_dst  # noqa: E402
from training_data import build_training_record  # noqa: E402
from vector_document import (  # noqa: E402
    VectorDocument,
    normalized_satin_rails,
)

DEFAULT_MANIFEST = ROOT / "fixtures" / "bracket-artwork" / "fixtures.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "bracket-fixtures"
STATUS_COLORS = {
    "pass": "#3c8f7a",
    "near_limit": "#c7a037",
    "warning": "#df9231",
    "fallback": "#c65f39",
    "blocker": "#b83232",
}
STATUS_FILLS = {
    "pass": "#d9ece6",
    "near_limit": "#f0e8c8",
    "warning": "#f3dfc8",
    "fallback": "#eed4ca",
    "blocker": "#f2cccc",
}


def slug(value: str, *, maximum: int = 72) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (result or "unnamed")[:maximum].rstrip("-")


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def drc_disposition(payload: dict) -> tuple[str, dict]:
    evaluation = evaluate_drc(payload)
    evidence = evaluation["evidence"]
    return evaluation["status"], {
        "policy_version": evaluation["policy_version"],
        "export_allowed": evaluation["export_allowed"],
        "override_required": evaluation["override_required"],
        "semantic_violations": evaluation["counts"]["semantic_violations"],
        "budget_violations": evaluation["counts"]["budget_violations"],
        "findings_by_severity": evidence["findings_by_severity"],
        "brackets_by_status": evidence["brackets_by_status"],
        "stitch_memory_exceeded": evidence["stitch_memory_exceeded"],
        "hard_blockers": evaluation["hard_blockers"],
        "conditional_blockers": evaluation["conditional_blockers"],
    }


def load_fixtures(path: Path, selected: set[str]) -> list[dict]:
    payload = json.loads(path.read_text())
    defaults = payload.get("defaults", {})
    fixtures = []
    for source in payload.get("fixtures", []):
        fixture = {**defaults, **source}
        fixture["name"] = slug(fixture["name"])
        if selected and fixture["name"] not in selected:
            continue
        fixture["source"] = (ROOT / fixture["path"]).resolve()
        if not fixture["source"].is_file():
            raise FileNotFoundError(f"fixture source does not exist: {fixture['source']}")
        fixtures.append(fixture)
    if selected:
        missing = selected - {fixture["name"] for fixture in fixtures}
        if missing:
            raise ValueError(f"unknown fixture names: {', '.join(sorted(missing))}")
    return fixtures


def source_bytes(path: Path) -> tuple[bytes, str]:
    original = path.read_bytes()
    if path.suffix.lower() != ".svg":
        return original, path.suffix.lower().lstrip(".")
    renderer = shutil.which("rsvg-convert")
    if not renderer:
        raise RuntimeError("SVG fixtures require rsvg-convert")
    result = subprocess.run(
        [
            renderer,
            "--format=png",
            "--width=500",
            "--keep-aspect-ratio",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout, "svg-rasterized-png"


def project_factory(
    operations: list[dict],
    extra_points: list[tuple[float, float]],
    *,
    pixels_per_mm: int,
    header_height: int,
) -> tuple[Image.Image, ImageDraw.ImageDraw, callable]:
    points = [
        (float(operation["x"]), float(operation["y"]))
        for operation in operations
        if operation["kind"] in {"stitch", "jump"}
    ] + extra_points
    if not points:
        raise ValueError("plan contains no drawable coordinates")
    minimum_x = min(point[0] for point in points)
    maximum_x = max(point[0] for point in points)
    minimum_y = min(point[1] for point in points)
    maximum_y = max(point[1] for point in points)
    margin = 4 * pixels_per_mm
    image = Image.new(
        "RGB",
        (
            max(320, round((maximum_x - minimum_x) * pixels_per_mm + margin * 2)),
            max(220, round((maximum_y - minimum_y) * pixels_per_mm + margin * 2 + header_height)),
        ),
        "#f8f6f1",
    )
    draw = ImageDraw.Draw(image)

    def project(point: tuple[float, float]) -> tuple[float, float]:
        return (
            margin + (point[0] - minimum_x) * pixels_per_mm,
            header_height + margin + (point[1] - minimum_y) * pixels_per_mm,
        )

    return image, draw, project


def draw_decoded_dst(
    draw: ImageDraw.ImageDraw,
    operations: list[dict],
    project: callable,
    palette: list[str],
    *,
    pixels_per_mm: int,
) -> None:
    color_index = 0
    previous = None
    for operation in operations:
        kind = operation["kind"]
        if kind == "color":
            color_index += 1
            previous = None
        elif kind == "trim":
            previous = None
        elif kind == "jump":
            previous = operation
        elif kind == "stitch":
            if previous is not None:
                start = project((previous["x"], previous["y"]))
                end = project((operation["x"], operation["y"]))
                draw.line(
                    (*start, *end),
                    fill=palette[min(color_index, len(palette) - 1)] if palette else "#202727",
                    width=max(2, round(pixels_per_mm * .42)),
                )
            previous = operation


def bracket_text(bracket: dict) -> tuple[str, str, str]:
    observed = bracket.get("observed")
    if isinstance(observed, (int, float)):
        value = f"{observed:.3f} {bracket.get('unit', '')}".strip()
    elif isinstance(observed, dict):
        value = " · ".join(
            f"{key.replace('_', ' ')} {number:.3f}"
            for key, number in observed.items()
        )
        if bracket.get("unit"):
            value += f" {bracket['unit']}"
    else:
        value = "not measurable"
    title = (
        f"{bracket['status'].upper()} · "
        f"{bracket['object_id']} · {bracket['rule'].replace('_', ' ')}"
    )
    return title, value, bracket.get("message", "")


def interval_points(
    document: VectorDocument, bracket: dict,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    try:
        item = document.object(bracket["object_id"])
    except StopIteration:
        return [], []
    rails, _ = normalized_satin_rails(item)
    if len(rails) != 2 or not rails[0]:
        return [], []
    segment = bracket.get("segment") or {"start": 0.0, "end": 1.0}
    maximum = len(rails[0]) - 1
    start = max(0, min(maximum, round(float(segment["start"]) * maximum)))
    end = max(start, min(maximum, round(float(segment["end"]) * maximum)))
    if start == end and maximum:
        start = max(0, start - 1)
        end = min(maximum, end + 1)
    return rails[0][start:end + 1], rails[1][start:end + 1]


def render_plan(
    dst_operations: list[dict],
    palette: list[str],
    output: Path,
    *,
    pixels_per_mm: int,
    bracket: dict | None = None,
    document: VectorDocument | None = None,
) -> None:
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    if bracket and document:
        left, right = interval_points(document, bracket)
    image, draw, project = project_factory(
        dst_operations,
        left + right,
        pixels_per_mm=pixels_per_mm,
        header_height=94 if bracket else 0,
    )
    draw_decoded_dst(
        draw, dst_operations, project, palette,
        pixels_per_mm=pixels_per_mm,
    )
    if bracket:
        color = STATUS_COLORS.get(bracket["status"], "#c65f39")
        draw.rectangle((0, 0, image.width, 94), fill="#172a2d")
        title, value, message = bracket_text(bracket)
        font = ImageFont.load_default()
        draw.text((14, 11), title, fill=color, font=font)
        draw.text((14, 31), value, fill="#f4e7bd", font=font)
        for line_number, line in enumerate(textwrap.wrap(message, width=105)[:3]):
            draw.text((14, 51 + line_number * 13), line, fill="#d5e8e5", font=font)
        if left and right:
            polygon = [project(point) for point in left] + [
                project(point) for point in reversed(right)
            ]
            if len(polygon) >= 3:
                draw.polygon(
                    polygon,
                    fill=STATUS_FILLS.get(bracket["status"], "#eed4ca"),
                )
            draw.line([project(point) for point in left], fill="#eebd54", width=2)
            draw.line([project(point) for point in right], fill="#6ed0c9", width=2)
            for left_point, right_point in zip(left, right):
                draw.line(
                    (*project(left_point), *project(right_point)),
                    fill=color,
                    width=1,
                )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)


def write_fixture_gallery(
    fixture_root: Path,
    fixture_name: str,
    full_render: Path,
    records: list[dict],
) -> Path:
    cards = []
    for record in records:
        image = Path(record["render"]).relative_to(fixture_name)
        cards.append(
            "<figure>"
            f'<a href="{html.escape(str(image))}">'
            f'<img loading="lazy" src="{html.escape(str(image))}" '
            f'alt="{html.escape(record["name"])}"></a>'
            "<figcaption>"
            f"<b>{html.escape(record['status'])}</b> · "
            f"{html.escape(record['object_id'])}<br>"
            f"{html.escape(record['rule'].replace('_', ' '))}"
            "</figcaption></figure>"
        )
    gallery = fixture_root / "index.html"
    gallery.write_text(
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>{html.escape(fixture_name)} bracket fixtures</title>"
        "<style>"
        "body{margin:24px;background:#f6f3ed;color:#142528;font:14px sans-serif}"
        "h1{margin-bottom:4px}.summary{color:#587074;margin-bottom:22px}"
        ".full{max-width:min(1100px,100%);border:1px solid #ccd6ce}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;margin-top:24px}"
        "figure{margin:0;background:white;border:1px solid #d8ddd5;padding:8px}"
        "img{display:block;width:100%;height:auto}figcaption{padding:8px 2px 2px;line-height:1.5}"
        "figcaption b{text-transform:uppercase;color:#a83434}"
        "</style>"
        f"<h1>{html.escape(fixture_name)}</h1>"
        f'<p class="summary">{len(records)} decision-bracket renders · '
        '<a href="manifest.json">fixture manifest</a> · '
        f'<a href="{html.escape(full_render.name)}">full render</a></p>'
        f'<img class="full" src="{html.escape(full_render.name)}" alt="Full sew-out">'
        f'<div class="grid">{"".join(cards)}</div>',
    )
    return gallery


def write_run_gallery(
    output: Path,
    results: list[dict],
    failures: list[dict],
    *,
    manifest_name: str = "manifest.json",
) -> None:
    rows = []
    for result in results:
        metrics = result["metrics"]
        rows.append(
            "<tr>"
            f'<td><a href="{html.escape(result["name"])}/index.html">'
            f'{html.escape(result["name"])}</a></td>'
            f'<td>{html.escape(result["source_format"])}</td>'
            f'<td>{metrics["stitches"]:,}</td>'
            f'<td>{metrics["objects"]:,}</td>'
            f'<td>{metrics["brackets"]:,}</td>'
            f'<td>{html.escape(str(metrics["brackets_by_status"]))}</td>'
            "</tr>"
        )
    failure_rows = "".join(
        f"<li>{html.escape(item['name'])}: {html.escape(item['error'])}</li>"
        for item in failures
    )
    (output / "index.html").write_text(
        "<!doctype html><meta charset=\"utf-8\">"
        "<title>Decision-bracket fixture run</title>"
        "<style>"
        "body{margin:28px;background:#f6f3ed;color:#142528;font:14px sans-serif}"
        "table{border-collapse:collapse;background:white;min-width:800px}"
        "th,td{padding:10px 12px;border:1px solid #d8ddd5;text-align:left}"
        "th{background:#172a2d;color:white}"
        "</style>"
        "<h1>Decision-bracket fixture run</h1>"
        f'<p><a href="{html.escape(manifest_name)}">'
        "Machine-readable run manifest</a></p>"
        "<table><thead><tr><th>Fixture</th><th>Source</th><th>Stitches</th>"
        "<th>Objects</th><th>Brackets</th><th>Status counts</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        + (f"<h2>Failures</h2><ul>{failure_rows}</ul>" if failures else ""),
    )


def write_training_records(
    output: Path,
    results: list[dict],
    *,
    filename: str = "training-records.jsonl",
) -> Path:
    target = output / filename
    with target.open("w") as stream:
        for result in results:
            relative = result.get("outputs", {}).get("training_record")
            if not relative:
                continue
            record_path = output / relative
            stream.write(json.dumps(json.loads(record_path.read_text())))
            stream.write("\n")
    return target


def write_fixture(
    fixture: dict,
    output_root: Path,
    *,
    pixels_per_mm: int,
    statuses: set[str],
    url_prefix: str = "/artifacts/bracket-fixtures",
) -> dict:
    source = fixture["source"]
    encoded, source_mode = source_bytes(source)
    payload = digitize_payload(
        encoded,
        width_mm=float(fixture["width_mm"]),
        colors=int(fixture["colors"]),
        profile=str(fixture["profile"]),
        density_multiplier=float(fixture["density_multiplier"]),
    )
    operations = payload["operations"]
    if not any(operation["kind"] == "stitch" for operation in operations):
        raise ValueError(f"{fixture['name']} produced no stitches")
    fixture_root = output_root / fixture["name"]
    fixture_root.mkdir(parents=True, exist_ok=True)
    dst_path = fixture_root / f"{fixture['name']}.dst"
    dst_path.write_bytes(as_dst(operations))
    decoded = inspect_dst(dst_path.read_bytes())
    render_path = fixture_root / f"{fixture['name']}--full.png"
    render_plan(
        decoded,
        payload.get("palette", []),
        render_path,
        pixels_per_mm=pixels_per_mm,
    )
    document = (
        VectorDocument.from_json(payload["document"])
        if payload.get("document") else None
    )
    brackets = [
        bracket for bracket in payload.get("brackets", [])
        if not statuses or bracket["status"] in statuses
    ]
    bracket_records = []
    bracket_root = fixture_root / "brackets"
    for index, bracket in enumerate(brackets, 1):
        segment = bracket.get("segment")
        interval = (
            f"--s{float(segment['start']):.3f}-e{float(segment['end']):.3f}"
            if segment else ""
        )
        base = (
            f"{index:03d}--{slug(bracket['object_id'], maximum=28)}"
            f"--{slug(bracket['rule'], maximum=36)}"
            f"--{slug(bracket['status'])}{interval}"
        )
        image_path = bracket_root / f"{base}.png"
        record_path = bracket_root / f"{base}.json"
        render_plan(
            decoded,
            payload.get("palette", []),
            image_path,
            pixels_per_mm=pixels_per_mm,
            bracket=bracket,
            document=document,
        )
        record = {
            **bracket,
            "source": display_path(source),
            "dst": str(dst_path.relative_to(output_root)),
            "render": str(image_path.relative_to(output_root)),
            "render_url": f"{url_prefix.rstrip('/')}/{image_path.relative_to(output_root)}",
        }
        record_path.write_text(json.dumps(record, indent=2) + "\n")
        bracket_records.append({
            "name": base,
            "rule": bracket["rule"],
            "status": bracket["status"],
            "object_id": bracket["object_id"],
            "segment": segment,
            "record": str(record_path.relative_to(output_root)),
            "render": str(image_path.relative_to(output_root)),
        })
    plan_path = fixture_root / f"{fixture['name']}--plan.json"
    plan_path.write_text(json.dumps(payload, indent=2) + "\n")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    training_path = fixture_root / f"{fixture['name']}--training-record.json"
    training_record = build_training_record(
        payload,
        source_sha256=source_hash,
        source_ref=display_path(source),
        settings={
            key: fixture[key]
            for key in (
                "width_mm", "colors", "profile", "density_multiplier"
            )
        },
        plan_ref=str(plan_path.relative_to(output_root)),
        plan_sha256=plan_hash,
    )
    training_path.write_text(json.dumps(training_record, indent=2) + "\n")
    bracket_path = fixture_root / f"{fixture['name']}--brackets.json"
    bracket_path.write_text(json.dumps(bracket_records, indent=2) + "\n")
    stitch_count = sum(operation["kind"] == "stitch" for operation in decoded)
    report_status, report_summary = drc_disposition(payload)
    result = {
        "name": fixture["name"],
        "source": display_path(source),
        "source_format": source.suffix.lower().lstrip("."),
        "source_mode": source_mode,
        "source_sha256": source_hash,
        "settings": {
            key: fixture[key]
            for key in ("width_mm", "colors", "profile", "density_multiplier")
        },
        "planner": payload.get("posterization", {}).get(
            "selected_planner", "raster_layered"
        ),
        "report_status": report_status,
        "report_summary": report_summary,
        "drc": payload.get("drc", {}),
        "outputs": {
            "dst": str(dst_path.relative_to(output_root)),
            "render": str(render_path.relative_to(output_root)),
            "plan": str(plan_path.relative_to(output_root)),
            "training_record": str(training_path.relative_to(output_root)),
            "brackets": str(bracket_path.relative_to(output_root)),
            "gallery": str(
                (fixture_root / "index.html").relative_to(output_root)
            ),
        },
        "urls": {
            "gallery": f"{url_prefix.rstrip('/')}/{fixture['name']}/index.html",
            "full_render": f"{url_prefix.rstrip('/')}/{render_path.relative_to(output_root)}",
            "dst": f"{url_prefix.rstrip('/')}/{dst_path.relative_to(output_root)}",
            "training_record": (
                f"{url_prefix.rstrip('/')}/"
                f"{training_path.relative_to(output_root)}"
            ),
        },
        "metrics": {
            "stitches": stitch_count,
            "objects": len(payload.get("regions", [])),
            "brackets": len(brackets),
            "all_brackets": len(payload.get("brackets", [])),
            "brackets_by_status": dict(Counter(
                bracket["status"] for bracket in brackets
            )),
            "brackets_by_rule": dict(Counter(
                bracket["rule"] for bracket in brackets
            )),
        },
        "bracket_artifacts": bracket_records,
    }
    write_fixture_gallery(
        fixture_root, fixture["name"], render_path, bracket_records
    )
    (fixture_root / "manifest.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    return result


def safe_clean(output: Path) -> None:
    base = DEFAULT_OUTPUT.resolve()
    resolved = output.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(f"refusing to clean output outside {base}")
    if resolved.exists():
        shutil.rmtree(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--only",
        help="Comma-separated fixture names from the manifest",
    )
    parser.add_argument(
        "--statuses",
        help="Comma-separated bracket statuses; empty generates every bracket",
    )
    parser.add_argument("--pixels-per-mm", type=int, default=10)
    parser.add_argument(
        "--clean", action="store_true",
        help="Remove the selected output directory before generating",
    )
    options = parser.parse_args()
    output = options.output.resolve()
    if options.clean:
        safe_clean(output)
    output.mkdir(parents=True, exist_ok=True)
    selected = {
        slug(value) for value in (options.only or "").split(",") if value
    }
    statuses = {
        value.strip() for value in (options.statuses or "").split(",")
        if value.strip()
    }
    fixtures = load_fixtures(options.manifest.resolve(), selected)
    results = []
    failures = []
    for fixture in fixtures:
        print(
            f"[brackets] {fixture['name']} <- "
            f"{display_path(fixture['source'])}",
            flush=True,
        )
        try:
            results.append(write_fixture(
                fixture,
                output,
                pixels_per_mm=max(2, options.pixels_per_mm),
                statuses=statuses,
            ))
        except Exception as error:
            failures.append({
                "name": fixture["name"],
                "source": display_path(fixture["source"]),
                "error": str(error),
            })
            print(
                f"[brackets] FAILED {fixture['name']}: {error}",
                file=sys.stderr,
                flush=True,
            )
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": display_path(Path(__file__)),
        "fixture_manifest": display_path(options.manifest),
        "pixels_per_mm": max(2, options.pixels_per_mm),
        "status_filter": sorted(statuses),
        "gallery_url": "/artifacts/bracket-fixtures/index.html",
        "fixtures": results,
        "failures": failures,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    write_training_records(output, results)
    write_run_gallery(output, results, failures)
    print(
        f"[brackets] wrote {len(results)} fixture sets, "
        f"{sum(item['metrics']['brackets'] for item in results)} bracket renders, "
        f"{len(failures)} failures to {output}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

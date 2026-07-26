#!/usr/bin/env python3
"""Process new artwork from a directory in persistent, idempotent batches."""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.generate_bracket_fixtures import (  # noqa: E402
    display_path,
    slug,
    write_fixture,
    write_run_gallery,
    write_training_records,
)

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg"}
STATE_DIRECTORY = ".digitizer-queue"
ARTIFACTS_ROOT = ROOT / "artifacts"
DEFAULTS = {
    "width_mm": 110.0,
    "colors": 8,
    "profile": "standard",
    "density_multiplier": 1.0,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def load_state(path: Path, queue: Path) -> dict:
    if not path.exists():
        return {
            "schema_version": 1,
            "queue": str(queue),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "jobs": {},
            "batches": [],
        }
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported queue state schema")
    payload.setdefault("jobs", {})
    payload.setdefault("batches", [])
    return payload


def sidecar_settings(path: Path) -> dict:
    sidecar = path.with_name(f"{path.name}.json")
    if not sidecar.exists():
        return {}
    payload = json.loads(sidecar.read_text())
    allowed = {
        "name", "width_mm", "colors", "profile",
        "density_multiplier", "pixels_per_mm", "statuses",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"unsupported settings in {sidecar.name}: {', '.join(sorted(unknown))}"
        )
    return payload


def discover(
    queue: Path,
    state: dict,
    *,
    recursive: bool,
    settle_seconds: float,
    retry_failed: bool,
    force: bool,
    attempted_keys: set[str] | None = None,
) -> list[dict]:
    iterator = queue.rglob("*") if recursive else queue.iterdir()
    threshold = time.time() - max(0.0, settle_seconds)
    pending = []
    for path in iterator:
        if (
            not path.is_file()
            or path.suffix.lower() not in SUPPORTED_SUFFIXES
            or path.name.startswith(".")
            or STATE_DIRECTORY in path.parts
            or path.stat().st_mtime > threshold
        ):
            continue
        relative = path.relative_to(queue)
        digest = sha256(path)
        key = f"{relative.as_posix()}::{digest}"
        if attempted_keys and key in attempted_keys:
            continue
        previous = state["jobs"].get(key)
        if previous and not force:
            if previous.get("status") != "failed" or not retry_failed:
                continue
        pending.append({
            "key": key,
            "sha256": digest,
            "path": path,
            "relative_path": relative.as_posix(),
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        })
    return sorted(
        pending,
        key=lambda item: (
            item["path"].stat().st_mtime_ns,
            item["relative_path"],
        ),
    )


def queue_output(queue: Path) -> Path:
    identifier = hashlib.sha256(str(queue.resolve()).encode()).hexdigest()[:8]
    return ARTIFACTS_ROOT / "queue-runs" / f"{slug(queue.name)}-{identifier}"


def url_prefix(output: Path) -> str:
    resolved = output.resolve()
    try:
        relative = resolved.relative_to(ARTIFACTS_ROOT.resolve())
        return f"/artifacts/{relative.as_posix()}"
    except ValueError:
        return resolved.as_uri()


def fixture_for(job: dict, queue: Path, options: argparse.Namespace) -> dict:
    settings = {
        **DEFAULTS,
        "width_mm": options.width_mm,
        "colors": options.colors,
        "profile": options.profile,
        "density_multiplier": options.density_multiplier,
        **sidecar_settings(job["path"]),
    }
    relative_stem = str(
        Path(job["relative_path"]).with_suffix("")
    ).replace(os.sep, "-")
    settings.update({
        "source": job["path"],
        "path": job["relative_path"],
        "name": slug(
            settings.get("name")
            or f"{relative_stem}-{job['sha256'][:8]}"
        ),
    })
    return settings


def normalized_statuses(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        raise ValueError("statuses must be a comma-separated string or list")
    return {str(item).strip() for item in values if str(item).strip()}


def batch_identifier(jobs: list[dict]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(
        "\n".join(job["key"] for job in jobs).encode()
    ).hexdigest()[:8]
    return f"{stamp}-{digest}"


def report_row(job: dict, result: dict | None, error: str | None) -> dict:
    if result is None:
        return {
            "job_id": job["job_id"],
            "source": job["relative_path"],
            "sha256": job["sha256"],
            "processing_status": "failed",
            "drc_status": "not_run",
            "error": error or "unknown error",
        }
    metrics = result["metrics"]
    summary = result["report_summary"]
    return {
        "job_id": job["job_id"],
        "source": job["relative_path"],
        "sha256": job["sha256"],
        "processing_status": "completed",
        "drc_status": result["report_status"],
        "stitches": metrics["stitches"],
        "objects": metrics["objects"],
        "brackets": metrics["all_brackets"],
        "semantic_violations": summary["semantic_violations"],
        "budget_violations": summary["budget_violations"],
        "stitch_memory_exceeded": summary["stitch_memory_exceeded"],
        "quality_score": result.get("drc", {}).get("quality_score", ""),
        "gallery": result["urls"]["gallery"],
        "dst": result["urls"]["dst"],
        "error": "",
    }


def write_batch_reports(
    output: Path,
    batch_id: str,
    rows: list[dict],
    results: list[dict],
    failures: list[dict],
    started_at: str,
) -> dict:
    finished_at = utc_now()
    report = {
        "schema_version": 1,
        "batch_id": batch_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "counts": {
            "jobs": len(rows),
            "completed": sum(row["processing_status"] == "completed" for row in rows),
            "failed": sum(row["processing_status"] == "failed" for row in rows),
            "pass": sum(row["drc_status"] == "pass" for row in rows),
            "review": sum(row["drc_status"] == "review" for row in rows),
            "block": sum(row["drc_status"] == "block" for row in rows),
        },
        "jobs": rows,
        "training_records": "training-records.jsonl",
    }
    atomic_json(output / "batch-report.json", report)
    columns = [
        "job_id", "source", "sha256", "processing_status", "drc_status",
        "stitches", "objects", "brackets", "semantic_violations",
        "budget_violations", "stitch_memory_exceeded", "quality_score",
        "gallery", "dst", "error",
    ]
    with (output / "batch-report.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    write_run_gallery(
        output,
        results,
        failures,
        manifest_name="batch-report.json",
    )
    write_training_records(output, results)
    html_rows = []
    for row in rows:
        output_link = (
            f'<a href="{html.escape(row["gallery"], quote=True)}">report</a>'
            if row.get("gallery")
            else html.escape(row.get("error", ""))
        )
        html_rows.append(
            "<tr>"
            f"<td>{html.escape(row['source'])}</td>"
            f"<td>{html.escape(row['processing_status'])}</td>"
            f"<td>{html.escape(row['drc_status'])}</td>"
            f"<td>{row.get('stitches', '')}</td>"
            f"<td>{row.get('objects', '')}</td>"
            f"<td>{row.get('brackets', '')}</td>"
            f"<td>{output_link}</td>"
            "</tr>"
        )
    rows_html = "".join(html_rows)
    (output / "batch-report.html").write_text(
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>Digitizing batch {html.escape(batch_id)}</title>"
        "<style>body{margin:28px;background:#f6f3ed;color:#142528;font:14px sans-serif}"
        "table{border-collapse:collapse;background:white}"
        "th,td{padding:9px 11px;border:1px solid #d8ddd5;text-align:left}"
        "th{background:#172a2d;color:white}</style>"
        f"<h1>Digitizing batch {html.escape(batch_id)}</h1>"
        '<p><a href="batch-report.json">JSON</a> · '
        '<a href="batch-report.csv">CSV</a> · '
        '<a href="index.html">fixture gallery</a></p>'
        "<table><thead><tr><th>Source</th><th>Processing</th><th>DRC</th>"
        "<th>Stitches</th><th>Objects</th><th>Brackets</th><th>Output</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table>"
    )
    return report


def write_queue_index(output_root: Path, state: dict) -> None:
    rows = []
    for batch in reversed(state.get("batches", [])):
        report = batch["report"]
        rows.append(
            "<tr>"
            f'<td><a href="{html.escape(report)}/batch-report.html">'
            f'{html.escape(batch["id"])}</a></td>'
            f'<td>{batch["counts"]["jobs"]}</td>'
            f'<td>{batch["counts"]["pass"]}</td>'
            f'<td>{batch["counts"]["review"]}</td>'
            f'<td>{batch["counts"]["block"]}</td>'
            f'<td>{batch["counts"]["failed"]}</td>'
            "</tr>"
        )
    (output_root / "index.html").write_text(
        "<!doctype html><meta charset=\"utf-8\"><title>Digitizing queue</title>"
        "<style>body{margin:28px;background:#f6f3ed;color:#142528;font:14px sans-serif}"
        "table{border-collapse:collapse;background:white}"
        "th,td{padding:9px 11px;border:1px solid #d8ddd5;text-align:left}"
        "th{background:#172a2d;color:white}</style>"
        "<h1>Digitizing queue</h1>"
        "<table><thead><tr><th>Batch</th><th>Jobs</th><th>Pass</th>"
        "<th>Review</th><th>Block</th><th>Failed</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def process_one_batch(
    queue: Path,
    output_root: Path,
    state_path: Path,
    state: dict,
    options: argparse.Namespace,
    attempted_keys: set[str],
) -> int:
    pending = discover(
        queue,
        state,
        recursive=options.recursive,
        settle_seconds=options.settle_seconds,
        retry_failed=options.retry_failed,
        force=options.force,
        attempted_keys=attempted_keys,
    )
    jobs = pending[:max(1, options.batch_size)]
    if not jobs:
        return 0
    batch_id = batch_identifier(jobs)
    batch_output = output_root / batch_id
    batch_output.mkdir(parents=True, exist_ok=False)
    batch_url = url_prefix(batch_output)
    started_at = utc_now()
    rows = []
    results = []
    failures = []
    status_filter = {
        value.strip() for value in (options.statuses or "").split(",")
        if value.strip()
    }
    for job in jobs:
        attempted_keys.add(job["key"])
        job["job_id"] = f"{slug(job['relative_path'], maximum=42)}-{job['sha256'][:12]}"
        print(f"[queue] {batch_id} · {job['relative_path']}", flush=True)
        try:
            fixture = fixture_for(job, queue, options)
            result = write_fixture(
                fixture,
                batch_output,
                pixels_per_mm=int(
                    fixture.get("pixels_per_mm", options.pixels_per_mm)
                ),
                statuses=normalized_statuses(
                    fixture.get("statuses", status_filter)
                ),
                url_prefix=batch_url,
            )
            results.append(result)
            row = report_row(job, result, None)
            state_status = "completed"
            error = None
        except Exception as exception:
            error = f"{type(exception).__name__}: {exception}"
            row = report_row(job, None, error)
            failures.append({
                "name": job["job_id"],
                "source": job["relative_path"],
                "error": error,
            })
            state_status = "failed"
            print(f"[queue] FAILED {job['relative_path']}: {error}", file=sys.stderr)
        rows.append(row)
        state["jobs"][job["key"]] = {
            "job_id": job["job_id"],
            "source": job["relative_path"],
            "sha256": job["sha256"],
            "size": job["size"],
            "mtime_ns": job["mtime_ns"],
            "status": state_status,
            "drc_status": row["drc_status"],
            "batch_id": batch_id,
            "processed_at": utc_now(),
            "error": error,
        }
        state["updated_at"] = utc_now()
        atomic_json(state_path, state)
    report = write_batch_reports(
        batch_output, batch_id, rows, results, failures, started_at
    )
    batch_relative = str(batch_output.relative_to(output_root))
    state["batches"].append({
        "id": batch_id,
        "report": batch_relative,
        "url": f"{batch_url}/batch-report.html",
        "started_at": report["started_at"],
        "finished_at": report["finished_at"],
        "counts": report["counts"],
    })
    state["updated_at"] = utc_now()
    atomic_json(state_path, state)
    write_queue_index(output_root, state)
    atomic_json(output_root / "latest.json", state["batches"][-1])
    print(
        f"[queue] {batch_id}: {report['counts']} · "
        f"{batch_url}/batch-report.html",
        flush=True,
    )
    return len(jobs)


def run(options: argparse.Namespace) -> int:
    queue = options.directory.resolve()
    if not queue.is_dir():
        raise NotADirectoryError(queue)
    metadata = queue / STATE_DIRECTORY
    metadata.mkdir(parents=True, exist_ok=True)
    state_path = metadata / "state.json"
    output_root = (
        options.output.resolve() if options.output else queue_output(queue)
    )
    try:
        output_root.relative_to(queue)
    except ValueError:
        pass
    else:
        raise ValueError(
            "the output directory must be outside the input queue "
            "to prevent generated PNG files from being re-ingested"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = metadata / "queue.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"queue is already being processed: {queue}", file=sys.stderr)
            return 2
        state = load_state(state_path, queue)
        attempted_keys: set[str] = set()
        try:
            while True:
                processed = process_one_batch(
                    queue, output_root, state_path, state, options, attempted_keys
                )
                if options.watch:
                    if not processed:
                        print(
                            f"[queue] waiting for new files in {queue}",
                            flush=True,
                        )
                    time.sleep(max(1.0, options.poll_seconds))
                    continue
                if not options.drain or not processed:
                    break
        except KeyboardInterrupt:
            print("\n[queue] watch stopped", flush=True)
        print(
            f"[queue] report index: {url_prefix(output_root)}/index.html",
            flush=True,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--width-mm", type=float, default=DEFAULTS["width_mm"])
    parser.add_argument("--colors", type=int, default=DEFAULTS["colors"])
    parser.add_argument("--profile", default=DEFAULTS["profile"])
    parser.add_argument(
        "--density-multiplier",
        type=float,
        default=DEFAULTS["density_multiplier"],
    )
    parser.add_argument("--pixels-per-mm", type=int, default=6)
    parser.add_argument(
        "--statuses",
        help="Only render the listed bracket statuses; DRC still evaluates all",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--force", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--drain", action="store_true")
    mode.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

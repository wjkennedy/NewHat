#!/usr/bin/env python3
"""Build a rights-aware ML corpus with versioned deterministic DRC labels."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prototype.training_data import (  # noqa: E402
    build_training_record,
    record_integrity_findings,
)

DEFAULT_OUTPUT = ROOT / "artifacts" / "ml-corpus"


def load_annotations(path: Path | None) -> dict:
    if path is None:
        return {"defaults": {}, "sources": {}}
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported annotation schema")
    if not isinstance(payload.get("sources", {}), dict):
        raise ValueError("annotation sources must be keyed by source SHA-256")
    return {
        "defaults": payload.get("defaults", {}),
        "sources": payload.get("sources", {}),
    }


def fixture_manifests(roots: list[Path]) -> list[Path]:
    manifests = set()
    for root in roots:
        if root.is_file():
            manifests.add(root.resolve())
        elif root.is_dir():
            manifests.update(
                path.resolve() for path in root.rglob("manifest.json")
            )
        else:
            raise FileNotFoundError(root)
    return sorted(manifests)


def resolve_plan(manifest_path: Path, plan_ref: str) -> Path:
    relative = Path(plan_ref)
    candidates = [
        manifest_path.parent / relative,
        manifest_path.parent / relative.name,
        manifest_path.parent.parent / relative,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"cannot resolve {plan_ref!r} from {manifest_path}"
    )


def record_from_manifest(
    manifest_path: Path,
    annotations: dict,
) -> dict | None:
    manifest = json.loads(manifest_path.read_text())
    source_hash = manifest.get("source_sha256")
    plan_ref = manifest.get("outputs", {}).get("plan")
    if not source_hash or not plan_ref:
        return None
    plan_path = resolve_plan(manifest_path, plan_ref)
    plan_bytes = plan_path.read_bytes()
    payload = json.loads(plan_bytes)
    annotation = {
        **annotations.get("defaults", {}),
        **annotations.get("sources", {}).get(source_hash, {}),
    }
    try:
        plan_display = str(plan_path.relative_to(ROOT))
    except ValueError:
        plan_display = str(plan_path)
    return build_training_record(
        payload,
        source_sha256=source_hash,
        source_ref=str(manifest.get("source", "unknown")),
        settings=manifest.get("settings", {}),
        plan_ref=plan_display,
        plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
        annotation=annotation,
    )


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True))
            stream.write("\n")


def build(
    roots: list[Path],
    output: Path,
    annotations: dict,
) -> tuple[dict, list[str]]:
    output.mkdir(parents=True, exist_ok=True)
    records = []
    failures = []
    seen_revisions = set()
    for manifest_path in fixture_manifests(roots):
        try:
            record = record_from_manifest(manifest_path, annotations)
            if record is None:
                continue
            identity = (record["example_id"], record["revision_id"])
            if identity in seen_revisions:
                continue
            seen_revisions.add(identity)
            records.append(record)
        except Exception as error:
            failures.append(f"{manifest_path}: {type(error).__name__}: {error}")
    records.sort(key=lambda item: (item["example_id"], item["revision_id"]))

    integrity = []
    splits_by_group: dict[str, set[str]] = defaultdict(set)
    for record in records:
        splits_by_group[record["split_group"]].add(record["split"])
        integrity.extend(
            f"{record['example_id']}: {finding}"
            for finding in record_integrity_findings(record)
        )
    for group, splits in splits_by_group.items():
        if len(splits) > 1:
            integrity.append(
                f"split leakage for group {group}: {', '.join(sorted(splits))}"
            )

    lanes = {
        "train": [],
        "validation": [],
        "test": [],
        "quarantine": [],
    }
    for record in records:
        lanes[record["lane"]].append(record)
    write_jsonl(output / "records.jsonl", records)
    # Engine/DRC regression intentionally retains quarantined fixtures. This
    # is a deterministic compiler test set, not permission to train on them.
    write_jsonl(output / "drc-regression.jsonl", records)
    for lane, lane_records in lanes.items():
        write_jsonl(output / f"{lane}.jsonl", lane_records)

    summary = {
        "schema_version": 1,
        "record_count": len(records),
        "records_by_lane": {
            lane: len(lane_records) for lane, lane_records in lanes.items()
        },
        "drc_statuses": dict(Counter(
            record["labels"]["drc"]["status"] for record in records
        )),
        "training_tasks": {
            key: sum(
                bool(record["eligibility"].get(key)) for record in records
            )
            for key in (
                "semantic_supervision",
                "drc_auxiliary_supervision",
                "evaluation",
            )
        },
        "integrity_findings": integrity,
        "ingest_failures": failures,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Fail on ingest errors, rights gaps, split leakage, or silent DRC overrides",
    )
    options = parser.parse_args()
    annotations = load_annotations(
        options.annotations.resolve() if options.annotations else None
    )
    summary, failures = build(
        [path.resolve() for path in options.roots],
        options.output.resolve(),
        annotations,
    )
    print(json.dumps(summary, indent=2))
    if options.enforce and (failures or summary["integrity_findings"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

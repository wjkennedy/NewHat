#!/usr/bin/env python3
"""Compare compiled candidate plans with a pinned DRC-labelled baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prototype.drc_policy import regression_findings  # noqa: E402


def load_records(path: Path) -> dict[str, dict]:
    records = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        identifier = record["example_id"]
        if identifier in records:
            raise ValueError(
                f"duplicate example_id {identifier} in {path}:{line_number}"
            )
        records[identifier] = record
    return records


def compare(
    baseline: dict[str, dict],
    candidate: dict[str, dict],
    *,
    allow_missing: bool,
    quality_tolerance: float,
    rate_tolerance: float,
) -> dict:
    violations = []
    for identifier, baseline_record in baseline.items():
        candidate_record = candidate.get(identifier)
        if candidate_record is None:
            if not allow_missing:
                violations.append({
                    "example_id": identifier,
                    "rule": "dataset.missing_candidate",
                })
            continue
        for finding in regression_findings(
            baseline_record,
            candidate_record,
            quality_tolerance=quality_tolerance,
            rate_tolerance=rate_tolerance,
        ):
            violations.append({"example_id": identifier, **finding})
    for identifier, record in candidate.items():
        if identifier in baseline:
            continue
        gate = record.get("labels", {}).get("drc", {})
        if gate.get("status") == "block":
            violations.append({
                "example_id": identifier,
                "rule": "drc.new_candidate_blocked",
            })
    return {
        "schema_version": 1,
        "baseline_records": len(baseline),
        "candidate_records": len(candidate),
        "compared_records": len(set(baseline) & set(candidate)),
        "passed": not violations,
        "violation_count": len(violations),
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Permit an empty comparison (normally rejected as meaningless)",
    )
    parser.add_argument("--quality-tolerance", type=float, default=2.0)
    parser.add_argument("--rate-tolerance", type=float, default=0.5)
    options = parser.parse_args()
    for label, path in (
        ("baseline", options.baseline),
        ("candidate", options.candidate),
    ):
        if not path.is_file():
            parser.error(
                f"{label} input does not exist: {path}\n"
                "Build snapshots first with tools/build_ml_corpus.py. "
                "For deterministic engine regression use "
                "drc-regression.jsonl; test.jsonl contains only reviewed, "
                "rights-eligible held-out records."
            )
    baseline = load_records(options.baseline)
    candidate = load_records(options.candidate)
    if not options.allow_empty:
        if not baseline:
            parser.error(
                "baseline input is empty. Use drc-regression.jsonl for the "
                "engineering fixture set, or annotate reviewed held-out "
                "examples before comparing test.jsonl."
            )
        if not candidate:
            parser.error(
                "candidate input is empty. Build the candidate corpus first."
            )
    report = compare(
        baseline,
        candidate,
        allow_missing=options.allow_missing,
        quality_tolerance=options.quality_tolerance,
        rate_tolerance=options.rate_tolerance,
    )
    encoded = json.dumps(report, indent=2) + "\n"
    if options.output:
        options.output.write_text(encoded)
    print(encoded, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

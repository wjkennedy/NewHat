"""Versioned DRC policy shared by production reports and ML evaluation."""
from __future__ import annotations

from collections import Counter
from math import hypot
from typing import Any

POLICY_VERSION = "new-hat-drc/1.1"
DEFAULT_STITCH_MEMORY_LIMIT = 650_000


def _integer_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): int(count)
        for key, count in value.items()
        if isinstance(count, (int, float, bool))
    }


def _rule(
    identifier: str,
    *,
    count: int = 1,
    source: str,
    object_id: str | None = None,
) -> dict:
    result = {
        "rule": identifier,
        "count": int(count),
        "source": source,
    }
    if object_id:
        result["object_id"] = object_id
    return result


def evaluate_drc(
    payload: dict,
    *,
    stitch_memory_limit: int = DEFAULT_STITCH_MEMORY_LIMIT,
) -> dict:
    """Map raw engine evidence into stable block/review/pass policy levels.

    Decision brackets remain advisory in policy 1.0. Their current numeric
    thresholds still require sew-out calibration, so even a bracket named
    ``blocker`` creates a review label rather than silently prohibiting export.
    Explicit blocker findings, impossible output, machine-length violations,
    and memory overflow are enforced.
    """
    drc = payload.get("drc", {}) if isinstance(payload.get("drc"), dict) else {}
    operations = [
        operation for operation in payload.get("operations", [])
        if isinstance(operation, dict)
    ]
    operation_stitches = sum(
        operation.get("kind") == "stitch" for operation in operations
    )
    stitches = operation_stitches or int(drc.get("stitch_count", 0) or 0)
    profile_maximum = float(drc.get(
        "machine_max_stitch_mm",
        12.1 if payload.get("profile") == "non_wearable" else 7.0,
    ))
    derived_long = derived_short = 0
    long_stitch_examples = []
    previous = None
    for operation in operations:
        if operation.get("kind") != "stitch":
            previous = None
            continue
        if previous is not None:
            length = hypot(
                float(operation.get("x", 0)) - float(previous.get("x", 0)),
                float(operation.get("y", 0)) - float(previous.get("y", 0)),
            )
            tie = str(operation.get("role", "")).startswith("tie_") or str(
                previous.get("role", "")
            ).startswith("tie_")
            if not tie:
                if length > profile_maximum:
                    derived_long += 1
                    if len(long_stitch_examples) < 50:
                        long_stitch_examples.append({
                            "length_mm": length,
                            "maximum_mm": profile_maximum,
                            "role": operation.get("role"),
                            "object_id": operation.get("region_id"),
                            "from": {
                                "x": previous.get("x"),
                                "y": previous.get("y"),
                            },
                            "to": {
                                "x": operation.get("x"),
                                "y": operation.get("y"),
                            },
                        })
                derived_short += length < 0.25
        previous = operation
    semantic = _integer_counts(drc.get("semantic_violations"))
    budget = _integer_counts(drc.get("budget_violations"))
    findings = [
        finding for finding in payload.get("findings", [])
        if isinstance(finding, dict)
    ]
    brackets = [
        bracket for bracket in payload.get("brackets", [])
        if isinstance(bracket, dict)
    ]
    finding_counts = Counter(
        str(item.get("severity", "observation")) for item in findings
    )
    bracket_counts = Counter(
        str(item.get("status", "pass")) for item in brackets
    )

    hard_blockers = []
    conditional_blockers = []
    review_findings = []

    if stitches <= 0:
        hard_blockers.append(_rule(
            "output.no_stitches", source="machine",
        ))
    posterization = (
        payload.get("posterization", {})
        if isinstance(payload.get("posterization"), dict)
        else {}
    )
    foreground_color_coverage = posterization.get(
        "foreground_color_coverage"
    )
    if (
        isinstance(foreground_color_coverage, (int, float))
        and foreground_color_coverage < 0.98
    ):
        hard_blockers.append(_rule(
            "artwork.foreground_color_loss",
            count=max(1, round((1 - foreground_color_coverage) * 100)),
            source="posterization",
        ))
    if stitches > stitch_memory_limit:
        hard_blockers.append(_rule(
            "machine.stitch_memory",
            count=stitches - stitch_memory_limit,
            source="machine",
        ))
    long_stitches = max(
        int(drc.get("long_stitches", 0) or 0),
        derived_long,
    )
    if long_stitches:
        hard_blockers.append(_rule(
            "machine.long_stitches",
            count=long_stitches,
            source="machine",
        ))
    for finding in findings:
        severity = str(finding.get("severity", "observation"))
        rule_name = f"finding.{finding.get('rule', 'unknown')}"
        item = _rule(
            rule_name,
            source="finding",
            object_id=finding.get("object_id"),
        )
        if severity == "blocker":
            hard_blockers.append(item)
        elif severity in {"warning", "conditional_blocker"}:
            review_findings.append(item)

    for name, count in semantic.items():
        if count <= 0:
            continue
        item = _rule(f"semantic.{name}", count=count, source="semantic")
        if name.endswith("_long"):
            conditional_blockers.append(item)
        else:
            review_findings.append(item)
    for name, count in budget.items():
        if count > 0:
            review_findings.append(_rule(
                f"budget.{name}", count=count, source="budget",
            ))
    short_stitches = max(
        int(drc.get("short_stitches", 0) or 0),
        derived_short,
    )
    if short_stitches:
        review_findings.append(_rule(
            "machine.short_stitches",
            count=short_stitches,
            source="machine",
        ))
    for status in ("near_limit", "warning", "fallback", "blocker"):
        if bracket_counts[status]:
            review_findings.append(_rule(
                f"bracket.{status}",
                count=bracket_counts[status],
                source="decision_bracket",
            ))

    status = (
        "block" if hard_blockers or conditional_blockers
        else "review" if review_findings
        else "pass"
    )
    trim_count = int(drc.get(
        "trim_count",
        sum(operation.get("kind") == "trim" for operation in operations),
    ) or 0)
    jump_count = int(drc.get(
        "machine_jump_count",
        drc.get(
            "jump_count",
            sum(operation.get("kind") == "jump" for operation in operations),
        ),
    ) or 0)
    trim_rate = drc.get("trims_per_1000_stitches")
    if not isinstance(trim_rate, (int, float)):
        trim_rate = trim_count * 1000 / max(1, stitches)
    jump_rate = drc.get("jumps_per_1000_stitches")
    if not isinstance(jump_rate, (int, float)):
        jump_rate = jump_count * 1000 / max(1, stitches)
    return {
        "policy_version": POLICY_VERSION,
        "status": status,
        "export_allowed": status != "block",
        "override_required": status == "block",
        "bracket_policy": "advisory_until_profile_calibration",
        "hard_blockers": hard_blockers,
        "conditional_blockers": conditional_blockers,
        "review_findings": review_findings,
        "counts": {
            "hard_blockers": sum(item["count"] for item in hard_blockers),
            "conditional_blockers": sum(
                item["count"] for item in conditional_blockers
            ),
            "review": sum(item["count"] for item in review_findings),
            "semantic_violations": sum(semantic.values()),
            "budget_violations": sum(budget.values()),
        },
        "evidence": {
            "semantic_violations": semantic,
            "budget_violations": budget,
            "findings_by_severity": dict(finding_counts),
            "brackets_by_status": dict(bracket_counts),
            "stitch_memory_exceeded": stitches > stitch_memory_limit,
            "foreground_color_coverage": foreground_color_coverage,
            "stitch_count": stitches,
            "quality_score": drc.get("quality_score"),
            "short_stitches": short_stitches,
            "long_stitches": long_stitches,
            "long_stitch_examples": long_stitch_examples,
            "trim_count": trim_count,
            "jump_count": jump_count,
            "trims_per_1000_stitches": trim_rate,
            "jumps_per_1000_stitches": jump_rate,
        },
    }


def regression_findings(
    baseline: dict,
    candidate: dict,
    *,
    quality_tolerance: float = 2.0,
    rate_tolerance: float = 0.5,
) -> list[dict]:
    """Return deterministic DRC regressions between two training records."""
    findings = []
    base_gate = baseline["labels"]["drc"]
    candidate_gate = candidate["labels"]["drc"]
    rank = {"pass": 0, "review": 1, "block": 2}
    if rank[candidate_gate["status"]] > rank[base_gate["status"]]:
        findings.append({
            "rule": "drc.status_regression",
            "baseline": base_gate["status"],
            "candidate": candidate_gate["status"],
        })
    for field in ("hard_blockers", "conditional_blockers"):
        base_count = base_gate["counts"].get(field, 0)
        candidate_count = candidate_gate["counts"].get(field, 0)
        if candidate_count > base_count:
            findings.append({
                "rule": f"drc.{field}_increased",
                "baseline": base_count,
                "candidate": candidate_count,
            })
    base_metrics = baseline["labels"].get("machine_metrics", {})
    candidate_metrics = candidate["labels"].get("machine_metrics", {})
    base_quality = base_metrics.get("quality_score")
    candidate_quality = candidate_metrics.get("quality_score")
    if (
        isinstance(base_quality, (int, float))
        and isinstance(candidate_quality, (int, float))
        and candidate_quality < base_quality - quality_tolerance
    ):
        findings.append({
            "rule": "drc.quality_score_regression",
            "baseline": base_quality,
            "candidate": candidate_quality,
        })
    for field in (
        "trims_per_1000_stitches",
        "jumps_per_1000_stitches",
    ):
        before = base_metrics.get(field)
        after = candidate_metrics.get(field)
        if (
            isinstance(before, (int, float))
            and isinstance(after, (int, float))
            and after > before + rate_tolerance
        ):
            findings.append({
                "rule": f"drc.{field}_regression",
                "baseline": before,
                "candidate": after,
            })
    return findings

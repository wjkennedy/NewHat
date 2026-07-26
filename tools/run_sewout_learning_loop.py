#!/usr/bin/env python3
"""Run resumable DRC-guided iterations and learn from physical sew-out ratings."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import html
import itertools
import json
from math import log1p, sqrt
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE = ROOT / "prototype"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROTOTYPE))

from digitizer import digitize_payload  # noqa: E402
from drc_policy import evaluate_drc  # noqa: E402
from server import as_dst, inspect_dst  # noqa: E402
from training_data import build_training_record  # noqa: E402
from vector_document import VectorDocument, plan_document  # noqa: E402
from tools.generate_bracket_fixtures import (  # noqa: E402
    display_path,
    render_plan,
    slug,
    source_bytes,
)

SCHEMA_VERSION = 1
KNOB_VALUES = {
    "density_multiplier": (0.80, 0.90, 1.00, 1.10, 1.20),
    "satin_spacing_mm": (0.38, 0.42, 0.46, 0.50),
    "pull_compensation_mm": (0.15, 0.25, 0.35, 0.45),
    "join_overlap_mm": (0.30, 0.40, 0.50, 0.60),
    "running_stitch_mm": (1.60, 2.00, 2.40),
}
BASELINE_KNOBS = {
    "density_multiplier": 1.00,
    "satin_spacing_mm": 0.42,
    "pull_compensation_mm": 0.25,
    "join_overlap_mm": 0.40,
    "running_stitch_mm": 2.00,
}
FEEDBACK_FIELDS = (
    "trial_id", "iteration", *KNOB_VALUES,
    "virtual_proxy_score", "drc_status", "stitches", "trims", "jumps",
    "overall_score", "registration_1_5", "coverage_1_5",
    "readability_1_5", "softness_1_5", "color_accuracy_1_5",
    "thread_breaks", "notes",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def candidate_id(knobs: dict) -> str:
    encoded = json.dumps(knobs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


def normalized_knobs(knobs: dict) -> list[float]:
    result = []
    for name, values in KNOB_VALUES.items():
        low, high = min(values), max(values)
        result.append((float(knobs[name]) - low) / max(1e-9, high - low))
    return result


def feature_vector(knobs: dict, active_names: list[str]) -> list[float]:
    normalized = dict(zip(KNOB_VALUES, normalized_knobs(knobs)))
    values = [normalized[name] for name in active_names]
    return [
        1.0,
        *values,
        *(value * value for value in values),
        *(
            values[index] * values[index + 1]
            for index in range(len(values) - 1)
        ),
    ]


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def solve_linear(matrix: list[list[float]], target: list[float]) -> list[float]:
    """Solve a small dense system with pivoted Gaussian elimination."""
    size = len(target)
    augmented = [
        [*map(float, matrix[row]), float(target[row])] for row in range(size)
    ]
    for column in range(size):
        pivot = max(
            range(column, size),
            key=lambda row: abs(augmented[row][column]),
        )
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        if abs(divisor) < 1e-12:
            raise ValueError("surrogate system is singular")
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if not factor:
                continue
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value
                in zip(augmented[row], augmented[column])
            ]
    return [augmented[row][-1] for row in range(size)]


def candidate_space(active_names: list[str]) -> list[dict]:
    names = list(active_names)
    return [
        {**BASELINE_KNOBS, **dict(zip(names, values))}
        for values in itertools.product(*(KNOB_VALUES[name] for name in names))
    ]


def physical_score(row: dict) -> float | None:
    if str(row.get("overall_score", "")).strip():
        return max(0.0, min(100.0, float(row["overall_score"])))
    required_components = (
        "registration_1_5", "coverage_1_5",
        "readability_1_5", "softness_1_5",
    )
    if not all(str(row.get(name, "")).strip() for name in required_components):
        return None
    component_names = list(required_components)
    if str(row.get("color_accuracy_1_5", "")).strip():
        component_names.append("color_accuracy_1_5")
    score = (
        sum(float(row[name]) for name in component_names)
        / len(component_names) * 20
    )
    breaks = float(row.get("thread_breaks") or 0)
    return max(0.0, min(100.0, score - breaks * 5))


def virtual_proxy_score(policy: dict) -> float:
    """Rank mechanical candidates; never present this as sew-out truth."""
    counts = policy["counts"]
    evidence = policy["evidence"]
    score = 100.0
    if policy["status"] == "block":
        score -= 45
    elif policy["status"] == "review":
        score -= 8
    score -= min(18, counts["hard_blockers"] * 5)
    score -= min(15, counts["conditional_blockers"] * 3)
    score -= min(15, log1p(counts["review"]) * 2.5)
    score -= min(8, float(evidence.get("trims_per_1000_stitches") or 0) * 0.5)
    score -= min(8, float(evidence.get("jumps_per_1000_stitches") or 0) * 0.2)
    return round(max(0.0, score), 3)


def fit_surrogate(
    trials: list[dict],
    feedback: dict[str, dict],
    active_names: list[str],
) -> tuple[list[float] | None, int]:
    rows = []
    targets = []
    weights = []
    physical_count = 0
    for trial in trials:
        rating = physical_score(feedback.get(trial["trial_id"], {}))
        if rating is None:
            rating = float(trial["virtual_proxy_score"])
            weight = 1.0
        else:
            physical_count += 1
            weight = 6.0
        rows.append(feature_vector(trial["knobs"], active_names))
        targets.append(rating)
        weights.append(weight)
    if len(rows) < 2:
        return None, physical_count
    feature_count = len(rows[0])
    normal = [
        [0.0 for _ in range(feature_count)] for _ in range(feature_count)
    ]
    right = [0.0 for _ in range(feature_count)]
    for features, target, weight in zip(rows, targets, weights):
        for row in range(feature_count):
            right[row] += weight * features[row] * target
            for column in range(feature_count):
                normal[row][column] += (
                    weight * features[row] * features[column]
                )
    for index in range(1, feature_count):
        normal[index][index] += 0.75
    coefficients = solve_linear(normal, right)
    return coefficients, physical_count


def select_candidates(
    completed: list[dict],
    feedback: dict[str, dict],
    count: int,
    active_names: list[str],
) -> tuple[list[dict], dict]:
    seen = {trial["trial_id"] for trial in completed}
    remaining = [
        knobs for knobs in candidate_space(active_names)
        if candidate_id(knobs) not in seen
    ]
    if not remaining:
        return [], {"training_rows": len(completed), "physical_rows": 0}
    coefficients, physical_count = fit_surrogate(
        completed, feedback, active_names
    )
    completed_vectors = [
        normalized_knobs(trial["knobs"]) for trial in completed
    ]

    def exploration(knobs: dict) -> float:
        vector = normalized_knobs(knobs)
        if not completed_vectors:
            return 1.0
        return min(
            sqrt(sum((left - right) ** 2 for left, right in zip(vector, item)))
            for item in completed_vectors
        )

    ranked = []
    for knobs in remaining:
        prediction = (
            dot(feature_vector(knobs, active_names), coefficients)
            if coefficients is not None else 50.0
        )
        acquisition = prediction + exploration(knobs) * (
            8.0 if physical_count else 4.0
        )
        ranked.append((acquisition, exploration(knobs), candidate_id(knobs), knobs))
    ranked.sort(reverse=True)
    selected = []
    if not completed and candidate_id(BASELINE_KNOBS) not in seen:
        selected.append(dict(BASELINE_KNOBS))
    for _, _, _, knobs in ranked:
        if len(selected) >= count:
            break
        if candidate_id(knobs) == candidate_id(BASELINE_KNOBS) and selected:
            continue
        selected.append(knobs)
    return selected, {
        "training_rows": len(completed),
        "physical_rows": physical_count,
        "active_knobs": active_names,
        "model": "weighted_ridge_quadratic" if coefficients is not None else "exploration_seed",
    }


def knob_activation(payload: dict) -> tuple[list[str], dict[str, str]]:
    if not payload.get("document"):
        return ["density_multiplier"], {
            "satin_spacing_mm": (
                "automatic raster replanning does not expose per-object satin spacing"
            ),
            "running_stitch_mm": (
                "automatic raster replanning does not expose per-object run length"
            ),
            "pull_compensation_mm": (
                "automatic raster replanning does not expose this vector control"
            ),
            "join_overlap_mm": (
                "automatic raster replanning does not expose this vector control"
            ),
        }
    stitch_types = {
        str(region.get("stitch_type"))
        for region in payload.get("regions", [])
        if isinstance(region, dict)
    }
    active = []
    inactive = {}
    if "tatami" in stitch_types:
        active.append("density_multiplier")
    else:
        inactive["density_multiplier"] = "no tatami objects in this plan"
    if "satin" in stitch_types:
        active.append("satin_spacing_mm")
    else:
        inactive["satin_spacing_mm"] = "no satin objects in this plan"
    if "running" in stitch_types:
        active.append("running_stitch_mm")
    else:
        inactive["running_stitch_mm"] = "no running objects in this plan"
    inactive["pull_compensation_mm"] = (
        "retained in the editable model but not yet applied by the compiler"
    )
    inactive["join_overlap_mm"] = (
        "retained in the editable model but not yet applied by the compiler"
    )
    return active, inactive


def outcome_signature(trial: dict) -> tuple:
    return (
        trial["drc"]["status"],
        trial["drc"]["counts"]["hard_blockers"],
        trial["drc"]["counts"]["conditional_blockers"],
        trial["metrics"]["stitches"],
        trial["metrics"]["trims"],
        trial["metrics"]["jumps"],
        trial["virtual_proxy_score"],
    )


def repair_classes(rule: str) -> list[str]:
    if rule in {"machine.long_stitches", "semantic.satin_long"}:
        return [
            "geometry_repair",
            "stitch_family_change",
            "generator_capability_missing",
        ]
    if rule == "output.no_stitches":
        return ["generator_capability_missing"]
    if rule == "budget.objects":
        return ["geometry_repair"]
    if rule.startswith("budget.") or any(
        term in rule for term in ("trim", "jump", "connector", "routing")
    ):
        return ["routing_repair"]
    if rule.startswith("bracket."):
        return ["geometry_repair", "physical_calibration_required"]
    if rule.startswith("semantic.") or rule == "machine.short_stitches":
        return ["parameter_adjustable", "geometry_repair"]
    if rule.startswith("finding."):
        return ["geometry_repair"]
    return ["physical_calibration_required"]


def experiment_analysis(
    state: dict,
    feedback: dict[str, dict],
) -> dict:
    trials = state["trials"]
    signatures = {outcome_signature(trial) for trial in trials}
    effects = {}
    for name in state["active_knobs"]:
        values = {}
        controlled = {}
        for trial in trials:
            values.setdefault(str(trial["knobs"][name]), set()).add(
                outcome_signature(trial)
            )
            other_values = tuple(
                (other, trial["knobs"][other])
                for other in state["active_knobs"] if other != name
            )
            controlled.setdefault(other_values, []).append(trial)
        comparisons = [
            group for group in controlled.values()
            if len({trial["knobs"][name] for trial in group}) > 1
        ]
        changed = sum(
            len({outcome_signature(trial) for trial in group}) > 1
            for group in comparisons
        )
        effects[name] = {
            "tested_values": sorted(values),
            "distinct_outcomes": len(set().union(*values.values())) if values else 0,
            "controlled_comparisons": len(comparisons),
            "changed_controlled_comparisons": changed,
            "observed_effect": changed > 0,
        }
    blocker_rules = {}
    all_rule_totals = {}
    for trial in trials:
        for finding in (
            trial["drc"].get("hard_blockers", [])
            + trial["drc"].get("conditional_blockers", [])
            + trial["drc"].get("review_findings", [])
        ):
            all_rule_totals[finding["rule"]] = (
                all_rule_totals.get(finding["rule"], 0) + finding["count"]
            )
        for blocker in (
            trial["drc"].get("hard_blockers", [])
            + trial["drc"].get("conditional_blockers", [])
        ):
            blocker_rules[blocker["rule"]] = (
                blocker_rules.get(blocker["rule"], 0) + blocker["count"]
            )
    sewout_candidates = []
    recommended_outcomes = set()
    for trial in sorted(
        trials,
        key=lambda item: item["virtual_proxy_score"],
        reverse=True,
    ):
        signature = outcome_signature(trial)
        if (
            trial["drc"]["status"] == "pass"
            and signature not in recommended_outcomes
        ):
            sewout_candidates.append(trial["name"])
            recommended_outcomes.add(signature)
        if len(sewout_candidates) >= 3:
            break
    blocking_count = sum(
        trial["drc"]["status"] == "block" for trial in trials
    )
    repair_classification = {
        rule: {
            "count": count,
            "classes": repair_classes(rule),
        }
        for rule, count in sorted(all_rule_totals.items())
    }
    active_effects_proven = [
        name for name, evidence in effects.items()
        if evidence["observed_effect"]
    ]
    inactive_or_no_effect = {
        **state["inactive_knobs"],
        **{
            name: "no changed compiled outcome in controlled comparisons"
            for name, evidence in effects.items()
            if evidence["controlled_comparisons"]
            and not evidence["observed_effect"]
        },
    }
    admission_status = (
        "structural_repair_required"
        if blocking_count == len(trials) and trials
        else (
            "physical_sewout_admitted"
            if sewout_candidates else "review_repair_required"
        )
    )
    remaining_candidate_count = sum(
        candidate_id(knobs) not in {
            trial["trial_id"] for trial in trials
        }
        for knobs in candidate_space(state["active_knobs"])
    )
    return {
        "trial_count": len(trials),
        "unique_compiled_outcomes": len(signatures),
        "active_knob_effects": effects,
        "inactive_knobs": state["inactive_knobs"],
        "blocking_trial_count": blocking_count,
        "blocker_rule_totals": blocker_rules,
        "repair_classification": repair_classification,
        "recommended_sewout_candidates": sewout_candidates,
        "physical_feedback_count": sum(
            physical_score(feedback.get(trial["trial_id"], {})) is not None
            for trial in trials
        ),
        "conclusion": (
            "physical feedback incorporated"
            if any(
                physical_score(feedback.get(trial["trial_id"], {})) is not None
                for trial in trials
            )
            else "virtual DRC/thread-economy evidence only"
        ),
        "next_action": (
            "resolve common blocking rules before physical sew-out"
            if blocking_count == len(trials) and trials
            else (
                "sew the recommended candidates and enter feedback"
                if sewout_candidates
                else "resolve review findings before physical sew-out"
            )
        ),
        "search_admission": {
            "status": admission_status,
            "initial_parameter_search_admitted": bool(state["active_knobs"]),
            "continued_parameter_search_admitted": (
                admission_status != "structural_repair_required"
                and bool(active_effects_proven)
                and remaining_candidate_count > 0
            ),
            "remaining_candidate_count": remaining_candidate_count,
            "active_parameters_with_proven_effect": active_effects_proven,
            "inactive_or_no_effect_parameters": inactive_or_no_effect,
            "unique_compiled_outcome_ratio": (
                len(signatures) / len(trials) if trials else 0
            ),
            "physical_sewout_admitted": bool(sewout_candidates),
            "compiled_equivalence_rule": "outcome_signature",
        },
    }


def load_feedback(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="") as stream:
        return {
            row["trial_id"]: row for row in csv.DictReader(stream)
            if row.get("trial_id")
        }


def write_feedback(path: Path, trials: list[dict], existing: dict[str, dict]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FEEDBACK_FIELDS)
        writer.writeheader()
        for trial in trials:
            row = {
                "trial_id": trial["trial_id"],
                "iteration": trial["iteration"],
                **trial["knobs"],
                "virtual_proxy_score": trial["virtual_proxy_score"],
                "drc_status": trial["drc"]["status"],
                "stitches": trial["metrics"]["stitches"],
                "trims": trial["metrics"]["trims"],
                "jumps": trial["metrics"]["jumps"],
                "overall_score": "",
                "registration_1_5": "",
                "coverage_1_5": "",
                "readability_1_5": "",
                "softness_1_5": "",
                "color_accuracy_1_5": "",
                "thread_breaks": "",
                "notes": "",
                **existing.get(trial["trial_id"], {}),
            }
            writer.writerow({field: row.get(field, "") for field in FEEDBACK_FIELDS})


def apply_knobs(document: VectorDocument, knobs: dict) -> None:
    for layer in document.layers:
        layer.density_multiplier = float(knobs["density_multiplier"])
    for item in document.objects:
        if item.stitch_type == "satin":
            item.satin_spacing_mm = float(knobs["satin_spacing_mm"])
            item.pull_compensation_mm = float(knobs["pull_compensation_mm"])
            item.join_overlap_mm = float(knobs["join_overlap_mm"])
        elif item.stitch_type == "running":
            item.stitch_length_mm = float(knobs["running_stitch_mm"])


def compact_drc(operations: list[dict]) -> dict:
    stitches = sum(item["kind"] == "stitch" for item in operations)
    trims = sum(item["kind"] == "trim" for item in operations)
    jumps = sum(item["kind"] == "jump" for item in operations)
    return {
        "stitch_count": stitches,
        "trim_count": trims,
        "jump_count": jumps,
        "short_stitches": 0,
        "long_stitches": 0,
        "trims_per_1000_stitches": trims * 1000 / max(1, stitches),
        "jumps_per_1000_stitches": jumps * 1000 / max(1, stitches),
    }


def plan_candidate(
    source_bytes_value: bytes,
    base_payload: dict,
    knobs: dict,
    arguments: dict,
) -> dict:
    if base_payload.get("document"):
        document = VectorDocument.from_json(base_payload["document"])
        apply_knobs(document, knobs)
        planned = plan_document(document)
        payload = {
            **base_payload,
            "density_multiplier": knobs["density_multiplier"],
            "document": document.json(),
            "regions": document.json()["objects"],
            "layers": document.json()["layers"],
            "operations": planned["operations"],
            "diagnostics": planned["diagnostics"],
            "brackets": planned["brackets"],
            "findings": planned["findings"],
            "sequence": planned["sequence"],
            "orientations": planned["orientations"],
            "neighbors": planned["neighbors"],
            "constructs": planned["constructs"],
            "thread_economy": planned["thread_economy"],
        }
        payload["drc"] = compact_drc(payload["operations"])
    else:
        payload = digitize_payload(
            source_bytes_value,
            **arguments,
            density_multiplier=knobs["density_multiplier"],
        )
    payload["drc_policy"] = evaluate_drc(payload)
    return payload


def write_trial(
    experiment_root: Path,
    source_path: Path,
    source_hash: str,
    base_payload: dict,
    source_bytes_value: bytes,
    knobs: dict,
    arguments: dict,
    iteration: int,
    pixels_per_mm: int,
) -> dict:
    identifier = candidate_id(knobs)
    name = f"i{iteration:02d}-{identifier}"
    trial_root = experiment_root / "trials" / name
    trial_root.mkdir(parents=True, exist_ok=False)
    payload = plan_candidate(
        source_bytes_value, base_payload, knobs, arguments
    )
    operations = payload["operations"]
    dst_path = trial_root / f"{name}.dst"
    dst_path.write_bytes(as_dst(operations))
    decoded = inspect_dst(dst_path.read_bytes())
    render_path = trial_root / f"{name}.png"
    render_plan(
        decoded,
        payload.get("palette", []),
        render_path,
        pixels_per_mm=pixels_per_mm,
    )
    plan_path = trial_root / f"{name}--plan.json"
    plan_path.write_text(json.dumps(payload, indent=2) + "\n")
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    training_record = build_training_record(
        payload,
        source_sha256=source_hash,
        source_ref=display_path(source_path),
        settings={**arguments, "density_multiplier": knobs["density_multiplier"]},
        plan_ref=str(plan_path.relative_to(experiment_root)),
        plan_sha256=plan_hash,
    )
    policy = payload["drc_policy"]
    proxy = virtual_proxy_score(policy)
    training_record["experiment"] = {
        "iteration": iteration,
        "trial_id": identifier,
        "knobs": knobs,
        "target_kind": "virtual_proxy_pending_physical_feedback",
        "virtual_proxy_score": proxy,
    }
    training_path = trial_root / f"{name}--training-record.json"
    training_path.write_text(json.dumps(training_record, indent=2) + "\n")
    evidence = policy["evidence"]
    return {
        "trial_id": identifier,
        "name": name,
        "iteration": iteration,
        "created_at": utc_now(),
        "knobs": knobs,
        "virtual_proxy_score": proxy,
        "drc": policy,
        "metrics": {
            "stitches": evidence["stitch_count"],
            "trims": evidence["trim_count"],
            "jumps": evidence["jump_count"],
            "trims_per_1000": evidence["trims_per_1000_stitches"],
            "jumps_per_1000": evidence["jumps_per_1000_stitches"],
        },
        "artifacts": {
            "dst": str(dst_path.relative_to(experiment_root)),
            "render": str(render_path.relative_to(experiment_root)),
            "plan": str(plan_path.relative_to(experiment_root)),
            "training_record": str(training_path.relative_to(experiment_root)),
        },
    }


def write_gallery(output: Path, state: dict, feedback: dict[str, dict]) -> None:
    cards = []
    for trial in sorted(
        state["trials"], key=lambda item: (item["iteration"], item["name"])
    ):
        rating = physical_score(feedback.get(trial["trial_id"], {}))
        cards.append(
            '<article style="background:white;padding:16px;border:1px solid #ccd4ce">'
            f'<h2>{html.escape(trial["name"])}</h2>'
            f'<img src="{html.escape(trial["artifacts"]["render"])}" '
            'style="max-width:100%;background:#eee">'
            f'<p>DRC <b>{html.escape(trial["drc"]["status"])}</b> · '
            f'proxy {trial["virtual_proxy_score"]:.1f} · '
            f'physical {rating if rating is not None else "pending"}</p>'
            f'<p>{trial["metrics"]["stitches"]:,} stitches · '
            f'{trial["metrics"]["trims"]} trims · '
            f'{trial["metrics"]["jumps"]} jumps</p>'
            f'<pre>{html.escape(json.dumps(trial["knobs"], indent=2))}</pre>'
            f'<p><a href="{html.escape(trial["artifacts"]["dst"])}">DST</a> · '
            f'<a href="{html.escape(trial["artifacts"]["plan"])}">plan</a></p>'
            '</article>'
        )
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Sew-out learning loop</title>'
        '<style>body{margin:28px;background:#f4f1ea;color:#172a2d;font:14px sans-serif}'
        '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));'
        'gap:18px}pre{font-size:12px}</style>'
        f'<h1>{html.escape(state["name"])}</h1>'
        '<p>Virtual proxy scores rank DRC and thread economy only. Physical '
        'improvement requires ratings in <code>feedback.csv</code>.</p>'
        f'<pre>{html.escape(json.dumps(state.get("analysis", {}), indent=2))}</pre>'
        f'<div class="grid">{"".join(cards)}</div>'
    )


def run(options: argparse.Namespace) -> int:
    source = options.source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output = options.output.resolve()
    state_path = output / "experiment.json"
    if state_path.exists() and not options.resume:
        raise FileExistsError(
            f"{output} already contains an experiment; use --resume"
        )
    output.mkdir(parents=True, exist_ok=True)
    encoded, _ = source_bytes(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    arguments = {
        "width_mm": options.width_mm,
        "colors": options.colors,
        "profile": options.profile,
    }
    base_payload = digitize_payload(
        encoded, **arguments, density_multiplier=1.0
    )
    active_knobs, inactive_knobs = knob_activation(base_payload)
    if not active_knobs:
        raise ValueError("this plan has no currently tunable compiled controls")
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["source_sha256"] != source_hash:
            raise ValueError("source content differs from the existing experiment")
    else:
        state = {
            "schema_version": SCHEMA_VERSION,
            "name": options.name or f"{slug(source.stem)} sew-out learning loop",
            "source": display_path(source),
            "source_sha256": source_hash,
            "settings": arguments,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "iterations": [],
            "trials": [],
            "active_knobs": active_knobs,
            "inactive_knobs": inactive_knobs,
        }
    state.setdefault("active_knobs", active_knobs)
    state.setdefault("inactive_knobs", inactive_knobs)
    feedback_path = output / "feedback.csv"
    feedback = load_feedback(feedback_path)
    start_iteration = (
        max((trial["iteration"] for trial in state["trials"]), default=-1) + 1
    )
    for iteration in range(start_iteration, start_iteration + options.iterations):
        selected, model = select_candidates(
            state["trials"], feedback, options.candidates_per_iteration,
            state["active_knobs"],
        )
        if not selected:
            break
        created = []
        for knobs in selected:
            print(
                f"[learning-loop] iteration {iteration} · {candidate_id(knobs)}",
                flush=True,
            )
            trial = write_trial(
                output, source, source_hash, base_payload, encoded, knobs,
                arguments, iteration, options.pixels_per_mm,
            )
            state["trials"].append(trial)
            created.append(trial["trial_id"])
            state["updated_at"] = utc_now()
            atomic_json(state_path, state)
        state["iterations"].append({
            "iteration": iteration,
            "created_at": utc_now(),
            "model": model,
            "trial_ids": created,
        })
        atomic_json(state_path, state)
        # Newly entered physical scores affect the next iteration. Within one
        # invocation, virtual results provide the provisional surrogate target.
    write_feedback(feedback_path, state["trials"], feedback)
    feedback = load_feedback(feedback_path)
    state["analysis"] = experiment_analysis(state, feedback)
    atomic_json(state_path, state)
    write_gallery(output, state, feedback)
    with (output / "training-records.jsonl").open("w") as stream:
        for trial in state["trials"]:
            record = json.loads(
                (output / trial["artifacts"]["training_record"]).read_text()
            )
            rating = physical_score(feedback.get(trial["trial_id"], {}))
            if rating is not None:
                record["experiment"]["physical_score"] = rating
                record["experiment"]["target_kind"] = "physical_sewout"
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"[learning-loop] gallery: {output / 'index.html'}")
    print(f"[learning-loop] physical feedback: {feedback_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--candidates-per-iteration", type=int, default=3)
    parser.add_argument("--width-mm", type=float, default=110)
    parser.add_argument("--colors", type=int, default=8)
    parser.add_argument("--profile", default="structured_cap")
    parser.add_argument("--pixels-per-mm", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

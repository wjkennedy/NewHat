"""Create leakage-resistant ML records with deterministic DRC supervision."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from typing import Any

try:
    from .drc_policy import POLICY_VERSION, evaluate_drc
except ImportError:  # direct module import from tools with prototype on sys.path
    from drc_policy import POLICY_VERSION, evaluate_drc

TRAINING_SCHEMA_VERSION = 1
TRAINING_METHOD_VERSION = "new-hat-semantic-training/1.0"
REVIEWED_STATES = {"approved", "rejected"}
TRAINING_USE = {"allowed", "evaluation_only", "prohibited", "unspecified"}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def deterministic_split(group: str, *, evaluation_only: bool = False) -> str:
    """Split by source family so variants of one mark cannot leak."""
    bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 100
    if evaluation_only:
        return "validation" if bucket < 50 else "test"
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def _histogram(regions: list[dict], field: str) -> dict[str, int]:
    return dict(Counter(
        str(region.get(field, "unspecified")) for region in regions
    ))


def _object_proposals(payload: dict) -> list[dict]:
    evidence: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: {"findings": [], "brackets": []}
    )
    for finding in payload.get("findings", []):
        if isinstance(finding, dict) and finding.get("object_id"):
            evidence[str(finding["object_id"])]["findings"].append({
                key: finding.get(key)
                for key in ("rule", "severity", "message")
                if key in finding
            })
    for bracket in payload.get("brackets", []):
        if isinstance(bracket, dict) and bracket.get("object_id"):
            evidence[str(bracket["object_id"])]["brackets"].append({
                key: bracket.get(key)
                for key in ("rule", "status", "decision", "observed", "unit")
                if key in bracket
            })
    result = []
    fields = (
        "id", "element_id", "layer_id", "physical_layer", "visual_role",
        "pass_name", "pass_index", "stitch_type", "color", "color_index",
        "closed", "underlay_enabled", "fill_spacing_mm", "satin_spacing_mm",
        "stitch_length_mm", "fill_angle_degrees", "pull_compensation_mm",
        "join_overlap_mm", "sequence_order", "lock_sequence",
    )
    for region in payload.get("regions", []):
        if not isinstance(region, dict):
            continue
        object_id = str(region.get("id", "unknown"))
        record = {
            key: region.get(key)
            for key in fields
            if key in region
        }
        record["geometry_counts"] = {
            "nodes": len(region.get("nodes", [])),
            "boundaries": len(region.get("boundaries", [])),
            "satin_rails": len(region.get("satin_rails", [])),
        }
        record["drc_evidence"] = evidence.get(
            object_id, {"findings": [], "brackets": []}
        )
        result.append(record)
    return result


def _machine_metrics(payload: dict, gate: dict) -> dict:
    drc = payload.get("drc", {}) if isinstance(payload.get("drc"), dict) else {}
    fields = (
        "stitch_count", "segments", "minimum_stitch_mm", "maximum_stitch_mm",
        "mean_stitch_mm", "short_stitches", "long_stitches", "jump_count",
        "machine_jump_count", "object_jump_count", "internal_jump_count",
        "jump_distance_mm", "trim_count", "structural_trim_count",
        "detail_trim_count", "color_change_count", "object_count",
        "geometry_element_count", "design_area_sq_in", "stitches_per_sq_in",
        "jumps_per_1000_stitches", "trims_per_1000_stitches",
        "short_fill_edge_rows", "local_density_variance", "quality_score",
    )
    metrics = {
        **{field: drc.get(field) for field in fields if field in drc},
        "semantic_violations": drc.get("semantic_violations", {}),
        "budget_violations": drc.get("budget_violations", {}),
    }
    for field in (
        "stitch_count", "short_stitches", "long_stitches", "trim_count",
        "jump_count", "trims_per_1000_stitches",
        "jumps_per_1000_stitches",
    ):
        if gate["evidence"].get(field) is not None:
            metrics[field] = gate["evidence"][field]
    if metrics.get("quality_score") is None:
        metrics["quality_score"] = gate["evidence"].get("quality_score")
    return metrics


def build_training_record(
    payload: dict,
    *,
    source_sha256: str,
    source_ref: str,
    settings: dict,
    plan_ref: str,
    plan_sha256: str | None = None,
    annotation: dict | None = None,
) -> dict:
    """Build one record; unreviewed assets are quarantined by construction."""
    annotation = dict(annotation or {})
    training_use = str(annotation.get("training_use", "unspecified"))
    if training_use not in TRAINING_USE:
        raise ValueError(f"unsupported training_use: {training_use}")
    review_status = str(annotation.get("review_status", "unreviewed"))
    source_group = str(
        annotation.get("split_group")
        or annotation.get("design_family")
        or source_sha256
    )
    split = deterministic_split(
        source_group,
        evaluation_only=training_use == "evaluation_only",
    )
    approved_plan = bool(annotation.get("expert_plan_approved", False))
    reviewed = review_status in REVIEWED_STATES
    rights_allow_evaluation = training_use in {"allowed", "evaluation_only"}
    eligibility = {
        "semantic_supervision": (
            training_use == "allowed"
            and review_status == "approved"
            and approved_plan
        ),
        "drc_auxiliary_supervision": (
            training_use == "allowed" and reviewed
        ),
        "evaluation": rights_allow_evaluation and reviewed,
        "quarantined": not (
            (training_use == "allowed" and reviewed)
            or (rights_allow_evaluation and reviewed)
        ),
    }
    if eligibility["quarantined"]:
        lane = "quarantine"
    elif training_use == "allowed":
        lane = split
    else:
        lane = split

    dimensions = payload.get("dimensions_mm", {})
    regions = [
        region for region in payload.get("regions", [])
        if isinstance(region, dict)
    ]
    gate = evaluate_drc(payload)
    example_material = {
        "source_sha256": source_sha256,
        "settings": settings,
        "profile": payload.get("profile", settings.get("profile", "standard")),
    }
    example_id = _canonical_hash(example_material)[:24]
    revision_hash = plan_sha256 or _canonical_hash(payload)
    record = {
        "schema_version": TRAINING_SCHEMA_VERSION,
        "method_version": TRAINING_METHOD_VERSION,
        "drc_policy_version": POLICY_VERSION,
        "example_id": example_id,
        "revision_id": revision_hash[:24],
        "split_group": source_group,
        "split": split,
        "lane": lane,
        "provenance": {
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "plan_ref": plan_ref,
            "plan_sha256": revision_hash,
            "license": annotation.get("license", "unspecified"),
            "training_use": training_use,
            "review_status": review_status,
            "reviewer": annotation.get("reviewer"),
            "inference_method": payload.get(
                "posterization", {}
            ).get("selected_planner", payload.get("algorithm", "unknown")),
            "parser_or_engine_version": payload.get("version"),
        },
        "features": {
            "production_context": {
                "width_mm": dimensions.get(
                    "width", payload.get("width_mm", settings.get("width_mm"))
                ),
                "height_mm": dimensions.get(
                    "height", payload.get("height_mm")
                ),
                "profile": payload.get(
                    "profile", settings.get("profile", "standard")
                ),
                "density_multiplier": payload.get(
                    "density_multiplier",
                    settings.get("density_multiplier", 1.0),
                ),
                "requested_colors": settings.get("colors"),
            },
            "artwork_observations": {
                key: payload.get("posterization", {}).get(key)
                for key in (
                    "method", "source_size_px", "foreground_pixels",
                    "foreground_color_coverage",
                    "source_geometry_elements", "semantic_color_count",
                    "stroke_vector_candidate", "selected_planner",
                    "color_separation", "construction_mode",
                    "running_passes",
                )
                if key in payload.get("posterization", {})
            },
        },
        "proposal": {
            "algorithm": payload.get("algorithm"),
            "palette_size": len(payload.get("palette", [])),
            "color_separation": payload.get(
                "posterization", {}
            ).get("color_separation", {}),
            "construction_mode": payload.get(
                "posterization", {}
            ).get("construction_mode", "auto"),
            "object_count": len(regions),
            "stitch_types": _histogram(regions, "stitch_type"),
            "physical_passes": _histogram(regions, "pass_name"),
            "objects": _object_proposals(payload),
        },
        "labels": {
            "drc": gate,
            "drc_auxiliary_targets": {
                item["rule"]: item["count"]
                for item in (
                    gate["hard_blockers"]
                    + gate["conditional_blockers"]
                    + gate["review_findings"]
                )
            },
            "machine_metrics": _machine_metrics(payload, gate),
            "human": {
                "review_status": review_status,
                "acceptance": annotation.get("human_acceptance", "unknown"),
                "expert_plan_approved": approved_plan,
                "corrections_ref": annotation.get("corrections_ref"),
                "physical_sewout": annotation.get("physical_sewout"),
            },
        },
        "eligibility": eligibility,
        "drc_override": annotation.get("drc_override"),
    }
    return record


def record_integrity_findings(record: dict) -> list[str]:
    """Reject silent DRC overrides and unsafe supervised examples."""
    findings = []
    provenance = record.get("provenance", {})
    if not provenance.get("source_sha256"):
        findings.append("missing source SHA-256")
    if provenance.get("training_use") == "allowed" and (
        provenance.get("license") in {None, "", "unspecified"}
    ):
        findings.append("training use is allowed without a recorded license")
    gate = record.get("labels", {}).get("drc", {})
    acceptance = (
        record.get("labels", {}).get("human", {}).get("acceptance", "unknown")
    )
    override = record.get("drc_override")
    if gate.get("status") == "block" and acceptance == "accepted":
        if not (
            isinstance(override, dict)
            and override.get("approved_by")
            and override.get("reason")
        ):
            findings.append(
                "accepted example has blocking DRC without an attributed override"
            )
    if (
        record.get("eligibility", {}).get("semantic_supervision")
        and gate.get("status") == "block"
        and not override
    ):
        findings.append(
            "blocking plan cannot be an approved semantic target without override"
        )
    return findings

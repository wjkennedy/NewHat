"""Compile temporary training-session artifacts into portable bundle v2."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import platform
import re
from pathlib import Path
import subprocess
import sys
import zipfile


BUNDLE_SCHEMA_VERSION = "2.0.0"
GENERATOR_VERSION = "new-hat-training-bundle/2.0.0"
METRIC_VERSION = "new-hat-metrics/2.0.0"
REWARD_VERSION = "embroidery-reward/2.0.0"
REVIEW_SCORE_AXES = {
    "silhouette_fidelity",
    "detail_retention",
    "boundary_smoothness",
    "lettering_legibility",
    "negative_space_integrity",
    "coverage",
    "stitch_family_suitability",
    "color_fidelity",
    "thread_economy",
    "path_efficiency",
    "visible_defect_severity",
    "production_acceptability",
}
PORTABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

DEFAULT_PARAMETERS = {
    "color_merge_tolerance": 8.0,
    "fragmented_color_threshold": 0.20,
    "posterization_smoothing_px": 0.0,
    "construction_mode": "auto",
    "density_multiplier": 1.0,
    "running_passes": 3,
    "satin_spacing_mm": None,
}
CONTROLLED_FIELDS = {
    "color_separation": [
        "color_merge_tolerance", "fragmented_color_threshold",
    ],
    "posterization_smoothing": ["posterization_smoothing_px"],
    "stitch_family": ["construction_mode", "running_passes"],
    "tatami_density": ["construction_mode", "density_multiplier"],
    "running_passes": ["construction_mode", "running_passes"],
    "satin_spacing_mm": ["satin_spacing_mm"],
}

SCHEMAS = {
    "manifest.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/manifest/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "bundle_id", "session_id", "status",
            "indexes", "training_ready",
        ],
        "properties": {
            "schema_version": {"const": BUNDLE_SCHEMA_VERSION},
            "bundle_id": {"type": "string"},
            "session_id": {"type": "string"},
            "status": {"const": "complete"},
            "training_ready": {"type": "boolean"},
            "indexes": {"type": "object"},
        },
    },
    "state.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/state/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "state_id", "source_ref",
            "resolved_parameters", "production_context_ref",
        ],
    },
    "candidate.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/candidate/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "candidate_id", "effective_candidate_id",
            "parent_state_id", "action_hash", "plan_hash", "output_hash",
            "status", "learning_weight",
        ],
    },
    "metrics.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/metrics/2.0.0",
        "type": "object",
        "required": ["schema_version", "metric_version", "measurements"],
    },
    "review.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/review/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "review_id", "comparison_id",
            "completion_state", "disposition", "reviewed_candidate_ids",
            "ranking", "candidate_scores", "candidate_defects",
        ],
    },
    "reward.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/reward/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "reward_id", "candidate_id",
            "feasible", "components", "aggregate_status",
        ],
    },
    "transition.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/transition/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "transition_id", "status",
            "state_before", "state_after",
        ],
    },
    "source.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/source/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "source_id", "artwork_family_id",
            "original", "normalized",
        ],
    },
    "proposal.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/proposal/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "proposal_id", "parent_state_id",
            "controlled_parameter_set", "candidate_ids",
        ],
    },
    "action.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/action/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "candidate_id", "parent_state_id",
            "action_delta", "resolved_parameters", "action_hash",
        ],
    },
    "recipe.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/recipe/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "candidate_id", "recipe_version",
            "objects", "parameter_provenance",
        ],
    },
    "layers.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/layers/2.0.0",
        "type": "object",
        "required": ["schema_version", "candidate_id", "layers"],
    },
    "drc.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/drc/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "candidate_id", "authoritative_status",
            "round_trip",
        ],
    },
    "comparison.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/comparison/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "comparison_id", "proposal_id",
            "candidate_ids", "effective_candidate_ids",
        ],
    },
    "environment.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/environment/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "generator", "components", "git",
            "dependency_lock_sha256", "runtime", "invocation",
        ],
    },
    "rights.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/rights/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "source_id", "status", "learnable",
        ],
    },
    "production-profile.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/production-profile/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "production_profile_id", "product_type",
            "machine", "frame", "fabric", "thread", "needle",
        ],
    },
    "effective-candidate.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/effective-candidate/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "effective_candidate_id",
            "candidate_ids", "learning_weight",
        ],
    },
    "artifact-refs.schema.json": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "new-hat://training-bundle/artifact-refs/2.0.0",
        "type": "object",
        "required": [
            "schema_version", "candidate_id", "artifacts",
        ],
    },
}

METRIC_DEFINITIONS = {
    "semantic_stitches": {
        "unit": "count", "direction": "minimize",
        "definition": "Stitch commands before machine encoding.",
        "method": "semantic_stream", "version": METRIC_VERSION,
    },
    "semantic_jumps": {
        "unit": "count", "direction": "minimize",
        "definition": "Logical jump operations before machine encoding.",
        "method": "semantic_stream", "version": METRIC_VERSION,
    },
    "machine_jumps": {
        "unit": "count", "direction": "minimize",
        "definition": "Jump records after DST encoding and decoding.",
        "method": "decoded_dst", "version": METRIC_VERSION,
    },
    "trims": {
        "unit": "count", "direction": "minimize",
        "definition": "Semantic trim operations.",
        "method": "semantic_stream", "version": METRIC_VERSION,
    },
    "color_changes": {
        "unit": "count", "direction": "minimize",
        "definition": "Decoded machine color-change records.",
        "method": "decoded_dst", "version": METRIC_VERSION,
    },
    "object_count": {
        "unit": "count", "direction": "minimize",
        "definition": "Semantic stitch objects in the candidate plan.",
        "method": "stitch_plan", "version": METRIC_VERSION,
    },
    "minimum_stitch_mm": {
        "unit": "mm", "direction": "constraint",
        "definition": "Shortest semantic stitch segment.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "maximum_stitch_mm": {
        "unit": "mm", "direction": "constraint",
        "definition": "Longest semantic stitch segment.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "short_stitches": {
        "unit": "count", "direction": "minimize",
        "definition": "Segments below the applicable stitch-family minimum.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "long_stitches": {
        "unit": "count", "direction": "minimize",
        "definition": "Segments above the machine or production limit.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "jump_distance_mm": {
        "unit": "mm", "direction": "minimize",
        "definition": "Total non-sewing travel distance.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "stitches_per_sq_in": {
        "unit": "stitches_per_square_inch", "direction": "target",
        "definition": "Needle penetrations divided by realized design area.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "short_fill_edge_rows": {
        "unit": "count", "direction": "minimize",
        "definition": "Short tatami edge rows requiring construction review.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "local_density_variance": {
        "unit": "variance", "direction": "minimize",
        "definition": "Variance of penetrations in the declared local-density grid.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "quality_score": {
        "unit": "score_0_100", "direction": "maximize",
        "definition": "Versioned diagnostic score; never a feasibility substitute.",
        "method": "stitch_plan_drc", "version": METRIC_VERSION,
    },
    "color_change_parity_error": {
        "unit": "count", "direction": "constraint",
        "definition": "Absolute difference between semantic and decoded color changes.",
        "method": "dst_round_trip", "version": METRIC_VERSION,
    },
    "round_trip_coordinate_error_mm": {
        "unit": "mm", "direction": "constraint",
        "definition": "Maximum command-coordinate error after DST round trip.",
        "method": "dst_round_trip", "version": METRIC_VERSION,
    },
    "silhouette_iou": {
        "unit": "ratio", "direction": "maximize",
        "definition": "Posterized silhouette intersection over source mask union.",
        "method": "analytical_geometry", "version": METRIC_VERSION,
    },
    "mean_color_error_rgb": {
        "unit": "rgb_euclidean", "direction": "minimize",
        "definition": "Legacy RGB error; diagnostic only, not reward-bearing.",
        "method": "legacy_color_separation", "version": "legacy/1",
    },
}


def canonical_json(value) -> bytes:
    """Serialize hash-bearing JSON with one deterministic representation."""
    def normalize(item):
        if isinstance(item, dict):
            return {
                str(key): normalize(item[key])
                for key in sorted(item)
            }
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("Canonical JSON cannot contain non-finite numbers")
            rounded = round(item, 9)
            return 0.0 if rounded == 0 else rounded
        if item is None or isinstance(item, (str, int, bool)):
            return item
        raise TypeError(f"Unsupported canonical JSON value: {type(item)!r}")

    return json.dumps(
        normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def opaque_id(prefix: str, value) -> str:
    return f"{prefix}_{digest_bytes(canonical_json(value))[:24]}"


def _portable_id(value, fallback: str) -> str:
    candidate = str(value or "")
    return candidate if PORTABLE_ID.fullmatch(candidate) else fallback


def _deep_merge(baseline: dict, overrides: dict) -> dict:
    result = dict(baseline)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _without_volatile_plan_fields(value):
    if isinstance(value, dict):
        return {
            key: _without_volatile_plan_fields(child)
            for key, child in value.items()
            if key not in {
                "preview_data_url", "created_at", "saved_at",
                "started_at", "completed_at",
            }
        }
    if isinstance(value, list):
        return [_without_volatile_plan_fields(child) for child in value]
    return value


def _git_value(root: Path, *arguments: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _environment(project_root: Path, exported_at: str) -> dict:
    lock_candidates = [
        project_root / "package-lock.json",
        project_root / "requirements.txt",
        project_root / "pyproject.toml",
    ]
    lock_content = b"".join(
        path.name.encode() + b"\0" + path.read_bytes()
        for path in lock_candidates if path.exists()
    )
    status = _git_value(project_root, "status", "--porcelain")
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generator": GENERATOR_VERSION,
        "components": {
            "digitizer": "new-hat-digitizer/2.1",
            "dst_codec": "pystitch-local/1.0.1",
            "virtual_sewout": "new-hat-canvas-sewout/1",
            "drc_policy": "new-hat-drc/1.1",
        },
        "git": {
            "commit": _git_value(project_root, "rev-parse", "HEAD"),
            "dirty": bool(status),
            "dirty_files": status.splitlines() if status else [],
        },
        "dependency_lock_sha256": (
            digest_bytes(lock_content) if lock_content else None
        ),
        "runtime": {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "operating_system": platform.platform(),
            "architecture": platform.machine(),
        },
        "invocation": {
            "entrypoint": "POST /api/training/session/export",
            "arguments": [],
            "deterministic_seed": 0,
        },
        "exported_at_utc": exported_at,
    }


class BundleFiles:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def add_bytes(self, path: str, content: bytes) -> None:
        if path in self.files and self.files[path] != content:
            raise ValueError(f"Bundle path collision: {path}")
        self.files[path] = content

    def add_json(self, path: str, value: dict | list) -> None:
        self.add_bytes(path, canonical_json(value) + b"\n")

    def replace_json(self, path: str, value: dict | list) -> None:
        """Replace a generated index record after its final weight is known."""
        if path not in self.files:
            raise ValueError(f"Bundle path does not exist: {path}")
        self.files[path] = canonical_json(value) + b"\n"

    def add_object(
        self,
        content: bytes,
        *,
        media_type: str,
        logical_name: str,
        content_encoding: str | None = None,
    ) -> dict:
        stored = content
        if content_encoding == "gzip":
            stored = gzip.compress(content, compresslevel=9, mtime=0)
        digest = digest_bytes(stored)
        path = f"objects/sha256/{digest}"
        self.add_bytes(path, stored)
        return {
            "sha256": digest,
            "path": path,
            "byte_length": len(stored),
            "decoded_byte_length": len(content),
            "media_type": media_type,
            "content_encoding": content_encoding,
            "logical_name": logical_name,
        }


def _resolved_action(parameter: str, value, parameters: dict) -> dict:
    action = {
        key: item for key, item in parameters.items()
        if item is not None
    }
    if parameter == "running_passes" and "running_passes" not in action:
        action["running_passes"] = int(value)
    elif parameter == "tatami_density" and "density_multiplier" not in action:
        action["density_multiplier"] = float(value)
    elif parameter == "posterization_smoothing" and (
        "posterization_smoothing_px" not in action
    ):
        smoothing = {
            "none": 0.0, "light": 0.5, "balanced": 0.85,
            "smooth": 1.25, "strong": 2.0,
        }
        action["posterization_smoothing_px"] = smoothing.get(str(value))
    return {key: item for key, item in action.items() if item is not None}


def _metric(name: str, raw_value, normalized_value=None) -> dict:
    definition = METRIC_DEFINITIONS[name]
    return {
        "name": name,
        **definition,
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "production_profile": "unknown",
    }


def _status(plan: dict, decoded: list[dict], round_trip_pass: bool) -> dict:
    drc = plan.get("drc", {})
    policy = plan.get("drc_policy", {})
    hard_reasons = []
    if not round_trip_pass:
        hard_reasons.append("dst_round_trip_failed")
    if not any(item.get("kind") == "stitch" for item in decoded):
        hard_reasons.append("zero_decoded_stitches")
    if policy.get("status") == "block":
        hard_reasons.extend(
            item.get("rule", "drc_block")
            for item in policy.get("hard_blockers", [])
        )
    if int(drc.get("long_stitches", 0) or 0) > 0:
        hard_reasons.append("maximum_stitch_exceeded")
    advisory = []
    if drc.get("budget_pass") is False:
        advisory.append("planning_budget_failed")
    if policy.get("status") == "review":
        advisory.append("drc_review")
    feasibility = "blocked" if hard_reasons else "eligible"
    review = "review" if advisory else "eligible"
    return {
        "status": "blocked" if hard_reasons else review,
        "hard_feasibility_status": feasibility,
        "planning_budget_status": (
            "within_budget" if drc.get("budget_pass", True)
            else "over_budget"
        ),
        "review_eligibility_status": (
            "ineligible" if hard_reasons else "eligible"
        ),
        "hard_blockers": sorted(set(hard_reasons)),
        "advisory_findings": sorted(set(advisory)),
        "legacy_candidate_status": None,
    }


def _construction_semantics(role: str, stitch_type: str) -> dict:
    table = {
        "design_center_underlay": (
            "foundation", "running", "center_run",
            "registration_support", "supports",
        ),
        "silhouette_tatami_underlay": (
            "foundation", "tatami", "tatami_underlay",
            "registration_support", "supports",
        ),
        "edge_underlay": (
            "underlay", "running", "edge_run",
            "registration_support", "supports",
        ),
        "fill_underlay": (
            "underlay", "tatami", "tatami_underlay",
            "registration_support", "supports",
        ),
        "top_fill": (
            "blocking", "tatami", "tatami_fill",
            "interior_fill", "independent",
        ),
        "top_fill_edge": (
            "blocking", "tatami", "tatami_fill",
            "interior_fill", "independent",
        ),
        "top_detail_running": (
            "detail", "running", "layered_run",
            "detail", "overlays",
        ),
        "vector_satin_top": (
            "detail", "satin", "satin_column",
            "standalone_line", "independent",
        ),
        "vector_running_top": (
            "detail", "running", "single_run",
            "standalone_line", "independent",
        ),
    }
    values = table.get(role, (
        "detail",
        "tatami" if stitch_type == "fill" else stitch_type or "running",
        "tatami_fill" if stitch_type in {"fill", "tatami"} else "single_run",
        "detail",
        "independent",
    ))
    return dict(zip(
        (
            "layer_role", "construction_family",
            "construction_technique", "semantic_role", "relationship",
        ),
        values,
    ))


def _decoded_operation_spans(
    operations: list[dict], decoded: list[dict]
) -> dict[int, tuple[int, int]]:
    """Map semantic commands to the decoded records that realize them."""
    spans = {}
    cursor = 0
    for semantic_index, operation in enumerate(operations):
        kind = operation.get("kind")
        if kind not in {"stitch", "jump", "trim", "color", "end"}:
            continue
        for decoded_index in range(cursor, len(decoded)):
            command = decoded[decoded_index]
            if command.get("kind") != kind:
                continue
            if kind in {"stitch", "jump"}:
                if not all(
                    abs(float(command.get(axis, 0))
                        - float(operation.get(axis, 0))) <= 0.11
                    for axis in ("x", "y")
                ):
                    continue
            spans[semantic_index] = (cursor, decoded_index)
            cursor = decoded_index + 1
            break
    return spans


def _recipe(
    plan: dict, resolved_parameters: dict, decoded: list[dict]
) -> tuple[dict, dict]:
    operations = plan.get("operations", [])
    decoded_spans = _decoded_operation_spans(operations, decoded)
    regions = plan.get("regions", [])
    spans: list[tuple[tuple[str, str], list[int]]] = []
    for index, operation in enumerate(operations):
        region_id = str(operation.get("region_id") or "design")
        role = str(operation.get("role") or operation.get("kind") or "unknown")
        key = (region_id, role)
        if spans and spans[-1][0] == key:
            spans[-1][1][1] = index
        else:
            spans.append((key, [index, index]))
    region_by_id = {
        str(region.get("id")): region for region in regions
        if region.get("id")
    }
    object_layers: dict[str, list[dict]] = defaultdict(list)
    ordered_layers = []
    for order, ((region_id, role), span) in enumerate(spans):
        region = region_by_id.get(region_id, {})
        semantics = _construction_semantics(
            role, str(region.get("stitch_type", ""))
        )
        layer_id = f"{region_id}-{order:04d}"
        decoded_indexes = [
            decoded_index
            for semantic_index in range(span[0], span[1] + 1)
            for decoded_index in decoded_spans.get(semantic_index, ())
        ]
        layer = {
            "layer_id": layer_id,
            "order": order,
            **semantics,
            "operation_role": role,
            "relationship": {
                "type": semantics.pop("relationship"),
                "target_layer_id": None,
            },
            "resolved_parameters": resolved_parameters,
            "operation_span": {
                "semantic_start": span[0],
                "semantic_end": span[1],
                "decoded_dst_start": (
                    min(decoded_indexes) if decoded_indexes else None
                ),
                "decoded_dst_end": (
                    max(decoded_indexes) if decoded_indexes else None
                ),
            },
        }
        object_layers[region_id].append(layer)
        ordered_layers.append({
            "layer_id": layer_id,
            "object_id": region_id,
            "order": order,
            **{
                key: layer[key] for key in (
                    "layer_role", "construction_family",
                    "construction_technique", "semantic_role",
                )
            },
            "operation_span": layer["operation_span"],
        })
    for layers_for_object in object_layers.values():
        for index, layer in enumerate(layers_for_object):
            relation = layer["relationship"]["type"]
            target = None
            if relation == "supports":
                target = next(
                    (
                        candidate["layer_id"]
                        for candidate in layers_for_object[index + 1:]
                        if candidate["layer_role"] not in {
                            "foundation", "underlay",
                        }
                    ),
                    None,
                )
            elif relation in {"overlays", "covers", "borders"}:
                target = next(
                    (
                        candidate["layer_id"]
                        for candidate in reversed(
                            layers_for_object[:index]
                        )
                        if candidate["layer_role"] not in {
                            "travel", "detail",
                        }
                    ),
                    None,
                )
            layer["relationship"]["target_layer_id"] = target
    objects = []
    for object_id in sorted(set(region_by_id) | set(object_layers)):
        region = region_by_id.get(object_id, {})
        objects.append({
            "object_id": object_id,
            "source_region_ref": None,
            "color": {
                "source_rgb": region.get("color"),
                "thread_ref": region.get("color"),
            },
            "geometry": {
                "pixels": region.get("pixels"),
                "bounds_px": region.get("bounds"),
                "area_mm2": region.get("area_mm2"),
                "estimated_width_mm": region.get("estimated_width_mm"),
            },
            "layers": object_layers.get(object_id, []),
        })
    return (
        {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "recipe_version": "new-hat-recipe/2.0.0",
            "objects": objects,
            "parameter_provenance": [
                {
                    "parameter": key,
                    "value": value,
                    "unit": "mm" if key.endswith("_mm") else None,
                    "source": "resolved_state",
                    "source_ref": None,
                    "baseline_value": DEFAULT_PARAMETERS.get(key),
                    "active": True,
                    "consumed_by": "new-hat-digitizer/2.1",
                    "actuation": {
                        "semantic_stream_changed": None,
                        "encoded_stream_changed": None,
                    },
                }
                for key, value in sorted(resolved_parameters.items())
            ],
        },
        {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "layers": ordered_layers,
        },
    )


def _round_trip(dst: bytes, decoded: list[dict], as_dst, inspect_dst) -> dict:
    try:
        reencoded = as_dst(decoded)
        redecoded = inspect_dst(reencoded)
        first_hash = digest_bytes(canonical_json(decoded))
        second_hash = digest_bytes(canonical_json(redecoded))
        return {
            "passed": first_hash == second_hash,
            "decoded_command_hash": first_hash,
            "redecoded_command_hash": second_hash,
            "encoded_byte_sha256": digest_bytes(dst),
            "reencoded_byte_sha256": digest_bytes(reencoded),
            "coordinate_tolerance_mm": 0.1,
        }
    except Exception as error:
        return {
            "passed": False,
            "error": f"{type(error).__name__}: {error}",
            "encoded_byte_sha256": digest_bytes(dst),
        }


def _parameter_provenance(
    baseline: dict,
    resolved: dict,
    action: dict,
    proposal_id: str,
    plan_hash: str,
    output_hash: str,
) -> list[dict]:
    return [
        {
            "parameter": key,
            "value": value,
            "unit": "mm" if key.endswith("_mm") else None,
            "source": (
                "candidate_action" if key in action
                else "parent_profile"
            ),
            "source_ref": proposal_id if key in action else "parent_state",
            "baseline_value": baseline.get(key),
            "active": key in action,
            "consumed_by": "new-hat-digitizer/2.1",
            "actuation": {
                "semantic_stream_changed": None if key in action else False,
                "encoded_stream_changed": None if key in action else False,
            },
        }
        for key, value in sorted(resolved.items())
    ]


def _validate_bundle(files: BundleFiles, manifest: dict) -> list[str]:
    findings = []
    required = {
        "manifest.json", "source/source.json",
        "context/production-profile.json", "context/rights.json",
        "generator/environment.json",
    }
    missing = sorted(required - set(files.files))
    findings.extend(f"missing_required_file:{path}" for path in missing)
    for path, content in files.files.items():
        if path.startswith("objects/sha256/"):
            expected_digest = path.rsplit("/", 1)[-1]
            if digest_bytes(content) != expected_digest:
                findings.append(f"content_address_mismatch:{path}")
    for index_name, paths in manifest["indexes"].items():
        if not isinstance(paths, list):
            continue
        for path in paths:
            if path not in files.files:
                findings.append(f"unresolved_index:{index_name}:{path}")
    def schema_name(path: str) -> str | None:
        direct = {
            "manifest.json": "manifest.schema.json",
            "source/source.json": "source.schema.json",
            "context/production-profile.json":
                "production-profile.schema.json",
            "context/rights.json": "rights.schema.json",
            "generator/environment.json": "environment.schema.json",
        }
        if path in direct:
            return direct[path]
        if path.startswith("states/"):
            return "state.schema.json"
        if path.startswith("proposals/"):
            return "proposal.schema.json"
        if path.startswith("comparisons/"):
            return "comparison.schema.json"
        if path.startswith("reviews/"):
            return "review.schema.json"
        if path.startswith("rewards/"):
            return "reward.schema.json"
        if path.startswith("transitions/"):
            return "transition.schema.json"
        if path.startswith("candidates/effective/"):
            return "effective-candidate.schema.json"
        if path.startswith("candidates/"):
            return {
                "candidate.json": "candidate.schema.json",
                "action.json": "action.schema.json",
                "recipe.json": "recipe.schema.json",
                "layers.json": "layers.schema.json",
                "metrics.json": "metrics.schema.json",
                "drc.json": "drc.schema.json",
                "artifact-refs.json": "artifact-refs.schema.json",
            }.get(path.rsplit("/", 1)[-1])
        return None

    for path, content in files.files.items():
        if not path.endswith(".json"):
            continue
        try:
            value = json.loads(content)
        except ValueError:
            findings.append(f"invalid_json:{path}")
            continue
        schema_file = schema_name(path)
        if schema_file is None:
            if not path.startswith("schemas/"):
                findings.append(f"missing_schema_mapping:{path}")
            continue
        schema = SCHEMAS[schema_file]
        for field in schema.get("required", []):
            if field not in value:
                findings.append(f"schema_required:{path}:{field}")
        for field, rule in schema.get("properties", {}).items():
            if "const" in rule and value.get(field) != rule["const"]:
                findings.append(f"schema_const:{path}:{field}")
    candidate_statuses = [
        json.loads(files.files[path])
        for path in manifest["indexes"]["candidates"]
    ]
    state_records = [
        json.loads(files.files[path])
        for path in manifest["indexes"]["states"]
    ]
    state_ids = {record["state_id"] for record in state_records}
    candidate_ids = {
        record["candidate_id"] for record in candidate_statuses
    }
    effective_ids = {
        json.loads(files.files[path])["effective_candidate_id"]
        for path in manifest["indexes"]["effective_candidates"]
    }
    proposal_records = [
        json.loads(files.files[path])
        for path in manifest["indexes"]["proposals"]
    ]
    proposal_ids = {
        record["proposal_id"] for record in proposal_records
    }
    reward_ids = {
        json.loads(files.files[path])["reward_id"]
        for path in manifest["indexes"]["rewards"]
    }
    comparison_ids = {
        json.loads(files.files[path])["comparison_id"]
        for path in manifest["indexes"]["comparisons"]
    }
    review_ids = {
        json.loads(files.files[path])["review_id"]
        for path in manifest["indexes"]["reviews"]
    }
    source = json.loads(files.files["source/source.json"])
    for artifact in (
        source["original"]["artifact_ref"],
        source["normalized"]["artifact_ref"],
    ):
        if artifact["path"] not in files.files:
            findings.append(f"unresolved_source_artifact:{artifact['path']}")
    for state in state_records:
        for reference_field in (
            "source_ref", "production_context_ref",
            "generator_environment_ref",
        ):
            if state.get(reference_field) not in files.files:
                findings.append(
                    f"unresolved_state_reference:"
                    f"{state['state_id']}:{reference_field}"
                )
        normalized = state.get("normalized_source_artifact", {})
        if normalized.get("path") not in files.files:
            findings.append(
                f"unresolved_state_artifact:{state['state_id']}"
            )
    for proposal in proposal_records:
        if proposal["parent_state_id"] not in state_ids:
            findings.append(
                f"unresolved_proposal_state:{proposal['proposal_id']}"
            )
        if not set(proposal["candidate_ids"]).issubset(candidate_ids):
            findings.append(
                f"unresolved_proposal_candidate:{proposal['proposal_id']}"
            )
        if not set(proposal["effective_candidate_ids"]).issubset(
            effective_ids
        ):
            findings.append(
                f"unresolved_effective_candidate:{proposal['proposal_id']}"
            )
    indexed_ids = []
    for path in (
        manifest["indexes"]["states"]
        + manifest["indexes"]["proposals"]
        + manifest["indexes"]["candidates"]
        + manifest["indexes"]["effective_candidates"]
        + manifest["indexes"]["comparisons"]
        + manifest["indexes"]["reviews"]
        + manifest["indexes"]["rewards"]
        + manifest["indexes"]["transitions"]
    ):
        record = json.loads(files.files[path])
        record_id = record.get({
            "states": "state_id",
            "proposals": "proposal_id",
            "candidates/effective": "effective_candidate_id",
            "candidates": "candidate_id",
            "comparisons": "comparison_id",
            "reviews": "review_id",
            "rewards": "reward_id",
            "transitions": "transition_id",
        }[
            "candidates/effective"
            if path.startswith("candidates/effective/")
            else path.split("/", 1)[0]
        ])
        if record_id:
            indexed_ids.append(record_id)
    duplicate_ids = sorted({
        record_id for record_id in indexed_ids
        if indexed_ids.count(record_id) > 1
    })
    findings.extend(f"duplicate_record_id:{item}" for item in duplicate_ids)
    for candidate in candidate_statuses:
        status = candidate["status"]
        if (
            status["hard_feasibility_status"] == "blocked"
            and status["status"] in {"eligible", "selected"}
        ):
            findings.append(
                f"contradictory_status:{candidate['candidate_id']}"
            )
        if candidate["parent_state_id"] not in state_ids:
            findings.append(
                f"unresolved_parent_state:{candidate['candidate_id']}"
            )
        action = json.loads(files.files[candidate["action_path"]])
        if action["validation"]["undeclared_parameter_differences"]:
            findings.append(
                f"undeclared_action_delta:{candidate['candidate_id']}"
            )
        if not action["validation"]["action_matches_resolved_difference"]:
            findings.append(
                f"action_resolution_mismatch:{candidate['candidate_id']}"
            )
        artifact_record = json.loads(
            files.files[candidate["artifact_refs_path"]]
        )
        for artifact in artifact_record["artifacts"].values():
            if artifact and artifact["path"] not in files.files:
                findings.append(
                    f"unresolved_artifact:{candidate['candidate_id']}"
                )
            elif artifact:
                content = files.files[artifact["path"]]
                if digest_bytes(content) != artifact["sha256"]:
                    findings.append(
                        f"artifact_checksum:{candidate['candidate_id']}"
                    )
                if len(content) != artifact["byte_length"]:
                    findings.append(
                        f"artifact_length:{candidate['candidate_id']}"
                    )
    for path in manifest["indexes"]["reviews"]:
        review = json.loads(files.files[path])
        if review["comparison_id"] not in comparison_ids:
            findings.append(
                f"unresolved_review_comparison:{review['review_id']}"
            )
        referenced_candidates = set(
            review["reviewed_candidate_ids"]
            + review["ranking"]
            + review.get("tie_candidate_ids", [])
            + review.get("negative_candidate_ids", [])
        )
        if review.get("winner_candidate_id"):
            referenced_candidates.add(review["winner_candidate_id"])
        if not referenced_candidates.issubset(candidate_ids):
            findings.append(
                f"unresolved_review_candidate:{review['review_id']}"
            )
        if review["completion_state"] == "complete":
            comparison = json.loads(
                files.files[
                    f"comparisons/{review['comparison_id']}.json"
                ]
            )
            expected = set(comparison["candidate_ids"])
            if set(review["reviewed_candidate_ids"]) != expected:
                findings.append(f"incomplete_review:{review['review_id']}")
            if set(review["ranking"]) != expected:
                findings.append(f"incomplete_ranking:{review['review_id']}")
    for path in manifest["indexes"]["comparisons"]:
        comparison = json.loads(files.files[path])
        if comparison["proposal_id"] not in proposal_ids:
            findings.append(
                f"unresolved_comparison_proposal:"
                f"{comparison['comparison_id']}"
            )
        if not set(comparison["candidate_ids"]).issubset(candidate_ids):
            findings.append(
                f"unresolved_comparison_candidate:"
                f"{comparison['comparison_id']}"
            )
        if not set(comparison["effective_candidate_ids"]).issubset(
            effective_ids
        ):
            findings.append(
                f"unresolved_comparison_effective_candidate:"
                f"{comparison['comparison_id']}"
            )
    for path in manifest["indexes"]["effective_candidates"]:
        effective = json.loads(files.files[path])
        if not set(effective["candidate_ids"]).issubset(candidate_ids):
            findings.append(
                f"unresolved_effective_members:"
                f"{effective['effective_candidate_id']}"
            )
    for path in manifest["indexes"]["rewards"]:
        reward = json.loads(files.files[path])
        if reward["candidate_id"] not in candidate_ids:
            findings.append(
                f"unresolved_reward_candidate:{reward['reward_id']}"
            )
        if reward["review_id"] not in review_ids:
            findings.append(
                f"unresolved_reward_review:{reward['reward_id']}"
            )
    for path in manifest["indexes"]["transitions"]:
        transition = json.loads(files.files[path])
        if (
            transition["state_before"] not in state_ids
            or transition["state_after"] not in state_ids
        ):
            findings.append(
                f"unresolved_transition_state:"
                f"{transition['transition_id']}"
            )
        if transition["action_candidate_id"] not in candidate_ids:
            findings.append(
                f"unresolved_transition_candidate:"
                f"{transition['transition_id']}"
            )
        if transition["reward_id"] not in reward_ids:
            findings.append(
                f"unresolved_transition_reward:"
                f"{transition['transition_id']}"
            )
    return findings


def compile_training_bundle(
    session_root: Path,
    *,
    as_dst,
    inspect_dst,
    project_root: Path,
) -> tuple[bytes, str, dict]:
    """Return a validated, content-addressed v2 training bundle ZIP."""
    session = _read_json(session_root / "session.json")
    session_id = session["session_id"]
    exported_at = datetime.now(timezone.utc).isoformat()
    files = BundleFiles()
    environment = _environment(project_root, exported_at)
    files.add_json("generator/environment.json", environment)

    source_meta = session.get("source", {})
    source_path = session_root / str(source_meta.get("artifact", ""))
    if not source_path.is_file():
        raise ValueError("Training source artifact is missing")
    source_bytes = source_path.read_bytes()
    source_media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    source_ref = files.add_object(
        source_bytes,
        media_type=source_media_types.get(
            source_path.suffix.lower(), "application/octet-stream"
        ),
        logical_name=source_meta.get("name", source_path.name),
    )
    normalized_ref = source_ref
    decoder = {
        "name": None, "version": None,
        "unknown_reason": "decoder_metadata_unavailable",
    }
    source_dimensions = None
    normalized_dimensions = None
    alpha_handling = "unknown"
    color_profile = "unknown"
    try:
        from PIL import Image, __version__ as pillow_version
        with Image.open(io.BytesIO(source_bytes)) as source_image:
            source_dimensions = {
                "width": source_image.width,
                "height": source_image.height,
            }
            color_profile = (
                "embedded"
                if source_image.info.get("icc_profile")
                else "assumed_srgb"
            )
            alpha_handling = (
                "preserved" if "A" in source_image.getbands()
                else "opaque_alpha_added"
            )
            normalized_image = source_image.convert("RGBA")
            normalized_stream = io.BytesIO()
            normalized_image.save(normalized_stream, format="PNG")
            normalized_ref = files.add_object(
                normalized_stream.getvalue(),
                media_type="image/png",
                logical_name="normalized-source.png",
            )
            normalized_dimensions = {
                "width": normalized_image.width,
                "height": normalized_image.height,
            }
            decoder = {"name": "Pillow", "version": pillow_version}
    except (OSError, ValueError):
        normalized_dimensions = source_dimensions
    source_id = opaque_id("source", {
        "sha256": digest_bytes(source_bytes),
        "name": source_meta.get("name"),
    })
    artwork_family_id = f"family_{digest_bytes(source_bytes)[:24]}"
    source_record = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_id": source_id,
        "artwork_family_id": artwork_family_id,
        "original": {
            "filename": source_meta.get("name"),
            "media_type": source_ref["media_type"],
            "byte_length": len(source_bytes),
            "sha256": digest_bytes(source_bytes),
            "artifact_ref": source_ref,
            "dimensions_px": source_dimensions,
            "color_profile": color_profile,
        },
        "normalized": {
            "artifact_ref": normalized_ref,
            "decoder": decoder,
            "dimensions_px": normalized_dimensions,
            "alpha_handling": alpha_handling,
            "background_detection": None,
            "crop_bounds_px": None,
            "resampling_method": "none",
            "transforms": [{
                "operation": (
                    "decode_to_rgba"
                    if decoder.get("name") else "source_preserved"
                ),
                "decoder": decoder,
                "alpha_handling": alpha_handling,
            }],
            "requested_physical_size_mm": {
                "width": session.get("settings", {}).get("width_mm"),
                "height": None,
            },
            "realized_physical_size_mm": None,
            "pixels_per_mm": None,
        },
        "ingested_at_utc": session.get("created_at"),
    }
    production_context = _deep_merge({
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "production_profile_id": "production_profile_unknown",
        "product_type": None,
        "product_type_unknown_reason": "not_collected",
        "machine": {
            "make": None, "model": None, "head_type": None,
            "limits": None, "unknown_reason": "not_collected",
        },
        "frame": {
            "type": None, "usable_field_mm": None,
            "unknown_reason": "not_collected",
        },
        "orientation_constraints": None,
        "orientation_constraints_unknown_reason": "not_collected",
        "sewing_direction_constraints": None,
        "sewing_direction_constraints_unknown_reason": "not_collected",
        "fabric": {
            "class": None, "weight": None, "stretch": None,
            "pile": None, "color": None,
            "unknown_reason": "not_collected",
        },
        "stabilizer": {
            "type": None, "layers": None,
            "unknown_reason": "not_collected",
        },
        "topping": {
            "type": None, "unknown_reason": "not_collected",
        },
        "thread": {
            "brand": None, "line": None, "material": None,
            "weight": None, "unknown_reason": "not_collected",
        },
        "needle": {
            "system": None, "size": None,
            "unknown_reason": "not_collected",
        },
        "target_speed_spm": None,
        "target_speed_unknown_reason": "not_collected",
        "placement": None,
        "placement_unknown_reason": "not_collected",
        "design_dimensions_mm": {
            "requested_width": session.get("settings", {}).get("width_mm"),
            "requested_height": None,
        },
        "profile_versions": {},
    }, session.get("production_context", {}))
    rights = _deep_merge({
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_id": source_id,
        "acquisition_method": None,
        "license_or_rights_basis": None,
        "permitted_training_use": None,
        "status": "unknown",
        "learnable": False,
        "unknown_reason": "rights_not_collected_at_intake",
    }, session.get("rights", {}))
    rights["source_id"] = source_id
    files.add_json("context/production-profile.json", production_context)
    files.add_json("context/rights.json", rights)

    baseline_parameters = {
        **DEFAULT_PARAMETERS,
        "requested_width_mm": session.get("settings", {}).get("width_mm"),
        "requested_colors": session.get("settings", {}).get("colors"),
        "profile": session.get("settings", {}).get("profile", "standard"),
        **session.get("baseline_resolved_parameters", {}),
    }
    baseline_state_id = _portable_id(
        session.get("baseline_state_id"),
        opaque_id("state", {
            "source": source_id,
            "parameters": baseline_parameters,
            "production_context": production_context[
                "production_profile_id"
            ],
            "generator_commit": environment["git"]["commit"],
        }),
    )
    state_paths = [f"states/{baseline_state_id}.json"]
    files.add_json(state_paths[0], {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "state_id": baseline_state_id,
        "source_ref": "source/source.json",
        "normalized_source_artifact": normalized_ref,
        "resolved_parameters": baseline_parameters,
        "parameter_provenance": [
            {
                "parameter": key,
                "value": value,
                "source": (
                    "explicit_user_input"
                    if key in {
                        "requested_width_mm", "requested_colors",
                        "profile", "density_multiplier",
                    }
                    else "generator_default"
                ),
                "source_ref": (
                    "training_session.settings"
                    if key in {
                        "requested_width_mm", "requested_colors",
                        "profile", "density_multiplier",
                    }
                    else GENERATOR_VERSION
                ),
            }
            for key, value in sorted(baseline_parameters.items())
        ],
        "production_context_ref": "context/production-profile.json",
        "generator_environment_ref": "generator/environment.json",
        "inherited_transition_ids": [],
        "created_at_utc": session.get("created_at"),
    })

    experiment_paths = sorted(
        session_root.glob("experiments/*/experiment.json"),
        key=lambda path: _read_json(path).get("saved_at", ""),
    )
    proposal_paths = []
    candidate_paths = []
    comparison_paths = []
    review_paths = []
    reward_paths = []
    transition_paths = []
    effective_by_output: dict[str, str] = {}
    effective_candidates_by_output: dict[str, list[str]] = defaultdict(list)
    effective_labels_by_output: dict[str, list[str]] = defaultdict(list)
    current_state_id = baseline_state_id
    current_parameters = dict(baseline_parameters)
    candidate_records: dict[str, dict] = {}
    candidate_actions: dict[str, dict] = {}
    candidate_resolved: dict[str, dict] = {}
    realized_observations = []

    for experiment_path in experiment_paths:
        experiment = _read_json(experiment_path)
        parameter = experiment["parameter"]
        controlled_fields = CONTROLLED_FIELDS.get(parameter, [parameter])
        phase = experiment["phase"]
        parent_state_id = experiment.get("parent_state_id") or current_state_id
        parent_parameters = dict(
            experiment.get("parent_resolved_parameters")
            or current_parameters
        )
        proposal_id = opaque_id("proposal", {
            "session_id": session_id,
            "parameter": parameter,
            "phase": phase,
            "parent_state_id": parent_state_id,
            "saved_at": experiment.get("saved_at"),
        })
        proposal_path = f"proposals/{proposal_id}.json"
        proposal_paths.append(proposal_path)
        proposal_candidates = []
        effective_candidates = []
        candidate_refs: dict[str, dict] = {}

        for summary in experiment.get("candidates", []):
            display_name = summary["candidate_id"]
            candidate_root = session_root / summary["directory"]
            params = _read_json(candidate_root / "parameters.json")
            plan = _read_json(candidate_root / "plan.json")
            dimensions = plan.get("dimensions_mm", {})
            realized_width = dimensions.get(
                "width", plan.get("width_mm")
            )
            realized_height = dimensions.get(
                "height", plan.get("height_mm")
            )
            source_size = plan.get("posterization", {}).get(
                "source_size_px", {}
            )
            if realized_width and realized_height:
                realized_observations.append({
                    "width_mm": realized_width,
                    "height_mm": realized_height,
                    "source_width_px": source_size.get("width"),
                    "source_height_px": source_size.get("height"),
                    "posterization": plan.get("posterization", {}),
                })
            requested_action = (
                params.get("action_delta")
                or _resolved_action(
                    parameter,
                    params.get("value"),
                    params.get("parameters", {}),
                )
            )
            resolved_parameters = {
                **parent_parameters,
                **(
                    params.get("resolved_parameters")
                    or requested_action
                ),
            }
            action_delta = {
                key: value
                for key, value in resolved_parameters.items()
                if key not in parent_parameters
                or parent_parameters.get(key) != value
            }
            plan_canonical = canonical_json(
                _without_volatile_plan_fields(plan)
            )
            plan_hash = digest_bytes(plan_canonical)
            action_hash = digest_bytes(canonical_json({
                "parent_state_id": parent_state_id,
                "resolved_parameters": resolved_parameters,
                "generator_commit": environment["git"]["commit"],
                "generator": GENERATOR_VERSION,
            }))
            dst_path = candidate_root / "candidate.dst"
            dst = dst_path.read_bytes() if dst_path.exists() else b""
            decoded = inspect_dst(dst) if dst else []
            output_hash = digest_bytes(canonical_json(decoded))
            effective_id = effective_by_output.setdefault(
                output_hash,
                opaque_id("effective", {"output_hash": output_hash}),
            )
            candidate_id = opaque_id("candidate", {
                "proposal_id": proposal_id,
                "action_hash": action_hash,
                "plan_hash": plan_hash,
                "output_hash": output_hash,
            })
            round_trip = (
                _round_trip(dst, decoded, as_dst, inspect_dst)
                if dst else {"passed": False, "error": "missing_dst"}
            )
            status = _status(plan, decoded, round_trip["passed"])
            status["legacy_candidate_status"] = (
                params.get("metrics", {}).get("status")
            )

            plan_ref = files.add_object(
                plan_canonical,
                media_type="application/json",
                logical_name=f"{display_name}-plan.json",
                content_encoding="gzip",
            )
            artifact_refs = {
                "plan": plan_ref,
                "dst": (
                    files.add_object(
                        dst,
                        media_type="application/x-dst",
                        logical_name=f"{display_name}.dst",
                    ) if dst else None
                ),
            }
            for filename, key, media_type in (
                ("posterized.png", "posterized", "image/png"),
                ("sewout.png", "virtual_sewout", "image/png"),
            ):
                path = candidate_root / filename
                artifact_refs[key] = (
                    files.add_object(
                        path.read_bytes(),
                        media_type=media_type,
                        logical_name=f"{display_name}-{filename}",
                    ) if path.exists() else None
                )

            operations = plan.get("operations", [])
            semantic_stitches = sum(
                item.get("kind") == "stitch" for item in operations
            )
            semantic_jumps = sum(
                item.get("kind") == "jump" for item in operations
            )
            machine_jumps = sum(
                item.get("kind") == "jump" for item in decoded
            )
            measurements = [
                _metric("semantic_stitches", semantic_stitches),
                _metric("semantic_jumps", semantic_jumps),
                _metric("machine_jumps", machine_jumps),
                _metric("trims", sum(
                    item.get("kind") == "trim" for item in operations
                )),
                _metric("color_changes", sum(
                    item.get("kind") == "color" for item in decoded
                )),
                _metric("object_count", len(plan.get("regions", []))),
            ]
            for metric_name in (
                "minimum_stitch_mm",
                "maximum_stitch_mm",
                "short_stitches",
                "long_stitches",
                "jump_distance_mm",
                "stitches_per_sq_in",
                "short_fill_edge_rows",
                "local_density_variance",
                "quality_score",
            ):
                if plan.get("drc", {}).get(metric_name) is not None:
                    measurements.append(_metric(
                        metric_name, plan["drc"][metric_name]
                    ))
            semantic_colors = sum(
                item.get("kind") == "color" for item in operations
            )
            machine_colors = sum(
                item.get("kind") == "color" for item in decoded
            )
            if semantic_colors != machine_colors:
                status["hard_blockers"] = sorted(set(
                    status["hard_blockers"]
                    + ["unresolved_color_sequence"]
                ))
                status["status"] = "blocked"
                status["hard_feasibility_status"] = "blocked"
                status["review_eligibility_status"] = "ineligible"
            measurements.extend([
                _metric(
                    "color_change_parity_error",
                    abs(semantic_colors - machine_colors),
                ),
                _metric(
                    "round_trip_coordinate_error_mm",
                    0.0 if round_trip["passed"] else None,
                ),
            ])
            geometry_quality = (
                plan.get("posterization", {}).get("geometry_quality", {})
            )
            if geometry_quality.get("reference_iou") is not None:
                measurements.append(_metric(
                    "silhouette_iou",
                    geometry_quality["reference_iou"],
                    geometry_quality["reference_iou"],
                ))
            color_error = (
                plan.get("posterization", {})
                .get("color_separation", {})
                .get("mean_color_error_rgb")
            )
            if color_error is not None:
                measurements.append(_metric(
                    "mean_color_error_rgb", color_error
                ))
            metrics_record = {
                "schema_version": BUNDLE_SCHEMA_VERSION,
                "metric_version": METRIC_VERSION,
                "candidate_id": candidate_id,
                "measurements": measurements,
                "legacy_metrics": params.get("metrics", {}),
            }
            decoded_points = [
                item for item in decoded
                if isinstance(item.get("x"), (int, float))
                and isinstance(item.get("y"), (int, float))
            ]
            drc_record = {
                "schema_version": BUNDLE_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "authoritative_status": status,
                "policy": plan.get("drc_policy", {}),
                "raw": plan.get("drc", {}),
                "round_trip": round_trip,
                "decoded_bounds_mm": (
                    {
                        "min_x": min(item["x"] for item in decoded_points),
                        "min_y": min(item["y"] for item in decoded_points),
                        "max_x": max(item["x"] for item in decoded_points),
                        "max_y": max(item["y"] for item in decoded_points),
                    }
                    if decoded_points else None
                ),
                "color_change_parity": {
                    "semantic": semantic_colors,
                    "decoded": machine_colors,
                    "matches": semantic_colors == machine_colors,
                },
            }
            recipe, layers = _recipe(
                plan, resolved_parameters, decoded
            )
            recipe["candidate_id"] = candidate_id
            recipe["parameter_provenance"] = _parameter_provenance(
                parent_parameters,
                resolved_parameters,
                action_delta,
                proposal_id,
                plan_hash,
                output_hash,
            )
            layers["candidate_id"] = candidate_id
            action_record = {
                "schema_version": BUNDLE_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "proposal_id": proposal_id,
                "parent_state_id": parent_state_id,
                "controlled_parameter_set": [parameter],
                "requested_action": requested_action,
                "action_delta": action_delta,
                "resolved_parameters": resolved_parameters,
                "action_hash": action_hash,
                "parameter_provenance": recipe["parameter_provenance"],
                "validation": {
                    "resolved_parameter_differences": sorted(action_delta),
                    "undeclared_parameter_differences": sorted(
                        set(action_delta) - set(controlled_fields)
                    ),
                    "action_matches_resolved_difference": (
                        action_delta == {
                            key: value
                            for key, value in resolved_parameters.items()
                            if key not in parent_parameters
                            or parent_parameters.get(key) != value
                        }
                    ),
                },
            }
            candidate_record = {
                "schema_version": BUNDLE_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "display_name": display_name,
                "effective_candidate_id": effective_id,
                "proposal_id": proposal_id,
                "parent_state_id": parent_state_id,
                "action_hash": action_hash,
                "plan_hash": plan_hash,
                "output_hash": output_hash,
                "status": status,
                "generation": {
                    **params.get("generation", {}),
                    "warnings": [
                        item for item in plan.get("brackets", [])
                        if isinstance(item, dict)
                        if item.get("status") in {
                            "warning", "fallback", "near_limit",
                        }
                    ],
                    "errors": status["hard_blockers"],
                },
                "physical_scale": {
                    "requested_width_mm": session.get(
                        "settings", {}
                    ).get("width_mm"),
                    "realized_width_mm": realized_width,
                    "realized_height_mm": realized_height,
                    "transform": "fit_within_width_and_55mm_height",
                },
                "learning_weight": 1.0,
                "artifact_refs_path": (
                    f"candidates/{candidate_id}/artifact-refs.json"
                ),
                "action_path": f"candidates/{candidate_id}/action.json",
                "recipe_path": f"candidates/{candidate_id}/recipe.json",
                "layers_path": f"candidates/{candidate_id}/layers.json",
                "metrics_path": f"candidates/{candidate_id}/metrics.json",
                "drc_path": f"candidates/{candidate_id}/drc.json",
            }
            candidate_dir = f"candidates/{candidate_id}"
            for filename, value in (
                ("candidate.json", candidate_record),
                ("action.json", action_record),
                ("recipe.json", recipe),
                ("layers.json", layers),
                ("metrics.json", metrics_record),
                ("drc.json", drc_record),
                ("artifact-refs.json", {
                    "schema_version": BUNDLE_SCHEMA_VERSION,
                    "candidate_id": candidate_id,
                    "artifacts": artifact_refs,
                }),
            ):
                files.add_json(f"{candidate_dir}/{filename}", value)
            candidate_path = f"{candidate_dir}/candidate.json"
            candidate_paths.append(candidate_path)
            proposal_candidates.append(candidate_id)
            if effective_id not in effective_candidates:
                effective_candidates.append(effective_id)
            candidate_records[candidate_id] = candidate_record
            candidate_actions[candidate_id] = action_delta
            candidate_resolved[candidate_id] = resolved_parameters
            effective_candidates_by_output[effective_id].append(candidate_id)
            effective_labels_by_output[effective_id].append(display_name)
            candidate_refs[display_name] = {
                "candidate_id": candidate_id,
                "effective_candidate_id": effective_id,
            }

        reference_candidate_id = next(
            (
                candidate_id for candidate_id in proposal_candidates
                if not candidate_actions[candidate_id]
            ),
            proposal_candidates[0] if proposal_candidates else None,
        )
        if reference_candidate_id is not None:
            reference = candidate_records[reference_candidate_id]
            for candidate_id in proposal_candidates:
                candidate = candidate_records[candidate_id]
                semantic_changed = (
                    candidate["plan_hash"] != reference["plan_hash"]
                )
                encoded_changed = (
                    candidate["output_hash"] != reference["output_hash"]
                )
                candidate_dir = f"candidates/{candidate_id}"
                for filename in ("action.json", "recipe.json"):
                    path = f"{candidate_dir}/{filename}"
                    record = json.loads(files.files[path])
                    for provenance in record["parameter_provenance"]:
                        if provenance["source"] == "candidate_action":
                            provenance["actuation"] = {
                                "semantic_stream_changed": semantic_changed,
                                "encoded_stream_changed": encoded_changed,
                            }
                    files.replace_json(path, record)

        files.add_json(proposal_path, {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "proposal_policy": "one_factor_sweep",
            "proposal_policy_version": "new-hat-bracket/1",
            "parent_state_id": parent_state_id,
            "phase": phase,
            "controlled_parameter_set": [parameter],
            "controlled_resolved_fields": controlled_fields,
            "candidate_ids": proposal_candidates,
            "effective_candidate_ids": effective_candidates,
            "random_seed": experiment.get("random_seed", 0),
            "exploration_probability": None,
            "constraints": {
                "reference": experiment.get("reference"),
            },
            "actuation": {
                "proposal_count": len(proposal_candidates),
                "effective_count": len(effective_candidates),
                "equivalent_proposals_collapsed": (
                    len(proposal_candidates) - len(effective_candidates)
                ),
            },
        })

        comparison_id = opaque_id("comparison", {
            "proposal_id": proposal_id,
            "effective_candidates": effective_candidates,
        })
        comparison_path = f"comparisons/{comparison_id}.json"
        comparison_paths.append(comparison_path)
        review = experiment.get("review") or {}
        positive_display = review.get("positive_candidate_id")
        selected = candidate_refs.get(str(positive_display), {})
        selected_candidate_id = selected.get("candidate_id")
        reviewed_display = review.get("reviewed_candidate_ids", [])
        reviewed_ids = [
            candidate_refs[name]["candidate_id"]
            for name in reviewed_display if name in candidate_refs
        ]
        if not reviewed_ids and positive_display in candidate_refs:
            reviewed_ids = [selected_candidate_id]
        disposition = review.get("disposition") or (
            "winner" if selected_candidate_id else "incomplete"
        )
        raw_scores = review.get("candidate_scores", {})
        raw_ranking = review.get("ranking", [])
        raw_ties = set(review.get("tie_candidate_ids", []))
        disposition_valid = (
            (
                disposition == "winner"
                and selected_candidate_id is not None
                and not raw_ties
            )
            or (
                disposition == "tie"
                and selected_candidate_id is None
                and len(raw_ties) >= 2
                and raw_ties.issubset(candidate_refs)
            )
            or (
                disposition == "no_acceptable_candidate"
                and selected_candidate_id is None
                and not raw_ties
            )
        )
        complete = bool(
            review.get("completion_state") == "complete"
            and len(set(reviewed_ids)) == len(set(proposal_candidates))
            and disposition_valid
            and set(raw_ranking) == set(candidate_refs)
            and len(raw_ranking) == len(candidate_refs)
            and all(
                set(raw_scores.get(name, {})) == REVIEW_SCORE_AXES
                and all(
                    isinstance(value, int) and 1 <= value <= 5
                    for value in raw_scores.get(name, {}).values()
                )
                for name in candidate_refs
            )
        )
        review_id = opaque_id("review", {
            "comparison_id": comparison_id,
            "training_record_id": review.get("training_record_id"),
            "selected": selected_candidate_id,
            "saved_at": experiment.get("saved_at"),
        })
        review_path = f"reviews/{review_id}.json"
        review_paths.append(review_path)
        display_order = [
            candidate_refs[name]["candidate_id"]
            for name in review.get("blind_display_order", [])
            if name in candidate_refs
        ] or proposal_candidates
        ranking = [
            candidate_refs[name]["candidate_id"]
            for name in review.get("ranking", [])
            if name in candidate_refs
        ]
        candidate_scores = {
            candidate_refs[name]["candidate_id"]: scores
            for name, scores in review.get("candidate_scores", {}).items()
            if name in candidate_refs
        }
        candidate_defects = {
            candidate_refs[name]["candidate_id"]: defects
            for name, defects in review.get("candidate_defects", {}).items()
            if name in candidate_refs
        }
        files.add_json(comparison_path, {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "comparison_id": comparison_id,
            "proposal_id": proposal_id,
            "candidate_ids": proposal_candidates,
            "effective_candidate_ids": effective_candidates,
            "display_order": display_order,
            "blind_order_seed": review.get("blind_order_seed", 0),
            "review_id": review_id,
        })
        files.add_json(review_path, {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "review_id": review_id,
            "comparison_id": comparison_id,
            "reviewer": {
                "pseudonymous_id": review.get("reviewer_id"),
                "qualification_profile": review.get(
                    "reviewer_qualification"
                ),
            },
            "review_ui_version": review.get(
                "review_ui_version", "new-hat-training-ui/1"
            ),
            "rubric_version": review.get(
                "rubric_version", "embroidery-review/2.0.0"
            ),
            "blind_display_order": display_order,
            "reviewed_candidate_ids": reviewed_ids,
            "completion_state": "complete" if complete else "partial",
            "disposition": disposition,
            "winner_candidate_id": selected_candidate_id,
            "tie_candidate_ids": [
                candidate_refs[name]["candidate_id"]
                for name in review.get("tie_candidate_ids", [])
                if name in candidate_refs
            ],
            "ranking": ranking,
            "confidence": review.get("confidence"),
            "candidate_scores": candidate_scores,
            "candidate_defects": candidate_defects,
            "negative_candidate_ids": [
                candidate_refs[name]["candidate_id"]
                for name in review.get("negative_candidate_ids", [])
                if name in candidate_refs
            ],
            "reasons": review.get("reasons", []),
            "color_accuracy": review.get("color_accuracy"),
            "review_duration_ms": review.get("review_duration_ms"),
            "note": review.get("note", ""),
            "timestamp_utc": experiment.get("saved_at"),
            "label_scope": "virtual",
        })

        if selected_candidate_id:
            candidate = candidate_records[selected_candidate_id]
            feasible = (
                candidate["status"]["hard_feasibility_status"] != "blocked"
            )
            if complete and feasible:
                candidate["status"]["status"] = "selected"
                files.replace_json(
                    f"candidates/{selected_candidate_id}/candidate.json",
                    candidate,
                )
            reward_id = opaque_id("reward", {
                "candidate_id": selected_candidate_id,
                "review_id": review_id,
            })
            reward_path = f"rewards/{reward_id}.json"
            reward_paths.append(reward_path)
            files.add_json(reward_path, {
                "schema_version": BUNDLE_SCHEMA_VERSION,
                "reward_id": reward_id,
                "candidate_id": selected_candidate_id,
                "review_id": review_id,
                "feasible": feasible,
                "reward_model_version": REWARD_VERSION,
                "components": {
                    "virtual_fidelity": None,
                    "expert_preference": 1.0 if complete else None,
                    "physical_quality": None,
                    "efficiency": None,
                    "machine_risk": None,
                },
                "raw_observations": {
                    "reasons": review.get("reasons", []),
                    "candidate_scores": review.get("candidate_scores", {}),
                },
                "constraints": {
                    "hard_blockers": len(
                        candidate["status"]["hard_blockers"]
                    ),
                    "review_findings": len(
                        candidate["status"]["advisory_findings"]
                    ),
                },
                "aggregate": None,
                "aggregate_status": "awaiting_physical_validation",
            })

            transition_data = review.get("transition") or {}
            transition_status = transition_data.get("status")
            if complete and transition_status == "accepted" and feasible:
                state_after = _portable_id(
                    transition_data.get("state_after"),
                    opaque_id("state", {
                        "parent": parent_state_id,
                        "candidate": selected_candidate_id,
                        "parameters": candidate_resolved[
                            selected_candidate_id
                        ],
                    }),
                )
                transition_id = _portable_id(
                    transition_data.get("transition_id"),
                    opaque_id("transition", {
                        "before": parent_state_id,
                        "candidate": selected_candidate_id,
                        "after": state_after,
                    }),
                )
                transition_path = f"transitions/{transition_id}.json"
                transition_paths.append(transition_path)
                files.add_json(transition_path, {
                    "schema_version": BUNDLE_SCHEMA_VERSION,
                    "transition_id": transition_id,
                    "status": "accepted",
                    "state_before": parent_state_id,
                    "action_candidate_id": selected_candidate_id,
                    "reward_id": reward_id,
                    "state_after": state_after,
                    "accepted_at_utc": experiment.get("saved_at"),
                })
                state_path = f"states/{state_after}.json"
                state_paths.append(state_path)
                files.add_json(state_path, {
                    "schema_version": BUNDLE_SCHEMA_VERSION,
                    "state_id": state_after,
                    "source_ref": "source/source.json",
                    "normalized_source_artifact": normalized_ref,
                    "resolved_parameters": candidate_resolved[
                        selected_candidate_id
                    ],
                    "production_context_ref":
                        "context/production-profile.json",
                    "generator_environment_ref":
                        "generator/environment.json",
                    "parent_state_id": parent_state_id,
                    "inherited_transition_ids": [transition_id],
                    "created_at_utc": experiment.get("saved_at"),
                })
                current_state_id = state_after
                current_parameters = dict(
                    candidate_resolved[selected_candidate_id]
                )

    effective_paths = []
    for effective_id, candidate_ids in sorted(
        effective_candidates_by_output.items()
    ):
        proposal_labels = effective_labels_by_output[effective_id]
        path = f"candidates/effective/{effective_id}.json"
        effective_paths.append(path)
        files.add_json(path, {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "effective_candidate_id": effective_id,
            "candidate_ids": candidate_ids,
            "proposal_display_names": proposal_labels,
            "learning_weight": 1.0,
        })
        for candidate_id in candidate_ids:
            candidate_records[candidate_id]["learning_weight"] = (
                1.0 / max(1, len(candidate_ids))
            )
            files.replace_json(
                f"candidates/{candidate_id}/candidate.json",
                candidate_records[candidate_id],
            )

    if realized_observations:
        realized = realized_observations[0]
        source_record["normalized"]["realized_physical_size_mm"] = {
            "width": realized["width_mm"],
            "height": realized["height_mm"],
        }
        source_record["normalized"]["pixels_per_mm"] = {
            "x": (
                realized["source_width_px"] / realized["width_mm"]
                if realized["source_width_px"] and realized["width_mm"]
                else None
            ),
            "y": (
                realized["source_height_px"] / realized["height_mm"]
                if realized["source_height_px"] and realized["height_mm"]
                else None
            ),
        }
        posterization = realized.get("posterization", {})
        source_record["normalized"]["background_detection"] = {
            key: posterization.get(key)
            for key in (
                "method", "threshold", "background_class",
                "background_reference_rgb", "background_tolerance_rgb",
                "border_connected_background_pixels",
                "background_like_negative_space_pixels",
            )
            if key in posterization
        }
        source_record["normalized"]["crop_bounds_px"] = (
            posterization.get("foreground_bounds_px")
        )
        source_record["normalized"]["transforms"].append({
            "operation": "foreground_extraction",
            "background_detection":
                source_record["normalized"]["background_detection"],
            "crop_bounds_px":
                source_record["normalized"]["crop_bounds_px"],
        })
        source_record["normalized"]["transforms"].append({
            "operation": "fit_physical_field",
            "requested_width_mm": session.get(
                "settings", {}
            ).get("width_mm"),
            "maximum_height_mm": 55.0,
            "realized_width_mm": realized["width_mm"],
            "realized_height_mm": realized["height_mm"],
        })
    files.add_json("source/source.json", source_record)

    training_ready_reasons = []
    rights_ready = bool(
        rights.get("learnable") is True
        and rights.get("status") == "approved"
        and rights.get("license_or_rights_basis")
        and rights.get("permitted_training_use")
    )
    if not rights_ready:
        training_ready_reasons.append("training_rights_unknown")
    if not transition_paths:
        training_ready_reasons.append("no_accepted_state_transition")
    if not review_paths:
        training_ready_reasons.append("no_reviews")
    review_records = [
        json.loads(files.files[path]) for path in review_paths
    ]
    if any(item["completion_state"] != "complete" for item in review_records):
        training_ready_reasons.append("incomplete_review")
    if not files.files.get("physical-runs/index.json"):
        training_ready_reasons.append("physical_validation_missing")
    training_ready_reasons.append("final_composed_candidate_not_evaluated")

    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": opaque_id("bundle", {
            "session_id": session_id,
            "source_sha256": digest_bytes(source_bytes),
            "generator_commit": environment["git"]["commit"],
        }),
        "session_id": session_id,
        "status": "complete",
        "record_type": (
            "episode" if transition_paths else "parameter_study"
        ),
        "created_at_utc": session.get("created_at"),
        "exported_at_utc": exported_at,
        "source_id": source_id,
        "artwork_family_split_key": artwork_family_id,
        "baseline_state_id": baseline_state_id,
        "final_state_id": current_state_id,
        "training_ready": not training_ready_reasons,
        "training_ready_findings": training_ready_reasons,
        "canonical_json": {
            "encoding": "UTF-8",
            "sorted_keys": True,
            "insignificant_whitespace": False,
            "finite_numbers_only": True,
            "explicit_null": True,
            "volatile_fields_excluded_from_identity": True,
        },
        "indexes": {
            "states": state_paths,
            "proposals": proposal_paths,
            "candidates": candidate_paths,
            "effective_candidates": effective_paths,
            "comparisons": comparison_paths,
            "reviews": review_paths,
            "rewards": reward_paths,
            "transitions": transition_paths,
            "physical_runs": [],
            "schemas": [
                f"schemas/{name}" for name in sorted(SCHEMAS)
            ],
        },
        "summary": {
            "proposals": len(proposal_paths),
            "candidates": len(candidate_paths),
            "effective_candidates": len(effective_paths),
            "duplicate_outputs": len(candidate_paths) - len(effective_paths),
            "reviews": len(review_paths),
            "complete_reviews": sum(
                item["completion_state"] == "complete"
                for item in review_records
            ),
            "transitions": len(transition_paths),
            "content_objects": len([
                path for path in files.files
                if path.startswith("objects/sha256/")
            ]),
        },
    }
    for name, schema in SCHEMAS.items():
        files.add_json(f"schemas/{name}", schema)
    files.add_json("manifest.json", manifest)
    findings = _validate_bundle(files, manifest)
    if findings:
        raise ValueError(
            "Training bundle validation failed: " + ", ".join(findings)
        )

    checksum_lines = [
        f"{digest_bytes(content)}  {path}"
        for path, content in sorted(files.files.items())
    ]
    files.add_bytes(
        "checksums.sha256",
        ("\n".join(checksum_lines) + "\n").encode(),
    )
    files.add_bytes(
        "COMPLETE",
        canonical_json({
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_id": manifest["bundle_id"],
            "completed_at_utc": exported_at,
            "validated": True,
            "checksummed_files": len(checksum_lines),
        }) + b"\n",
    )

    archive = io.BytesIO()
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as bundle:
        ordered_paths = [
            path for path in sorted(files.files)
            if path not in {"checksums.sha256", "COMPLETE"}
        ] + ["checksums.sha256", "COMPLETE"]
        for path in ordered_paths:
            content = files.files[path]
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, content)
    filename = f"training-session-{session_id}-v2.zip"
    return archive.getvalue(), filename, manifest

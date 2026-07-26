#!/usr/bin/env python3
"""Dependency-free local server for the New Hat vertical-slice prototype."""
from __future__ import annotations

import io
import json
import sys
import base64
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
ARTIFACTS_ROOT = ROOT.parent / "artifacts"
BRACKET_ARTIFACTS = ARTIFACTS_ROOT / "bracket-fixtures"
TRAINING_FEEDBACK_ROOT = ARTIFACTS_ROOT / "training-feedback"
sys.path.insert(0, str(ROOT.parent / "pystitch" / "src"))

TRAINING_PARAMETERS = {
    "color_separation",
    "stitch_family",
    "tatami_density",
    "running_passes",
    "density_multiplier",
    "satin_spacing_mm",
    "running_stitch_mm",
    "pull_compensation_mm",
    "join_overlap_mm",
}
TRAINING_PHASES = {"separation", "interpretation", "construction"}
TRAINING_REASONS = {
    "negative_space",
    "curve_flow",
    "edge_quality",
    "coverage",
    "color_accuracy",
    "thread_economy",
    "pathing",
}
TRAINING_COLOR_ACCURACY = {
    "accurate",
    "acceptable",
    "shifted",
    "incorrect",
}


def save_training_feedback(
    payload: dict, output: Path = TRAINING_FEEDBACK_ROOT
) -> dict:
    """Validate and persist one human-labelled bracket comparison."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported training-feedback schema")
    source = payload.get("source")
    comparison = payload.get("comparison")
    reinforcement = payload.get("reinforcement")
    if not all(isinstance(item, dict) for item in (
        source, comparison, reinforcement
    )):
        raise ValueError("Source, comparison, and reinforcement are required")
    source_hash = str(source.get("sha256", "")).lower()
    if (
        len(source_hash) != 64
        or any(character not in "0123456789abcdef" for character in source_hash)
    ):
        raise ValueError("Source SHA-256 is invalid")
    parameter = comparison.get("parameter")
    if parameter not in TRAINING_PARAMETERS:
        raise ValueError("Training parameter is not supported")
    phase = str(comparison.get("phase", "construction"))
    if phase not in TRAINING_PHASES:
        raise ValueError("Training phase is not supported")
    candidates = comparison.get("candidates")
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= 7:
        raise ValueError("A comparison requires two to seven candidates")
    candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    if len(candidate_ids) != len(candidates):
        raise ValueError("Candidate identifiers must be present and unique")
    positive = str(reinforcement.get("positive_candidate_id", ""))
    negatives = {
        str(identifier)
        for identifier in reinforcement.get("negative_candidate_ids", [])
    }
    reasons = {
        str(reason) for reason in reinforcement.get("reasons", [])
    }
    color_accuracy = str(reinforcement.get("color_accuracy", ""))
    if positive not in candidate_ids:
        raise ValueError("The positive candidate is not in this comparison")
    if positive in negatives or not negatives.issubset(candidate_ids):
        raise ValueError("Negative candidate selection is invalid")
    if not reasons or not reasons.issubset(TRAINING_REASONS):
        raise ValueError("At least one supported reinforcement reason is required")
    if color_accuracy not in TRAINING_COLOR_ACCURACY:
        raise ValueError("A supported color-accuracy classification is required")
    note = str(reinforcement.get("note", "")).strip()
    if len(note) > 2000:
        raise ValueError("Reviewer note exceeds 2,000 characters")

    candidates_by_id = {
        str(candidate["candidate_id"]): candidate for candidate in candidates
    }
    positive_candidate = candidates_by_id[positive]
    preference_pairs = [
        {
            "phase": phase,
            "parameter": parameter,
            "preferred": positive_candidate,
            "rejected": candidates_by_id[negative],
            "reasons": sorted(reasons),
            "color_accuracy": color_accuracy,
        }
        for negative in sorted(negatives)
    ]
    learning_target = {
        "phase": phase,
        "parameter": parameter,
        "preferred_candidate_id": positive,
        "preferred_value": positive_candidate.get("value"),
        "preferred_parameters": positive_candidate.get("parameters", {}),
        "preferred_metrics": positive_candidate.get("metrics", {}),
    }
    if phase == "separation":
        metrics = positive_candidate.get("metrics", {})
        learning_target["posterization_target"] = {
            key: metrics.get(key)
            for key in (
                "palette",
                "color_count",
                "quantized_color_count",
                "merged_shade_count",
                "mean_color_error_rgb",
            )
        }

    saved_at = datetime.now(timezone.utc)
    record_id = f"{saved_at:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
    record = {
        **payload,
        "record_id": record_id,
        "saved_at": saved_at.isoformat(),
        "learning_target": learning_target,
        "preference_pairs": preference_pairs,
        "reinforcement": {
            **reinforcement,
            "positive_candidate_id": positive,
            "negative_candidate_ids": sorted(negatives),
            "reasons": sorted(reasons),
            "color_accuracy": color_accuracy,
            "note": note,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{record_id}.json"
    temporary = output / f".{record_id}.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(destination)
    return {
        "record_id": record_id,
        "saved_at": record["saved_at"],
        "record": str(destination),
    }


def as_dst(operations: list[dict]) -> bytes:
    """Compile the browser's richer operation list through the local DST codec."""
    from pystitch import EmbPattern, write_dst

    if not any(operation.get("kind") == "stitch" for operation in operations):
        raise ValueError("A DST requires at least one stitch operation")
    pattern = EmbPattern()
    pattern.metadata("name", "New Hat Prototype")
    for operation in operations:
        kind = operation.get("kind")
        x, y = float(operation.get("x", 0)) * 10, float(operation.get("y", 0)) * 10
        if kind == "stitch":
            pattern.stitch_abs(x, y)
        elif kind == "jump":
            pattern.move_abs(x, y)
        elif kind == "trim":
            pattern.trim()
        elif kind == "color":
            pattern.color_change()
    pattern.end()
    output = io.BytesIO()
    write_dst(pattern, output)
    return output.getvalue()


def inspect_dst(dst: bytes) -> list[dict]:
    """Decode exactly what DST retained, for use by the simulator."""
    from pystitch import read_dst
    from pystitch.EmbConstant import COLOR_CHANGE, COMMAND_MASK, END, JUMP, STITCH, TRIM

    names = {STITCH: "stitch", JUMP: "jump", TRIM: "trim", COLOR_CHANGE: "color"}
    pattern = read_dst(io.BytesIO(dst))
    decoded = []
    for x, y, command in pattern.stitches:
        command &= COMMAND_MASK
        if command == END:
            break
        if command in names:
            decoded.append({"kind": names[command], "x": x / 10, "y": y / 10})
    return decoded


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        if urlparse(self.path).path.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        """Serve generated artifact galleries without exposing the project tree."""
        request_path = unquote(urlparse(path).path)
        prefix = "/artifacts"
        if request_path == prefix or request_path.startswith(f"{prefix}/"):
            relative = PurePosixPath(request_path[len(prefix):].lstrip("/"))
            candidate = (ARTIFACTS_ROOT / Path(*relative.parts)).resolve()
            try:
                candidate.relative_to(ARTIFACTS_ROOT.resolve())
            except ValueError:
                return str(ARTIFACTS_ROOT / "__not_found__")
            return str(candidate)
        return super().translate_path(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/export.dst", "/api/preview.dst", "/api/inspect.dst", "/api/digitize", "/api/vector/plan", "/api/vectorize", "/api/training/feedback"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if path == "/api/training/feedback" and length > 256 * 1024:
                raise ValueError("Training feedback exceeds 256 KB")
            body = self.rfile.read(length)
            if path == "/api/inspect.dst":
                dst = body
            elif path == "/api/training/feedback":
                saved = save_training_feedback(json.loads(body))
                payload = json.dumps({
                    "record_id": saved["record_id"],
                    "saved_at": saved["saved_at"],
                }).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            elif path == "/api/digitize":
                request = json.loads(body)
                encoded = request["image"].split(",", 1)[1]
                from digitizer import digitize_payload
                arguments = {
                    "width_mm": float(request["width_mm"]),
                    "colors": int(request["colors"]),
                    "profile": str(request.get("profile", "standard")),
                    "limits": request.get("limits"),
                    "density_multiplier": float(
                        request.get("density_multiplier", 1.0)
                    ),
                    "color_merge_tolerance": float(
                        request.get("color_merge_tolerance", 8.0)
                    ),
                    "fragmented_color_threshold": float(
                        request.get("fragmented_color_threshold", 0.20)
                    ),
                    "construction_mode": str(
                        request.get("construction_mode", "auto")
                    ),
                    "running_passes": int(
                        request.get("running_passes", 3)
                    ),
                    "include_posterized": bool(
                        request.get("include_posterized", False)
                    ),
                }
                result = digitize_payload(
                    base64.b64decode(encoded),
                    **arguments,
                )
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            elif path == "/api/vector/plan":
                from vector_document import VectorDocument, plan_document
                document = VectorDocument.from_json(json.loads(body))
                plan = plan_document(document)
                payload = json.dumps({
                    "document": document.json(),
                    "composition": document.composition(),
                    **plan,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            elif path == "/api/vectorize":
                request = json.loads(body)
                from vectorize import vectorize_posterized
                from vector_document import plan_document
                document = vectorize_posterized(
                    base64.b64decode(request["image"].split(",", 1)[1]),
                    width_mm=float(request.get("width_mm", 72)),
                )
                plan = plan_document(document)
                payload = json.dumps({
                    "document": document.json(),
                    "composition": document.composition(),
                    **plan,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            else:
                dst = as_dst(json.loads(body)["operations"])
        except Exception as error:
            self.send_error(400, f"Could not compile stitch plan: {error}")
            return
        if path in {"/api/preview.dst", "/api/inspect.dst"}:
            payload = json.dumps({"operations": inspect_dst(dst)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="new-hat-job.dst"')
        self.send_header("Content-Length", str(len(dst)))
        self.end_headers()
        self.wfile.write(dst)


if __name__ == "__main__":
    address = ("127.0.0.1", 8000)
    print(f"New Hat prototype: http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()

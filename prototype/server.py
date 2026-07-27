#!/usr/bin/env python3
"""Dependency-free local server for the New Hat vertical-slice prototype."""
from __future__ import annotations

import io
import importlib
import json
import sys
import base64
import hashlib
import re
import shutil
import types
import zipfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
ARTIFACTS_ROOT = ROOT.parent / "artifacts"
BRACKET_ARTIFACTS = ARTIFACTS_ROOT / "bracket-fixtures"
TRAINING_FEEDBACK_ROOT = ARTIFACTS_ROOT / "training-feedback"
TRAINING_SESSION_ROOT = ARTIFACTS_ROOT / "training-sessions-tmp"
DST_CODEC_ROOT = ROOT.parent / "pystitch" / "src" / "pystitch"
DST_CODEC_PACKAGE = "_newhat_pystitch_codec"
sys.path.insert(0, str(ROOT.parent / "pystitch" / "src"))

TRAINING_PARAMETERS = {
    "color_separation",
    "posterization_smoothing",
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
    "boundary_smoothness",
    "negative_space",
    "curve_flow",
    "detail_retention",
    "edge_quality",
    "silhouette_fidelity",
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
SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _decode_data_url(value: str) -> tuple[str, bytes]:
    if not isinstance(value, str) or not value.startswith("data:"):
        raise ValueError("Artifact must be a data URL")
    header, encoded = value.split(",", 1)
    if ";base64" not in header:
        raise ValueError("Artifact data URL must be base64 encoded")
    mime = header[5:].split(";", 1)[0].lower()
    extensions = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError("Artifact data URL is invalid") from error
    return extensions.get(mime, ".bin"), content


def _session_path(session_id: str, output: Path) -> Path:
    if not SAFE_IDENTIFIER.fullmatch(str(session_id)):
        raise ValueError("Training session identifier is invalid")
    return output / str(session_id)


def create_training_session(
    payload: dict, output: Path = TRAINING_SESSION_ROOT
) -> dict:
    """Create a temporary, exportable experiment session."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported training-session schema")
    source = payload.get("source")
    settings = payload.get("settings")
    if not isinstance(source, dict) or not isinstance(settings, dict):
        raise ValueError("Training-session source and settings are required")
    extension, source_bytes = _decode_data_url(source.get("data_url", ""))
    created_at = datetime.now(timezone.utc)
    session_id = f"{created_at:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
    root = _session_path(session_id, output)
    root.mkdir(parents=True, exist_ok=False)
    source_name = Path(str(source.get("name", "source-art"))).name
    source_path = root / f"source{extension}"
    source_path.write_bytes(source_bytes)
    manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "created_at": created_at.isoformat(),
        "source": {
            "name": source_name,
            "artifact": source_path.name,
        },
        "settings": settings,
        "origin": payload.get("origin"),
        "experiments": {},
    }
    _write_json(root / "session.json", manifest)
    return {
        "session_id": session_id,
        "created_at": manifest["created_at"],
    }


def save_training_experiment(
    payload: dict, output: Path = TRAINING_SESSION_ROOT
) -> dict:
    """Persist plans, renders, DST files, and parameters for one phase."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported training-experiment schema")
    session_id = str(payload.get("session_id", ""))
    root = _session_path(session_id, output)
    manifest_path = root / "session.json"
    recovered_without_source = False
    if not manifest_path.exists():
        recovered_without_source = True
        root.mkdir(parents=True, exist_ok=True)
        _write_json(manifest_path, {
            "schema_version": 1,
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "name": "Source unavailable during session recovery",
                "artifact": None,
            },
            "settings": {},
            "origin": "recovered",
            "recovered_without_source": True,
            "experiments": {},
        })
    experiment = payload.get("experiment")
    if not isinstance(experiment, dict):
        raise ValueError("Training experiment is required")
    parameter = str(experiment.get("parameter", ""))
    if parameter not in TRAINING_PARAMETERS:
        raise ValueError("Training parameter is not supported")
    phase = str(experiment.get("phase", ""))
    if phase not in TRAINING_PHASES:
        raise ValueError("Training phase is not supported")
    candidates = experiment.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 7:
        raise ValueError("A training experiment requires one to seven candidates")

    experiment_root = root / "experiments" / parameter
    if experiment_root.exists():
        shutil.rmtree(experiment_root)
    experiment_root.mkdir(parents=True)
    candidate_summaries = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Training candidate must be an object")
        candidate_id = str(candidate.get("candidate_id", ""))
        if not SAFE_IDENTIFIER.fullmatch(candidate_id):
            raise ValueError("Training candidate identifier is invalid")
        plan = candidate.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("Training candidate plan is required")
        candidate_root = experiment_root / candidate_id
        candidate_root.mkdir()
        _write_json(candidate_root / "parameters.json", {
            "candidate_id": candidate_id,
            "value": candidate.get("value"),
            "parameters": candidate.get("parameters", {}),
            "metrics": candidate.get("metrics", {}),
        })
        _write_json(candidate_root / "plan.json", plan)
        operations = plan.get("operations", [])
        dst_name = None
        if any(
            operation.get("kind") == "stitch"
            for operation in operations
            if isinstance(operation, dict)
        ):
            dst_name = "candidate.dst"
            (candidate_root / dst_name).write_bytes(as_dst(operations))
        for field, filename in (
            ("posterized_data_url", "posterized.png"),
            ("sewout_data_url", "sewout.png"),
        ):
            value = candidate.get(field)
            if value:
                _, content = _decode_data_url(value)
                (candidate_root / filename).write_bytes(content)
        candidate_summaries.append({
            "candidate_id": candidate_id,
            "value": candidate.get("value"),
            "directory": str(
                candidate_root.relative_to(root)
            ),
            "dst": dst_name,
        })
    review = experiment.get("review")
    experiment_manifest = {
        "phase": phase,
        "parameter": parameter,
        "reference": experiment.get("reference"),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidate_summaries,
        "review": review if isinstance(review, dict) else None,
    }
    _write_json(experiment_root / "experiment.json", experiment_manifest)
    manifest = json.loads(manifest_path.read_text())
    manifest["experiments"][parameter] = experiment_manifest
    _write_json(manifest_path, manifest)
    return {
        "session_id": session_id,
        "parameter": parameter,
        "candidate_count": len(candidate_summaries),
        "experiment_count": len(manifest["experiments"]),
        "recovered_without_source": recovered_without_source,
    }


def export_training_session(
    session_id: str, output: Path = TRAINING_SESSION_ROOT
) -> tuple[bytes, str]:
    """Build an in-memory archive without clearing the retryable session."""
    root = _session_path(session_id, output)
    if not (root / "session.json").exists():
        raise ValueError("Training session was not found or was already cleared")
    archive = io.BytesIO()
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED
    ) as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(root))
    return archive.getvalue(), f"training-session-{session_id}.zip"


def delete_training_session(
    session_id: str, output: Path = TRAINING_SESSION_ROOT
) -> bool:
    """Delete temporary sewouts and plans for an abandoned session."""
    root = _session_path(session_id, output)
    if not root.exists():
        return False
    shutil.rmtree(root)
    return True


def _training_signal_fingerprint(payload: dict) -> str:
    reinforcement = payload.get("reinforcement", {})
    canonical = {
        "schema_version": payload.get("schema_version"),
        "training_session_id": payload.get("training_session_id"),
        "source": payload.get("source"),
        "context": payload.get("context"),
        "comparison": payload.get("comparison"),
        "reinforcement": {
            "positive_candidate_id": reinforcement.get(
                "positive_candidate_id"
            ),
            "negative_candidate_ids": sorted(
                reinforcement.get("negative_candidate_ids", [])
            ),
            "reasons": sorted(reinforcement.get("reasons", [])),
            "color_accuracy": reinforcement.get("color_accuracy"),
            "note": str(reinforcement.get("note", "")).strip(),
        },
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


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
                "geometry_quality",
            )
        }

    signal_fingerprint = _training_signal_fingerprint(payload)
    output.mkdir(parents=True, exist_ok=True)
    for existing_path in output.glob("*.json"):
        try:
            existing = json.loads(existing_path.read_text())
        except (OSError, ValueError):
            continue
        existing_fingerprint = (
            existing.get("signal_fingerprint")
            or _training_signal_fingerprint(existing)
        )
        if existing_fingerprint == signal_fingerprint:
            return {
                "record_id": existing["record_id"],
                "saved_at": existing["saved_at"],
                "record": str(existing_path),
                "duplicate": True,
            }

    saved_at = datetime.now(timezone.utc)
    record_id = f"{saved_at:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
    record = {
        **payload,
        "record_id": record_id,
        "saved_at": saved_at.isoformat(),
        "signal_fingerprint": signal_fingerprint,
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
    destination = output / f"{record_id}.json"
    temporary = output / f".{record_id}.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(destination)
    return {
        "record_id": record_id,
        "saved_at": record["saved_at"],
        "record": str(destination),
        "duplicate": False,
    }


def as_dst(operations: list[dict]) -> bytes:
    """Compile the browser's richer operation list through the local DST codec."""
    pattern_module, writer_module, _ = _dst_codec_modules()
    EmbPattern = pattern_module.EmbPattern

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
    EmbPattern.write_embroidery(writer_module, pattern, output)
    return output.getvalue()


def inspect_dst(dst: bytes) -> list[dict]:
    """Decode exactly what DST retained, for use by the simulator."""
    pattern_module, _, reader_module = _dst_codec_modules()
    constants = importlib.import_module(
        f"{DST_CODEC_PACKAGE}.EmbConstant"
    )
    COLOR_CHANGE = constants.COLOR_CHANGE
    COMMAND_MASK = constants.COMMAND_MASK
    END = constants.END
    JUMP = constants.JUMP
    STITCH = constants.STITCH
    TRIM = constants.TRIM

    names = {STITCH: "stitch", JUMP: "jump", TRIM: "trim", COLOR_CHANGE: "color"}
    pattern = pattern_module.EmbPattern.read_embroidery(
        reader_module,
        io.BytesIO(dst),
    )
    decoded = []
    for x, y, command in pattern.stitches:
        command &= COMMAND_MASK
        if command == END:
            break
        if command in names:
            decoded.append({"kind": names[command], "x": x / 10, "y": y / 10})
    return decoded


def _dst_codec_modules():
    """Load only the DST codec, without unrelated format dependencies."""
    if DST_CODEC_PACKAGE not in sys.modules:
        package = types.ModuleType(DST_CODEC_PACKAGE)
        package.__package__ = DST_CODEC_PACKAGE
        package.__path__ = [str(DST_CODEC_ROOT)]
        sys.modules[DST_CODEC_PACKAGE] = package
    return (
        importlib.import_module(f"{DST_CODEC_PACKAGE}.EmbPattern"),
        importlib.import_module(f"{DST_CODEC_PACKAGE}.DstWriter"),
        importlib.import_module(f"{DST_CODEC_PACKAGE}.DstReader"),
    )


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
        if path not in {
            "/api/export.dst",
            "/api/preview.dst",
            "/api/inspect.dst",
            "/api/digitize",
            "/api/vector/plan",
            "/api/vectorize",
            "/api/training/feedback",
            "/api/training/session/create",
            "/api/training/session/experiment",
            "/api/training/session/export",
            "/api/training/session/cleanup",
        }:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if path == "/api/training/feedback" and length > 256 * 1024:
                raise ValueError("Training feedback exceeds 256 KB")
            if (
                path == "/api/training/session/experiment"
                and length > 100 * 1024 * 1024
            ):
                raise ValueError("Training experiment exceeds 100 MB")
            body = self.rfile.read(length)
            if path == "/api/inspect.dst":
                dst = body
            elif path == "/api/training/session/create":
                saved = create_training_session(json.loads(body))
                payload = json.dumps(saved).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            elif path == "/api/training/session/experiment":
                saved = save_training_experiment(json.loads(body))
                payload = json.dumps(saved).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            elif path == "/api/training/session/export":
                request = json.loads(body)
                session_id = str(request.get("session_id", ""))
                archive, filename = export_training_session(
                    session_id
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(len(archive)))
                self.end_headers()
                try:
                    self.wfile.write(archive)
                except (BrokenPipeError, ConnectionResetError):
                    # Keep the session intact so the browser can retry.
                    return
                delete_training_session(session_id)
                return
            elif path == "/api/training/session/cleanup":
                request = json.loads(body)
                deleted = delete_training_session(
                    str(request.get("session_id", ""))
                )
                payload = json.dumps({"deleted": deleted}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            elif path == "/api/training/feedback":
                saved = save_training_feedback(json.loads(body))
                payload = json.dumps({
                    "record_id": saved["record_id"],
                    "saved_at": saved["saved_at"],
                    "duplicate": saved["duplicate"],
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
                    "posterization_smoothing_px": float(
                        request.get("posterization_smoothing_px", 0.0)
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
        except (BrokenPipeError, ConnectionResetError):
            return
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

from io import BytesIO
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image, ImageDraw

from digitizer import (
    Operation,
    _enforce_stitch_criteria,
    _satin_rails,
    digitize,
    digitize_payload,
)
from server import (
    ARTIFACTS_ROOT,
    BRACKET_ARTIFACTS,
    Handler,
    as_dst,
    create_training_session,
    delete_training_session,
    export_training_session,
    inspect_dst,
    save_training_experiment,
    save_training_feedback,
)
from tools.render_stitch_preview import render


class DigitizerTest(unittest.TestCase):
    def test_dst_export_does_not_require_optional_xml_writer(self):
        script = """
import builtins
from server import as_dst

real_import = builtins.__import__
def without_xml(name, *args, **kwargs):
    if name == "xml" or name.startswith("xml."):
        raise ModuleNotFoundError(
            "No module named 'xml'",
            name="xml",
        )
    return real_import(name, *args, **kwargs)

builtins.__import__ = without_xml
dst = as_dst([
    {"kind": "stitch", "x": 0, "y": 0},
    {"kind": "stitch", "x": 1, "y": 1},
    {"kind": "end", "x": 1, "y": 1},
])
assert len(dst) > 512
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0,
            result.returncode,
            result.stderr,
        )

    def test_training_workspace_is_linked_and_omits_machine_chrome(self):
        prototype_root = Path(__file__).resolve().parent
        workspace = (prototype_root / "index.html").read_text()
        training = (prototype_root / "train.html").read_text().lower()
        training_script = (prototype_root / "train.js").read_text()
        self.assertIn('id="trainModel"', workspace)
        self.assertIn("train model", workspace.lower())
        self.assertIn("source art", training)
        self.assertIn("pipeline experiments", training)
        self.assertIn("posterized art", training)
        self.assertIn("boundary cleanup", training_script.lower())
        self.assertIn("boundary_smoothness", training)
        self.assertIn("detail_retention", training)
        self.assertIn("silhouette_fidelity", training)
        self.assertIn("bracketed sewouts", training)
        self.assertIn("training signal", training)
        self.assertNotIn("job preflight", training)
        self.assertNotIn("needle bar", training)
        self.assertNotIn("pagehide", training_script)
        self.assertIn(
            "for(const parameter of state.experiments.keys())",
            training_script,
        )

    def test_training_feedback_persists_validated_pairwise_signal(self):
        payload = {
            "schema_version": 1,
            "training_session_id": "session-123",
            "source": {
                "name": "script-mark.png",
                "sha256": "a" * 64,
            },
            "context": {
                "origin": "advanced",
                "width_mm": 110,
                "colors": 2,
            },
            "comparison": {
                "phase": "construction",
                "parameter": "satin_spacing_mm",
                "reference": "0.40–0.44 mm",
                "candidates": [
                    {
                        "candidate_id": "satin_spacing_mm-0.36",
                        "value": 0.36,
                        "metrics": {"stitches": 2200, "status": "warning"},
                    },
                    {
                        "candidate_id": "satin_spacing_mm-0.42",
                        "value": 0.42,
                        "metrics": {"stitches": 1900, "status": "pass"},
                    },
                    {
                        "candidate_id": "satin_spacing_mm-0.5",
                        "value": 0.5,
                        "metrics": {"stitches": 1650, "status": "warning"},
                    },
                ],
            },
            "reinforcement": {
                "positive_candidate_id": "satin_spacing_mm-0.42",
                "negative_candidate_ids": ["satin_spacing_mm-0.5"],
                "reasons": ["negative_space", "curve_flow", "color_accuracy"],
                "color_accuracy": "accurate",
                "note": "The counter remains open.",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            saved = save_training_feedback(payload, output)
            saved_record = Path(saved["record"])
            legacy_record = json.loads(saved_record.read_text())
            legacy_record.pop("signal_fingerprint")
            saved_record.write_text(json.dumps(legacy_record))
            duplicate = save_training_feedback(payload, output)
            self.assertFalse(saved["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(saved["record_id"], duplicate["record_id"])
            self.assertEqual(1, len(list(output.glob("*.json"))))
            record_path = Path(saved["record"])
            self.assertTrue(record_path.exists())
            record = json.loads(record_path.read_text())
            self.assertEqual(
                "satin_spacing_mm-0.42",
                record["reinforcement"]["positive_candidate_id"],
            )
            self.assertEqual(
                ["color_accuracy", "curve_flow", "negative_space"],
                record["reinforcement"]["reasons"],
            )
            self.assertEqual(
                "accurate",
                record["reinforcement"]["color_accuracy"],
            )
            self.assertEqual(
                "construction",
                record["learning_target"]["phase"],
            )
            self.assertEqual(
                "session-123",
                record["training_session_id"],
            )
            self.assertEqual(1, len(record["preference_pairs"]))

    def test_training_feedback_retains_posterization_geometry_target(self):
        geometry = {
            "corners_per_100_boundary_px": 55.2,
            "negative_space_count": 4,
            "reference_iou": 0.986,
        }
        with tempfile.TemporaryDirectory() as temporary:
            saved = save_training_feedback({
                "schema_version": 1,
                "source": {
                    "name": "rough-mark.png",
                    "sha256": "c" * 64,
                },
                "context": {"width_mm": 72, "colors": 2},
                "comparison": {
                    "phase": "separation",
                    "parameter": "posterization_smoothing",
                    "candidates": [
                        {
                            "candidate_id": "posterization_smoothing-none",
                            "value": "none",
                            "metrics": {"geometry_quality": {}},
                        },
                        {
                            "candidate_id": "posterization_smoothing-balanced",
                            "value": "balanced",
                            "parameters": {
                                "posterization_smoothing_px": 0.85,
                            },
                            "metrics": {"geometry_quality": geometry},
                        },
                    ],
                },
                "reinforcement": {
                    "positive_candidate_id":
                        "posterization_smoothing-balanced",
                    "negative_candidate_ids": [
                        "posterization_smoothing-none",
                    ],
                    "reasons": [
                        "boundary_smoothness",
                        "detail_retention",
                        "silhouette_fidelity",
                    ],
                    "color_accuracy": "accurate",
                },
            }, Path(temporary))
            record = json.loads(Path(saved["record"]).read_text())
            target = record["learning_target"]["posterization_target"]
            self.assertEqual(geometry, target["geometry_quality"])
            self.assertEqual(
                0.85,
                record["learning_target"]["preferred_parameters"]
                ["posterization_smoothing_px"],
            )

    def test_training_session_archive_remains_retryable_until_cleanup(self):
        image = Image.new("RGBA", (8, 6), (255, 255, 255, 255))
        stream = BytesIO()
        image.save(stream, format="PNG")
        data_url = (
            "data:image/png;base64,"
            + base64.b64encode(stream.getvalue()).decode()
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            session = create_training_session({
                "schema_version": 1,
                "source": {"name": "white-mark.png", "data_url": data_url},
                "settings": {"width_mm": 72, "colors": 2},
                "origin": "basic",
            }, output)
            session_id = session["session_id"]
            saved = save_training_experiment({
                "schema_version": 1,
                "session_id": session_id,
                "experiment": {
                    "phase": "construction",
                    "parameter": "tatami_density",
                    "reference": "0.60–1.50×",
                    "candidates": [{
                        "candidate_id": "tatami_density-1.0",
                        "value": "1.0",
                        "parameters": {"density_multiplier": 1.0},
                        "metrics": {"stitches": 2},
                        "posterized_data_url": data_url,
                        "sewout_data_url": data_url,
                        "plan": {
                            "operations": [
                                {"kind": "stitch", "x": 0, "y": 0},
                                {"kind": "stitch", "x": 1, "y": 0},
                            ],
                        },
                    }],
                },
            }, output)
            self.assertEqual(1, saved["experiment_count"])
            archive, filename = export_training_session(session_id, output)
            self.assertTrue(filename.endswith(".zip"))
            self.assertTrue((output / session_id).exists())
            with zipfile.ZipFile(BytesIO(archive)) as bundle:
                names = set(bundle.namelist())
            self.assertIn("session.json", names)
            self.assertIn(
                "experiments/tatami_density/"
                "tatami_density-1.0/candidate.dst",
                names,
            )
            self.assertIn(
                "experiments/tatami_density/"
                "tatami_density-1.0/sewout.png",
                names,
            )
            self.assertTrue(delete_training_session(session_id, output))
            self.assertFalse((output / session_id).exists())
            recovered = save_training_experiment({
                "schema_version": 1,
                "session_id": session_id,
                "experiment": {
                    "phase": "construction",
                    "parameter": "tatami_density",
                    "candidates": [{
                        "candidate_id": "tatami_density-1.0",
                        "plan": {
                            "operations": [
                                {"kind": "stitch", "x": 0, "y": 0},
                                {"kind": "stitch", "x": 1, "y": 0},
                            ],
                        },
                    }],
                },
            }, output)
            self.assertTrue(recovered["recovered_without_source"])
            recovered_manifest = json.loads(
                (output / session_id / "session.json").read_text()
            )
            self.assertTrue(
                recovered_manifest["recovered_without_source"]
            )

    def test_training_feedback_rejects_candidate_outside_comparison(self):
        with self.assertRaisesRegex(ValueError, "positive candidate"):
            save_training_feedback({
                "schema_version": 1,
                "source": {"name": "mark.png", "sha256": "b" * 64},
                "comparison": {
                    "phase": "construction",
                    "parameter": "density_multiplier",
                    "candidates": [
                        {"candidate_id": "density_multiplier-0.8"},
                        {"candidate_id": "density_multiplier-1.0"},
                    ],
                },
                "reinforcement": {
                    "positive_candidate_id": "density_multiplier-1.2",
                    "negative_candidate_ids": [],
                    "reasons": ["coverage"],
                    "color_accuracy": "acceptable",
                },
            }, Path("/tmp/not-written-training-feedback"))

    def test_bracket_gallery_route_is_scoped_to_generated_artifacts(self):
        handler = Handler.__new__(Handler)
        self.assertEqual(
            (BRACKET_ARTIFACTS / "index.html").resolve(),
            Path(handler.translate_path(
                "/artifacts/bracket-fixtures/index.html"
            )).resolve(),
        )
        escaped = Path(handler.translate_path(
            "/artifacts/bracket-fixtures/../../README.md"
        )).resolve()
        self.assertEqual(
            (ARTIFACTS_ROOT / "__not_found__").resolve(),
            escaped,
        )

    def test_white_thread_survives_transparent_background_planning(self):
        image = Image.new("RGBA", (100, 45), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 5, 42, 39), fill=(252, 252, 248, 255))
        draw.rectangle((58, 5, 95, 39), fill=(30, 72, 126, 255))
        stream = BytesIO()
        image.save(stream, format="PNG")
        plan = digitize(
            stream.getvalue(),
            width_mm=72,
            colors=2,
            include_posterized=True,
        )
        white_regions = [
            region for region in plan.regions
            if all(
                int(region.color[offset:offset + 2], 16) >= 235
                for offset in (1, 3, 5)
            )
        ]
        self.assertTrue(white_regions)
        self.assertTrue(any(
            operation.kind == "stitch"
            for region in white_regions
            for operation in region.operations
        ))

    def test_single_plan_has_three_layers_and_adjustable_density(self):
        image = Image.new("RGBA", (220, 120), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 20, 100, 26), fill="#153d7a")
        draw.ellipse((115, 15, 205, 105), fill="#d22d54")
        draw.rectangle((145, 45, 175, 75), fill=(0, 0, 0, 0))
        draw.rectangle((10, 70, 18, 78), fill="#d22d54")
        draw.rectangle((30, 70, 38, 78), fill="#d22d54")
        stream = BytesIO()
        image.save(stream, format="PNG")

        low = digitize(
            stream.getvalue(), width_mm=72, colors=3,
            density_multiplier=0.7,
        )
        high = digitize(
            stream.getvalue(), width_mm=72, colors=3,
            density_multiplier=1.3,
        )
        self.assertEqual(
            ["underlay", "blocking", "detail"],
            [layer["id"] for layer in high.json()["layers"]],
        )
        self.assertTrue(high.operations)
        self.assertEqual(0, high.posterization["dropped_objects_for_movement_budget"])
        self.assertEqual(
            low.posterization["source_geometry_elements"],
            high.posterization["source_geometry_elements"],
        )
        self.assertGreater(high.drc()["stitch_count"], low.drc()["stitch_count"])
        underlay_positions = [
            index for index, operation in enumerate(high.operations)
            if "underlay" in operation.role
        ]
        blocking_positions = [
            index for index, operation in enumerate(high.operations)
            if operation.role in {"top_fill", "satin_top"}
        ]
        detail_positions = [
            index for index, operation in enumerate(high.operations)
            if operation.role == "top_detail_running"
        ]
        self.assertTrue(underlay_positions)
        self.assertTrue(blocking_positions)
        self.assertLess(max(underlay_positions), min(blocking_positions))
        if detail_positions:
            self.assertLess(max(blocking_positions), min(detail_positions))
        else:
            detail_layer = next(
                layer for layer in high.layer_summary()
                if layer["id"] == "detail"
            )
            self.assertEqual(0, detail_layer["stitch_count"])
        self.assertIn("pass_filter_sets", high.posterization)
        self.assertTrue(high.posterization["element_filter_analysis"])
        for analysis in high.posterization["element_filter_analysis"]:
            self.assertEqual(
                {"underlay", "blocking", "detail"},
                set(analysis["passes"]),
            )

    def test_running_detail_uses_centerline_instead_of_double_outline(self):
        image = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((10, 28, 190, 29), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        region = next(region for region in plan.regions if region.pass_index == 2)
        self.assertEqual("running", region.stitch_type)
        running = [
            operation for operation in region.operations
            if operation.role in {"detail_running", "top_detail_running"}
            and operation.kind == "stitch"
        ]
        self.assertLess(len(running), 90)
        self.assertLess(
            max(operation.y for operation in running)
            - min(operation.y for operation in running),
            0.5,
        )

    def test_posterized_satin_rails_smooth_one_pixel_edge_jitter(self):
        # Alternating one-pixel shifts approximate a diagonal after palette
        # quantization. The rail smoother should produce an aligned column.
        component = {
            (x, y)
            for y in range(9)
            for x in range(5 + y % 2, 17 + y % 2)
        }
        rails = list(_satin_rails(component, vertical=True, step=1))
        self.assertEqual(1, len({left[0] for left, _ in rails}))
        self.assertEqual(1, len({right[0] for _, right in rails}))

    def test_design_center_underlay_precedes_object_underlay(self):
        image = Image.new("RGBA", (180, 90), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((15, 20, 165, 70), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")
        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        self.assertTrue(plan.foundation_operations)
        self.assertEqual("design_center_underlay", plan.operations[0].role)
        self.assertTrue(any(
            op.role == "silhouette_tatami_underlay"
            for op in plan.foundation_operations
        ))

    def test_shared_criteria_splits_oversize_non_tie_stitches(self):
        operations = [
            Operation("jump", 0, 0, "entry", "r"),
            Operation("stitch", 0, 0, "top_fill", "r"),
            Operation("stitch", 25, 0, "top_fill", "r"),
        ]
        checked = _enforce_stitch_criteria(operations, maximum_radial_mm=12)
        stitches = [operation for operation in checked if operation.kind == "stitch"]
        self.assertEqual(4, len(stitches))
        for actual, expected in zip((operation.x for operation in stitches), (0, 25 / 3, 50 / 3, 25)):
            self.assertAlmostEqual(expected, actual)

    def test_drc_reports_machine_travel_and_density_measures(self):
        image = Image.new("RGBA", (100, 70), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((10, 10, 90, 60), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")
        report = digitize(stream.getvalue(), width_mm=72, colors=2).drc()
        self.assertGreater(report["jump_count"], 0)
        self.assertGreaterEqual(report["trim_count"], 1)
        self.assertGreater(report["local_density_variance"], 0)
        self.assertIsNotNone(report["maximum_axial_stitch_mm"])

    def test_product_profiles_adjust_machine_limits_and_cap_routing(self):
        image = Image.new("RGBA", (180, 80), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 20, 40, 60), fill="#153d7a")
        draw.rectangle((75, 20, 105, 60), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")
        cap = digitize(stream.getvalue(), width_mm=72, colors=2, profile="structured_cap")
        self.assertEqual("structured_cap", cap.profile)
        self.assertEqual(7.0, cap.drc()["machine_max_stitch_mm"])
        self.assertEqual("c0-2", cap.regions[0].id)
        with self.assertRaisesRegex(ValueError, "unknown digitizing profile"):
            digitize(stream.getvalue(), profile="unknown")

    def test_codec_rejects_an_empty_dst(self):
        with self.assertRaisesRegex(ValueError, "at least one stitch"):
            as_dst([])

    def test_two_color_regions_have_structured_fill_roles(self):
        image = Image.new("RGBA", (160, 90), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((15, 15, 80, 75), fill="#d22d54")
        draw.rectangle((95, 25, 145, 65), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        roles = {operation.role for operation in plan.operations}

        interior = [region for region in plan.regions if region.pass_name == "interior"]
        self.assertEqual(2, len(interior))
        self.assertTrue({"tie_in", "edge_underlay", "top_fill", "tie_off", "trim"} <= roles)
        self.assertIn("silhouette_tatami_underlay", roles)
        decoded = inspect_dst(as_dst([operation.__dict__ for operation in plan.operations]))
        self.assertTrue(any(operation["kind"] == "color" for operation in decoded))

    def test_same_color_components_share_a_thread_block(self):
        image = Image.new("RGBA", (180, 90), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        # Two disconnected red components must not cause two thread changes.
        draw.ellipse((10, 20, 55, 70), fill="#d22d54")
        draw.ellipse((70, 20, 115, 70), fill="#d22d54")
        draw.rectangle((130, 20, 170, 70), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        red = "#d22d54"
        self.assertEqual(2, len([
            region for region in plan.regions
            if region.color == red and region.pass_name == "interior"
        ]))
        # Each physical pass may revisit the palette, but disconnected objects
        # within one color/layer never create a change by themselves.
        self.assertLessEqual(
            len([operation for operation in plan.operations if operation.kind == "color"]),
            len(plan.palette) * 3 - 1,
        )
        self.assertLess(
            plan.operations.index(next(operation for operation in plan.operations if operation.kind == "color")),
            len(plan.operations) - 1,
        )

    def test_closed_region_retains_an_interior_hole(self):
        image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((15, 15, 105, 105), fill="#153d7a")
        draw.rectangle((45, 45, 75, 75), fill=(0, 0, 0, 0))
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        interior = [region for region in plan.regions if region.pass_name == "interior"]
        self.assertEqual(1, len(interior))
        self.assertEqual(2, len(interior[0].contours))
        # Contours are semantic geometry and are preserved in the JSON plan.
        self.assertEqual(2, len(interior[0].contours))

    def test_fill_uses_rotated_physical_stitch_rows(self):
        image = Image.new("RGBA", (160, 90), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((20, 15, 140, 75), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        top = [operation for operation in plan.regions[0].operations if operation.role == "top_fill"]
        diagonal_segment = any(
            left.kind == right.kind == "stitch"
            and abs(right.x - left.x) > 0.01
            and abs(right.y - left.y) > 0.01
            for left, right in zip(top, top[1:])
        )
        self.assertTrue(diagonal_segment)
        self.assertTrue(any(operation.role == "fill_connector" for operation in plan.regions[0].operations))
        self.assertLess(len([operation for operation in top if operation.kind == "jump"]), 100)

    def test_tall_artwork_fits_without_vertical_squash(self):
        image = Image.new("RGBA", (90, 180), "#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        self.assertAlmostEqual(55.0, plan.height_mm)
        self.assertAlmostEqual(27.5, plan.width_mm)

    def test_narrow_regions_use_satin_or_detail_running_not_fill(self):
        image = Image.new("RGBA", (180, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((15, 20, 155, 29), fill="#153d7a")  # about 4 mm wide: satin
        draw.rectangle((15, 55, 155, 56), fill="#d22d54")  # about 1 mm wide: detail running
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        types = {region.stitch_type for region in plan.regions}
        roles = {operation.role for operation in plan.operations}
        self.assertTrue({"satin", "running"} <= types)
        self.assertTrue({"satin_top", "satin_center_underlay", "top_detail_running"} <= roles)
        satin = next(region for region in plan.regions if region.stitch_type == "satin")
        running = next(
            region for region in plan.regions if region.stitch_type == "running"
        )
        self.assertTrue(satin.stitch_diagnostics["satin"]["valid"])
        self.assertGreater(satin.stitch_diagnostics["satin"]["pair_count"], 2)
        self.assertFalse(any(
            region.pass_index == 1 and region.element_id == running.element_id
            for region in plan.regions
        ))
        self.assertTrue(
            running.stitch_diagnostics["pass_filters"]["blocking"]
            ["replaced_by_running_detail"]
        )

    def test_construction_brackets_emit_distinct_stitch_families(self):
        image = Image.new("RGBA", (220, 80), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle(
            (10, 34, 210, 44), radius=5, fill="#b81438"
        )
        stream = BytesIO()
        image.save(stream, format="PNG")

        automatic = digitize_payload(
            stream.getvalue(),
            width_mm=90,
            colors=4,
            construction_mode="auto",
            include_posterized=True,
        )
        tatami = digitize_payload(
            stream.getvalue(),
            width_mm=90,
            colors=4,
            construction_mode="tatami",
            include_posterized=True,
        )
        multi_run = digitize_payload(
            stream.getvalue(),
            width_mm=90,
            colors=4,
            construction_mode="multi_run",
            running_passes=3,
            include_posterized=True,
        )

        self.assertTrue(
            automatic["posterization"]["preview_data_url"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertIn(
            "fill",
            {region["stitch_type"] for region in tatami["regions"]},
        )
        self.assertIn(
            "top_multi_running",
            {operation["role"] for operation in multi_run["operations"]},
        )
        signatures = {
            tuple(
                (operation["kind"], operation.get("role"))
                for operation in payload["operations"]
            )
            for payload in (automatic, tatami, multi_run)
        }
        self.assertEqual(3, len(signatures))

    def test_plan_reports_density_and_budget_without_erasing_geometry(self):
        image = Image.new("RGBA", (240, 120), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for index in range(12):
            x = 5 + index * 19
            draw.rectangle((x, 35, x + 12, 47), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(
            stream.getvalue(), width_mm=72, colors=2,
            limits={"max_objects": 8, "max_trims": 20, "max_jumps": 140},
        )
        report = plan.drc()
        interior = [
            region for region in plan.regions if region.pass_name == "interior"
        ]
        self.assertEqual(12, len(interior))
        self.assertGreater(report["stitches_per_sq_in"], 0)
        self.assertEqual(8, report["planning_limits"]["max_objects"])
        self.assertGreater(report["budget_violations"]["objects"], 0)
        self.assertFalse(report["budget_pass"])
        self.assertEqual(0, plan.posterization["dropped_objects_for_movement_budget"])
        self.assertIn("interior", report["composition_passes"])
        self.assertTrue(any(
            operation.role == "silhouette_tatami_underlay"
            for operation in plan.foundation_operations
        ))

    def test_a9qr_posterization_preserves_square_binary_art(self):
        image_bytes = (Path(__file__).resolve().parent.parent / "a9qr.png").read_bytes()
        plan = digitize(image_bytes, width_mm=55, colors=2)

        self.assertEqual(["#000000"], plan.palette)
        self.assertAlmostEqual(55.0, plan.width_mm)
        self.assertAlmostEqual(55.0, plan.height_mm)
        self.assertTrue(plan.regions)
        self.assertTrue(all(region.color == "#000000" and region.stitch_type == "fill" for region in plan.regions))
        self.assertFalse(any(operation.kind == "color" for operation in plan.operations))
        self.assertEqual(0, plan.drc()["short_stitches"])
        self.assertEqual(0, plan.drc()["long_stitches"])

    @unittest.skipUnless(shutil.which("zbarimg"), "zbarimg is required for QR visual regression")
    def test_a9qr_decoded_dst_still_scans(self):
        image_bytes = (Path(__file__).resolve().parent.parent / "a9qr.png").read_bytes()
        plan = digitize(image_bytes, width_mm=55, colors=2)
        with tempfile.TemporaryDirectory() as directory:
            dst = Path(directory) / "a9qr.dst"
            preview = Path(directory) / "a9qr-preview.png"
            dst.write_bytes(as_dst([operation.__dict__ for operation in plan.operations]))
            render(dst, preview, pixels_per_mm=20)
            result = subprocess.run(["zbarimg", "--quiet", str(preview)], check=True, capture_output=True, text=True)
        self.assertEqual("QR-Code:https://a9group.net", result.stdout.strip())

    def test_posterization_keeps_half_millimetre_color_detail(self):
        image = Image.new("RGBA", (1000, 500), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 100, 500, 400), fill="#153d7a")
        # 7 pixels at this source scale is about 0.5 mm in a 72 mm-wide plan.
        draw.rectangle((700, 200, 707, 207), fill="#d22d54")
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        self.assertIn("#d22d54", plan.palette)
        self.assertTrue(any(region.color == "#d22d54" for region in plan.regions))

    def test_posterization_smoothing_is_reported_and_changes_plan_geometry(self):
        image = Image.new("RGB", (100, 70), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 8, 88, 62), fill="#b81438")
        draw.ellipse((35, 24, 65, 46), fill="white")
        for x in range(20, 81, 2):
            draw.point((x, 7 - x % 2), fill="#b81438")
        stream = BytesIO()
        image.save(stream, format="PNG")

        raw = digitize(
            stream.getvalue(),
            width_mm=72,
            colors=2,
            posterization_smoothing_px=0,
            include_posterized=True,
        )
        smoothed = digitize(
            stream.getvalue(),
            width_mm=72,
            colors=2,
            posterization_smoothing_px=1.25,
            include_posterized=True,
        )

        raw_quality = raw.posterization["geometry_quality"]
        smooth_quality = smoothed.posterization["geometry_quality"]
        self.assertEqual(1.25, smoothed.posterization["smoothing_radius_px"])
        self.assertLess(
            smooth_quality["small_component_count"],
            raw_quality["small_component_count"],
        )
        self.assertEqual(1, smooth_quality["negative_space_count"])
        self.assertGreater(smooth_quality["reference_iou"], 0.98)
        self.assertNotEqual(
            len(raw.regions),
            len(smoothed.regions),
        )

    def test_opaque_script_art_is_sized_from_foreground_not_white_canvas(self):
        image = Path(__file__).resolve().parent.parent / "bobevans.jpg"
        plan = digitize(image.read_bytes(), width_mm=110, colors=8)
        self.assertAlmostEqual(110.0, plan.width_mm)
        self.assertAlmostEqual(21.5, plan.height_mm, delta=0.2)
        self.assertEqual(1, len(plan.palette))
        self.assertFalse(any(
            operation.kind == "color" for operation in plan.operations
        ))
        self.assertEqual(
            1, plan.posterization["semantic_color_count"]
        )
        self.assertTrue(
            plan.posterization["stroke_vector_candidate"]
        )
        self.assertTrue(plan.posterization["cropped_to_foreground"])
        self.assertEqual(
            {"width": 500, "height": 500},
            plan.posterization["source_size_px"],
        )

    def test_auto_payload_selects_one_color_satin_rail_plan(self):
        image = Path(__file__).resolve().parent.parent / "bobevans.jpg"
        payload = digitize_payload(
            image.read_bytes(), width_mm=110, colors=8
        )
        self.assertEqual(1, len(payload["palette"]))
        red = payload["palette"][0].lstrip("#")
        red, green, blue = (
            int(red[index:index + 2], 16) for index in (0, 2, 4)
        )
        self.assertGreater(red, green * 4)
        self.assertGreater(red, blue * 2)
        self.assertEqual(
            "stroke_vector",
            payload["posterization"]["selected_planner"],
        )
        satin = [
            region for region in payload["regions"]
            if region["stitch_type"] == "satin"
        ]
        self.assertGreaterEqual(len(satin), 20)
        self.assertTrue(all(
            len(region["satin_rails"]) == 2 for region in satin
        ))
        self.assertTrue(payload["brackets"])
        self.assertEqual(
            "new-hat-drc/1.1",
            payload["drc_policy"]["policy_version"],
        )
        self.assertIn(
            payload["drc_policy"]["status"],
            {"pass", "review", "block"},
        )
        self.assertTrue(all(
            bracket["object_id"] in {
                region["id"] for region in satin
            }
            for bracket in payload["brackets"]
        ))
        self.assertEqual(
            0,
            sum(
                operation["kind"] == "color"
                for operation in payload["operations"]
            ),
        )


if __name__ == "__main__":
    unittest.main()

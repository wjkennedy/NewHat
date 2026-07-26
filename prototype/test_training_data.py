import json
import tempfile
import unittest
from pathlib import Path

from prototype.drc_policy import evaluate_drc, regression_findings
from prototype.training_data import (
    build_training_record,
    deterministic_split,
    record_integrity_findings,
)
from tools.build_ml_corpus import build


def payload(**drc_overrides):
    drc = {
        "stitch_count": 100,
        "short_stitches": 0,
        "long_stitches": 0,
        "trim_count": 1,
        "jump_count": 1,
        "trims_per_1000_stitches": 1.0,
        "jumps_per_1000_stitches": 1.0,
        "quality_score": 98,
        "semantic_violations": {},
        "budget_violations": {},
        **drc_overrides,
    }
    return {
        "version": "2.0",
        "dimensions_mm": {"width": 72, "height": 20},
        "profile": "standard",
        "algorithm": "layered",
        "palette": ["#123456"],
        "posterization": {
            "selected_planner": "raster_layered",
            "construction_mode": "tatami",
            "color_separation": {
                "merge_tolerance_rgb": 8,
                "semantic_color_count": 1,
            },
        },
        "regions": [{
            "id": "object-1",
            "element_id": "element-1",
            "pass_name": "blocking",
            "stitch_type": "tatami",
        }],
        "operations": [{"kind": "stitch", "x": 0, "y": 0}],
        "findings": [],
        "brackets": [],
        "drc": drc,
    }


def record(plan: dict, annotation: dict | None = None) -> dict:
    return build_training_record(
        plan,
        source_sha256="a" * 64,
        source_ref="fixture.png",
        settings={
            "width_mm": 72,
            "colors": 2,
            "profile": "standard",
            "density_multiplier": 1,
        },
        plan_ref="fixture--plan.json",
        annotation=annotation,
    )


class DrcTrainingTests(unittest.TestCase):
    def test_training_record_retains_separation_and_construction_evidence(self):
        training_record = record(payload())
        observations = training_record["features"]["artwork_observations"]
        self.assertEqual(
            8,
            observations["color_separation"]["merge_tolerance_rgb"],
        )
        self.assertEqual(
            "tatami",
            training_record["proposal"]["construction_mode"],
        )

    def test_drc_policy_distinguishes_pass_review_and_block(self):
        self.assertEqual(evaluate_drc(payload())["status"], "pass")

        review = payload(short_stitches=3)
        review["brackets"] = [{
            "status": "blocker",
            "rule": "inside_curve_radius_ratio",
        }]
        evaluation = evaluate_drc(review)
        self.assertEqual(evaluation["status"], "review")
        self.assertEqual(
            evaluation["bracket_policy"],
            "advisory_until_profile_calibration",
        )

        blocked = payload(long_stitches=1)
        evaluation = evaluate_drc(blocked)
        self.assertEqual(evaluation["status"], "block")
        self.assertFalse(evaluation["export_allowed"])

        omitted_metric = payload(long_stitches=0)
        omitted_metric["operations"] = [
            {"kind": "stitch", "x": 0, "y": 0},
            {"kind": "stitch", "x": 9, "y": 0},
        ]
        evaluation = evaluate_drc(omitted_metric)
        self.assertEqual(evaluation["status"], "block")
        self.assertEqual(evaluation["evidence"]["long_stitches"], 1)
        self.assertAlmostEqual(
            evaluation["evidence"]["long_stitch_examples"][0]["length_mm"],
            9,
        )

    def test_training_records_are_group_split_and_rights_gated(self):
        first = deterministic_split("same-design-family")
        self.assertEqual(first, deterministic_split("same-design-family"))
        quarantined = record(payload())
        self.assertEqual(quarantined["lane"], "quarantine")
        self.assertFalse(
            quarantined["eligibility"]["drc_auxiliary_supervision"]
        )

        approved = record(payload(), {
            "training_use": "allowed",
            "license": "internal-approved",
            "review_status": "approved",
            "expert_plan_approved": True,
            "human_acceptance": "accepted",
            "split_group": "same-design-family",
        })
        self.assertTrue(approved["eligibility"]["semantic_supervision"])
        self.assertTrue(approved["eligibility"]["drc_auxiliary_supervision"])
        self.assertEqual(approved["split"], first)
        self.assertEqual(record_integrity_findings(approved), [])

    def test_blocking_accepted_target_requires_attributed_override(self):
        accepted = record(payload(long_stitches=2), {
            "training_use": "allowed",
            "license": "internal-approved",
            "review_status": "approved",
            "expert_plan_approved": True,
            "human_acceptance": "accepted",
        })
        findings = record_integrity_findings(accepted)
        self.assertTrue(any("blocking DRC" in item for item in findings))

        accepted["drc_override"] = {
            "approved_by": "reviewer-7",
            "reason": "measured profile exception",
        }
        self.assertEqual(record_integrity_findings(accepted), [])

    def test_regression_gate_detects_status_quality_and_rate_changes(self):
        baseline = record(payload())
        candidate = record(payload(
            long_stitches=1,
            quality_score=90,
            trims_per_1000_stitches=4.0,
        ))
        rules = {
            finding["rule"]
            for finding in regression_findings(baseline, candidate)
        }
        self.assertIn("drc.status_regression", rules)
        self.assertIn("drc.quality_score_regression", rules)
        self.assertIn("drc.trims_per_1000_stitches_regression", rules)

    def test_corpus_builder_emits_split_files_and_enforces_annotations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "run" / "sample"
            fixture.mkdir(parents=True)
            plan_path = fixture / "sample--plan.json"
            plan_path.write_text(json.dumps(payload()))
            source_hash = "b" * 64
            (fixture / "manifest.json").write_text(json.dumps({
                "source": "sample.png",
                "source_sha256": source_hash,
                "settings": {
                    "width_mm": 72,
                    "colors": 2,
                    "profile": "standard",
                    "density_multiplier": 1,
                },
                "outputs": {"plan": "sample/sample--plan.json"},
            }))
            output = root / "dataset"
            summary, failures = build(
                [root / "run"],
                output,
                {
                    "defaults": {},
                    "sources": {
                        source_hash: {
                            "training_use": "allowed",
                            "license": "internal-approved",
                            "review_status": "approved",
                            "expert_plan_approved": True,
                        }
                    },
                },
            )
            self.assertEqual(failures, [])
            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(summary["integrity_findings"], [])
            self.assertTrue((output / "records.jsonl").exists())
            self.assertTrue((output / "drc-regression.jsonl").exists())
            self.assertTrue((output / "train.jsonl").exists())
            self.assertTrue((output / "validation.jsonl").exists())
            self.assertTrue((output / "test.jsonl").exists())
            self.assertTrue((output / "quarantine.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

import unittest

from tools.run_sewout_learning_loop import (
    BASELINE_KNOBS,
    candidate_id,
    experiment_analysis,
    knob_activation,
    physical_score,
    select_candidates,
    virtual_proxy_score,
)


def policy(status="pass", hard=0, conditional=0, review=0):
    return {
        "status": status,
        "hard_blockers": (
            [{"rule": "machine.long_stitches", "count": hard}]
            if hard else []
        ),
        "conditional_blockers": [],
        "review_findings": [],
        "counts": {
            "hard_blockers": hard,
            "conditional_blockers": conditional,
            "review": review,
        },
        "evidence": {
            "trims_per_1000_stitches": 1,
            "jumps_per_1000_stitches": 2,
        },
    }


class SewoutLearningLoopTests(unittest.TestCase):
    def test_only_compiled_controls_are_activated(self):
        active, inactive = knob_activation({
            "document": {"objects": []},
            "regions": [
                {"stitch_type": "satin"},
                {"stitch_type": "running"},
            ]
        })
        self.assertEqual(active, ["satin_spacing_mm", "running_stitch_mm"])
        self.assertIn("density_multiplier", inactive)
        self.assertIn("pull_compensation_mm", inactive)
        self.assertIn("join_overlap_mm", inactive)

    def test_initial_candidates_hold_inactive_knobs_at_baseline(self):
        candidates, model = select_candidates(
            [], {}, 3, ["satin_spacing_mm"]
        )
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0], BASELINE_KNOBS)
        self.assertEqual(model["model"], "exploration_seed")
        for candidate in candidates:
            for name in (
                "density_multiplier", "pull_compensation_mm",
                "join_overlap_mm", "running_stitch_mm",
            ):
                self.assertEqual(candidate[name], BASELINE_KNOBS[name])

    def test_physical_feedback_overrides_virtual_proxy_target(self):
        self.assertEqual(physical_score({"overall_score": "87"}), 87)
        self.assertEqual(physical_score({
            "registration_1_5": "5",
            "coverage_1_5": "4",
            "readability_1_5": "5",
            "softness_1_5": "4",
            "thread_breaks": "1",
        }), 85)
        self.assertEqual(physical_score({
            "registration_1_5": "5",
            "coverage_1_5": "4",
            "readability_1_5": "5",
            "softness_1_5": "4",
            "color_accuracy_1_5": "5",
            "thread_breaks": "1",
        }), 87)
        self.assertIsNone(physical_score({}))
        self.assertGreater(
            virtual_proxy_score(policy()),
            virtual_proxy_score(policy("block", hard=2, review=10)),
        )

    def test_analysis_reports_blocked_and_duplicate_outcomes(self):
        first = {
            "trial_id": candidate_id(BASELINE_KNOBS),
            "knobs": dict(BASELINE_KNOBS),
            "drc": {
                **policy("block", hard=2),
                "conditional_blockers": [],
            },
            "metrics": {"stitches": 100, "trims": 2, "jumps": 3},
            "virtual_proxy_score": 40,
        }
        second = {
            **first,
            "trial_id": "different",
            "knobs": {**BASELINE_KNOBS, "satin_spacing_mm": 0.46},
        }
        analysis = experiment_analysis({
            "trials": [first, second],
            "active_knobs": ["satin_spacing_mm"],
            "inactive_knobs": {"density_multiplier": "not applicable"},
        }, {})
        self.assertEqual(analysis["trial_count"], 2)
        self.assertEqual(analysis["unique_compiled_outcomes"], 1)
        self.assertEqual(analysis["blocking_trial_count"], 2)
        self.assertEqual(analysis["physical_feedback_count"], 0)
        self.assertFalse(
            analysis["active_knob_effects"]["satin_spacing_mm"][
                "observed_effect"
            ]
        )
        self.assertEqual(analysis["recommended_sewout_candidates"], [])
        self.assertIn("resolve", analysis["next_action"])
        self.assertEqual(
            analysis["search_admission"]["status"],
            "structural_repair_required",
        )
        self.assertFalse(
            analysis["search_admission"][
                "continued_parameter_search_admitted"
            ]
        )
        self.assertIn(
            "geometry_repair",
            analysis["repair_classification"]["machine.long_stitches"][
                "classes"
            ],
        )


if __name__ == "__main__":
    unittest.main()

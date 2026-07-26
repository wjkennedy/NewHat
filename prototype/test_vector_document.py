import unittest

from vector_document import (
    BezierNode,
    EmbroideryLayer,
    VectorDocument,
    VectorObject,
    plan_document,
    satin_decision_brackets,
    satin_diagnostics,
    stitch_operations,
)


class VectorDocumentTest(unittest.TestCase):
    def setUp(self):
        self.rectangle = VectorObject("solid", "#153d7a", "tatami", [
            BezierNode(-10, -5), BezierNode(10, -5), BezierNode(10, 5), BezierNode(-10, 5),
        ])

    def test_document_round_trips_editable_bezier_nodes(self):
        document = VectorDocument(objects=[self.rectangle])
        restored = VectorDocument.from_json(document.json())
        restored.move_node("solid", 0, -12, -6)
        self.assertEqual((-12.0, -6.0), (restored.objects[0].nodes[0].x, restored.objects[0].nodes[0].y))

    def test_stitch_types_emit_semantic_operations(self):
        running = VectorObject("line", "#e85d48", "running", [BezierNode(-5, 0), BezierNode(5, 0)], closed=False)
        operations = stitch_operations(VectorDocument(objects=[self.rectangle, running]))
        self.assertIn("vector_tatami", {item.get("role") for item in operations})
        self.assertIn("vector_running", {item.get("role") for item in operations})
        self.assertTrue(any(item["kind"] == "color" for item in operations))

    def test_layers_preserve_compositing_order_and_report_overlap(self):
        top = VectorObject("top", "#e85d48", "tatami", [
            BezierNode(-5, -3), BezierNode(12, -3), BezierNode(12, 6), BezierNode(-5, 6),
        ], layer_id="top", blend_with_below=True)
        document = VectorDocument(objects=[top, self.rectangle], layers=[
            EmbroideryLayer("base", "Base", 0), EmbroideryLayer("top", "Top", 1),
        ])
        operations = stitch_operations(document)
        first_object = next(
            item["region_id"] for item in operations
            if item["kind"] == "stitch" and item["region_id"] != "foundation"
        )
        self.assertEqual("solid", first_object)
        relationship = document.composition()["overlaps"][0]
        self.assertEqual({"above": "top", "below": "solid"}, {key: relationship[key] for key in ("above", "below")})
        self.assertTrue(relationship["blend_candidate"])

    def test_satin_uses_two_resampled_rails_and_reports_scores(self):
        satin = VectorObject(
            "column", "#153d7a", "satin",
            [
                BezierNode(-10, -2), BezierNode(10, -2),
                BezierNode(10, 2), BezierNode(-10, 2),
            ],
            satin_rails=[
                [BezierNode(-10, -2), BezierNode(0, -2), BezierNode(10, -2)],
                [BezierNode(-10, 2), BezierNode(10, 2)],
            ],
            satin_spacing_mm=.5,
        )
        diagnostics = satin_diagnostics(satin)
        self.assertTrue(diagnostics.valid)
        self.assertTrue(diagnostics.resampled)
        self.assertAlmostEqual(4.0, diagnostics.mean_width_mm)
        roles = {item.get("role") for item in stitch_operations(VectorDocument(objects=[satin]))}
        self.assertTrue({
            "vector_satin_edge_underlay",
            "vector_satin_zigzag_underlay",
            "vector_satin",
        } <= roles)
        restored = VectorDocument.from_json(VectorDocument(objects=[satin]).json())
        self.assertEqual(2, len(restored.objects[0].satin_rails))

    def test_object_compensation_and_foundation_policy_round_trip(self):
        item = VectorObject(
            "script", "#b51338", "running",
            [BezierNode(-2, 0), BezierNode(2, 0)],
            closed=False,
            visual_role="script_stroke",
            pull_compensation_mm=.25,
            join_overlap_mm=.4,
        )
        restored = VectorDocument.from_json(VectorDocument(
            objects=[item], foundation_enabled=False,
        ).json())
        self.assertFalse(restored.foundation_enabled)
        self.assertEqual("script_stroke", restored.objects[0].visual_role)
        self.assertEqual(.25, restored.objects[0].pull_compensation_mm)
        self.assertEqual(.4, restored.objects[0].join_overlap_mm)

    def test_satin_score_rejects_columns_that_should_be_tatami(self):
        satin = VectorObject(
            "wide", "#153d7a", "satin",
            [
                BezierNode(-10, -5), BezierNode(10, -5),
                BezierNode(10, 5), BezierNode(-10, 5),
            ],
            satin_rails=[
                [BezierNode(-10, -5), BezierNode(10, -5)],
                [BezierNode(-10, 5), BezierNode(10, 5)],
            ],
        )
        diagnostics = satin_diagnostics(satin)
        self.assertFalse(diagnostics.valid)
        self.assertEqual(0, diagnostics.score)
        self.assertIn("wider", diagnostics.reason)

    def test_satin_decision_brackets_expose_object_and_interval_evidence(self):
        satin = VectorObject(
            "variable-column", "#153d7a", "satin",
            [BezierNode(-10, -2), BezierNode(10, -2)],
            closed=False,
            satin_rails=[
                [
                    BezierNode(-10, -2),
                    BezierNode(0, -2),
                    BezierNode(10, -4.5),
                ],
                [
                    BezierNode(-10, 2),
                    BezierNode(0, 2),
                    BezierNode(10, 4.5),
                ],
            ],
            satin_spacing_mm=.5,
        )
        brackets = satin_decision_brackets(satin)
        rules = {item.rule for item in brackets}
        self.assertTrue({
            "satin_mean_width",
            "satin_width_interval",
            "satin_width_deviation",
            "inside_curve_penetration_spacing",
            "satin_course_angle_change",
        } <= rules)
        intervals = [
            item for item in brackets
            if item.rule == "satin_width_interval"
        ]
        self.assertTrue(all(item.segment is not None for item in intervals))
        plan = plan_document(VectorDocument(
            objects=[satin], foundation_enabled=False,
        ))
        self.assertEqual(
            len(brackets),
            len([
                item for item in plan["brackets"]
                if item["object_id"] == "variable-column"
            ]),
        )
        self.assertIn(
            "decision_brackets",
            next(
                item for item in plan["diagnostics"]
                if item["id"] == "variable-column"
            ),
        )

    def test_tatami_boundary_list_preserves_a_hole(self):
        outer = [
            BezierNode(-10, -10), BezierNode(10, -10),
            BezierNode(10, 10), BezierNode(-10, 10),
        ]
        hole = [
            BezierNode(-3, -3), BezierNode(3, -3),
            BezierNode(3, 3), BezierNode(-3, 3),
        ]
        tatami = VectorObject(
            "ring", "#153d7a", "tatami", outer,
            boundaries=[outer, hole], fill_spacing_mm=1.0,
        )
        plan = plan_document(VectorDocument(objects=[tatami]))
        top = [
            item for item in plan["operations"]
            if item.get("role") == "vector_tatami" and item["kind"] == "stitch"
        ]
        self.assertTrue(top)
        self.assertFalse(any(
            -2.9 < item["x"] < 2.9 and -2.9 < item["y"] < 2.9
            for item in top
        ))
        self.assertEqual(
            2,
            next(item for item in plan["diagnostics"] if item["id"] == "ring")[
                "boundary_count"
            ],
        )

    def test_neighbor_graph_routes_same_color_inside_a_physical_layer(self):
        left = VectorObject(
            "left", "#153d7a", "running",
            [BezierNode(-10, 0), BezierNode(-6, 0)], closed=False,
            physical_layer=2,
        )
        right = VectorObject(
            "right", "#153d7a", "running",
            [BezierNode(-4, 0), BezierNode(0, 0)], closed=False,
            physical_layer=2,
        )
        plan = plan_document(VectorDocument(objects=[left, right]))
        self.assertEqual(["left", "right"], plan["sequence"])
        self.assertEqual(1, len(plan["neighbors"]))
        self.assertTrue(plan["neighbors"][0]["same_color"])

    def test_design_foundation_breaks_at_gaps_between_objects(self):
        left = VectorObject(
            "left", "#153d7a", "tatami",
            [
                BezierNode(-10, -2), BezierNode(-6, -2),
                BezierNode(-6, 2), BezierNode(-10, 2),
            ],
        )
        right = VectorObject(
            "right", "#153d7a", "tatami",
            [
                BezierNode(6, -2), BezierNode(10, -2),
                BezierNode(10, 2), BezierNode(6, 2),
            ],
        )
        foundation = [
            item for item in plan_document(
                VectorDocument(objects=[left, right])
            )["operations"]
            if item.get("role") == "design_center_underlay"
            and item["kind"] == "stitch"
        ]
        self.assertTrue(foundation)
        self.assertFalse(any(-5.9 < item["x"] < 5.9 for item in foundation))

    def test_satin_primitives_share_covered_underpath(self):
        satin = VectorObject(
            "column", "#153d7a", "satin",
            [
                BezierNode(-10, -2), BezierNode(10, -2),
                BezierNode(10, 2), BezierNode(-10, 2),
            ],
            satin_rails=[
                [BezierNode(-10, -2), BezierNode(10, -2)],
                [BezierNode(-10, 2), BezierNode(10, 2)],
            ],
            satin_spacing_mm=.5,
        )
        plan = plan_document(VectorDocument(
            objects=[satin], foundation_enabled=False,
        ))
        self.assertEqual(
            1,
            sum(item["kind"] == "jump" for item in plan["operations"]),
        )
        self.assertGreater(
            plan["thread_economy"]["covered_underpath_connector_count"],
            0,
        )
        self.assertEqual(
            1.0,
            plan["thread_economy"]["needle_down_movement_ratio"],
        )
        self.assertTrue(any(
            finding["rule"] == "covered_underpath_routing"
            for finding in plan["findings"]
        ))

    def test_route_can_reverse_a_path_to_reduce_entry_travel(self):
        first = VectorObject(
            "first", "#153d7a", "running",
            [BezierNode(-10, 0), BezierNode(-5, 0)], closed=False,
        )
        authored_backwards = VectorObject(
            "second", "#153d7a", "running",
            [BezierNode(0, 0), BezierNode(-4, 0)], closed=False,
        )
        plan = plan_document(VectorDocument(
            objects=[first, authored_backwards],
            foundation_enabled=False,
        ))
        self.assertEqual(["first", "second"], plan["sequence"])
        self.assertTrue(plan["orientations"]["second"])

    def test_plan_exposes_line_column_and_fill_constructs(self):
        running = VectorObject(
            "line", "#153d7a", "running",
            [BezierNode(-5, 8), BezierNode(5, 8)], closed=False,
        )
        satin = VectorObject(
            "column", "#153d7a", "satin",
            [BezierNode(-5, -2), BezierNode(5, -2)],
            closed=False,
            satin_rails=[
                [BezierNode(-5, -2), BezierNode(5, -2)],
                [BezierNode(-5, 2), BezierNode(5, 2)],
            ],
        )
        plan = plan_document(VectorDocument(
            objects=[running, satin, self.rectangle],
            foundation_enabled=False,
        ))
        constructs = {
            item["object_id"]: item for item in plan["constructs"]
        }
        self.assertEqual("line_path", constructs["line"]["construct"])
        self.assertEqual(
            "satin_column", constructs["column"]["construct"]
        )
        self.assertEqual("fill_area", constructs["solid"]["construct"])
        self.assertGreater(
            constructs["solid"]["top_course_count"], 0
        )
        self.assertGreater(
            constructs["column"]["rail_pair_count"], 0
        )

    def test_color_depth_preserves_physical_order_and_thread_change(self):
        upper = VectorObject(
            "upper", "#e85d48", "tatami",
            [
                BezierNode(-5, -3), BezierNode(8, -3),
                BezierNode(8, 4), BezierNode(-5, 4),
            ],
            layer_id="upper",
            blend_with_below=True,
        )
        document = VectorDocument(
            objects=[upper, self.rectangle],
            layers=[
                EmbroideryLayer("base", "Base", 0),
                EmbroideryLayer("upper", "Upper", 1),
            ],
            foundation_enabled=False,
        )
        plan = plan_document(document)
        self.assertEqual(["solid", "upper"], plan["sequence"])
        self.assertEqual(
            1,
            sum(item["kind"] == "color" for item in plan["operations"]),
        )
        self.assertEqual(
            2,
            sum(
                item["construct"] == "fill_area"
                for item in plan["constructs"]
            ),
        )


if __name__ == "__main__":
    unittest.main()

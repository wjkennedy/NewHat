import unittest

from vector_document import BezierNode, EmbroideryLayer, VectorDocument, VectorObject, stitch_operations


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
        first_object = next(item["region_id"] for item in operations if item["kind"] == "stitch")
        self.assertEqual("solid", first_object)
        relationship = document.composition()["overlaps"][0]
        self.assertEqual({"above": "top", "below": "solid"}, {key: relationship[key] for key in ("above", "below")})
        self.assertTrue(relationship["blend_candidate"])


if __name__ == "__main__":
    unittest.main()

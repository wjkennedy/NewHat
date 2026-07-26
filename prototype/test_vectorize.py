from io import BytesIO
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from vectorize import vectorize_posterized
from vector_document import plan_document, satin_diagnostics


class PosterizedVectorizeTest(unittest.TestCase):
    def test_touching_colours_are_one_geometry_not_two_colour_objects(self):
        image = Image.new("RGBA", (160, 60), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 25, 75, 32), fill="#e85d48")
        draw.rectangle((75, 25, 150, 32), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")
        document = vectorize_posterized(stream.getvalue(), width_mm=72)
        self.assertEqual(1, len(document.objects))
        self.assertEqual("satin", document.objects[0].stitch_type)
        self.assertEqual(2, len(document.objects[0].satin_rails))
        self.assertEqual("element-1", document.objects[0].element_id)

    def test_irregular_foreground_becomes_tatami_and_transparency_is_removed(self):
        image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.polygon(((15, 15), (85, 25), (70, 85), (30, 70)), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")
        document = vectorize_posterized(stream.getvalue(), width_mm=72)
        self.assertEqual(1, len(document.objects))
        self.assertEqual("tatami", document.objects[0].stitch_type)
        self.assertEqual(1, len(document.objects[0].boundaries))

    def test_bob_evans_script_mark_proposes_editable_curved_satin(self):
        image = Path(__file__).resolve().parent.parent / "bobevans.jpg"
        document = vectorize_posterized(image.read_bytes(), width_mm=110)
        satin = [
            item for item in document.objects if item.stitch_type == "satin"
        ]
        registered = [
            item for item in document.objects
            if item.visual_role == "registered_mark"
        ]
        self.assertAlmostEqual(110.0, document.width_mm)
        self.assertAlmostEqual(21.5, document.height_mm, delta=0.2)
        self.assertFalse(document.foundation_enabled)
        self.assertGreaterEqual(len(satin), 20)
        self.assertLessEqual(len(document.objects), 45)
        self.assertFalse(any(
            item.stitch_type == "tatami" for item in document.objects
        ))
        self.assertTrue(registered)
        self.assertTrue(all(
            item.stitch_type == "running" and not item.underlay_enabled
            for item in registered
        ))
        self.assertTrue(all(
            item.pull_compensation_mm == 0.25
            and item.join_overlap_mm == 0.4
            and satin_diagnostics(item).valid
            for item in satin
        ))
        curved = 0
        for item in satin:
            rail = item.satin_rails[0]
            if (
                max(node.x for node in rail) - min(node.x for node in rail) > 1
                and max(node.y for node in rail) - min(node.y for node in rail) > 1
            ):
                curved += 1
        self.assertGreaterEqual(curved, 8)
        plan = plan_document(document)
        self.assertFalse(any(
            finding["severity"] == "blocker"
            for finding in plan["findings"]
        ))
        self.assertTrue(any(
            finding["rule"] in {"satin_width", "registered_mark_size"}
            for finding in plan["findings"]
        ))
        self.assertFalse(any(
            operation.get("role") == "design_center_underlay"
            for operation in plan["operations"]
        ))
        for item in satin:
            object_operations = [
                operation for operation in plan["operations"]
                if operation.get("region_id") == item.id
            ]
            top = next(
                index for index, operation in enumerate(object_operations)
                if operation.get("role") == "vector_satin"
            )
            underlay = [
                index for index, operation in enumerate(object_operations)
                if "underlay" in operation.get("role", "")
            ]
            self.assertTrue(underlay)
            self.assertLess(max(underlay), top)


if __name__ == "__main__":
    unittest.main()

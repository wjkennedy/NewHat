from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

from digitizer import Operation, _enforce_stitch_criteria, _satin_rails, digitize
from server import as_dst, inspect_dst
from tools.render_stitch_preview import render


class DigitizerTest(unittest.TestCase):
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
        self.assertTrue(all(op.region_id == "foundation" for op in plan.foundation_operations))

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

        self.assertEqual(2, len(plan.regions))
        self.assertTrue({"tie_in", "edge_underlay", "fill_underlay", "top_fill", "tie_off", "trim"} <= roles)
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
        self.assertEqual(2, len([region for region in plan.regions if region.color == red]))
        self.assertEqual(1, len([operation for operation in plan.operations if operation.kind == "color"]))
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
        self.assertEqual(1, len(plan.regions))
        self.assertEqual(2, len(plan.regions[0].contours))
        # Contours are semantic geometry and are preserved in the JSON plan.
        self.assertEqual(2, len(plan.json()["regions"][0]["contours"]))

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
        draw.rectangle((15, 55, 155, 57), fill="#d22d54")  # about 1 mm wide: detail running
        stream = BytesIO()
        image.save(stream, format="PNG")

        plan = digitize(stream.getvalue(), width_mm=72, colors=2)
        types = {region.stitch_type for region in plan.regions}
        roles = {operation.role for operation in plan.operations}
        self.assertTrue({"satin", "running"} <= types)
        self.assertTrue({"satin_top", "satin_center_underlay", "detail_running"} <= roles)

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


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from posterize import (
    analyze_pass_filters,
    detail_foreground_mask,
    otsu_foreground_mask,
    otsu_threshold,
    quantize_foreground,
)


class PosterizeTest(unittest.TestCase):
    def test_otsu_separates_bimodal_samples(self):
        threshold = otsu_threshold([18] * 50 + [224] * 50)
        self.assertGreaterEqual(threshold, 18)
        self.assertLess(threshold, 224)

    def test_opaque_light_border_is_removed_without_color_rule(self):
        image = Image.new("RGB", (40, 30), (230, 220, 205))
        ImageDraw.Draw(image).rectangle((10, 8, 30, 22), fill=(40, 80, 120))
        mask, report = otsu_foreground_mask(image)
        self.assertIn((20, 15), mask)
        self.assertNotIn((0, 0), mask)
        self.assertEqual("otsu_luminance", report["method"])

    def test_transparency_is_the_geometry_contract(self):
        image = Image.new("RGBA", (30, 20), (245, 245, 245, 0))
        ImageDraw.Draw(image).rectangle((5, 4, 25, 16), fill=(245, 245, 245, 255))
        mask, report = otsu_foreground_mask(image)
        self.assertIn((10, 10), mask)
        self.assertNotIn((0, 0), mask)
        self.assertEqual("otsu_alpha", report["method"])

    def test_detail_scan_is_independent_of_palette_assignment(self):
        image = Image.new("RGBA", (80, 50), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 72, 42), fill=(40, 80, 120, 255))
        draw.line((15, 25, 65, 25), fill=(235, 235, 220, 255), width=3)
        foreground, _ = otsu_foreground_mask(image)
        detail, report = detail_foreground_mask(image, foreground)
        self.assertTrue(detail)
        self.assertEqual("multiscale_color_edge_otsu", report["method"])
        self.assertIn("difference_of_gaussians", report["filters"])

    def test_pass_filters_reject_flat_fill_detail_but_keep_support(self):
        image = Image.new("RGBA", (80, 50), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle(
            (8, 8, 72, 42), fill=(40, 80, 120, 255)
        )
        foreground, _ = otsu_foreground_mask(image)
        _, assigned, _ = quantize_foreground(image, foreground, 4)
        report, masks = analyze_pass_filters(
            image,
            foreground,
            assigned,
            pixel_scale=0.2,
            pixel_area_mm2=0.04,
        )
        self.assertTrue(report["passes"]["underlay"]["eligible"])
        self.assertTrue(report["passes"]["blocking"]["eligible"])
        self.assertFalse(report["passes"]["detail"]["eligible"])
        self.assertTrue(masks["underlay"])
        self.assertFalse(masks["detail"])

    def test_pass_filters_admit_internal_multicolor_detail(self):
        image = Image.new("RGBA", (80, 50), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 72, 42), fill=(40, 80, 120, 255))
        draw.line((15, 25, 65, 25), fill=(235, 235, 220, 255), width=3)
        foreground, _ = otsu_foreground_mask(image)
        _, assigned, _ = quantize_foreground(image, foreground, 4)
        report, masks = analyze_pass_filters(
            image,
            foreground,
            assigned,
            pixel_scale=0.2,
            pixel_area_mm2=0.04,
        )
        self.assertEqual(2, report["features"]["color_complexity"])
        self.assertTrue(report["passes"]["detail"]["eligible"])
        self.assertTrue(masks["detail"])

    def test_jpeg_shades_collapse_to_one_semantic_thread_color(self):
        image = Image.open(
            Path(__file__).resolve().parent.parent / "bobevans.jpg"
        ).convert("RGBA")
        foreground, _ = otsu_foreground_mask(image)
        palette, assigned, selected = quantize_foreground(
            image, foreground, 8
        )
        self.assertEqual(1, len(selected))
        self.assertEqual(1, len(set(assigned.values())))
        red, green, blue = palette[selected[0]]
        self.assertGreater(red, green * 4)
        self.assertGreater(red, blue * 2)

    def test_script_jpeg_separation_brackets_span_literal_to_one_thread(self):
        image = Image.open(
            Path(__file__).resolve().parent.parent / "bobevans.jpg"
        ).convert("RGBA")
        foreground, _ = otsu_foreground_mask(image)
        brackets = (
            (0, 0),
            (4, 0),
            (8, 0),
            (20, 0),
            (8, .20),
        )
        counts = [
            len(quantize_foreground(
                image,
                foreground,
                8,
                merge_tolerance=tolerance,
                fragmented_merge_threshold=fragmented,
            )[2])
            for tolerance, fragmented in brackets
        ]
        self.assertEqual([8, 7, 4, 2, 1], counts)

    def test_coherent_same_hue_regions_remain_separate_colors(self):
        image = Image.new("RGB", (80, 30), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 5, 34, 24), fill="#b81438")
        draw.rectangle((45, 5, 74, 24), fill="#7d0f2a")
        foreground = {
            (x, y)
            for y in range(5, 25)
            for x in (*range(5, 35), *range(45, 75))
        }
        _, assigned, selected = quantize_foreground(
            image, foreground, 4
        )
        self.assertEqual(2, len(selected))
        self.assertEqual(2, len(set(assigned.values())))

    def test_near_identical_antialias_colors_merge_even_when_coherent(self):
        image = Image.new("RGB", (80, 30), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 5, 34, 24), fill="#b81438")
        draw.rectangle((45, 5, 74, 24), fill="#b71439")
        foreground = {
            (x, y)
            for y in range(5, 25)
            for x in (*range(5, 35), *range(45, 75))
        }
        _, assigned, selected = quantize_foreground(
            image, foreground, 4
        )
        self.assertEqual(1, len(selected))
        self.assertEqual(1, len(set(assigned.values())))

    def test_color_separation_tolerance_brackets_related_thread_shades(self):
        image = Image.new("RGB", (96, 30), "white")
        draw = ImageDraw.Draw(image)
        colors = ("#b81438", "#c21c42", "#ae1034")
        foreground = set()
        for index, color in enumerate(colors):
            left = 5 + index * 30
            draw.rectangle((left, 5, left + 24, 24), fill=color)
            foreground.update(
                (x, y)
                for y in range(5, 25)
                for x in range(left, left + 25)
            )
        strict_report = {}
        _, strict_assignment, strict = quantize_foreground(
            image,
            foreground,
            8,
            merge_tolerance=0,
            fragmented_merge_threshold=0,
            diagnostics=strict_report,
        )
        consolidated_report = {}
        _, consolidated_assignment, consolidated = quantize_foreground(
            image,
            foreground,
            8,
            merge_tolerance=32,
            fragmented_merge_threshold=0,
            diagnostics=consolidated_report,
        )
        self.assertEqual(3, len(strict))
        self.assertEqual(3, len(set(strict_assignment.values())))
        self.assertEqual(1, len(consolidated))
        self.assertEqual(1, len(set(consolidated_assignment.values())))
        self.assertEqual(2, consolidated_report["merged_shade_count"])
        self.assertGreater(
            consolidated_report["mean_color_error_rgb"],
            strict_report["mean_color_error_rgb"],
        )


if __name__ == "__main__":
    unittest.main()

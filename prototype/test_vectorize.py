from io import BytesIO
import unittest

from PIL import Image, ImageDraw

from vectorize import vectorize_posterized


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

    def test_irregular_foreground_becomes_tatami_and_transparency_is_removed(self):
        image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.polygon(((15, 15), (85, 25), (70, 85), (30, 70)), fill="#153d7a")
        stream = BytesIO()
        image.save(stream, format="PNG")
        document = vectorize_posterized(stream.getvalue(), width_mm=72)
        self.assertEqual(1, len(document.objects))
        self.assertEqual("tatami", document.objects[0].stitch_type)


if __name__ == "__main__":
    unittest.main()

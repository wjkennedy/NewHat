"""Colour-agnostic posterized-raster to editable vector objects."""
from __future__ import annotations

from io import BytesIO

from PIL import Image

from digitizer import _components, _contours, _simplify_loop
from vector_document import BezierNode, VectorDocument, VectorObject


def vectorize_posterized(image_bytes: bytes, *, width_mm: float = 72.0) -> VectorDocument:
    """Vectorize an already background-removed raster without colour grouping.

    Alpha is the foreground contract: all non-transparent pixels participate in
    one mask, so touching posterized colours form one composed element. This
    deliberately avoids treating palette entries as separate geometry.
    """
    image = Image.open(BytesIO(image_bytes)).convert("RGBA")
    width, height = image.size
    alpha = image.getchannel("A").load()
    mask = {(x, y) for y in range(height) for x in range(width) if alpha[x, y] > 40}
    height_mm = width_mm * height / width
    objects: list[VectorObject] = []
    for index, component in enumerate(_components(mask), 1):
        contours = _contours(component)
        if not contours:
            continue
        contour = max(contours, key=len)
        contour = _simplify_loop(contour)
        min_x, max_x = min(x for x, _ in component), max(x for x, _ in component)
        min_y, max_y = min(y for _, y in component), max(y for _, y in component)
        span_x, span_y = (max_x - min_x + 1) * width_mm / width, (max_y - min_y + 1) * height_mm / height
        narrow, long = min(span_x, span_y), max(span_x, span_y)
        stitch_type = "satin" if narrow >= 1.0 and long / max(narrow, .01) >= 2.0 and narrow <= 8.0 else "tatami"
        nodes = [BezierNode((x / max(1, width - 1) - .5) * width_mm,
                            (y / max(1, height - 1) - .5) * height_mm) for x, y in contour]
        objects.append(VectorObject(f"element-{index}", "#202020", stitch_type, nodes))
    return VectorDocument(width_mm, height_mm, objects)

"""Editable vector embroidery primitives and deterministic stitch planning.

This module is deliberately independent of the raster autopuncher.  It gives
the editor a lossless document model: users edit anchors and Bezier handles,
then regenerate stitches from those objects at any size or profile.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil, hypot
from typing import Literal


StitchType = Literal["running", "satin", "tatami"]


@dataclass
class BezierNode:
    x: float
    y: float
    in_x: float = 0.0
    in_y: float = 0.0
    out_x: float = 0.0
    out_y: float = 0.0


@dataclass
class VectorObject:
    id: str
    color: str
    stitch_type: StitchType
    nodes: list[BezierNode]
    closed: bool = True
    enabled: bool = True
    fill_spacing_mm: float = 0.45
    stitch_length_mm: float = 4.5
    layer_id: str = "base"
    blend_with_below: bool = False

    def flattened(self, segments: int = 8) -> list[tuple[float, float]]:
        """Flatten cubic segments while retaining editable control handles."""
        if len(self.nodes) < 2:
            return [(node.x, node.y) for node in self.nodes]
        count = len(self.nodes) if self.closed else len(self.nodes) - 1
        points: list[tuple[float, float]] = []
        for index in range(count):
            start = self.nodes[index]
            end = self.nodes[(index + 1) % len(self.nodes)]
            for step in range(segments):
                t = step / segments
                u = 1 - t
                points.append((
                    u ** 3 * start.x + 3 * u ** 2 * t * (start.x + start.out_x)
                    + 3 * u * t ** 2 * (end.x + end.in_x) + t ** 3 * end.x,
                    u ** 3 * start.y + 3 * u ** 2 * t * (start.y + start.out_y)
                    + 3 * u * t ** 2 * (end.y + end.in_y) + t ** 3 * end.y,
                ))
        points.append((self.nodes[-1].x, self.nodes[-1].y))
        return points


@dataclass
class EmbroideryLayer:
    id: str
    name: str
    order: int
    enabled: bool = True
    density_multiplier: float = 1.0


@dataclass
class VectorDocument:
    width_mm: float = 72.0
    height_mm: float = 55.0
    objects: list[VectorObject] = field(default_factory=list)
    layers: list[EmbroideryLayer] = field(default_factory=lambda: [EmbroideryLayer("base", "Base", 0)])

    @classmethod
    def from_json(cls, payload: dict) -> "VectorDocument":
        return cls(
            float(payload.get("width_mm", 72)), float(payload.get("height_mm", 55)),
            [VectorObject(
                id=item["id"], color=item["color"], stitch_type=item["stitch_type"],
                nodes=[BezierNode(**node) for node in item["nodes"]],
                closed=bool(item.get("closed", True)), enabled=bool(item.get("enabled", True)),
                fill_spacing_mm=float(item.get("fill_spacing_mm", .45)),
                stitch_length_mm=float(item.get("stitch_length_mm", 4.5)),
                layer_id=item.get("layer_id", "base"),
                blend_with_below=bool(item.get("blend_with_below", False)),
            ) for item in payload.get("objects", [])],
            [EmbroideryLayer(**layer) for layer in payload.get("layers", [{"id": "base", "name": "Base", "order": 0}])],
        )

    def json(self) -> dict:
        return {"width_mm": self.width_mm, "height_mm": self.height_mm,
                "layers": [asdict(layer) for layer in self.layers],
                "objects": [{**asdict(item), "nodes": [asdict(node) for node in item.nodes]} for item in self.objects]}

    def object(self, object_id: str) -> VectorObject:
        return next(item for item in self.objects if item.id == object_id)

    def move_node(self, object_id: str, node_index: int, x: float, y: float) -> None:
        node = self.object(object_id).nodes[node_index]
        node.x, node.y = float(x), float(y)

    def ordered_layers(self) -> list[EmbroideryLayer]:
        return sorted((layer for layer in self.layers if layer.enabled), key=lambda layer: layer.order)

    def composition(self) -> dict:
        """Return overlap relationships used by UI density/blend decisions.

        Bounding-box overlap is intentionally conservative: it is a fast
        preflight cue, not a claim of exact geometric intersection.
        """
        ordered = self.ordered_layers()
        rank = {layer.id: index for index, layer in enumerate(ordered)}
        boxes = {}
        for item in self.objects:
            points = item.flattened()
            if points:
                boxes[item.id] = (min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points))
        overlaps = []
        for above in self.objects:
            for below in self.objects:
                if above.id == below.id or rank.get(above.layer_id, -1) <= rank.get(below.layer_id, -1):
                    continue
                ax1, ay1, ax2, ay2 = boxes.get(above.id, (0, 0, 0, 0))
                bx1, by1, bx2, by2 = boxes.get(below.id, (0, 0, 0, 0))
                width, height = max(0, min(ax2, bx2) - max(ax1, bx1)), max(0, min(ay2, by2) - max(ay1, by1))
                if width and height:
                    overlaps.append({"above": above.id, "below": below.id, "area_mm2": width * height,
                                     "same_color": above.color == below.color,
                                     "blend_candidate": above.blend_with_below})
        return {"layers": [{**asdict(layer), "object_count": sum(item.layer_id == layer.id for item in self.objects)} for layer in ordered],
                "overlaps": overlaps}


def _length(left: tuple[float, float], right: tuple[float, float]) -> float:
    return hypot(right[0] - left[0], right[1] - left[1])


def _emit_path(operations: list[dict], points: list[tuple[float, float]], *, role: str, object_id: str, maximum: float) -> None:
    if len(points) < 2:
        return
    operations.append({"kind": "jump", "x": points[0][0], "y": points[0][1], "role": role, "region_id": object_id})
    operations.append({"kind": "stitch", "x": points[0][0], "y": points[0][1], "role": role, "region_id": object_id})
    for start, end in zip(points, points[1:]):
        pieces = max(1, ceil(_length(start, end) / maximum))
        for index in range(1, pieces + 1):
            t = index / pieces
            operations.append({"kind": "stitch", "x": start[0] + (end[0] - start[0]) * t,
                               "y": start[1] + (end[1] - start[1]) * t,
                               "role": role, "region_id": object_id})


def _scanline(points: list[tuple[float, float]], y: float) -> list[float]:
    hits = []
    for start, end in zip(points, points[1:] + points[:1]):
        if (start[1] <= y < end[1]) or (end[1] <= y < start[1]):
            hits.append(start[0] + (y - start[1]) * (end[0] - start[0]) / (end[1] - start[1]))
    return sorted(hits)


def stitch_operations(document: VectorDocument) -> list[dict]:
    """Generate stitches in compositing-layer order, preserving overlaps."""
    result: list[dict] = []
    active_color = None
    for layer in document.ordered_layers():
        for item in (item for item in document.objects if item.enabled and item.layer_id == layer.id):
            if active_color is not None and item.color != active_color:
                result.append({"kind": "color", "role": "color_change"})
            active_color = item.color
            path = item.flattened()
            if item.stitch_type == "running":
                _emit_path(result, path, role="vector_running", object_id=item.id, maximum=min(7.0, item.stitch_length_mm))
                continue
            if not item.closed or len(path) < 4:
                raise ValueError(f"{item.stitch_type} object {item.id} must be a closed path")
            min_y, max_y = min(y for _, y in path), max(y for _, y in path)
            spacing = (item.stitch_length_mm if item.stitch_type == "satin" else item.fill_spacing_mm) * layer.density_multiplier
            row = min_y + spacing / 2
            row_number = 0
            while row < max_y:
                for left, right in zip(_scanline(path, row)[::2], _scanline(path, row)[1::2]):
                    line = [(left, row), (right, row)]
                    if row_number % 2:
                        line.reverse()
                    _emit_path(result, line, role=f"vector_{item.stitch_type}", object_id=item.id,
                               maximum=item.stitch_length_mm)
                row_number += 1
                row += spacing
    return result

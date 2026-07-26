"""Editable vector embroidery primitives and deterministic stitch planning.

The document keeps authored geometry, physical embroidery layers, thread
colour, and machine order as separate concepts. Satin columns use two rails;
tatami areas use one or more closed boundary loops; running stitches use a
centreline. Machine operations are derived and can always be regenerated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import product
from math import acos, ceil, cos, degrees, hypot, radians, sin
from statistics import pstdev
from typing import Literal


StitchType = Literal["running", "satin", "tatami"]
Point = tuple[float, float]


@dataclass
class BezierNode:
    x: float
    y: float
    in_x: float = 0.0
    in_y: float = 0.0
    out_x: float = 0.0
    out_y: float = 0.0


def _flatten_nodes(
    nodes: list[BezierNode], *, closed: bool, segments: int = 8
) -> list[Point]:
    """Flatten editable cubic segments without discarding their source nodes."""
    if len(nodes) < 2:
        return [(node.x, node.y) for node in nodes]
    count = len(nodes) if closed else len(nodes) - 1
    points: list[Point] = []
    for index in range(count):
        start = nodes[index]
        end = nodes[(index + 1) % len(nodes)]
        for step in range(segments):
            t = step / segments
            u = 1 - t
            points.append((
                u ** 3 * start.x
                + 3 * u ** 2 * t * (start.x + start.out_x)
                + 3 * u * t ** 2 * (end.x + end.in_x)
                + t ** 3 * end.x,
                u ** 3 * start.y
                + 3 * u ** 2 * t * (start.y + start.out_y)
                + 3 * u * t ** 2 * (end.y + end.in_y)
                + t ** 3 * end.y,
            ))
    points.append(
        (nodes[0].x, nodes[0].y) if closed else (nodes[-1].x, nodes[-1].y)
    )
    return points


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
    # Tatami loops. The first is normally the exterior; subsequent loops can
    # be holes or islands and are resolved with even/odd parity.
    boundaries: list[list[BezierNode]] = field(default_factory=list)
    # Satin rails are open Bezier paths. They remain independently editable;
    # the closed perimeter and paired stitch positions are derived.
    satin_rails: list[list[BezierNode]] = field(default_factory=list)
    satin_spacing_mm: float = 0.40
    fill_angle_degrees: float = 45.0
    physical_layer: int | None = None
    sequence_order: int | None = None
    lock_sequence: bool = False
    element_id: str | None = None
    underlay_enabled: bool = True
    visual_role: str = "shape"
    pull_compensation_mm: float = 0.0
    join_overlap_mm: float = 0.0

    def flattened(self, segments: int = 8) -> list[Point]:
        return _flatten_nodes(self.nodes, closed=self.closed, segments=segments)

    def boundary_paths(self, segments: int = 8) -> list[list[Point]]:
        sources = self.boundaries or ([self.nodes] if self.closed else [])
        return [
            _flatten_nodes(nodes, closed=True, segments=segments)
            for nodes in sources
            if len(nodes) >= 3
        ]

    def rail_paths(self, segments: int = 8) -> list[list[Point]]:
        return [
            _flatten_nodes(nodes, closed=False, segments=segments)
            for nodes in self.satin_rails[:2]
            if len(nodes) >= 2
        ]


@dataclass
class EmbroideryLayer:
    id: str
    name: str
    order: int
    enabled: bool = True
    density_multiplier: float = 1.0
    physical_order: int | None = None


@dataclass
class SatinDiagnostics:
    valid: bool
    score: int
    width_score: int
    deviation_score: int
    shortening_score: int
    pair_count: int
    mean_width_mm: float | None
    width_deviation_mm: float | None
    shortening_deviation_mm: float | None
    resampled: bool = False
    reason: str = ""


@dataclass
class DecisionBracket:
    """Inspectable evidence for one automatic embroidery decision."""

    rule: str
    object_id: str
    observed: float | dict | None
    unit: str
    bracket: dict
    status: str
    message: str
    decision: str
    segment: dict | None = None
    evidence: dict = field(default_factory=dict)
    fallback: str | None = None
    operator_controls: list[str] = field(default_factory=list)

    def json(self) -> dict:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, {}, [])
        }


@dataclass
class SewPrimitive:
    """A reversible, thread-bearing path within one embroidery object.

    Primitives are the bridge between editable geometry and machine commands.
    They let the planner choose direction and join consecutive paths through
    coverage that will hide the connecting run.
    """

    role: str
    points: list[Point]
    maximum_stitch_mm: float
    reversible: bool = True


@dataclass
class VectorDocument:
    width_mm: float = 72.0
    height_mm: float = 55.0
    objects: list[VectorObject] = field(default_factory=list)
    layers: list[EmbroideryLayer] = field(
        default_factory=lambda: [EmbroideryLayer("base", "Base", 0)]
    )
    neighbor_distance_mm: float = 12.0
    connector_max_mm: float = 7.0
    center_outward: bool = False
    foundation_enabled: bool = True

    @classmethod
    def from_json(cls, payload: dict) -> "VectorDocument":
        objects = []
        for item in payload.get("objects", []):
            objects.append(VectorObject(
                id=item["id"],
                color=item["color"],
                stitch_type=item["stitch_type"],
                nodes=[BezierNode(**node) for node in item.get("nodes", [])],
                closed=bool(item.get("closed", True)),
                enabled=bool(item.get("enabled", True)),
                fill_spacing_mm=float(item.get("fill_spacing_mm", .45)),
                stitch_length_mm=float(item.get("stitch_length_mm", 4.5)),
                layer_id=item.get("layer_id", "base"),
                blend_with_below=bool(item.get("blend_with_below", False)),
                boundaries=[
                    [BezierNode(**node) for node in boundary]
                    for boundary in item.get("boundaries", [])
                ],
                satin_rails=[
                    [BezierNode(**node) for node in rail]
                    for rail in item.get("satin_rails", [])
                ],
                satin_spacing_mm=float(item.get("satin_spacing_mm", .40)),
                fill_angle_degrees=float(item.get("fill_angle_degrees", 45)),
                physical_layer=item.get("physical_layer"),
                sequence_order=item.get("sequence_order"),
                lock_sequence=bool(item.get("lock_sequence", False)),
                element_id=item.get("element_id"),
                underlay_enabled=bool(item.get("underlay_enabled", True)),
                visual_role=str(item.get("visual_role", "shape")),
                pull_compensation_mm=float(
                    item.get("pull_compensation_mm", 0.0)
                ),
                join_overlap_mm=float(item.get("join_overlap_mm", 0.0)),
            ))
        return cls(
            width_mm=float(payload.get("width_mm", 72)),
            height_mm=float(payload.get("height_mm", 55)),
            objects=objects,
            layers=[
                EmbroideryLayer(**layer)
                for layer in payload.get(
                    "layers", [{"id": "base", "name": "Base", "order": 0}]
                )
            ],
            neighbor_distance_mm=float(payload.get("neighbor_distance_mm", 12)),
            connector_max_mm=float(payload.get("connector_max_mm", 7)),
            center_outward=bool(payload.get("center_outward", False)),
            foundation_enabled=bool(payload.get("foundation_enabled", True)),
        )

    def json(self) -> dict:
        return {
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "neighbor_distance_mm": self.neighbor_distance_mm,
            "connector_max_mm": self.connector_max_mm,
            "center_outward": self.center_outward,
            "foundation_enabled": self.foundation_enabled,
            "layers": [asdict(layer) for layer in self.layers],
            "objects": [asdict(item) for item in self.objects],
        }

    def object(self, object_id: str) -> VectorObject:
        return next(item for item in self.objects if item.id == object_id)

    def move_node(
        self, object_id: str, node_index: int, x: float, y: float
    ) -> None:
        node = self.object(object_id).nodes[node_index]
        node.x, node.y = float(x), float(y)

    def ordered_layers(self) -> list[EmbroideryLayer]:
        return sorted(
            (layer for layer in self.layers if layer.enabled),
            key=lambda layer: (
                layer.physical_order
                if layer.physical_order is not None
                else layer.order,
                layer.order,
            ),
        )

    def composition(self) -> dict:
        """Return conservative overlap and physical-layer relationships."""
        ordered = self.ordered_layers()
        rank = {layer.id: index for index, layer in enumerate(ordered)}
        boxes: dict[str, tuple[float, float, float, float]] = {}
        for item in self.objects:
            points = [
                point
                for boundary in item.boundary_paths()
                for point in boundary
            ] or item.flattened()
            if points:
                boxes[item.id] = (
                    min(x for x, _ in points),
                    min(y for _, y in points),
                    max(x for x, _ in points),
                    max(y for _, y in points),
                )
        overlaps = []
        for above in self.objects:
            for below in self.objects:
                if (
                    above.id == below.id
                    or rank.get(above.layer_id, -1)
                    <= rank.get(below.layer_id, -1)
                ):
                    continue
                ax1, ay1, ax2, ay2 = boxes.get(above.id, (0, 0, 0, 0))
                bx1, by1, bx2, by2 = boxes.get(below.id, (0, 0, 0, 0))
                width = max(0, min(ax2, bx2) - max(ax1, bx1))
                height = max(0, min(ay2, by2) - max(ay1, by1))
                if width and height:
                    overlaps.append({
                        "above": above.id,
                        "below": below.id,
                        "area_mm2": width * height,
                        "same_color": above.color == below.color,
                        "blend_candidate": above.blend_with_below,
                    })
        return {
            "layers": [
                {
                    **asdict(layer),
                    "object_count": sum(
                        item.enabled and item.layer_id == layer.id
                        for item in self.objects
                    ),
                }
                for layer in ordered
            ],
            "overlaps": overlaps,
        }

    def analysis(self) -> dict:
        objects = []
        findings = []
        brackets = []
        for item in self.objects:
            detail = {
                "id": item.id,
                "element_id": item.element_id or item.id,
                "stitch_type": item.stitch_type,
                "layer_id": item.layer_id,
                "physical_layer": item.physical_layer,
                "boundary_count": len(item.boundary_paths()),
            }
            if item.stitch_type == "satin":
                diagnostics = satin_diagnostics(item)
                detail["satin"] = asdict(diagnostics)
                item_brackets = satin_decision_brackets(item)
                detail["decision_brackets"] = [
                    bracket.json() for bracket in item_brackets
                ]
                brackets.extend(bracket.json() for bracket in item_brackets)
                if not diagnostics.valid:
                    findings.append({
                        "severity": "blocker",
                        "rule": "satin_rail_validity",
                        "object_id": item.id,
                        "message": diagnostics.reason or
                        "Satin rails do not have stable correspondence.",
                    })
                elif (
                    diagnostics.mean_width_mm is not None
                    and diagnostics.mean_width_mm > 6.0
                ):
                    findings.append({
                        "severity": "warning",
                        "rule": "satin_width",
                        "object_id": item.id,
                        "value_mm": diagnostics.mean_width_mm,
                        "message":
                        "Column exceeds the initial 6 mm wearable satin range.",
                    })
                if (
                    diagnostics.shortening_deviation_mm is not None
                    and diagnostics.shortening_deviation_mm > 0.75
                ):
                    findings.append({
                        "severity": "warning",
                        "rule": "inside_curve_concentration",
                        "object_id": item.id,
                        "value_mm": diagnostics.shortening_deviation_mm,
                        "message":
                        "Width shortening suggests concentrated inside-curve penetrations.",
                    })
            objects.append(detail)
        registered = [
            item for item in self.objects
            if item.enabled and item.visual_role == "registered_mark"
        ]
        registered_points = [
            point for item in registered for point in item.flattened()
        ]
        if registered_points:
            registered_width = (
                max(x for x, _ in registered_points)
                - min(x for x, _ in registered_points)
            )
            registered_height = (
                max(y for _, y in registered_points)
                - min(y for _, y in registered_points)
            )
            if min(registered_width, registered_height) < 2.5:
                findings.append({
                    "severity": "warning",
                    "rule": "registered_mark_size",
                    "object_id": ",".join(item.id for item in registered),
                    "dimensions_mm": {
                        "width": registered_width,
                        "height": registered_height,
                    },
                    "message":
                    "Registered mark needs a simplified running-stitch sew-out review.",
                })
        sequence, neighbors = _sequence_objects(self)
        return {
            "objects": objects,
            "sequence": [item.id for item in sequence],
            "neighbors": neighbors,
            "findings": findings,
            "brackets": brackets,
        }


def _length(left: Point, right: Point) -> float:
    return hypot(right[0] - left[0], right[1] - left[1])


def _path_length(points: list[Point]) -> float:
    return sum(_length(start, end) for start, end in zip(points, points[1:]))


def _resample_path(points: list[Point], count: int) -> list[Point]:
    """Resample an open path by arc length to produce matched satin topology."""
    if not points or count <= 0:
        return []
    if len(points) == 1:
        return [points[0]] * count
    lengths = [0.0]
    for start, end in zip(points, points[1:]):
        lengths.append(lengths[-1] + _length(start, end))
    total = lengths[-1]
    if total == 0:
        return [points[0]] * count
    result = []
    segment = 0
    for index in range(count):
        target = total * index / max(1, count - 1)
        while segment + 1 < len(lengths) and lengths[segment + 1] < target:
            segment += 1
        start, end = points[segment], points[min(segment + 1, len(points) - 1)]
        span = lengths[min(segment + 1, len(lengths) - 1)] - lengths[segment]
        fraction = 0 if span == 0 else (target - lengths[segment]) / span
        result.append((
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
        ))
    return result


def _axis_intersections(
    boundary: list[Point], value: float, *, vertical: bool
) -> list[float]:
    hits = []
    for start, end in zip(boundary, boundary[1:]):
        a0, a1 = (start[0], end[0]) if vertical else (start[1], end[1])
        b0, b1 = (start[1], end[1]) if vertical else (start[0], end[0])
        if (a0 <= value < a1) or (a1 <= value < a0):
            hits.append(b0 + (value - a0) * (b1 - b0) / (a1 - a0))
    return sorted(hits)


def _derived_satin_rails(item: VectorObject) -> list[list[Point]]:
    """Derive two rails from a legacy single-boundary satin object."""
    boundaries = item.boundary_paths()
    if not boundaries:
        return []
    boundary = boundaries[0]
    min_x, max_x = min(x for x, _ in boundary), max(x for x, _ in boundary)
    min_y, max_y = min(y for _, y in boundary), max(y for _, y in boundary)
    vertical = (max_x - min_x) >= (max_y - min_y)
    low, high = (min_x, max_x) if vertical else (min_y, max_y)
    spacing = max(0.2, item.satin_spacing_mm)
    value = low + spacing / 2
    left: list[Point] = []
    right: list[Point] = []
    while value < high:
        hits = _axis_intersections(boundary, value, vertical=vertical)
        spans = list(zip(hits[::2], hits[1::2]))
        if spans:
            first, second = max(spans, key=lambda span: span[1] - span[0])
            if vertical:
                left.append((value, first))
                right.append((value, second))
            else:
                left.append((first, value))
                right.append((second, value))
        value += spacing
    return [left, right] if len(left) >= 2 else []


def normalized_satin_rails(item: VectorObject) -> tuple[list[list[Point]], bool]:
    rails = item.rail_paths()
    derived = False
    if len(rails) != 2:
        rails = _derived_satin_rails(item)
        derived = True
    if len(rails) != 2 or min(map(len, rails)) < 2:
        return [], derived
    same = _length(rails[0][0], rails[1][0]) + _length(
        rails[0][-1], rails[1][-1]
    )
    flipped = _length(rails[0][0], rails[1][-1]) + _length(
        rails[0][-1], rails[1][0]
    )
    if flipped < same:
        rails[1].reverse()
    count = max(
        2,
        ceil(max(_path_length(rail) for rail in rails) /
             max(0.2, item.satin_spacing_mm)) + 1,
    )
    resampled = derived or any(len(rail) != count for rail in rails)
    return [_resample_path(rail, count) for rail in rails], resampled


def _bounded_score(
    value: float, lower: float, upper: float, maximum: int
) -> int:
    """Recovered bounded linear penalty-to-score mapping."""
    if value <= lower:
        return maximum
    if value >= upper:
        return 0
    return max(0, min(maximum, round(
        maximum * (upper - value) / (upper - lower)
    )))


def score_satin_rail_points(
    rails: list[list[Point]], *, resampled: bool = False
) -> SatinDiagnostics:
    """Score already paired rails using the shared physical satin criteria."""
    if len(rails) != 2 or len(rails[0]) != len(rails[1]) or not rails[0]:
        return SatinDiagnostics(
            False, 0, 0, 0, 0, 0, None, None, None, resampled,
            "satin requires two non-empty rails with matched topology",
        )
    widths = [_length(left, right) for left, right in zip(*rails)]
    mean_width = sum(widths) / len(widths)
    deviation = pstdev(widths) if len(widths) > 1 else 0.0
    shortening = []
    for index, width in enumerate(widths):
        neighborhood = widths[max(0, index - 2):index + 3]
        delta = max(neighborhood) - width
        if delta > 0.05:
            shortening.append(delta)
    shortening_deviation = (
        pstdev(shortening) if len(shortening) > 1 else 0.0
    )
    # The recovered implementation gives a full score below a lower threshold,
    # zero above an upper threshold, and linearly interpolates between them.
    # These thresholds apply the project's production guidance: satin is
    # preferred from 1--6 mm and transitions to fill by 8 mm.
    width_score = (
        _bounded_score(mean_width, 6.0, 8.0, 100)
        if mean_width >= 1.0 else 0
    )
    deviation_score = _bounded_score(
        mean_width + deviation, 6.25, 9.5, 10000
    )
    shortening_score = _bounded_score(
        mean_width + shortening_deviation, 6.15, 9.0, 10000
    )
    score = min(
        width_score,
        round(deviation_score / 100),
        round(shortening_score / 100),
    )
    valid = 1.0 <= mean_width <= 8.0 and score > 0
    reason = ""
    if mean_width < 1.0:
        reason = "column is narrower than the 1 mm satin minimum"
    elif mean_width > 8.0:
        reason = "column is wider than the 8 mm satin-to-fill transition"
    return SatinDiagnostics(
        valid, score, width_score, deviation_score, shortening_score,
        len(widths), mean_width, deviation, shortening_deviation, resampled,
        reason,
    )


def satin_diagnostics(item: VectorObject) -> SatinDiagnostics:
    rails, resampled = normalized_satin_rails(item)
    return score_satin_rail_points(rails, resampled=resampled)


def _bracket_status(
    value: float, *, lower: float | None = None,
    preferred_upper: float | None = None, warning_upper: float | None = None,
) -> str:
    if lower is not None and value < lower:
        return "blocker"
    if warning_upper is not None and value > warning_upper:
        return "blocker"
    if preferred_upper is not None and value > preferred_upper:
        return "warning"
    return "pass"


def _contiguous_intervals(statuses: list[str]) -> list[tuple[int, int, str]]:
    if not statuses:
        return []
    result = []
    start = 0
    for index, status in enumerate(statuses[1:], 1):
        if status != statuses[start]:
            result.append((start, index - 1, statuses[start]))
            start = index
    result.append((start, len(statuses) - 1, statuses[start]))
    return result


def _circumradius(left: Point, middle: Point, right: Point) -> float | None:
    a = _length(middle, right)
    b = _length(left, right)
    c = _length(left, middle)
    double_area = abs(
        (middle[0] - left[0]) * (right[1] - left[1])
        - (middle[1] - left[1]) * (right[0] - left[0])
    )
    if double_area < 1e-6:
        return None
    return a * b * c / (2 * double_area)


def satin_decision_brackets(item: VectorObject) -> list[DecisionBracket]:
    """Return object and rail-interval evidence for a satin column."""
    diagnostics = satin_diagnostics(item)
    rails, _ = normalized_satin_rails(item)
    controls = [
        "edit rails", "move split", "adjust satin spacing",
        "change stitch type",
    ]
    if len(rails) != 2 or not rails[0] or len(rails[0]) != len(rails[1]):
        return [DecisionBracket(
            "satin_rail_topology", item.id, None, "rail pairs",
            {"required_rails": 2, "matched_topology": True},
            "blocker",
            diagnostics.reason or "Satin rails do not have stable correspondence.",
            "block_satin",
            fallback="convert object to running or tatami",
            operator_controls=controls,
        )]

    widths = [_length(left, right) for left, right in zip(*rails)]
    mean_width = sum(widths) / len(widths)
    width_status = _bracket_status(
        mean_width, lower=1.0, preferred_upper=6.0, warning_upper=8.0,
    )
    brackets = [DecisionBracket(
        "satin_mean_width", item.id, mean_width, "mm",
        {"minimum": 1.0, "preferred": [1.0, 6.0], "fallback": [6.0, 8.0]},
        width_status,
        (
            "Mean column width is inside the preferred satin range."
            if width_status == "pass"
            else "Mean column width is outside the preferred satin range."
        ),
        "keep_satin" if width_status == "pass" else "review_satin_construction",
        evidence={
            "minimum_width_mm": min(widths),
            "maximum_width_mm": max(widths),
            "pair_count": len(widths),
            "score": diagnostics.score,
        },
        fallback=(
            None if width_status == "pass"
            else "convert narrow intervals to running or wide intervals to tatami"
        ),
        operator_controls=controls,
    )]

    width_statuses = [
        _bracket_status(
            width, lower=1.0, preferred_upper=6.0, warning_upper=8.0
        )
        for width in widths
    ]
    divisor = max(1, len(widths) - 1)
    for start, end, status in _contiguous_intervals(width_statuses):
        interval = widths[start:end + 1]
        brackets.append(DecisionBracket(
            "satin_width_interval", item.id,
            {"minimum": min(interval), "maximum": max(interval)}, "mm",
            {
                "minimum": 1.0,
                "preferred": [1.0, 6.0],
                "fallback": [6.0, 8.0],
            },
            status,
            (
                "Rail interval remains inside the preferred width range."
                if status == "pass"
                else "Rail interval needs a split or alternate construction."
            ),
            "keep_interval" if status == "pass" else "split_or_fallback",
            segment={"start": start / divisor, "end": end / divisor},
            fallback=(
                None if status == "pass"
                else "running stitch below 1 mm; tatami above 8 mm"
            ),
            operator_controls=controls,
        ))

    deviation = diagnostics.width_deviation_mm or 0.0
    deviation_status = _bracket_status(
        deviation, preferred_upper=.75, warning_upper=1.5,
    )
    brackets.append(DecisionBracket(
        "satin_width_deviation", item.id, deviation, "mm",
        {"preferred_maximum": .75, "block_above": 1.5},
        deviation_status,
        "Rail width variation is stable." if deviation_status == "pass"
        else "Rapid width variation may destabilize rail correspondence.",
        "keep_rails" if deviation_status == "pass" else "split_at_width_change",
        fallback=(
            None if deviation_status == "pass"
            else "split the column at the width transition"
        ),
        operator_controls=controls,
    ))

    inside_spacings = []
    radius_ratios = []
    angle_changes = []
    for index in range(1, len(widths)):
        inside_spacings.append(min(
            _length(rails[0][index - 1], rails[0][index]),
            _length(rails[1][index - 1], rails[1][index]),
        ))
        previous_vector = (
            rails[1][index - 1][0] - rails[0][index - 1][0],
            rails[1][index - 1][1] - rails[0][index - 1][1],
        )
        current_vector = (
            rails[1][index][0] - rails[0][index][0],
            rails[1][index][1] - rails[0][index][1],
        )
        denominator = hypot(*previous_vector) * hypot(*current_vector)
        if denominator:
            cosine = max(-1.0, min(1.0, (
                previous_vector[0] * current_vector[0]
                + previous_vector[1] * current_vector[1]
            ) / denominator))
            angle_changes.append(degrees(acos(cosine)))
    for index in range(1, len(widths) - 1):
        radii = [
            radius
            for radius in (
                _circumradius(rail[index - 1], rail[index], rail[index + 1])
                for rail in rails
            )
            if radius is not None
        ]
        if radii and widths[index]:
            radius_ratios.append(min(radii) / widths[index])

    minimum_spacing = min(inside_spacings) if inside_spacings else item.satin_spacing_mm
    spacing_status = _bracket_status(
        minimum_spacing, lower=.30,
    )
    if spacing_status == "pass" and minimum_spacing < .50:
        spacing_status = "warning"
    brackets.append(DecisionBracket(
        "inside_curve_penetration_spacing", item.id, minimum_spacing, "mm",
        {"block_below": .30, "warning_below": .50, "preferred_minimum": .50},
        spacing_status,
        "Inside-curve penetrations remain separated."
        if spacing_status == "pass"
        else "Inside-curve penetrations are concentrating.",
        "keep_satin" if spacing_status == "pass" else "split_and_fan",
        evidence={
            "configured_satin_spacing_mm": item.satin_spacing_mm,
            "minimum_inside_spacing_mm": minimum_spacing,
        },
        fallback=(
            None if spacing_status == "pass"
            else "split the curve and stagger inside penetrations"
        ),
        operator_controls=controls,
    ))

    if radius_ratios:
        minimum_ratio = min(radius_ratios)
        radius_status = (
            "blocker" if minimum_ratio < .75
            else "warning" if minimum_ratio < 1.5
            else "pass"
        )
        brackets.append(DecisionBracket(
            "inside_curve_radius_ratio", item.id, minimum_ratio,
            "radius / column width",
            {"block_below": .75, "warning_below": 1.5, "preferred_minimum": 1.5},
            radius_status,
            "Curve radius supports stable satin correspondence."
            if radius_status == "pass"
            else "Curve radius is tight relative to the column width.",
            "keep_satin" if radius_status == "pass" else "split_and_fan",
            evidence={"minimum_ratio": minimum_ratio},
            fallback=(
                None if radius_status == "pass"
                else "split the curve or convert the tight interval"
            ),
            operator_controls=controls,
        ))

    maximum_angle = max(angle_changes, default=0.0)
    angle_status = (
        "blocker" if maximum_angle > 20.0
        else "warning" if maximum_angle > 12.0
        else "pass"
    )
    brackets.append(DecisionBracket(
        "satin_course_angle_change", item.id, maximum_angle, "degrees",
        {"preferred_maximum": 12.0, "block_above": 20.0},
        angle_status,
        "Stitch-angle progression is smooth." if angle_status == "pass"
        else "Stitch angle changes too abruptly between courses.",
        "keep_satin" if angle_status == "pass" else "smooth_or_split",
        fallback=(
            None if angle_status == "pass"
            else "smooth the rails or split before the angle change"
        ),
        operator_controls=controls,
    ))
    return brackets


def _split_segment(start: Point, end: Point, maximum: float) -> list[Point]:
    pieces = max(1, ceil(_length(start, end) / max(0.1, maximum)))
    return [
        (
            start[0] + (end[0] - start[0]) * index / pieces,
            start[1] + (end[1] - start[1]) * index / pieces,
        )
        for index in range(1, pieces + 1)
    ]


def _emit_path(
    operations: list[dict],
    points: list[Point],
    *,
    role: str,
    object_id: str,
    maximum: float,
) -> None:
    if len(points) < 2:
        return
    operations.append({
        "kind": "jump", "x": points[0][0], "y": points[0][1],
        "role": role, "region_id": object_id,
    })
    operations.append({
        "kind": "stitch", "x": points[0][0], "y": points[0][1],
        "role": role, "region_id": object_id,
    })
    for start, end in zip(points, points[1:]):
        for point in _split_segment(start, end, maximum):
            operations.append({
                "kind": "stitch", "x": point[0], "y": point[1],
                "role": role, "region_id": object_id,
            })


def _closed_loop(points: list[Point]) -> list[Point]:
    if points and points[0] != points[-1]:
        return [*points, points[0]]
    return points


def _satin_coverage(rails: list[list[Point]]) -> list[list[Point]]:
    if len(rails) != 2:
        return []
    return [_closed_loop([*rails[0], *reversed(rails[1])])]


def _emit_connected_path(
    operations: list[dict],
    primitive: SewPrimitive,
    *,
    object_id: str,
    coverage: list[list[Point]],
    previous: Point | None,
    connector_maximum: float,
) -> Point | None:
    """Emit one primitive, sewing a covered connector where it is physical."""
    points = primitive.points
    if len(points) < 2:
        return previous
    if (
        previous is not None
        and primitive.reversible
        and _length(previous, points[-1]) < _length(previous, points[0])
    ):
        points = list(reversed(points))
    start = points[0]
    connected = (
        previous is not None
        and (
            _length(previous, start) <= 0.05
            or (
                coverage
                and _safe_connector(
                    previous, start, coverage, connector_maximum
                )
            )
        )
    )
    if connected:
        if _length(previous, start) > 0.05:
            for point in _split_segment(previous, start, 2.5):
                operations.append({
                    "kind": "stitch",
                    "x": point[0],
                    "y": point[1],
                    "role": "covered_underpath_connector",
                    "region_id": object_id,
                })
        operations.append({
            "kind": "stitch",
            "x": start[0],
            "y": start[1],
            "role": primitive.role,
            "region_id": object_id,
        })
        for left, right in zip(points, points[1:]):
            for point in _split_segment(
                left, right, primitive.maximum_stitch_mm
            ):
                operations.append({
                    "kind": "stitch",
                    "x": point[0],
                    "y": point[1],
                    "role": primitive.role,
                    "region_id": object_id,
                })
    else:
        _emit_path(
            operations,
            points,
            role=primitive.role,
            object_id=object_id,
            maximum=primitive.maximum_stitch_mm,
        )
    return points[-1]


def _emit_primitive_chain(
    primitives: list[SewPrimitive],
    *,
    object_id: str,
    coverage: list[list[Point]],
    connector_maximum: float = 7.0,
) -> list[dict]:
    operations: list[dict] = []
    previous: Point | None = None
    choices = [
        (False, True) if primitive.reversible else (False,)
        for primitive in primitives
    ]
    orientations = min(
        product(*choices),
        key=lambda candidate: sum(
            _length(
                (primitives[index].points[0]
                 if candidate[index] else primitives[index].points[-1]),
                (primitives[index + 1].points[-1]
                 if candidate[index + 1]
                 else primitives[index + 1].points[0]),
            )
            for index in range(len(primitives) - 1)
        ),
        default=(),
    )
    oriented = []
    for primitive, reverse in zip(primitives, orientations):
        oriented.append(SewPrimitive(
            primitive.role,
            (list(reversed(primitive.points))
             if reverse else primitive.points),
            primitive.maximum_stitch_mm,
            reversible=False,
        ))
    for primitive in oriented:
        previous = _emit_connected_path(
            operations,
            primitive,
            object_id=object_id,
            coverage=coverage,
            previous=previous,
            connector_maximum=connector_maximum,
        )
    return operations


def _rotate(point: Point, angle: float) -> Point:
    c, s = cos(angle), sin(angle)
    return point[0] * c - point[1] * s, point[0] * s + point[1] * c


def _fill_runs(
    boundaries: list[list[Point]], *, angle_degrees: float,
    spacing: float, maximum: float,
) -> list[list[Point]]:
    """Generate even/odd tatami rows across outer boundaries and holes."""
    angle = radians(angle_degrees)
    loops = [[_rotate(point, -angle) for point in loop] for loop in boundaries]
    values = [point for loop in loops for point in loop]
    if not values:
        return []
    row = min(y for _, y in values) + spacing / 2
    max_y = max(y for _, y in values)
    row_number = 0
    result: list[list[Point]] = []
    while row < max_y:
        hits = []
        for loop in loops:
            hits.extend(_axis_intersections(loop, row, vertical=False))
        hits.sort()
        for left, right in zip(hits[::2], hits[1::2]):
            length = right - left
            if length < 0.1:
                continue
            count = max(1, ceil(length / maximum))
            run = [
                _rotate((left + length * index / count, row), angle)
                for index in range(count + 1)
            ]
            if row_number % 2:
                run.reverse()
            result.append(run)
        row += spacing
        row_number += 1
    return result


def _point_in_boundaries(point: Point, boundaries: list[list[Point]]) -> bool:
    inside = False
    x, y = point
    for loop in boundaries:
        for start, end in zip(loop, loop[1:]):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length_squared = dx * dx + dy * dy
            if length_squared:
                projection = (
                    (x - start[0]) * dx + (y - start[1]) * dy
                ) / length_squared
                if 0 <= projection <= 1:
                    nearest = (
                        start[0] + projection * dx,
                        start[1] + projection * dy,
                    )
                    if _length(point, nearest) <= 0.01:
                        return True
            if (start[1] > y) != (end[1] > y):
                crossing = (
                    start[0]
                    + (y - start[1]) * (end[0] - start[0])
                    / (end[1] - start[1])
                )
                if x < crossing:
                    inside = not inside
    return inside


def _safe_connector(
    start: Point, end: Point, boundaries: list[list[Point]], maximum: float
) -> bool:
    distance = _length(start, end)
    if distance > maximum:
        return False
    samples = max(2, ceil(distance / 0.25))
    return all(
        _point_in_boundaries((
            start[0] + (end[0] - start[0]) * index / samples,
            start[1] + (end[1] - start[1]) * index / samples,
        ), boundaries)
        for index in range(1, samples)
    )


def _emit_fill(
    operations: list[dict], runs: list[list[Point]], boundaries: list[list[Point]],
    *, role: str, object_id: str, connector_maximum: float,
) -> None:
    previous: Point | None = None
    for run in runs:
        if len(run) < 2:
            continue
        if previous and _safe_connector(
            previous, run[0], boundaries, connector_maximum
        ):
            operations.append({
                "kind": "stitch", "x": run[0][0], "y": run[0][1],
                "role": "vector_fill_connector", "region_id": object_id,
            })
            for point in run[1:]:
                operations.append({
                    "kind": "stitch", "x": point[0], "y": point[1],
                    "role": role, "region_id": object_id,
                })
        else:
            _emit_path(
                operations, run, role=role, object_id=object_id,
                maximum=12.1,
            )
        previous = run[-1]


def _object_operations(
    item: VectorObject, layer: EmbroideryLayer, *, reverse: bool = False
) -> list[dict]:
    operations: list[dict] = []
    if item.stitch_type == "running":
        points = item.flattened()
        if reverse:
            points.reverse()
        _emit_path(
            operations, points, role="vector_running",
            object_id=item.id, maximum=min(7.0, item.stitch_length_mm),
        )
        return operations

    if item.stitch_type == "satin":
        rails, _ = normalized_satin_rails(item)
        if len(rails) != 2:
            raise ValueError(
                f"satin object {item.id} needs two rails or a derivable boundary"
            )
        if reverse:
            rails = [list(reversed(rail)) for rail in rails]
        centre = [
            ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
            for left, right in zip(*rails)
        ]
        primitives: list[SewPrimitive] = []
        if item.underlay_enabled:
            diagnostics = satin_diagnostics(item)
            if (
                diagnostics.mean_width_mm is not None
                and diagnostics.mean_width_mm >= 3.5
            ):
                inset = 0.35
                left_edge = []
                right_edge = []
                for left, right in zip(*rails):
                    width = _length(left, right)
                    fraction = min(0.45, inset / max(0.01, width))
                    left_edge.append((
                        left[0] + (right[0] - left[0]) * fraction,
                        left[1] + (right[1] - left[1]) * fraction,
                    ))
                    right_edge.append((
                        right[0] + (left[0] - right[0]) * fraction,
                        right[1] + (left[1] - right[1]) * fraction,
                    ))
                primitives.extend([
                    SewPrimitive(
                        "vector_satin_edge_underlay", left_edge, 2.5
                    ),
                    SewPrimitive(
                        "vector_satin_edge_underlay", right_edge, 2.5
                    ),
                ])
                zigzag = [
                    pair[index % 2]
                    for index, pair in enumerate(zip(*rails))
                ]
                primitives.append(SewPrimitive(
                    "vector_satin_zigzag_underlay", zigzag, 7.0,
                ))
            else:
                primitives.append(SewPrimitive(
                    "vector_satin_center_underlay", centre, 2.5,
                ))
        top: list[Point] = []
        for index, pair in enumerate(zip(*rails)):
            top.extend(pair if index % 2 == 0 else reversed(pair))
        primitives.append(SewPrimitive(
            "vector_satin", top, 12.1, reversible=False,
        ))
        return _emit_primitive_chain(
            primitives,
            object_id=item.id,
            coverage=_satin_coverage(rails),
            connector_maximum=min(7.0, item.stitch_length_mm),
        )

    boundaries = item.boundary_paths()
    if not boundaries:
        raise ValueError(f"tatami object {item.id} must have a closed boundary")
    density = max(0.1, layer.density_multiplier)
    top_spacing = item.fill_spacing_mm / density
    if item.underlay_enabled:
        for boundary in boundaries:
            _emit_path(
                operations, boundary, role="vector_tatami_edge_underlay",
                object_id=item.id, maximum=3.5,
            )
        _emit_fill(
            operations,
            _fill_runs(
                boundaries,
                angle_degrees=item.fill_angle_degrees + 90,
                spacing=max(1.1, top_spacing * 3),
                maximum=min(3.2, item.stitch_length_mm),
            ),
            boundaries,
            role="vector_tatami_underlay",
            object_id=item.id,
            connector_maximum=2.2,
        )
    _emit_fill(
        operations,
        _fill_runs(
            boundaries, angle_degrees=item.fill_angle_degrees,
            spacing=top_spacing, maximum=item.stitch_length_mm,
        ),
        boundaries,
        role="vector_tatami",
        object_id=item.id,
        connector_maximum=1.6,
    )
    return operations


def _object_entry(item: VectorObject, reverse: bool = False) -> Point:
    if item.stitch_type == "satin":
        rails, _ = normalized_satin_rails(item)
        if rails:
            return rails[0][-1] if reverse else rails[0][0]
    paths = item.boundary_paths()
    points = paths[0] if paths else item.flattened()
    if not points:
        return (0.0, 0.0)
    return points[-1] if reverse else points[0]


def _object_exit(item: VectorObject, reverse: bool = False) -> Point:
    if item.stitch_type == "satin":
        rails, _ = normalized_satin_rails(item)
        if rails:
            return rails[1][0] if reverse else rails[1][-1]
    paths = item.boundary_paths()
    points = paths[0] if paths else item.flattened()
    if not points:
        return (0.0, 0.0)
    return points[0] if reverse else points[-1]


def _object_reversible(item: VectorObject) -> bool:
    return not item.lock_sequence and item.stitch_type in {"running", "satin"}


def _object_coverage(item: VectorObject) -> list[list[Point]]:
    if item.stitch_type == "satin":
        rails, _ = normalized_satin_rails(item)
        return _satin_coverage(rails)
    return item.boundary_paths()


def _sequence_layer_oriented(
    objects: list[VectorObject], *, center_outward: bool
) -> list[tuple[VectorObject, bool]]:
    """Graph-aware route within one hard compositing/physical layer."""
    if not objects:
        return []
    locked = sorted(
        (item for item in objects if item.lock_sequence),
        key=lambda item: (
            item.sequence_order if item.sequence_order is not None else 10**9,
            item.id,
        ),
    )
    pending = [item for item in objects if not item.lock_sequence]
    routed = [(item, False) for item in locked]
    if not routed and pending:
        if center_outward:
            choices = [
                (index, reverse)
                for index, item in enumerate(pending)
                for reverse in ([False, True] if _object_reversible(item)
                                else [False])
            ]
            index, reverse = min(
                choices,
                key=lambda choice: (
                    _object_entry(
                        pending[choice[0]], choice[1]
                    )[0] ** 2
                    + _object_entry(
                        pending[choice[0]], choice[1]
                    )[1] ** 2,
                    pending[choice[0]].sequence_order
                    if pending[choice[0]].sequence_order is not None else 10**9,
                    pending[choice[0]].id,
                    choice[1],
                ),
            )
        else:
            index = min(
                range(len(pending)),
                key=lambda index: (
                    pending[index].sequence_order
                    if pending[index].sequence_order is not None else index,
                    pending[index].id,
                ),
            )
            reverse = False
        routed.append((pending.pop(index), reverse))
    while pending:
        previous, previous_reverse = routed[-1]
        origin = _object_exit(previous, previous_reverse)
        same_color = [
            index for index, item in enumerate(pending)
            if item.color == previous.color
        ]
        candidates = same_color or list(range(len(pending)))
        choices = [
            (index, reverse)
            for index in candidates
            for reverse in (
                [False, True] if _object_reversible(pending[index])
                else [False]
            )
        ]
        index, reverse = min(
            choices,
            key=lambda choice: (
                _length(
                    origin,
                    _object_entry(pending[choice[0]], choice[1]),
                ),
                pending[choice[0]].sequence_order
                if pending[choice[0]].sequence_order is not None else 10**9,
                pending[choice[0]].id,
                choice[1],
            ),
        )
        routed.append((pending.pop(index), reverse))
    return routed


def _route_objects(
    document: VectorDocument,
) -> tuple[list[VectorObject], list[dict], dict[str, bool]]:
    result: list[VectorObject] = []
    neighbors = []
    orientations: dict[str, bool] = {}
    for layer in document.ordered_layers():
        objects = [
            item for item in document.objects
            if item.enabled and item.layer_id == layer.id
        ]
        physical_groups: dict[int, list[VectorObject]] = {}
        for item in objects:
            key = item.physical_layer if item.physical_layer is not None else 0
            physical_groups.setdefault(key, []).append(item)
        for physical_layer in sorted(physical_groups):
            group = physical_groups[physical_layer]
            for index, left in enumerate(group):
                for right in group[index + 1:]:
                    distance = min(
                        _length(_object_exit(left), _object_entry(right)),
                        _length(_object_exit(right), _object_entry(left)),
                    )
                    if distance <= document.neighbor_distance_mm:
                        neighbors.append({
                            "left": left.id, "right": right.id,
                            "distance_mm": distance,
                            "same_color": left.color == right.color,
                            "layer_id": layer.id,
                            "physical_layer": physical_layer,
                        })
            routed = _sequence_layer_oriented(
                group, center_outward=document.center_outward
            )
            result.extend(item for item, _ in routed)
            orientations.update({
                item.id: reverse for item, reverse in routed
            })
    return result, neighbors, orientations


def _sequence_objects(
    document: VectorDocument,
) -> tuple[list[VectorObject], list[dict]]:
    sequence, neighbors, _ = _route_objects(document)
    return sequence, neighbors


def _foundation_operations(document: VectorDocument) -> list[dict]:
    """Add the design-level running foundation before object underlay."""
    if not document.foundation_enabled:
        return []
    closed_items = [
        item for item in document.objects
        if item.enabled and item.boundary_paths()
    ]
    points = [
        point for item in closed_items
        for boundary in item.boundary_paths() for point in boundary
    ]
    if not points:
        points = [
            point for item in document.objects if item.enabled
            for point in item.flattened()
        ]
    if len(points) < 2:
        return []
    min_x, max_x = min(x for x, _ in points), max(x for x, _ in points)
    min_y, max_y = min(y for _, y in points), max(y for _, y in points)
    horizontal = max_x - min_x >= max_y - min_y
    intervals: list[tuple[float, float]] = []
    if horizontal:
        centre = (min_y + max_y) / 2
        for item in closed_items:
            hits = []
            for boundary in item.boundary_paths():
                hits.extend(_axis_intersections(
                    boundary, centre, vertical=False
                ))
            hits.sort()
            intervals.extend(zip(hits[::2], hits[1::2]))
    else:
        centre = (min_x + max_x) / 2
        for item in closed_items:
            hits = []
            for boundary in item.boundary_paths():
                hits.extend(_axis_intersections(
                    boundary, centre, vertical=True
                ))
            hits.sort()
            intervals.extend(zip(hits[::2], hits[1::2]))
    if intervals:
        intervals.sort()
        merged = [list(intervals[0])]
        for start, end in intervals[1:]:
            if start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
    else:
        merged = [[min_x, max_x] if horizontal else [min_y, max_y]]
    operations: list[dict] = []
    for start, end in merged:
        path = (
            [(start, centre), (end, centre)]
            if horizontal else [(centre, start), (centre, end)]
        )
        _emit_path(
            operations, path, role="design_center_underlay",
            object_id="foundation", maximum=2.5,
        )
    return operations


def _thread_economy(operations: list[dict]) -> dict:
    previous: Point | None = None
    stitch_mm = 0.0
    jump_mm = 0.0
    stitch_count = 0
    jump_count = 0
    trim_count = 0
    hidden = 0
    underpath = 0
    for operation in operations:
        kind = operation["kind"]
        point = (
            (operation["x"], operation["y"])
            if "x" in operation and "y" in operation else None
        )
        if kind == "stitch":
            stitch_count += 1
            if previous is not None and point is not None:
                stitch_mm += _length(previous, point)
            if operation.get("role") == "hidden_connector":
                hidden += 1
            if operation.get("role") == "covered_underpath_connector":
                underpath += 1
            previous = point
        elif kind == "jump":
            jump_count += 1
            if previous is not None and point is not None:
                jump_mm += _length(previous, point)
            previous = point
        elif kind == "trim":
            trim_count += 1
            previous = None
        elif kind == "color":
            previous = None
    movement = stitch_mm + jump_mm
    return {
        "stitch_count": stitch_count,
        "sewn_thread_mm": stitch_mm,
        "jump_count": jump_count,
        "jump_distance_mm": jump_mm,
        "trim_count": trim_count,
        "hidden_connector_count": hidden,
        "covered_underpath_connector_count": underpath,
        "needle_down_movement_ratio": (
            stitch_mm / movement if movement else 1.0
        ),
    }


def _construct_analysis(
    item: VectorObject, layer: EmbroideryLayer
) -> dict:
    """Describe the geometry-to-sewing contract before machine expansion."""
    if item.stitch_type == "running":
        return {
            "object_id": item.id,
            "construct": "line_path",
            "reversible": _object_reversible(item),
            "course_count": 1,
            "coverage_loop_count": 0,
        }
    if item.stitch_type == "satin":
        rails, _ = normalized_satin_rails(item)
        diagnostics = satin_diagnostics(item)
        underlay_courses = (
            3 if (
                item.underlay_enabled
                and diagnostics.mean_width_mm is not None
                and diagnostics.mean_width_mm >= 3.5
            )
            else int(item.underlay_enabled)
        )
        return {
            "object_id": item.id,
            "construct": "satin_column",
            "reversible": _object_reversible(item),
            "course_count": underlay_courses + 1,
            "underlay_course_count": underlay_courses,
            "rail_pair_count": len(rails[0]) if len(rails) == 2 else 0,
            "coverage_loop_count": len(_satin_coverage(rails)),
        }
    boundaries = item.boundary_paths()
    density = max(0.1, layer.density_multiplier)
    top_spacing = item.fill_spacing_mm / density
    top_runs = _fill_runs(
        boundaries,
        angle_degrees=item.fill_angle_degrees,
        spacing=top_spacing,
        maximum=item.stitch_length_mm,
    )
    underlay_runs = (
        _fill_runs(
            boundaries,
            angle_degrees=item.fill_angle_degrees + 90,
            spacing=max(1.1, top_spacing * 3),
            maximum=min(3.2, item.stitch_length_mm),
        )
        if item.underlay_enabled else []
    )
    return {
        "object_id": item.id,
        "construct": "fill_area",
        "reversible": False,
        "course_count": len(top_runs) + len(underlay_runs),
        "underlay_course_count": len(underlay_runs),
        "top_course_count": len(top_runs),
        "coverage_loop_count": len(boundaries),
    }


def plan_document(document: VectorDocument) -> dict:
    """Plan a document and return operations plus inspectable diagnostics."""
    sequence, neighbors, orientations = _route_objects(document)
    layers = {layer.id: layer for layer in document.ordered_layers()}
    result = _foundation_operations(document)
    active_color: str | None = None
    previous_exit: Point | None = None
    later_boundaries = {
        item.id: _object_coverage(item)
        for item in sequence
    }
    for index, item in enumerate(sequence):
        reverse = orientations.get(item.id, False)
        object_ops = _object_operations(
            item, layers[item.layer_id], reverse=reverse
        )
        if not object_ops:
            continue
        positioned = [
            operation for operation in object_ops
            if "x" in operation and "y" in operation
        ]
        entry = (
            (positioned[0]["x"], positioned[0]["y"])
            if positioned else _object_entry(item, reverse)
        )
        if active_color is not None and item.color != active_color:
            result.append({"kind": "trim", "role": "object_trim"})
            result.append({"kind": "color", "role": "color_change"})
            previous_exit = None
        elif previous_exit is not None:
            origin = previous_exit
            covers = [
                boundary
                for later in sequence[index:]
                for boundary in later_boundaries[later.id]
            ]
            can_hide = (
                _length(origin, entry) <= document.connector_max_mm
                and covers
                and _safe_connector(
                    origin, entry, covers, document.connector_max_mm
                )
            )
            if can_hide:
                result.append({
                    "kind": "stitch", "x": entry[0], "y": entry[1],
                    "role": "hidden_connector", "region_id": item.id,
                })
                if object_ops[0]["kind"] == "jump":
                    object_ops = object_ops[1:]
            else:
                result.append({"kind": "trim", "role": "object_trim"})
        active_color = item.color
        result.extend(object_ops)
        previous_exit = (
            (positioned[-1]["x"], positioned[-1]["y"])
            if positioned else _object_exit(item, reverse)
        )
    analysis = document.analysis()
    economy = _thread_economy(result)
    findings = list(analysis["findings"])
    if economy["covered_underpath_connector_count"]:
        findings.append({
            "severity": "optimization",
            "rule": "covered_underpath_routing",
            "count": economy["covered_underpath_connector_count"],
            "message":
            "Covered running connectors replaced internal frame jumps.",
        })
    return {
        "operations": result,
        "sequence": [item.id for item in sequence],
        "orientations": orientations,
        "neighbors": neighbors,
        "constructs": [
            _construct_analysis(item, layers[item.layer_id])
            for item in sequence
        ],
        "diagnostics": analysis["objects"],
        "brackets": analysis["brackets"],
        "findings": findings,
        "thread_economy": economy,
    }


def stitch_operations(document: VectorDocument) -> list[dict]:
    """Backward-compatible operation-only planner entry point."""
    return plan_document(document)["operations"]

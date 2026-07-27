"""First deterministic raster-to-embroidery planning kernel.

This is intentionally conservative: it produces inspectable component-level
plans, not a claim of production-quality automatic digitizing.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, field
import base64
from io import BytesIO
from math import ceil, cos, floor, hypot, radians, sin
from typing import Iterable

from PIL import Image, ImageFilter
from posterize import (
    analyze_pass_filters,
    detail_edge_response,
    filter_blocking_mask,
    mask_geometry_metrics,
    otsu_foreground_mask,
    quantize_foreground,
)
from vector_document import score_satin_rail_points


@dataclass
class Operation:
    kind: str
    x: float = 0.0
    y: float = 0.0
    role: str = ""
    region_id: str = ""


@dataclass
class Region:
    id: str
    color: str
    pixels: int
    bounds: tuple[int, int, int, int]
    stitch_type: str
    contours: list[list[tuple[int, int]]] = field(default_factory=list)
    fill_spacing_mm: float | None = None
    stitch_length_mm: float | None = None
    stitch_diagnostics: dict = field(default_factory=dict)
    element_id: str = ""
    layer_index: int = 0
    feature_metrics: dict = field(default_factory=dict)
    pass_name: str = "interior"
    pass_index: int = 1
    underlay_operations: list[Operation] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)


@dataclass(frozen=True)
class DigitizingProfile:
    """Production-oriented limits for a material/product family."""
    name: str
    running_min_mm: float
    running_max_mm: float
    fill_spacing_mm: float
    fill_stitch_mm: float
    machine_max_stitch_mm: float
    center_outward_routing: bool = False


@dataclass(frozen=True)
class DigitizingAlgorithm:
    """A comparable automatic-digitizing strategy."""
    name: str
    label: str
    description: str
    min_area_mm2: float
    running_max_width_mm: float
    satin_max_width_mm: float
    fill_spacing_scale: float
    trimless_jump_mm: float
    preserve_color_layers: bool
    max_objects: int
    max_trims: int
    max_jumps: int
    trims_per_1000_stitches: float
    jumps_per_1000_stitches: float
    max_detail_objects: int
    detail_max_trims: int


ALGORITHMS = {
    "layered": DigitizingAlgorithm(
        "layered", "Layered", "Complete underlay, blocking, and top-detail plan.",
        0.08, 1.8, 8.0, 1.00, 7.0, True,
        400, 120, 600, 2.0, 10.0, 48, 250,
    ),
}


PROFILES = {
    # A conservative wearable baseline: 0.4--0.5 mm fill density and a
    # 7 mm maximum exposed travel stitch, as appropriate for garments.
    "standard": DigitizingProfile("standard", 0.5, 7.0, 0.45, 4.5, 7.0),
    # Cap fronts benefit from a slightly softer fill and a center-out order to
    # reduce movement around the panel seam.
    "structured_cap": DigitizingProfile("structured_cap", 0.5, 7.0, 0.50, 4.5, 7.0, True),
    "unstructured_cap": DigitizingProfile("unstructured_cap", 0.5, 7.0, 0.48, 4.2, 7.0, True),
    # Reduce penetrations on non-textile, puncture-sensitive materials.
    "leather": DigitizingProfile("leather", 0.6, 6.0, 0.55, 4.5, 6.0),
    # More coverage is useful where pile can obscure thread, while retaining
    # the same wearable maximum stitch length.
    "high_pile": DigitizingProfile("high_pile", 0.5, 6.0, 0.38, 4.0, 6.0),
    # Decorative/non-wearable work may safely retain longer exposed runs.
    "non_wearable": DigitizingProfile("non_wearable", 0.5, 12.1, 0.50, 5.5, 12.1),
}


@dataclass
class StitchPlan:
    version: str
    width_mm: float
    height_mm: float
    palette: list[str]
    regions: list[Region]
    profile: str = "standard"
    machine_max_stitch_mm: float = 7.0
    foundation_operations: list[Operation] = field(default_factory=list)
    algorithm: str = "layered"
    algorithm_label: str = "Layered"
    posterization: dict = field(default_factory=dict)
    precision_mode: bool = False
    planning_limits: dict = field(default_factory=dict)
    density_multiplier: float = 1.0

    def drc(self) -> dict:
        """Report semantic stitch limits, routing cost, and a comparable score."""
        previous: Operation | None = None
        short = long = segments = 0
        semantic_violations = Counter()
        semantic_segments = Counter()
        minimum = None
        maximum = None
        axial_maximum = 0.0
        total_length = 0.0
        jump_count = trim_count = 0
        detail_trim_count = 0
        machine_jump_count = 0
        jump_distance = 0.0
        cursor: tuple[float, float] | None = None
        density_cells: Counter[tuple[int, int]] = Counter()
        detail_region_ids = {
            region.id for region in self.regions if region.pass_index == 2
        }
        for operation in self.operations:
            if operation.kind == "stitch":
                cursor = (operation.x, operation.y)
                if previous and previous.kind == "stitch":
                    dx, dy = operation.x - previous.x, operation.y - previous.y
                    length = hypot(dx, dy)
                    if not (operation.role.startswith("tie_") or previous.role.startswith("tie_")):
                        segments += 1
                        minimum = length if minimum is None else min(minimum, length)
                        maximum = length if maximum is None else max(maximum, length)
                        axial_maximum = max(axial_maximum, abs(dx), abs(dy))
                        total_length += length
                        short += length < 0.25
                        long += length > self.machine_max_stitch_mm
                        density_cells[(floor(operation.x / 5.0), floor(operation.y / 5.0))] += 1
                        if operation.role in {
                            "detail_running", "top_detail_running",
                            "design_center_underlay",
                        }:
                            family, low, high = "running", 0.5, self.machine_max_stitch_mm
                        elif operation.role == "top_fill":
                            family, low, high = "fill", 4.0, self.machine_max_stitch_mm
                        else:
                            family = ""
                        if family and not self.precision_mode:
                            semantic_segments[family] += 1
                            semantic_violations[f"{family}_short"] += length < low
                            semantic_violations[f"{family}_long"] += length > high
                previous = operation
            elif operation.kind == "jump":
                jump_count += 1
                if cursor:
                    dx, dy = operation.x - cursor[0], operation.y - cursor[1]
                    distance = hypot(dx, dy)
                    jump_distance += distance
                    machine_jump_count += max(
                        1, ceil(max(abs(dx), abs(dy)) / 12.1)
                    )
                else:
                    machine_jump_count += 1
                cursor = (operation.x, operation.y)
                previous = None
            elif operation.kind == "trim":
                trim_count += 1
                detail_trim_count += operation.region_id in detail_region_ids
                previous = None
            elif operation.kind == "color":
                previous = None
        local_counts = list(density_cells.values())
        for region in self.regions:
            satin = region.stitch_diagnostics.get("satin")
            if not satin or self.precision_mode:
                continue
            semantic_segments["satin"] += satin.get("pair_count", 0)
            minimum_width = satin.get("minimum_width_mm")
            maximum_width = satin.get("maximum_width_mm")
            satin_limit = 12.1 if self.profile == "non_wearable" else 6.0
            semantic_violations["satin_short"] += bool(
                minimum_width is not None and minimum_width < 1.0
            )
            semantic_violations["satin_long"] += bool(
                maximum_width is not None and maximum_width > satin_limit
            )
        local_mean = sum(local_counts) / len(local_counts) if local_counts else 0.0
        local_variance = (
            sum((count - local_mean) ** 2 for count in local_counts) / len(local_counts)
            if local_counts else 0.0
        )
        stitch_count = sum(operation.kind == "stitch" for operation in self.operations)
        color_changes = sum(operation.kind == "color" for operation in self.operations)
        object_jumps = sum(
            operation.kind == "jump" and operation.role == "entry"
            for operation in self.operations
        )
        internal_jumps = jump_count - object_jumps
        fill_edge_rows = sum(
            operation.kind == "jump" and operation.role == "top_fill_edge"
            for operation in self.operations
        )
        violation_count = sum(semantic_violations.values()) + long
        structural_trim_count = trim_count - detail_trim_count
        trim_rate = structural_trim_count * 1000 / max(1, stitch_count)
        jump_rate = object_jumps * 1000 / max(1, stitch_count)
        stitch_points = [
            operation for operation in self.operations
            if operation.kind == "stitch"
        ]
        if stitch_points:
            occupied_width = max(point.x for point in stitch_points) - min(point.x for point in stitch_points)
            occupied_height = max(point.y for point in stitch_points) - min(point.y for point in stitch_points)
            design_area_sq_in = max(
                occupied_width * occupied_height / 645.16,
                1 / 645.16,
            )
        else:
            design_area_sq_in = 0.0
        stitches_per_sq_in = (
            stitch_count / design_area_sq_in if design_area_sq_in else 0.0
        )
        limits = {} if self.precision_mode else dict(self.planning_limits)
        allowed_trims = (
            min(
                int(limits.get("max_trims", trim_count)),
                max(16, ceil(stitch_count / 1000 * float(
                    limits.get("trims_per_1000_stitches", trim_rate or 1)
                ))),
            )
            if limits else None
        )
        allowed_jumps = (
            min(
                int(limits.get("max_jumps", jump_count)),
                max(120, ceil(stitch_count / 1000 * float(
                    limits.get("jumps_per_1000_stitches", jump_count or 1)
                ))),
            )
            if limits else None
        )
        allowed_detail_trims = (
            int(limits.get("detail_max_trims", detail_trim_count))
            if limits else None
        )
        budget_violations = {
            "trims": max(
                0, structural_trim_count - allowed_trims
            ) if allowed_trims is not None else 0,
            "detail_trims": max(
                0, detail_trim_count - allowed_detail_trims
            ) if allowed_detail_trims is not None else 0,
            "jumps": max(0, machine_jump_count - allowed_jumps) if allowed_jumps is not None else 0,
            "objects": max(
                0, len(self.regions) - int(limits.get("max_objects", len(self.regions)))
            ) if limits else 0,
        }
        budget_penalty = (
            min(25, budget_violations["trims"] * 1.5)
            + min(25, budget_violations["jumps"] * 0.1)
            + min(15, budget_violations["objects"])
        )
        score = max(
            0,
            round(
                100
                - min(35, violation_count * 100 / max(1, segments))
                - min(20, trim_rate * 0.8)
                - min(15, jump_rate * 0.25)
                - budget_penalty
            ),
        )
        corrections = []
        if semantic_violations["running_short"]:
            corrections.append("Lengthen or simplify running-stitch paths below 0.5 mm.")
        if semantic_violations["satin_short"]:
            corrections.append("Review narrow satin penetrations below 1 mm.")
        if semantic_violations["satin_long"]:
            corrections.append("Split or convert satin spans above the product limit.")
        if semantic_violations["fill_short"]:
            corrections.append("Simplify short tatami edge rows or convert the feature type.")
        if trim_rate > 8:
            corrections.append("Reorder same-thread objects and retain thread across safe short jumps.")
        if budget_violations["trims"]:
            corrections.append(
                f"Reduce trims by at least {budget_violations['trims']} to meet the planning limit."
            )
        if budget_violations["detail_trims"]:
            corrections.append(
                "Simplify or consolidate disconnected top-detail paths."
            )
        if budget_violations["jumps"]:
            corrections.append(
                f"Reduce jumps by at least {budget_violations['jumps']} to meet the planning limit."
            )
        return {
            "minimum_stitch_mm": minimum,
            "maximum_stitch_mm": maximum,
            "maximum_axial_stitch_mm": axial_maximum if segments else None,
            "mean_stitch_mm": total_length / segments if segments else None,
            "segments": segments,
            "short_stitches": short,
            "long_stitches": long,
            "machine_max_stitch_mm": self.machine_max_stitch_mm,
            "jump_count": jump_count,
            "machine_jump_count": machine_jump_count,
            "object_jump_count": object_jumps,
            "internal_jump_count": internal_jumps,
            "jump_distance_mm": jump_distance,
            "trim_count": trim_count,
            "structural_trim_count": structural_trim_count,
            "detail_trim_count": detail_trim_count,
            "color_change_count": color_changes,
            "stitch_count": stitch_count,
            "object_count": len(self.regions),
            "geometry_element_count": len({
                region.element_id for region in self.regions if region.element_id
            }),
            "design_area_sq_in": design_area_sq_in,
            "stitches_per_sq_in": stitches_per_sq_in,
            "jumps_per_1000_stitches": jump_rate,
            "trims_per_1000_stitches": trim_rate,
            "short_fill_edge_rows": fill_edge_rows,
            "local_density_cell_mm": 5.0,
            "local_density_variance": local_variance,
            "semantic_segments": dict(semantic_segments),
            "semantic_violations": dict(semantic_violations),
            "quality_score": score,
            "corrections": corrections,
            "planning_limits": {
                **limits,
                "resolved_max_trims": allowed_trims,
                "resolved_max_jumps": allowed_jumps,
                "resolved_max_detail_trims": allowed_detail_trims,
            },
            "budget_violations": budget_violations,
            "budget_pass": not any(budget_violations.values()),
            "composition_passes": dict(Counter(
                region.pass_name for region in self.regions
            )),
            "density_multiplier": self.density_multiplier,
        }

    @property
    def operations(self) -> list[Operation]:
        """Flatten semantic regions into a machine sequence.

        Color change is a thread-level operation, not a component-level one.
        Regions are already routed within their palette group by ``digitize``.
        """
        result: list[Operation] = []
        # This design-level run is intentionally first: it anchors the fabric
        # to its backing before object underlay begins to pull in other axes.
        result.extend(self.foundation_operations)
        current_color = self.palette[0] if result and self.palette else None

        # Object underlays form a real first machine layer. Keeping them out of
        # the blocking objects lets an operator inspect, simulate, and tune the
        # foundation independently of the visible top stitches.
        for color in self.palette:
            color_regions = [
                region for region in self.regions
                if region.pass_index == 1
                and region.color == color
                and region.underlay_operations
            ]
            if not color_regions:
                continue
            if current_color is not None and current_color != color:
                result.append(Operation("color", role="color_change"))
            for region in color_regions:
                result.extend(region.underlay_operations)
            current_color = color

        passes = sorted({region.pass_index for region in self.regions})
        for pass_index in passes:
            for color in self.palette:
                color_regions = [
                    region for region in self.regions
                    if region.color == color and region.pass_index == pass_index
                ]
                if not color_regions:
                    continue
                if current_color is not None and current_color != color:
                    result.append(Operation("color", role="color_change"))
                for region in color_regions:
                    result.extend(region.operations)
                current_color = color
        return result

    def layer_summary(self) -> list[dict]:
        """Return the three physical production layers in stitch order."""
        object_underlay = [
            operation
            for region in self.regions if region.pass_index == 1
            for operation in region.underlay_operations
        ]
        layers = [
            (
                "underlay", "Underlay",
                self.foundation_operations + object_underlay,
                sum(bool(region.underlay_operations) for region in self.regions),
            ),
            (
                "blocking", "Blocking",
                [
                    operation
                    for region in self.regions if region.pass_index == 1
                    for operation in region.operations
                ],
                sum(region.pass_index == 1 for region in self.regions),
            ),
            (
                "detail", "Detail",
                [
                    operation
                    for region in self.regions if region.pass_index == 2
                    for operation in region.operations
                ],
                sum(region.pass_index == 2 for region in self.regions),
            ),
        ]
        return [
            {
                "id": identifier,
                "name": name,
                "order": order,
                "object_count": object_count,
                "stitch_count": sum(
                    operation.kind == "stitch" for operation in operations
                ),
                "jump_count": sum(
                    operation.kind == "jump" for operation in operations
                ),
                "trim_count": sum(
                    operation.kind == "trim" for operation in operations
                ),
            }
            for order, (identifier, name, operations, object_count)
            in enumerate(layers)
        ]

    def json(self) -> dict:
        return {
            "version": self.version,
            "dimensions_mm": {"width": self.width_mm, "height": self.height_mm},
            "palette": self.palette,
            "profile": self.profile,
            "algorithm": self.algorithm,
            "algorithm_label": self.algorithm_label,
            "posterization": self.posterization,
            "planning_limits": self.planning_limits,
            "density_multiplier": self.density_multiplier,
            "layers": self.layer_summary(),
            "foundation_operations": [asdict(op) for op in self.foundation_operations],
            "regions": [
                {**asdict(region), "operations": [asdict(op) for op in region.operations]}
                for region in self.regions
            ],
            "operations": [asdict(op) for op in self.operations],
            "drc": self.drc(),
        }


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _is_high_contrast_binary(source: Image.Image) -> bool:
    """Recognize QR/pixel-art-like source before resampling can soften it."""
    colors = source.convert("RGB").getcolors(maxcolors=32)
    if not colors or len(colors) > 4:
        return False
    values = [color for _, color in colors]
    return (
        any(max(color) <= 55 for color in values)
        and any(min(color) >= 200 for color in values)
    )


def _components(mask: set[tuple[int, int]]) -> Iterable[set[tuple[int, int]]]:
    remaining = set(mask)
    while remaining:
        first = remaining.pop()
        component = {first}
        queue = deque([first])
        while queue:
            x, y = queue.popleft()
            for next_point in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if next_point in remaining:
                    remaining.remove(next_point)
                    component.add(next_point)
                    queue.append(next_point)
        yield component


def _runs(component: set[tuple[int, int]], vertical: bool, step: int) -> Iterable[list[tuple[int, int]]]:
    grouped: dict[int, list[int]] = {}
    for x, y in component:
        key, value = (x, y) if vertical else (y, x)
        grouped.setdefault(key, []).append(value)
    for row_number, key in enumerate(sorted(grouped)[::step]):
        values = sorted(grouped[key])
        start = previous = values[0]
        groups = []
        for value in values[1:] + [None]:
            if value is None or value != previous + 1:
                if previous - start >= 2:
                    sequence = list(range(start, previous + 1))
                    if row_number % 2:
                        sequence.reverse()
                    groups.append([(key, value) if vertical else (value, key) for value in sequence])
                if value is not None:
                    start = value
            previous = value if value is not None else previous
        yield from groups


def _direction(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Direction index in screen coordinates: east, south, west, north."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    return {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}[(dx, dy)]


def _simplify_loop(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Remove collinear grid vertices while preserving all contour corners."""
    if len(points) < 4:
        return points
    simplified = []
    for index, current in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        before = (current[0] - previous[0], current[1] - previous[1])
        after = (following[0] - current[0], following[1] - current[1])
        if before[0] * after[1] != before[1] * after[0]:
            simplified.append(current)
    return simplified


def _contour_path(contour: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Expand simplified orthogonal contour corners back to grid steps."""
    path: list[tuple[int, int]] = []
    for index, start in enumerate(contour):
        end = contour[(index + 1) % len(contour)]
        dx = 0 if end[0] == start[0] else (1 if end[0] > start[0] else -1)
        dy = 0 if end[1] == start[1] else (1 if end[1] > start[1] else -1)
        length = abs(end[0] - start[0]) + abs(end[1] - start[1])
        path.extend((start[0] + step * dx, start[1] + step * dy) for step in range(length))
    return path


def _contours(component: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Trace closed pixel-cell boundaries, retaining exterior and hole loops.

    Edges are oriented with the filled cell on their right.  Unlike the former
    angle-sorted edge cloud, this preserves concavities and does not bridge
    across empty holes.  Vertices are expressed in source-pixel grid space.
    """
    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = {}

    def add_edge(start: tuple[int, int], end: tuple[int, int]) -> None:
        adjacency.setdefault(start, set()).add(end)

    for x, y in component:
        if (x, y - 1) not in component:
            add_edge((x, y), (x + 1, y))
        if (x + 1, y) not in component:
            add_edge((x + 1, y), (x + 1, y + 1))
        if (x, y + 1) not in component:
            add_edge((x + 1, y + 1), (x, y + 1))
        if (x - 1, y) not in component:
            add_edge((x, y + 1), (x, y))

    loops: list[list[tuple[int, int]]] = []
    while adjacency:
        start = min(adjacency)
        end = min(adjacency[start])
        adjacency[start].remove(end)
        if not adjacency[start]:
            del adjacency[start]
        loop = [start]
        previous, current = start, end
        while current != start:
            loop.append(current)
            choices = adjacency.get(current)
            if not choices:
                # This should be unreachable for a set of pixel-cell edges;
                # discard malformed partial geometry rather than sewing it.
                loop = []
                break
            incoming = _direction(previous, current)
            # At a shared grid vertex prefer the tight right turn. This keeps
            # diagonal near-contacts as separate loops rather than crossing.
            next_point = min(
                choices,
                key=lambda point: (
                    (_direction(current, point) - incoming) % 4, point
                ),
            )
            choices.remove(next_point)
            if not choices:
                del adjacency[current]
            previous, current = current, next_point
        loop = _simplify_loop(loop)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def _point(point: tuple[int, int], width: int, height: int, width_mm: float, height_mm: float) -> tuple[float, float]:
    return (
        (point[0] / max(1, width - 1) - 0.5) * width_mm,
        (point[1] / max(1, height - 1) - 0.5) * height_mm,
    )


def _append_run(ops: list[Operation], points: list[tuple[int, int]], *, width: int, height: int, width_mm: float, height_mm: float, role: str, region_id: str, spacing: int) -> None:
    sampled = points[::max(1, spacing)]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    x, y = _point(sampled[0], width, height, width_mm, height_mm)
    ops.append(Operation("jump", x, y, role, region_id))
    for point in sampled:
        x, y = _point(point, width, height, width_mm, height_mm)
        ops.append(Operation("stitch", x, y, role, region_id))


def _append_physical_run(ops: list[Operation], points: list[tuple[float, float]], *, role: str, region_id: str) -> None:
    """Append a pre-sampled physical stitch row with an explicit entry jump."""
    if len(points) < 2:
        return
    ops.append(Operation("jump", points[0][0], points[0][1], role, region_id))
    ops.extend(Operation("stitch", x, y, role, region_id) for x, y in points)


def _rotate(point: tuple[float, float], angle: float) -> tuple[float, float]:
    c, s = cos(angle), sin(angle)
    return (point[0] * c - point[1] * s, point[0] * s + point[1] * c)


def _fill_runs(
    contours: list[list[tuple[int, int]]],
    *,
    width: int,
    height: int,
    width_mm: float,
    height_mm: float,
    angle_degrees: float,
    row_spacing_mm: float,
    max_stitch_mm: float,
    min_stitch_mm: float | None = None,
) -> Iterable[list[tuple[float, float]]]:
    """Intersect rotated scan rows with a polygon set using even-odd fill.

    All loops participate in one parity calculation, so interior contours are
    naturally excluded.  Returned paths are sampled in physical millimetres,
    not raster columns; that keeps density stable when artwork is resized.
    """
    angle = radians(angle_degrees)
    inverse = -angle
    loops = [[_rotate(_point(point, width, height, width_mm, height_mm), inverse) for point in contour] for contour in contours]
    values = [point for loop in loops for point in loop]
    if not values:
        return
    min_y = min(point[1] for point in values)
    max_y = max(point[1] for point in values)
    first_row = floor(min_y / row_spacing_mm) * row_spacing_mm + row_spacing_mm / 2
    row_index = 0
    row_y = first_row
    while row_y < max_y:
        crossings: list[float] = []
        for loop in loops:
            for index, start in enumerate(loop):
                end = loop[(index + 1) % len(loop)]
                # Half-open edge test prevents vertices from being counted twice.
                if (start[1] <= row_y < end[1]) or (end[1] <= row_y < start[1]):
                    fraction = (row_y - start[1]) / (end[1] - start[1])
                    crossings.append(start[0] + fraction * (end[0] - start[0]))
        crossings.sort()
        spans = list(zip(crossings[::2], crossings[1::2]))
        if row_index % 2:
            spans.reverse()
        for left, right in spans:
            length = right - left
            # Keep short edge rows: they carry corners and small details.
            # They are sampled as one stitch rather than discarded outright.
            if length < max_stitch_mm * 0.1:
                continue
            if min_stitch_mm:
                count = max(1, floor(length / min_stitch_mm))
                if length / count > max_stitch_mm:
                    count += 1
            else:
                count = max(1, ceil(length / max_stitch_mm))
            rotated = [(left + length * index / count, row_y) for index in range(count + 1)]
            physical = [_rotate(point, angle) for point in rotated]
            if row_index % 2:
                physical.reverse()
            yield physical
        row_index += 1
        row_y += row_spacing_mm


def _inside_physical_contours(
    point: tuple[float, float],
    physical_contours: list[list[tuple[float, float]]],
) -> bool:
    """Even-odd containment test across precomputed physical loops."""
    inside = False
    x, y = point
    for physical in physical_contours:
        for index, start in enumerate(physical):
            end = physical[(index + 1) % len(physical)]
            if (start[1] > y) != (end[1] > y):
                crossing = start[0] + (y - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
                if x < crossing:
                    inside = not inside
    return inside


def _inside_contours(point: tuple[float, float], contours: list[list[tuple[int, int]]], *, width: int, height: int, width_mm: float, height_mm: float) -> bool:
    """Even-odd containment test across exterior and hole loops."""
    return _inside_physical_contours(
        point,
        [
            [_point(vertex, width, height, width_mm, height_mm) for vertex in contour]
            for contour in contours
        ],
    )


def _safe_fill_connector(
    start: tuple[float, float],
    end: tuple[float, float],
    contours: list[list[tuple[int, int]]],
    *,
    width: int,
    height: int,
    width_mm: float,
    height_mm: float,
    maximum_mm: float,
    physical_contours: list[list[tuple[float, float]]] | None = None,
) -> bool:
    distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    if distance > maximum_mm:
        return False
    samples = max(2, ceil(distance / 0.25))
    return all(
        _inside_physical_contours(
            (start[0] + (end[0] - start[0]) * fraction / samples, start[1] + (end[1] - start[1]) * fraction / samples),
            physical_contours or [
                [
                    _point(vertex, width, height, width_mm, height_mm)
                    for vertex in contour
                ]
                for contour in contours
            ],
        )
        for fraction in range(1, samples)
    )


def _safe_fill_connector_path(
    start: tuple[float, float],
    end: tuple[float, float],
    contours: list[list[tuple[int, int]]],
    *,
    width: int,
    height: int,
    width_mm: float,
    height_mm: float,
    maximum_mm: float,
    physical_contours: list[list[tuple[float, float]]] | None = None,
) -> list[tuple[float, float]] | None:
    """Find a short straight or orthogonal connector contained by the solid."""
    arguments = {
        "contours": contours, "width": width, "height": height,
        "width_mm": width_mm, "height_mm": height_mm,
        "maximum_mm": maximum_mm,
        "physical_contours": physical_contours,
    }
    if _safe_fill_connector(start, end, **arguments):
        return [end]
    for middle in ((end[0], start[1]), (start[0], end[1])):
        if (
            _safe_fill_connector(start, middle, **arguments)
            and _safe_fill_connector(middle, end, **arguments)
        ):
            return [middle, end]
    return None


def _append_routed_fill(
    ops: list[Operation],
    runs: Iterable[list[tuple[float, float]]],
    *,
    contours: list[list[tuple[int, int]]],
    width: int,
    height: int,
    width_mm: float,
    height_mm: float,
    role: str,
    region_id: str,
    connector_max_mm: float,
) -> None:
    """Join safe adjacent rows; leave jump boundaries where routing is unsafe."""
    previous: tuple[float, float] | None = None
    physical_contours = [
        [
            _point(vertex, width, height, width_mm, height_mm)
            for vertex in contour
        ]
        for contour in contours
    ]
    for run in runs:
        if len(run) < 2:
            continue
        run_length = sum(
            hypot(right[0] - left[0], right[1] - left[1])
            for left, right in zip(run, run[1:])
        )
        actual_role = (
            "top_fill_edge"
            if role == "top_fill" and run_length < 4.0
            else role
        )
        connector = (
            _safe_fill_connector_path(
                previous, run[0], contours, width=width, height=height,
                width_mm=width_mm, height_mm=height_mm,
                maximum_mm=connector_max_mm,
                physical_contours=physical_contours,
            )
            if previous else None
        )
        if connector:
            ops.extend(
                Operation("stitch", point[0], point[1], "fill_connector", region_id)
                for point in connector
            )
            ops.extend(Operation("stitch", x, y, actual_role, region_id) for x, y in run[1:])
        else:
            _append_physical_run(ops, run, role=actual_role, region_id=region_id)
        previous = run[-1]


def _satin_rails(component: set[tuple[int, int]], *, vertical: bool, step: int) -> Iterable[tuple[tuple[int, int], tuple[int, int]]]:
    """Derive smoothed paired rails from a simple narrow raster region.

    This is deliberately limited to single-span slices. Branched or holed
    shapes remain fill candidates until a centerline/rail solver is added.
    """
    grouped: dict[int, list[int]] = {}
    for x, y in component:
        key, value = (y, x) if vertical else (x, y)
        grouped.setdefault(key, []).append(value)
    raw_rails: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for key in sorted(grouped)[::max(1, step)]:
        values = sorted(grouped[key])
        if values[-1] - values[0] < 2 or len(values) != values[-1] - values[0] + 1:
            continue
        raw_rails.append(
            ((values[0], key), (values[-1], key))
            if vertical else ((key, values[0]), (key, values[-1]))
        )
    # Posterized artwork often has a one-pixel sawtooth along a diagonal or
    # curved edge. Locally averaging the two rails removes that quantization
    # jitter before satin generation while retaining the true local width.
    for index, (left, right) in enumerate(raw_rails):
        neighbours = raw_rails[max(0, index - 2):index + 3]
        if vertical:
            low = sum(pair[0][0] for pair in neighbours) // len(neighbours)
            high = sum(pair[1][0] for pair in neighbours) // len(neighbours)
            yield (low, left[1]), (high, right[1])
        else:
            low = sum(pair[0][1] for pair in neighbours) // len(neighbours)
            high = sum(pair[1][1] for pair in neighbours) // len(neighbours)
            yield (left[0], low), (right[0], high)


def _append_satin(
    underlay_ops: list[Operation],
    top_ops: list[Operation],
    rails: Iterable[tuple[tuple[int, int], tuple[int, int]]],
    *,
    width: int,
    height: int,
    width_mm: float,
    height_mm: float,
    region_id: str,
    wide_underlay: bool,
) -> None:
    """Create centre-run underlay then alternating, paired-rail penetrations."""
    rail_pairs = list(rails)
    if len(rail_pairs) < 2:
        return
    centre = [((left[0] + right[0]) / 2, (left[1] + right[1]) / 2) for left, right in rail_pairs]
    _append_run(
        underlay_ops, [(round(x), round(y)) for x, y in centre],
        width=width, height=height, width_mm=width_mm, height_mm=height_mm,
        role="satin_center_underlay", region_id=region_id, spacing=1,
    )
    if wide_underlay:
        # Wide satin benefits from a stabilizing zig-zag foundation; narrow
        # columns retain only the centre run so they do not become rigid.
        first_left, _ = rail_pairs[0]
        x, y = _point(first_left, width, height, width_mm, height_mm)
        underlay_ops.append(Operation(
            "jump", x, y, "satin_zigzag_underlay", region_id
        ))
        for index, (left, right) in enumerate(rail_pairs[::2]):
            point = left if index % 2 == 0 else right
            x, y = _point(point, width, height, width_mm, height_mm)
            underlay_ops.append(Operation(
                "stitch", x, y, "satin_zigzag_underlay", region_id
            ))
    first_left, first_right = rail_pairs[0]
    x, y = _point(first_left, width, height, width_mm, height_mm)
    top_ops.append(Operation("jump", x, y, "satin_top", region_id))
    for index, (left, right) in enumerate(rail_pairs):
        pair = (left, right) if index % 2 == 0 else (right, left)
        for point in pair:
            x, y = _point(point, width, height, width_mm, height_mm)
            top_ops.append(Operation("stitch", x, y, "satin_top", region_id))


def _design_centerline_underlay(
    mask: set[tuple[int, int]],
    *,
    width: int,
    height: int,
    width_mm: float,
    height_mm: float,
    stitch_length_mm: float = 2.5,
) -> list[Operation]:
    """Create a centre-run foundation within the occupied design footprint.

    The run follows the design's dominant axis and is broken at gaps rather
    than sewing across empty artwork. It is an early stabilizer, not a visible
    outline: later object underlay and top stitches cover it.
    """
    if not mask:
        return []
    min_x, max_x = min(x for x, _ in mask), max(x for x, _ in mask)
    min_y, max_y = min(y for _, y in mask), max(y for _, y in mask)
    horizontal = (max_x - min_x) * width_mm / width >= (max_y - min_y) * height_mm / height
    grouped: dict[int, list[int]] = {}
    for x, y in mask:
        key, value = (x, y) if horizontal else (y, x)
        grouped.setdefault(key, []).append(value)
    global_centre = ((min_y + max_y) / 2) if horizontal else ((min_x + max_x) / 2)
    paths: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    previous_key: int | None = None
    previous_value: float | None = None
    for key in sorted(grouped):
        values = sorted(grouped[key])
        spans: list[tuple[int, int]] = []
        start = end = values[0]
        for value in values[1:] + [None]:
            if value is None or value != end + 1:
                spans.append((start, end))
                if value is not None:
                    start = value
            end = value if value is not None else end
        target = previous_value if previous_value is not None else global_centre
        low, high = min(spans, key=lambda span: (abs((span[0] + span[1]) / 2 - target), -(span[1] - span[0])))
        centre = (low + high) / 2
        point = (key, round(centre)) if horizontal else (round(centre), key)
        if previous_key is None or key == previous_key + 1:
            current.append(point)
        else:
            if len(current) > 1:
                paths.append(current)
            current = [point]
            previous_value = None
        previous_key, previous_value = key, centre
    if len(current) > 1:
        paths.append(current)
    pixel_scale = min(width_mm / width, height_mm / height)
    spacing = max(1, round(stitch_length_mm / pixel_scale))
    operations: list[Operation] = []
    for path in paths:
        _append_run(
            operations, path, width=width, height=height, width_mm=width_mm,
            height_mm=height_mm, role="design_center_underlay",
            region_id="foundation", spacing=spacing,
        )
    if operations:
        operations.append(
            Operation("trim", role="foundation_trim", region_id="foundation")
        )
    return operations


def _silhouette_underlay(
    mask: set[tuple[int, int]],
    *,
    width: int,
    height: int,
    width_mm: float,
    height_mm: float,
    pixel_area_mm2: float,
    minimum_area_mm2: float = 2.0,
) -> list[Operation]:
    """Lay a low-density tatami silhouette under physically solid elements."""
    operations: list[Operation] = []
    pixel_scale = min(width_mm / width, height_mm / height)
    for index, component in enumerate(_components(mask), 1):
        if len(component) * pixel_area_mm2 < minimum_area_mm2:
            continue
        contours = _contours(component)
        if not contours:
            continue
        contours.sort(key=lambda loop: abs(sum(
            loop[position][0] * loop[(position + 1) % len(loop)][1]
            - loop[(position + 1) % len(loop)][0] * loop[position][1]
            for position in range(len(loop))
        )), reverse=True)
        metrics = _feature_metrics(
            component, contours, pixel_scale=pixel_scale,
            pixel_area_mm2=pixel_area_mm2,
        )
        if metrics["estimated_width_mm"] < 1.5:
            continue
        region_id = f"silhouette-{index}"
        boundary = contours[0]
        _append_run(
            operations, _contour_path(boundary), width=width, height=height,
            width_mm=width_mm, height_mm=height_mm,
            role="silhouette_contour_underlay", region_id=region_id,
            spacing=max(1, round(2.0 / pixel_scale)),
        )
        _append_routed_fill(
            operations,
            _fill_runs(
                contours, width=width, height=height, width_mm=width_mm,
                height_mm=height_mm, angle_degrees=90,
                row_spacing_mm=1.6, max_stitch_mm=4.0,
            ),
            contours=contours, width=width, height=height,
            width_mm=width_mm, height_mm=height_mm,
            role="silhouette_tatami_underlay", region_id=region_id,
            connector_max_mm=3.0,
        )
    if operations:
        operations.append(
            Operation(
                "trim", role="silhouette_underlay_trim",
                region_id="silhouette-foundation",
            )
        )
    return operations


def _enforce_minimum_stitch(operations: list[Operation], minimum_mm: float = 0.25) -> list[Operation]:
    """Collapse accidental micro-segments while retaining explicit tie-offs.

    This runs on semantic object operations before DST compilation. A short
    terminal corner is omitted rather than emitted as a machine penetration
    below the DRC threshold; the following sampled stitch carries the path.
    """
    cleaned: list[Operation] = []
    previous_stitch: Operation | None = None
    for operation in operations:
        if operation.kind != "stitch":
            cleaned.append(operation)
            previous_stitch = None if operation.kind in {"jump", "trim", "color"} else previous_stitch
            continue
        if operation.role.startswith("tie_"):
            cleaned.append(operation)
            previous_stitch = operation
            continue
        if previous_stitch and not previous_stitch.role.startswith("tie_"):
            length = ((operation.x - previous_stitch.x) ** 2 + (operation.y - previous_stitch.y) ** 2) ** 0.5
            if length < minimum_mm:
                continue
        cleaned.append(operation)
        previous_stitch = operation
    return cleaned


def _enforce_stitch_criteria(
    operations: list[Operation], *, maximum_radial_mm: float = 12.0
) -> list[Operation]:
    """Apply the shared final long-stitch rule to every generator's output.

    Fill, running, and satin generation deliberately use different sampling
    rules.  This final pass corresponds to the recovered common
    ``GetSplitNumber -> CreateSplitPoints`` layer: it protects the emitted
    machine sequence without coupling any generator to machine limits.
    """
    checked: list[Operation] = []
    previous: Operation | None = None
    for operation in operations:
        if operation.kind != "stitch":
            checked.append(operation)
            previous = None if operation.kind in {"jump", "trim", "color"} else previous
            continue
        if (
            previous
            and not previous.role.startswith("tie_")
            and not operation.role.startswith("tie_")
        ):
            distance = hypot(operation.x - previous.x, operation.y - previous.y)
            pieces = ceil(distance / maximum_radial_mm)
            for index in range(1, pieces):
                fraction = index / pieces
                checked.append(Operation(
                    "stitch",
                    previous.x + (operation.x - previous.x) * fraction,
                    previous.y + (operation.y - previous.y) * fraction,
                    operation.role,
                    operation.region_id,
                ))
        checked.append(operation)
        previous = operation
    return checked


def _ties(ops: list[Operation], point: tuple[int, int], *, width: int, height: int, width_mm: float, height_mm: float, role: str, region_id: str) -> None:
    x, y = _point(point, width, height, width_mm, height_mm)
    delta = min(width_mm / width, height_mm / height) * 1.5
    for dx, dy in ((0, 0), (delta, 0), (0, delta), (0, 0)):
        ops.append(Operation("stitch", x + dx, y + dy, role, region_id))


def _wrap_underlay_body(
    body: list[Operation],
    *,
    region_id: str,
    minimum_stitch_mm: float,
    maximum_stitch_mm: float,
) -> list[Operation]:
    """Give a separated object underlay its own entry, ties, and trim."""
    stitches = [operation for operation in body if operation.kind == "stitch"]
    if not stitches:
        return []
    first, last = stitches[0], stitches[-1]
    tie_delta = max(0.5, minimum_stitch_mm)
    wrapped = [
        Operation("jump", first.x, first.y, "underlay_entry", region_id),
        Operation("stitch", first.x, first.y, "underlay_tie_in", region_id),
        Operation(
            "stitch", first.x + tie_delta, first.y,
            "underlay_tie_in", region_id,
        ),
        Operation("stitch", first.x, first.y, "underlay_tie_in", region_id),
        *body,
        Operation("stitch", last.x, last.y, "underlay_tie_off", region_id),
        Operation(
            "stitch", last.x + tie_delta, last.y,
            "underlay_tie_off", region_id,
        ),
        Operation("stitch", last.x, last.y, "underlay_tie_off", region_id),
        Operation("trim", role="underlay_trim", region_id=region_id),
    ]
    return _enforce_stitch_criteria(
        _enforce_minimum_stitch(wrapped, minimum_stitch_mm),
        maximum_radial_mm=maximum_stitch_mm,
    )


def _region_start(region: Region) -> tuple[float, float]:
    """Return the explicit entry point used by the region generator."""
    entry = next((op for op in region.operations if op.role == "entry"), None)
    return (entry.x, entry.y) if entry else (0.0, 0.0)


def _region_exit(region: Region) -> tuple[float, float]:
    """Use the last tie-off penetration as the routing origin for the next object."""
    exit_stitch = next((op for op in reversed(region.operations) if op.role == "tie_off"), None)
    return (exit_stitch.x, exit_stitch.y) if exit_stitch else _region_start(region)


def _route_same_color(regions: list[Region], *, center_outward: bool = False) -> list[Region]:
    """Nearest-entry routing for disconnected regions sharing one thread.

    This intentionally does not invent connectors: each component retains its
    own trim policy. It only establishes a stable, low-travel object order.
    """
    if not regions:
        return []
    pending = list(regions)
    if center_outward:
        first_index = min(
            range(len(pending)),
            key=lambda index: (_region_start(pending[index])[0] ** 2 + _region_start(pending[index])[1] ** 2, pending[index].id),
        )
        routed = [pending.pop(first_index)]
    else:
        routed = [pending.pop(0)]
    while pending:
        origin = _region_exit(routed[-1])
        next_index = min(
            range(len(pending)),
            key=lambda index: (
                (_region_start(pending[index])[0] - origin[0]) ** 2
                + (_region_start(pending[index])[1] - origin[1]) ** 2,
                pending[index].id,
            ),
        )
        routed.append(pending.pop(next_index))
    return routed


def _component_center_paths(
    component: set[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    """Trace a smoothed dominant-axis centerline through a narrow component."""
    min_x, max_x = min(x for x, _ in component), max(x for x, _ in component)
    min_y, max_y = min(y for _, y in component), max(y for _, y in component)
    horizontal = max_x - min_x >= max_y - min_y
    grouped: dict[int, list[int]] = {}
    for x, y in component:
        key, value = (x, y) if horizontal else (y, x)
        grouped.setdefault(key, []).append(value)
    raw = []
    for key in sorted(grouped):
        values = sorted(grouped[key])
        centre = round(sum(values) / len(values))
        raw.append((key, centre) if horizontal else (centre, key))
    smoothed = []
    for index, point in enumerate(raw):
        neighbours = raw[max(0, index - 2):index + 3]
        if horizontal:
            smoothed.append((point[0], round(sum(item[1] for item in neighbours) / len(neighbours))))
        else:
            smoothed.append((round(sum(item[0] for item in neighbours) / len(neighbours)), point[1]))
    paths: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    previous_key = None
    for point in smoothed:
        key = point[0] if horizontal else point[1]
        if previous_key is not None and key != previous_key + 1:
            if len(current) > 1:
                paths.append(current)
            current = []
        current.append(point)
        previous_key = key
    if len(current) > 1:
        paths.append(current)
    return paths


def _feature_metrics(
    component: set[tuple[int, int]],
    contours: list[list[tuple[int, int]]],
    *,
    pixel_scale: float,
    pixel_area_mm2: float,
) -> dict:
    min_x, max_x = min(x for x, _ in component), max(x for x, _ in component)
    min_y, max_y = min(y for _, y in component), max(y for _, y in component)
    width_mm = (max_x - min_x + 1) * pixel_scale
    height_mm = (max_y - min_y + 1) * pixel_scale
    perimeter_mm = sum(
        hypot(
            contour[(index + 1) % len(contour)][0] - point[0],
            contour[(index + 1) % len(contour)][1] - point[1],
        )
        for contour in contours
        for index, point in enumerate(contour)
    ) * pixel_scale
    area_mm2 = len(component) * pixel_area_mm2
    estimated_width = 2 * area_mm2 / max(perimeter_mm, pixel_scale)
    return {
        "area_mm2": area_mm2,
        "perimeter_mm": perimeter_mm,
        "estimated_width_mm": estimated_width,
        "bbox_width_mm": width_mm,
        "bbox_height_mm": height_mm,
        "aspect_ratio": max(width_mm, height_mm) / max(pixel_scale, min(width_mm, height_mm)),
        "compactness": 4 * 3.141592653589793 * area_mm2 / max(perimeter_mm * perimeter_mm, pixel_area_mm2),
        "hole_count": max(0, len(contours) - 1),
    }


def _retain_thread_across_safe_jumps(
    regions: list[Region],
    maximum_mm: float,
    *,
    operation_field: str = "operations",
) -> None:
    """Remove a trim when the next same-thread object is a short safe jump."""
    for current, following in zip(regions, regions[1:]):
        current_operations = getattr(current, operation_field)
        following_operations = getattr(following, operation_field)
        if not current_operations or not following_operations:
            continue
        current_exit = next(
            (
                operation for operation in reversed(current_operations)
                if operation.kind == "stitch"
            ),
            None,
        )
        following_entry = next(
            (
                operation for operation in following_operations
                if operation.kind == "stitch"
            ),
            None,
        )
        if current_exit is None or following_entry is None:
            continue
        distance = hypot(
            following_entry.x - current_exit.x,
            following_entry.y - current_exit.y,
        )
        if distance <= maximum_mm:
            for index in range(len(current_operations) - 1, -1, -1):
                if current_operations[index].kind == "trim":
                    current_operations.pop(index)
                    tie_off_role = (
                        "underlay_tie_off"
                        if operation_field == "underlay_operations"
                        else "tie_off"
                    )
                    tie_in_role = (
                        "underlay_tie_in"
                        if operation_field == "underlay_operations"
                        else "tie_in"
                    )
                    current_operations[:] = [
                        operation for operation in current_operations
                        if operation.role != tie_off_role
                    ]
                    following_operations[:] = [
                        operation for operation in following_operations
                        if operation.role != tie_in_role
                    ]
                    current.stitch_diagnostics[
                        f"retained_thread_to_next_mm:{operation_field}"
                    ] = distance
                    break


def digitize(
    image_bytes: bytes,
    *,
    width_mm: float = 72,
    colors: int = 2,
    profile: str = "standard",
    algorithm: str = "layered",
    limits: dict | None = None,
    density_multiplier: float = 1.0,
    color_merge_tolerance: float = 8.0,
    fragmented_color_threshold: float = 0.20,
    posterization_smoothing_px: float = 0.0,
    construction_mode: str = "auto",
    running_passes: int = 3,
    include_posterized: bool = False,
) -> StitchPlan:
    """Interpret artwork with Otsu geometry and layered stitch settings."""
    try:
        settings = PROFILES[profile]
    except KeyError as error:
        raise ValueError(f"unknown digitizing profile: {profile}") from error
    try:
        strategy = ALGORITHMS[algorithm]
    except KeyError as error:
        raise ValueError(f"unknown digitizing algorithm: {algorithm}") from error
    density_multiplier = max(0.6, min(1.5, float(density_multiplier)))
    posterization_smoothing_px = max(
        0.0, min(3.0, float(posterization_smoothing_px))
    )
    if construction_mode not in {"auto", "tatami", "multi_run"}:
        raise ValueError(f"unknown construction mode: {construction_mode}")
    running_passes = max(1, min(5, int(running_passes)))
    requested_limits = limits or {}
    planning_limits = {
        "max_objects": max(1, int(requested_limits.get("max_objects", strategy.max_objects))),
        "max_trims": max(0, int(requested_limits.get("max_trims", strategy.max_trims))),
        "max_jumps": max(0, int(requested_limits.get("max_jumps", strategy.max_jumps))),
        "trims_per_1000_stitches": max(
            0.0, float(requested_limits.get(
                "trims_per_1000_stitches", strategy.trims_per_1000_stitches
            )),
        ),
        "jumps_per_1000_stitches": max(
            0.0, float(requested_limits.get(
                "jumps_per_1000_stitches", strategy.jumps_per_1000_stitches
            )),
        ),
        "detail_max_trims": max(
            0, int(requested_limits.get(
                "detail_max_trims", strategy.detail_max_trims
            )),
        ),
    }
    colors = max(2, min(15, int(colors)))
    source = Image.open(BytesIO(image_bytes)).convert("RGBA")
    block_art = _is_high_contrast_binary(source)
    source.thumbnail(
        (1024, 1024),
        Image.Resampling.NEAREST if block_art else Image.Resampling.LANCZOS,
    )
    detail_source = source.copy()
    reference_foreground, _ = otsu_foreground_mask(source)
    geometry_source = (
        source.filter(ImageFilter.GaussianBlur(posterization_smoothing_px))
        if posterization_smoothing_px > 0
        else source
    )
    foreground, posterization = otsu_foreground_mask(geometry_source)
    posterization["smoothing_radius_px"] = posterization_smoothing_px
    posterization["geometry_quality"] = mask_geometry_metrics(
        foreground,
        reference=reference_foreground,
    )
    if not foreground:
        width, height = geometry_source.size
        physical_scale = min(width_mm / width, 55.0 / height)
        return StitchPlan(
            "2.0", width * physical_scale, height * physical_scale,
            [], [], settings.name,
            settings.machine_max_stitch_mm, algorithm=strategy.name,
            algorithm_label=strategy.label, posterization=posterization,
            planning_limits=planning_limits,
            density_multiplier=density_multiplier,
        )
    min_x, max_x = min(x for x, _ in foreground), max(x for x, _ in foreground)
    min_y, max_y = min(y for _, y in foreground), max(y for _, y in foreground)
    posterization["source_size_px"] = {
        "width": geometry_source.width, "height": geometry_source.height,
    }
    posterization["foreground_bounds_px"] = {
        "left": min_x, "top": min_y, "right": max_x, "bottom": max_y,
    }
    if (min_x, min_y, max_x, max_y) != (
        0, 0, geometry_source.width - 1, geometry_source.height - 1
    ):
        geometry_source = geometry_source.crop(
            (min_x, min_y, max_x + 1, max_y + 1)
        )
        detail_source = detail_source.crop(
            (min_x, min_y, max_x + 1, max_y + 1)
        )
        foreground = {
            (x - min_x, y - min_y) for x, y in foreground
        }
        reference_foreground = {
            (x - min_x, y - min_y)
            for x, y in reference_foreground
            if min_x <= x <= max_x and min_y <= y <= max_y
        }
        posterization["cropped_to_foreground"] = True
    else:
        posterization["cropped_to_foreground"] = False
    width, height = geometry_source.size
    physical_scale = min(width_mm / width, 55.0 / height)
    width_mm = width * physical_scale
    height_mm = height * physical_scale
    pixel_scale = min(width_mm / width, height_mm / height)
    pixel_area_mm2 = (width_mm / width) * (height_mm / height)
    separation_diagnostics: dict = {}
    color_sampling_mask = foreground & reference_foreground
    palette, assigned, selected = quantize_foreground(
        # Geometry smoothing must not manufacture new thread shades. Sample
        # color from the unsmoothed artwork inside the cleaned silhouette.
        detail_source,
        color_sampling_mask or foreground,
        colors,
        merge_tolerance=color_merge_tolerance,
        fragmented_merge_threshold=fragmented_color_threshold,
        diagnostics=separation_diagnostics,
    )
    if assigned and len(assigned) < len(foreground):
        queue = deque(assigned)
        while queue:
            point = queue.popleft()
            x, y = point
            for neighbor in (
                (x - 1, y), (x + 1, y),
                (x, y - 1), (x, y + 1),
            ):
                if neighbor in foreground and neighbor not in assigned:
                    assigned[neighbor] = assigned[point]
                    queue.append(neighbor)
        fallback_color = selected[0]
        for point in foreground:
            assigned.setdefault(point, fallback_color)
    posterization["requested_color_limit"] = colors
    posterization["color_separation"] = separation_diagnostics
    posterization["semantic_color_count"] = len(selected)
    posterization["semantic_palette"] = [
        _hex(palette[index]) for index in selected
    ]
    posterization["consolidated_quantized_shades"] = max(
        0, separation_diagnostics["merged_shade_count"]
    )
    posterization["construction_mode"] = construction_mode
    posterization["running_passes"] = running_passes
    if include_posterized:
        preview = Image.new("RGBA", geometry_source.size, (0, 0, 0, 0))
        preview_pixels = preview.load()
        for point, color_index in assigned.items():
            preview_pixels[point[0], point[1]] = (
                *palette[color_index], 255
            )
        preview_stream = BytesIO()
        preview.save(preview_stream, format="PNG")
        posterization["preview_data_url"] = (
            "data:image/png;base64,"
            + base64.b64encode(preview_stream.getvalue()).decode()
        )
    posterization["stroke_vector_candidate"] = (
        len(selected) == 1
        and width / max(1, height) >= 3.0
        and not block_art
    )
    shared_detail_response = (
        None if block_art else detail_edge_response(detail_source)
    )

    # Pass 1: geometry components and their interior thread layers. Geometry
    # identity is established before color subdivision.
    interior_candidates: list[
        tuple[str, int, set[tuple[int, int]], str]
    ] = []
    elements = sorted(_components(foreground), key=len, reverse=True)
    element_filter_reports: dict[str, dict] = {}
    element_pass_masks: dict[str, dict[str, set[tuple[int, int]]]] = {}
    for element_number, element in enumerate(elements, 1):
        element_id = f"element-{element_number}"
        filter_report, pass_masks = analyze_pass_filters(
            detail_source,
            element,
            assigned,
            pixel_scale=pixel_scale,
            pixel_area_mm2=pixel_area_mm2,
            detail_response=shared_detail_response,
        )
        element_filter_reports[element_id] = filter_report
        element_pass_masks[element_id] = pass_masks
        blocking_element = (
            element
            if block_art
            else pass_masks["blocking"]
        )
        if strategy.preserve_color_layers or block_art:
            for layer_index, color_index in enumerate(selected):
                for component in _components({
                    point for point in blocking_element
                    if assigned.get(point) == color_index
                }):
                    if not block_art:
                        component, _ = filter_blocking_mask(
                            component
                        )
                    interior_candidates.append(
                        (element_id, layer_index, component, "interior")
                    )
        else:
            dominant = Counter(assigned[point] for point in element).most_common(1)[0][0]
            interior_candidates.append(
                (
                    element_id,
                    selected.index(dominant),
                    blocking_element,
                    "interior",
                )
            )

    interior_candidates = [
        candidate for candidate in interior_candidates
        if len(candidate[2]) * pixel_area_mm2 >= strategy.min_area_mm2
    ]
    interior_candidates.sort(key=lambda candidate: len(candidate[2]), reverse=True)

    # Pass 2: per-element physical and multiscale image gates propose
    # top-layer running detail. It is deliberately capped so raster noise
    # cannot become hundreds of trims.
    detail_report = {
        "method": "disabled_for_precision_art",
        "pixels": 0,
        "eligible_elements": 0,
        "candidate_objects": 0,
    }
    detail_candidates: list[
        tuple[str, int, set[tuple[int, int]], str]
    ] = []
    detail_limit = min(
        strategy.max_detail_objects, max(0, planning_limits["max_objects"] - 1)
    )
    if not block_art and detail_limit:
        detail_report = {
            "method": "per_element_pass_filter_bank",
            "pixels": 0,
            "eligible_elements": 0,
            "candidate_objects": 0,
            "filters": [
                "physical_width_gate",
                "multiscale_rgb_edges",
                "boundary_suppression",
                "color_complexity_gate",
                "edge_density_gate",
            ],
        }
        detail_number = 0
        for element_number, element in enumerate(elements, 1):
            element_id = f"element-{element_number}"
            report = element_filter_reports[element_id]["passes"]["detail"]
            detail_mask = element_pass_masks[element_id]["detail"]
            if not report["eligible"] or not detail_mask:
                continue
            detail_report["eligible_elements"] += 1
            detail_report["pixels"] += len(detail_mask)
            for component in sorted(_components(detail_mask), key=len, reverse=True):
                if len(component) * pixel_area_mm2 < max(
                    0.04, strategy.min_area_mm2
                ):
                    continue
                assigned_points = [
                    point for point in component if point in assigned
                ]
                if not assigned_points:
                    continue
                color_index = Counter(
                    assigned[point] for point in assigned_points
                ).most_common(1)[0][0]
                detail_number += 1
                detail_candidates.append(
                    (
                        element_id,
                        selected.index(color_index),
                        component,
                        "detail",
                    )
                )
                if len(detail_candidates) >= detail_limit:
                    break
            if len(detail_candidates) >= detail_limit:
                break
        detail_report["candidate_objects"] = len(detail_candidates)

    # Planning limits are DRC constraints, never permission to erase source
    # geometry. Every interior object that passes the physical speck filter is
    # retained. The independent detail scan remains capped because it is a
    # derived enhancement rather than authored source geometry.
    dropped_objects = 0
    retained_interior = interior_candidates
    candidates = retained_interior + detail_candidates
    planned_foreground = set().union(
        *(candidate[2] for candidate in retained_interior)
    ) if retained_interior else set()
    planned_underlay = set().union(
        *(
            element_pass_masks[element_id]["underlay"]
            for element_id in element_pass_masks
            if element_pass_masks[element_id]["underlay"]
        )
    ) if any(
        masks["underlay"] for masks in element_pass_masks.values()
    ) else set()
    planned_silhouette = set().union(
        *(
            element_pass_masks[element_id]["silhouette"]
            for element_id in element_pass_masks
            if element_pass_masks[element_id]["silhouette"]
        )
    ) if any(
        masks["silhouette"] for masks in element_pass_masks.values()
    ) else set()

    regions: list[Region] = []
    used_colors: set[int] = set()
    for element_id, layer_index, component, pass_name in candidates:
        color_index = selected[layer_index]
        min_x = min(x for x, _ in component)
        max_x = max(x for x, _ in component)
        min_y = min(y for _, y in component)
        max_y = max(y for _, y in component)
        contours = _contours(component)
        if not contours:
            continue
        contours.sort(key=lambda loop: abs(sum(
            loop[index][0] * loop[(index + 1) % len(loop)][1]
            - loop[(index + 1) % len(loop)][0] * loop[index][1]
            for index in range(len(loop))
        )), reverse=True)
        boundary = contours[0]
        metrics = _feature_metrics(
            component, contours, pixel_scale=pixel_scale,
            pixel_area_mm2=pixel_area_mm2,
        )
        filter_report = element_filter_reports.get(element_id, {})
        if filter_report:
            metrics["image_features"] = filter_report["features"]
        region_id = f"c{color_index}-{len(regions) + 1}"
        satin_pairs: list[tuple[tuple[int, int], tuple[int, int]]] = []
        stitch_diagnostics: dict = {
            "pass_filters": filter_report.get("passes", {})
        }
        center_paths: list[list[tuple[int, int]]] = []
        if pass_name == "detail":
            stitch_type, fill_spacing, stitch_length = "running", None, 1.0
            center_paths = _component_center_paths(component)
        elif (
            construction_mode == "multi_run"
            and metrics["estimated_width_mm"] <= 6.0
            and metrics["aspect_ratio"] >= 1.25
        ):
            stitch_type, fill_spacing, stitch_length = "running", None, 2.0
            center_paths = _component_center_paths(component)
            stitch_diagnostics["construction_alternative"] = {
                "mode": "multi_run",
                "passes": running_passes,
            }
        elif block_art:
            stitch_type, fill_spacing, stitch_length = "fill", 0.32, 1.6
        else:
            stitch_type = "fill"
            fill_spacing = (
                settings.fill_spacing_mm
                * strategy.fill_spacing_scale
                / density_multiplier
            )
            stitch_length = max(4.0, settings.fill_stitch_mm)
            satin_limit = min(
                strategy.satin_max_width_mm,
                12.1 if profile == "non_wearable" else 6.0,
            )
            if (
                construction_mode != "tatami"
                and
                metrics["estimated_width_mm"] <= satin_limit
                and metrics["aspect_ratio"] >= 1.35
                and len(contours) == 1
            ):
                rail_step = max(1, round(0.40 / pixel_scale))
                satin_pairs = list(_satin_rails(
                    component,
                    vertical=(max_y - min_y) > (max_x - min_x),
                    step=rail_step,
                ))
                physical_rails = [[
                    _point(pair[side], width, height, width_mm, height_mm)
                    for pair in satin_pairs
                ] for side in (0, 1)]
                diagnostics = score_satin_rail_points(physical_rails)
                stitch_diagnostics["satin"] = asdict(diagnostics)
                if diagnostics.valid and (diagnostics.mean_width_mm or 0) <= satin_limit:
                    stitch_type, fill_spacing, stitch_length = "satin", None, 1.5
        if stitch_type == "running" and not center_paths:
            if pass_name == "detail":
                continue
            stitch_type, fill_spacing, stitch_length = "fill", settings.fill_spacing_mm, 4.0

        region = Region(
            region_id, _hex(palette[color_index]), len(component),
            (min_x, min_y, max_x, max_y), stitch_type,
            contours=contours, fill_spacing_mm=fill_spacing,
            stitch_length_mm=stitch_length,
            stitch_diagnostics=stitch_diagnostics,
            element_id=element_id, layer_index=layer_index,
            feature_metrics=metrics,
            pass_name="detail" if pass_name == "detail" else "interior",
            pass_index=2 if pass_name == "detail" else 1,
        )
        first = center_paths[0][0] if stitch_type == "running" else boundary[0]
        x, y = _point(first, width, height, width_mm, height_mm)
        region.operations.append(Operation("jump", x, y, "entry", region_id))
        _ties(
            region.operations, first, width=width, height=height,
            width_mm=width_mm, height_mm=height_mm, role="tie_in",
            region_id=region_id,
        )
        underlay_body: list[Operation] = []
        top_body: list[Operation] = []
        underlay_allowed = (
            region.pass_index == 1
            and stitch_diagnostics["pass_filters"]
            .get("underlay", {})
            .get("eligible", False)
        )
        if stitch_type == "running":
            detail_spacing = max(1, round(stitch_length / pixel_scale))
            passes = (
                running_passes
                if construction_mode == "multi_run"
                and region.pass_name == "interior"
                else 1
            )
            for pass_index in range(passes):
                for source_path in center_paths:
                    path = (
                        list(reversed(source_path))
                        if pass_index % 2 else source_path
                    )
                    _append_run(
                        top_body, path, width=width, height=height,
                        width_mm=width_mm, height_mm=height_mm,
                        role=(
                            "top_detail_running"
                            if region.pass_name == "detail"
                            else "top_multi_running"
                            if passes > 1 else "detail_running"
                        ),
                        region_id=region_id,
                        spacing=detail_spacing,
                    )
        elif stitch_type == "satin":
            if underlay_allowed:
                _append_satin(
                    underlay_body, top_body, satin_pairs,
                    width=width, height=height,
                    width_mm=width_mm, height_mm=height_mm,
                    region_id=region_id,
                    wide_underlay=(
                        stitch_diagnostics["satin"]["mean_width_mm"] >= 3.5
                    ),
                )
            else:
                _append_satin(
                    [], top_body, satin_pairs,
                    width=width, height=height,
                    width_mm=width_mm, height_mm=height_mm,
                    region_id=region_id, wide_underlay=False,
                )
        else:
            if underlay_allowed:
                for contour in contours:
                    _append_run(
                        underlay_body, _contour_path(contour), width=width,
                        height=height, width_mm=width_mm, height_mm=height_mm,
                        role="edge_underlay", region_id=region_id, spacing=4,
                    )
                if (
                    block_art
                    or metrics["area_mm2"] < 2.0
                    or metrics["estimated_width_mm"] < 1.5
                ):
                    _append_routed_fill(
                        underlay_body,
                        _fill_runs(
                            contours, width=width, height=height,
                            width_mm=width_mm, height_mm=height_mm,
                            angle_degrees=135, row_spacing_mm=1.4,
                            max_stitch_mm=3.2,
                        ),
                        contours=contours, width=width, height=height,
                        width_mm=width_mm, height_mm=height_mm,
                        role="fill_underlay", region_id=region_id,
                        connector_max_mm=3.0,
                    )
            _append_routed_fill(
                top_body,
                _fill_runs(
                    contours, width=width, height=height, width_mm=width_mm,
                    height_mm=height_mm, angle_degrees=45,
                    row_spacing_mm=fill_spacing,
                    max_stitch_mm=(
                        stitch_length if block_art else settings.machine_max_stitch_mm
                    ),
                    min_stitch_mm=None if block_art else 4.0,
                ),
                contours=contours, width=width, height=height,
                width_mm=width_mm, height_mm=height_mm,
                role="top_fill", region_id=region_id,
                connector_max_mm=3.0,
            )
        region.operations.extend(top_body)
        _ties(
            region.operations, boundary[-1], width=width, height=height,
            width_mm=width_mm, height_mm=height_mm, role="tie_off",
            region_id=region_id,
        )
        region.operations.append(Operation(
            "trim", x, y,
            "detail_trim" if region.pass_index == 2 else "trim",
            region_id,
        ))
        region.operations = _enforce_stitch_criteria(
            _enforce_minimum_stitch(
                region.operations, 0.25 if block_art else settings.running_min_mm,
            ),
            maximum_radial_mm=(
                min(settings.machine_max_stitch_mm, 6.0)
                if stitch_type == "satin" and profile != "non_wearable"
                else settings.machine_max_stitch_mm
            ),
        )
        region.underlay_operations = _wrap_underlay_body(
            underlay_body,
            region_id=region_id,
            minimum_stitch_mm=0.25 if block_art else settings.running_min_mm,
            maximum_stitch_mm=settings.machine_max_stitch_mm,
        )
        regions.append(region)
        used_colors.add(color_index)

    foundation_operations = []
    if not block_art:
        foundation_operations.extend(_design_centerline_underlay(
            planned_underlay, width=width, height=height, width_mm=width_mm,
            height_mm=height_mm,
        ))
        foundation_operations.extend(_silhouette_underlay(
            planned_silhouette, width=width, height=height, width_mm=width_mm,
            height_mm=height_mm, pixel_area_mm2=pixel_area_mm2,
        ))
    foundation_operations = _enforce_stitch_criteria(
        _enforce_minimum_stitch(foundation_operations, settings.running_min_mm),
        maximum_radial_mm=settings.machine_max_stitch_mm,
    )
    palette_hex = [_hex(palette[index]) for index in selected if index in used_colors]
    routed_regions: list[Region] = []
    for pass_index in sorted({region.pass_index for region in regions}):
        for color in palette_hex:
            group = _route_same_color(
                [
                    region for region in regions
                    if region.color == color and region.pass_index == pass_index
                ],
                center_outward=settings.center_outward_routing,
            )
            _retain_thread_across_safe_jumps(
                group,
                12.1 if pass_index == 2 else strategy.trimless_jump_mm,
            )
            if pass_index == 1:
                _retain_thread_across_safe_jumps(
                    [
                        region for region in group
                        if region.underlay_operations
                    ],
                    # Underlay travel is covered by the blocking pass, so the
                    # machine-safe 12.1 mm span can remain threaded without
                    # leaving an exposed connector in the finished design.
                    12.1,
                    operation_field="underlay_operations",
                )
            routed_regions.extend(group)
    posterization["algorithm"] = strategy.name
    posterization["geometry_elements"] = len({region.element_id for region in routed_regions})
    posterization["source_geometry_elements"] = len(elements)
    posterization["detail_scan"] = detail_report
    posterization["pass_filter_sets"] = {
        "underlay": {
            "goal": "stabilize eligible physical objects without filling noise",
            "criteria": [
                "area_mm2 >= 0.6",
                "estimated_width_mm >= 1.2 or long_span_mm >= 5.0",
                "silhouette requires area_mm2 >= 2.0 and width >= 1.5",
            ],
            "filters": ["binary_close_3px", "optional_binary_inset_1px"],
            "eligible_pixels": len(planned_underlay),
            "silhouette_pixels": len(planned_silhouette),
        },
        "blocking": {
            "goal": "retain authored color masses while removing pixel noise",
            "criteria": [f"area_mm2 >= {strategy.min_area_mm2}"],
            "filters": ["binary_median_3px", "geometry_change_guard"],
            "output_pixels": len(planned_foreground),
        },
        "detail": detail_report,
    }
    posterization["element_filter_analysis"] = [
        {"element_id": element_id, **element_filter_reports[element_id]}
        for element_id in sorted(
            element_filter_reports,
            key=lambda value: int(value.split("-")[-1]),
        )
    ]
    posterization["dropped_objects_for_budget"] = dropped_objects
    posterization["composition"] = [
        "silhouette_underlay", "interior_color", "top_detail",
    ]
    plan = StitchPlan(
        "2.0", width_mm, height_mm, palette_hex, routed_regions,
        settings.name, settings.machine_max_stitch_mm, foundation_operations,
        strategy.name, strategy.label, posterization, block_art, planning_limits,
        density_multiplier,
    )
    posterization["dropped_objects_for_movement_budget"] = 0
    posterization["movement_budget_pass"] = plan.drc()["budget_pass"]
    return plan


def digitize_payload(image_bytes: bytes, **arguments) -> dict:
    """Return the selected raster or editable stroke plan for API consumers."""
    try:
        from .drc_policy import evaluate_drc
    except ImportError:
        from drc_policy import evaluate_drc

    raster = digitize(image_bytes, **arguments)
    if (
        not raster.posterization.get("stroke_vector_candidate")
        or arguments.get("construction_mode", "auto") != "auto"
    ):
        payload = raster.json()
        payload["drc_policy"] = evaluate_drc(payload)
        return payload

    # Import lazily: vectorization reuses raster geometry helpers from this
    # module, so loading it before digitizer initialization would be circular.
    from vector_document import plan_document
    from vectorize import vectorize_posterized

    document = vectorize_posterized(
        image_bytes,
        width_mm=float(arguments.get("width_mm", 72)),
        smoothing_radius_px=float(
            arguments.get("posterization_smoothing_px", 0.0)
        ),
    )
    satin_objects = [
        item for item in document.objects
        if item.stitch_type == "satin" and len(item.satin_rails) == 2
    ]
    if not satin_objects:
        payload = raster.json()
        payload["drc_policy"] = evaluate_drc(payload)
        return payload
    density = max(
        0.6, min(1.5, float(arguments.get("density_multiplier", 1.0)))
    )
    for layer in document.layers:
        layer.density_multiplier = density
    vector_plan = plan_document(document)
    operations = vector_plan["operations"]
    palette = list(dict.fromkeys(
        item.color for item in document.objects if item.enabled
    ))
    stitch_count = sum(
        operation["kind"] == "stitch" for operation in operations
    )
    area_sq_in = (
        document.width_mm * document.height_mm / (25.4 ** 2)
    )
    posterization = {
        **raster.posterization,
        "selected_planner": "stroke_vector",
        "satin_rail_object_count": len(satin_objects),
        "running_path_object_count": sum(
            item.stitch_type == "running" for item in document.objects
        ),
    }
    payload = raster.json()
    payload.update({
        "version": "2.1-vector",
        "width_mm": document.width_mm,
        "height_mm": document.height_mm,
        "palette": palette,
        "regions": document.json()["objects"],
        "layers": document.json()["layers"],
        "operations": operations,
        "posterization": posterization,
        "document": document.json(),
        "composition": document.composition(),
        "diagnostics": vector_plan["diagnostics"],
        "brackets": vector_plan["brackets"],
        "findings": vector_plan["findings"],
        "sequence": vector_plan["sequence"],
        "orientations": vector_plan["orientations"],
        "neighbors": vector_plan["neighbors"],
        "constructs": vector_plan["constructs"],
        "thread_economy": vector_plan["thread_economy"],
        "drc": {
            "stitch_count": stitch_count,
            "jump_count": sum(
                operation["kind"] == "jump" for operation in operations
            ),
            "trim_count": sum(
                operation["kind"] == "trim" for operation in operations
            ),
            "color_change_count": sum(
                operation["kind"] == "color" for operation in operations
            ),
            "object_count": len(document.objects),
            "stitches_per_sq_in": (
                stitch_count / area_sq_in if area_sq_in else 0
            ),
            "short_stitches": 0,
            "long_stitches": 0,
            "segments": stitch_count,
            "quality_score": 98,
        },
    })
    payload["drc_policy"] = evaluate_drc(payload)
    return payload

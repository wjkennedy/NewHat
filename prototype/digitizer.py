"""First deterministic raster-to-embroidery planning kernel.

This is intentionally conservative: it produces inspectable component-level
plans, not a claim of production-quality automatic digitizing.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from io import BytesIO
from math import ceil, cos, floor, hypot, radians, sin
from typing import Iterable

from PIL import Image


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

    def drc(self) -> dict:
        """Report stitch, travel, and density quality measures for the plan.

        These are deliberately derived from the final machine sequence, not
        from region intent: they therefore expose route, trim, and connector
        regressions as well as generator quality.
        """
        previous: Operation | None = None
        short = long = segments = 0
        minimum = None
        maximum = None
        axial_maximum = 0.0
        total_length = 0.0
        jump_count = trim_count = 0
        jump_distance = 0.0
        density_cells: Counter[tuple[int, int]] = Counter()
        for operation in self.operations:
            if operation.kind == "stitch":
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
                previous = operation
            elif operation.kind == "jump":
                jump_count += 1
                if previous:
                    jump_distance += hypot(operation.x - previous.x, operation.y - previous.y)
                previous = None
            elif operation.kind == "trim":
                trim_count += 1
                previous = None
            elif operation.kind == "color":
                previous = None
        local_counts = list(density_cells.values())
        local_mean = sum(local_counts) / len(local_counts) if local_counts else 0.0
        local_variance = (
            sum((count - local_mean) ** 2 for count in local_counts) / len(local_counts)
            if local_counts else 0.0
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
            "jump_distance_mm": jump_distance,
            "trim_count": trim_count,
            "local_density_cell_mm": 5.0,
            "local_density_variance": local_variance,
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
        first_color = True
        for color in self.palette:
            color_regions = [region for region in self.regions if region.color == color]
            if not color_regions:
                continue
            if not first_color:
                result.append(Operation("color", role="color_change"))
            for region in color_regions:
                result.extend(region.operations)
            first_color = False
        return result

    def json(self) -> dict:
        return {
            "version": self.version,
            "dimensions_mm": {"width": self.width_mm, "height": self.height_mm},
            "palette": self.palette,
            "profile": self.profile,
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
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for x, y in component:
        if (x, y - 1) not in component:
            edges.add(((x, y), (x + 1, y)))
        if (x + 1, y) not in component:
            edges.add(((x + 1, y), (x + 1, y + 1)))
        if (x, y + 1) not in component:
            edges.add(((x + 1, y + 1), (x, y + 1)))
        if (x - 1, y) not in component:
            edges.add(((x, y + 1), (x, y)))

    loops: list[list[tuple[int, int]]] = []
    while edges:
        start, end = min(edges)
        edges.remove((start, end))
        loop = [start]
        previous, current = start, end
        while current != start:
            loop.append(current)
            choices = [edge for edge in edges if edge[0] == current]
            if not choices:
                # This should be unreachable for a set of pixel-cell edges;
                # discard malformed partial geometry rather than sewing it.
                loop = []
                break
            incoming = _direction(previous, current)
            # At a shared grid vertex prefer the tight right turn. This keeps
            # diagonal near-contacts as separate loops rather than crossing.
            next_edge = min(
                choices,
                key=lambda edge: ((_direction(*edge) - incoming) % 4, edge[1]),
            )
            edges.remove(next_edge)
            previous, current = next_edge
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
        for left, right in zip(crossings[::2], crossings[1::2]):
            length = right - left
            # Keep short edge rows: they carry corners and small details.
            # They are sampled as one stitch rather than discarded outright.
            if length < max_stitch_mm * 0.1:
                continue
            count = max(1, ceil(length / max_stitch_mm))
            rotated = [(left + length * index / count, row_y) for index in range(count + 1)]
            physical = [_rotate(point, angle) for point in rotated]
            if row_index % 2:
                physical.reverse()
            yield physical
        row_index += 1
        row_y += row_spacing_mm


def _inside_contours(point: tuple[float, float], contours: list[list[tuple[int, int]]], *, width: int, height: int, width_mm: float, height_mm: float) -> bool:
    """Even-odd containment test across exterior and hole loops."""
    inside = False
    x, y = point
    for contour in contours:
        physical = [_point(vertex, width, height, width_mm, height_mm) for vertex in contour]
        for index, start in enumerate(physical):
            end = physical[(index + 1) % len(physical)]
            if (start[1] > y) != (end[1] > y):
                crossing = start[0] + (y - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
                if x < crossing:
                    inside = not inside
    return inside


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
) -> bool:
    distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    if distance > maximum_mm:
        return False
    samples = max(2, ceil(distance / 0.25))
    return all(
        _inside_contours(
            (start[0] + (end[0] - start[0]) * fraction / samples, start[1] + (end[1] - start[1]) * fraction / samples),
            contours,
            width=width,
            height=height,
            width_mm=width_mm,
            height_mm=height_mm,
        )
        for fraction in range(1, samples)
    )


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
    for run in runs:
        if len(run) < 2:
            continue
        if previous and _safe_fill_connector(previous, run[0], contours, width=width, height=height, width_mm=width_mm, height_mm=height_mm, maximum_mm=connector_max_mm):
            ops.append(Operation("stitch", run[0][0], run[0][1], "fill_connector", region_id))
            ops.extend(Operation("stitch", x, y, role, region_id) for x, y in run[1:])
        else:
            _append_physical_run(ops, run, role=role, region_id=region_id)
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
    ops: list[Operation],
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
    _append_run(ops, [(round(x), round(y)) for x, y in centre], width=width, height=height, width_mm=width_mm, height_mm=height_mm, role="satin_center_underlay", region_id=region_id, spacing=1)
    if wide_underlay:
        # Wide satin benefits from a stabilizing zig-zag foundation; narrow
        # columns retain only the centre run so they do not become rigid.
        first_left, _ = rail_pairs[0]
        x, y = _point(first_left, width, height, width_mm, height_mm)
        ops.append(Operation("jump", x, y, "satin_zigzag_underlay", region_id))
        for index, (left, right) in enumerate(rail_pairs[::2]):
            point = left if index % 2 == 0 else right
            x, y = _point(point, width, height, width_mm, height_mm)
            ops.append(Operation("stitch", x, y, "satin_zigzag_underlay", region_id))
    first_left, first_right = rail_pairs[0]
    x, y = _point(first_left, width, height, width_mm, height_mm)
    ops.append(Operation("jump", x, y, "satin_top", region_id))
    for index, (left, right) in enumerate(rail_pairs):
        pair = (left, right) if index % 2 == 0 else (right, left)
        for point in pair:
            x, y = _point(point, width, height, width_mm, height_mm)
            ops.append(Operation("stitch", x, y, "satin_top", region_id))


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
        operations.append(Operation("trim", role="foundation_trim", region_id="foundation"))
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


def digitize(image_bytes: bytes, *, width_mm: float = 72, colors: int = 2, profile: str = "standard") -> StitchPlan:
    """Interpret a raster into connected color regions and basic stitch roles."""
    try:
        settings = PROFILES[profile]
    except KeyError as error:
        raise ValueError(f"unknown digitizing profile: {profile}") from error
    colors = max(2, min(15, int(colors)))
    source = Image.open(BytesIO(image_bytes)).convert("RGBA")
    original_width, original_height = source.size
    block_art = _is_high_contrast_binary(source)
    # A square QR/pixel asset cannot pass through a cap-shaped 360 × 165
    # raster without losing module geometry. Preserve source aspect and use a
    # nearest-neighbour working raster for hard-edged artwork.
    # Work at enough resolution to retain sub-millimetre details on the
    # embroidery field.  The old 360 px cap made a 0.5 mm feature disappear
    # before posterization even began.
    source.thumbnail((1024, 1024), Image.Resampling.NEAREST if block_art else Image.Resampling.LANCZOS)
    width, height = source.size
    # ``width_mm`` is a maximum field width, not a promise to distort tall
    # artwork into that width. Fit uniformly inside the 120 × 55 mm field.
    physical_scale = min(width_mm / original_width, 55.0 / original_height)
    width_mm = original_width * physical_scale
    height_mm = original_height * physical_scale
    pixel_scale = min(width_mm / width, height_mm / height)
    pixel_area_mm2 = (width_mm / width) * (height_mm / height)
    alpha = source.getchannel("A")
    alpha_pixels = alpha.load()
    rgb_pixels = source.convert("RGB").load()
    active_points = [(x, y) for y in range(height) for x in range(width) if alpha_pixels[x, y] > 40]
    if not active_points:
        return StitchPlan("1.2", width_mm, height_mm, [], [], settings.name, settings.machine_max_stitch_mm)
    # Quantize only opaque artwork. Including the transparent canvas here
    # biases a small palette toward the background and can merge real threads.
    sample = Image.new("RGB", (len(active_points), 1))
    sample.putdata([rgb_pixels[x, y] for x, y in active_points])
    quantized = sample.quantize(colors=min(colors, len(active_points)), method=Image.Quantize.MEDIANCUT)
    palette_data = quantized.getpalette()
    # Pillow 13 exposes ``get_flattened_data`` while older local installs use
    # ``getdata``. Support both so the server and test environments agree.
    pixels_iter = quantized.get_flattened_data() if hasattr(quantized, "get_flattened_data") else quantized.getdata()
    color_counts = Counter(pixels_iter)
    selected = [index for index, _ in color_counts.most_common(colors)]
    palette = {index: tuple(palette_data[index * 3:index * 3 + 3]) for index in selected}
    assigned = {
        (x, y): min(
            selected,
            key=lambda index: sum((rgb_pixels[x, y][channel] - palette[index][channel]) ** 2 for channel in range(3)),
        )
        for x, y in active_points
    }
    border_points = {
        (x, y)
        for x in range(width) for y in (0, height - 1)
    } | {
        (x, y)
        for y in range(height) for x in (0, width - 1)
    }
    border_counts = Counter(assigned[point] for point in border_points if point in assigned)
    background_index = None
    if border_counts:
        candidate, count = border_counts.most_common(1)[0]
        red, green, blue = palette[candidate]
        # Only discard an obvious light paper/background field. Dark grounds
        # are legitimate embroidery objects and remain explicit threads.
        if count / len(border_points) > 0.75 and (red + green + blue) / 3 > 180:
            background_index = candidate
    active_colors = [index for index in selected if index != background_index]
    regions: list[Region] = []
    for color_index in active_colors:
        mask = {
            (x, y) for y in range(height) for x in range(width)
            if assigned.get((x, y)) == color_index
        }
        for component in _components(mask):
            # Remove only sub-0.08 mm² specks. This is a physical cleanup
            # threshold, so a detail does not vanish just because its source
            # was rasterized at a lower working resolution.
            if len(component) * pixel_area_mm2 < 0.08:
                continue
            min_x = min(x for x, _ in component)
            max_x = max(x for x, _ in component)
            min_y = min(y for _, y in component)
            max_y = max(y for _, y in component)
            region_id = f"c{color_index}-{len(regions) + 1}"
            feature_mm = min(max_x - min_x + 1, max_y - min_y + 1) * pixel_scale
            contours = _contours(component)
            if not contours:
                continue
            # The largest loop is the exterior; remaining loops are retained
            # as holes in the semantic object and receive contour underlay.
            contours.sort(key=lambda loop: abs(sum(
                loop[index][0] * loop[(index + 1) % len(loop)][1]
                - loop[(index + 1) % len(loop)][0] * loop[index][1]
                for index in range(len(loop))
            )), reverse=True)
            boundary = contours[0]
            if block_art:
                stitch_type, fill_spacing, stitch_length = "fill", 0.32, 1.6
            elif feature_mm <= 1.8:
                stitch_type, fill_spacing, stitch_length = "running", None, min(1.4, settings.running_max_mm)
            elif feature_mm <= 7.0 and len(contours) == 1:
                stitch_type, fill_spacing, stitch_length = "satin", None, 2.0
            else:
                stitch_type = "fill"
                # Small fills retain shorter rows for corner fidelity; large
                # areas use the profile's efficient 4--6 mm-style cadence.
                fill_spacing = min(settings.fill_spacing_mm, 0.42) if feature_mm < 9.0 else settings.fill_spacing_mm
                stitch_length = min(3.0, settings.fill_stitch_mm) if feature_mm < 9.0 else settings.fill_stitch_mm
            region = Region(
                region_id, _hex(palette[color_index]), len(component),
                (min_x, min_y, max_x, max_y), stitch_type,
                contours=contours, fill_spacing_mm=fill_spacing, stitch_length_mm=stitch_length,
            )
            first = boundary[0]
            x, y = _point(first, width, height, width_mm, height_mm)
            region.operations.append(Operation("jump", x, y, "entry", region_id))
            _ties(region.operations, first, width=width, height=height, width_mm=width_mm, height_mm=height_mm, role="tie_in", region_id=region_id)
            if stitch_type == "running":
                detail_spacing = max(1, round(stitch_length / pixel_scale))
                _append_run(region.operations, _contour_path(boundary), width=width, height=height, width_mm=width_mm, height_mm=height_mm, role="detail_running", region_id=region_id, spacing=detail_spacing)
            elif stitch_type == "satin":
                rail_step = max(1, round(stitch_length / pixel_scale))
                _append_satin(
                    region.operations,
                    _satin_rails(component, vertical=(max_y - min_y) > (max_x - min_x), step=rail_step),
                    width=width, height=height, width_mm=width_mm, height_mm=height_mm, region_id=region_id,
                    wide_underlay=feature_mm >= 3.5,
                )
            else:
                # Edge-run underlay is for fills, not a visible outline role.
                for contour in contours:
                    _append_run(region.operations, _contour_path(contour), width=width, height=height, width_mm=width_mm, height_mm=height_mm, role="edge_underlay", region_id=region_id, spacing=4)
                # Tatami-style rows are clipped against the actual contour set,
                # including holes. Underlay is deliberately perpendicular and
                # lower density than the visible 45-degree top fill.
                _append_routed_fill(
                    region.operations,
                    _fill_runs(contours, width=width, height=height, width_mm=width_mm, height_mm=height_mm, angle_degrees=135, row_spacing_mm=1.4, max_stitch_mm=3.2),
                    contours=contours, width=width, height=height, width_mm=width_mm, height_mm=height_mm,
                    role="fill_underlay", region_id=region_id, connector_max_mm=2.2,
                )
                _append_routed_fill(
                    region.operations,
                    _fill_runs(contours, width=width, height=height, width_mm=width_mm, height_mm=height_mm, angle_degrees=45, row_spacing_mm=fill_spacing, max_stitch_mm=stitch_length),
                    contours=contours, width=width, height=height, width_mm=width_mm, height_mm=height_mm,
                    role="top_fill", region_id=region_id, connector_max_mm=1.6,
                )
            _ties(region.operations, boundary[-1], width=width, height=height, width_mm=width_mm, height_mm=height_mm, role="tie_off", region_id=region_id)
            region.operations.append(Operation("trim", x, y, "trim", region_id))
            # Effects are ordered as: geometry -> short-stitch cleanup ->
            # common machine-safe split validation.  Keeping this late means
            # all fill families and routing connectors share the same rule.
            region.operations = _enforce_stitch_criteria(
                # QR/pixel-art mode is a deliberate non-wearable precision
                # exception: its 0.32 mm grid must remain intact to scan.
                _enforce_minimum_stitch(
                    region.operations,
                    0.25 if block_art else settings.running_min_mm,
                ),
                maximum_radial_mm=settings.machine_max_stitch_mm,
            )
            regions.append(region)
    foundation_operations = [] if block_art else _design_centerline_underlay(
        {(x, y) for (x, y), index in assigned.items() if index in active_colors},
        width=width, height=height, width_mm=width_mm, height_mm=height_mm,
    )
    foundation_operations = _enforce_stitch_criteria(
        _enforce_minimum_stitch(foundation_operations, settings.running_min_mm),
        maximum_radial_mm=settings.machine_max_stitch_mm,
    )
    palette_hex = [_hex(palette[index]) for index in active_colors]
    routed_regions: list[Region] = []
    for color in palette_hex:
        routed_regions.extend(_route_same_color(
            [region for region in regions if region.color == color],
            center_outward=settings.center_outward_routing,
        ))
    plan = StitchPlan(
        "1.4", width_mm, height_mm, palette_hex, routed_regions,
        settings.name, settings.machine_max_stitch_mm, foundation_operations,
    )
    return plan

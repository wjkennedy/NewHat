"""Colour-agnostic posterized-raster to editable vector objects."""
from __future__ import annotations

from collections import Counter
from io import BytesIO
from math import hypot

from PIL import Image

from digitizer import _components, _contours, _satin_rails, _simplify_loop
from posterize import otsu_foreground_mask, quantize_foreground
from vector_document import (
    BezierNode,
    VectorDocument,
    VectorObject,
    satin_diagnostics,
)


_NEIGHBORS_8 = (
    (0, -1), (1, -1), (1, 0), (1, 1),
    (0, 1), (-1, 1), (-1, 0), (-1, -1),
)


def _thin(mask: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Zhang-Suen thinning for an editable calligraphic stroke proposal."""
    skeleton = set(mask)
    changed = True
    while changed:
        changed = False
        for phase in (0, 1):
            remove: list[tuple[int, int]] = []
            for x, y in skeleton:
                neighbors = [
                    (x + dx, y + dy) in skeleton
                    for dx, dy in _NEIGHBORS_8
                ]
                count = sum(neighbors)
                if count < 2 or count > 6:
                    continue
                transitions = sum(
                    not neighbors[index] and neighbors[(index + 1) % 8]
                    for index in range(8)
                )
                if transitions != 1:
                    continue
                north, east, south, west = (
                    neighbors[0], neighbors[2], neighbors[4], neighbors[6]
                )
                if phase == 0:
                    if north and east and south:
                        continue
                    if east and south and west:
                        continue
                else:
                    if north and east and west:
                        continue
                    if north and south and west:
                        continue
                remove.append((x, y))
            if remove:
                skeleton.difference_update(remove)
                changed = True
    return skeleton


def _skeleton_neighbors(
    point: tuple[int, int],
    skeleton: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    x, y = point
    return [
        (x + dx, y + dy)
        for dx, dy in _NEIGHBORS_8
        if (x + dx, y + dy) in skeleton
    ]


def _skeleton_paths(
    skeleton: set[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    """Split a skeleton at endpoints, junctions, and closed cycles."""
    if not skeleton:
        return []
    nodes = {
        point for point in skeleton
        if len(_skeleton_neighbors(point, skeleton)) != 2
    }
    visited: set[frozenset[tuple[int, int]]] = set()
    paths: list[list[tuple[int, int]]] = []

    def trace(
        start: tuple[int, int],
        following: tuple[int, int],
    ) -> list[tuple[int, int]]:
        path = [start, following]
        previous, current = start, following
        visited.add(frozenset((start, following)))
        while current not in nodes:
            choices = [
                point for point in _skeleton_neighbors(current, skeleton)
                if point != previous
                and frozenset((current, point)) not in visited
            ]
            if not choices:
                break
            next_point = choices[0]
            visited.add(frozenset((current, next_point)))
            path.append(next_point)
            previous, current = current, next_point
        return path

    for node in sorted(nodes, key=lambda point: (point[1], point[0])):
        for neighbor in _skeleton_neighbors(node, skeleton):
            edge = frozenset((node, neighbor))
            if edge in visited:
                continue
            path = trace(node, neighbor)
            if len(path) >= 2:
                paths.append(path)

    # Components with degree two everywhere are closed loops.
    for start in sorted(skeleton, key=lambda point: (point[1], point[0])):
        unvisited = [
            point for point in _skeleton_neighbors(start, skeleton)
            if frozenset((start, point)) not in visited
        ]
        if not unvisited:
            continue
        path = [start]
        previous = None
        current = start
        while True:
            choices = [
                point for point in _skeleton_neighbors(current, skeleton)
                if point != previous
                and frozenset((current, point)) not in visited
            ]
            if not choices:
                break
            next_point = choices[0]
            visited.add(frozenset((current, next_point)))
            path.append(next_point)
            previous, current = current, next_point
            if current == start:
                break
        if len(path) >= 4:
            paths.append(path)
    return paths


def _smooth_path(
    points: list[tuple[int, int]],
    *,
    sample_step: int,
) -> list[tuple[float, float]]:
    """Smooth skeleton quantization while retaining path endpoints."""
    if len(points) < 2:
        return [(float(x), float(y)) for x, y in points]
    smoothed = []
    for index, point in enumerate(points):
        neighbors = points[max(0, index - 2):index + 3]
        smoothed.append((
            sum(item[0] for item in neighbors) / len(neighbors),
            sum(item[1] for item in neighbors) / len(neighbors),
        ))
    sampled = smoothed[::max(1, sample_step)]
    if sampled[-1] != smoothed[-1]:
        sampled.append(smoothed[-1])
    return sampled


def _merge_continuous_paths(
    paths: list[list[tuple[int, int]]],
) -> list[list[tuple[int, int]]]:
    """Join the straightest continuation through a skeleton junction.

    This preserves the dominant pen stroke through a crossing while leaving
    the less-aligned branch as a separate overlapping satin object.
    """
    result = [path for path in paths if len(path) >= 2]
    while True:
        best: tuple[
            float, int, int, list[tuple[int, int]], list[tuple[int, int]]
        ] | None = None
        for left_index, left_source in enumerate(result):
            for right_index in range(left_index + 1, len(result)):
                right_source = result[right_index]
                for left_reverse in (False, True):
                    left = list(reversed(left_source)) if left_reverse else left_source
                    for right_reverse in (False, True):
                        right = (
                            list(reversed(right_source))
                            if right_reverse else right_source
                        )
                        join_distance = hypot(
                            right[0][0] - left[-1][0],
                            right[0][1] - left[-1][1],
                        )
                        if join_distance > 2.5:
                            continue
                        left_anchor = left[max(0, len(left) - 4)]
                        right_anchor = right[min(len(right) - 1, 3)]
                        first = (
                            left_anchor[0] - left[-1][0],
                            left_anchor[1] - left[-1][1],
                        )
                        second = (
                            right_anchor[0] - right[0][0],
                            right_anchor[1] - right[0][1],
                        )
                        first_length = hypot(*first)
                        second_length = hypot(*second)
                        if first_length < 0.5 or second_length < 0.5:
                            continue
                        alignment = (
                            first[0] * second[0] + first[1] * second[1]
                        ) / (first_length * second_length)
                        if alignment > -0.62:
                            continue
                        score = alignment + join_distance * 0.03
                        if best is None or score < best[0]:
                            best = (
                                score, left_index, right_index,
                                list(left), list(right),
                            )
        if best is None:
            return result
        _, left_index, right_index, left, right = best
        join = (
            round((left[-1][0] + right[0][0]) / 2),
            round((left[-1][1] + right[0][1]) / 2),
        )
        merged = left[:-1] + [join] + right[1:]
        result = [
            path for index, path in enumerate(result)
            if index not in {left_index, right_index}
        ]
        result.append(merged)


def _distance_to_boundary(
    component: set[tuple[int, int]],
) -> dict[tuple[int, int], int]:
    """Return a fast conservative local radius for every stroke pixel."""
    boundary = [
        point for point in component
        if any(
            (point[0] + dx, point[1] + dy) not in component
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
        )
    ]
    distance = {point: 0 for point in boundary}
    queue = list(boundary)
    cursor = 0
    while cursor < len(queue):
        point = queue[cursor]
        cursor += 1
        next_distance = distance[point] + 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (point[0] + dx, point[1] + dy)
            if neighbor in component and neighbor not in distance:
                distance[neighbor] = next_distance
                queue.append(neighbor)
    return distance


def _ray_to_rail(
    point: tuple[float, float],
    normal: tuple[float, float],
    component: set[tuple[int, int]],
    direction: float,
    maximum: float,
) -> tuple[float, float]:
    """Walk from a centerline point to the last pixel inside the stroke."""
    last = point
    for step in range(1, 161):
        distance = step * 0.25
        if distance > maximum:
            break
        candidate = (
            point[0] + normal[0] * distance * direction,
            point[1] + normal[1] * distance * direction,
        )
        if (round(candidate[0]), round(candidate[1])) not in component:
            break
        last = candidate
    return last


def _rails_for_path(
    path: list[tuple[float, float]],
    component: set[tuple[int, int]],
    distance_to_boundary: dict[tuple[int, int], int],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    previous_normal: tuple[float, float] | None = None
    for index, point in enumerate(path):
        before = path[max(0, index - 1)]
        after = path[min(len(path) - 1, index + 1)]
        dx, dy = after[0] - before[0], after[1] - before[1]
        length = hypot(dx, dy)
        if length < 0.01:
            normal = previous_normal or (0.0, 1.0)
        else:
            normal = (-dy / length, dx / length)
        if (
            previous_normal is not None
            and normal[0] * previous_normal[0] + normal[1] * previous_normal[1] < 0
        ):
            normal = (-normal[0], -normal[1])
        previous_normal = normal
        radius = distance_to_boundary.get(
            (round(point[0]), round(point[1])), 1
        ) + 0.75
        maximum = max(0.75, radius * 1.25)
        left.append(_ray_to_rail(
            point, normal, component, 1.0, maximum
        ))
        right.append(_ray_to_rail(
            point, normal, component, -1.0, maximum
        ))
    return left, right


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(
        hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _split_path_by_length(
    points: list[tuple[float, float]],
    maximum: float,
) -> list[list[tuple[float, float]]]:
    """Split a long turn before rail correspondence becomes unstable."""
    total = _path_length(points)
    chord = (
        hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1])
        if len(points) >= 2 else 0.0
    )
    if (
        len(points) < 3
        or total <= maximum
        or chord / max(0.01, total) >= 0.86
    ):
        return [points]
    result: list[list[tuple[float, float]]] = []
    current = [points[0]]
    length = 0.0
    for point in points[1:]:
        segment = hypot(
            point[0] - current[-1][0],
            point[1] - current[-1][1],
        )
        if length + segment > maximum and len(current) >= 2:
            result.append(current)
            current = [current[-1]]
            length = 0.0
        current.append(point)
        length += segment
    if len(current) >= 2:
        result.append(current)
    return result


def _extend_open_path(
    points: list[tuple[float, float]],
    amount: float,
) -> list[tuple[float, float]]:
    if len(points) < 2 or amount <= 0:
        return points
    result = list(points)
    start_length = hypot(
        result[1][0] - result[0][0],
        result[1][1] - result[0][1],
    )
    end_length = hypot(
        result[-1][0] - result[-2][0],
        result[-1][1] - result[-2][1],
    )
    if start_length > 0.01:
        result[0] = (
            result[0][0] - (result[1][0] - result[0][0]) / start_length * amount,
            result[0][1] - (result[1][1] - result[0][1]) / start_length * amount,
        )
    if end_length > 0.01:
        result[-1] = (
            result[-1][0] + (result[-1][0] - result[-2][0]) / end_length * amount,
            result[-1][1] + (result[-1][1] - result[-2][1]) / end_length * amount,
        )
    return result


def _compensate_rails(
    rails: list[list[tuple[float, float]]],
    amount: float,
) -> list[list[tuple[float, float]]]:
    if len(rails) != 2 or amount <= 0:
        return rails
    result = [[], []]
    for left, right in zip(*rails):
        width = hypot(right[0] - left[0], right[1] - left[1])
        if width < 0.01:
            result[0].append(left)
            result[1].append(right)
            continue
        dx = (right[0] - left[0]) / width
        dy = (right[1] - left[1]) / width
        result[0].append((left[0] - dx * amount, left[1] - dy * amount))
        result[1].append((right[0] + dx * amount, right[1] + dy * amount))
    return result


def _foreground_color(
    image: Image.Image,
    mask: set[tuple[int, int]],
) -> str:
    palette, _, selected = quantize_foreground(image, mask, 8)
    if selected:
        red, green, blue = palette[selected[0]]
        return f"#{red:02x}{green:02x}{blue:02x}"
    pixels = image.convert("RGB").load()
    bins = Counter(
        (
            pixels[x, y][0] // 8 * 8,
            pixels[x, y][1] // 8 * 8,
            pixels[x, y][2] // 8 * 8,
        )
        for x, y in mask
    )
    red, green, blue = bins.most_common(1)[0][0]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _script_objects(
    image: Image.Image,
    mask: set[tuple[int, int]],
    *,
    width_mm: float,
    height_mm: float,
) -> list[VectorObject]:
    """Propose editable curved satin/running objects from a script silhouette."""
    width, height = image.size
    pixel_mm = min(
        width_mm / max(1, width),
        height_mm / max(1, height),
    )
    sample_step = max(1, round(0.40 / max(0.01, pixel_mm)))
    color = _foreground_color(image, mask)
    objects: list[VectorObject] = []

    def convert(point: tuple[float, float]) -> BezierNode:
        return BezierNode(
            (point[0] / max(1, width - 1) - 0.5) * width_mm,
            (point[1] / max(1, height - 1) - 0.5) * height_mm,
        )

    component_number = 0
    for component in sorted(_components(mask), key=len, reverse=True):
        component_number += 1
        min_x, max_x = min(x for x, _ in component), max(x for x, _ in component)
        min_y, max_y = min(y for _, y in component), max(y for _, y in component)
        component_width_mm = (max_x - min_x + 1) * pixel_mm
        component_height_mm = (max_y - min_y + 1) * pixel_mm
        trademark_like = max(component_width_mm, component_height_mm) <= 5.0
        skeleton = _thin(component)
        paths = _merge_continuous_paths(_skeleton_paths(skeleton))
        distance_to_boundary = _distance_to_boundary(component)
        segment_number = 0
        prepared_paths = [
            segment
            for raw_path in paths
            for segment in _split_path_by_length(
                _smooth_path(raw_path, sample_step=sample_step),
                maximum=max(4.0, 16.0 / max(0.01, pixel_mm)),
            )
        ]
        for path in prepared_paths:
            length_mm = _path_length(path) * pixel_mm
            if length_mm < (0.65 if trademark_like else 1.0):
                continue
            segment_number += 1
            identifier = f"script-{component_number}-{segment_number}"
            rails = _rails_for_path(path, component, distance_to_boundary)
            physical_rails = [
                [
                    (
                        (point[0] / max(1, width - 1) - 0.5) * width_mm,
                        (point[1] / max(1, height - 1) - 0.5) * height_mm,
                    )
                    for point in rail
                ]
                for rail in rails
            ]
            pull_compensation = 0.25
            join_overlap = 0.4
            physical_rails = _compensate_rails(
                physical_rails, pull_compensation
            )
            physical_rails = [
                _extend_open_path(rail, join_overlap / 2)
                for rail in physical_rails
            ]
            widths = [
                hypot(right[0] - left[0], right[1] - left[1])
                for left, right in zip(*physical_rails)
            ]
            mean_width = sum(widths) / max(1, len(widths))
            use_running = trademark_like or mean_width < 1.0
            item = VectorObject(
                identifier,
                color,
                "running" if use_running else "satin",
                [convert(point) for point in path],
                closed=False,
                satin_rails=(
                    []
                    if use_running
                    else [
                        [BezierNode(x, y) for x, y in physical_rails[0]],
                        [BezierNode(x, y) for x, y in physical_rails[1]],
                    ]
                ),
                stitch_length_mm=1.5 if trademark_like else 2.0,
                satin_spacing_mm=0.42,
                sequence_order=len(objects),
                element_id=f"element-{component_number}",
                underlay_enabled=not use_running,
                visual_role=(
                    "registered_mark" if trademark_like else "script_stroke"
                ),
                pull_compensation_mm=(
                    0.0 if use_running else pull_compensation
                ),
                join_overlap_mm=0.0 if use_running else join_overlap,
            )
            if item.stitch_type == "satin" and not satin_diagnostics(item).valid:
                # Unstable or over-wide proposals stay editable as running
                # centerlines instead of silently becoming an irregular fill.
                item.stitch_type = "running"
                item.satin_rails = []
                item.underlay_enabled = False
            objects.append(item)
    return objects


def vectorize_posterized(image_bytes: bytes, *, width_mm: float = 72.0) -> VectorDocument:
    """Vectorize an already background-removed raster without colour grouping.

    Alpha is the foreground contract: all non-transparent pixels participate in
    one mask, so touching posterized colours form one composed element. This
    deliberately avoids treating palette entries as separate geometry.
    """
    image = Image.open(BytesIO(image_bytes)).convert("RGBA")
    mask, _ = otsu_foreground_mask(image)
    if not mask:
        return VectorDocument(width_mm, 0.0, [])
    min_x, max_x = min(x for x, _ in mask), max(x for x, _ in mask)
    min_y, max_y = min(y for _, y in mask), max(y for _, y in mask)
    image = image.crop((min_x, min_y, max_x + 1, max_y + 1))
    mask = {(x - min_x, y - min_y) for x, y in mask}
    width, height = image.size
    height_mm = width_mm * height / width
    aspect_ratio = width / max(1, height)
    if aspect_ratio >= 3.0:
        script = _script_objects(
            image, mask, width_mm=width_mm, height_mm=height_mm
        )
        if script:
            return VectorDocument(
                width_mm, height_mm, script,
                foundation_enabled=False,
            )
    objects: list[VectorObject] = []
    for index, component in enumerate(_components(mask), 1):
        contours = _contours(component)
        if not contours:
            continue
        contours = [_simplify_loop(contour) for contour in contours]
        contour = max(contours, key=len)
        min_x, max_x = min(x for x, _ in component), max(x for x, _ in component)
        min_y, max_y = min(y for _, y in component), max(y for _, y in component)
        span_x, span_y = (max_x - min_x + 1) * width_mm / width, (max_y - min_y + 1) * height_mm / height
        narrow, long = min(span_x, span_y), max(span_x, span_y)
        stitch_type = "satin" if narrow >= 1.0 and long / max(narrow, .01) >= 2.0 and narrow <= 8.0 else "tatami"
        convert = lambda point: BezierNode(
            (point[0] / max(1, width - 1) - .5) * width_mm,
            (point[1] / max(1, height - 1) - .5) * height_mm,
        )
        nodes = [convert(point) for point in contour]
        boundaries = [[convert(point) for point in loop] for loop in contours]
        rails: list[list[BezierNode]] = []
        if stitch_type == "satin":
            pixel_mm = min(width_mm / max(1, width), height_mm / max(1, height))
            rail_step = max(1, round(.40 / max(pixel_mm, .001)))
            pairs = list(_satin_rails(
                component,
                vertical=span_y > span_x,
                step=rail_step,
            ))
            if pairs:
                rails = [
                    [convert(left) for left, _ in pairs],
                    [convert(right) for _, right in pairs],
                ]
        item = VectorObject(
            f"element-{index}", "#202020", stitch_type, nodes,
            boundaries=boundaries if stitch_type == "tatami" else [],
            satin_rails=rails,
            element_id=f"element-{index}",
        )
        # Geometry, not source colour, decides whether this element can sustain
        # a satin column. Failed rail topology or a poor physical-width score
        # falls back to a boundary-based tatami object.
        if stitch_type == "satin" and not satin_diagnostics(item).valid:
            item.stitch_type = "tatami"
            item.boundaries = boundaries
            item.satin_rails = []
        objects.append(item)
    return VectorDocument(width_mm, height_mm, objects)

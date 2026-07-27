"""Foreground extraction and thread-palette assignment for raster artwork.

Geometry and color are intentionally separate. Otsu thresholding establishes
the stitchable foreground; quantization only assigns thread attributes inside
that foreground.
"""
from __future__ import annotations

from collections import Counter
from colorsys import rgb_to_hsv
from math import log2, sqrt

from PIL import Image, ImageChops, ImageFilter


def _pixels(image: Image.Image):
    return (
        image.get_flattened_data()
        if hasattr(image, "get_flattened_data")
        else image.getdata()
    )


def otsu_threshold(values: list[int]) -> int:
    """Return the between-class-variance threshold for 8-bit samples."""
    if not values:
        return 0
    histogram = Counter(max(0, min(255, int(value))) for value in values)
    total = len(values)
    weighted_total = sum(value * count for value, count in histogram.items())
    background_count = 0
    background_sum = 0
    best_threshold = min(histogram)
    best_variance = -1.0
    for threshold in range(256):
        count = histogram.get(threshold, 0)
        background_count += count
        background_sum += threshold * count
        foreground_count = total - background_count
        if not background_count or not foreground_count:
            continue
        background_mean = background_sum / background_count
        foreground_mean = (weighted_total - background_sum) / foreground_count
        variance = background_count * foreground_count * (background_mean - foreground_mean) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def otsu_foreground_mask(source: Image.Image) -> tuple[set[tuple[int, int]], dict]:
    """Extract foreground without assuming a particular background color.

    Transparency is the strongest geometry contract. For opaque artwork, the
    border is treated as the background class and Otsu supplies the boundary
    between that class and the artwork class.
    """
    rgba = source.convert("RGBA")
    width, height = rgba.size
    alpha_channel = rgba.getchannel("A")
    alpha_values = list(_pixels(alpha_channel))
    alpha_pixels = alpha_channel.load()
    alpha_min, alpha_max = min(alpha_values), max(alpha_values)
    if alpha_min < 250:
        if alpha_min == alpha_max:
            mask = (
                {(x, y) for y in range(height) for x in range(width)}
                if alpha_max > 0 else set()
            )
            return mask, {
                "method": "uniform_alpha",
                "threshold": alpha_min,
                "foreground_pixels": len(mask),
            }
        threshold = otsu_threshold(alpha_values)
        mask = {
            (x, y)
            for y in range(height)
            for x in range(width)
            if alpha_pixels[x, y] > threshold
        }
        return mask, {
            "method": "otsu_alpha",
            "threshold": threshold,
            "foreground_pixels": len(mask),
        }

    rgb = rgba.convert("RGB")
    rgb_pixels = rgb.load()
    values = list(_pixels(rgb))
    if len(set(values)) == 1:
        mask = {(x, y) for y in range(height) for x in range(width)}
        return mask, {
            "method": "uniform_opaque",
            "background_reference_rgb": list(values[0]),
            "foreground_pixels": len(mask),
            "foreground_color_coverage": 1.0,
        }
    border_points = (
        [(x, y) for x in range(width) for y in (0, height - 1)]
        + [(x, y) for y in range(1, height - 1) for x in (0, width - 1)]
    )
    border_colors = [rgb_pixels[x, y] for x, y in border_points]
    ordered_channels = [
        sorted(color[channel] for color in border_colors)
        for channel in range(3)
    ]
    middle = len(border_colors) // 2
    background_reference = tuple(
        channel[middle] for channel in ordered_channels
    )

    def background_distance(point: tuple[int, int]) -> float:
        color = rgb_pixels[point[0], point[1]]
        return sqrt(sum(
            (color[channel] - background_reference[channel]) ** 2
            for channel in range(3)
        ))

    border_distances = sorted(
        background_distance(point) for point in border_points
    )
    robust_index = min(
        len(border_distances) - 1,
        round((len(border_distances) - 1) * 0.98),
    )
    tolerance = max(18.0, min(36.0, border_distances[robust_index] + 4.0))
    background_like = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if background_distance((x, y)) <= tolerance
    }
    border_background = {
        point for point in border_points if point in background_like
    }
    connected_background = set(border_background)
    queue = list(border_background)
    while queue:
        x, y = queue.pop()
        for neighbor in (
            (x - 1, y), (x + 1, y),
            (x, y - 1), (x, y + 1),
        ):
            if (
                neighbor in background_like
                and neighbor not in connected_background
            ):
                connected_background.add(neighbor)
                queue.append(neighbor)

    # Background-colored counters remain negative space. Authored white or
    # cream objects survive when their source color differs materially from
    # the sampled canvas, even though both are visually light.
    background_like_holes = background_like - connected_background
    background = connected_background | background_like_holes
    mask = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if (x, y) not in background
    }
    hue_families = _significant_hue_families([
        rgb_pixels[x, y] for x, y in mask
    ])
    if hue_families == 1:
        # A one-hue mark benefits from the tighter Otsu silhouette: pale JPEG
        # fringe is still the same eventual thread family and should not
        # become dozens of disconnected geometry objects.
        gray = rgba.convert("L")
        gray_values = list(_pixels(gray))
        gray_pixels = gray.load()
        threshold = otsu_threshold(gray_values)
        border_light = sum(
            gray_pixels[x, y] > threshold for x, y in border_points
        )
        light_background = border_light >= len(border_points) / 2
        mask = {
            (x, y)
            for y in range(height)
            for x in range(width)
            if (gray_pixels[x, y] <= threshold) == light_background
        }
        return mask, {
            "method": "otsu_luminance_single_hue",
            "threshold": threshold,
            "background_class": "light" if light_background else "dark",
            "background_reference_rgb": list(background_reference),
            "significant_hue_families": hue_families,
            "foreground_pixels": len(mask),
            "foreground_color_coverage": 1.0,
        }
    return mask, {
        "method": "border_connected_color_distance",
        "background_reference_rgb": list(background_reference),
        "background_tolerance_rgb": tolerance,
        "border_connected_background_pixels": len(connected_background),
        "background_like_negative_space_pixels": len(background_like_holes),
        "significant_hue_families": hue_families,
        "foreground_pixels": len(mask),
        "foreground_color_coverage": 1.0,
    }


def mask_geometry_metrics(
    mask: set[tuple[int, int]],
    *,
    reference: set[tuple[int, int]] | None = None,
) -> dict:
    """Measure raster-boundary quality without claiming visual correctness."""
    if not mask:
        return {
            "foreground_pixels": 0,
            "component_count": 0,
            "small_component_count": 0,
            "perimeter_px": 0,
            "boundary_corner_count": 0,
            "corners_per_100_boundary_px": 0.0,
            "negative_space_count": 0,
            "reference_iou": 0.0,
            "area_delta_percent": 0.0,
        }

    remaining = set(mask)
    component_sizes: list[int] = []
    while remaining:
        start = remaining.pop()
        stack = [start]
        size = 1
        while stack:
            x, y = stack.pop()
            for neighbor in (
                (x - 1, y), (x + 1, y),
                (x, y - 1), (x, y + 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
                    size += 1
        component_sizes.append(size)

    perimeter = sum(
        neighbor not in mask
        for x, y in mask
        for neighbor in (
            (x - 1, y), (x + 1, y),
            (x, y - 1), (x, y + 1),
        )
    )
    min_x, max_x = min(x for x, _ in mask), max(x for x, _ in mask)
    min_y, max_y = min(y for _, y in mask), max(y for _, y in mask)
    corners = 0
    for y in range(min_y - 1, max_y + 1):
        for x in range(min_x - 1, max_x + 1):
            occupied = sum(
                point in mask
                for point in (
                    (x, y), (x + 1, y),
                    (x, y + 1), (x + 1, y + 1),
                )
            )
            if occupied in {1, 3}:
                corners += 1

    background = {
        (x, y)
        for y in range(min_y - 1, max_y + 2)
        for x in range(min_x - 1, max_x + 2)
        if (x, y) not in mask
    }
    outside_start = (min_x - 1, min_y - 1)
    outside = {outside_start}
    stack = [outside_start]
    while stack:
        x, y = stack.pop()
        for neighbor in (
            (x - 1, y), (x + 1, y),
            (x, y - 1), (x, y + 1),
        ):
            if neighbor in background and neighbor not in outside:
                outside.add(neighbor)
                stack.append(neighbor)
    enclosed = background - outside
    negative_spaces = 0
    while enclosed:
        start = enclosed.pop()
        stack = [start]
        while stack:
            x, y = stack.pop()
            for neighbor in (
                (x - 1, y), (x + 1, y),
                (x, y - 1), (x, y + 1),
            ):
                if neighbor in enclosed:
                    enclosed.remove(neighbor)
                    stack.append(neighbor)
        negative_spaces += 1

    reference = reference if reference is not None else mask
    union = mask | reference
    intersection = mask & reference
    return {
        "foreground_pixels": len(mask),
        "component_count": len(component_sizes),
        "small_component_count": sum(size <= 4 for size in component_sizes),
        "perimeter_px": perimeter,
        "boundary_corner_count": corners,
        "corners_per_100_boundary_px": round(
            corners * 100 / max(1, perimeter), 3
        ),
        "negative_space_count": negative_spaces,
        "reference_iou": round(len(intersection) / max(1, len(union)), 6),
        "area_delta_percent": round(
            (len(mask) - len(reference)) * 100 / max(1, len(reference)),
            3,
        ),
    }


def _significant_hue_families(
    colors: list[tuple[int, int, int]],
    *,
    bins: int = 18,
) -> int:
    """Count materially populated chromatic hue groups."""
    if not colors:
        return 0
    step = max(1, len(colors) // 50_000)
    counts = Counter()
    sampled = 0
    for red, green, blue in colors[::step]:
        hue, saturation, value = rgb_to_hsv(
            red / 255, green / 255, blue / 255
        )
        if saturation < 0.25 or value < 0.10:
            continue
        counts[int(hue * bins) % bins] += 1
        sampled += 1
    threshold = max(12, round(sampled * 0.003))
    occupied = sorted(
        index for index, count in counts.items() if count >= threshold
    )
    if not occupied:
        return 0
    groups = 1 + sum(
        right - left > 1
        for left, right in zip(occupied, occupied[1:])
    )
    if occupied[0] == 0 and occupied[-1] == bins - 1 and groups > 1:
        groups -= 1
    return groups


def quantize_foreground(
    source: Image.Image,
    foreground: set[tuple[int, int]],
    colors: int,
    *,
    merge_tolerance: float = 8.0,
    fragmented_merge_threshold: float = 0.20,
    diagnostics: dict | None = None,
) -> tuple[dict[int, tuple[int, int, int]], dict[tuple[int, int], int], list[int]]:
    """Assign foreground pixels to a compact thread palette."""
    if not foreground:
        return {}, {}, []
    rgb = source.convert("RGB")
    rgb_pixels = rgb.load()
    ordered_points = sorted(foreground, key=lambda point: (point[1], point[0]))
    sample = Image.new("RGB", (len(ordered_points), 1))
    source_colors = [rgb_pixels[x, y] for x, y in ordered_points]
    sample.putdata(source_colors)
    hue_families = _significant_hue_families(source_colors)
    quantizer = (
        Image.Quantize.FASTOCTREE
        if hue_families >= 2
        else Image.Quantize.MEDIANCUT
    )
    quantizer_name = (
        "fast_octree_accent_preserving"
        if quantizer == Image.Quantize.FASTOCTREE
        else "median_cut_single_hue"
    )
    quantized = sample.quantize(
        colors=max(1, min(int(colors), len(ordered_points))),
        # Fast octree retains small, perceptually distinct accents in
        # multicolor artwork. Median cut remains more economical for
        # antialiased one-hue marks, where its shades consolidate cleanly.
        method=quantizer,
    )
    palette_data = quantized.getpalette()
    data = list(
        quantized.get_flattened_data()
        if hasattr(quantized, "get_flattened_data")
        else quantized.getdata()
    )
    counts = Counter(data)
    selected = [index for index, _ in counts.most_common(colors)]
    palette = {
        index: tuple(palette_data[index * 3:index * 3 + 3])
        for index in selected
    }
    # Quantization already chose the nearest palette entry for every ordered
    # source pixel. Reuse those assignments instead of repeating a
    # colors × pixels Python nearest-neighbor search.
    assigned = dict(zip(ordered_points, data))
    fragmentation = {
        index: _largest_component_fraction({
            point for point, assignment in assigned.items()
            if assignment == index
        })
        for index in selected
    }
    representatives: list[int] = []
    remap: dict[int, int] = {}
    merge_tolerance = max(0.0, min(96.0, float(merge_tolerance)))
    fragmented_merge_threshold = max(
        0.0, min(1.0, float(fragmented_merge_threshold))
    )
    for index in selected:
        representative = next((
            candidate for candidate in representatives
            if (
                sum(
                    (palette[index][channel] - palette[candidate][channel]) ** 2
                    for channel in range(3)
                ) <= merge_tolerance ** 2
                or (
                    fragmented_merge_threshold > 0
                    and fragmentation[index] <= fragmented_merge_threshold
                    and fragmentation[candidate] <= fragmented_merge_threshold
                )
            )
            and _same_thread_family(
                palette[index], palette[candidate]
            )
        ), None)
        if representative is None:
            representatives.append(index)
            remap[index] = index
        else:
            remap[index] = representative
    assigned = {
        point: remap.get(index, index)
        for point, index in assigned.items()
    }
    if diagnostics is not None:
        source_errors = [
            sqrt(sum(
                (
                    rgb_pixels[point[0], point[1]][channel]
                    - palette[assigned[point]][channel]
                ) ** 2
                for channel in range(3)
            ))
            for point in ordered_points
        ]
        diagnostics.update({
            "quantized_color_count": len(selected),
            "quantizer": quantizer_name,
            "significant_hue_families": hue_families,
            "semantic_color_count": len(representatives),
            "merged_shade_count": len(selected) - len(representatives),
            "merge_tolerance_rgb": merge_tolerance,
            "fragmented_merge_threshold": fragmented_merge_threshold,
            "mean_color_error_rgb": (
                sum(source_errors) / len(source_errors)
                if source_errors else 0.0
            ),
            "maximum_color_error_rgb": max(source_errors, default=0.0),
            "quantized_palette": [
                "#%02x%02x%02x" % palette[index]
                for index in selected
            ],
        })
    return (
        {index: palette[index] for index in representatives},
        assigned,
        representatives,
    )


def _largest_component_fraction(points: set[tuple[int, int]]) -> float:
    """Measure whether a quantized shade is a region or scattered edge noise."""
    if not points:
        return 0.0
    remaining = set(points)
    largest = 0
    while remaining:
        start = remaining.pop()
        stack = [start]
        size = 1
        while stack:
            x, y = stack.pop()
            for neighbor in (
                (x - 1, y), (x + 1, y),
                (x, y - 1), (x, y + 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
                    size += 1
        largest = max(largest, size)
    return largest / len(points)


def _same_thread_family(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
) -> bool:
    """Recognize quantized variants that describe one physical thread color."""
    left_h, left_s, left_v = rgb_to_hsv(*(value / 255 for value in left))
    right_h, right_s, right_v = rgb_to_hsv(*(value / 255 for value in right))
    hue_distance = min(
        abs(left_h - right_h),
        1.0 - abs(left_h - right_h),
    )
    chromatic = min(left_s, right_s) >= 0.20
    if chromatic:
        return (
            hue_distance <= 0.04
            and abs(left_s - right_s) <= 0.38
            and abs(left_v - right_v) <= 0.28
        )
    if (
        max(left_s, right_s) >= 0.20
        and min(left_s, right_s) >= 0.08
    ):
        # JPEG antialiasing commonly creates a pale tint of the authored
        # thread hue. Treat that tint as the same family while still keeping
        # true achromatic white/cream independent.
        return (
            hue_distance <= 0.04
            and abs(left_v - right_v) <= 0.70
        )
    return (
        max(left_s, right_s) <= 0.18
        and abs(left_v - right_v) <= 0.20
    )


def _cropped_mask_image(
    mask: set[tuple[int, int]],
    *,
    padding: int = 4,
) -> tuple[Image.Image, tuple[int, int]]:
    min_x, max_x = min(x for x, _ in mask), max(x for x, _ in mask)
    min_y, max_y = min(y for _, y in mask), max(y for _, y in mask)
    origin = (min_x - padding, min_y - padding)
    image = Image.new(
        "L",
        (
            max_x - min_x + 1 + padding * 2,
            max_y - min_y + 1 + padding * 2,
        ),
    )
    pixels = image.load()
    for x, y in mask:
        pixels[x - origin[0], y - origin[1]] = 255
    return image, origin


def _mask_points(
    image: Image.Image,
    threshold: int = 127,
    *,
    origin: tuple[int, int] = (0, 0),
) -> set[tuple[int, int]]:
    pixels = image.load()
    width, height = image.size
    return {
        (x + origin[0], y + origin[1])
        for y in range(height)
        for x in range(width)
        if pixels[x, y] > threshold
    }


def _safe_filtered_mask(
    original: set[tuple[int, int]],
    filtered: Image.Image,
    *,
    origin: tuple[int, int] = (0, 0),
    minimum_retained: float = 0.55,
    maximum_growth: float = 1.20,
) -> set[tuple[int, int]]:
    """Reject a cleanup filter when it materially changes source geometry."""
    result = _mask_points(filtered, origin=origin)
    if not result:
        return set(original)
    ratio = len(result) / max(1, len(original))
    if ratio < minimum_retained or ratio > maximum_growth:
        return set(original)
    return result


def filter_underlay_mask(
    mask: set[tuple[int, int]],
    *,
    inset: bool,
) -> tuple[set[tuple[int, int]], list[str]]:
    """Close small raster gaps and optionally inset a support silhouette."""
    if len(mask) < 9:
        return set(mask), ["identity_small_object"]
    image, origin = _cropped_mask_image(mask)
    closed = image.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    filters = ["binary_close_3px"]
    if inset:
        candidate = closed.filter(ImageFilter.MinFilter(3))
        filters.append("binary_inset_1px")
    else:
        candidate = closed
    return _safe_filtered_mask(mask, candidate, origin=origin), filters


def filter_blocking_mask(
    mask: set[tuple[int, int]],
) -> tuple[set[tuple[int, int]], list[str]]:
    """Remove isolated one-pixel edge noise without redesigning the region."""
    if len(mask) < 25:
        return set(mask), ["identity_small_object"]
    image, origin = _cropped_mask_image(mask)
    filtered = image.filter(ImageFilter.MedianFilter(3))
    return _safe_filtered_mask(
        mask,
        filtered,
        origin=origin,
        minimum_retained=0.82,
        maximum_growth=1.08,
    ), ["binary_median_3px", "geometry_change_guard"]


def detail_edge_response(source: Image.Image) -> Image.Image:
    """Return a multichannel difference-of-Gaussians edge response."""
    rgb = source.convert("RGB")
    response = Image.new("L", rgb.size)
    for channel in rgb.split():
        fine = channel.filter(ImageFilter.GaussianBlur(radius=0.65))
        coarse = channel.filter(ImageFilter.GaussianBlur(radius=1.8))
        difference = ImageChops.difference(fine, coarse)
        gradient = channel.filter(ImageFilter.FIND_EDGES)
        response = ImageChops.lighter(
            response,
            ImageChops.lighter(difference, gradient),
        )
    return response


def detail_foreground_mask(
    source: Image.Image,
    foreground: set[tuple[int, int]],
    *,
    suppress_boundary: bool = False,
    response: Image.Image | None = None,
) -> tuple[set[tuple[int, int]], dict]:
    """Run a multiscale, color-aware scan for candidate line detail."""
    if not foreground:
        return set(), {
            "method": "multiscale_color_edge_otsu",
            "threshold": 0,
            "pixels": 0,
            "filters": [],
        }
    response = response or detail_edge_response(source)
    response_pixels = response.load()
    sample_mask = set(foreground)
    filters = [
        "rgb_gaussian_0.65px",
        "rgb_gaussian_1.8px",
        "difference_of_gaussians",
        "rgb_edge_max",
    ]
    if suppress_boundary and len(foreground) >= 25:
        cropped, origin = _cropped_mask_image(foreground)
        eroded = _mask_points(
            cropped.filter(ImageFilter.MinFilter(7)),
            origin=origin,
        )
        if eroded:
            sample_mask = eroded
            filters.append("outer_boundary_suppression_3px")
    values = [response_pixels[x, y] for x, y in sample_mask]
    threshold = max(6, otsu_threshold(values))
    mask = {
        point for point in sample_mask
        if response_pixels[point[0], point[1]] > threshold
    }
    return mask, {
        "method": "multiscale_color_edge_otsu",
        "threshold": threshold,
        "pixels": len(mask),
        "sample_pixels": len(sample_mask),
        "filters": filters,
    }


def analyze_pass_filters(
    source: Image.Image,
    component: set[tuple[int, int]],
    assigned: dict[tuple[int, int], int],
    *,
    pixel_scale: float,
    pixel_area_mm2: float,
    detail_response: Image.Image | None = None,
) -> tuple[dict, dict[str, set[tuple[int, int]]]]:
    """Measure one visual element and prepare purpose-specific pass masks.

    The output is intentionally inspectable. Eligibility decisions remain
    deterministic and are returned with their thresholds so they can be
    coached and later replaced by an editable object proposal.
    """
    min_x, max_x = min(x for x, _ in component), max(x for x, _ in component)
    min_y, max_y = min(y for _, y in component), max(y for _, y in component)
    width_px = max_x - min_x + 1
    height_px = max_y - min_y + 1
    neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1))
    boundary_edges = sum(
        (x + dx, y + dy) not in component
        for x, y in component
        for dx, dy in neighbors
    )
    area_mm2 = len(component) * pixel_area_mm2
    perimeter_mm = boundary_edges * pixel_scale
    estimated_width_mm = 2 * area_mm2 / max(perimeter_mm, pixel_scale)
    span_long_mm = max(width_px, height_px) * pixel_scale
    span_short_mm = min(width_px, height_px) * pixel_scale
    aspect_ratio = span_long_mm / max(pixel_scale, span_short_mm)
    color_counts = Counter(
        assigned[point] for point in component if point in assigned
    )
    color_total = sum(color_counts.values())
    color_entropy = -sum(
        (count / color_total) * log2(count / color_total)
        for count in color_counts.values()
    ) if color_total else 0.0

    elongated_shape = (
        estimated_width_mm <= 1.8
        and span_long_mm >= 2.5
        and aspect_ratio >= 2.0
    )
    running_stroke = elongated_shape and estimated_width_mm < 1.25
    edge_mask, edge_report = detail_foreground_mask(
        source,
        component,
        suppress_boundary=not running_stroke,
        response=detail_response,
    )
    edge_density = len(edge_mask) / max(1, len(component))
    color_complexity = len(color_counts)
    underlay_eligible = (
        area_mm2 >= 0.6
        and (
            estimated_width_mm >= 1.2
            or span_long_mm >= 5.0
        )
    )
    silhouette_eligible = (
        area_mm2 >= 2.0 and estimated_width_mm >= 1.5
    )
    detail_eligible = (
        running_stroke
        or (
            area_mm2 >= 1.0
            and len(edge_mask) >= max(4, round(sqrt(len(component))))
            and (
                color_complexity >= 2
                or color_entropy >= 0.35
                or 0.025 <= edge_density <= 0.45
            )
        )
    )
    if running_stroke:
        detail_mask = set(component)
        edge_report = {
            **edge_report,
            "method": "narrow_stroke_geometry",
            "pixels": len(detail_mask),
            "filters": ["physical_width_gate", "dominant_axis_centerline"],
        }
    else:
        detail_mask = edge_mask if detail_eligible else set()

    underlay_mask, underlay_filters = filter_underlay_mask(
        component,
        inset=silhouette_eligible,
    )
    blocking_mask, blocking_filters = filter_blocking_mask(
        component
    )
    report = {
        "features": {
            "pixels": len(component),
            "area_mm2": area_mm2,
            "estimated_width_mm": estimated_width_mm,
            "bbox_width_mm": width_px * pixel_scale,
            "bbox_height_mm": height_px * pixel_scale,
            "aspect_ratio": aspect_ratio,
            "geometric_edge_count": boundary_edges,
            "color_complexity": color_complexity,
            "color_entropy_bits": color_entropy,
            "image_edge_count": len(edge_mask),
            "image_edge_density": edge_density,
        },
        "passes": {
            "underlay": {
                "eligible": underlay_eligible,
                "silhouette_eligible": silhouette_eligible,
                "filters": underlay_filters,
                "output_pixels": len(underlay_mask) if underlay_eligible else 0,
            },
            "blocking": {
                "eligible": area_mm2 >= 0.08 and not running_stroke,
                "filters": blocking_filters,
                "output_pixels": (
                    len(blocking_mask) if not running_stroke else 0
                ),
                "replaced_by_running_detail": running_stroke,
            },
            "detail": {
                "eligible": detail_eligible,
                **edge_report,
            },
        },
    }
    return report, {
        "underlay": underlay_mask if underlay_eligible else set(),
        "silhouette": underlay_mask if silhouette_eligible else set(),
        "blocking": set() if running_stroke else blocking_mask,
        "detail": detail_mask,
    }

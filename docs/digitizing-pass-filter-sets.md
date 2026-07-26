# Digitizing pass filter sets

Status: implemented deterministic baseline
Date: 2026-07-24

## Purpose

Underlay, blocking, and detail have different manufacturing goals and should
not consume one undifferentiated foreground mask. The raster planner now
measures each connected visual element once, then prepares a purpose-specific
mask and eligibility decision for every pass.

These filters are an inspectable baseline. They do not replace the editable
object graph required by the world-class guide.

## Shared element measurements

Each element records:

- physical area;
- estimated width from area and perimeter;
- physical bounding width and height;
- aspect ratio;
- four-neighbor geometric edge count;
- quantized color count;
- color entropy;
- multiscale RGB image-edge count; and
- image-edge density.

The plan exposes these under
`posterization.element_filter_analysis[].features`.

## Underlay filter set

Goal: establish useful support without automatically duplicating foundations.

Image operations:

1. binary closing with a `3 px` kernel to bridge isolated one-pixel gaps;
2. optional one-pixel binary inset for broad silhouette support; and
3. a geometry-change guard that falls back to the original mask if filtering
   removes more than 45% or expands it by more than 20%.

Eligibility:

- object support requires at least `0.6 mm²`; and
- either estimated width of at least `1.2 mm` or a long span of at least
  `5 mm`.

The low-density silhouette foundation has a stronger gate:

- at least `2 mm²`; and
- estimated width of at least `1.5 mm`.

Long narrow objects can therefore receive center support without necessarily
receiving a broad tatami silhouette.

## Blocking filter set

Goal: preserve the visible color mass while removing isolated raster noise.

Image operations:

1. binary median filter with a `3 px` kernel; and
2. a strict geometry-change guard.

The filtered mask is rejected unless it retains at least 82% of the source
pixels and grows by no more than 8%. Small objects below 25 pixels bypass the
filter entirely.

Source-derived blocking objects still use the physical speck threshold of
`0.08 mm²`. DRC movement limits do not delete blocking geometry.

## Detail filter set

Goal: propose running-stitch detail only when image and physical evidence
support an intentional line.

Image operations for broad objects:

1. per-channel Gaussian blur at `0.65 px`;
2. per-channel Gaussian blur at `1.8 px`;
3. difference-of-Gaussians response;
4. per-channel edge response;
5. maximum response across RGB channels, retaining chromatic edges that have
   similar luminance;
6. three-pixel outer-boundary suppression for broad elements; and
7. Otsu thresholding with a noise floor of 6.

Detail is eligible when either:

- the element is a running-width stroke: estimated width below `1.25 mm`, long span
  at least `2.5 mm`, and aspect ratio at least 2; or
- it is at least `1 mm²`, has enough edge pixels relative to object size, and
  has evidence from multiple colors, color entropy, or bounded internal edge
  density.

Running-width strokes use their geometry directly and are converted to a
smoothed dominant-axis centerline. Their blocking mask is disabled so the same
line is not sewn once as a solid and again as detail. Wider elongated objects
remain blocking candidates and may qualify for satin. Broad flat single-color
objects are rejected from the detail pass. Derived detail remains subject to
the configured object cap.

## Plan output

`posterization.pass_filter_sets` reports aggregate pass goals, filters,
criteria, and output sizes.

`posterization.element_filter_analysis` reports, for each parent element:

- the shared features;
- pass eligibility;
- the exact filters selected;
- threshold and sample information for detail; and
- output mask size.

Each generated region also retains the parent element's pass decisions in
`stitch_diagnostics.pass_filters`.

## Known limitations

- Pixel kernel sizes are currently expressed in pixels, although eligibility
  is expressed in physical units. Kernels should eventually be scaled from a
  physical radius and capped for computational cost.
- Connected foreground is still only a visual-element proposal, not an
  approved semantic object.
- Color complexity is based on the reduced palette rather than a perceptual
  thread model.
- Narrow-stroke centerlines remain dominant-axis approximations.
- Detail eligibility does not yet label outline, internal detail, texture, or
  duplication against an existing blocking edge.
- Filter decisions are deterministic suggestions; they are not yet editable
  in the UI.

## Next calibration step

Build a fixture matrix containing:

- flat single-color fills;
- narrow straight and curved strokes;
- multi-color internal details;
- textured artwork;
- noisy antialiased edges;
- small isolated marks; and
- designs with holes and intentional negative space.

For every fixture, retain source, pass masks, decision report, semantic stitch
plan, encoded preview, and physical sew-out result. Tune criteria by product
profile rather than introducing one universal threshold set.

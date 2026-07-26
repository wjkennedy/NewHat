# New Hat autopunch: current state and gap analysis

> Historical note: this document begins with the original scanline
> demonstrator and contains later implementation updates. For the current
> end-to-end flow and human coaching questions, use
> [`digitizing-flow-human-review.md`](digitizing-flow-human-review.md).

## Purpose

This document describes the prototype as it exists today. It is deliberately factual: the current raster path is a **posterized scanline demonstrator**, not a production embroidery digitizer.

The observed result — predominantly horizontal running stitches that begin immediately in fill — follows directly from that implementation.

## Current pipeline

```text
PNG / JPEG / WebP / SVG
  ↓ browser rasterization and placement transform
posterized pixel palette (2–15 bins)
  ↓
one connected horizontal scanline run per color region
  ↓
browser operation list: JUMP, STITCH, TRIM, COLOR_CHANGE
  ↓
Python codec adapter
  ↓
Tajima DST
  ↓
same Python codec reads DST back
  ↓
DST preview / ESP9000-style playback
```

### Browser-side raster translation

`prototype/app.js` currently owns all artwork interpretation.

1. It rasterizes the selected artwork to a `360 × 165` canvas.
2. It applies scale, rotation, mirror, and horizontal placement before sampling pixels.
3. It creates a palette by frequency-ranking quantized RGB bins and retaining the requested two to fifteen colors.
4. Every non-background pixel is assigned to the nearest retained RGB color.
5. For each palette color, it walks image rows from top to bottom. Each contiguous same-color run becomes a left-to-right or right-to-left row of stitches.
6. It inserts a jump before, and a trim after, every run. It inserts a color change between non-empty palette blocks.

The current nominal constraints are approximately `0.42 mm` row spacing and at most `2.5 mm` between horizontal stitch samples. Coordinates sent to the DST compiler are physical millimetres, not display pixels.

### Current emitted structure

For a connected region, the effective output is currently:

```text
JUMP to first horizontal run
STITCH across run
TRIM
JUMP to next horizontal run
STITCH across run
TRIM
...
COLOR_CHANGE
```

This is why the preview looks busy and horizontal. It does not model a filled embroidery object.

## Python tools: what they do today

`prototype/server.py` adds the local `pystitch/src` directory to Python's import path and exposes three operations:

| Function | Current responsibility |
| --- | --- |
| `as_dst()` | Converts browser operations to `EmbPattern` calls, then writes Tajima DST. |
| `inspect_dst()` | Reads a DST with `pystitch.read_dst()` and returns decoded stitches, jumps, trims, and color changes. |
| HTTP endpoints | Export, inspect an existing DST, or compile-and-read-back a preview. |

The adapter maps millimetres to `pystitch`'s internal tenth-millimetre units. It uses:

```python
pattern.stitch_abs(x, y)
pattern.move_abs(x, y)
pattern.trim()
pattern.color_change()
write_dst(pattern, output)
```

This is the right division of responsibility for a codec. `pystitch` is preserving and encoding an instruction sequence; it is not deciding what an artwork region should become.

### What `pystitch` is not doing

It does not currently provide this project with:

- color segmentation or cleanup;
- connected-component extraction;
- boundary or hole detection;
- path simplification;
- running-stitch sampling of an outline;
- fill routing;
- underlay;
- satin-column detection;
- tie-in / tie-off policy;
- stitch-order optimisation;
- density, pull, or fabric analysis.

Those are digitizer responsibilities and must exist before the codec boundary.

## Ink/Stitch: relevant reference logic

Ink/Stitch is considered here as an architectural and algorithmic reference, not as a component we currently invoke.

### Architecture that applies

Ink/Stitch separates geometry from the resulting stitch plan. Its implementation has dedicated stitch generators, routing utilities, validation, a stitch-plan representation, and a separate simulator. That is the separation New Hat should adopt.

Relevant source areas in the `wjkennedy/inkstitch` fork include:

```text
lib/stitches/fill.py
lib/stitches/running_stitch.py
lib/stitches/auto_satin.py
lib/stitches/utils/autoroute.py
lib/stitch_plan/
lib/elements/validation.py
lib/gui/simulator/
```

### Fill logic worth carrying forward

The reference fill implementation intersects a geometric region with a rotated grating, turns intersections into contiguous runs, routes those runs, and samples each run to a maximum stitch length. It also supports staggered rows so successive rows do not place needle penetrations in the same vertical columns.

That differs fundamentally from the prototype's pixel-row scan. A proper fill needs a **region polygon with holes**, an angle, row spacing, stitch length, start/end points, and routing policy.

Ink/Stitch's documented fill settings include maximum fill-stitch length, minimum stitch length, row spacing, staggers, start/end positions, trims, lock stitches, and underlay. Its AutoFill underlay is normally offset 90° from the top fill, uses a wider row spacing, and is inset so it does not show beyond the edge.

### Object types worth carrying forward

Ink/Stitch distinguishes:

| Source geometry | Manufacturing interpretation |
| --- | --- |
| Open thin path | Running stitch, bean stitch, or manual stitch |
| Narrow bounded region / paired rails | Satin column or zig-zag |
| Closed region | Fill, guided fill, contour fill, etc. |

New Hat currently treats every retained raster area as a fill run. It has no classifier to select the appropriate interpretation.

### Practical order for a filled region

A robust basic filled object should use an explicit operation sequence:

```text
trim / move to start
tie-in
inset contour running-stitch underlay
perpendicular low-density fill underlay
staggered top fill
optional visible outline, normally after fill
tie-off
trim
```

The final outline is not necessarily first; it is different from an inset contour underlay. The former is a visible finish and the latter is structural support.

## Confirmed prototype evidence

The two DST fixtures illustrate the current gap:

| File | Stitches | Color changes | Color blocks | Trims | Jumps |
| --- | ---: | ---: | ---: | ---: | ---: |
| `new-hat-job (1).dst` | 1,758 | 5 | 6 | 404 | 432 |
| `santa_gnome.dst` | 11,663 | 15 | 16 | 27 | 194 |

The generated job has a disproportionately high trim/jump count because every scanline island is treated as a separate object. The comparison file is not a universal quality target, but it demonstrates that a real design can contain far fewer travel interventions relative to stitch count.

## Current gaps

### Geometry and segmentation

- No cleanup of anti-aliased edges, isolated pixels, holes, or narrow bridges.
- No connected-component or contour extraction.
- No vector region model retained after rasterization.
- Palette selection is frequency-based RGB binning, not perceptual color reduction or thread-palette matching.
- White is heuristically treated as thread only in two-color mode; this is not an explicit background/white-thread decision.

### Stitch construction

- No outlines.
- No true running-stitch path generation.
- No tie-in or tie-off.
- No edge-run, contour, or fill underlay.
- No satin-column strategy for narrow regions.
- No top-fill angle selection, stagger phase, or alternate fill styles.
- No intra-color routing across regions.
- Every run is trimmed, even when an underpath or short connector would be better.
- No object-order policy beyond palette order.

### Validation

- No min/max stitch-length check on the *compiled* output.
- No stitch-density field or penetration count.
- No seam, cap-field, or bill clearance evaluation in the current raw DST preview.
- No tiny-region, narrow-column, or hole-closure rules.
- No comparison between pre-compile `StitchPlan` and re-read DST beyond rendering the latter.

### Data model

- Browser operation dictionaries are the current de facto plan format.
- Operations lack source region ID, operation role (`underlay`, `top_fill`, `outline`), needle assignment, ties, confidence, or findings.
- DST cannot restore thread identities, region boundaries, or object semantics. The app therefore needs a persistent richer job format alongside the generated `.dst`.

## Recommended next architecture

Do not move the entire digitizer to Node merely because the editor is a browser app. Keep the web application responsible for interaction, preview, and ESP9000 emulation. Put geometry-heavy digitization behind a Python service or local worker.

```text
Browser UI
  ├─ artwork placement, palette approval, job controls
  ├─ DST preview and ESP9000 emulation
  └─ renders richer StitchPlan returned by the service

Python digitizer
  ├─ raster cleanup / posterization
  ├─ connected components → polygons with holes
  ├─ classify region: running / satin / fill
  ├─ create underlay, top stitching, ties, trims
  ├─ route and validate plan
  └─ return versioned StitchPlan JSON

pystitch adapter
  ├─ compile validated plan to DST
  └─ decode DST for round-trip comparison
```

## Recommended implementation order

1. Define a versioned `StitchPlan` JSON schema and operation metadata.
2. Implement one closed, single-color region end-to-end:
   - contour extraction;
   - 0.4–0.6 mm inset edge-run underlay;
   - perpendicular, low-density fill underlay;
   - staggered top fill;
   - tie-in, tie-off, and trim.
3. Add compiled-plan validation: extents, color blocks, min/max stitch length, trim/jump counts, and density warnings.
4. Add connected-component ordering and safe intra-color routing.
5. Add palette-to-thread matching and deterministic 15-needle assignment.
6. Add narrow-region classification and satin columns.
7. Reintroduce 2–15-color raster jobs only after each region can be digitized as a proper object.

## Implemented kernel progress

The current `prototype/digitizer.py` now quantizes only visible artwork pixels,
extracts connected color components, traces closed pixel-cell contours
(including holes), preserves region IDs and operation roles, and groups
disconnected components of the same thread color into a single thread block.
A deterministic nearest-entry order reduces same-color travel without
fabricating unsafe connectors; each disconnected object still retains its own
trim.

This is an intentionally limited routing policy. It fixes the prior error in
which every component implied a color change, but it is not yet an underpath,
visibility-aware, or cap-aware router.

## References

- [Ink/Stitch workflow](https://inkstitch.org/docs/workflow/)
- [Ink/Stitch AutoFill and underlay parameters](https://inkstitch.org/developers/inkstitch/namespace/)
- [Importance of underlay](https://inkstitch.org/tutorials/underlay/)
- [Ink/Stitch stroke and autoroute tools](https://inkstitch.org/docs/stroke-tools/)
- [Ink/Stitch satin-column underlay](https://inkstitch.org/docs/stitches/satin-column/)
- [Local `pystitch` adapter](../prototype/server.py)
- [Current browser autopunch](../prototype/app.js)

## Training and learning-loop requirements

The current system is not an appropriate target for machine learning: its only available target is a fragmented horizontal scan pattern. Training against that would reinforce the prototype's main defect.

The first learned components should instead predict semantic decisions from structured geometry:

```text
artwork / image
  → regions and paths
  → object classification
  → stitch-family and parameter predictions
  → object ordering and entry/exit predictions
  → semantic StitchPlan
```

The deterministic compiler must retain responsibility for exact stitch placement, fill intersections, satin sampling, underlay, ties, movement subdivision, and DST serialization. A model should not emit raw DST records or bytes.

### Required datasets

| Dataset | Essential fields |
| --- | --- |
| Procedural ground truth | geometry, stitch family, parameters, order, generated plan, rendered image |
| Editable professional work | source art, editable objects, output DST, dimensions, profiles, corrections |
| Physical stitch-outs | material, machine, runtime, defects, interventions, corrected plan, quality rating |

Human correction is a primary product capability, not merely a UI convenience. Every correction should retain the original prediction, the corrected value, job context, findings, plan revision, and—when available—the result of the physical stitch-out.

## Evaluation requirements

Playback and raw stitch counts are useful diagnostics, but not quality metrics. Evaluation needs four levels:

| Level | Examples |
| --- | --- |
| Artwork | region recall, boundary deviation, color agreement, negative-space preservation |
| Plan | stitch-family correctness, parameter error, object fragmentation, ordering quality |
| Machine | stitch/jump/trim counts, runtime, stitch-length and density violations |
| Physical | coverage, registration, puckering, distortion, thread breaks, expert acceptance |

The eventual primary product metric is the percentage of generated jobs accepted for production without manual stitch-plan editing. Correction time and first-pass stitch-out success are complementary measures.

## Delivery phases

1. **Digitizer kernel:** one single-color polygon-with-holes object with edge-run underlay, perpendicular fill underlay, staggered top fill, ties, trim, and round-trip validation.
2. **Semantic job model:** versioned plan schema, object IDs, operation roles, profiles, source traceability, persisted job files, and findings.
3. **Routing:** connected-component ordering, same-color grouping, entry/exit selection, underpath connectors, trim policy, and cap-aware constraints.
4. **Stitch vocabulary:** running stitch, satin column, tatami fill, visible outlines, underlay families, narrow-region classification, and tiny-detail suppression.
5. **Learning foundation:** procedural dataset generation, analytical renderer, human-correction capture, and baseline region/stitch-family models.
6. **Production calibration:** material and machine profiles, density calibration, compensation learning, physical stitch-out feedback, and quality optimization.

## Build-versus-reference decision

| Area | Direction |
| --- | --- |
| DST read/write | Keep `pystitch` as the codec adapter. |
| Semantic plan and profiles | Build in New Hat. |
| Geometry extraction | Build around existing geometry/image libraries. |
| Running, fill, satin, and routing | Adapt proven Ink/Stitch ideas; extend for cap-specific needs. |
| Validation and simulator | Build in New Hat using Ink/Stitch as a behavioral reference. |
| Training and physical calibration | Build and own; this is product-specific data. |

Ink/Stitch is therefore both an algorithmic reference and a source of known-good deterministic behavior. It is not a substitute for New Hat's article-aware plan model, correction loop, or production data.

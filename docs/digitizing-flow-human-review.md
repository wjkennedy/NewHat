# Digitizing flow: human review and coaching brief

Status: current implementation review
Date: 2026-07-24
Scope: raster auto-digitizing, editable-vector planning, DRC, and training data

Normative companion:
[`world-class-programmatic-embroidery-digitizing-guide.md`](world-class-programmatic-embroidery-digitizing-guide.md)

## Purpose

This document describes what the prototype does today, not what an expert
digitizer would necessarily choose. It is intended to support direct critique
of the decisions that turn artwork into stitches.

The central review question is:

> At which point does the system stop representing the design and begin
> manufacturing plausible-looking stitches from weak geometric heuristics?

The current implementation is entirely deterministic. No machine-learning
model participates in segmentation, object recognition, stitch-family
selection, sequencing, or stitch placement.

## Executive summary

The prototype has two related but distinct planning paths:

1. **Raster auto-digitizing** accepts an image, estimates its foreground,
   reduces its colors, extracts connected pixel regions, classifies each
   region as satin or tatami, creates a separate high-frequency running-stitch
   detail scan, and emits underlay, blocking, and detail passes.
2. **Editable-vector planning** accepts authored Bézier paths, satin rails,
   tatami boundaries, layers, and explicit stitch types. It preserves much
   more intent and generates stitches from that authored geometry.

The raster path is currently the weak link. It has no concept of letters,
strokes, negative space, foreground/background objects, semantic edges,
intentional overlaps, or visual importance. It subdivides connected artwork
by quantized color and then makes local decisions from area, perimeter,
bounding box, holes, and approximate rails.

The result can be mechanically valid while remaining visually unlike the
design. DRC currently reports rule violations and movement budgets; it does
not repair a poor object model or prove that the result will sew well.

## Guide-directed verdict

The world-class guide establishes that the first product of digitizing must be
an editable, intent-preserving object graph. The present raster planner skips
that product and promotes thresholded color components directly into
embroidery regions. Satin, tatami, underlay, detail, and routing decisions are
therefore being made before the system knows what the design objects mean.

The primary recommendation is to pause further tuning of raster thresholds,
stitch counts, and movement budgets as the main improvement strategy. The
next milestone should be:

```text
transformed artwork and job profile
  -> supervised foreground and visual objects
  -> editable object/layer/overlap graph
  -> construction proposal with confidence
  -> deterministic stitch generation
  -> repair-capable DRC
  -> machine compilation
  -> profile-specific sew-out record
```

Three physical passes should not be treated as a mandatory construction for
every design. A design may require foundation, blocking, and detail roles, but
each object must justify its own underlay and visible passes. Existing support
from earlier objects, hidden travel, or the selected material may make another
foundation redundant or harmful. Likewise, a generic final detail scan is not
a substitute for authored or recognized outlines and internal detail.

The guide's optimization order is also important when interpreting DRC:

1. preserve design identity and hierarchy;
2. achieve a stable, repeatable sew-out;
3. preserve registration and clean edges;
4. avoid material and machine failure;
5. preserve acceptable hand and flexibility;
6. avoid visible or unsecured travel; and
7. only then optimize trims, jumps, color changes, runtime, and stitch count.

## Current system boundary

```text
Source image
  |
  +-- sizing and resampling
  |
  +-- foreground estimation (alpha or luminance threshold)
  |
  +-- foreground-only color quantization
  |
  +-- connected elements and color subdivisions
  |
  +-- pixel-cell contours and simple physical measurements
  |
  +-- local stitch-family classification
  |     +-- narrow/simple -> satin candidate
  |     +-- broad/irregular/holed -> tatami
  |     +-- high-frequency detail -> running
  |
  +-- three physical passes
  |     +-- design and object underlay
  |     +-- visible color blocking
  |     +-- running-stitch detail
  |
  +-- same-color ordering and limited connector retention
  |
  +-- stitch-length enforcement and DRC
  |
  +-- machine-operation stream
  |
  +-- DST encoding and decode-back preview
```

The editable-vector path enters near the middle of this diagram. It replaces
foreground estimation, color quantization, contour extraction, and automatic
classification with an authored document containing explicit objects and
stitch types.

## Raster auto-digitizing flow in detail

### 1. Input, dimensions, and resampling

The service decodes the uploaded image as RGBA. Images are reduced to at most
`1024 x 1024` pixels. Most artwork uses Lanczos resampling; artwork recognized
as high-contrast binary art uses nearest-neighbor resampling.

Physical dimensions are fitted into both:

- the requested width; and
- a hard-coded maximum height of `55 mm`.

The aspect ratio is retained. The requested width is therefore a maximum, not
always the final width.

Important UI limitation: rotation, mirroring, and left/center/right placement
currently change the browser presentation, but the raster request sends the
original image plus width, color count, and density. Those presentation
transforms are not baked into the image or passed as planning parameters.

### 2. Foreground extraction

Foreground geometry is chosen independently from palette color:

- If meaningful transparency exists, Otsu thresholding is applied to alpha.
- If opaque artwork contains multiple significant hue families, the border
  supplies an adaptive background reference. Background-similar pixels are
  removed while distinct white/cream, yellow, and light-color objects remain.
- If opaque artwork has one significant hue family, Otsu luminance supplies a
  tighter silhouette that avoids manufacturing objects from JPEG fringe.
- A uniform opaque image is treated entirely as foreground.

This remains a deterministic foreground decision. There is no learned matting,
edge-confidence map, or user-approved mask.

This stage can fail when:

- artwork touches the image border;
- the background is textured or shaded;
- foreground and background overlap in luminance;
- antialiasing creates weak edge pixels; or
- negative space should be interpreted from design structure.

### 3. Palette assignment

Only foreground pixels participate in color quantization. The user requests
between 2 and 15 colors. Multicolor art uses an accent-preserving octree so
small, distinct thread families are not displaced by a dominant field.
One-hue art uses median cut so antialiased shades can be bracketed and
consolidated into a physical thread.

Color is an attribute after foreground extraction, but it still controls
subdivision: each connected foreground element is split into connected
components for every quantized color.

The implementation does not currently provide:

- perceptual color-distance control;
- thread-chart matching;
- protected brand colors;
- merge/split supervision;
- minimum contrast between adjacent thread colors;
- intentional blending;
- gradient interpretation; or
- an explicit upper-color/lower-color overlap model.

### 4. Geometry elements and color regions

Four-connected components in the foreground mask establish parent geometry
elements. Each parent is then subdivided by palette color. A color subdivision
becomes an embroidery region if its area is at least `0.08 mm²`.

Each region receives:

- a stable region and parent-element ID;
- its quantized color;
- pixel area and bounds;
- pixel-cell contour loops, including holes;
- area, perimeter, compactness, aspect ratio, and estimated width; and
- a candidate stitch type.

The estimated width is approximately `2 x area / perimeter`. It is a useful
shape measure, but it is not a medial-axis width field.

Contour geometry remains derived from square pixel cells. Collinear grid
vertices are removed, but there is no curve fitting, topology-aware smoothing,
corner classification, or error-bounded Bézier approximation in this path.

### 5. Stitch-family classification

For ordinary artwork, every interior region starts as tatami.

A region becomes a satin candidate only when:

- its estimated width is below the profile's satin limit;
- its bounding-box aspect ratio is at least `1.35`;
- it has no holes; and
- a single-axis paired-rail construction passes width and consistency checks.

For wearable profiles, the effective satin width ceiling is `6 mm`. Satin
rails are sampled on the dominant horizontal or vertical axis. A five-sample
moving average reduces one-pixel rail jitter.

This is not a general satin-column solver. It does not infer a curved
centerline, branch points, turning points, stitch-angle guides, or paired rails
along arbitrary stroke flow. Curved lettering and organic narrow shapes can
therefore be rejected as tatami or receive poorly oriented satin.

Running stitch is not selected for ordinary source regions. It is reserved for
the separately derived detail scan. High-contrast binary artwork bypasses
satin classification and is emitted as fill with special sampling settings.

### 6. Independent detail scan

The source luminance is blurred with radius `1.6`, then subtracted from the
original to create a high-pass image. Otsu thresholding, with a minimum
threshold of 6, identifies high-frequency foreground pixels.

Connected high-frequency components become running-stitch detail candidates:

- components below the physical speck threshold are removed;
- at most 48 detail objects are retained by default;
- each object takes the dominant quantized color beneath it; and
- a dominant-axis centerline is estimated and smoothed with a five-point
  moving average.

This scan does not distinguish an intentional outline from texture,
antialiasing, compression noise, a color boundary, or an edge already expressed
by satin/tatami. It can duplicate structure already present in the blocking
pass and can fragment one visual stroke into many disconnected paths.

### 7. Underlay pass

The machine sequence begins with design-level foundation stitches:

1. A dominant-axis center run follows the occupied silhouette and breaks at
   gaps.
2. Solid foreground components of at least `2 mm²` and approximately
   `1.5 mm` wide receive a contour plus low-density `90°` tatami silhouette.
3. Object-specific underlay is then emitted for all blocking objects, grouped
   by thread color.

Object-specific underlay depends on stitch type:

- Satin receives a center run. Columns with mean width at least `3.5 mm` also
  receive a zig-zag underlay.
- Tatami receives contour underlay around every loop. Small areas, narrow
  regions, and binary artwork also receive a sparse `135°` fill underlay.
- Running-detail objects receive no object underlay.

This can create overlapping foundation systems: design center run, silhouette
underlay, contour underlay, and sometimes object fill underlay. The code does
not calculate accumulated penetration density across those systems or decide
which foundation is redundant.

### 8. Blocking pass

The blocking pass emits the visible representation of every retained
source-derived color region.

- Satin alternates penetrations between the two derived rails.
- Tatami intersects the contour set with fixed `45°` rows.
- Tatami density is the profile spacing divided by the operator's density
  multiplier.
- Fill rows use even/odd contour parity so holes are preserved.
- Adjacent rows are joined only by a straight or orthogonal connector of at
  most `3 mm` that remains inside the region. Otherwise a jump starts the next
  row.

The current fill algorithm uses no object-specific angle selection, stagger
phase, contour inset, pull compensation, push compensation, shortened open
ends, edge-walk offset, or overlap allowance.

The `4 mm` fill rule is applied as a preferred minimum sampling interval on
each row, but a row shorter than 4 mm is still emitted as one shorter stitch.
DRC then flags it. This exposes a conceptual issue for coaching: short edge
rows are a geometric consequence of angled fill, so a blanket minimum cannot
be satisfied without a corner-routing or edge-treatment strategy.

### 9. Detail pass

After all blocking regions, the high-frequency centerline components are
emitted as approximately `1 mm` running stitch. Detail objects have a separate,
more relaxed trim allowance.

The current detail pass is not derived from the final blocked composition. It
does not reason about:

- which edges remain visible after layering;
- which boundaries need an outline;
- whether an outline should share or contrast with the underlying color;
- line hierarchy or visual importance;
- intentional open paths; or
- registration risk at a color boundary.

### 10. Object and color sequencing

The complete raster sequence is:

```text
design center run and silhouette underlay
all object underlay, iterating through palette colors
all blocking objects, iterating through palette colors
all detail objects, iterating through palette colors
```

Within a pass and color, objects are ordered by nearest next entry. Cap
profiles choose an initial object near the design center and route outward.

An object normally contains entry jump, tie-in, stitch body, tie-off, and
trim. For two consecutive same-color objects, the trim and ties can be removed
when their distance is:

- at most `7 mm` for normal blocking;
- at most `12.1 mm` for detail; or
- at most `12.1 mm` for underlay that will later be covered.

The jump remains a machine movement. The router does not globally optimize
entry/exit points, reverse paths, jointly route fill rows and objects, or
evaluate alternate color sequences. Repeating the palette for underlay,
blocking, and detail can add color changes even when a different physical
construction would be preferable.

### 11. Stitch criteria and DRC

Before export, non-tie stitches closer than the configured minimum are
collapsed. Oversized radial stitches are split to the profile's machine
maximum.

DRC reports:

- stitch, object, color-change, jump, and trim counts;
- estimated machine jumps after splitting long frame moves;
- structural trims separately from detail trims;
- minimum, maximum, and mean stitch length;
- semantic running, satin, and fill findings;
- occupied bounding-box area and stitches per square inch;
- penetration-count variance in `5 x 5 mm` cells;
- absolute and per-1,000-stitch movement budgets; and
- a heuristic quality score.

The budgets are warnings. The planner does not erase blocking geometry to
pass them, and it does not perform a repair/plan/validate loop.

The stitches-per-square-inch measure uses the occupied rectangular bounding
box, not the true sewn area. The quality score is a hand-weighted diagnostic,
not a learned or physically calibrated quality estimate.

### 12. DST compilation and preview

The semantic operation stream is converted to absolute stitch, move, trim,
and color-change commands in tenth-millimeter units. The generated DST is
immediately decoded for preview, so the simulator displays the encoded machine
path rather than only the pre-encoding plan.

DST is the final machine artifact, not the editable source of truth. It does
not retain the raster regions, Bézier geometry, stitch-family labels, underlay
roles, actual thread colors, layer relationships, or the reasons behind a
planning decision.

## Editable-vector flow

The full editor uses a separate document model with:

- Bézier nodes and handles;
- open running paths;
- paired satin rails;
- closed tatami boundary loops with holes;
- authored compositing layers and physical layers;
- explicit stitch types, colors, density, fill angle, and underlay control;
- sequence locks and stable object IDs; and
- overlap and neighbor analysis.

The vector planner creates a design-level center foundation, generates
stitch-type-specific object operations, sequences objects within hard layer
constraints, and hides a short connector only when later geometry covers it.

This path is closer to a production architecture because intent survives into
the stitch planner. It is not yet fed by a raster vectorizer capable of
recovering the same quality of objects, rails, boundaries, and layer
relationships.

The current posterized vectorizer uses alpha as a single color-agnostic
foreground mask. Touching colors become one perimeter. A long, narrow component
may become satin if simple axis-derived rails pass; otherwise it becomes
tatami. It does not yet produce internal color blocks, running-detail objects,
semantic layers, curved stroke skeletons, or overlap relationships from the
image.

## Where the present output is most likely going wrong

| Area | Present assumption | Likely symptom |
| --- | --- | --- |
| Foreground | One binary threshold can isolate the design | Missing or merged design areas |
| Elements | Four-connected pixels approximate visual objects | Excessive fragments or accidental bridges |
| Color | Quantized color regions are embroidery objects | Too many objects and no intentional overlaps |
| Contours | Simplified pixel-cell boundaries are adequate rails/perimeters | Stair-stepped edges and unstable satin |
| Satin | One dominant axis describes stroke direction | Poor curved text, leaves, arcs, and organic lines |
| Tatami | A fixed `45°` top angle suits every object | Flat visual flow and unnecessary distortion |
| Detail | High-frequency pixels correspond to final running detail | Noise, duplicate edges, jumps, and trims |
| Underlay | More complete foundation systems are generally safer | Excessive stitch count, stiffness, and bulk |
| Sequence | Pass/color grouping plus nearest entry is sufficient | Repeated color changes and weak registration |
| Travel | Short-distance rules are enough to retain thread | Visible floats or excessive machine moves |
| DRC | Count and length thresholds approximate quality | Passing plans that still look wrong |
| Preview | A correct machine stream predicts the stitch-out | Unseen fabric, tension, pile, and compensation failures |

## Gap matrix against the world-class guide

| Guide priority | Current state | Assessment | Required evidence of completion |
| --- | --- | --- | --- |
| Preserve transformations and job context | Width, colors, and density reach raster planning; rotation, mirror, placement, and a required production profile do not | Missing | The semantic job records original and transformed artwork, final dimensions, placement, profile, and versioned parameters |
| Build an editable object graph | Raster parents and color regions have IDs, but not visual roles, occlusion, negative-space meaning, merge/split supervision, or confidence | Foundational gap | A user can approve/correct objects, layers, overlaps, and negative spaces before stitch generation |
| General satin proposals | The raster path uses one dominant axis, simple paired spans, and local smoothing | Prototype only | Curved centerline and rails, angle guides, branches, corners, width transitions, split decisions, and unstable segments are editable |
| Replace generic high-pass detail | A luminance high-pass scan creates the entire automatic detail pass | Contradicts target architecture | Open paths, outlines, and internal details are recognized by role, checked for duplication, ranked by salience, and approved |
| Parameterize fill construction | Physical row spacing and holes exist, but top angle is fixed and there is no stagger, inset, angle field, edge policy, or compensation | Partial | Each fill exposes and scores angle, spacing, length, phase, inset, edge, hole, and connector alternatives |
| Material-aware support and compensation | Named profiles alter a few lengths and spacings; support can be duplicated and compensation/overlap are absent | Major gap | Object-level underlay, directional compensation, overlap, and combined penetration load are derived from a versioned production profile |
| Constrained global pathing | Nearest-entry routing and distance-limited thread retention operate inside pass/color groups | Partial | Hard compositing and registration constraints, entry/exit candidates, reversal, verified future coverage, seam risk, and product progression participate in one costed plan |
| Repair-capable DRC | Counts, lengths, movement budgets, and a heuristic score are reported | Diagnostic only | Findings have severity, affected object, mechanism, proposed repairs, predicted tradeoffs, incremental regeneration, and an explicit export decision |
| Predict physical behavior | Encoded machine playback is available | Missing | Comparative preview includes directional displacement, overlap/registration risk, buildup, sinking, and paired sew-out measurements |
| Structured learning loop | Corpus statistics exist; no learned component or correction capture exists | Missing | Licensed paired records retain object graphs, edits, profiles, machine files, simulation, sew-out measurements, photographs, and approval |

### Four truths to keep separate during review

Every critique should say which layer of truth it addresses:

| Truth | Review artifact |
| --- | --- |
| Visual intent | Source artwork, hierarchy, salience, and acceptable simplification |
| Digitizing intent | Objects, layers, stitch families, angles, support, compensation, and route constraints |
| Machine truth | Encoded stitches, jumps, trims, color/needle commands, and runtime |
| Physical truth | Profile-specific sew-out, measured distortion, coverage, registration, reliability, and hand |

A clean machine preview is evidence only for machine truth. A legacy machine
file is a construction example, not proof of the original visual intent or of
physical quality on the current product.

## Questions for a human digitizer

The most useful coaching will annotate specific artwork and answer these
questions in order.

### Object interpretation

1. What are the actual visual objects in the design?
2. Which objects sit behind, inside, or in front of others?
3. Which color boundaries should be overlaps rather than exact shared edges?
4. Which small details should be omitted, merged, enlarged, or represented by
   a different stitch type?
5. Where should negative space remain open?

### Stitch-family choice

1. Which shapes should be running, satin, tatami, appliqué, or intentionally
   unsupported by automatic conversion?
2. For each satin object, where are the two rails, centerline, turning points,
   split points, and angle changes?
3. For each tatami object, what angle supports its shape and adjacent objects?
4. Which details belong in the blocking object rather than a generic final
   scan?

### Foundation and compensation

1. Does the design need one global stabilizing run, object underlay, or both?
2. Which objects require center, contour, zig-zag, or tatami underlay?
3. Where is underlay redundant or likely to create bulk?
4. What pull/push compensation and overlap should be applied by object,
   direction, and material?

### Pathing

1. What is the intended start and end point of each object?
2. Which paths may be reversed?
3. Which travel is safe to hide under later work?
4. Where is a trim preferable even if a connector is short?
5. Should the design be routed center-out, bottom-up, back-to-front, or by a
   hybrid rule?
6. Where is an extra color change justified to improve registration?

### Acceptance

1. Which visual errors are worse than extra stitches or trims?
2. What count ranges are reasonable for this design and physical size?
3. Which measurements should block export, and which should remain warnings?
4. What must be checked in a physical sew-out that simulation cannot answer?

## Suggested coaching artifact

For each reviewed design, capture one row per intended embroidery object:

| Field | Example content |
| --- | --- |
| Object ID | Human-stable name |
| Visual role | Background, letter, border, highlight, detail |
| Parent/layer | Behind/in front of which object |
| Stitch family | Running, satin, tatami |
| Entry/exit | Coordinates or annotated points |
| Direction | Rail direction or fill angle |
| Density/length | Physical settings |
| Underlay | Type, angle, inset, spacing |
| Compensation | Pull, push, overlap |
| Travel policy | Hidden connector, jump, trim |
| Simplification | Keep, merge, omit, enlarge |
| Confidence | Certain, acceptable alternative, subjective |
| Evidence class | Foundation, profile heuristic, practitioner technique, anecdote |
| Sew-out result | Registration, puckering, coverage, stiffness, breaks |

This object table is more valuable for improving the planner than a single
overall rating.

## Corpus and learning implications

### What exists now

The indexed corpus currently contains 13,565 readable PES files and associated
metadata, plus tutorial/reference material. It is used for aggregate command
statistics and fixture selection. It is not connected to the automatic
digitizer as training data, and the current digitizer contains no learned
component.

Adding DST files would broaden machine-path coverage and provide useful
distributions for:

- stitches, jumps, trims, color blocks, and extents;
- local stitch vectors and angle changes;
- run-length and movement patterns;
- density and fragmentation baselines; and
- codec/simulator regression.

### What DST cannot teach by itself

A DST file does not directly identify:

- original artwork or intended perimeter;
- running versus satin versus tatami semantics;
- underlay versus visible top stitching;
- object and layer boundaries;
- actual thread colors;
- material, stabilizer, tension, or machine setup;
- compensation values;
- why a path was sequenced a particular way; or
- whether the physical result was good.

Those properties can sometimes be inferred from stitch geometry, order, and
density, but they are weak labels and must carry confidence and provenance.
Training directly from unannotated DST risks teaching common production
artifacts as design intent.

### Minimum useful paired dataset

The strongest training/evaluation record would pair:

1. source artwork and approved foreground mask;
2. physical design dimensions;
3. expert object geometry and layer order;
4. per-object stitch family, angle, density, underlay, and compensation;
5. the final machine file, including DST where available;
6. product, fabric, stabilizer, thread, needle, and machine profile;
7. rendered simulation and physical sew-out photographs; and
8. expert findings, corrections, and final acceptance.

Usage rights must be recorded per asset before designs or tutorial content are
used for model training.

### Appropriate learned and deterministic boundaries

A learned system is most useful for uncertain semantic choices:

- foreground and object segmentation;
- stroke versus area recognition;
- layer and occlusion inference;
- stitch-family suggestions;
- satin centerline/rail proposals;
- fill-angle and sequencing proposals; and
- salience-based detail simplification.

Deterministic geometry should remain responsible for:

- contour and rail constraints;
- exact stitch sampling;
- underlay construction from approved objects;
- connector containment;
- machine limits and DRC;
- operation ordering after explicit constraints; and
- machine-file encoding.

### Practical corpus work before model training

1. Add DST indexing beside the existing corpus index without treating it as
   semantic ground truth.
2. Build a stitch-stream analyzer that proposes running, satin, tatami,
   underlay, and object boundaries with confidence.
3. Select a small, licensed, stratified review set rather than training on the
   entire corpus immediately.
4. Pair each selected machine file with source artwork where available.
5. Have a digitizer correct the proposed object graph and record the edits.
6. Use the corrected graphs first as evaluation fixtures and only later as
   supervised training data.
7. Record physical sew-out results so optimization targets manufacturing
   quality, not merely similarity to legacy stitch streams.

## Recommended review sequence

Human review should proceed with a small set of representative failures:

1. one-color lettering with curves and narrow columns;
2. a flat multi-color logo with intentional overlap;
3. an irregular mascot or illustrated mark;
4. small running-stitch detail over a broad fill;
5. a design crossing a cap center seam; and
6. a high-pile material case.

For each design, compare:

- source artwork;
- foreground mask;
- quantized color map;
- extracted regions and contours;
- proposed stitch-family labels;
- foundation only;
- blocking only;
- detail only;
- complete slow redraw;
- DRC report; and
- physical sew-out.

The immediate goal is not to tune more thresholds. It is to identify which
planning decisions require a better representation of design intent.

## Code map

| Responsibility | Current implementation |
| --- | --- |
| Foreground and palette | `prototype/posterize.py` |
| Raster regions and stitch plan | `prototype/digitizer.py` |
| Posterized image to vector document | `prototype/vectorize.py` |
| Editable geometry and vector stitch planning | `prototype/vector_document.py` |
| API and DST adapter | `prototype/server.py` |
| Browser controls and preview | `prototype/app.js` |
| Raster planner tests | `prototype/test_digitizer.py` |
| Vector planner tests | `prototype/test_vector_document.py` |
| Corpus summary | `docs/pes-corpus-index.md` |

The automated tests verify deterministic mechanics such as masks, holes,
layers, semantic operation roles, rail smoothing, stitch splitting, routing
counts, and codec round trips. They do not currently provide perceptual
similarity scoring or physical stitch-out acceptance.

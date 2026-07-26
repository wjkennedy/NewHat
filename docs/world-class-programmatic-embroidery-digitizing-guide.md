# World-class programmatic embroidery digitizing

**Status:** working craft and engineering guide
**Date:** 2026-07-24
**Scope:** artwork interpretation, object planning, stitch generation, material-aware DRC, machine compilation, corpus development, and physical validation
**Technology boundary:** `pystitch`, `libembroidery`, PES/DST corpora, deterministic geometry, and learned planning assistance

## Purpose

This guide defines what a world-class programmatic digitizing system must know
and preserve before it emits stitches.

The project is not an image-to-DST converter. It is an embroidery CAM system
whose output happens to include DST and PES. Its job is to interpret visual
intent, choose a manufacturable construction, plan how thread and fabric will
interact, and compile that plan into a reliable machine operation stream.

The key distinction is:

> A stitch file is the compiled result of digitizing, not the design model and
> not the source of design intent.

`pystitch` and `libembroidery` are valuable at the machine-file boundary. They
read, write, normalize, inspect, and convert embroidery command streams. They
do not, by themselves, determine which parts of artwork are objects, how those
objects overlap, which stitch family expresses each object, or how the design
must change for a particular garment.

The existing implementation review is retained as **Appendix A**. The main
body establishes the craft standard against which that implementation should
be evaluated.

---

## 1. How to use this guide

Every recommendation is assigned one of four evidence classes.

| Class | Meaning | Appropriate use |
| --- | --- | --- |
| **Foundation** | Repeated across commercial manuals, open-source digitizing documentation, and experienced trade educators | Default architecture or rule |
| **Profile heuristic** | Widely used for a specific material, machine, size, or product | Parameterized starting point |
| **Practitioner technique** | Credible method used by an experienced digitizer, but not universal | Candidate strategy tested by sew-out |
| **Anecdote** | Forum, social, or historical discussion without controlled evidence | Hypothesis, corpus lead, or failure example |

This distinction matters because embroidery advice often appears as an
absolute rule even when it is specific to one thread, fabric, machine,
software convention, or design size.

### 1.1 Normalize every parameter to physical units

Commercial software uses inconsistent definitions and scales for density,
compensation, and stitch settings. The programmatic system should store and
reason in canonical physical units:

- dimensions, stitch lengths, insets, overlaps, and compensation in
  millimeters;
- fill row spacing in millimeters between rows;
- stitch direction in degrees or unit vectors;
- thread by weight/Tex and material;
- needle by standardized size and point type;
- fabric by measured or classified mechanical properties;
- machine movement and command limits in machine units plus millimeters; and
- penetration concentration as a local spatial and temporal measurement.

Software-specific sliders may be imported or exported, but they should never
be the internal source of truth.

### 1.2 Separate four kinds of truth

A production record should distinguish:

1. **Visual intent:** what the artwork is meant to communicate.
2. **Digitizing intent:** the object graph, layers, stitch families, angles,
   underlay, compensation, and routing.
3. **Machine truth:** the actual sequence of stitches, jumps, trims, stops,
   color changes, and needle assignments.
4. **Physical truth:** what happened on the selected fabric, stabilizer,
   thread, needle, machine, hoop, speed, and tension.

A preview can validate machine truth. Only a sew-out can validate physical
truth.

---

# Part I: The craft standard

## 2. Embroidery is material-aware rendering

Embroidery does not reproduce an image by depositing independent pixels.
Every stitch:

- has length and direction;
- pulls along its axis;
- displaces thread and fabric across its width;
- changes the support available to later stitches;
- creates friction and needle penetrations;
- may be visible on the face or reverse;
- may be affected by nap, stretch, weave, seam, backing, topping, and hooping;
- and is executed in time, making order part of the design.

A world-class planner therefore optimizes a **construction**, not a bitmap.

### 2.1 The primary optimization order

When objectives conflict, use this order unless a product profile explicitly
overrides it:

1. preserve the design's identity and hierarchy;
2. produce a stable, repeatable sew-out;
3. preserve registration and clean edges;
4. avoid thread breaks, fabric damage, and machine faults;
5. retain acceptable hand, drape, and flexibility;
6. minimize visible travel and unsecured thread;
7. reduce trims, jumps, color changes, runtime, and stitch count.

This ordering prevents an optimizer from destroying the design merely to
achieve a lower movement count.

### 2.2 A design is allowed to change

Source artwork is not automatically embroidery-ready. The planner must be able
to propose or require:

- simplifying small texture;
- enlarging counters and negative spaces;
- widening narrow stems;
- reducing color count;
- merging visually equivalent objects;
- replacing thin filled shapes with running or bean stitch;
- replacing broad satin with split satin or fill;
- converting gradients to blends, layers, or fewer tonal bands;
- moving boundaries to create overlap;
- changing corner geometry;
- and omitting detail that cannot survive at the requested size.

These are not failures of fidelity. They are the embroidery equivalent of
optical correction and manufacturing tolerance.

---

## 3. Artwork interpretation before stitch generation

The first output of digitizing should be an **intent-preserving object graph**,
not stitches.

### 3.1 Required artwork interpretation

For each design, establish:

- foreground and background;
- visual objects rather than merely connected pixels;
- object identity and role;
- open versus closed geometry;
- positive and negative spaces;
- text, symbols, strokes, filled masses, borders, highlights, and texture;
- occlusion and layer order;
- shared boundaries versus intentional overlaps;
- repeated or symmetric structures;
- salient detail versus disposable detail;
- expected final size and placement; and
- whether the design is suitable for automatic conversion.

### 3.2 Object graph

Represent the design as a directed graph.

**Nodes** are embroidery objects.
**Edges** describe relationships such as:

- behind / in front of;
- contained by;
- overlaps;
- shares visual boundary with;
- must sew before / after;
- may hide connector for;
- same logical object;
- same color but separate construction;
- and registration-sensitive neighbor.

This graph is more important than the palette. Two regions of the same color
may require different stitch types or sequence. Two regions of different
colors may need to be treated as one registration unit.

### 3.3 Segmentation must support supervision

Automatic segmentation should expose editable intermediate results:

- foreground mask;
- color segmentation;
- proposed visual objects;
- proposed object merges and splits;
- negative-space interpretation;
- layer and overlap suggestions;
- stroke skeletons;
- satin rail proposals;
- and confidence for each decision.

A user should be able to correct the object graph without redrawing the entire
design.

### 3.4 Refusal and escalation are features

Automatic digitizing should refuse or escalate when:

- the requested physical size cannot retain the design's identity;
- text falls below the validated profile for the selected thread and fabric;
- foreground/background confidence is poor;
- the object graph has unresolved topology;
- a proposed satin column branches or crosses itself;
- adjacent colors cannot maintain registration without redesign;
- the chosen garment profile is missing;
- or the result depends on unsupported specialty techniques.

The proper output may be a simplified design proposal rather than a stitch
file.

---

## 4. Canonical embroidery object model

Every object should carry enough information to regenerate stitches for a new
material profile without reverse engineering a machine file.

| Field | Purpose |
| --- | --- |
| `object_id` | Stable identity across edits and sew-outs |
| `visual_role` | Letter, border, base fill, highlight, texture, detail, cut line, placement line |
| `geometry` | Open path, closed boundary, paired rails, centerline, holes |
| `parent_id` / `layer_id` | Structural and compositing relationships |
| `stitch_family` | Running, bean/triple, satin, split satin, tatami, contour, motif, appliqué, manual |
| `angle_field` | Constant angle, guided field, rail-normal field, or local angle map |
| `entry_candidates` / `exit_candidates` | Valid starts and ends with costs |
| `sequence_constraints` | Hard before/after and adjacency requirements |
| `underlay_plan` | Type, angle, spacing, inset, stitch length, sequence |
| `top_stitch_plan` | Density/spacing, stitch length, pattern, row phase, edge policy |
| `compensation` | Directional pull, push, end shortening, overlap, border allowance |
| `travel_policy` | Hidden run, jump, trim, tie policy, reverse permission |
| `material_profile` | Fabric, stabilizer, topping, thread, needle, machine, hoop, speed |
| `confidence` | Model certainty and source provenance |
| `validation_state` | Simulated, sample sewn, approved, rejected, superseded |
| `provenance` | Human-authored, inferred from artwork, inferred from stitch file, imported native object |

### 4.1 Preserve semantics through compilation

The semantic object model must survive long enough to support:

- object-level editing;
- material-specific regeneration;
- DRC explanations;
- comparison between intended and compiled stitches;
- training labels;
- and regression testing.

DST should be generated at the end. It should never replace the editable
document.

---

## 5. Stitch-family selection

The primary families are running, satin, and fill/tatami, but the planner must
select them by visual role and physical behavior rather than by bounding-box
shape alone.

## 5.1 Running stitch

Use running stitch for:

- open lines and contours;
- small details too narrow for satin;
- placement and alignment lines;
- selective underlay;
- hidden travel;
- quilting and line-art effects;
- and structural paths that intentionally remain visible.

Important parameters include:

- stitch length;
- curvature-sensitive resampling;
- corner handling;
- repetition count;
- run direction;
- bean/triple pattern;
- and whether retracing is acceptable.

### Programmatic requirements

- Resample by curvature and material, not only by equal distance.
- Prevent a long segment from cutting across a curve.
- Avoid repeated penetrations at exactly the same points when multiple passes
  are intended.
- Recognize an authored open path directly. Do not infer all running detail
  from a high-pass image filter.
- Distinguish decorative lines from hidden travel and underlay.

## 5.2 Satin and column stitches

Use satin for narrow, visually important forms such as:

- lettering;
- borders;
- stems;
- ribbons;
- outlines;
- and glossy dimensional details.

A satin object is not merely a narrow polygon. It is a coordinated structure
with:

- two rails or equivalent boundary correspondence;
- a centerline or progression axis;
- stitch-angle control;
- turning points;
- corner strategy;
- entry and exit;
- underlay;
- and a width field.

### General satin solver

A production satin solver should:

1. infer or accept a medial path;
2. establish corresponding points on opposite rails;
3. detect branches, cusps, self-intersections, and unstable correspondence;
4. generate an angle field that follows the form;
5. taper or cap ends intentionally;
6. manage short stitches on inside curves;
7. split or convert over-wide sections;
8. avoid excessive penetrations at acute turns;
9. support local manual angle guides; and
10. expose every unstable segment for review.

### Corners

Corners need explicit strategy:

- capped;
- mitered with controlled overlap;
- turned with short-stitch filtering;
- split into separate objects;
- or converted to another stitch family.

Automatic underlay must follow the same corner strategy. A generic underlay
that ignores taper or miter geometry can protrude.

## 5.3 Tatami and fill stitches

Use fill for broad areas requiring coverage while retaining flexibility.

A production fill object requires more than row intersection:

- fill angle or angle field;
- row spacing;
- stitch length and pattern;
- stagger/partition sequence;
- row phase;
- contour inset;
- edge treatment;
- hole handling;
- branch and island handling;
- underlay;
- connector strategy;
- and interaction with neighboring fills.

### Fill angle

Fill angle affects:

- visual flow and sheen;
- distortion direction;
- interaction with fabric grain;
- contrast between neighboring objects;
- registration;
- and the dimensional appearance of layered details.

The planner should propose multiple candidate angles and score them against:

- shape direction;
- adjacent-object contrast;
- expected pull axis;
- fabric grain or stretch axis;
- average stitch length;
- short edge-row burden;
- connector containment;
- and visual intent.

A single global `45°` angle is not a production rule.

### Fill row termination

Short rows near corners are a normal result of angled fills. A DRC rule should
not simply reject every short edge stitch. The planner needs a corner and edge
strategy, including:

- row clipping and redistribution;
- contour-following edge stitches;
- short-stitch filtering with shape preservation;
- local angle adjustment;
- corner segmentation;
- or a different fill method.

### Layered fills

Some experienced digitizers use multiple low-density layers to build coverage
gradually and reduce distortion. This is a **practitioner technique**, not a
universal replacement for normal fill and compensation. It should be
implemented as an optional strategy and evaluated for:

- penetration coincidence;
- runtime;
- hand and stiffness;
- thread build;
- color blending;
- and the selected fabric.

## 5.4 Borders and outlines

Borders should be represented as registration-sensitive objects with an
explicit relationship to what they enclose.

The planner should determine:

- whether the border is a true satin column, running line, bean stitch, or
  edge created by the underlying fill;
- how far the underlying object overlaps or stops short;
- whether the border sews immediately after its local base or after all base
  objects;
- whether the border can tolerate garment movement;
- and whether local sequencing is more important than color-change reduction.

A border that looks aligned in vector geometry can fail after pull and push.
Registration must be evaluated on predicted stitched geometry.

---

## 6. Underlay as a designed support system

Underlay is not a checkbox and more underlay is not automatically safer. It is
a structural system chosen for an object, material, and visual goal.

Common types include:

- center run;
- edge run or contour;
- zigzag;
- double zigzag;
- low-density tatami;
- and combinations of these.

Underlay may:

- attach fabric to stabilizer;
- control the final object width;
- flatten nap;
- support long top stitches;
- improve edge definition;
- reduce sinking;
- establish a path for top stitches;
- and distribute distortion.

Underlay may also:

- show outside the top stitching;
- close small counters;
- create stiffness;
- add unnecessary penetrations;
- distort tiny lettering;
- and compound density where objects overlap.

### 6.1 Underlay selection model

Choose underlay using:

- stitch family;
- object width and area;
- top-stitch length;
- fabric stability, loft, and stretch;
- stabilizer and topping;
- desired edge profile;
- neighboring stitch layers;
- expected coverage;
- and whether a prior object already provides foundation.

### 6.2 Avoid duplicate foundations

Before adding underlay, calculate the support already present from:

- design-level foundation runs;
- neighboring or underlying objects;
- appliqué tack-down;
- prior fill layers;
- shared edge runs;
- and hidden travel.

The planner should optimize **effective support**, not number of underlay
objects.

### 6.3 Underlay DRC

Flag:

- underlay outside compensated top geometry;
- dense intersections between multiple underlays;
- underlay visible through counters or open areas;
- underlay direction matching top stitch where sinking is likely;
- excessive repeated penetrations;
- unsupported long satin;
- and foundation systems whose combined density exceeds the material profile.

---

## 7. Compensation and overlap

Compensation is directional pre-distortion used to achieve the intended
stitched result.

### 7.1 Pull

Stitches tend to draw material inward along the stitch direction. Pull
compensation usually widens geometry across the stitch direction.

### 7.2 Push

Accumulated thread can displace geometry along the progression or edge
direction, causing ends and boundaries to extend. Push correction may require:

- shortening geometry at open ends;
- moving boundaries inward;
- changing angle;
- reducing density;
- changing underlay;
- or changing sequence.

### 7.3 Compensation is an object field

Do not store one global compensation value. Store a directional compensation
model that may vary by:

- object;
- stitch family;
- local width;
- stitch length;
- angle;
- material;
- underlay;
- neighboring layers;
- and machine profile.

### 7.4 Overlap is intentional

Exact shared vector boundaries are often a registration defect waiting to
happen. Adjacent or bordered objects may need planned overlap.

The planner should calculate:

- predicted post-stitch boundary;
- required overlap margin;
- border capture area;
- direction of likely gap;
- and whether the overlap will create excessive bulk.

### 7.5 Learn compensation from measured sew-outs

For a test coupon, compare:

- intended geometry;
- compiled stitch geometry;
- photographed stitched boundary;
- and measured dimensional error by direction.

Fit compensation by object and material profile rather than teaching a model
that the distorted legacy geometry is the original visual intent.

---

## 8. Density, stitch length, and penetration load

Density is not a quality score. It is one parameter in a physical system.

The planner must reason about:

- row spacing;
- stitch length;
- thread diameter;
- needle size;
- fabric openness and stability;
- underlay;
- number of layers;
- crossing angles;
- penetration coincidence;
- machine speed;
- and local time between repeated penetrations.

### 8.1 Distinguish coverage from packing

A design can have insufficient visual coverage and still be locally
overpacked. Coverage should be estimated from:

- thread width and sheen;
- stitch orientation;
- fabric color and texture;
- underlay lift;
- top-stitch spacing;
- and layer interaction.

Packing should be estimated from:

- needle penetrations per local area;
- thread path length per area;
- repeated hits within a small radius;
- crossing layers;
- and temporal concentration.

### 8.2 Short stitches

Short stitches may:

- be required at curves and corners;
- be generated accidentally by noisy geometry;
- damage fabric when clustered;
- cause thread breaks;
- or be harmless when sparse.

DRC should detect **clusters and context**, not apply one universal minimum to
every semantic stitch.

### 8.3 Long stitches

Long stitches increase snag risk and may pull more strongly. They may also
provide gloss and intentional texture. The planner should distinguish:

- machine movement maximum;
- safe exposed top-stitch maximum;
- supported satin span;
- underlay or couching support;
- and decorative exceptions.

### 8.4 Penetration heat map

Create a multiscale heat map over the complete time-ordered design:

- fine scale for needle-hole coincidence;
- medium scale for local stiffness and thread buildup;
- coarse scale for garment distortion.

Report both total concentration and bursts that occur close together in
machine time.

---

## 9. Sequencing and pathing

Pathing affects appearance, registration, runtime, drape, and reliability.
Nearest-neighbor routing is only one cost term.

### 9.1 Sequence constraints

The sequence solver should honor:

- back-to-front compositing;
- local base-before-border relationships;
- underlay-before-top;
- placement-before-appliqué;
- center-out or bottom-up product constraints;
- registration-sensitive adjacency;
- coverage for hidden connectors;
- color and needle availability;
- and user sequence locks.

### 9.2 Cost function

Optimize a weighted objective including:

- visual correctness;
- predicted registration error;
- uncovered travel;
- trim and tie count;
- jump distance;
- color changes;
- machine movement;
- object reversal penalty;
- fabric destabilization;
- seam crossing;
- and runtime.

Hard design and material constraints must not be traded away for lower travel
cost.

### 9.3 Entry and exit are part of object design

For every object, generate candidate entry and exit points and score:

- connection to previous and next objects;
- visibility;
- underlay coverage;
- tie security;
- direction of fabric movement;
- satin progression;
- fill-row continuity;
- and whether reversing the object changes appearance.

### 9.4 Hidden connectors

A connector is hidden only when:

- it lies within the geometry that will cover it;
- the covering object occurs later;
- sufficient coverage exists after expected pull;
- it does not cross a hole or negative space;
- and the connector itself will not distort the top object.

Distance alone is not sufficient.

### 9.5 Trim decisions

A trim may be preferable even for short travel when:

- travel crosses visible open fabric;
- the next object is registration-sensitive;
- a float may snag;
- a cap seam or structure prevents clean travel;
- or the thread path would distort a small object.

Conversely, a longer hidden travel path may be preferable to repeated trims
when it remains covered and stable.

---

## 10. Small lettering

Small lettering is a specialized construction problem, not merely scaled
large lettering.

### 10.1 Preserve letter identity

For each glyph:

- enlarge counters;
- open joins;
- simplify serifs;
- strengthen thin stems;
- adjust crossbar position;
- prevent adjacent columns from merging;
- and consider a purpose-built embroidery alphabet.

The design should be evaluated in thread at final size, not only as outlines
on screen.

### 10.2 Choose the construction by actual stroke width

Possible strategies include:

- satin with no underlay;
- satin with center-run underlay;
- light-density satin with increased width;
- running or bean stitch;
- simplified block lettering;
- thinner thread and smaller needle;
- or redesign.

There is no universal minimum height. Minimum viable text depends on:

- font;
- actual column width;
- thread;
- needle;
- fabric;
- backing;
- machine;
- cap structure;
- and expected viewing distance.

### 10.3 Small-lettering validation profile

Maintain a calibrated test alphabet for each production profile:

- capitals, lowercase, numerals, punctuation;
- narrow, round, diagonal, and counter-heavy glyphs;
- multiple sizes;
- selected thread and needle combinations;
- and representative fabrics.

Record the smallest approved result for each combination. The planner should
reference this empirical capability table rather than a generic size rule.

### 10.4 Trim and connector policy

Between letters, evaluate:

- closest-point connector;
- whether the connector is visually hidden;
- word-level versus letter-level trims;
- tie security;
- and thread-drop risk.

The lowest trim count is not automatically the best result.

---

## 11. Caps and structured headwear

A cap is not a curved shirt. It requires its own product profile.

Relevant factors include:

- center seam and seam bulk;
- buckram or front-panel structure;
- cylindrical frame movement;
- limited usable height;
- bill and lower frame obstruction;
- panel count;
- mesh;
- cap driver and machine;
- and how the cap is hooped.

### 11.1 Common routing heuristic

Center-out and bottom-up sequencing is a widely used starting heuristic for
structured caps because it can reduce cumulative shifting around the center
seam and move work away from the most constrained area.

Treat it as a profile heuristic, not a universal law. The solver must still
consider:

- design size;
- object structure;
- local borders;
- seam crossings;
- machine and frame;
- and whether splitting the design creates visible joins.

### 11.2 Cap-specific planner behavior

The cap profile should:

- model a center seam exclusion or risk zone;
- prioritize objects near the center before outward regions when beneficial;
- reduce unsupported wide satin over the seam;
- plan overlaps when a center-spanning object is split;
- prefer lower-to-upper progression where frame clearance requires it;
- and apply cap-specific sew-out validation.

A left-chest file should not be assumed transferable to a cap.

---

## 12. Fabric and production profiles

The planner needs named, versioned profiles rather than a single density
multiplier.

A profile should include:

- product type and placement;
- fabric construction;
- stretch axes and recovery;
- thickness and loft;
- surface texture or pile;
- backing type and layers;
- topping;
- thread material and size;
- needle size and point;
- machine and cap driver;
- hoop or frame;
- operating speed;
- tension baseline;
- validated stitch-length and density ranges;
- compensation priors;
- underlay priors;
- and physical test history.

### 12.1 Representative profile families

At minimum:

- stable woven twill;
- lightweight woven;
- piqué knit;
- jersey or stretch knit;
- fleece;
- terry/high pile with topping;
- structured six-panel cap;
- unstructured cap;
- mesh cap;
- patch twill;
- felt;
- and appliqué substrate.

Each profile should have its own regression coupons and acceptance limits.

---

# Part II: Programmatic architecture

## 13. Layered system design

```text
Artwork and job specification
  |
  +-- product/material profile
  |
  +-- visual analysis
  |     +-- foreground and color
  |     +-- text/stroke/area recognition
  |     +-- salience and simplification
  |
  +-- editable object graph
  |     +-- layers and occlusion
  |     +-- rails, paths, boundaries, holes
  |     +-- overlaps and registration relationships
  |
  +-- construction proposal
  |     +-- stitch-family choices
  |     +-- angle fields
  |     +-- underlay and compensation
  |     +-- entry/exit candidates
  |
  +-- constrained sequence and path planner
  |
  +-- deterministic stitch generators
  |
  +-- semantic DRC and repair proposals
  |
  +-- machine-operation intermediate representation
  |
  +-- codec adapters
  |     +-- pystitch
  |     +-- libembroidery
  |
  +-- DST/PES and other machine files
  |
  +-- decode-back verification
  |
  +-- physical sew-out record
```

### 13.1 Codec layer

Use `pystitch` and `libembroidery` for:

- format parsing and writing;
- machine command normalization;
- color-change and needle command handling;
- extents and command inspection;
- conversion regression;
- and decode-back verification.

Do not ask the codec layer to infer:

- objects;
- stitch semantics;
- underlay roles;
- artwork;
- or manufacturing quality.

### 13.2 Machine-operation IR

Before encoding, compile to a format-independent operation stream with:

- absolute physical coordinates;
- semantic operation role;
- object ID;
- thread/needle assignment;
- stitch, jump, trim, stop, color-change, and end commands;
- tie semantics;
- machine profile;
- and provenance back to the generating object.

This enables output-format comparison and debugging.

### 13.3 Deterministic geometry layer

Keep deterministic:

- rail correspondence validation;
- contour intersection;
- stitch resampling;
- underlay generation after approval;
- connector containment;
- sequence constraint enforcement;
- compensation geometry application;
- machine-limit enforcement;
- and encoding.

Learned models may propose parameters and geometry, but deterministic code
should validate and compile them.

---

## 14. Learned assistance

Machine learning is best used where the problem is ambiguous and semantic.

High-value learned tasks include:

- foreground and background segmentation;
- text and glyph detection;
- visual-object segmentation;
- stroke versus area classification;
- layer and occlusion inference;
- detail salience and simplification;
- stitch-family proposal;
- satin centerline and rail proposal;
- fill-angle proposal;
- underlay and compensation priors;
- and sequence proposal.

### 14.1 Model output must be editable

Every proposal should include:

- confidence;
- alternate candidates;
- source evidence;
- affected objects;
- expected benefit;
- and deterministic validation results.

A model should not directly emit an opaque DST file as its only output.

### 14.2 Human correction is training data

Capture edits as structured deltas:

- merged/split objects;
- corrected rails;
- changed stitch family;
- changed layer order;
- moved entry/exit;
- changed angle;
- underlay added or removed;
- compensation changed;
- detail omitted;
- and sequence changed.

These corrections are more valuable than a single final quality score.

---

## 15. Corpus strategy

The PES and DST corpus is useful, but it is not automatically semantic ground
truth.

### 15.1 What machine files can teach

With provenance and licensing, machine files can support:

- command distributions;
- stitch-vector distributions;
- color-block and movement statistics;
- local density and angle patterns;
- candidate object segmentation;
- running/satin/fill inference;
- underlay inference;
- pathing patterns;
- format and codec regression;
- and retrieval of similar constructions.

### 15.2 Weak-label risks

A machine file may encode:

- poor commercial digitizing;
- legacy machine constraints;
- edits made after losing object semantics;
- compensations specific to an unknown fabric;
- accidental jumps or trims;
- conversion artifacts;
- and production shortcuts.

The corpus should therefore label each inferred property with:

- provenance;
- inference method;
- confidence;
- source format;
- parser version;
- and review status.

### 15.3 Minimum training record

The strongest record pairs:

1. source artwork;
2. approved mask and object graph;
3. physical size and placement;
4. layer and overlap model;
5. per-object stitch plan;
6. final machine file;
7. machine, hoop, fabric, backing, topping, thread, needle, speed, and tension;
8. simulation;
9. front and reverse sew-out photographs;
10. measured defects and dimensions;
11. human corrections; and
12. final approval.

### 15.4 Corpus strata

Build balanced strata for:

- small text;
- simple one-color marks;
- multi-color logos;
- mascots and illustrated art;
- outlines over fills;
- cap designs;
- high-pile designs;
- patches;
- appliqué;
- and known failure cases.

Do not train only on attractive finished files. Include rejected sew-outs and
their repairs.

### 15.5 Rights and provenance

For each asset record:

- license;
- owner;
- permitted training use;
- permitted redistribution;
- source location;
- modifications;
- and whether physical sew-out imagery contains customer or brand material.

Public-domain and licensed machine files do not automatically grant rights to
unrelated source artwork or trademarks embedded in them.

---

## 16. Reverse analysis of PES and DST

A stitch-stream analyzer should infer a candidate semantic graph without
pretending the inference is certain.

### 16.1 Candidate segmentation signals

Use:

- color blocks;
- jumps and trims;
- abrupt direction changes;
- local stitch-vector parallelism;
- alternating rail-like penetrations;
- fill-row periodicity;
- density changes;
- repeated contours;
- underlay-like sparse paths;
- temporal proximity;
- and spatial overlap.

### 16.2 Candidate semantic labels

Infer with confidence:

- running;
- bean/triple;
- satin;
- tatami;
- contour;
- underlay;
- hidden travel;
- tie-in/tie-off;
- object boundary;
- and layer relationship.

### 16.3 Do not reconstruct artwork from stitches alone

The recovered geometry is a **construction hypothesis**. It may already
contain compensation and production distortion. It should not be treated as
the intended artwork perimeter without paired evidence.

---

## 17. Design-rule checking

DRC should explain whether a plan is mechanically valid, materially plausible,
and visually faithful.

### 17.1 Rule levels

| Level | Result |
| --- | --- |
| **Blocker** | Export prohibited without explicit override |
| **Conditional blocker** | Export prohibited for the current profile, allowed after profile or geometry change |
| **Warning** | Likely production or visual risk |
| **Optimization** | Quality, runtime, or maintainability suggestion |
| **Observation** | Measured property used for comparison |

### 17.2 Geometry rules

Check:

- invalid or self-intersecting boundaries;
- holes too small to survive;
- unstable satin rails;
- satin branches;
- impossible rail correspondence;
- narrow spikes;
- noisy contours;
- unintentional bridges;
- and negative spaces below the validated profile.

### 17.3 Stitch rules

Check:

- machine displacement limits;
- exposed long stitches;
- short-stitch clusters;
- repeated penetrations;
- high curvature with inadequate sampling;
- satin width and split requirements;
- fill row fragments;
- and tie security.

### 17.4 Density and material rules

Check:

- local penetration concentration;
- thread path length per area;
- combined underlay and top-stitch load;
- overlapping object load;
- likely stiffness;
- likely sinking on pile;
- insufficient support;
- and unsupported profile combinations.

### 17.5 Registration rules

Check:

- exact shared edges without overlap;
- predicted pull gaps;
- push blowout beyond borders;
- border capture margin;
- outline sequence;
- cap seam risk;
- and long intervals between a base object and its border.

### 17.6 Pathing rules

Check:

- visible connectors;
- unsafe floats;
- excessive trims or color changes;
- unnecessary reversal restrictions;
- entry/exit in visible areas;
- jumps crossing open negative space;
- and routing that violates product-specific progression.

### 17.7 Semantic rules

Check:

- text counters likely to close;
- important detail omitted;
- detail duplicated by multiple passes;
- background objects sewn after foreground;
- low-salience texture receiving more stitches than primary forms;
- and output that differs materially from the approved object graph.

### 17.8 DRC repair loop

For every actionable finding:

1. identify the affected object and rule;
2. explain the physical or visual mechanism;
3. offer one or more repairs;
4. estimate consequences for appearance, count, and runtime;
5. regenerate only affected objects;
6. rerun DRC; and
7. preserve the accepted change as training data.

---

## 18. Simulation

A stitch player is necessary but insufficient.

### 18.1 Machine simulation

Show:

- exact encoded path;
- speed-adjusted redraw;
- jumps, trims, ties, and color changes;
- front and reverse thread;
- object and operation provenance;
- and decode-back differences.

### 18.2 Predictive material simulation

A practical near-term model should estimate:

- directional shrink;
- edge displacement;
- local buildup;
- likely sinking;
- overlap after compensation;
- and registration risk.

It does not need to be a full textile finite-element simulation to improve on
a flat stitch preview. It can begin as a calibrated empirical model.

### 18.3 Comparative preview

Allow side-by-side comparison of:

- artwork;
- object graph;
- uncompensated geometry;
- compensated geometry;
- semantic stitch plan;
- encoded machine file;
- predicted stitched geometry;
- and sew-out photograph.

---

# Part III: Physical validation

## 19. Sew-out protocol

Physical sew-out is part of compilation validation, not an optional marketing
step.

### 19.1 Record the complete setup

- design and revision;
- output format and encoder version;
- machine and firmware;
- hoop or cap frame;
- garment and lot;
- backing and topping;
- thread brand/material/weight/color;
- needle;
- speed;
- tension adjustments;
- operator;
- and environmental or handling notes when relevant.

### 19.2 Standard test set

Maintain coupons for:

- line and curve fidelity;
- satin widths and corners;
- fill angles and densities;
- overlapping colors and borders;
- small lettering;
- negative spaces;
- hidden travel;
- and the cap center seam.

### 19.3 Measure

At minimum:

- overall dimensional error in X and Y;
- object and border registration;
- gap and blowout width;
- counter closure;
- pucker or cupping;
- coverage;
- visible underlay;
- thread breaks;
- trims and runtime;
- fabric damage;
- and subjective hand/stiffness.

### 19.4 Photograph

Capture:

- full front;
- full reverse;
- macro details;
- oblique light for texture;
- ruler or scale;
- color reference when color matters;
- and failed areas before destructive inspection.

### 19.5 Approval status

Use:

- rejected;
- conditional;
- production-approved for profile;
- production-approved with known limitations;
- and reference standard.

Approval must be tied to a material and machine profile, not only to a design.

---

## 20. Quality metrics

Do not collapse quality into one opaque score.

Maintain a scorecard with separate dimensions:

- visual fidelity;
- legibility;
- edge quality;
- registration;
- coverage;
- texture and sheen;
- hand/drape;
- structural stability;
- machine reliability;
- runtime efficiency;
- and editability/provenance.

A composite score may be shown, but all component measurements and penalties
must remain visible.

---

# Part IV: Implementation priorities

## 21. Immediate changes to the current raster planner

Prioritize representation over threshold tuning.

### Priority 0: preserve transformations and job context

- bake or transmit rotation, mirror, placement, and final dimensions;
- require a material/product profile;
- preserve original and transformed artwork;
- and version every planning parameter.

### Priority 1: replace color components with an object graph

- separate visual object detection from color subdivision;
- detect text and stroke-like structures;
- support user merge/split;
- preserve negative space;
- and infer layer relationships.

### Priority 2: build a real satin proposal system

- skeleton/centerline extraction;
- branch detection;
- paired rail proposal;
- guided stitch-angle field;
- corner strategy;
- width-based split or conversion;
- and human correction handles.

### Priority 3: remove the generic high-pass detail pass

Replace it with:

- explicit open-path detection;
- outline and internal-detail classification;
- salience-based detail selection;
- duplication checks against blocking geometry;
- and user approval.

### Priority 4: parameterize fill

- object-specific angle;
- row spacing and stitch length;
- row phase/stagger;
- inset and edge treatment;
- connector strategy;
- and neighboring-object contrast.

### Priority 5: material-aware foundation and compensation

- object-level underlay selection;
- duplicate-foundation analysis;
- directional compensation;
- overlap planning;
- and profile-specific priors.

### Priority 6: constrained global pathing

- hard layer and registration constraints;
- entry/exit candidates;
- path reversal;
- hidden connector verification;
- and product-specific progression.

### Priority 7: repair-capable DRC

- local repair proposals;
- incremental regeneration;
- severity by material profile;
- and explicit export blockers.

---

## 22. Reference implementation opportunities

The project should study, test against, and where licenses permit reuse ideas
from:

- Ink/Stitch for open-source object semantics, satin/fill generation,
  underlay, compensation, and path optimization;
- `pystitch` and `libembroidery` for file and command handling;
- commercial manuals for terminology and parameter interaction;
- trade-magazine case studies for physical failure mechanisms;
- experienced digitizer examples for pathing and optical correction;
- and the licensed/public-domain corpus for statistical priors and regression.

Do not copy software-specific slider defaults as universal craft laws.

---

## 23. Research and source hierarchy

### Tier 1: primary technical references

Use these for definitions, available controls, file semantics, and
implementation comparison.

- [Pystitch repository](https://github.com/inkstitch/pystitch)
- [Libembroidery repository](https://github.com/Embroidermodder/libembroidery)
- [Ink/Stitch workflow](https://inkstitch.org/docs/workflow/)
- [Ink/Stitch fill stitch documentation](https://inkstitch.org/docs/stitches/fill-stitch/)
- [Ink/Stitch satin column documentation](https://inkstitch.org/docs/stitches/satin-column/)
- [Ink/Stitch tutorials](https://inkstitch.org/tutorials/)
- [Wilcom EmbroideryStudio e4 reference manual](https://docs.wilcom.com/embroiderystudio/e4/en/downloads/referencemanual.pdf)
- [Melco DesignShop 12 manual](https://www.melco-service.com/docs/DS12_Manual.pdf)
- [Melco legacy DesignShop manual](https://www.melco-service.com/docs/legacy/32590a.pdf)
- [mySewnet Digitizing Stitch Guide](https://softwarehelp.mysewnet.com/SampleGuides/100/mySewnetEmbroidery_DigitizingGuide_100.pdf)

### Tier 2: experienced trade educators and magazines

Use these for physical mechanisms, troubleshooting patterns, and techniques
that require profile-specific testing.

- [Impressions: Custom Machine Embroidery Digitizing 101](https://impressionsmagazine.com/process-technique/custom-machine-embroidery-digitizing-101-how-to/167083/)
- [Impressions: Anticipate Push-Pull in Designs](https://impressionsmagazine.com/process-technique/anticipate-push-pull-in-designs/16265/)
- [Impressions: How to Work With Push-Pull Compensation](https://impressionsmagazine.com/process-technique/how-to-work-with-push-pull-compensation/16206/)
- [Impressions: Digitizing for Thin Fabrics](https://impressionsmagazine.com/process-technique/digitizing-for-thin-fabrics/33806/)
- [Impressions: Preventing Embroidery Digitizing Disasters, Part 1](https://impressionsmagazine.com/process-technique/isolating-embroidery-problems-digitizing-vs-execution-pull-push-distortion/168424/)
- [Impressions: Preventing Embroidery Digitizing Disasters, Part 2](https://impressionsmagazine.com/process-technique/preventing-embroidery-digitizing-disasters-part-2preventing-embroidery-digitizing-disasters-lettering-density-underlay-problems/169753/)
- [Graphics Pro: Pre-planning a design's pathing](https://graphics-pro.com/education/how-can-embroiderers-pre-plan-a-designs-pathing/)
- [Mr X Stitch: The Pathing Principle](https://www.mrxstitch.com/pathing/)
- [Embroidery Legacy: Understanding Underlay](https://embroiderylegacy.com/embroidery-digitizing-underlay-digitizing/)
- [Embroidery Legacy: Push and Pull Compensation](https://embroiderylegacy.com/push-pull-compensation-embroidery-digitizing/)
- [Embroidery Legacy: Digitizing Caps](https://embroiderylegacy.com/how-to-digitize-a-cap-complete-embroidery-digitizing-guide/)
- [ColDesi: Digitizing for Small Text](https://support.coldesi.com/Portals/8/Documents/Liberty%20Docs/Digitizing%20for%20Small%20Text.pdf)

### Tier 3: forums and historical discussion

Use these to locate recurrent problems, terminology, edge cases, and candidate
tests. Treat numeric settings as anecdotal until reproduced.

- [Embroidery forum: small lettering density and pull compensation](https://forum.embroideres.com/forums/topic/521-small-lettering-density-and-pull-comp/)
- [Embroidery forum: choosing compensation values](https://forum.embroideres.com/articles.html/articles/how-choose-compensation-values-for-machine-embroidery-design-r318/)
- [Usenet archive: request for help digitizing unclear art to PES](https://groups.google.com/g/rec.crafts.textiles.needlework/c/m0YFugE9D2s)
- [Usenet archive: discussion of the labor and price of digitizing](https://groups.google.com/g/rec.crafts.textiles.needlework/c/Jk1iJPiY5Wk)

The Usenet material is historically useful because it shows that users often
described the process as scanning an image while practitioners treated
digitizing as skilled, reusable production work. It is not a technical
authority for modern settings.

---

## 24. Editorial principles for future additions

When adding advice from a forum, video, book, or magazine:

1. preserve the author's actual context;
2. record date, machine, software, fabric, thread, needle, and design size
   when available;
3. translate settings into physical units;
4. classify the evidence;
5. identify competing advice;
6. turn the claim into a reproducible test;
7. record the sew-out result;
8. promote it to a profile heuristic only after repeated validation; and
9. never erase the original source or the uncertainty.

---

# Appendix A: Current implementation review

The following implementation review is preserved from the project brief and
should be read as the gap analysis between the current deterministic prototype
and the craft standard above.

The canonical, actively maintained review is
[`digitizing-flow-human-review.md`](digitizing-flow-human-review.md). It now
includes a guide-directed verdict, priority gap matrix, and four-truth review
framework. The embedded copy below is retained as a dated snapshot.


# Digitizing flow: human review and coaching brief

Status: current implementation review
Date: 2026-07-24
Scope: raster auto-digitizing, editable-vector planning, DRC, and training data

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
- If the image is opaque, Otsu thresholding is applied to luminance. The
  majority border class is assumed to be background.
- A uniform opaque image is treated entirely as foreground.

This is a binary foreground decision. There is no semantic background-removal
model, matting, edge-confidence map, hole repair, morphological cleanup, or
user-approved mask.

This stage can fail when:

- artwork touches the image border;
- the background is textured or shaded;
- foreground and background overlap in luminance;
- a light and a dark object both need to survive;
- antialiasing creates weak edge pixels; or
- negative space should be interpreted from design structure.

### 3. Palette assignment

Only foreground pixels participate in median-cut color quantization. The user
requests between 2 and 15 colors. Quantization assigns each foreground pixel
to a retained RGB palette entry.

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

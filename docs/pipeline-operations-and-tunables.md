# Digitizing Pipeline: Operations, Tunables, and Decision Brackets

## Purpose

The product should remain simple at its default surface:

1. upload artwork;
2. inspect a virtual sew-out;
3. make small corrections when necessary;
4. download the machine file.

That simplicity is supported by a large number of geometric, material, stitch,
routing, and machine decisions. This document makes those decisions
inspectable without requiring every operator to manage every parameter.

The intended control model has three levels:

- **Automatic:** the engine selects values and shows only blocking findings.
- **Guided:** the engine exposes important decisions as pass, warning, or
  fallback brackets with a small number of safe adjustments.
- **Expert:** the complete object, layer, stitch, routing, and DRC parameters
  are editable.

This is an inventory of the current pipeline followed by the proposed
bracketed-output contract.

## Status notation

| Mark | Meaning |
| --- | --- |
| **Engine** | Implemented in the current engine |
| **API** | Accepted or returned by the current API |
| **UI** | Exposed in the current interface |
| **Proposed** | Recommended addition |

## Pipeline overview

```text
source file
  -> decode and normalize
  -> establish physical size
  -> extract foreground geometry
  -> consolidate semantic thread colors
  -> find visual elements and layer relationships
  -> prepare underlay, blocking, and detail masks
  -> reconstruct editable paths, boundaries, centerlines, and satin rails
  -> classify and construct running, satin, and tatami objects
  -> apply compensation and object underlay
  -> sequence layers and route same-thread objects
  -> enforce stitch and movement limits
  -> compile machine operations
  -> decode the compiled file
  -> virtual sew-out, DRC, trace, and export
```

The editable vector document is the preferred intermediate representation.
Pixels describe evidence; embroidery objects describe manufacturing intent.

## 1. File ingest and source normalization

### Operations

- Decode PNG, JPEG, WebP, or SVG input.
- Convert raster input to RGBA.
- Detect transparent, opaque, uniform, and high-contrast block artwork.
- Limit working resolution to 1024 × 1024 pixels.
- Preserve hard edges for block artwork; use high-quality resampling for
  continuous-tone artwork.
- Record original dimensions and crop bounds.

### Tunable parameters

| Parameter | Current behavior | Exposure |
| --- | --- | --- |
| Maximum working dimensions | 1024 × 1024 px | Engine |
| Block-art detection | Automatic | Engine |
| Block-art resampling | Nearest neighbor | Engine |
| General resampling | Lanczos | Engine |
| Alpha handling | Transparency takes precedence | Engine |
| Source rotation | -180° to 180° | UI, API transform |
| Horizontal mirror | On/off | UI, API transform |
| Placement | Center, left, or right | UI |

### Outputs and checks

- Decoded image.
- Source classification.
- Source dimensions.
- Warning for unreadable or empty input.
- Proposed: warning for insufficient physical source resolution after the
  production size is selected.

## 2. Physical sizing and frame selection

### Operations

- Select a cap frame or full machine field.
- Convert source pixels into millimeters.
- Preserve aspect ratio.
- Center the design on the work origin.
- Fit the design to the usable frame when requested.

### Tunable parameters

| Parameter | Current default or range | Exposure |
| --- | --- | --- |
| Design width | 72 mm default | UI, API |
| Raster planning height ceiling | 55 mm | Engine; should become profile-driven |
| Cap field | 120 × 55 mm, 110 × 45 mm usable | UI |
| Full field | 500 × 360 mm | UI |
| Work-fit mode | Design fit or physical work fit | UI |
| Machine speed | 300–1,200 SPM | UI |
| Stitch memory | 650,000 stitches | Engine DRC, UI |
| Stored-design capacity | 99 designs | UI information |

### Outputs and checks

- Physical width and height.
- Millimeters per source pixel.
- UI fit and scale clamping for the selected frame.
- Stitch-memory DRC.
- Proposed: explicit frame-overflow DRC in the engine and API.
- Proposed: minimum feature-size preview at the selected production size.

## 3. Foreground extraction

### Operations

- Use alpha geometry when transparency is meaningful.
- Otherwise calculate an Otsu luminance threshold.
- Inspect the image border to determine which threshold class is background.
- Extract geometry without assuming that background is white or that artwork
  has a particular color.
- Crop to the foreground bounds.

### Tunable parameters

| Parameter | Current behavior | Exposure |
| --- | --- | --- |
| Geometry source | Alpha or Otsu luminance | Engine |
| Otsu threshold | Computed per image | Returned diagnostic |
| Background class | Inferred from border majority | Returned diagnostic |
| Alpha threshold | Computed with Otsu when needed | Returned diagnostic |
| Crop to foreground | Automatic | Returned diagnostic |
| Foreground polarity override | Not available | Proposed guided control |
| Mask cleanup strength | Purpose-specific and guarded | Engine |

### Outputs and checks

- Foreground mask.
- Threshold method and value.
- Background class.
- Foreground pixel count and bounds.
- Proposed bracket: foreground retained-area ratio before and after cleanup.
- Proposed overlay: accepted foreground, uncertain boundary band, and rejected
  background.

## 4. Semantic thread-color consolidation

### Operations

- Quantize only foreground pixels.
- Rank palette entries by coverage.
- Measure whether each shade forms a real region or fragmented edge noise.
- Merge fragmented shades belonging to the same thread family.
- Keep geometry identity separate from thread color.

### Tunable parameters

| Parameter | Current default or range | Exposure |
| --- | --- | --- |
| Requested palette limit | 2–15 colors | UI, API |
| Semantic shade fragmentation threshold | 0.20 largest-component fraction | Engine |
| Same-thread hue/saturation/value tolerances | Internal | Engine |
| Preserve interior color layers | Enabled by layered strategy | Engine |
| Palette color replacement | Per thread family | UI |
| Color enable/disable | Per color | UI |
| Manual shade merge/split | Not available | Proposed guided control |

### Outputs and checks

- Semantic palette.
- Pixel-to-thread assignments.
- Requested versus retained color count.
- Consolidated shade count.
- Proposed bracket: color-family distance and spatial-coherence evidence for
  every automatic merge.

## 5. Visual-element and composition analysis

### Operations

- Find connected foreground elements before subdividing them by color.
- Preserve element identity across physical passes.
- Measure bounds, area, perimeter, estimated stroke width, aspect ratio,
  compactness, holes, color complexity, color entropy, and edge density.
- Associate derived underlay, blocking, and detail objects with their source
  element.
- Preserve authored or inferred layer order and overlap relationships.

### Tunable parameters

| Parameter | Current value | Exposure |
| --- | --- | --- |
| Minimum interior object area | 0.08 mm² | Engine, API limit indirectly |
| Maximum automatic objects | 400 | API planning limit |
| Maximum derived detail objects | 48 | Engine |
| Layer enabled state | On/off | Vector document |
| Layer density multiplier | 1.0 | Vector document |
| Physical layer order | Authored or inferred | Vector document |
| Blend with lower layer | Off by default | Vector document |
| Sequence lock | Off by default | Vector document |
| Object split/merge | Manual vector editing | UI foundation exists |

### Outputs and checks

- Element and object identifiers.
- Physical feature report.
- Layer and overlap graph.
- Proposed: distinguish source objects, inferred objects, and derived detail
  objects visibly in the editor.

## 6. Purpose-specific pass filters

The three physical passes are related but do not need identical geometry.

### 6.1 Foundation and underlay pass

#### Operations

- Determine whether an element needs structural support.
- Close small mask discontinuities.
- Optionally inset a solid silhouette.
- Reject cleanup that removes too much or grows the source excessively.
- Prepare a design-level center run.
- Prepare object-specific center, edge, zig-zag, or tatami underlay.

#### Current eligibility

- Area at least 0.6 mm²; and
- estimated width at least 1.2 mm or long span at least 5 mm.
- A silhouette underlay additionally requires area at least 2.0 mm² and
  estimated width at least 1.5 mm.

#### Tunables

- Underlay enabled per document, layer, and object.
- Binary close radius: 3 px.
- Optional inset: 1 px.
- Cleanup minimum retained geometry: 55%.
- Cleanup maximum growth: 120%.
- Design center-run stitch length: 2.5 mm.
- Satin center-run maximum stitch: 2.5 mm.
- Wide-satin underlay threshold: 3.5 mm mean width.
- Satin zig-zag maximum stitch: 7 mm.
- Tatami underlay angle: top angle + 90°.
- Raster tatami underlay spacing: 1.4 mm.
- Minimum silhouette area: 2.0 mm².

### 6.2 Blocking or mid-layer pass

#### Operations

- Apply guarded median cleanup.
- Preserve each element’s perimeter and internal color subdivision.
- Replace a truly narrow stroke with running detail rather than a solid block.
- Construct satin where a valid stroke model exists; otherwise use tatami.

#### Tunables

- Minimum area: 0.08 mm².
- Binary median radius: 3 px.
- Geometry-change guard.
- Per-profile fill spacing.
- Global density multiplier: 60–150%.
- Fill angle.
- Fill stitch length.
- Pull compensation.
- Join overlap.

### 6.3 Top-detail pass

#### Operations

- Calculate fine and coarse edge responses.
- Suppress the outer boundary when it would duplicate an existing object.
- Admit narrow geometric strokes directly.
- Gate other details by physical area, edge response, color complexity, color
  entropy, and edge density.
- Trace accepted detail as running paths.

#### Current eligibility

- Narrow stroke: estimated width below 1.25 mm, long span at least 2.5 mm,
  and aspect ratio at least 2.0; or
- area at least 1.0 mm² with sufficient edge evidence and useful color or
  edge complexity.
- Accepted edge density is currently 0.025–0.45.

#### Tunables

- Fine blur radius: 0.65 px.
- Coarse blur radius: 1.8 px.
- Boundary suppression: 3 px.
- Color entropy threshold: 0.35 bits.
- Maximum detail objects: 48.
- Maximum detail trims: 250.
- Detail running-stitch length.
- Whether a detail object may cross visible negative space.

### Outputs and checks for all passes

- Eligibility decision.
- Filters applied.
- Input and output pixel counts.
- Every measured feature and threshold.
- Proposed: mask overlays and decision brackets for each pass.

## 7. Editable geometry reconstruction

### Operations

- Simplify raster boundaries into Bézier loops.
- Preserve exterior loops, counters, holes, and islands.
- Skeletonize stroke-like artwork.
- Merge the straightest continuation through junctions.
- Split less-aligned branches into overlapping objects.
- Smooth centerlines.
- Cast local normals from centerlines to recover two satin rails.
- Split long turns before correspondence becomes unstable.

### Tunable parameters

| Parameter | Current behavior | Exposure |
| --- | --- | --- |
| Bézier flattening segments | 8 per segment | Engine |
| Skeleton continuation distance | 2.5 px | Engine |
| Junction alignment threshold | Dot product below -0.62 | Engine |
| Rail ray step | 0.25 px | Engine |
| Rail search radius | Local radius × 1.25 | Engine |
| Long-turn split threshold | Geometry-dependent | Engine |
| Centerline smoothing | Automatic | Engine |
| Node and handle positions | Editable | Full digitizer UI |
| Object type | Running, satin, or tatami | Full digitizer UI |
| Counter/hole preservation | Even/odd boundary parity for tatami | Engine |

### Outputs and checks

- Editable centerlines, boundaries, and paired rails.
- Junction and split locations.
- Source-to-vector deviation.
- Proposed: explicit uncertainty bands around reconstructed rails and counters.

## 8. Stitch-type classification

### Operations

- Use running stitch for narrow centerline detail.
- Use satin for narrow, elongated, single-span geometry with valid paired
  rails.
- Use tatami for broad, irregular, branched, holed, or invalid satin geometry.
- Preserve an operator-authored stitch type unless DRC blocks manufacture.

### Current tunables

| Parameter | Current value |
| --- | --- |
| Running maximum inferred width | 1.8 mm strategy limit |
| Narrow-stroke direct running width | Below 1.25 mm |
| Satin preferred minimum width | 1.0 mm |
| Wearable satin recommended maximum | 6.0 mm |
| Satin-to-fill transition ceiling | 8.0 mm |
| Raster satin aspect-ratio gate | At least 1.35 |
| Fill spacing | Profile default × strategy scale ÷ density multiplier |

### Proposed output

Every inferred object should report:

- selected stitch type;
- candidate alternatives;
- measurements supporting the decision;
- decision bracket;
- fallback used, if any;
- whether the selection was automatic or operator-authored.

## 9. Curved satin construction

This is the most important target for bracketed output.

### Operations

- Normalize rail direction.
- Resample both rails to matching topology.
- Pair corresponding locations across the column.
- Alternate penetrations across the rails.
- Keep stitch direction approximately normal to the centerline.
- Apply center-run underlay.
- Add edge or light zig-zag support for wider columns.
- Apply pull compensation to both rails.
- Extend and overlap joined column ends.
- Split at crossings, junctions, acute turns, and unstable correspondence.

### Current tunable parameters

| Parameter | Current default |
| --- | --- |
| Satin row spacing | 0.40 mm |
| Minimum satin width | 1.0 mm |
| Recommended wearable maximum | 6.0 mm |
| Fill-transition maximum | 8.0 mm |
| Wide-underlay threshold | 3.5 mm |
| Pull compensation | 0.0 mm until authored |
| Join overlap | 0.0 mm until authored |
| Inside-curve warning | Shortening deviation above 0.75 mm |
| Rail width score | Full through 6 mm; zero at 8 mm |
| Rail deviation score | Bounded physical score |
| Rail shortening score | Bounded physical score |

### Satin failure modes that need first-class brackets

1. **Tight inside radius**
   - Measure local centerline radius, inside-rail radius, column width,
     turn angle per course, and inside-rail penetration spacing.
   - Split, fan, stagger, or fall back before penetrations collapse into a
     dense knot.

2. **Negative-space collapse**
   - Measure each counter before compensation, after compensation, and after
     expected pull/push distortion.
   - Block a plan whose predicted clearance is zero or negative.
   - Warn when the remaining counter is below the selected material profile.

3. **Rail inversion or crossing**
   - Detect changes in paired-rail orientation and segment intersections.
   - Split before the first unstable interval.

4. **Rapid width change**
   - Measure width slope and local deviation.
   - Taper, split, or change construction when correspondence becomes visually
     discontinuous.

5. **Abrupt stitch-angle change**
   - Measure angle change between consecutive courses.
   - Smooth, insert a split, or create a controlled overlap.

6. **Outside-curve starvation**
   - Measure outside-rail course spacing.
   - Add coverage without duplicating inside-curve penetrations.

7. **Junction accumulation**
   - Count object starts, stops, overlaps, and underlay penetrations inside a
     physical neighborhood.
   - Move the join or reduce overlapping foundations.

8. **Compensation encroachment**
   - Report how much pull compensation consumes adjacent negative space or
     overlaps a neighboring object.

## 10. Tatami construction

### Operations

- Preserve exterior and hole loops using even/odd parity.
- Rotate geometry into the selected fill angle.
- Find every valid interior span on each row.
- Alternate row direction.
- Subdivide long rows into safe stitch lengths.
- Route nearby runs through covered interior where safe.
- Apply perpendicular underlay.

### Tunable parameters

- Fill spacing: 0.45 mm standard; profile-specific values range from
  0.38–0.55 mm.
- Density multiplier: 0.60–1.50.
- Top fill angle: 45° default.
- Underlay angle offset: 90°.
- Fill stitch length: 4.0–5.5 mm by profile.
- Connector maximum: 3 mm within raster fill; 7 mm vector-document default.
- Minimum stitch length: 4 mm for ordinary top fill.
- Machine maximum stitch: 6–12.1 mm by profile.
- Pull compensation and layer density multiplier.

### Proposed brackets

- Row-span length.
- Edge short-stitch rate.
- Hole/counter clearance.
- Density after overlapping layers.
- Connector coverage confidence.
- Predicted penetrations per square inch.

## 11. Running-stitch construction

### Operations

- Trace centerlines or authored paths.
- Resample by physical stitch length.
- Enforce minimum and maximum segment lengths.
- Reverse paths when routing benefits.
- Use covered running connectors where later objects hide them.

### Tunable parameters

- Minimum: 0.5 mm standard.
- Maximum: 6–7 mm for wearable profiles; 12.1 mm non-wearable.
- Common authored length: approximately 1–2.5 mm depending on role.
- Design foundation length: 2.5 mm.
- Repeat count for deliberate double-run detail.
- Connector visibility and coverage rules.

### Proposed brackets

- Segment-length distribution.
- Repeated-penetration concentration.
- Path deviation from the source centerline.
- Visible-negative-space crossings.

## 12. Compensation, underlay, and object finishing

### Operations

- Apply pull compensation across satin rails and solid boundaries.
- Apply join overlap at column transitions.
- Generate tie-in and tie-off stitches.
- Choose center, edge, zig-zag, or tatami underlay by construction.
- Avoid duplicating global silhouette and full object underlay unnecessarily.

### Tunable parameters

- Pull compensation per object.
- Push compensation at open ends; proposed explicit field.
- Join overlap per object.
- Underlay on/off.
- Underlay family and inset.
- Underlay density and stitch length.
- Tie pattern and length.
- Maximum local penetration count.

### Proposed brackets

- Predicted finished width.
- Counter clearance after compensation.
- Overlap depth.
- Underlay intrusion into negative space.
- Neighborhood penetration count.

## 13. Layering, sequence, and thread economy

### Operations

- Order physical layers.
- Respect authored sequence locks and overlap relationships.
- Route same-color objects by proximity after satisfying layer order.
- Reverse reversible objects where useful.
- Start center-outward for cap profiles.
- Replace safe short jumps with retained-thread travel.
- Hide connectors beneath later coverage.
- Keep a trim when travel would cross exposed negative space.

### Tunable parameters

| Parameter | Current default |
| --- | --- |
| Neighbor search distance | 12 mm |
| Hidden connector maximum | 7 mm |
| Raster trimless structural jump | 7 mm |
| Maximum objects | 400 |
| Maximum structural trims | 120 |
| Maximum jumps | 600 |
| Structural trims per 1,000 stitches | 2 |
| Jumps per 1,000 stitches | 10 |
| Detail trim allowance | 250 |
| Center-outward routing | Enabled for cap profiles |

### Outputs and checks

- Object sequence and direction.
- Neighbor graph.
- Structural versus detail trims.
- Machine jumps.
- Covered underpath connector count.
- Needle-down movement ratio.
- Trim and jump rates per 1,000 stitches.
- Proposed: show the rejected routing alternatives and the rule that prevented
  each unsafe connector.

## 14. Stitch normalization and machine DRC

### Operations

- Enforce stitch-family minimums and profile maximums.
- Subdivide excessive movements.
- Count stitches, jumps, trims, colors, objects, and layers.
- Calculate stitches per square inch.
- Calculate runtime from stitches, speed, trims, and color changes.
- Apply field and memory limits.

### Tunable parameters

- Running minimum and maximum.
- Satin minimum and maximum.
- Fill minimum and target length.
- Machine maximum movement.
- Maximum stitches per design.
- Maximum object, jump, trim, and rate budgets.
- Machine speed.
- Trim and color-change time overhead.

### Outputs and checks

- Semantic stitch-family violations.
- Movement-budget violations.
- Quality score and corrections.
- Stitch-memory blocker.
- Physical-field blocker.
- Estimated runtime.

## 15. Compilation, round trip, and virtual sew-out

### Operations

- Convert semantic operations into DST commands.
- Compile the machine file.
- Decode the compiled result rather than trusting the pre-export stream.
- Display stitch order, work motion, thread colors, and the stitch envelope.
- Trace the design boundary.
- Export only when a stitchable, machine-valid pattern exists.

### Tunable parameters

- Needle-motion or work-motion point of view.
- Design-fit or physical-work projection.
- Playback speed.
- Timeline position.
- Enabled colors.
- Active needle and thread assignment.

### Outputs and checks

- Compiled DST.
- Round-trip decoded operations.
- Difference between planned and retained operations.
- Proposed blocker when compilation changes color, movement, or endpoint
  semantics beyond tolerance.

## Decision-bracket output

### Principle

A bracket explains an automatic decision in production units. It should answer:

1. What did the engine measure?
2. What range is preferred?
3. How close is the result to a failure boundary?
4. What part of the object is affected?
5. What action did the engine take?
6. What can the operator safely change?

A bracket is more useful than a raw confidence score because it preserves the
measurement, threshold, and consequence.

### Common schema

```json
{
  "rule": "inside_curve_penetration_spacing",
  "object_id": "satin-12",
  "segment": {"start": 0.31, "end": 0.44},
  "observed": 0.42,
  "unit": "mm",
  "bracket": {
    "block_below": 0.30,
    "warn_below": 0.50,
    "preferred": [0.50, 1.20]
  },
  "status": "warning",
  "evidence": {
    "centerline_radius_mm": 1.8,
    "inside_rail_radius_mm": 0.7,
    "column_width_mm": 2.2,
    "course_angle_change_degrees": 14.0
  },
  "decision": "split_and_fan",
  "fallback": "two overlapping satin objects",
  "operator_controls": [
    "move split",
    "relax satin spacing",
    "reduce pull compensation",
    "convert interval to running stitch"
  ]
}
```

The numeric values above illustrate the contract. Production defaults must be
calibrated against sew-outs by material, thread, needle, stabilizer, and
machine profile.

### Bracket statuses

| Status | Meaning | Default behavior |
| --- | --- | --- |
| `pass` | Comfortably inside the validated range | Keep automatic result |
| `near_limit` | Valid but sensitive to compensation or material | Show in guided review |
| `warning` | Manufacturable with an automatic correction or operator review | Highlight affected interval |
| `fallback` | Preferred construction failed; another valid construction was used | Show both constructions |
| `blocker` | No validated construction remains | Prevent export |

### Satin bracket set

Each satin object should return object-level summaries and interval-level
brackets for:

- width;
- width deviation;
- width slope;
- centerline radius;
- inside-rail radius;
- inside penetration spacing;
- outside coverage spacing;
- consecutive course-angle change;
- rail orientation and crossings;
- rail correspondence distance;
- counter clearance before compensation;
- counter clearance after compensation;
- source-to-rail deviation;
- underlay clearance;
- overlap penetration concentration;
- predicted maximum stitch length.

### Negative-space bracket

Negative space must be treated as authored geometry, not the absence of a
foreground color.

For every counter or protected gap, report:

```json
{
  "rule": "negative_space_clearance",
  "object_id": "letter-o",
  "space_id": "counter-1",
  "observed": {
    "source_minimum_mm": 1.6,
    "vector_minimum_mm": 1.5,
    "after_compensation_mm": 1.0,
    "predicted_finished_mm": 0.8
  },
  "bracket": {
    "profile": "structured_cap",
    "block_at_or_below_mm": 0.0,
    "warning_below_mm": 1.0
  },
  "status": "warning",
  "decision": "reduce_inside_compensation",
  "protected_geometry": true
}
```

The editor should render protected negative space as a visible region with its
own boundary and warning band.

## UI presentation

The default UI should not expose all parameters simultaneously.

### Automatic view

- Artwork size.
- Frame.
- Thread colors.
- Density.
- Virtual sew-out.
- Stitch, object, trim, jump, density, and runtime metrics.
- Blocking findings.

### Guided review

- A pipeline strip showing each stage as pass, warning, fallback, or blocker.
- Click a stage to reveal its input mask, output geometry, and decision
  brackets.
- Click a finding to select and zoom to the affected object interval.
- Show the automatic correction and one or two safe alternatives.
- Allow comparison of nominal geometry against compensated and predicted
  finished geometry.

### Expert view

- Complete vector geometry.
- Protected negative-space objects.
- Satin centerline and both rails.
- Split and overlap controls.
- Stitch type, spacing, length, angle, underlay, and compensation per object.
- Layer and sequence graph.
- Routing, connector, trim, and DRC budgets.
- Raw bracket JSON for repeatability and API coaching.

## API additions

The existing API already returns much of the required evidence. A consistent
top-level structure should organize it:

```json
{
  "source": {},
  "physical_plan": {},
  "posterization": {},
  "elements": [],
  "composition": {},
  "objects": [],
  "sequence": [],
  "operations": [],
  "thread_economy": {},
  "drc": {},
  "brackets": [],
  "fallbacks": [],
  "compiled_round_trip": {}
}
```

Recommended request additions:

- `frame_profile`
- `material_profile`
- `thread_profile`
- `needle_profile`
- `stabilizer_profile`
- `detail_level`
- `protected_negative_spaces`
- `decision_overrides`
- `bracket_profile`

An override should name the decision it replaces and retain the engine’s
original recommendation in the output.

## Recommended implementation sequence

1. Create a shared `DecisionBracket` schema and attach it to current satin
   width, deviation, and shortening diagnostics.
2. Add interval-level centerline curvature, rail crossing, and course-angle
   measurements.
3. Promote counters and protected gaps to explicit negative-space geometry.
4. Evaluate negative-space clearance before and after compensation.
5. Add automatic satin splits at bracket transitions while retaining editable
   split locations.
6. Return fallback history instead of silently converting satin to tatami or
   running stitch.
7. Add a pipeline inspector to the full digitizer.
8. Build calibrated bracket profiles from controlled sew-outs and DST/vector
   fixtures.

## Acceptance criteria

- Every automatic stitch-type selection is explainable in physical units.
- Every satin fallback identifies the failed interval and rule.
- A counter cannot disappear silently after compensation.
- Rail crossing, inversion, or unstable correspondence blocks that satin
  interval.
- Tight curves are split, fanned, staggered, or downgraded deliberately.
- Operators can adjust a bracketed interval without redrawing the whole object.
- Default users can still upload, preview, and download without entering the
  expert workflow.
- API consumers can reproduce a plan from its source, profiles, overrides, and
  returned parameter values.

## ML-trained parameter engine and virtual sewout loop

New Hat will incorporate machine-learning training and evaluation directly into the parameter engine.

The enforced portable-record and transition contract is defined in
[`docs/machine-embroidery-training-bundle-v2.md`](docs/machine-embroidery-training-bundle-v2.md).

The system will not treat stitch parameters as fixed constants, nor will it rely on a single model to emit raw DST instructions. Instead, deterministic stitch generators will operate from a versioned set of learned parameters. Candidate parameter sets will be evaluated through automated virtual sewouts and reviewer classification.

The resulting feedback will become training data for future iterations.

```text id="wblx21"
Artwork region
    ↓
Region classification
    ↓
Candidate parameter generation
    ↓
Deterministic stitch construction
    ↓
Virtual sewout
    ↓
Automated measurements
    ↓
Reviewer classification
    ↓
Training record
    ↓
Updated stitch and fabric profiles
    ↓
Future candidate baseline
```

This turns digitization into a measurable optimization process rather than a collection of hard-coded embroidery conventions.

### Relationship to the current prototype

The current prototype already separates browser-generated operations from the `pystitch` codec. The codec accepts stitch, jump, trim and color-change operations and compiles them to DST, but it does not determine how an artwork region should be sewn.

The new parameter engine will occupy that missing decision layer.

```text id="ps1jal"
Raster and geometry interpretation
    ↓
ML-trained parameter engine
    ↓
Deterministic running, satin and fill generators
    ↓
Validated StitchPlan
    ↓
pystitch DST adapter
```

The parameter engine will decide how deterministic algorithms are configured. It will not initially replace those algorithms.

---

## Construction and search admission

Machine-learning search is only useful when the deterministic engine can
represent the required construction and the proposed controls can change the
compiled result.

Before generating candidates, the engine must evaluate the object or related
object group against a versioned search-admission contract.

```text
Semantic object or object group
    ↓
Can the current construction represent the required result?
    ↓
Can the active generator repair the current DRC findings?
    ↓
Which parameters are actually consumed by that generator?
    ↓
Do bounded parameter changes produce distinct compiled stitch streams?
    ↓
Admit parameter search or route to deterministic engine work
```

The admission record must answer:

1. Is the object representation adequate?
2. Does the generator support the required stitch family, underlay, corner,
   compensation, split, and routing strategies?
3. Which DRC findings are adjustable through parameters?
4. Which declared parameters are consumed by the selected generator?
5. Which parameters are active for this geometry and production profile?
6. Do sampled values alter geometry, semantic operations, or encoded machine
   operations?
7. Are multiple candidates merely different metadata for the same compiled
   stitch stream?

If representation or generator capability is missing, the object is not
admitted to ML search. It is routed to deterministic engine development.

Examples include:

* a wide satin column when no split-satin or acceptable fallback construction
  exists;
* unstable or crossing satin rails that require geometry repair;
* routing fragmentation when the engine exposes no entry, exit, connector, or
  ordering controls;
* a density control applied to a construction whose stitch stream ignores
  density; and
* compensation fields retained in the editable model but not applied by the
  compiler.

This gate prevents the learning system from assigning value to placebo
parameters or repeatedly searching a space that cannot contain a valid
solution.

### Parameter-actuation contract

Every deterministic generator must publish a machine-readable actuation
contract:

| Field | Meaning |
| --- | --- |
| `parameter` | Stable parameter identifier |
| `generator` | Generator and version that consume it |
| `applicable_stitch_families` | Running, satin, tatami, underlay, or routing |
| `applicable_geometry` | Width, curvature, hole, corner, or topology limits |
| `allowed_range` | Hard profile-bounded search interval |
| `default_value` | Current profile baseline |
| `operation_effect` | Geometry, stitch placement, order, movement, or metadata |
| `actuation_test` | Deterministic fixture proving that the value changes output |
| `inactive_reason` | Why the parameter is unavailable for the current object |

A parameter is eligible for training or exploration only when its actuation
test passes for the selected generator version. The compiled operation-stream
hash is the final equivalence test: candidates with identical streams are one
manufacturing candidate, even if their metadata differs.

---

## Parameterized stitch construction

Training experiments are separated by pipeline phase. A reviewer never compares
a color-separation change and a stitch-density change in the same bracket set.
Every candidate still compiles through the complete downstream pipeline so the
decision is evaluated in the context of a final embroidery file.

### Color-separation parameters

* Foreground color limit
* Related-shade merge tolerance
* Fragmented antialias-shade merge threshold
* Thread-family hue, saturation, and value tolerances
* Minimum retained color-region area
* Palette error relative to source pixels
* Semantic thread-color count

Color-separation candidates must show the source, the posterized result, the
thread palette, and the final sewout together. A successful one-color mark may
begin with several JPEG or antialias shades, but its preferred target can still
be one physical thread color.

Each stitch family will then expose a controlled parameter space.

### Satin parameters

* Satin angle or local direction field
* Stitch spacing
* Minimum and maximum stitch length
* Rail inset or expansion
* Pull compensation
* Corner strategy
* Short-stitch behavior
* Split-satin threshold
* Center-run underlay
* Contour underlay
* Zig-zag underlay
* Underlay inset
* Underlay density
* Entry and exit positions
* Tie-in and tie-off policy

### Tatami parameters

* Fill angle
* Row spacing
* Maximum stitch length
* Minimum stitch length
* Stagger count and phase
* Row-to-row progression
* Edge inset
* Travel routing
* Hole-routing policy
* Short-row treatment
* Edge-run underlay
* Fill-underlay angle
* Fill-underlay spacing
* Underlay inset
* Compensation
* Entry and exit positions

### Running-stitch parameters

* Target stitch length
* Minimum stitch length
* Corner shortening
* Path simplification tolerance
* Bean-stitch repetition
* Repetition offset
* Underpath eligibility
* Connector visibility threshold
* Tie policy

### Routing parameters

* Maximum connector distance without trim
* Hidden-travel tolerance
* Jump penalty
* Trim penalty
* Color-change penalty
* Object-order weighting
* Registration-risk weighting
* Center-out preference
* Edge-to-center preference
* Entry and exit optimization strength

These values should be bounded by hard manufacturing constraints. Machine
learning may select values within an allowed range, but it may not bypass DST,
machine, frame or safety limits.

Hard manufacturing limits remain deterministic, versioned rules. Learned
profiles provide preferred values, conditional ranges and confidence within
those limits; they do not learn replacements for safety or format constraints.

### DRC repair classification

Before candidate generation, each actionable DRC finding must be assigned one
or more repair mechanisms:

| Repair class | Meaning |
| --- | --- |
| `parameter_adjustable` | A proven active parameter can address the finding |
| `geometry_repair` | Boundaries, rails, centerlines, holes, or topology must change |
| `stitch_family_change` | Running, satin, tatami, split satin, or another construction is required |
| `routing_repair` | Entry, exit, object order, connector, trim, or color sequence must change |
| `profile_change` | The requested article, material, or machine profile is incompatible |
| `generator_capability_missing` | The deterministic engine cannot yet construct the repair |
| `physical_calibration_required` | Virtual evidence cannot decide the finding reliably |

Only `parameter_adjustable` findings enter ordinary parameter search. Other
classes create explicit engineering, editing, profile, or sewout-calibration
work rather than consuming unproductive ML iterations.

---

## Candidate generation

For every admitted embroidery object or tightly coupled object group, the
engine should generate several candidate plans around the current profile
baseline. Candidate generation must respect overlap, registration, shared-edge
and sequencing constraints; independently optimizing every object would create
a combinatorial search and could destroy composition-level relationships.

For example:

```text id="bnfzun"
Candidate A:
    satin spacing = 0.40 mm
    pull compensation = 0.20 mm
    center-run + contour underlay

Candidate B:
    satin spacing = 0.38 mm
    pull compensation = 0.30 mm
    center-run + zig-zag underlay

Candidate C:
    satin spacing = 0.42 mm
    pull compensation = 0.25 mm
    contour underlay only
```

Candidate generation can initially use:

* Parameter grids
* Latin hypercube sampling
* Bounded random perturbation
* Bayesian optimization
* Evolutionary search
* Profile-derived presets

As training data accumulates, the candidate generator should concentrate exploration around parameter regions that have historically produced acceptable sewouts while continuing to test a smaller number of exploratory alternatives.

The system must preserve the distinction between:

```text id="26azui"
exploitation:
    use parameters already known to perform well

exploration:
    test nearby or novel parameters that may perform better
```

Without deliberate exploration, reviewer feedback will merely reinforce the first acceptable baseline.

Candidate generation must also:

* hold inactive parameters at the versioned baseline;
* record why each inactive parameter was excluded;
* remove candidates with identical compiled operation hashes;
* retain a baseline candidate in every comparison set;
* identify whether a candidate is exploitation or exploration;
* regenerate only affected objects and dependent routes where possible; and
* stop parameter search when every remaining blocker requires a non-parametric
  repair.

Candidate sets are grouped into independent experiments:

1. **Color separation:** literal through thread-consolidated palettes.
2. **Object and stackup interpretation:** satin, tatami, or repeated running
   constructions for the same separated geometry.
3. **Stitch construction:** spacing, density, pass count, underlay, and routing
   parameters within one selected construction.

Posterized-image equivalence and compiled-operation equivalence are both
reported. Equivalent final machine plans collapse to one review candidate.

---

## Automated virtual sewout

Every candidate StitchPlan will be rendered through a repeatable virtual sewout process.

The virtual sewout should generate:

* Final appearance rendering
* Stitch-order playback
* Thread coverage map
* Needle-penetration density map
* Stitch-length distribution
* Direction-field visualization
* Underlay visibility map
* Edge-conformity map
* Travel and jump map
* Overlap-depth map
* Estimated stitch count
* Estimated runtime
* Trim and color-change counts

Every render and measurement must state whether it is:

* directly measured from the semantic or encoded operation stream;
* analytically derived from geometry;
* predicted by the virtual material model; or
* observed in a physical sewout.

Machine-file output must be decoded after encoding and compared with the
semantic plan. Virtual review should use the decoded machine path whenever the
output format can change, subdivide, or discard commands.

The reviewer should not be limited to a photorealistic preview. Diagnostic views are necessary to explain why one candidate is better than another.

For example, two satin candidates may appear similar at normal scale while differing significantly in:

* Rail conformity
* Corner penetration concentration
* Underlay exposure
* Stitch-length regularity
* Edge coverage
* Total stitch count

The virtual sewout therefore serves both as a visual simulation and as an inspection interface.

---

## Automated evaluation

Before human review, each candidate should receive machine-generated measurements.

### Artwork-conformity measurements

* Boundary displacement from the original art
* Area coverage difference
* Negative-space preservation
* Hole-size preservation
* Centerline alignment
* Detail retention
* Color-region overlap
* Visible edge deviation

### Satin measurements

* Percentage of the original region covered
* Percentage of stitches crossing from one valid rail to the other
* Rail penetration conformity
* Local satin-width violations
* Stitch-angle continuity
* Corner bunching
* Excessively short stitches
* Excessively long stitches
* Split-satin usage
* Underlay exposure
* Predicted edge pull

### Tatami measurements

* Region coverage
* Gap area
* Excessive overlap
* Row-spacing consistency
* Stagger effectiveness
* Needle-column repetition
* Edge conformity
* Hole preservation
* Travel-stitch exposure
* Row fragmentation
* Local penetration density

### Underlay measurements

* Underlay containment within the top-stitch region
* Underlay coverage
* Underlay-to-top angle relationship
* Edge support
* Center support
* Density
* Excessive buildup
* Visibility outside the final region
* Number of unnecessary underlay segments

### Production-efficiency measurements

* Total stitch count
* Top-stitch count
* Underlay stitch count
* Semantic object-transition count
* Logical jump-operation count
* Encoded machine jump-record count
* Hidden connector count and distance
* Exposed travel count and distance
* Total jump distance
* Maximum jump displacement
* Trim count
* Color changes
* Estimated execution time
* Repeated penetrations
* Long-stitch subdivisions
* Machine-limit violations

These measurements should be stored whether or not a reviewer sees them.

### Staged measurement implementation

Automated measurements should be implemented and validated in this order:

1. Machine validity, exact displacement and encode/decode agreement.
2. Satin rail conformity, width, crossing and long-span detection.
3. Artwork boundary, counter, negative-space and hole preservation.
4. Underlay containment, support and top-stitch relationship.
5. Local penetration concentration, repeated columns and density buildup.
6. Visible travel, semantic versus machine jumps, trims and fragmentation.
7. Predicted pull, push, registration, distortion and material response.

Direct measurements must not be blended invisibly with renderer predictions.
Each metric carries its method and version so physical feedback can calibrate
the correct subsystem.

---

## Reviewer classification

Reviewers should classify both the overall candidate and specific aspects of its construction.

A simple overall rating could be:

```text id="r0u2vf"
ACCEPT
ACCEPT_WITH_RESERVATIONS
REVISE
REJECT
```

That rating alone is not sufficient for training. The reviewer should also identify the reason.

### Suggested review dimensions

| Dimension                    | Example classifications                                        |
| ---------------------------- | -------------------------------------------------------------- |
| Artwork conformity           | accurate, swollen, contracted, missing detail, distorted       |
| Satin completeness           | complete, gaps, rail misses, corner failure, excessive split   |
| Satin appearance             | tidy, irregular, loose, over-dense, directionally inconsistent |
| Tatami coverage              | complete, gaps, excessive overlap, poor edge, visible travel   |
| Underlay                     | appropriate, insufficient, excessive, exposed, wrong technique |
| Density                      | low, acceptable, high, damaging                                |
| Color accuracy               | accurate, acceptable, shifted, incorrect                       |
| Stitch length                | stable, too short, too long, inconsistent                      |
| Registration                 | stable, moderate risk, unacceptable                            |
| Efficiency                   | efficient, acceptable, excessive stitches, excessive trims     |
| Sew order                    | appropriate, inefficient, registration risk, visible travel    |
| Overall production readiness | ready, minor edit, major edit, unusable                        |

The reviewer should also be able to select a preferred candidate from a set:

```text id="w67b0m"
Candidate B is better than Candidate A because:
    cleaner edge
    lower stitch count
    adequate underlay
    fewer short stitches
```

Pairwise preferences are valuable because reviewers are often more consistent when choosing between alternatives than when assigning an absolute score.

Virtual-review classification and physical-sewout classification are separate
events. A virtual reviewer may approve a candidate for sampling; only a
physical review can label actual coverage, distortion, stiffness, thread
breaks, registration and production readiness.

### Physical-sewout eligibility

A candidate is recommended for physical sewout only when:

1. it has no blocking or conditional-blocking DRC findings;
2. it represents a unique compiled operation stream;
3. every varied parameter has a passing actuation contract;
4. it has no unresolved structural repair requirement;
5. its source, profile, generator, renderer and DRC versions are pinned; and
6. it is either an improved baseline candidate or a deliberate, bounded
   exploration candidate.

Review findings must be acknowledged before sewing. Blocked candidates remain
available for diagnosis and repair research but are not presented as
production-ready samples.

---

## Training-record structure

Each reviewed candidate should become a self-contained training record.

```json id="g9ph2f"
{
  "sample_id": "sample-000184",
  "artwork_id": "artwork-0042",
  "region_id": "region-0017",
  "profile": {
    "machine": "tajima-cap-v1",
    "fabric": "structured-cotton-twill-v2",
    "thread": "polyester-40",
    "needle": "75-11",
    "frame": "cap"
  },
  "geometry_features": {
    "area_mm2": 422.7,
    "perimeter_mm": 118.3,
    "minimum_width_mm": 2.8,
    "maximum_width_mm": 9.4,
    "curvature_summary": {},
    "hole_count": 0
  },
  "stitch_family": "satin",
  "parameters": {},
  "automated_metrics": {},
  "drc": {
    "policy_version": "new-hat-drc/1.0",
    "status": "review",
    "findings": [],
    "repair_classification": {}
  },
  "actuation": {
    "active_parameters": [],
    "inactive_parameters": {},
    "semantic_operation_hash": "",
    "encoded_operation_hash": ""
  },
  "review": {
    "overall": "ACCEPT_WITH_RESERVATIONS",
    "artwork_conformity": "accurate",
    "underlay": "excessive",
    "efficiency": "excessive_stitches",
    "notes": "Reduce zig-zag underlay density."
  },
  "provenance": {
    "source_sha256": "",
    "plan_sha256": "",
    "machine_file_sha256": "",
    "split_group": "",
    "license": "",
    "training_use": ""
  },
  "correction": {
    "original_prediction_ref": "",
    "structured_delta_ref": "",
    "accepted_repair_ref": ""
  },
  "profile_version": "structured-cap-satin-0008",
  "generator_version": "satin-generator-0.4.1",
  "renderer_version": "virtual-sewout-0.3.0"
}
```

Versioning is essential. A review is only meaningful when the exact geometry, generator, renderer and profile versions can be reconstructed.

The record must also preserve:

* source, plan, render, semantic-operation and machine-file hashes;
* DRC policy version and raw findings;
* repair classifications;
* generator parameter-actuation results;
* rights and permitted training/evaluation use;
* design-family split group;
* original prediction and structured correction delta;
* reviewer identity and attributed DRC overrides;
* whether a label is virtual, analytical or physical; and
* encode/decode differences.

---

## Learned stitch profiles

The system should not converge on one universal set of embroidery parameters.

It should develop hierarchical profiles.

```text id="2wxwph"
global stitch-family baseline
    ↓
machine-specific profile
    ↓
frame-specific profile
    ↓
fabric-family profile
    ↓
garment-specific profile
    ↓
geometry-specific adjustment
```

An example profile hierarchy:

```text id="98p53r"
satin-global
└── tajima-satin
    └── tajima-cap-satin
        └── structured-twill-satin
            ├── narrow-column
            ├── medium-column
            ├── wide-column
            ├── acute-corner
            └── high-curvature
```

A trained profile should contain more than one average parameter value.

It should retain:

* Preferred value
* Valid range
* Confidence
* Number of supporting examples
* Distribution of accepted values
* Failure boundaries
* Applicable geometry range
* Applicable machine and fabric context
* Last training date
* Parent profile
* Training dataset version

For example:

```json id="m0jwsr"
{
  "parameter": "pull_compensation_mm",
  "preferred": 0.27,
  "accepted_range": [0.20, 0.34],
  "confidence": 0.82,
  "supporting_reviews": 346,
  "applicable_width_mm": [3.0, 8.5]
}
```

New examples should update the profile statistically rather than simply replacing the old baseline with the most recently accepted candidate.

Sparse child profiles must use hierarchical backoff or partial pooling rather
than behaving as independent tables. A geometry/material leaf with little
evidence inherits its parent distribution and may deviate only in proportion
to reviewed physical support. Confidence must decrease when a query falls
outside the geometry, material, machine or frame range represented in its
training data.

---

## Baseline promotion

A reviewed candidate should not automatically become the production baseline.

Profile promotion should require:

1. A minimum number of reviewed examples.
2. Improvement over the current baseline.
3. No regression against a fixed validation set.
4. No increase in hard-rule violations.
5. Acceptable performance across more than one artwork example.
6. Separate evaluation by stitch family and fabric profile.
7. Versioned rollback capability.
8. Passing construction/search admission for every promoted parameter.
9. Evaluation on unique compiled outcomes rather than duplicate metadata
   candidates.
10. Stratified results for geometry, stitch family, material, frame and
    machine context, with no hidden regression in a small but critical group.

The training process should distinguish among:

```text id="7js84k"
experimental profile
candidate profile
validated profile
production profile
retired profile
```

This prevents one unusual logo or reviewer preference from contaminating the general baseline.

---

## Training objectives

The parameter model should optimize several competing goals.

```text id="t0bl28"
visual accuracy
+ stitch completeness
+ edge conformity
+ underlay effectiveness
+ mechanical stability
+ production efficiency
+ reviewer acceptance
```

These goals should not be collapsed prematurely into a single opaque score.

A useful scorecard might contain:

```text id="mks0v7"
Artwork fidelity:        0–100
Coverage completeness:   0–100
Structural support:      0–100
Stitch regularity:       0–100
Production efficiency:   0–100
Rule compliance:         pass/fail
Reviewer acceptance:     categorical
```

A design that receives excellent visual-fidelity scores but fails density or machine constraints must remain ineligible for promotion.

---

## Virtual-to-physical calibration

Virtual sewout review can train the planner efficiently, but it will also train against the assumptions of the renderer.

That creates a simulation-bias risk:

```text id="nv91z3"
model learns to satisfy virtual renderer
rather than physical embroidery
```

Physical sewouts must therefore be sampled throughout development.

The physical process should compare:

* Predicted coverage against photographed coverage
* Predicted edge position against measured edge position
* Predicted distortion against observed distortion
* Predicted density risk against thread breaks or fabric damage
* Predicted registration against actual registration
* Estimated runtime against machine runtime

Differences should be used to calibrate the renderer and the profile engine.

The feedback hierarchy becomes:

```text id="brp125"
automated virtual metrics
    ↓
reviewer classification
    ↓
sampled physical sewout
    ↓
renderer calibration
    ↓
profile correction
```

Virtual review supplies scale. Physical review supplies truth.

---

## Revised development sequence

### 0. Establish construction and search admission

Verify representation adequacy, classify DRC repair mechanisms, publish
generator actuation contracts, eliminate equivalent compiled candidates and
route missing capabilities to deterministic engine work.

### 1. Parameterize deterministic generators

Expose all meaningful satin, tatami, running-stitch, underlay and routing parameters through the StitchPlan.

### 2. Build repeatable virtual sewout

The same plan and renderer version must always produce the same visual and analytical result.

### 3. Add automated measurements

Generate coverage, conformity, density, stitch-length, travel and efficiency metrics.

### 4. Build reviewer classification

Allow overall, dimensional and pairwise review.

### 5. Persist complete training records

Store artwork context, geometry, parameters, metrics, classifications and software versions.

### 6. Establish initial expert baselines

Seed profiles with accepted digitizing conventions and reviewer-approved examples.

### 7. Train parameter recommendation

Predict parameter ranges from region geometry, stitch family and production profile.

### 8. Add controlled exploration

Generate candidate variations around the learned baseline.

### 9. Promote profiles through regression testing

Only validated improvements become future production defaults.

### 10. Calibrate with physical sewouts

Continuously measure and reduce the gap between virtual and physical results.

---

## Revised architectural principle

The parameter engine is not a static rule table.

It is a versioned learning system:

```text id="dk0gpy"
deterministic geometry
+ construction and search admission
+ proven parameter actuation
+ bounded parameter search
+ virtual sewout
+ automated measurements
+ human classification
+ profile training
+ regression-controlled promotion
```

The result should be a growing library of trained stitch and fabric profiles that answer questions such as:

* Which underlay technique performs best for this geometry and fabric?
* How much satin compensation preserves the source silhouette?
* What tatami spacing produces complete coverage without unnecessary buildup?
* When does a region need satin rather than fill?
* How can stitch count be reduced without degrading appearance?
* Which routing choice reduces trims while keeping connectors hidden?
* Which parameter set produces the tidiest acceptable sewout for this production profile?

The system’s competitive advantage will come from the accumulated relationship among artwork geometry, chosen parameters, virtual outcomes, reviewer classifications and physical sewout results.

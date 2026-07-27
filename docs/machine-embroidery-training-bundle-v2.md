# Machine Embroidery Training Bundle Process v2

This document is the implementation companion to
[`ml-cycle.md`](../ml-cycle.md). The ML-cycle document defines the parameter
engine and promotion doctrine. This document defines the evidence contract
used by the training workspace and its exported portable bundle.

## Purpose

Bundle v2 is designed to:

1. reproduce a candidate from its source, production context, parent state,
   complete parameters, generator environment, and deterministic seed;
2. compare effective manufacturing outputs without treating duplicate plans
   or DST streams as independent evidence;
3. attach automated, virtual, expert, and physical observations to the exact
   action that produced an immutable candidate; and
4. promote a learned profile only after held-out, DRC, virtual, and physical
   validation.

A parameter sweep is not automatically an RL episode. A sweep becomes a
transition only after a complete review accepts a feasible candidate and
materializes that candidate as the next immutable state.

## Record model

| Record | Meaning |
| --- | --- |
| Source | Original content identity, artwork-family split key, rights, and normalization transforms |
| State | Immutable source, production context, complete parameter vector, generator, and inherited decisions |
| Proposal | Candidate policy, parent state, controlled fields, values, seed, and constraints |
| Candidate | One proposed action and its plan/output identities |
| Effective candidate | One canonical decoded stitch stream shared by one or more proposals |
| Comparison | Blind presentation order and candidates admitted for review |
| Review | Completion, disposition, ranking, per-axis scores, defects, and reviewer provenance |
| Reward | Separate raw and normalized virtual, expert, physical, efficiency, and risk components |
| Transition | Accepted, rejected, inconclusive, or abandoned state change |
| Physical run | Machine, material, settings, observations, photographs, and operator review |

## Generation contract

### Intake and normalization

The original source is stored once as a content-addressed object. Its source
record includes filename, media type, byte length, SHA-256, artwork-family ID,
requested and realized size, pixels per millimetre, and ordered transforms.
Unknown rights remain explicit and make the bundle ineligible for training.

The production context is part of the state. Product, machine, frame, fabric,
stabilizer, topping, thread, needle, speed, placement, and calibrated profile
versions are recorded. Missing values are `null` with an unknown reason; they
are not silently replaced with a generic production claim.

### Immutable state and action

Every proposal references one `parent_state_id`. Every candidate stores:

- `requested_action`: the values proposed by the experiment;
- `action_delta`: the actual differences from the parent state;
- `resolved_parameters`: the complete result after inheritance and defaults;
- parameter provenance and actuation evidence; and
- `action_hash`, `plan_hash`, and `output_hash`.

The exporter rejects undeclared resolved-parameter differences. A selected
candidate only affects later experiments after an accepted transition creates
the next state.

### Deterministic generation and identities

The generator record captures the source commit and dirty-tree state,
dependency-lock hash, component versions, operating system, architecture,
runtime, invocation, seed, and UTC export time.

Identity is computed from canonical UTF-8 JSON with sorted keys, no
insignificant whitespace, finite normalized numbers, explicit `null`, and no
volatile timestamps or generated IDs in action content.

The three candidate identities are:

- `action_hash`: parent state, complete resolved parameters, and generator;
- `plan_hash`: canonical StitchPlan with volatile preview/timestamp fields
  removed; and
- `output_hash`: canonical decoded DST command stream.

Proposals with the same `output_hash` remain traceable but point to one
effective candidate. Their individual learning weights sum to one.

### Feasibility, DRC, and status

The authoritative status separates:

- `hard_feasibility_status`;
- `planning_budget_status`; and
- `review_eligibility_status`.

Round-trip failure, zero decoded stitches, hard DRC blockers, and encoded
maximum-stitch violations block a candidate. Planning-budget failures and
other advisory findings produce `review`, never a contradictory `pass`.
Blocked candidates remain available for failure learning but cannot become a
production winner.

Metrics carry a stable name, definition, unit, direction, method/version, raw
value, normalized value, and applicable production profile. Semantic jumps
and decoded machine jumps are distinct measures.

### Explainable construction

Each candidate contains a compact `recipe.json` and `layers.json`. These map
semantic objects and operation spans to:

- `layer_role`: foundation, underlay, blocking, border, detail, or travel;
- `construction_family`: running, satin, or tatami;
- `construction_technique`: center run, edge run, zigzag underlay, tatami
  underlay/fill, satin column, single run, bean, or layered run;
- `semantic_role`: fill, border, standalone line, lettering, detail,
  connector, or registration support; and
- `relationship`: supports, covers, borders, overlays, connects, or
  independent.

The expanded StitchPlan remains authoritative for coordinates.

### Human and physical evidence

The first review pass is blind. Parameter labels and metrics are revealed only
in diagnostic mode. A complete review requires every effective candidate to be
inspected, a full ranking, a disposition, and anchored 1–5 scores for:

- silhouette fidelity;
- detail retention;
- boundary smoothness;
- lettering legibility;
- negative-space integrity;
- coverage;
- stitch-family suitability;
- color fidelity;
- thread economy;
- path efficiency;
- visible-defect severity; and
- overall production acceptability.

Virtual coverage and appearance remain virtual observations. Physical quality
stays `null` until an immutable candidate receives a recorded machine sewout.

## Portable layout

```text
training-session-<session_id>/
  manifest.json
  COMPLETE
  checksums.sha256
  schemas/
  source/source.json
  context/
    production-profile.json
    rights.json
  generator/environment.json
  states/
  proposals/
  candidates/
    <candidate_id>/
      candidate.json
      action.json
      recipe.json
      layers.json
      metrics.json
      drc.json
      artifact-refs.json
    effective/
  comparisons/
  reviews/
  rewards/
  transitions/
  physical-runs/
  objects/sha256/<digest>
```

Large plans and repeated images are stored once under `objects/sha256/`.
Portable bundles do not use symlinks. The exporter validates records and
references, decodes and re-encodes each DST, verifies content identities,
writes checksums, and writes `COMPLETE` last before returning the in-memory
archive. A failed export leaves the temporary session intact for retry.

## Training readiness

`manifest.json` reports both `training_ready` and explicit findings. A bundle
is training-ready only when:

- all records satisfy their included schemas;
- indexed references and artifact hashes resolve;
- generator provenance and deterministic seed are present;
- candidates have a parent state, actual action delta, and full parameters;
- duplicate outputs share one effective-candidate learning weight;
- feasibility, advisory DRC, and preference states do not conflict;
- review completeness and disposition are explicit;
- virtual and physical observations are distinct;
- every accepted choice creates a traceable next state;
- every DST passes canonical round-trip verification;
- the artwork-family split key is declared; and
- the final composed candidate is evaluated after stage-level selection.

Bundles that fail these conditions remain useful experiment evidence, but must
not be consumed as clean reinforcement episodes.

## Dataset and promotion rules

Training/evaluation splits use `artwork_family_id`, never candidate ID.
Near-duplicates and derived artwork stay in the same split. Each effective
output receives one sample weight.

A learned profile remains a versioned challenger until it passes the held-out
comparison, hard DRC regression suite, virtual-sewout benchmark, physical
calibration set, and champion/challenger threshold. Historical profile versions
are immutable.

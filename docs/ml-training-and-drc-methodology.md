# ML training and DRC methodology

Status: executable methodology, policy version `new-hat-drc/1.0`
Scope: semantic proposal models, deterministic stitch compilation, training
corpus construction, regression tests, and physical validation

## Objective

The learned system should improve ambiguous design decisions without learning
to bypass physical embroidery constraints.

```text
artwork + article profile
  -> learned semantic proposals
  -> editable object graph
  -> deterministic stitch compiler
  -> versioned DRC
  -> accept, repair, review, or attributed override
  -> machine file and sew-out
  -> structured human/physical feedback
```

Models may propose foreground masks, objects, layers, stroke/area classes,
stitch families, satin rails, fill angles, underlay choices, compensation,
salience, and sequence candidates. Deterministic code remains authoritative
for geometry validity, exact stitch placement, containment, underlay
construction, machine limits, DRC, and file encoding.

The model must never be trained to emit an opaque machine file as its only
output, and a learned quality score must never replace deterministic DRC.

## Three uses of DRC

### Enforcement

Every candidate is compiled and checked by the same pinned DRC policy used by
batch production. Policy 1.0 treats these as blocking:

- no needle-down stitches;
- machine stitch-memory overflow;
- unresolved stitch vectors over the machine/profile length;
- explicit blocker findings; and
- semantic long-stitch violations that require a profile or geometry change.

Blocking output cannot become an approved semantic training target or pass an
evaluation gate without an attributed override containing a reviewer and
reason.

Warnings, movement budgets, short-stitch findings, and current decision
brackets produce review labels. Satin curve bracket thresholds are retained as
object-level training evidence but remain advisory until calibrated against
physical sew-outs for each profile. This prevents an uncalibrated threshold
from silently rejecting the corpus.

### Training signal

DRC is an auxiliary, multi-label target:

- global disposition: `pass`, `review`, or `block`;
- rule identity and severity;
- affected object;
- violation count;
- semantic and movement-budget counts;
- stitch, trim, jump, color-change, density, and runtime-related measures;
- satin bracket status and decision;
- proposed repair; and
- before/after result when a repair is accepted.

Use DRC auxiliary heads to predict risk early enough to rank semantic
proposals. Do not feed post-compilation DRC results back into the proposal
model as input features; that leaks the target.

A suitable multi-task objective is:

```text
L =
  L_object_segmentation
  + L_layer_and_occlusion
  + L_stitch_family
  + L_geometry_or_rail
  + L_parameter_regression
  + L_sequence_ranking
  + lambda_drc * L_DRC_risk
```

`L_DRC_risk` is not permission to trade a hard constraint against a better
visual loss. After inference, the deterministic compiler and DRC gate still
decide whether output may proceed.

Use class weighting or focal loss for rare blocker rules. Report performance
per rule and per article profile; an aggregate DRC accuracy can hide a model
that never predicts rare production failures.

### Testing

Every candidate model is evaluated at four levels:

| Level | Required measures |
| --- | --- |
| Artwork | foreground/object recall, boundary error, counter and negative-space preservation, color-depth agreement |
| Semantic plan | stitch-family accuracy, layer/overlap accuracy, rail error, parameter error, fragmentation, sequence constraints |
| Compiled machine plan | DRC status, rule counts, trims/jumps per 1,000 stitches, color changes, local density, stitch count, runtime |
| Physical sew-out | registration, distortion, puckering, coverage, stiffness, thread breaks, readability, expert acceptance |

The regression gate rejects:

- `pass -> review` or `pass/review -> block`;
- increased hard or conditional blockers;
- a quality-score decrease beyond the declared tolerance;
- trim or jump rates beyond the declared tolerance;
- missing held-out examples unless explicitly allowed; and
- a newly introduced example that compiles to blocked output.

The heuristic quality score is diagnostic, not the primary metric. Primary
product measures are first-pass production acceptance, correction time, and
successful physical sew-out.

## Training-record contract

Each generated fixture and queued job emits a versioned training record beside
its plan. Batch output also contains `training-records.jsonl`.

The record separates:

- `features`: pre-proposal production context and artwork observations;
- `proposal`: the generated object/stitch-family/parameter choices;
- `labels.drc`: deterministic policy output;
- `labels.drc_auxiliary_targets`: rule/count targets;
- `labels.machine_metrics`: compiled-plan measures;
- `labels.human`: review, corrections, acceptance, and sew-out references;
- `provenance`: source and plan hashes, rights, reviewer, and engine version;
- `eligibility`: allowed supervision tasks; and
- `split_group`: design-family grouping used before deterministic splitting.

Generated data is quarantined by default. It is not silently promoted to
ground truth.

### Eligibility

| Evidence | Semantic supervision | DRC auxiliary training | Evaluation |
| --- | ---: | ---: | ---: |
| Unreviewed generated proposal | No | No | No |
| Reviewed rejected proposal | No | Yes, if training rights allow | Yes |
| Expert-approved plan | Yes, if training rights allow | Yes | Yes |
| Evaluation-only licensed plan | No | No | Yes |
| Prohibited or unspecified use | No | No | No |

An expert-approved plan that still blocks DRC requires a structured override.
This conflict is valuable calibration evidence, but it must not be hidden.

## Split and leakage policy

Split on `split_group`, not the generated file or plan revision. All sizes,
profiles, edits, source encodings, and machine variants of one mark must stay
in one split.

The default split for training-permitted families is 80% train, 10%
validation, and 10% test. Evaluation-only families are assigned between
validation and test. The assignment is a deterministic hash, so rebuilding
the corpus does not reshuffle examples.

Also isolate a final physical acceptance set by customer/design family,
article profile, and sew-out session. Near-duplicate artwork must not cross
those boundaries.

## Corpus strata and sampling

Balance the corpus across:

- simple one-color marks;
- curved script and closed counters;
- small lettering;
- satin/tatami boundary cases;
- multi-color overlap and blending;
- illustrated or high-edge-count artwork;
- cap seams and center-out routing;
- high-pile and puncture-sensitive materials;
- intentional negative space;
- rejected plans and accepted repairs; and
- known trim, jump, density, registration, and distortion failures.

Machine files without paired artwork and expert review are weak evidence. They
may provide movement distributions, stitch-vector priors, inferred stitch
families, and regression fixtures, but not unquestioned semantic targets.
Every inferred label needs its method, confidence, parser version, provenance,
and review state.

## Human correction and repair records

Capture each edit as a structured delta rather than only saving the final
plan:

- original proposal and confidence;
- DRC policy and findings before the edit;
- merged/split objects;
- changed layer, stitch family, rail, fill angle, underlay, compensation,
  entry/exit, or sequence;
- reviewer identity and reason;
- DRC after incremental recompilation;
- predicted and actual changes in count/runtime;
- physical sew-out result; and
- final acceptance.

This supports preference/ranking examples:

```text
same artwork + same profile:
  rejected proposal < accepted repair
```

It also supports counterfactual DRC training: the model can learn which
semantic decisions tend to create a downstream violation while the exact rule
remains deterministic.

## Operational workflow

Build a corpus from generated fixture or queue manifests:

```sh
python3 tools/build_ml_corpus.py artifacts/bracket-fixtures \
  --output artifacts/ml-corpus \
  --annotations fixtures/ml-annotations.json \
  --enforce
```

Start from
[`fixtures/ml-annotations.example.json`](../fixtures/ml-annotations.example.json)
and record actual rights and review evidence. `--enforce` fails on ingest
errors, missing license evidence for training-permitted records, design-family
split leakage, and silent overrides.

The output contains:

- `records.jsonl`: every deduplicated revision;
- `drc-regression.jsonl`: every engineering fixture, including quarantined
  records, for deterministic compiler/DRC comparison only;
- `train.jsonl`, `validation.jsonl`, and `test.jsonl`: eligible split records;
- `quarantine.jsonl`: unreviewed or rights-unknown records; and
- `summary.json`: class, task, integrity, and ingest counts.

Create a pinned deterministic baseline, then build and compare a candidate:

```sh
python3 tools/build_ml_corpus.py artifacts/bracket-fixtures \
  --output artifacts/ml-baseline --enforce

python3 tools/build_ml_corpus.py artifacts/bracket-fixtures \
  --output artifacts/ml-candidate --enforce

python3 tools/check_drc_regression.py \
  artifacts/ml-baseline/drc-regression.jsonl \
  artifacts/ml-candidate/drc-regression.jsonl \
  --output artifacts/ml-candidate/drc-regression.json
```

The regression view does not confer training rights and must not be passed to
a model trainer. Use `test.jsonl` only after reviewed, rights-eligible records
exist; an empty comparison is rejected by default because it proves nothing.

Pin the following together in every experiment:

- dataset manifest and source hashes;
- split-group assignment;
- annotation revision;
- proposal model and configuration;
- deterministic engine revision;
- DRC policy version;
- article/material profiles;
- compiler/codec revision; and
- physical test protocol.

When DRC policy changes, run old and new policy versions side by side. Review
changed labels before adopting the new policy; do not mutate historical
experiment meaning in place.

### Iterative sew-out optimization

`tools/run_sewout_learning_loop.py` provides the bridge from deterministic
DRC to physical learning:

1. generate a baseline and several parameter candidates;
2. compile every candidate through the exact stitch engine and DRC policy;
3. render and retain every DST/plan/training-record tuple;
4. rank provisional candidates with an explicitly named virtual proxy;
5. refuse to recommend blocking candidates for physical sew-out;
6. collect structured physical ratings in `feedback.csv`;
7. fit a weighted quadratic ridge surrogate, giving physical scores six times
   the weight of virtual proxy rows; and
8. use prediction plus distance-based exploration to select the next batch.

The loop activates only controls that reach the compiled stitch stream for the
current design. It reports metadata-only controls and controls with no observed
effect under matched comparisons. This guards against a particularly harmful
failure mode: training a model to prefer settings that never changed the
manufactured output.

The virtual proxy is a search aid, not a sew-out label. It penalizes hard and
conditional blockers, review evidence, and trim/jump rates. Once physical
feedback exists, the experiment record identifies its target as
`physical_sewout`; until then it remains
`virtual_proxy_pending_physical_feedback`.

## Promotion gate

A model is eligible for production trial only when:

1. dataset rights and split-integrity checks pass;
2. semantic held-out metrics meet their pinned baseline;
3. deterministic DRC regression has no unapproved regressions;
4. no accepted target carries an unattributed blocking override;
5. per-rule rare-failure recall meets its declared threshold;
6. representative virtual sew-outs pass human review; and
7. the stratified physical sew-out set meets acceptance and defect limits.

Production deployment does not remove DRC. The exact same deterministic policy
continues to evaluate every generated plan.

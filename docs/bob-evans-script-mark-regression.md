# Bob Evans script-mark regression

Status: coached reference construction and automated proposal fixture
Production reference width: `110 mm`
Expected height: approximately `21.5 mm`

## Why this fixture matters

This one-color mark is handwriting, not a red silhouette. It contains curved
strokes, closed counters, sharp turns, junctions, overlaps, and a very small
registered-mark symbol.

The desired result should appear written in thread. Local satin direction and
sheen must reinforce the motion of the lettering.

The JPEG is visual evidence only. Foreground cropping finds a content box of
approximately `490 x 97 px` inside the `500 x 500 px` image. At `110 mm` wide,
the content is approximately `21.5 mm` high.

## Construction target

- Reconstruct the primary lettering as a calligraphic stroke graph.
- Give each primary stroke a centerline and two independently editable rails.
- Keep satin penetrations approximately normal to local stroke direction.
- Split at junctions, tight turns, overlaps, acute points, and unstable rail
  correspondence.
- Preserve letter counters as open background.
- Use tapered overlap rather than butting adjacent columns.
- Keep hidden travel inside geometry covered later.
- Use a trim rather than crossing an open counter or inter-word whitespace.
- Treat the registered symbol as simplified running stitch sewn last.
- Do not generate a global tatami silhouette for the script.
- Generate object underlay immediately before the corresponding top satin.

## Initial physical parameters

| Parameter | Initial value |
| --- | ---: |
| Logo width | `105–120 mm` |
| Height at 110 mm | approximately `21.5 mm` |
| Satin spacing | `0.40–0.44 mm` |
| Running stitch | `1.8–2.4 mm` |
| Pull compensation | `0.20–0.35 mm` per side |
| Join overlap | `0.3–0.6 mm` |

The automated baseline uses:

- `0.42 mm` satin spacing;
- `0.25 mm` pull compensation per side; and
- `0.4 mm` join overlap.

These are proposal values pending material-specific sew-outs.

## Object decomposition

Object count follows rail continuity, not letter count. The coached estimate
is roughly 20–28 primary satin objects plus the registered symbol.

Expected subdivisions include:

- capital B: stem, entrance/swash, upper bowl, lower bowl, and exit;
- o: split closed satin ring with protected counter;
- lowercase b: ascender, bowl, and connections;
- capital E: entrance, upper loop, crossing, lower loop, and exit;
- v: two legs split at the acute point;
- a: bowl, stem, and exit;
- n: entry/downstroke, arch, and descending exit;
- s: upper and lower curvature objects; and
- registered symbol: running circle and simplified running R.

The automatic vector proposal is allowed to contain additional editable
segments around uncertain junctions. Reducing object count must not merge
rails through a branch or destroy authored overlap.

## Underlay policy

| Column condition | Initial foundation |
| --- | --- |
| About `1–2.2 mm` | center walk |
| About `2.2–3.5 mm` | center walk or inset edge walk |
| About `3.5–5.5 mm` | inset edge walk plus light zig-zag |
| Tight loop or small counter | reduced center/edge support |
| Tiny connector | usually none |

The current vector planner uses center walk below `3.5 mm` and inset edge
walk plus zig-zag at or above `3.5 mm`. Running registered-mark objects have no
conventional satin underlay.

## Sequence constraints

For a flat product:

1. sew Bob according to internal overlap;
2. trim or make a controlled move across the word gap;
3. sew Evans according to internal overlap;
4. sew the registered symbol last; and
5. tie off and trim.

For a structured cap, the approximately `4 mm` inter-word whitespace is a
candidate center-seam position. A candidate route begins near that gap, works
through Evans toward the right, returns safely to center, then works through
Bob toward the left. Product-specific frame direction and bottom-up
constraints remain authoritative.

The present automatic proposal does not yet recover this semantic word and
overlap order. Its output remains editable and must not be described as
production-approved.

## Blocking DRC

- counter predicted to close after compensation;
- satin rails cross, reverse, or lose correspondence;
- column exceeds the selected profile's satin width;
- column falls below producible satin width;
- hidden travel crosses a counter or visible whitespace;
- acute point clusters repeated penetrations;
- sequence violates an approved overlap;
- registered symbol cannot survive at requested size; and
- global support duplicates object underlay.

## Warning DRC

- high penetration concentration on an inside curve;
- limited counter clearance;
- excessive join buildup;
- broad capital stroke approaching split satin;
- multiple objects terminating at one coordinate;
- abrupt angle progression;
- underlay intruding into a counter; and
- scalloped or noisy rails derived from JPEG artifacts.

## Rejection criteria

Reject the sew-out if:

- counters pinch or close;
- the hand-lettered rhythm is lost;
- satin sheen changes without structural reason;
- loops form dense knots;
- joins expose gaps or raised lumps;
- edges become scalloped or sawtoothed;
- the final s becomes unreadable;
- the long logo puckers;
- or the registered symbol becomes an indistinct knot.

Machine validity alone is not acceptance.

## Current automated regression

The regression checks that:

- foreground, rather than the white JPEG canvas, determines physical size;
- a 110 mm proposal is approximately 21.5 mm high;
- the script path contains at least 20 editable satin objects;
- no primary object falls back to tatami;
- curved rails exist rather than only horizontal/vertical spans;
- pull compensation and join overlap remain explicit object fields;
- registered-mark objects use running stitch without satin underlay;
- no global foundation is emitted; and
- every satin object's underlay occurs immediately before its top stitches.

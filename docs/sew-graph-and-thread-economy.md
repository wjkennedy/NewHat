# Sew graph and thread economy

The editable artwork model and the machine plan are intentionally different.
Artwork describes appearance. The sew graph describes how thread can
physically traverse that artwork while respecting coverage, layer order,
stitch direction, and the places where travel would remain visible.

## Basic sewing constructs

Every enabled vector object is reduced to one of three constructs before DST
commands are generated:

| Construct | Authored geometry | Machine interpretation |
| --- | --- | --- |
| Line path | Open centerline | Running stitch course |
| Satin column | Centerline plus two paired rails | Underlay courses followed by alternating rail penetrations |
| Fill area | One or more closed perimeter loops | Edge support and alternating tatami courses |

Color is an attribute of a construct, not its geometry classifier. Multiple
colors may occupy overlapping physical layers. Their compositing order,
coverage relationship, and required thread changes remain explicit.

## Routing rules

The planner currently applies these rules:

1. Preserve compositing layer and physical-layer order.
2. Preserve explicitly locked object order.
3. Prefer the current thread color within an otherwise reorderable group.
4. Reverse running paths and satin columns when the opposite end is closer.
5. Jointly orient the underlay and top-stitch courses inside a satin column.
6. Sew a connecting running path when it remains inside satin or fill
   coverage and will be hidden by the completed object.
7. Use a trim when a connector would cross exposed negative space.
8. Treat a color change as a hard break in thread continuity.

A trim is not considered a failure when it protects a counter, opening, or
other visible negative space. Thread economy means minimizing unnecessary
motion without sacrificing the design.

## Planner output

`POST /api/vector/plan` returns:

- `constructs`: the normalized line, satin-column, and fill-area contracts;
- `orientations`: whether each reversible object was sewn backward;
- `sequence`: resolved object order;
- `neighbors`: nearby objects considered by the route;
- `thread_economy`: sewn thread distance, jump distance, jumps, trims,
  hidden connectors, covered underpath connectors, and the proportion of
  movement performed with the needle down;
- `operations`: the derived machine command stream.

These values are regenerated after every vector edit. They are diagnostic
outputs, not additional authored geometry.

## Next routing stages

The current implementation establishes the primitive and coverage graph. The
next stages should add:

- junction-aware splitting of satin columns;
- shared graph nodes at line and column intersections;
- a global route over same-color connected components;
- selectable entry and exit points for tatami areas;
- coverage-aware travel through later overlapping objects;
- cost terms for thread length, trim time, registration risk, and penetration
  accumulation;
- separate cap-routing constraints such as center-out sequencing.

Physical sew-outs remain required. A mathematically shorter route can still be
inferior when it concentrates penetrations, disturbs registration, or changes
the intended overlap order.

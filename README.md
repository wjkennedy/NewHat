# New Hat digitizer prototype

New Hat converts raster artwork into an inspectable embroidery stitch plan,
simulates the resulting machine path, and exports DST files. It is a
local development prototype intended for review and test stitching, not an
unattended production digitizer.

![maybe.png](maybe.png)

![screenshot.png](screenshot.png)
## Run the UI

The UI and API are served together from the `prototype` directory:

```sh
cd prototype
python3 server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser.

The UI supports:

- Importing PNG, JPEG, WebP, or SVG artwork.
- Changing width, palette size, rotation, mirroring, and left/centre/right
  placement on the cap preview.
- Editing a generated layer's display/thread colour and disabling any colour
  layer before previewing or exporting. Disabled layers are omitted from the
  generated DST sequence.
- Inspecting an existing DST file with the stitch simulator.
- Playing, stepping through, or expanding the stitch simulation.
- Adjusting displayed needle colours and names.
- Exporting the generated or imported operation sequence as DST.

When raster artwork is imported, the browser sends it to the local digitizing
endpoint and displays the returned object plan. The preflight panel reports
stitch counts, trims, movement, and stitch-length findings. Always review the
slow stitch simulation and run a sample on the intended material before
production.

The current UI uses the `standard` digitizing profile. Use the API to select a
material- or product-specific profile until a profile selector is added to the
UI.

## Digitizing API

All endpoints are local and accept JSON unless noted otherwise.

### Create a stitch plan

`POST /api/digitize`

```json
{
  "image": "data:image/png;base64,...",
  "width_mm": 72,
  "colors": 6,
  "profile": "structured_cap"
}
```

`image`, `width_mm`, and `colors` are required. `profile` is optional and
defaults to `standard`.

The response contains a semantic plan:

```json
{
  "version": "1.3",
  "dimensions_mm": {"width": 72.0, "height": 42.0},
  "profile": "structured_cap",
  "palette": ["#153d7a"],
  "regions": [],
  "operations": [],
  "drc": {}
}
```

`regions` identifies the selected stitch type and its semantic operations.
`operations` is the complete ordered machine sequence. `drc` reports final
sequence metrics including stitch-length limits, jump distance/count, trim
count, and local-density variance.

Available profiles:

| Profile | Intended use |
|---|---|
| `standard` | General wearable embroidery; 7 mm maximum stitch length. |
| `structured_cap` | Structured cap fronts; center-out routing and softer fill density. |
| `unstructured_cap` | Softer cap fronts; center-out routing with moderate density. |
| `leather` | Leather, PVC, and similarly puncture-sensitive materials. |
| `high_pile` | Towels, sherpa, fleece, and other pile fabrics requiring more coverage. |
| `non_wearable` | Decorative pieces where longer exposed stitches are acceptable. |

### Preview an operation sequence

`POST /api/preview.dst`

```json
{"operations": [{"kind": "stitch", "x": 0, "y": 0}]}
```

The server compiles the sequence to DST, immediately decodes it, and returns
the decoded operations for a true-format preview. Coordinates are millimetres.
Operation kinds are `stitch`, `jump`, `trim`, and `color`.

### Export DST

`POST /api/export.dst`

Uses the same `operations` request body as the preview endpoint and returns an
`application/octet-stream` DST download.

### Inspect DST

`POST /api/inspect.dst`

Send raw DST bytes with `Content-Type: application/octet-stream`. The response
is JSON containing the decoded operations used by the simulator.

## Python use

Use the digitizer directly when working within the repository:

```python
from prototype.digitizer import digitize

plan = digitize(
    open("mark.png", "rb").read(),
    width_mm=72,
    colors=6,
    profile="structured_cap",
)
print(plan.drc())
operations = plan.operations
```

The final criteria pass removes accidental short stitches and subdivides long
non-tie stitches according to the selected profile. It also chooses running,
satin, or fill treatment based on feature size, applies stitch-type-appropriate
underlay, adds a design-level centre-run foundation before object underlay, and
routes same-colour objects to reduce travel.

## Verification

Run the digitizer tests with both the repository root and prototype directory
on the Python module path:

```sh
PYTHONPATH="$PWD:$PWD/prototype" python3 -m unittest prototype/test_digitizer.py
```

The test suite includes DST encoding/decoding, contour and hole handling,
stitch criteria, profile selection, and a QR/pixel-art visual regression when
`zbarimg` is available.

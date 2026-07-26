# PES corpus index

## Scope

The external, read-only corpus at `/Volumes/Untitled/PES` has been indexed without copying or modifying source files.

| Asset type | Read successfully | Read failures |
| --- | ---: | ---: |
| PES | 13,565 | 1 |
| PDF | 1,068 | 17 |

The PDF failures are retained in `corpus-pdf-index/failures.json`; the affected files have PDF extensions but could not be parsed as PDFs.

## Generated artifacts

| Artifact | Purpose |
| --- | --- |
| `corpus-index/pes.json` | Per-design PES metadata, command counts, bounds, and thread records. |
| `corpus-index/pes.csv` | Flat tabular version for filtering and analysis. |
| `corpus-index/summary.json` | Corpus-level PES totals. |
| `corpus-pdf-index/pdfs.json` | Tutorial/catalog path, page count, and first-two-page text excerpt. |
| `*/failures.json` | Parse failures retained as corpus evidence. |

## PES baseline

The corpus contains 226,116,927 decoded stitch records. These are file-level reference statistics, not quality targets.

| Metric | Minimum | Median | 90th percentile | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Stitches | 92 | 12,363 | 37,500 | 127,588 |
| Jumps | 0 | 22 | 98 | 2,130 |
| Trims | 0 | 15 | 86 | 825 |
| Color changes | 0 | 7 | 26 | 91 |
| Color blocks | 1 | 8 | 27 | 92 |

The current New Hat generated fixture had 1,758 stitches, 432 jumps, and 404 trims. Against this corpus, that trim/jump density is a useful regression signal: the new digitizer must measure fragmentation by embroidery object, not by raster scanline.

## Tutorial material

The PDF corpus is particularly useful for construction-specific profiles and validation rules. Text classification finds substantial material around:

- lace (628 occurrences);
- tutorials (469);
- appliqué (278);
- satin (222);
- quilting (110);
- thread color (108);
- freestanding embroidery (43);
- redwork (15);
- couching (3).

For example, `Basic Guide to Stitching Lace.pdf` calls out matching bobbin/upper thread, a new needle, water-soluble stabilizer, and layering stabilizer for larger hoops. These are not first-pass cap-digitizer parameters, but they establish that a future material profile needs to carry construction and stabilizer requirements alongside stitch planning.

### Visual tutorial spot-check

Rendered-page inspection confirms that the tutorials contain more useful manufacturing intent than their filenames and extracted text alone convey.

- The lace guide is a concise material/setup sheet: matching upper and bobbin thread, a new needle, and two to three layers of water-soluble stabilizer are explicit requirements.
- `Applique Bouquet Tutorial.pdf` labels its sew sequence by role: placement stitch, tack-down stitch, running-stitch details, satin outlines, then decorative fills. It also supplies finished dimensions and stitch counts for each size.

This supports an explicit construction profile and operation-role taxonomy in `StitchPlan`; it is not evidence that a PES alone retains those roles. Tutorial pages should be linked as reference material, while any inferred object/role labels remain separately annotated with confidence and provenance.

## Intended use

This corpus should support three separate efforts:

1. **Codec and simulator regression:** open PES, preserve counts/extents/color blocks, compile equivalent DST only where appropriate, and render decoded operations.
2. **Digitizer evaluation:** select a stratified fixture suite by stitch count, colors, trims, design family, and construction category; do not infer editable object intent from PES alone.
3. **Profile and training research:** link nearby tutorial PDFs to construction categories, then capture human annotations and physical stitch-out feedback before using samples as supervised digitization training data.

## Rebuild command

The indexer is read-only with respect to `/Volumes/Untitled/PES`:

```bash
find /Volumes/Untitled/PES -type f -iname '*.pes' -print \
  | python3 tools/index_pes_corpus.py /Volumes/Untitled/PES \
      --output corpus-index --pes-stdin --no-pdf
```

For PDF extraction, generate a temporary file list and use `--no-pes --pdf-list` so the expensive corpus walks remain separate.

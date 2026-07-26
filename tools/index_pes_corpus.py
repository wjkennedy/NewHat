#!/usr/bin/env python3
"""Build a read-only metadata index for an external PES/PDF corpus."""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pystitch" / "src"))
sys.path.insert(0, "/private/tmp/newhat-pdf")

from pystitch import read_pes  # noqa: E402
from pystitch.EmbConstant import COLOR_CHANGE, COMMAND_MASK, END, JUMP, STITCH, TRIM  # noqa: E402


def pes_record(path: Path, corpus: Path) -> dict:
    pattern = read_pes(io.BytesIO(path.read_bytes()))
    commands = Counter(command & COMMAND_MASK for _, _, command in pattern.stitches)
    return {
        "path": str(path.relative_to(corpus)),
        "bytes": path.stat().st_size,
        "stitches": commands[STITCH],
        "jumps": commands[JUMP],
        "trims": commands[TRIM],
        "color_changes": commands[COLOR_CHANGE],
        "color_blocks": commands[COLOR_CHANGE] + (1 if commands[STITCH] else 0),
        "end_commands": commands[END],
        "bounds_01mm": list(pattern.bounds()),
        "threads": [str(thread) for thread in pattern.threadlist],
    }


def pdf_record(path: Path, corpus: Path) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:2])
    return {
        "path": str(path.relative_to(corpus)),
        "bytes": path.stat().st_size,
        "pages": len(reader.pages),
        "excerpt": " ".join(text.split())[:2000],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "corpus-index")
    parser.add_argument("--limit", type=int, default=0, help="Limit PES records for a smoke run")
    parser.add_argument("--pdf-limit", type=int, default=0, help="Limit PDF records for a smoke run")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF extraction")
    parser.add_argument("--no-pes", action="store_true", help="Skip PES extraction")
    parser.add_argument("--pes-list", type=Path, help="Newline-delimited absolute PES paths from find")
    parser.add_argument("--pes-stdin", action="store_true", help="Read newline-delimited absolute PES paths from stdin")
    parser.add_argument("--pdf-list", type=Path, help="Newline-delimited absolute PDF paths from find")
    args = parser.parse_args()
    corpus = args.corpus.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.pes_stdin:
        pes_files = [Path(line.strip()) for line in sys.stdin if line.strip()]
    elif args.pes_list:
        pes_files = [Path(line.strip()) for line in args.pes_list.read_text().splitlines() if line.strip()]
    else:
        pes_files = sorted(path for path in corpus.rglob("*") if path.suffix.lower() == ".pes")
    if args.no_pes:
        pes_files = []
    if args.limit:
        pes_files = pes_files[:args.limit]
    pes, failures = [], []
    for index, path in enumerate(pes_files, 1):
        try:
            pes.append(pes_record(path, corpus))
        except Exception as error:  # retain corrupt/unsupported corpus entries as evidence
            failures.append({"path": str(path.relative_to(corpus)), "error": str(error)})
        if index % 250 == 0:
            print(f"Indexed {index}/{len(pes_files)} PES files")
    pdf, pdf_failures = [], []
    if args.no_pdf:
        pdf_files = []
    elif args.pdf_list:
        pdf_files = [Path(line.strip()) for line in args.pdf_list.read_text().splitlines() if line.strip()]
    else:
        pdf_files = sorted({*corpus.rglob("*.pdf"), *corpus.rglob("*.PDF")})
    if args.pdf_limit:
        pdf_files = pdf_files[:args.pdf_limit]
    for index, path in enumerate(pdf_files, 1):
        try:
            pdf.append(pdf_record(path, corpus))
        except Exception as error:
            pdf_failures.append({"path": str(path.relative_to(corpus)), "error": str(error)})
        if index % 25 == 0:
            print(f"Indexed {index}/{len(pdf_files)} PDFs")
    (args.output / "pes.json").write_text(json.dumps(pes, indent=2))
    (args.output / "pdfs.json").write_text(json.dumps(pdf, indent=2))
    (args.output / "failures.json").write_text(json.dumps({"pes": failures, "pdf": pdf_failures}, indent=2))
    with (args.output / "pes.csv").open("w", newline="") as stream:
        fields = ["path", "bytes", "stitches", "jumps", "trims", "color_changes", "color_blocks", "bounds_01mm"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in pes:
            writer.writerow({field: record[field] for field in fields})
    summary = {
        "corpus": str(corpus),
        "pes_indexed": len(pes),
        "pes_failures": len(failures),
        "pdf_indexed": len(pdf),
        "pdf_failures": len(pdf_failures),
        "total_stitches": sum(record["stitches"] for record in pes),
        "median_stitches": sorted(record["stitches"] for record in pes)[len(pes) // 2] if pes else 0,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

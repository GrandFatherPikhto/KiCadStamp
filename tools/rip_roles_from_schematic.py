#!/usr/bin/env python3
"""Rip every Role/Cluster out of the SCHEMATIC hierarchy, keyed by refdes.

Why the schematic and not the board: the board's own Role/Cluster fields can be
empty while the tagging is real — measured 22.09.2026 on ControllerESP32, where
`F1: Role=null, Cluster=null` over IPC although the cluster was fully tagged.
The schematic is the source that always has it.

Input, in order of convenience:
  --config <profile dir or config.sexp>   reads (root_sheet ...) out of it   [default]
  --root-sheet <file.kicad_sch>           the hierarchy entry point, explicit

The PROJECT file (.kicad_pro) is NOT an input: it carries settings and netclasses,
not the sheet hierarchy. The hierarchy hangs off the ROOT .kicad_sch through its
(property "Sheetfile" ...) nodes, which is exactly what walk_schematic_hierarchy
follows.

Read-only: opens the schematic files and writes nothing (rule: profiles/ and the
KiCad directory are live data).

Sibling, deliberately NOT the same job: tools/probe_cluster_tags.py audits the
LIVE BOARD's Cluster fields against what the config declares, through the
placement machinery. This one reads the SCHEMATIC instead, which is the source
that still has the tagging when the board's own fields come back empty.

One row per INSTANCE, not per symbol: a sheet reused twice gives the same symbol
block two refdes on two different sheet paths, and each carries its own fields.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME  # noqa: E402
from kicadstamp.schematic_blocks import (find_property_value_span,  # noqa: E402
                                         unescape_sexp_string)
from kicadstamp.schematic_discovery import load_schematic_tree  # noqa: E402

_ROOT_SHEET = re.compile(r'\(root_sheet\s+"([^"]+)"\)')


def root_sheet_from_config(where: Path) -> Path:
    cfg = where / "config.sexp" if where.is_dir() else where
    m = _ROOT_SHEET.search(cfg.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"no (root_sheet ...) in {cfg}")
    # the stored path is relative TO THE CONFIG's directory
    return (cfg.parent / m.group(1)).resolve()


def field_of(span_text: str, name: str):
    span = find_property_value_span(span_text, name)
    if span is None:
        return None
    value = unescape_sexp_string(span_text[span[0]:span[1]])
    return value or None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="profile directory or config.sexp")
    ap.add_argument("--root-sheet", help="root .kicad_sch (overrides --config)")
    ap.add_argument("--cluster", help="only this Cluster")
    ap.add_argument("--csv", help="also write the rows here")
    ap.add_argument("--json", dest="json_out", help="also write the rows here")
    args = ap.parse_args(argv)

    if args.root_sheet:
        root = Path(args.root_sheet).resolve()
    elif args.config:
        root = root_sheet_from_config(Path(args.config).resolve())
    else:
        ap.error("give --config or --root-sheet")
    if not root.exists():
        raise SystemExit(f"root sheet not found: {root}")

    files, texts, blocks = load_schematic_tree(str(root))

    rows = []
    for b in blocks:
        span_text = texts[b.file][b.start:b.end]
        role = field_of(span_text, ROLE_FIELD_NAME)
        cluster = field_of(span_text, CLUSTER_FIELD_NAME)
        # instances carry the per-path refdes; fall back to the flat refs set
        pairs = b.instances or tuple((("",), r) for r in sorted(b.refs))
        for path_uuids, refdes in pairs:
            rows.append({"ref": refdes, "role": role, "cluster": cluster,
                         "sheet_path": "/".join(path_uuids),
                         "file": str(Path(b.file).name)})

    rows.sort(key=lambda r: (r["cluster"] or "~", r["role"] or "~", r["ref"]))
    total_instances = len(rows)
    if args.cluster:
        rows = [r for r in rows if r["cluster"] == args.cluster]

    print(f"root sheet : {root}")
    print(f"sheets     : {len(files)}")
    # The counts above the filter are the WHOLE hierarchy; saying "instances: 4"
    # next to "symbols: 129" would read as a parse failure, not as a filter.
    print(f"symbols    : {len(blocks)}   instances: {total_instances}"
          + (f"   (filtered to Cluster={args.cluster!r}: {len(rows)})"
             if args.cluster else ""))
    tagged = [r for r in rows if r["role"] or r["cluster"]]
    print(f"tagged     : {len(tagged)}   "
          f"clusters: {len({r['cluster'] for r in tagged if r['cluster']})}\n")

    print(f"  {'ref':<8} {'Role':<22} {'Cluster':<24} sheet")
    for r in rows:
        if not (r["role"] or r["cluster"]):
            continue
        print(f"  {r['ref']:<8} {r['role'] or '-':<22} "
              f"{r['cluster'] or '-':<24} {r['file']}")

    untagged = [r["ref"] for r in rows if not (r["role"] or r["cluster"])]
    if untagged:
        print(f"\n  without Role AND Cluster ({len(untagged)}): "
              f"{', '.join(untagged[:20])}{' …' if len(untagged) > 20 else ''}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else
                               ["ref", "role", "cluster", "sheet_path", "file"])
            w.writeheader()
            w.writerows(rows)
        print(f"\ncsv  -> {args.csv}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print(f"json -> {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

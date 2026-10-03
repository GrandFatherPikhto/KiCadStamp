#!/usr/bin/env python3
# kicadstamp/diagnostics/probe_format3_equivalence.py
"""probe_format3_equivalence.py — У2.4 equivalence on COPIES of live profiles.

Read the plan §4: format 2, a minted format 3 and a minted format 3 whose hints
are all garbage must load to the SAME Config (identity fields — uuid/*_uuid and
the folders table — removed), so the loader's UUID normalization (У2.1) cannot
change a single non-identity value.

SAFETY: this probe touches ONLY ``profiles/*-copy/**``. Copy ONE working profile
beside itself, at the SAME depth (relative paths in the config depend on it), and
remove the copy when done — Syncthing carries it to the other machines:

    cp -r profiles/<name> profiles/<name>-copy
    .venv/bin/python kicadstamp/diagnostics/probe_format3_equivalence.py
    rm -rf profiles/<name>-copy

The mint/lie stubs are the TEST rig (tests/fakes/format3.py); the product never
imports them.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from kicadstamp.config import format_version  # noqa: E402
from kicadstamp.config.format_version import parse_raw_file  # noqa: E402
from kicadstamp.config.loader import _f3_files, load_config  # noqa: E402
from kicadstamp.config.sexp_format import dict_to_sexp  # noqa: E402
from kicadstamp.utils.file_cache import (  # noqa: E402
    invalidate_graph_path, invalidate_path)
from tests.fakes.format3 import lie_hints, mint_format3_files  # noqa: E402


def _strip(obj):
    """Drop the identity fields: every `uuid`/`*_uuid` and the `folders` table."""
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items()
                if k != "folders" and k != "uuid" and not k.endswith("_uuid")}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def _serialize(path: Path, data: dict) -> None:
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps({**data, "version": 3}, indent=2), encoding="utf-8")
    else:
        path.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")
    invalidate_path(path)
    invalidate_graph_path(path)
    format_version.invalidate_probe(path)


def _roots():
    for copy_dir in sorted(p for p in (_REPO / "profiles").glob("*-copy") if p.is_dir()):
        for name in ("config.sexp", "config.json"):
            if (copy_dir / name).exists():
                yield copy_dir / name


def _run_one(root: Path):
    """(before==after, before==after-lying, note) for ONE profile copy."""
    prev = format_version.CURRENT_FORMAT
    try:
        format_version.CURRENT_FORMAT = 2
        before = _strip(asdict(load_config(str(root))[0]))

        graph = [Path(f) for f in _f3_files(str(root))]
        for f in graph:
            if not str(f.resolve()).startswith(str(root.parent.resolve())):
                raise RuntimeError(f"graph file escapes the copy dir: {f}")
        raws = {str(f): parse_raw_file(f)[0] for f in graph}
        minted = mint_format3_files(raws)

        for path_str, data in minted.items():
            _serialize(Path(path_str), data)
        format_version.CURRENT_FORMAT = 3
        after = _strip(asdict(load_config(str(root))[0]))

        for path_str, data in minted.items():
            _serialize(Path(path_str), lie_hints(data))
        lying = _strip(asdict(load_config(str(root))[0]))

        return (before == after, before == lying, "")
    except Exception as exc:  # noqa: BLE001 — a diagnostic reports, never raises
        return (None, None, f"{type(exc).__name__}: {exc}".replace("\n", " ")[:200])
    finally:
        format_version.CURRENT_FORMAT = prev


def main() -> int:
    roots = list(_roots())
    print(f"# У2.4 format-3 equivalence probe — {len(roots)} *-copy profile(s)\n")
    if not roots:
        print("No profiles/*-copy/ found. Copy ONE working profile beside itself first:")
        print("  cp -r profiles/<name> profiles/<name>-copy")
        return 0
    print("| profile | before==after | before==after-lying | note |")
    print("|---|---|---|---|")
    for root in roots:
        a, l, note = _run_one(root)
        print(f"| {root.parent.name} | {a} | {l} | {note} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# kicadstamp/diagnostics/claude_probe_u34_vacuity_2026_10_04.py
"""Claude's acceptance of U3.4 part A (0b343c7), 2026-10-04: runs the Demon's
deepseek_probe_u34_equivalence_2026_10_04.py UNCHANGED, wrapped so its two
equalities cannot pass vacuously:

  * "before" == "after" — the probe strips every uuid/folders field and compares.
    It would also be equal if the format-3 open never happened (a cache hit, a
    lift that did not run). The wrapper records every load_config under
    CURRENT_FORMAT 3 and counts the records that carry a uuid in the result: 0
    there means the "after" side is not format 3 at all.
  * "UUIDs of the two copies match" — sorted uuid lists per file. Two EMPTY
    lists match too. The wrapper records the length of every list compared.

A file with NO records (the probe's own record count) has nothing to carry a
uuid: it is reported as "nothing to check", not as a finding. A finding is a
format-3 open whose records are not ALL uuid-stamped, or an empty uuid list for
a file that HAS records.

Copies, the boundary check and the cleanup are the probe's own (rule 28: copies
at the same depth, removed in `finally`; the originals are only copied).

Run from the repo root:
  .venv/bin/python kicadstamp/diagnostics/claude_probe_u34_vacuity_2026_10_04.py
"""
from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "u34_probe", REPO_ROOT / "kicadstamp/diagnostics/deepseek_probe_u34_equivalence_2026_10_04.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

from kicadstamp.config import format_version  # noqa: E402

# root path -> [(records, records with a uuid)] for every format-3 open
_F3_OPENS: dict[str, list[tuple[int, int]]] = defaultdict(list)
# file path -> [(len of the uuid list, records in the file)] per comparison
_UUID_LISTS: dict[str, list[tuple[int, int]]] = defaultdict(list)


def _count_uuids(obj, acc):
    """(records, with uuid): a record is any dict that has a 'uuid' key."""
    if isinstance(obj, dict):
        if "uuid" in obj:
            acc[0] += 1
            if obj["uuid"]:
                acc[1] += 1
        for v in obj.values():
            _count_uuids(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _count_uuids(v, acc)


_real_load = probe.load_config


def _load(path):
    result = _real_load(path)
    if format_version.CURRENT_FORMAT >= 3:
        acc = [0, 0]
        _count_uuids(asdict(result[0]), acc)
        _F3_OPENS[str(path)].append(tuple(acc))
    return result


_real_collect = probe._collect_uuids


def _collect(path):
    out = _real_collect(path)
    _UUID_LISTS[str(path)].append((len(out), probe._record_count(Path(path))))
    return out


probe.load_config = _load
probe._collect_uuids = _collect


def main() -> int:
    code = probe.main()
    print("\n=== Claude: vacuity of the probe's equalities ===")
    bad = 0
    print("format-3 opens (root: records / with uuid per open):")
    for root, opens in sorted(_F3_OPENS.items()):
        if all(r == 0 for r, _u in opens):
            flag = "  (no records - nothing to check)"
        elif all(r > 0 and u == r for r, u in opens):
            flag = ""
        else:
            flag = "  <<< VACUOUS/PARTIAL"
            bad += 1
        print(f"  {Path(root).relative_to(probe.PROFILES_DIR)}: {opens}{flag}")
    print("uuid lists compared across the two copies (file: (uuids, records)):")
    for f, lens in sorted(_UUID_LISTS.items()):
        if all(n > 0 for n, _r in lens):
            flag = ""
        elif all(r == 0 for _n, r in lens):
            flag = "  (no records - nothing to check)"
        else:
            flag = "  <<< EMPTY"
            bad += 1
        print(f"  {Path(f).relative_to(probe.PROFILES_DIR)}: {lens}{flag}")
    if not _F3_OPENS:
        print("  NO format-3 open was seen at all  <<< VACUOUS")
        bad += 1
    print(f"vacuity findings: {bad}")
    return code or (3 if bad else 0)


if __name__ == "__main__":
    raise SystemExit(main())

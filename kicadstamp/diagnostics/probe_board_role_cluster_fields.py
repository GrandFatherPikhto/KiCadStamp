#!/usr/bin/env python3
"""Stage 2 probe — can a role table write Role/Cluster straight onto the board?

2026-09-17, chat plan "role table by selection" (stage 2 of the spoke work).
The table would write through KiCadBoardAdapter.set_field_values_bulk, which
REFUSES a footprint that has no such field at all (it never creates one — see
design_2026_09_08_uuid_role_cluster_authoring §0.3, "needs confirmation"). This
probe gives that confirmation for the live board:

  * how many footprints carry a Role / Cluster field, how many have it EMPTY,
    how many lack it entirely (the table could not tag those);
  * how long reading both fields for the whole board takes (the table fills
    itself from these reads);
  * with --selection, the same verdict per selected footprint — "taggable" or
    "missing field X".

READ-ONLY: own KiCad socket, closed in `finally`; writes nothing.

Usage:
    python -m kicadstamp.diagnostics.probe_board_role_cluster_fields [--selection]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME   # noqa: E402
from kicadstamp.domain.board import Footprint                          # noqa: E402
from kicadstamp.kicad.adapter import KiCadBoardAdapter                 # noqa: E402

LIST_LIMIT = 40


def field_state(adapter, fp, name: str) -> str:
    """'value' | 'empty' | 'absent'."""
    if not adapter.has_field(fp, name):
        return "absent"
    return "value" if (adapter.get_field_value(fp, name) or "").strip() else "empty"


def listing(refs: list[str]) -> str:
    refs = sorted(refs, key=lambda r: (r.rstrip("0123456789"), len(r), r))
    head = ", ".join(refs[:LIST_LIMIT])
    return head + (f", … (+{len(refs) - LIST_LIMIT})" if len(refs) > LIST_LIMIT else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selection", action="store_true",
                        help="also give a per-footprint verdict for the selection")
    args = parser.parse_args()

    adapter = KiCadBoardAdapter()
    try:
        adapter.refresh_board()
        footprints = adapter.get_footprints()
        started = time.perf_counter()
        states = {fp.ref: (field_state(adapter, fp, ROLE_FIELD_NAME),
                           field_state(adapter, fp, CLUSTER_FIELD_NAME))
                  for fp in footprints}
        elapsed = time.perf_counter() - started
        print(f"footprints on the board: {len(footprints)}; reading Role+Cluster "
              f"state for all took {elapsed:.3f} s "
              f"({1000 * elapsed / max(len(footprints), 1):.2f} ms per footprint)")
        for index, name in enumerate((ROLE_FIELD_NAME, CLUSTER_FIELD_NAME)):
            by_state: dict[str, list[str]] = {"value": [], "empty": [], "absent": []}
            for ref, pair in states.items():
                by_state[pair[index]].append(ref)
            print(f"\n{name}: with a value {len(by_state['value'])}, "
                  f"empty {len(by_state['empty'])}, field ABSENT {len(by_state['absent'])}")
            if by_state["absent"]:
                print(f"  absent (a table could NOT tag these): {listing(by_state['absent'])}")
            if by_state["empty"]:
                print(f"  empty (taggable): {listing(by_state['empty'])}")

        if args.selection:
            selected = [i for i in adapter.get_selected_items() if isinstance(i, Footprint)]
            print(f"\nselection: {len(selected)} footprint(s)")
            for fp in sorted(selected, key=lambda f: f.ref):
                role, cluster = states.get(fp.ref, ("?", "?"))
                missing = [n for n, s in ((ROLE_FIELD_NAME, role), (CLUSTER_FIELD_NAME, cluster))
                           if s == "absent"]
                verdict = "taggable" if not missing else "missing field " + ", ".join(missing)
                print(f"  {fp.ref:8s} Role={adapter.get_field_value(fp, ROLE_FIELD_NAME)!r} "
                      f"Cluster={adapter.get_field_value(fp, CLUSTER_FIELD_NAME)!r}  -> {verdict}")
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

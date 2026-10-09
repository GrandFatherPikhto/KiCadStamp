# kicadstamp/diagnostics/deepseek_probe_enclosed_carve_2026_10_09.py
"""LIVE probe for the CARVE of «Select enclosed copper»
(plan ``plan_2026_10_09_enclosed_copper_carve.md``; Denis 2026-10-09).

READ-ONLY: it never calls ``select_items`` and never writes the board or a
profile. It reads the live board through its own adapter (the profile makes the
adapter see OUR Role/Cluster overrides) and reports, per instance:

  * the counters ``enclosed_copper`` now returns (``pieces`` / ``carved`` /
    ``foreign`` / ``dangling`` / ``pruned`` / copper ``items``);
  * the OLD behaviour for comparison — ``carved + foreign`` is how many pieces a
    piece-reaching-a-foreign-pad was DROPPED WHOLE under the pre-carve rule;
  * for every net named ``*DAC*_SPI_CS`` (the DAC chains the fix is about), the
    tracks of that net with a ``taken``/``NOT`` verdict by uuid prefix — the
    expectation for DAC1 is: the three tracks toward R41 are taken, the three
    toward IC3 are not.

Run with KiCad open on the board:

    .venv/bin/python kicadstamp/diagnostics/deepseek_probe_enclosed_carve_2026_10_09.py

``--config`` overrides the profile (default ``profiles/3ch-awg-tia-v103/config.sexp``);
``--dac-sheet`` repeats to pick the DAC_BUF instances (default ALL THREE channels).

Acceptance expectations by uuid prefix (plan_2026_10_09_enclosed_copper_carve,
live check 09.10; Д1 added the DAC2 case):

  * DAC1_SPI_CS — taken ``c64c5494`` / ``88193b64`` / ``aecebfea``;
    NOT taken ``ad7ca755`` / ``3eb0e0b1`` / ``f86b3167``;
  * DAC2_SPI_CS — taken ``36a66808`` / ``ecce6069`` / ``821679b5`` /
    ``7123cf6c`` / ``03b94286``; NOT taken ``c44f77e4`` / ``1af8f3a4`` /
    ``89041254`` (the 13 µm pad-entry stub);
  * DAC0_SPI_CS — taken ``cdb10b1e`` / ``0fef37d1`` / ``bc8289f7``;
    NOT taken ``b8956fd5`` / ``53b13050`` / ``79698328``.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.cell_instance import resolve_context_footprints
from kicadstamp.config import load_config
from kicadstamp.domain.board import Track, Via
from kicadstamp.enclosed_copper import enclosed_copper

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _ROOT / "profiles" / "3ch-awg-tia-v103" / "config.sexp"


def _short(uuid) -> str:
    return str(uuid)[:8] if uuid is not None else "?"


def _copper_uuids(result) -> set:
    return {getattr(i, "uuid", None) for i in result.items
            if isinstance(i, (Track, Via))}


def _report_instance(adapter, cluster, sheet, sheet_names, tracks) -> None:
    instance = resolve_context_footprints(
        adapter, adapter.get_footprints(), cluster, sheet, sheet_names)
    where = f"{cluster}/{'FPGA' if sheet is None else sheet}"
    if not instance:
        print(f"\n== {where}: NO live component for this instance ==")
        return
    t0 = time.perf_counter()
    result = enclosed_copper(adapter, instance)
    dt = time.perf_counter() - t0
    taken = _copper_uuids(result)
    before = result.carved + result.not_taken_foreign
    print(f"\n== {where}: {len(instance)} component(s) — {dt:.3f} s ==")
    print(f"   pieces taken    : {result.pieces}  (copper items: {len(taken)})")
    print(f"   carved          : {result.carved}")
    print(f"   foreign (dropped): {result.not_taken_foreign}")
    print(f"   dangling (1 pad): {result.not_taken_dangling}")
    print(f"   pruned          : {result.pruned}")
    print(f"   BEFORE (whole-drop rule): {before} piece(s) reached a foreign pad")
    nets = sorted({t.net_name for t in tracks
                   if t.net_name and "DAC" in t.net_name
                   and t.net_name.endswith("SPI_CS")})
    for net in nets:
        print(f"   net {net}:")
        for t in tracks:
            if t.net_name == net:
                verdict = "taken" if getattr(t, "uuid", None) in taken else "NOT  "
                print(f"     {verdict} {_short(getattr(t, 'uuid', None))}")


def main() -> int:
    parser = argparse.ArgumentParser(description="enclosed-copper carve probe (read-only)")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    parser.add_argument("--dac-sheet", action="append", default=None,
                        help="DAC_BUF sheet (repeatable); default all three "
                             "channels (Channel_0..2)")
    args = parser.parse_args()

    dac_sheets = args.dac_sheet or ["Channel_0", "Channel_1", "Channel_2"]
    print(f"profile: {args.config}")
    cfg, ctx = load_config(args.config)
    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    adapter = create_board_adapter(config_path=args.config)
    adapter.refresh_board()
    try:
        tracks = list(adapter.get_tracks() or [])
        instances = [("FPGA", None)] + [("DAC_BUF", s) for s in dac_sheets]
        for cluster, sheet in instances:
            _report_instance(adapter, cluster, sheet, sheet_names, tracks)
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

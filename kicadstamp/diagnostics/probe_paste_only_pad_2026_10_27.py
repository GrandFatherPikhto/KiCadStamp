# kicadstamp/diagnostics/probe_paste_only_pad_2026_10_27.py
"""probe_paste_only_pad_2026_10_27.py — «Select enclosed copper» with and without
the paste-only pad rule, on the LIVE board.

§5 of `plan_2026_10_27_paste_only_pad_is_not_copper`. The live defect (Denis, v103):
«Select enclosed copper» for `dac_buf_channel_0` selected the +3V3 In2 bus almost
whole, because `D14` (`Diode_SMD:D_SOD-882`) carries two PASTE-only pads and
`_pad_copper_layers` answered `None` («unknown = every copper layer») for them — the
paste window became a through-copper pad, the bus «touched two instance pads», the
piece was carved, and the bus was what was left.

BEFORE and AFTER are measured on the SAME board read, in the same process: AFTER is
the code as it now stands; BEFORE re-maps exactly those pads the old cut answered
`None` for (``copper_layers == ()`` -> ``None``), which is byte-for-byte the input
the old cut produced for them. One board, one read, two verdicts.

READ-ONLY: the board is only read, the profile is COPIED (``_copy_profile``
absolutizes its relative paths), ``select_items`` is never called, nothing is
written anywhere.

USAGE
    .venv/bin/python -m kicadstamp.diagnostics.probe_paste_only_pad_2026_10_27 \
        [--profile profiles/3ch-awg-tia-v103/config.sexp] \
        [--cell dac_buf] [--cluster DAC_BUF] [--sheet Channel_0] \
        [--piece c6ac3128] [--json out.json]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from kicadstamp.cell_instance import resolve_context_footprints
from kicadstamp.config.loader import load_config
from kicadstamp.config_working_set import set_active_graph_root
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.diagnostics.probe_reread_noise_2026_10_26 import _copy_profile
from kicadstamp.domain.board import Track, Via
from kicadstamp.enclosed_copper import enclosed_copper
from kicadstamp.explode_connectivity import has_copper

__all__ = ["LegacyPadAdapter", "summarise"]


class LegacyPadAdapter:
    """The SAME board, read with the OLD cut: a pad whose stack reported no copper
    (``copper_layers == ()`` now) is handed over as ``None`` — «unknown = every
    copper layer». Everything else is passed straight through."""

    def __init__(self, adapter):
        self._adapter = adapter
        self.converted = 0

    def __getattr__(self, name):
        return getattr(self._adapter, name)

    def get_footprint_pads(self, footprint):
        out = []
        for pad in self._adapter.get_footprint_pads(footprint):
            layers = getattr(pad, "copper_layers", None)
            if layers is not None and not layers:
                self.converted += 1
                pad = dataclasses.replace(pad, copper_layers=None)
            out.append(pad)
        return out


def _copper_items(result) -> list:
    return [it for it in result.items if isinstance(it, (Track, Via))]


def _net_counts(items) -> dict:
    counts: dict = {}
    for it in items:
        net = str(getattr(it, "net_name", None) or "(no net)")
        counts[net] = counts.get(net, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def summarise(result, label: str, piece: Optional[str]) -> dict:
    """The honest counters of ONE verdict, plus the net split of what it took."""
    copper = _copper_items(result)
    uuids = {str(getattr(it, "uuid", "")) for it in copper}
    return {
        "verdict": label,
        "pieces": result.pieces,
        "carved": result.carved,
        "not_taken_foreign": result.not_taken_foreign,
        "not_taken_dangling": result.not_taken_dangling,
        "pruned": result.pruned,
        "items": len(result.items),
        "copper_items": len(copper),
        "nets": _net_counts(copper),
        "piece_selected": (None if piece is None
                           else any(u.startswith(piece) for u in uuids)),
    }


def _retry(op: str, fn, *, attempts: int = 6, backoff_s: float = 5.0):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — «KiCad is busy» is a retry, not a fact
            last = exc
            print(f"  {op}: attempt {attempt}/{attempts} failed — {exc}",
                  file=sys.stderr)
            time.sleep(backoff_s)
    raise RuntimeError(f"{op} failed after {attempts} attempts: {last}")


def _print(summary: dict) -> None:
    nets = ", ".join(f"{net}: {n}" for net, n in summary["nets"].items()) or "—"
    piece = ("n/a" if summary["piece_selected"] is None
             else ("TAKEN" if summary["piece_selected"] else "not taken"))
    print(f"\n{summary['verdict']}")
    print(f"  pieces {summary['pieces']}, carved {summary['carved']}, "
          f"foreign {summary['not_taken_foreign']}, "
          f"dangling {summary['not_taken_dangling']}, pruned {summary['pruned']}")
    print(f"  items {summary['items']}, copper {summary['copper_items']} — {nets}")
    print(f"  the c6ac3128 bus: {piece}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--cell", default="dac_buf")
    parser.add_argument("--cluster", default="DAC_BUF")
    parser.add_argument("--sheet", default="Channel_0")
    parser.add_argument("--piece", default="c6ac3128",
                        help="uuid PREFIX of the bus the defect selected")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    from kicadstamp.adapter_factory import create_board_adapter

    out: dict[str, Any] = {"profile": args.profile, "cell": args.cell,
                           "cluster": args.cluster, "sheet": args.sheet}
    with tempfile.TemporaryDirectory(prefix="paste_only_") as tmp:
        copy_config = _copy_profile(Path(args.profile).resolve(), Path(tmp))
        set_active_graph_root(copy_config)
        cfg, ctx = load_config(str(copy_config))
        sheet_names = dict(getattr(ctx, "sheet_names", None) or {})

        adapter = _retry("connect", lambda: create_board_adapter(
            timeout_ms=DEFAULT_TIMEOUT_MS, config_path=str(copy_config)))
        try:
            _retry("refresh_board", adapter.refresh_board)
            fps = _retry("get_footprints", adapter.get_footprints)
            instance = resolve_context_footprints(
                adapter, fps, args.cluster, args.sheet, sheet_names)
            out["components"] = len(instance)
            if not instance:
                print("the instance is not on the board — nothing to measure")
                return 1

            paste_pads = 0
            for fp in fps:
                for pad in adapter.get_footprint_pads(fp):
                    if not has_copper(pad):
                        paste_pads += 1
            out["paste_only_pads_board"] = paste_pads

            # Which components of THIS instance carry such pads — the honest answer
            # to «did the defect have a runway here at all?»
            per_ref: dict = {}
            for fp in instance:
                n = sum(1 for pad in adapter.get_footprint_pads(fp)
                        if not has_copper(pad))
                if n:
                    per_ref[str(getattr(fp, "ref", "?"))] = n
            out["instance_paste_pads"] = per_ref
            print("instance components with PASTE-only pads: "
                  + (", ".join(f"{ref}: {n}" for ref, n in sorted(per_ref.items()))
                     or "none"))

            legacy = LegacyPadAdapter(adapter)
            before = summarise(enclosed_copper(legacy, instance), "BEFORE (old cut)",
                               args.piece)
            after = summarise(enclosed_copper(adapter, instance), "AFTER (no-copper)",
                              args.piece)
            out["converted_pads"] = legacy.converted
            out["before"] = before
            out["after"] = after

            _print(before)
            _print(after)
            print(f"\npaste-only pads on the board: {paste_pads}; "
                  f"pads the old cut read as THROUGH copper: {legacy.converted}")
            plus3v3 = [s for s in (before, after)
                       if any(net.startswith("+3V3") for net in s["nets"])]
            print("+3V3 copper selected: "
                  + ", ".join(f"{s['verdict']}: "
                              f"{sum(n for net, n in s['nets'].items() if net.startswith('+3V3'))}"
                              for s in (before, after)))
            if plus3v3 and not any(net.startswith("+3V3")
                                   for net in after["nets"]):
                print("=> AFTER takes no +3V3 copper at all — the defect is gone")
        finally:
            try:
                adapter.close()
            except Exception:  # noqa: BLE001 — a probe must not fail on close
                pass

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        print(f"json: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

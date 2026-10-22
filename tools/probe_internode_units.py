"""tools/probe_internode_units.py — read-only: how the inter-node classifier sees ONE tree's copper.

Runs the SAME classifier "Select inter-node copper" and the re-read use (internode_capture._classify_area)
on the whole OPEN board and prints every copper unit that touches at least one part of the tree's nodes:
its verdict, its pads (ref:pad -> node cluster/sheet, or FOREIGN), its nets and its size. This answers
"why was this copper taken / not taken" without guessing. Nothing is written; the profile is loaded
from a temporary copy exactly as tools/check_placement.py does it.

usage: .venv/bin/python tools/probe_internode_units.py <profile dir | profile .sexp> <tree name>
"""
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_placement import _config_name, _load_copy                 # noqa: E402
from kicadstamp.adapter_factory import create_board_adapter          # noqa: E402
from kicadstamp.constants import DEFAULT_TIMEOUT_MS                  # noqa: E402
from kicadstamp.internode_capture import _classify_area              # noqa: E402
from kicadstamp.internode_copper import classify_unit, find_copper_units   # noqa: E402


def _net(item) -> str:
    return getattr(item, "net_name", None) or "-"


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    arg = Path(argv[1]).resolve()
    profile, cfg_name = (arg.parent, arg.name) if arg.is_file() else (arg, _config_name(arg))
    tmp, cfg_file, cfg, ctx = _load_copy(profile, cfg_name)
    try:
        tree = next((t for t in cfg.trees if t.name == argv[2]), None)
        if tree is None:
            print(f"no tree {argv[2]!r}; trees: {', '.join(t.name for t in cfg.trees)}")
            return 2
        sheet_names = dict((ctx.sheet_names if ctx else {}) or {})
        adapter = create_board_adapter(timeout_ms=DEFAULT_TIMEOUT_MS, config_path=str(cfg_file))
        try:
            adapter.refresh_board()
            footprints = list(adapter.get_footprints())
            items = list(adapter.get_tracks()) + list(adapter.get_vias())
            area = _classify_area(adapter, cfg, tree, area_items=items,
                                  area_footprints=footprints, sheet_names=sheet_names)
            print(f"board: {len(footprints)} footprints, {len(items)} tracks+vias")
            print(f"tree {tree.name!r} nodes: {', '.join(area.node_keys)}")
            print(f"matched components: {area.matched_components}, "
                  f"unmatched with a Cluster: {area.unmatched_components}")
            by_node = Counter(area.node_key_by_ref.values())
            for key, count in sorted(by_node.items(), key=str):
                print(f"  {key}: {count} part(s)")
            units, _warnings = find_copper_units(adapter, items, footprints=footprints)
            verdicts = Counter()
            for unit in units:
                if not any(p.ref in area.node_key_by_ref for p in unit.pads):
                    continue
                verdict = classify_unit(unit, area.node_key_by_ref)
                verdicts[verdict.value] += 1
                pads = ", ".join(
                    f"{p.ref}:{p.pad}->{'/'.join(str(x) for x in area.node_key_by_ref[p.ref])}"
                    if p.ref in area.node_key_by_ref else f"{p.ref}:{p.pad}->FOREIGN"
                    for p in unit.pads)
                nets = sorted({_net(i) for i in (*unit.tracks, *unit.vias)})
                print(f"[{verdict.value}] {len(unit.tracks)}t {len(unit.vias)}v "
                      f"nets {', '.join(nets)} | {pads}")
            print(f"units touching the tree: {dict(verdicts)}")
            print(f"taken by the classifier: {len(area.units)}")
            for warning in area.warnings:
                print(f"warning: {warning}")
        finally:
            adapter.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

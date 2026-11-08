# kicadstamp/diagnostics/probe_internode_place_2026_11_08.py
"""LIVE cell of plan_2026_11_08_internode_cell_copper_by_place — READ-ONLY.

What it answers, on the WHOLE board (never on a selection: «Select inter-node copper» always takes
the whole board, so a probe that waited for a selection measured the wrong thing — Denis, 08.11),
all three columns on ONE AND THE SAME board snapshot:

  keys  — the pre-08.11 behaviour: the registry key is the only proof (no place pass at all);
  old   — commit 9ac3db53: the place pass asks only the slots with NO key and never looks at the
          layer. Rebuilt HERE in memory (a measurement of the past, not a second product rule);
  new   — §2 of the acceptance: the place pass also asks a slot whose KEY points elsewhere, and
          «at the place» means XY AND the layer.

The measure is PIECES LEFT IN THE UNITS, not units: cutting cell copper rarely empties a unit, it
empties it of pieces — «83 куска → 64» is a piece count. Every piece the NEW rule removes and the
OLD one did not (and the other way round) is printed by uuid, net and coordinates, so a wrong number
is named piece by piece, never bent to fit an expectation.

Safety, in this order:
  1. the PROFILE IS COPIED and the probe runs on the copy (`profiles/<name>-copy`, the SAME depth —
     the config's relative paths must keep resolving). The copy is removed in `finally`;
  2. a STOP-CHECK BEFORE the first board read: the profile's `root_sheet` (resolved) against the
     board KiCad has OPEN. A mismatch aborts with nothing read (Denis's rule: `-test` in a name is
     not a sandbox — resolve the path and look at it);
  3. nothing is written: no config, no registry, no board. The «before» columns are produced by
     patching the rule IN MEMORY, never by a code edit, and every patch is reverted in `finally`.

Run:
  .venv/bin/python -m kicadstamp.diagnostics.probe_internode_place_2026_11_08 \\
      --config profiles/<profile>/<name>.sexp --tree switch_a
"""
from __future__ import annotations

import argparse
import contextlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _open_board_name(adapter) -> str | None:
    """The board KiCad has OPEN right now — `None` when KiCad answers nothing.

    `read_open_board_name` takes the CLIENTS' `Kicad` object (one `GetOpenDocuments`
    round trip, no board read), so it is asked BEFORE `refresh_board()`. The first cut of
    this probe called it with NO argument, the TypeError was swallowed as "no answer", and
    the stop-check silently did nothing — the very shape of a guard that reads as present.
    """
    try:
        from kicadstamp.kicad.kipy_board_identity import read_open_board_name
    except Exception:  # noqa: BLE001 — a probe must be able to say "no answer"
        return None
    try:
        return read_open_board_name(adapter._kicad)
    except Exception as exc:  # noqa: BLE001
        print(f"board name: no answer ({exc.__class__.__name__}: {exc})")
        return None


def _copy_profile(config_path: Path) -> Path:
    """`profiles/<name>-copy` — SAME depth as the original (rule 34: the config's paths are
    relative, and a copy in /tmp breaks them). Removed by the caller."""
    src_dir = config_path.parent
    dst_dir = src_dir.parent / f"{src_dir.name}-copy"
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)
    return dst_dir / config_path.name


@contextlib.contextmanager
def _rule(mode: str):
    """Make ONE of the three rules current for the block, and put everything back.

    `keys` — no place pass at all; `old` — the 9ac3db53 rule (keyless slots only, no layer); `new`
    — nothing touched. The «old» patch goes through `slots_asked`/`layer_agrees` because those are
    the two decisions §2 added: which slots to ask, and whether the layer is compared."""
    import kicadstamp.cell_copper_place as place
    import kicadstamp.internode_cell_copper as orchestrator

    if mode == "new":
        yield
        return
    if mode == "keys":
        real_pass = orchestrator.place_pass
        orchestrator.place_pass = lambda *a, **k: place.PlacePass()
        try:
            yield
        finally:
            orchestrator.place_pass = real_pass
        return
    real_asked, real_layer = place.slots_asked, place.layer_agrees

    def keyless_only(cell, slots, keyed, live_by_uuid, frame, tol_mm):
        keyless = set(slots) - set(keyed)
        return keyless, keyless

    place.slots_asked = keyless_only
    place.layer_agrees = lambda *a, **k: True
    try:
        yield
    finally:
        place.slots_asked, place.layer_agrees = real_asked, real_layer


def _column(adapter, cfg, tree, config_path: str, items, footprints, sheet_names, snapshot,
            mode: str) -> dict:
    """One rule's answer: the units kept, the PIECES left in them (and their uuids), the cell
    copper map and its Log lines."""
    from kicadstamp.internode_capture import internode_units
    from kicadstamp.internode_cell_copper import tree_cell_copper

    with _rule(mode):
        units, _discarded, warnings = internode_units(
            adapter, cfg, tree, config_path=config_path, area_items=items,
            area_footprints=footprints, sheet_names=sheet_names)
        mapping = tree_cell_copper(adapter, cfg, config_path, tree,
                                   sheet_names=sheet_names, live=snapshot)
    tracks = sum(len(u.tracks or ()) for u in units)
    vias = sum(len(u.vias or ()) for u in units)
    in_units = {getattr(piece, "uuid", None)
                for u in units
                for piece in list(u.tracks or ()) + list(u.vias or ())}
    return {"units": len(units), "pieces": tracks + vias, "tracks": tracks, "vias": vias,
            "in_units": in_units, "map": mapping, "warnings": list(warnings)}


def _index(snapshot: dict) -> dict:
    return {getattr(item, "uuid", None): item
            for kind in ("via", "track") for item in (snapshot.get(kind) or ())}


def _describe(by_uuid: dict, uuid: str) -> str:
    item = by_uuid.get(uuid)
    if item is None:
        return f"{uuid}: NOT ON THE BOARD (a stale registry key?)"
    net = getattr(item, "net_name", None)
    if hasattr(item, "position"):
        where = (f"via at ({item.position.x / 1_000_000:.3f}, "
                 f"{item.position.y / 1_000_000:.3f})")
    else:
        where = (f"track ({item.start.x / 1_000_000:.3f}, {item.start.y / 1_000_000:.3f})"
                 f"–({item.end.x / 1_000_000:.3f}, {item.end.y / 1_000_000:.3f})")
    layer = getattr(item, "layer", None)
    return f"{uuid}: net {net!r}, {where}, layer {getattr(layer, 'name', layer)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True,
                        help="the profile's config file (NEVER written: the probe copies it)")
    parser.add_argument("--tree", default=None,
                        help="tree name to classify (default: the config's first tree)")
    parser.add_argument("--timeout-ms", type=int, default=None)
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"no such config: {config_path}")
        return 2

    from kicadstamp.config import load_config

    cfg, ctx = load_config(str(config_path))
    root_sheet = getattr(ctx, "root_sheet", None)
    expected = Path(root_sheet).name if root_sheet else None
    print(f"profile       : {config_path}")
    print(f"root_sheet    : {root_sheet}")

    tree = next((t for t in cfg.trees if args.tree in (None, t.name)), None)
    if tree is None:
        print(f"no tree {args.tree!r} in this config: {[t.name for t in cfg.trees]}")
        return 2

    copy_config = _copy_profile(config_path)
    try:
        from kicadstamp.adapter_factory import create_board_adapter

        adapter = create_board_adapter(args.timeout_ms, config_path=str(copy_config))
        # STOP-CHECK before the FIRST board read: the identity tick is one
        # `GetOpenDocuments` round trip, not a board read.
        open_name = _open_board_name(adapter)
        print(f"open board    : {open_name}")
        if expected and open_name and Path(open_name).stem != Path(expected).stem:
            print("STOP: the open board is NOT the profile's board — nothing is read.")
            return 3
        if open_name is None:
            print("WARNING: KiCad did not answer the board name — the stop-check could not "
                  "run. READ-ONLY probe: nothing is written either way.")
        adapter.refresh_board()
        sheet_names = dict(getattr(ctx, "sheet_names", None) or {})

        # The WHOLE board, always: the door this probe measures never narrows itself to a
        # selection (Denis, 08.11 — «Select всегда берёт всю плату»).
        from kicadstamp.internode_cell_copper import live_board_copper

        snapshot = live_board_copper(adapter)
        items = list(snapshot.get("track") or ()) + list(snapshot.get("via") or ())
        footprints = list(snapshot.get("footprint") or ())
        print(f"area          : the WHOLE board ({len(items)} copper piece(s), "
              f"{len(footprints)} footprint(s))")
        print(f"tree          : {tree.name}")

        columns = {}
        for mode in ("keys", "old", "new"):
            col = _column(adapter, cfg, tree, str(copy_config), items, footprints,
                          sheet_names, snapshot, mode)
            columns[mode] = col
            mapping = col["map"]
            print(f"{mode:5s}         : {col['units']} unit(s) kept, {col['pieces']} PIECE(s) "
                  f"in them ({col['tracks']} tracks + {col['vias']} vias); "
                  f"{len(mapping.uuids)} piece(s) given to cells "
                  f"({mapping.tracks} tracks + {mapping.vias} vias), "
                  f"by PLACE {mapping.by_place}, not given back {mapping.without_registry}")

        for mode in ("old", "new"):
            for line in columns[mode]["warnings"]:
                print(f"  log[{mode}]: {line}")

        by_uuid = _index(snapshot)

        def _report(title: str, ids) -> None:
            print(f"\n{title}: {len(ids)}")
            for uuid in sorted(ids):
                print(f"  {_describe(by_uuid, uuid)}")

        _report("pieces REMOVED from the units by the NEW rule, not by the OLD one",
                columns["old"]["in_units"] - columns["new"]["in_units"])
        _report("pieces the OLD rule removed and the NEW one gives back",
                columns["new"]["in_units"] - columns["old"]["in_units"])
        _report("cell copper given by the NEW rule, not by the OLD one (whole board)",
                columns["new"]["map"].uuids - columns["old"]["map"].uuids)

        print("\nper cell (old → new, pieces given):")
        old_cell = columns["old"]["map"].by_cell
        new_cell = columns["new"]["map"].by_cell
        for name in sorted(set(old_cell) | set(new_cell)):
            # `by_cell` is filled by the KEY pass and the PLACE pass alike.
            print(f"  {name}: {old_cell.get(name, 0)} → {new_cell.get(name, 0)}")

        print(f"\npieces kept: keys {columns['keys']['pieces']} → "
              f"old {columns['old']['pieces']} → new {columns['new']['pieces']}")
        if columns["new"]["pieces"] == columns["keys"]["pieces"]:
            print("NOTE: the place pass changes nothing on this board — read the Log lines "
                  "above before concluding anything (a non-rigid frame refuses every pair).")
    finally:
        shutil.rmtree(copy_config.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

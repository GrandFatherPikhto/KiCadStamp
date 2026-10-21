# kicadstamp/diagnostics/probe_2026_10_21_place_drift_lines.py
"""Т6 of plan_2026_10_20_cells_save_keeps_trees_cfg_fresh: what the PARTIAL
redraw would WARN about on the LIVE board — read-only, no movement.

The product question is "I select THESE nodes; what does the check print?". This
probe asks exactly that, once per candidate parent: for every tree node that is
plan-borne (`tree_plan_warnings.plan_pose_subject` — an Entity, or an internal
mount, whose children are laid from the TREE pose) AND has children, it calls
`gui.docks.tree_place_drift.place_drift_lines` with that node's children as the
run's names. The parent is therefore NOT in the run — which is the shape the
warning exists for.

WHAT TO READ IN THE OUTPUT

  * a line naming the SUBJECT — the parent itself, or, for an internal mount, the
    COMPONENT that places it (`COMP`, never the mount node). `stands N mm` is the
    DISTANCE between the tree pose and the live one, `turn +X.XX deg` the
    normalised turn;
  * `silence` for a parent that IS at its place. That is the CONTROL row: a run
    without one proves nothing (rule 39), and this probe prints it explicitly so
    the drift rows have something to be compared against;
  * `could not check ...` with the reason in WORDS when the pose cannot be read
    (a cluster that is not a rigid copy of its cell, a missing role/pad, a mount
    the classifier refuses) — never a made-up number.

WHY ONE PROBE AND NOT ONE NODE. The candidates are DISCOVERED, not hardcoded: a
node reported as shifted in the 20.10 measurement (`fpga_vcca_35` 3.0000 mm,
`mcu_boot0_mcu` 16.5990 mm) may well be a LEAF in the profile (measured on
21.10: both are), and a leaf can never be an ancestor of a redraw. The probe
names whatever the live forest actually offers.

NOTHING is written: no config, no board, no registry. The adapter is closed in a
finally. Run it against a COPY of the profile, OUTSIDE `profiles/` so Syncthing
does not pick it up (the deletion rides in the same command):

    d=$(mktemp -d); cp -r profiles/3ch-awg-tia-v103 "$d/p"
    .venv/bin/python -m kicadstamp.diagnostics.probe_2026_10_21_place_drift_lines \
        --config "$d/p/config.sexp" \
        --sheets-from profiles/3ch-awg-tia-v103/config.sexp \
        --out /tmp/place_drift_lines.txt
    rm -rf "$d"

WHY `--sheets-from`. The copy's `schematic_files` are relative four levels up
(rule 34), so inside `mktemp` they point at /KiCad/… and the {uuid: sheet-name}
map cannot be built. The SHEET MAP is what the product passes as
`ctx.sheet_names` (gui/docks/cascade.py), and without it a tree INSTANCE's
self-anchor cannot be narrowed — the probe would print "could not check" for
three `dac_buf` trees that the PRODUCT resolves fine. `--sheets-from` therefore
builds that map from the ORIGINAL profile's config: READ ONLY, the same reading
the GUI does; the original is never written. Without the flag the probe falls
back to the copy's own map, prints how it went, and says in the summary what
that costs.

KiCad must be RUNNING with the board open; without it there is no measurement.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.adapter_factory import create_board_adapter            # noqa: E402
from kicadstamp.config import load_config                              # noqa: E402
from kicadstamp.constants import DEFAULT_TIMEOUT_MS                    # noqa: E402
from kicadstamp.exceptions import ValidationError                      # noqa: E402
from kicadstamp.link_trees import link_trees                           # noqa: E402
from kicadstamp.tree_plan_warnings import plan_pose_subject            # noqa: E402

from gui.docks.tree_place_drift import place_drift_lines               # noqa: E402

_DEFAULT_CONFIG = Path("profiles/3ch-awg-tia-v103-copy/config.sexp")


def _candidates(linked_trees, cfg, only_tree):
    """[(tree, linked_node)] — every node whose children are laid from the PLAN
    and which HAS children. Nothing else can be the subject of the warning."""
    out = []
    for linked_tree in linked_trees:
        if only_tree is not None and linked_tree.name != only_tree:
            continue

        def walk(nodes):
            for node in nodes:
                if node.children:
                    try:
                        subject = plan_pose_subject(cfg, node, linked_tree.name)
                    except Exception as exc:  # noqa: BLE001 — a report, not a run
                        out.append((linked_tree, node, f"classifier refused: {exc}"))
                    else:
                        if subject is not None:
                            out.append((linked_tree, node, None))
                walk(node.children)

        walk(linked_tree.nodes)
    return out


def _sheet_names(ctx, source: Path | None) -> tuple[dict, str | None]:
    """({uuid: sheet name}, note) — the map the GUI's `ctx.sheet_names` carries.

    A copy in `mktemp` cannot build it (`schematic_files` are relative four
    levels up), so `--sheets-from` names the ORIGINAL profile whose map is built
    instead: READ ONLY, exactly the reading the GUI does. Never a silent {}: the
    caller prints the outcome, because an empty map is what turns a resolvable
    tree instance into "could not check"."""
    try:
        if source is not None:
            _cfg, src_ctx = load_config(source)
            return dict(src_ctx.sheet_names), None
        return dict(ctx.sheet_names), None
    except ValidationError as exc:
        reason = str(exc).splitlines()[0]
        return {}, f"sheet names unavailable ({reason})"


def _report(adapter, cfg, linked_trees, only_tree, sheet_names) -> tuple[list[str], dict]:
    """(all lines printed, per-kind counters)."""
    printed: list[str] = []
    counts = {"drift": 0, "could": 0, "silent": 0}
    for linked_tree, node, problem in _candidates(linked_trees, cfg, only_tree):
        header = (f"\n[{linked_tree.name}] parent {node.node.ref!r} "
                  f"({len(node.children)} child(ren): "
                  f"{', '.join(c.node.ref for c in node.children)})")
        if problem is not None:
            printed.append(header)
            printed.append(f"  ! not a candidate: {problem}")
            continue
        names = {child.node.ref for child in node.children}
        try:
            lines = place_drift_lines(adapter, cfg, [linked_tree], names,
                                      sheet_names)
        except Exception as exc:  # noqa: BLE001 — the probe must finish
            printed.append(header)
            printed.append(f"  ! the check raised: {exc}")
            continue
        printed.append(header)
        if not lines:
            counts["silent"] += 1
            printed.append("  silence (parent stands at its place — CONTROL row)")
            continue
        for line in lines:
            counts["drift" if "stands " in line else "could"] += 1
            printed.append(f"  WARNING: {line}")
    return printed, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG),
                        help="path to a COPY of a profile's config.sexp")
    parser.add_argument("--tree", default=None,
                        help="only this tree (default: every tree)")
    parser.add_argument("--sheets-from", default=None,
                        help="ORIGINAL profile config.sexp to build the "
                             "{uuid: sheet name} map from (read only) — needed "
                             "when --config is a copy outside profiles/")
    parser.add_argument("--out", default=None,
                        help="also write the report to this file")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"no such config: {config_path}")
        return 2
    if "profiles" in config_path.resolve().parts:
        print("refusing to run on a path INSIDE profiles/ — use a copy "
              "(rule 34: the original is never opened, not even for reading)")
        return 2

    cfg, ctx = load_config(config_path)
    sheet_names, sheet_note = _sheet_names(
        ctx, Path(args.sheets_from) if args.sheets_from else None)
    root_sheet = getattr(ctx, "root_sheet", None)
    header = [
        f"config: {config_path}",
        f"root_sheet: {root_sheet} "
        f"({'exists' if root_sheet and Path(root_sheet).is_file() else 'NOT resolved in the copy — see --sheets-from'})",
        f"sheet names: {len(sheet_names)}"
        + (f" (from {args.sheets_from})" if args.sheets_from else "")
        + (f" — {sheet_note}" if sheet_note else ""),
        f"trees: {len(getattr(cfg, 'trees', []) or [])}"
        + (f", only {args.tree!r}" if args.tree else ""),
    ]
    linked = link_trees(cfg, cfg.trees)
    adapter = create_board_adapter(timeout_ms=args.timeout_ms,
                                   config_path=str(config_path))
    try:
        adapter.refresh_board()
        body, counts = _report(adapter, cfg, linked, args.tree, sheet_names)
    finally:
        adapter.close()

    summary = (f"\nlines: {counts['drift']} DRIFT (a subject and its distance), "
               f"{counts['could']} 'could not check' (reason in words, no "
               f"number), {counts['silent']} silent rows (CONTROL: the parent "
               f"stands where the tree says it does). A silent row is NOT a "
               f"failed check, and a run with no silent row proves nothing "
               f"(rule 39).")
    text = "\n".join(header + body + [summary])
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

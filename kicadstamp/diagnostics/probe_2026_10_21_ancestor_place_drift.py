# kicadstamp/diagnostics/probe_2026_10_21_ancestor_place_drift.py
"""READ-ONLY recon for point 2 of plan_2026_10_20_cells_save_keeps_trees_cfg_fresh
(Ч1-0): how far does each tree node stand from ITS PLACE IN THE TREE?

WHY this exists. A partial curated redraw ("Redraw selected") does NOT move the
ancestors that are not selected, yet for a kind "placement" record (an Entity)
the pose the redraw uses as a base is not read from the board at all — it is
COMPUTED from the tree (tree_position.resolve_record_live_position ->
entity_placement.resolve_entity_live_position). So a child of an Entity that
stands 3 mm off its tree place is placed from the PLAN, while the Entity itself
stays where it stands. Point 2 adds a warning for exactly that; this probe
measures the numbers the warning will report, BEFORE any product code exists.

WHAT it prints, per tree node that the layout places:

  plan      the node's place in the tree (layout_tree_from_base from the tree's
            own LIVE anchor — the same call the redraw plans with);
  used      the pose the redraw READS for this node as a base
            (resolve_base_live_position / resolve_base_rotation_deg — for an
            Entity that is the plan, for every other kind it is the board);
  live      where the board says the node stands (read_record_live_pose with
            strict=True: a cluster that is not a rigid copy of its cell is
            REFUSED, never guessed — the honest_live_read decision of 20.10);
  d(mm) / d(deg)  live vs used, in WHOLE nanometres for the verdict;
  verdict   DRIFT / same place / same by construction / not checked (<reason>).

TWO VERDICTS THAT LOOK ALIKE AND ARE NOT:

  * "same place" — an INDEPENDENT live read agreed with `used`. This is the
    CONTROL SAMPLE: a run without a single "same place" row is a run whose
    numbers are not to be trusted (rule 39), and without it a figure like
    3.000 mm proves nothing.
  * "same by construction" — the "live" read is the SAME function that produced
    `used` (a component node's single footprint, a clone/coordinate record whose
    live pose is anchor+config, an Entity without a cell/cluster). Zero here is
    arithmetic, not a measurement, and it must never be counted as control.

COST. Every INDEPENDENT live read is timed; the median/max is printed so the
price of the check can be named next to the call in gui/docks/cascade.py.

NOTHING is written: no config, no board, no registry. The adapter is closed in a
finally. Run it against a COPY of the profile (rule 34):

    cp -r profiles/3ch-awg-tia-v103 profiles/3ch-awg-tia-v103-copy
    .venv/bin/python -m kicadstamp.diagnostics.probe_2026_10_21_ancestor_place_drift \
        --config profiles/3ch-awg-tia-v103-copy/config.sexp [--tree fpga] [--out report.txt]

KiCad must be RUNNING with the board open; without it there is no measurement
and no substitute with a fake board.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.anchor_graph import build_records
from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.component_address import resolve_component_footprint
from kicadstamp.config import load_config
from kicadstamp.constants import ANGLE_TOLERANCE_DEG, DEFAULT_TIMEOUT_MS
from kicadstamp.exceptions import NonRigidClusterError, ValidationError
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import (
    layout_tree_from_base,
    resolve_base_live_position,
    resolve_base_rotation_deg,
    tree_layout_base,
)
from kicadstamp.trees import _walk_nodes
from kicadstamp.utils.units import MM

# The profile the live fact was reported on; the -copy convention is rule 34.
_DEFAULT_CONFIG = Path("profiles/3ch-awg-tia-v103-copy/config.sexp")

_TOL_NM = int(RIGID_TOLERANCE_MM * MM)

# Verdicts — kept as plain strings so a report file reads without the source.
_DRIFT = "DRIFT"
_SAME = "same place"
_ARITHMETIC = "same by construction"
_UNCHECKED = "not checked"


def _fmt_pos(pos) -> str:
    return f"({pos.x / MM:+10.4f}, {pos.y / MM:+10.4f})"


def _fmt_deg(deg) -> str:
    return "     -" if deg is None else f"{deg:+8.3f}"


def _normalized_deg(delta: float) -> float:
    """Relative angle in (-180, 180] — the project's own normalisation."""
    return (delta + 180.0) % 360.0 - 180.0


class _Reading:
    """One node's numbers and the verdict they add up to.

    independent — the live pose came from a DIFFERENT source than `used` (a
    cluster frame), so its zero or non-zero is a measurement; False means the
    same call answered both sides and the delta is arithmetic."""

    __slots__ = ("ref", "kind", "plan", "used", "live", "drift_nm", "angle_deg",
                 "residual_mm", "verdict", "reason", "independent")

    def __init__(self, ref: str, kind: str):
        self.ref = ref
        self.kind = kind
        self.plan = None
        self.used = None            # (pos, deg) — what the redraw reads
        self.live = None            # (pos, deg) — what the board says
        self.drift_nm = 0
        self.angle_deg = None
        self.residual_mm = None
        self.verdict = _UNCHECKED
        self.reason = None
        self.independent = False


def _pose_of_record(adapter, cfg, ref, record, sheet_names):
    """(pos, rotation_deg) — the pose the REDRAW takes for this record as a base.
    For kind "placement" it IS the plan (the subject of the whole probe)."""
    pos = resolve_base_live_position(adapter, cfg, ref, record, {}, sheet_names)
    deg = resolve_base_rotation_deg(adapter, cfg, ref, record, sheet_names)
    return pos, deg


def _independent_live(adapter, cfg, record) -> bool:
    """Whether `read_record_live_pose` will read the board INDEPENDENTLY for this
    record instead of falling back to the very function that produced `used`.

    Only an Entity with a cell AND a cluster is read from its live cluster
    (live_position.read_record_live_pose, the placement branch). Everything else
    — a clone/coordinate/point record, or an Entity without a cell/cluster — is
    answered by resolve_base_* itself, so its delta can only be 0."""
    from gui.docks.live_position import entity_mount_fallback_reason

    if getattr(record, "kind", None) != "placement":
        return False
    return entity_mount_fallback_reason(cfg, record.obj) is None


def _read_one(adapter, cfg, node_by_ref, records, ref, sheet_names,
              timings: list[float], forest_nodes: dict | None = None) -> _Reading:
    """(reading) for ONE node name — never raises: a refused or unreadable live
    pose is a VERDICT, because the warning this probe prepares must invent no
    number and must not stop the run either.

    A name that is not a node of the tree being walked but IS a node of another
    tree is MODULE CONTENT: `layout_tree_from_base` lays an embedded tree's content
    inside this tree's map, and the redraw places it from the stage-2 layout (the
    recorded gap of point 2). It is named as such, never as "not found" — the
    live run of 20.10 printed 33 misleading rows this way."""
    from gui.docks.live_position import read_record_live_pose

    node = node_by_ref.get(ref)
    if node is None:
        other = (forest_nodes or {}).get(ref)
        reading = _Reading(ref, "embedded")
        reading.reason = (
            f"content of the embedded tree {other[0]!r} — modules are a recorded "
            f"gap of point 2 (stage-2 layout), nothing to compare here"
            if other else "node not found in any tree of this config")
        return reading
    record = None if node.kind == "component" else records.get(ref)
    kind = "component" if node.kind == "component" else (
        getattr(record, "kind", None) or node.kind)
    reading = _Reading(ref, kind)
    if record is None and node.kind != "component":
        # A point / external node places no config record of its own — asking the
        # board for a footprint under a tree-local name would only produce a
        # misleading refusal.
        reading.reason = (f"node of kind {node.kind!r} places no record — "
                          f"nothing to compare")
        return reading

    try:
        if record is None:
            fp = resolve_component_footprint(adapter, node, sheet_names)
            reading.used = (fp.position, float(fp.angle_deg))
            reading.live = reading.used           # one footprint, one call
            reading.verdict = _ARITHMETIC
            return reading
        reading.used = _pose_of_record(adapter, cfg, ref, record, sheet_names)
    except Exception as exc:  # noqa: BLE001 — a verdict, not a crash
        reading.reason = (f"the pose the redraw reads is unresolvable "
                          f"({type(exc).__name__}: {exc})")
        return reading

    reading.independent = _independent_live(adapter, cfg, record)

    started = time.perf_counter()
    try:
        live = read_record_live_pose(adapter, cfg, ref, record, sheet_names,
                                     strict=True)
    except NonRigidClusterError as exc:
        # The honest_live_read decision: a cluster that is not a rigid copy of
        # its cell refuses instead of guessing, so NO deviation number exists —
        # the residual (carried as data, never parsed out of the text) is all we
        # may report.
        reading.residual_mm = getattr(exc, "residual_mm", None)
        reading.reason = "the cluster is not a rigid copy of the cell"
        return reading
    except ValidationError as exc:
        reading.reason = str(exc)
        return reading
    except Exception as exc:  # noqa: BLE001 — a verdict, not a crash
        reading.reason = f"{type(exc).__name__}: {exc}"
        return reading
    finally:
        if reading.independent:
            timings.append((time.perf_counter() - started) * 1000.0)

    reading.live = (live.position, live.rotation_deg)
    used_pos, used_deg = reading.used
    dx_nm = abs(int(live.position.x) - int(used_pos.x))
    dy_nm = abs(int(live.position.y) - int(used_pos.y))
    reading.drift_nm = max(dx_nm, dy_nm)
    if live.rotation_deg is not None and used_deg is not None:
        reading.angle_deg = _normalized_deg(float(live.rotation_deg)
                                            - float(used_deg))
    turned = (reading.angle_deg is not None
              and abs(reading.angle_deg) > ANGLE_TOLERANCE_DEG)
    if reading.drift_nm > _TOL_NM or turned:
        reading.verdict = _DRIFT
    else:
        reading.verdict = _SAME if reading.independent else _ARITHMETIC
    return reading


def _row(reading: _Reading) -> str:
    plan = ("        -            " if reading.plan is None
            else _fmt_pos(reading.plan[0]))
    used = ("(unresolvable)   " if reading.used is None
            else _fmt_pos(reading.used[0]))
    live = ("(not read)       " if reading.live is None
            else _fmt_pos(reading.live[0]))
    drop_mm = "-" if reading.live is None else f"{reading.drift_nm / MM:8.4f}"
    drop_deg = "-" if reading.angle_deg is None else f"{reading.angle_deg:+7.2f}"
    residual = "-" if reading.residual_mm is None else f"{reading.residual_mm:.3f}"
    line = (f"  {reading.ref:30s} {reading.kind:11s} {plan} {used} {live} "
            f"{drop_mm} {drop_deg} {residual:>8s}  {reading.verdict}")
    return line if reading.reason is None else f"{line}  ({reading.reason})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG),
                        help="profile config.sexp — a COPY of the live profile")
    parser.add_argument("--tree", default=None,
                        help="one tree by name (default: every tree of the config)")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--out", default=None,
                        help="also write the whole report to this file")
    args = parser.parse_args(argv)

    out_lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        out_lines.append(text)

    config_path = str(args.config)
    emit("=== ancestor place drift probe (READ-ONLY: plan vs live) ===")
    emit(f"platform     : {sys.platform}")
    emit(f"cwd          : {Path.cwd()}")
    emit(f"config       : {config_path}")
    emit(f"tolerance    : {RIGID_TOLERANCE_MM} mm / {ANGLE_TOLERANCE_DEG} deg "
         f"(existing constants; comparison in whole nm: > {_TOL_NM} nm)")
    if not Path(config_path).exists():
        emit(f"FATAL: config not found: {config_path}")
        return 2

    cfg, ctx = load_config(config_path)
    sheet_names = dict((ctx.sheet_names if ctx else {}) or {})
    records = {rec.name: rec for rec in build_records(cfg)}
    # The forest the LAYOUT seam expects is a {tree name: plain Tree} DICT (a
    # module node looks its embedded tree up by name); link_trees is used only
    # to reject a forest link_trees itself would refuse, exactly like the dock.
    link_trees(cfg, cfg.trees)
    forest = {t.name: t for t in cfg.trees}

    trees = [t for t in cfg.trees if args.tree is None or t.name == args.tree]
    if not trees:
        emit(f"FATAL: tree {args.tree!r} not in {[t.name for t in cfg.trees]}")
        return 2
    emit(f"trees        : {[t.name for t in trees]}")

    adapter = create_board_adapter(timeout_ms=args.timeout_ms,
                                   config_path=config_path)
    try:
        try:
            adapter.refresh_board()
        except Exception as exc:  # noqa: BLE001 — no board, no measurement
            emit("FATAL: cannot read the live board "
                 f"({type(exc).__name__}: {exc}).")
            emit("KiCad must be RUNNING with the board open; without it this "
                 "probe is not done and must not be replaced with a fake board.")
            return 3
        try:
            emit(f"live board   : {adapter.get_board_filename()!r}")
        except Exception:  # noqa: BLE001 — identity is a nicety, not the point
            pass

        # {ref: (tree name, node)} over EVERY tree — only to tell "module content
        # of an embedded tree" (a recorded gap) from a genuinely unknown name.
        forest_nodes: dict[str, tuple[str, object]] = {}
        for t in cfg.trees:
            for n in _walk_nodes(t.nodes):
                forest_nodes.setdefault(n.ref, (t.name, n))

        timings: list[float] = []
        readings: list[_Reading] = []
        for tree in trees:
            emit("")
            emit(f"=== tree {tree.name!r} ===")
            try:
                base_pos, base_rot = tree_layout_base(adapter, cfg, tree,
                                                      sheet_names, forest)
                planned = layout_tree_from_base(
                    tree, base_pos, base_rot, forest,
                    adapter=adapter, cfg=cfg, sheet_names=sheet_names)
            except Exception as exc:  # noqa: BLE001 — a tree without a base
                emit(f"  tree skipped: base/layout unavailable "
                     f"({type(exc).__name__}: {exc})")
                continue
            emit(f"  layout base {_fmt_pos(base_pos)} rot={_fmt_deg(base_rot)}; "
                 f"{len(planned)} node(s) placed by the tree")
            emit("  ref                            kind        plan (mm)          "
                 "      used (mm)                 live (mm)             d(mm)   "
                 " d(deg) residual  verdict")
            node_by_ref = {n.ref: n for n in _walk_nodes(tree.nodes)}
            for ref, pose in planned.items():
                reading = _read_one(adapter, cfg, node_by_ref, records, ref,
                                    sheet_names, timings, forest_nodes)
                reading.plan = pose
                readings.append(reading)
                emit(_row(reading))

        emit("")
        emit("=== summary ===")
        measured = [r for r in readings if r.independent]
        same = [r for r in measured if r.verdict == _SAME]
        drifted = [r for r in readings if r.verdict == _DRIFT]
        arithmetic = [r for r in readings if r.verdict == _ARITHMETIC]
        unchecked = [r for r in readings if r.verdict == _UNCHECKED]
        emit(f"nodes read          : {len(readings)}")
        emit(f"independent reads   : {len(measured)}")
        emit(f"same place          : {len(same)}  "
             f"(CONTROL SAMPLE — a run with 0 of these is not a measurement)")
        emit(f"same by construction: {len(arithmetic)}  "
             f"(the same call answered both sides — NOT control)")
        emit(f"DRIFT               : {len(drifted)}")
        emit(f"not checked         : {len(unchecked)}")
        if timings:
            emit(f"live read cost      : median {statistics.median(timings):.1f} ms, "
                 f"max {max(timings):.1f} ms, n={len(timings)}")
        if drifted:
            worst = max(drifted, key=lambda r: r.drift_nm)
            emit("")
            emit(f"worst displaced node: {worst.ref!r} ({worst.kind}) — "
                 f"{worst.drift_nm / MM:.4f} mm"
                 + ("" if worst.angle_deg is None
                    else f", {worst.angle_deg:+.2f} deg")
                 + " between where the redraw takes it from and the board")
            emit("  THIS is the node the point-2 warning should fire on. If the "
                 "reported one (fpga_fpga) now reads 'same place', take the worst "
                 "row above and name it in the plan — never bend the measurement "
                 "to the old fact.")
        for r in unchecked:
            if r.reason:
                emit(f"  not checked {r.ref!r}: {r.reason}")
        emit("")
        emit("Read: 'plan' is the node's place in the tree, 'used' is what the "
             "redraw takes as a base (equal to the plan for an Entity), 'live' "
             "is the board. A partial redraw places the nodes under an ancestor "
             "from 'used'; a DRIFT row is exactly where that differs from where "
             "the ancestor stands.")

        if args.out:
            Path(args.out).write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            print(f"\nreport written: {args.out}")
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())

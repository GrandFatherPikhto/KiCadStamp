# tests/gui/docks/test_embedded_tree_stm32_snapshot.py
"""Cell «л» of plan_2026_11_15_embedded_tree_redraw_through_module — on the REAL 16.11 snapshot.

Denis's premise for the whole plan: on `.history/…_2026-11-16_23-40-51.sexp` the owner `main` sits
at `Origin` with NO shift and the marker `power` at (6, −64), while `power`'s own anchor is
`Origin` + (5, −60). A redraw started on the `power` tab therefore laid every node out
(−1.000, +4.000) mm away from where `main` puts it — measured live on 16.11 and the reason this
plan exists.

The snapshot is COPIED into `tmp_path` first (the profile stays read-only, and nothing ever runs
against the live board): `main`'s `fpga`/`mcu` markers are dropped from the copy for the layout
alone, because their mounts need the board to be placed (Ч0 of the plan says so explicitly).

What the cell pins: the OLD path is off by exactly (−1.000, +4.000), and the NEW path (the marker
chain this plan hands over) lands on `main`'s numbers — i.e. the second redraw changes nothing.
"""
from pathlib import Path

import pytest

from kicadstamp.config.loader import load_config
from kicadstamp.domain.geometry import Vector2
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import (curated_forest_module_content, layout_tree_from_anchor,
                                      layout_tree_from_base, tree_layout_base)

from gui.docks.trees_embedding import chain_selection

SNAPSHOT = (Path(__file__).resolve().parents[3] / "profiles" / "HiPiMS-Controller-STM32-v099"
            / ".history" / "HiPiMS-Controller-STM32-v099_2026-11-16_23-40-51.sexp")


class _Adapter:
    """Only the board ORIGIN is asked: every tree's anchor is the point `Origin`, and the shift
    of `main` is zero on this snapshot, so the origin's value cancels out of every comparison."""

    def get_board_origin(self, *_args, **_kwargs):
        return Vector2.from_xy(0, 0)


@pytest.fixture
def snapshot(tmp_path):
    if not SNAPSHOT.exists():
        pytest.skip(f"the 16.11 snapshot is not in this checkout ({SNAPSHOT})")
    copy = tmp_path / "snapshot.sexp"
    copy.write_text(SNAPSHOT.read_text(encoding="utf-8"), encoding="utf-8")
    cfg, ctx = load_config(str(copy))
    trees = [t for t in (cfg.trees or []) if t.name in ("main", "power")]
    main = next(t for t in trees if t.name == "main")
    # The layout half only: `fpga`/`mcu` mounts need the board (Ч0 of the plan).
    main.nodes = [n for n in main.nodes if n.kind != "module" or n.ref == "power"]
    cfg.trees = trees
    return cfg, ctx, trees


def _mm(pose):
    pos, _rot = pose
    return (pos.x / 1_000_000, pos.y / 1_000_000)


def test_l_the_16_11_snapshot_shows_the_defect_the_plan_removes(snapshot):
    """The OLD path, measured: `power`'s own anchor is (−1.000, +4.000) mm away from the place
    `main` gives the same nodes — the number Denis saw live on 16.11."""
    cfg, ctx, trees = snapshot
    by_name = {t.name: t for t in trees}
    power, main = by_name["power"], by_name["main"]

    base = tree_layout_base(_Adapter(), cfg, main, ctx.sheet_names, by_name)
    through_main = layout_tree_from_base(main, base[0], base[1], by_name,
                                         adapter=_Adapter(), cfg=cfg,
                                         sheet_names=ctx.sheet_names)
    own = layout_tree_from_anchor(power, by_name, adapter=_Adapter(), cfg=cfg,
                                  sheet_names=ctx.sheet_names)

    moved = 0
    for ref, pose in through_main.items():
        if ref not in own:
            continue
        dx = round(_mm(pose)[0] - _mm(own[ref])[0], 3)
        dy = round(_mm(pose)[1] - _mm(own[ref])[1], 3)
        assert (dx, dy) == (1.0, -4.0), f"{ref}: the snapshot's defect changed to {(dx, dy)}"
        moved += 1
    assert moved >= 4, "the snapshot must still hold power's four records"


def test_l_the_chain_is_what_fixes_it_and_the_second_redraw_changes_nothing(snapshot):
    """The NEW path: the tab hands over the chain (`{'power'}`), the planner takes `main` for a
    flow root, and the stage-2 layout equals the module's own Redraw — so running the tab's redraw
    and then the owner's redraw leaves every node where the first one put it."""
    cfg, ctx, trees = snapshot
    by_name = {t.name: t for t in trees}
    linked = link_trees(cfg, trees)

    assert chain_selection(trees, by_name["power"]) == {"power"}

    content_refs, flow_roots = curated_forest_module_content(linked, {"power"})
    assert flow_roots == ["main"], "the owner is the flow root, not the tree's own anchor"
    assert sorted(content_refs) == ["ldo_adj_p2v5_power", "ldo_p1v2_power", "ldo_p3v3_power",
                                    "pwr_5v_in_power"]

    main = by_name["main"]
    base = tree_layout_base(_Adapter(), cfg, main, ctx.sheet_names, by_name)
    first = layout_tree_from_base(main, base[0], base[1], by_name, adapter=_Adapter(), cfg=cfg,
                                  sheet_names=ctx.sheet_names)
    second = layout_tree_from_base(main, base[0], base[1], by_name, adapter=_Adapter(), cfg=cfg,
                                   sheet_names=ctx.sheet_names)
    for ref, pose in first.items():
        assert _mm(pose) == _mm(second[ref]), f"{ref} moved on the second pass"

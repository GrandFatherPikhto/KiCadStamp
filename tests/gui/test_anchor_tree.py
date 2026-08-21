#!/usr/bin/env python3
"""
Tests for AnchorTreeDock (gui/docks/anchor_tree.py) — the anchor dependency
tree (§1 of plan_2026_08_21_anchor_dependency_tree_cascade_redraw.md):
the same config records as ConfigTreeDock, regrouped by static anchor edges,
with Sheet folders (§1.1) and DAG duplication for multi-parent nodes (§1.2).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.docks.anchor_tree import AnchorTreeDock


def _children(item):
    return [item.child(i) for i in range(item.childCount())]


def _write(root: Path, yaml_text: str) -> Path:
    root.write_text(yaml_text, encoding="utf-8")
    return root


def test_fpga_example_renders_sheet_folders(main_window, tmp_path):
    """The plan's FPGA example: an external anchor_ref root with three
    same-role records under three Sheet folders, short names inside."""
    root = _write(tmp_path / "root.yaml", """
coordinate_placements:
  - name: Channel_0_DAC_BUF
    cluster: DAC_BUF
    role: DAC_BUF
    sheet: Channel_0
    anchor_ref: FPGA
    x_mm: 1.0
    y_mm: 0.0
    rotation_deg: 0.0
  - name: Channel_1_DAC_BUF
    cluster: DAC_BUF
    role: DAC_BUF
    sheet: Channel_1
    anchor_ref: FPGA
    x_mm: 2.0
    y_mm: 0.0
    rotation_deg: 0.0
  - name: Channel_2_DAC_BUF
    cluster: DAC_BUF
    role: DAC_BUF
    sheet: Channel_2
    anchor_ref: FPGA
    x_mm: 3.0
    y_mm: 0.0
    rotation_deg: 0.0
""")

    dock = AnchorTreeDock(main_window)
    dock.set_root_file(root)

    tops = _children(dock.tree.invisibleRootItem())
    assert len(tops) == 1
    assert tops[0].text(0).startswith("FPGA")

    folders = _children(tops[0])
    assert [f.text(0) for f in folders] == ["Channel_0", "Channel_1", "Channel_2"]
    for folder in folders:
        leaves = _children(folder)
        assert len(leaves) == 1
        assert leaves[0].text(0) == "DAC_BUF"  # "{Sheet}_" prefix stripped


def test_multi_parent_node_duplicated_under_each_parent(main_window, tmp_path):
    """§1.2: a record anchored on a role two producers both emit gets TWO
    parents, and is therefore duplicated under each of them in the tree."""
    root = _write(tmp_path / "root.yaml", """
cells:
  p_cell:
    components:
      - role: R
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
  c_cell:
    components:
      - role: C
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
clone_placements:
  - name: P1
    cell: p_cell
    xy: [0.0, 0.0]
  - name: P2
    cell: p_cell
    xy: [0.0, 0.0]
  - name: C
    cell: c_cell
    xy: [0.0, 0.0]
    anchor_role: R
""")

    dock = AnchorTreeDock(main_window)
    dock.set_root_file(root)

    tops = _children(dock.tree.invisibleRootItem())
    assert sorted(t.text(0) for t in tops) == ["P1", "P2"]
    for top in tops:
        leaves = _children(top)
        assert [leaf.text(0) for leaf in leaves] == ["C"]


def test_broken_anchor_shows_error_node(main_window, tmp_path):
    """anchor_role into nowhere is fatal at graph build — the dock renders
    the error as a single node instead of crashing."""
    root = _write(tmp_path / "root.yaml", """
coordinate_placements:
  - name: X
    cluster: C
    role: R
    anchor_role: NOWHERE
    x_mm: 1.0
    y_mm: 0.0
    rotation_deg: 0.0
""")

    dock = AnchorTreeDock(main_window)
    dock.set_root_file(root)

    tops = _children(dock.tree.invisibleRootItem())
    assert len(tops) == 1
    assert "NOWHERE" in tops[0].text(0)

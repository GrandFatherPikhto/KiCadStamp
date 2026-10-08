# tests/gui/docks/test_trees_dock_node_ref_row.py
"""The node form's Apply must update the tree ROW IN PLACE when the Ref (or the
Kind) changes — the defect reported by Denis on 08.10.2026
(techdocs/handoff/deepseek/plan/plan_2026_10_08_tree_node_ref_apply.md):
NodeFormWidget.apply() mutated the node in place and marked the dock dirty, but
`_node_items` stayed keyed by the OLD ref, so the row's text went stale until the
whole GUI was restarted.

The guard is the MASTER-DETAIL Node tab — the ONE production node-edit path. The
modal `_NodeDialog`'s Edit mode wraps the SAME form and carries the same fix,
but no production caller ever opens it for an existing node (`_prompt_node` is
wired to the three Add flows only, `existing=None`); it is exercised by tests,
not by the running app.

Red before the fix (the row keeps the old text and the old `_node_items` key);
green after.
"""
import pytest
from PyQt6.QtWidgets import QTreeWidget

from gui.docks.trees_dock import NodeFormWidget, TreesDock

from tests.fakes.format3 import det_uuid, format3  # noqa: F401 — pytest fixture
from tests.fakes.write_later import write_later


@pytest.fixture(autouse=True)
def _row_rig(tmp_path, monkeypatch):
    """The two rigs the node-form Apply write path needs, plus the format pin.

    У3.5 (class (в)): an Apply marks the dock dirty, which stages the trees:
    section through the config writer; under format 3 the writer stamp resolves
    references against the process-wide ACTIVE GRAPH ROOT, so it must be set
    (the same rig tests/gui/docks/test_trees_dock.py uses).

    CURRENT_FORMAT is pinned to 2 because the SUBJECT here is the ROW update,
    never the format: a Kind change to a record kind ("point") edits a node whose
    ref has no record in this throwaway config, and only the format-3 stamp would
    refuse that at write time. Under format 2 the write path is byte-for-byte the
    product's own (the same pin its format-independent cells use)."""
    from kicadstamp.config import format_version
    from kicadstamp.config_working_set import set_active_graph_root

    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


# One tree, two nodes: "A" carries a child (so expansion is meaningful) and "B"
# is a second, independent row. All kinds are "external" — a live-board refdes,
# never resolved against the config, so a minimal root config loads and an
# Apply needs no records, no board and no clone/points sections.
TREES = {
    # The kind-change cell below switches B to kind "point": a node of a
    # record-backed kind must name a record of ITS section (§п.3 of
    # plan_2026_10_08_tree_node_ref_apply), so the rig carries one.
    "points": {"B": {"anchor_ref": "IC1"}},
    "trees": [
        {"name": "t", "anchor": {"origin": True},
         "nodes": [
             {"ref": "A", "kind": "external", "xy": [1.0, 0.0],
              "children": [{"ref": "A_CHILD", "kind": "external",
                            "xy": [0.5, 0.0]}]},
             {"ref": "B", "kind": "external", "xy": [2.0, 0.0]},
         ]},
    ],
}


def _dock(main_window, tmp_path) -> TreesDock:
    from kicadstamp.config.sexp_format import dict_to_sexp

    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp(TREES, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config did not load"
    return dock


def _open_form(dock: TreesDock, ref: str):
    """Select the row `ref` on the master-detail page and return (widget,
    item, form). The item is the one the dock's registry carries."""
    widget = dock._tree_widget_of_page(dock.tree_tabs.widget(0))
    assert isinstance(widget, QTreeWidget)
    widget.expandAll()
    item = dock._node_items[ref]
    widget.setCurrentItem(item)
    form = dock._embedded_form_of(dock._panel_page(dock._active_form_panel()))
    assert isinstance(form, NodeFormWidget)
    return widget, item, form


def test_node_form_ref_change_updates_row_in_place(main_window, tmp_path,
                                                   monkeypatch):
    """Ref edit -> Apply: the SAME row moves to the new `_node_items` key, its
    text is the new ref, the old key is gone, the selection and the expansion
    survive, and the whole dock is NOT rebuilt (`_rebuild_tabs` would tear the
    open form down)."""
    dock = _dock(main_window, tmp_path)
    widget, item, form = _open_form(dock, "A")
    node = form._existing
    assert node.ref == "A"
    assert item.isExpanded() is True

    # The fix must be IN PLACE: a full rebuild is forbidden here (the plan's
    # item 2 keeps it as the last resort only).
    monkeypatch.setattr(dock, "_rebuild_tabs",
                        lambda: pytest.fail("the row update rebuilt every tab"))

    form.ref_combo.setCurrentText("A2")
    assert form.apply() is True

    assert node.ref == "A2"
    assert "A" not in dock._node_items
    assert dock._node_items["A2"] is item          # the SAME widget item
    assert item.text(0) == "A2 (external)"         # render's own text idiom
    assert widget.currentItem() is item            # selection kept
    assert item.isExpanded() is True               # expansion kept
    # The dock's own master-detail identity follows the node.
    assert dock._current_node_ref == "A2"


def test_node_form_kind_change_updates_row_text(main_window, tmp_path):
    """Kind edit (same ref) -> Apply: the `_node_items` key stays, but the row's
    text follows the new kind tag through the render's OWN text routine."""
    dock = _dock(main_window, tmp_path)
    _widget, item, form = _open_form(dock, "B")
    node = form._existing
    assert item.text(0) == "B (external)"

    idx = form.kind_combo.findData("point")
    assert idx >= 0, "the Kind combo must offer the 'point' kind"
    form.kind_combo.setCurrentIndex(idx)
    assert form.apply() is True

    assert node.kind == "point"
    assert dock._node_items["B"] is item
    assert item.text(0) == "B (point)"


def test_node_form_ref_change_when_no_board_keeps_child_rows(main_window,
                                                             tmp_path):
    """The child's row is untouched (only the renamed row is re-keyed): the
    subtree stays reachable under the moved parent item."""
    dock = _dock(main_window, tmp_path)
    _widget, item, form = _open_form(dock, "A")
    child_item = dock._node_items["A_CHILD"]

    form.ref_combo.setCurrentText("A_RENAMED")
    assert form.apply() is True

    assert dock._node_items["A_CHILD"] is child_item
    assert child_item.parent() is item             # still the renamed parent's


# ── format 3: a Ref edit must carry the node's ref_uuid with it ─────────────
#
# Under format 3 a tree node's `ref` is a NAME HINT beside an authoritative
# `ref_uuid` (see config/format3._F3_NODE_KIND_TARGET). `build_node()` builds a
# TreeNode WITHOUT a uuid and `copy_node_onto` copies none either, so a Ref edit
# left the OLD record's uuid beside the NEW name — and the writer stamp (and the
# loader's `_normalize_format3_refs`) then put the OLD name back: the edit was
# silently ROLLED BACK, and only a GUI restart "fixed" it (Denis, 08.10).
#
# The cell below witnesses that rollback end to end: the file is written as
# format 2 and LIFTED by the real open path (the lift mints every record's uuid
# AND the node's `ref_uuid`), a Ref edit is Applied, and the FILE is read back —
# first raw (no cache: exactly what got written), then through `load_config`.

FMT3_TREES = {
    "cells": {"c": {"components": [], "vias": [], "tracks": []}},
    "entities": [{"name": "E1", "cell": "c", "cluster": "C1"},
                 {"name": "E2", "cell": "c", "cluster": "C2"}],
    "trees": [
        {"name": "t", "anchor": {"origin": True},
         "nodes": [{"ref": "E1", "kind": "placement", "xy": [1.0, 0.0]}]},
    ],
}


def _fmt3_dock(main_window, tmp_path, data=None):
    """A dock on a format-2 file the loader LIFTS to format 3 — the real open
    path, so the node carries the identity a saved format-3 graph has."""
    from kicadstamp.config import format_version
    from kicadstamp.config.sexp_format import dict_to_sexp
    from kicadstamp.config_working_set import set_active_graph_root

    assert format_version.current_format() == 3, "the format-3 rig must pin 3"
    root = tmp_path / "fmt3_root.sexp"
    write_later(root, dict_to_sexp(data if data is not None else FMT3_TREES,
                                   format_number=2))
    set_active_graph_root(root)
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config did not load"
    return dock, root


def _node_shot(root, tree_index=0, node_index=0) -> dict:
    """The node as WRITTEN, from a raw read of the file (no reader cache): the
    bytes the next open will see. `ref_uuid`/`kind` are dropped when default."""
    from kicadstamp.config.sexp_format import sexp_to_dict

    data = sexp_to_dict(root.read_text(encoding="utf-8")) or {}
    return data["trees"][tree_index]["nodes"][node_index]


def test_node_ref_change_moves_the_ref_uuid(main_window, tmp_path, format3):
    """(а) Ref edit -> Apply: the node's `ref_uuid` follows the NEW record, so
    the next open reads the node as NEW.

    RED before the fix — the stale uuid made the writer stamp (and the loader's
    normalization) name the OLD record again: the edit was silently rolled
    back."""
    from kicadstamp.config import load_config
    from kicadstamp.utils.file_cache import invalidate_graph_path, invalidate_path

    dock, root = _fmt3_dock(main_window, tmp_path)
    node = dock._cfg.trees[0].nodes[0]
    assert node.ref == "E1"
    assert node.ref_uuid == det_uuid("entities:E1")   # the lift's identity

    _widget, _item, form = _open_form(dock, "E1")
    index = form.ref_combo.findText("E2")
    assert index >= 0, "the Ref combo must offer the other Entity"
    form.ref_combo.setCurrentIndex(index)
    assert form.apply() is True

    written = _node_shot(root)
    assert (written["ref"], written["ref_uuid"]) == ("E2", det_uuid("entities:E2"))

    invalidate_path(root)
    invalidate_graph_path(root)
    reloaded, _ctx = load_config(str(root))
    after = reloaded.trees[0].nodes[0]
    assert (after.ref, after.ref_uuid) == ("E2", det_uuid("entities:E2"))


# ── the §п.3 rules: ref_uuid, the tree-level cascade, the loader probe ──────
#
# The rig below is the same format-2-lifted-to-3 file, richer: five Entities
# (E3 / E5 name no node — they are the free targets a rename moves TO), one
# Mount node, a plain non-handle node E4, and a SECOND tree (`t2`, node E2)
# whose only job is to make a ref taken ELSEWHERE visible to the forest probe.

def _rig_data(**t1_extra):
    """The tree under test (`t1`) with `t1_extra` merged into it, plus `t2`."""
    t1 = {"name": "t1", "anchor": {"origin": True},
          "nodes": [{"ref": "E1", "kind": "placement", "xy": [1.0, 0.0]},
                    {"ref": "E4", "kind": "placement", "xy": [2.0, 0.0]},
                    {"ref": "M1", "kind": "mount", "xy": [0.0, 0.0],
                     "anchor": {"role": "FPGA"}}]}
    t1.update(t1_extra)
    return {
        "cells": {"c": {"components": [], "vias": [], "tracks": []}},
        "entities": [{"name": name, "cell": "c", "cluster": name}
                     for name in ("E1", "E2", "E3", "E4", "E5")],
        # A record of ANOTHER section, so a cell can pin that a kind change moves
        # the Ref (and the uuid) into the NEW section.
        "clone_placements": [{"name": "CP1", "cluster": "CP1", "cell": "c",
                              "xy": [0.0, 0.0]}],
        "trees": [t1, {"name": "t2", "anchor": {"origin": True},
                       "nodes": [{"ref": "E2", "kind": "placement",
                                  "xy": [0.0, 0.0]}]}],
    }


def _rich_dock(main_window, tmp_path, **t1_extra):
    return _fmt3_dock(main_window, tmp_path, _rig_data(**t1_extra))


def _reload(root):
    """The config the NEXT OPEN reads — cache dropped first (the Apply's write may
    land on the same mtime tick as the rig's own read)."""
    from kicadstamp.config import load_config
    from kicadstamp.utils.file_cache import invalidate_graph_path, invalidate_path

    invalidate_path(root)
    invalidate_graph_path(root)
    cfg, _ctx = load_config(str(root))
    return cfg


def test_a_ref_naming_no_record_refuses_the_apply(main_window, tmp_path, format3,
                                                  caplog):
    """(б) A Ref no record of the section carries -> the Apply REFUSES: red line
    under the form and in the Log, the node keeps its ref and its uuid, and
    nothing reaches the file (the writer stamp would otherwise put the old name
    back on the next save/load)."""
    dock, root = _rich_dock(main_window, tmp_path)
    _widget, _item, form = _open_form(dock, "E1")
    node = form._existing
    before = (node.ref, node.ref_uuid, root.read_text(encoding="utf-8"))

    form.ref_combo.setEditText("GHOST")
    with caplog.at_level("ERROR"):
        assert form.apply() is False

    assert (node.ref, node.ref_uuid) == before[:2]
    assert "names no existing entities record" in caplog.text
    assert "the node keeps 'E1'" in caplog.text
    assert root.read_text(encoding="utf-8") == before[2]   # nothing written
    assert "GHOST" in form.apply_status_label.text()        # the red line
    assert dock._dirty is False


def test_renaming_the_handle_moves_the_pivot_ref(main_window, tmp_path, format3,
                                                 caplog):
    """(в) The tree's pivot-ref names a NODE: renaming that node moves it along,
    and the next open reads a config that still names the moved handle. One INFO
    line says so; the row keeps its (handle) mark."""
    from kicadstamp.link_trees import link_trees

    dock, root = _rich_dock(main_window, tmp_path, pivot_ref="E1")
    tree = dock._trees[0]
    _widget, item, form = _open_form(dock, "E1")
    assert "(handle)" in item.text(0)

    form.ref_combo.setCurrentText("E5")
    with caplog.at_level("INFO"):
        assert form.apply() is True

    assert tree.pivot_ref == "E5"                    # the cascade
    assert "'E1' → 'E5'" in caplog.text and "pivot ref follows" in caplog.text
    assert "(handle)" in item.text(0)                # the mark followed the row
    link_trees(dock._cfg, dock._trees)               # and the forest still links
    reloaded = _reload(root)
    assert reloaded.trees[0].pivot_ref == "E5"       # the next open agrees


def test_renaming_the_handle_moves_the_self_anchor(main_window, tmp_path,
                                                   format3, caplog):
    """(г) The same for the tree's (self) anchor: it names a node of THIS tree."""
    from kicadstamp.link_trees import link_trees

    data = _rig_data()
    data["trees"][0]["anchor"] = {"self": {"ref": "E1"}}
    dock, root = _fmt3_dock(main_window, tmp_path, data)
    tree = dock._trees[0]
    _widget, item, form = _open_form(dock, "E1")

    form.ref_combo.setCurrentText("E5")
    with caplog.at_level("INFO"):
        assert form.apply() is True

    assert tree.anchor.self_ref == "E5"
    assert "'E1' → 'E5'" in caplog.text and "self anchor follows" in caplog.text
    assert "(handle)" not in item.text(0)            # no pivot-ref in this tree
    link_trees(dock._cfg, dock._trees)
    assert _reload(root).trees[0].anchor.self_ref == "E5"


def test_switching_a_node_to_a_record_free_kind_clears_the_ref_uuid(
        main_window, tmp_path, format3):
    """(д) Kind "placement" -> "mount": the node references NO record any more, so
    its `ref_uuid` must be CLEARED — the loader fatals on a uuid beside a local
    kind (trees._LOCAL_REF_KINDS), so leaving it would write an unreadable
    config."""
    dock, root = _rich_dock(main_window, tmp_path)
    _widget, _item, form = _open_form(dock, "E4")
    node = form._existing
    assert node.ref_uuid                            # the lift gave it one

    index = form.kind_combo.findData("mount")
    assert index >= 0
    form.kind_combo.setCurrentIndex(index)
    # A kind change repopulates the Ref combo, and a mount node has no placeable
    # candidates — the typed ref has to come back.
    form.ref_combo.setEditText("E4")
    form.mount_anchor_widget.load(mode="anchor", role="FPGA")
    assert form.apply() is True

    written = _node_shot(root, node_index=1)        # E4 is the second node of t1
    assert written["kind"] == "mount"
    assert "ref_uuid" not in written                # cleared, not carried
    reloaded = _reload(root)                        # ... and the file loads
    assert reloaded.trees[0].nodes[1].kind == "mount"
    assert reloaded.trees[0].nodes[1].ref_uuid is None


def test_a_handle_that_changes_its_kind_is_refused_by_the_loader_rule(
        main_window, tmp_path, format3, caplog):
    """(е) The tree's pivot-ref must stay a RECORD-BACKED node: switching that
    node's kind to a local one (mount) would make the next load a fatal. The
    Apply runs the LOADER's own rule and refuses with its text — the node and the
    tree are untouched.

    The other half (re-hanging the handle UNDER a mount through the Parent combo)
    cannot even be driven: `_node_parent_candidates` never offers a mount row for
    the tree's pivot-ref, and `_reparent_node` refuses it — the same predicate, no
    second copy."""
    dock, root = _rich_dock(main_window, tmp_path, pivot_ref="E1")
    _widget, _item, form = _open_form(dock, "E1")
    node = form._existing
    before = (node.kind, node.ref, node.ref_uuid)

    index = form.kind_combo.findData("mount")
    form.kind_combo.setCurrentIndex(index)
    form.ref_combo.setEditText("E1")     # the kind change cleared the combo
    form.mount_anchor_widget.load(mode="anchor", role="FPGA")
    with caplog.at_level("ERROR"):
        assert form.apply() is False

    assert (node.kind, node.ref, node.ref_uuid) == before   # nothing touched
    assert "record-backed node" in caplog.text              # the loader's text
    assert dock._dirty is False
    assert dock._trees[0].pivot_ref == "E1"


def test_a_record_to_record_kind_change_moves_the_section(main_window, tmp_path,
                                                          format3):
    """(ж) A plain kind change of a non-handle node (placement -> clone) is legal:
    the Ref moves to a record of the NEW section and the uuid follows it there —
    the ORDINARY case the new rules must not block."""
    dock, root = _rich_dock(main_window, tmp_path)
    _widget, _item, form = _open_form(dock, "E4")

    index = form.kind_combo.findData("clone")
    form.kind_combo.setCurrentIndex(index)
    form.ref_combo.setEditText("CP1")     # the kind change repopulated the combo
    assert form.apply() is True

    written = _node_shot(root, node_index=1)
    assert (written["kind"], written["ref"]) == ("clone", "CP1")
    assert written["ref_uuid"] == det_uuid("clone_placements:CP1")
    reloaded = _reload(root)
    assert reloaded.trees[0].nodes[1].ref == "CP1"


def test_the_ref_checker_is_not_stale_after_an_apply(main_window, tmp_path,
                                                     format3):
    """Т5: the form's "used refs" set is captured when it OPENS, so after a rename
    the freed old ref must not still count as taken — otherwise a perfectly legal
    second edit is refused (and the "(used)" hint lies). The set is refreshed from
    the dock on every successful Apply."""
    dock, root = _rich_dock(main_window, tmp_path)
    _widget, _item, form = _open_form(dock, "E4")

    form.ref_combo.setCurrentText("E5")
    assert form.apply() is True
    assert form._existing.ref == "E5"

    form.ref_combo.setCurrentText("E4")   # free again — nothing holds it
    assert form.apply() is True
    assert form._existing.ref == "E4"
    assert _node_shot(root, node_index=1)["ref"] == "E4"


def test_a_ref_taken_in_another_tree_is_refused_by_the_forest_probe(
        main_window, tmp_path, format3, caplog):
    """(з) The loader bars ONE ref from appearing in two nodes of the file — and
    it is only visible with a SHARED `seen_refs` across the forest. The form's own
    "used" set was captured when it opened, so a ref another tree takes AFTERWARDS
    slips past it: the probe must catch it, or the Apply writes a config the next
    open refuses."""
    dock, root = _rich_dock(main_window, tmp_path)
    _widget, _item, form = _open_form(dock, "E1")     # the form is OPEN now
    node = form._existing
    before = root.read_text(encoding="utf-8")

    # Another dock/editor renames t2's own node to E3 — after this form opened,
    # so this form's `_used_refs` cannot know about it (the real app has one form
    # per tree and both are live).
    t2 = next(t for t in dock._trees if t.name == "t2")
    t2.nodes[0].ref = "E3"

    form.ref_combo.setCurrentText("E3")
    with caplog.at_level("ERROR"):
        assert form.apply() is False

    assert node.ref == "E1"                           # nothing touched
    assert "already has a node" in caplog.text
    assert root.read_text(encoding="utf-8") == before  # nothing written


def test_an_entity_staged_while_the_form_is_open_resolves_at_apply(
        main_window, tmp_path, format3):
    """(и) The name -> uuid map comes from the DOCK's CURRENT cfg — the snapshot a
    form took when it OPENED is stale the moment anything is created elsewhere (a
    Save in another dock, a "Create entity" of this very session). The rig
    reproduces Denis's order exactly: the form is open FIRST, the Entity is
    written and the dock re-reads the graph AFTERWARDS — so `form._cfg` no longer
    is `dock._cfg` at Apply time, and judging by the snapshot would refuse a record
    that exists (the false "names no existing entities record")."""
    from kicadstamp.config_writer import read_data, write_data

    dock, root = _rich_dock(main_window, tmp_path)
    _widget, _item, form = _open_form(dock, "E1")     # the form is OPEN now

    data = read_data(root)
    data["entities"].append({"name": "E_NEW", "cell": "c", "uuid": "U-NEW"})
    write_data(root, data)                            # the product's write path
    dock.refresh_ref_candidates()                     # the dock swaps its cfg
    assert form._cfg is not dock._cfg, "the rig must keep the form's snapshot stale"

    form.ref_combo.setCurrentText("E_NEW")            # free-typed: the combo is old
    assert form.apply() is True

    written = _node_shot(root)
    assert (written["ref"], written["ref_uuid"]) == ("E_NEW", "U-NEW")
    assert _reload(root).trees[0].nodes[0].ref == "E_NEW"


def test_reparenting_the_handle_under_a_mount_is_refused(main_window, tmp_path,
                                                         format3, caplog):
    """(к) The tree's pivot-ref must stay a node that FOLLOWS the tree: re-hanging
    it under a `mount` node pins its base to a live component and the next load is
    a fatal ("hangs under mount node …"). The LOADER's own rule catches it in the
    probe — BEFORE `_apply_parent_change` touches the structure — so the refusal
    carries the loader's text and the node is still where it was.

    The Parent combo offers that mount row only because the tree's pivot-ref moved
    to this node AFTER the form opened (another editor); a form built afterwards
    bars the mount row by construction, and `_reparent_node` refuses it too — the
    SAME predicate, no second copy. The probe is what makes the refusal the
    loader's, and this cell is what keeps that true."""
    dock, root = _rich_dock(main_window, tmp_path)
    tree = dock._trees[0]
    _widget, _item, form = _open_form(dock, "E1")
    node = form._existing
    before = root.read_text(encoding="utf-8")

    tree.pivot_ref = "E1"                    # the handle moved to E1 while open
    index = form.parent_combo.findText("M1")
    assert index >= 0, "the rig must still offer the mount row"
    form.parent_combo.setCurrentIndex(index)
    with caplog.at_level("ERROR"):
        assert form.apply() is False

    assert "hangs under mount node" in caplog.text   # the LOADER's own text
    assert node.ref == "E1"
    assert dock._find_parent(tree, node) is None      # not re-hung
    assert tree.pivot_ref == "E1"
    assert root.read_text(encoding="utf-8") == before


def test_an_auto_kind_node_loses_its_ref_uuid(main_window, tmp_path, format3):
    """(л) kind None ("auto"): the node references no FIXED section, so
    `node_ref_uuid` resolves None and the copy CLEARS the uuid. The loader neither
    fatals on it (the local-kind rule names concrete kinds) nor normalizes such a
    node, so the file loads and the node keeps the name it names — the decision's
    "any other kind -> ref_uuid = None", for the auto case."""
    dock, root = _rich_dock(main_window, tmp_path)
    _widget, _item, form = _open_form(dock, "E1")
    node = form._existing
    assert node.ref_uuid                              # the lift gave it one

    index = form.kind_combo.findData(None)            # the "auto" row
    assert index >= 0
    form.kind_combo.setCurrentIndex(index)
    form.ref_combo.setEditText("E1")                  # the kind change cleared it
    assert form.apply() is True

    written = _node_shot(root)
    assert "ref_uuid" not in written                  # cleared, not carried
    assert "kind" not in written                      # "auto" is the default kind
    reloaded = _reload(root)
    assert reloaded.trees[0].nodes[0].ref == "E1"     # the name is untouched

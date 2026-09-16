# tests/test_component_nodes.py
"""Tests for the kind "component" tree node (plan_2026_09_17 Э3, guards С6-С10).

A component node places ONE live component directly — no cell, no Entity, no
config record:

    (node (ref "place_ic7") (kind component)
          (anchor (role "AD_DAC") (sheet "Channel_0") (cluster "DAC_BUF"))
          (xy 12.5 40.0) (rotation 90))
    (node (ref "place_ram") (kind component) (anchor (ref "IC7")) (xy 1 2))

The guards here pin, in order: the GRAMMAR (both formats, both addresses, the
local-ref rules and the load-time duplicate-address rule), the MATERIALIZATION
(one transient CoordinatePlacement at the node's pose, --only by node name, the
config untouched), the PAD seating (numbers, not "the call happened"), the LIVE
duplicate rule, the rigid group (the node rides its parent's redraw), and — since
plan_2026_09_16_commit_document_and_pending_direction, Э4/Т4.1 — the IDENTITY
SIDE MAP that carries the refdes the address found all the way to Phase 0, so the
transient record is never re-resolved by tags (the Ф8 defect).
"""
import logging
from unittest.mock import MagicMock

import pytest

from kicadstamp.apply_pipeline import apply_only_filter
from kicadstamp.config import Config, CoordinatePlacement
from kicadstamp.config.loader import load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Pad
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.link_trees import link_trees
from kicadstamp.placement.entity_placement import materialize_component_nodes
from kicadstamp.placement.services.coordinate_position_calculator import (
    build_coordinate_moves,
)
from kicadstamp.trees import load_trees, tree_from_dict, tree_to_dict
from kicadstamp.tree_position import (
    PositionOverride,
    apply_rigid_override,
    capture_rigid_state,
    curated_redraw_plan_forest,
)
from kicadstamp.utils.units import MM

F_CU = BoardLayer.BL_F_Cu


class _Board:
    """Live board double: real Footprint/Pad objects, the two custom fields read
    through the adapter exactly as the real one is.

    sheet_paths — {ref: (uuid, ...)} for the footprint's hierarchical path, the
    LAST uuid being the component's own. The default ("sheet",) is the
    one-segment path every older guard wants: its resolved leaf is empty, exactly
    like a board whose sheet dictionary is empty (_fp_on_sheet then matches
    nothing and narrows nothing — see sheet_names.resolve_sheet_path_names).
    selection — the refs the role resolver's LAST narrowing step sees as selected;
    empty (a headless run) by default."""

    def __init__(self, components, sheet_paths=None, selection=None):
        # components: {ref: (role, cluster, (x_mm, y_mm), angle_deg, [pads])}
        self._fields = {}
        self._fps = []
        self._pads = {}
        self._selection = list(selection or [])
        paths = sheet_paths or {}
        for ref, (role, cluster, xy, angle, pads) in components.items():
            self._fields[ref] = {ROLE_FIELD_NAME: role, CLUSTER_FIELD_NAME: cluster}
            self._fps.append(Footprint(
                ref=ref, uuid=ref,
                position=Vector2.from_xy(int(xy[0] * MM), int(xy[1] * MM)),
                angle_deg=angle, layer=F_CU,
                sheet_path_uuids=tuple(paths.get(ref, ("sheet",)))))
            self._pads[ref] = [
                Pad(number=num, net_name="N",
                    position=Vector2.from_xy(int(px * MM), int(py * MM)))
                for num, px, py in pads]

    def get_footprints(self):
        return list(self._fps)

    def get_footprint(self, ref):
        return next((fp for fp in self._fps if fp.ref == ref), None)

    def get_field_value(self, fp, field):
        return self._fields[fp.ref].get(field)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_pad_by_number(self, fp, number):
        for pad in self._pads.get(fp.ref, []):
            if str(pad.number) == str(number):
                return pad
        return None

    def get_selected_items(self):
        """The role resolver's LAST narrowing step reads the selection; an empty
        selection is what a headless run sees (the default)."""
        return [fp for fp in self._fps if fp.ref in self._selection]


def _cfg(*trees):
    return Config(cells={}, entities=[], trees=list(trees))


def _tree(data):
    return tree_from_dict(data)


def _role_tree(nodes, name="t"):
    return _tree({"name": name, "anchor": {"origin": True}, "nodes": nodes})


def _component_node(ref="place_1", kind="component", **kw):
    node = {"ref": ref, "kind": kind}
    node.update(kw)
    return node


# ── С6: the grammar ──────────────────────────────────────────────────────────

def test_a_component_node_loads_in_both_formats_with_both_addresses(tmp_path):
    """С6: (anchor (role ...)) and (anchor (ref ...)) parse in the s-expr and the
    dict shape, and the s-expr round-trip keeps them identical."""
    body = ('(tree (name "t") (anchor (origin))\n'
            '      (node (ref "place_1") (kind component)\n'
            '            (anchor (role "AD_DAC") (sheet "Channel_0")\n'
            '                    (cluster "DAC_BUF") (pad "11"))\n'
            '            (xy 12.5 40.0) (rotation 90))\n'
            '      (node (ref "place_2") (kind component)\n'
            '            (anchor (ref "IC7")) (xy 1.0 2.0)))')
    path = tmp_path / "trees.trees"
    path.write_text("(kicadstamp-trees\n" + body + ")", encoding="utf-8")
    from_sexp = load_trees(str(path))[0]

    tree = tree_from_dict({
        "name": "t", "anchor": {"origin": True}, "nodes": [
            _component_node(xy=[12.5, 40.0], rotation=90.0,
                            anchor={"role": "AD_DAC", "sheet": "Channel_0",
                                    "cluster": "DAC_BUF", "pad": "11"}),
            {"ref": "place_2", "kind": "component", "anchor": {"ref": "IC7"},
             "xy": [1.0, 2.0]}]})
    assert from_sexp == tree
    assert from_sexp.nodes[0].anchor.role == "AD_DAC"
    assert from_sexp.nodes[0].anchor.anchor_pad == "11"
    assert from_sexp.nodes[1].anchor.ref == "IC7"
    # Serializing back keeps the address (the writers pick the shape by kind).
    assert tree_to_dict(from_sexp)["nodes"][1]["anchor"] == {"ref": "IC7"}


def test_a_component_node_ref_is_local_so_two_trees_may_share_it():
    """С6/Т3.2: the ref is a LOCAL name — it is exempt from "a record's node
    appears once in the whole file", so two trees may each carry a node called
    "place_1" (their addresses differ)."""
    trees = [
        _role_tree([_component_node(anchor={"ref": "IC1"})], name="a"),
        _role_tree([_component_node(anchor={"ref": "IC2"})], name="b"),
    ]
    assert [t.nodes[0].ref for t in trees] == ["place_1", "place_1"]


def test_a_component_node_ref_must_be_unique_within_its_tree():
    """С6: within ONE tree the local name is the identity a redraw applies by —
    a duplicate is a load-time fatal, with the component-specific wording."""
    with pytest.raises(ValidationError) as exc:
        _role_tree([_component_node(ref="dup", anchor={"ref": "IC1"}),
                    _component_node(ref="dup", anchor={"ref": "IC2"})])
    assert "component node ref(s) dup are not unique" in str(exc.value)


def test_a_component_node_without_an_address_is_a_fatal():
    """Т3.1: the address is part of the node (it is how the component is named),
    so a component node without (anchor ...) cannot load."""
    with pytest.raises(ValidationError) as exc:
        _role_tree([_component_node()])
    assert "needs an anchor mapping with the ADDRESS" in str(exc.value)


def test_a_ref_and_role_together_is_a_fatal():
    with pytest.raises(ValidationError) as exc:
        _role_tree([_component_node(anchor={"ref": "IC1", "role": "AD_DAC"})])
    assert "must be ref OR role, not both" in str(exc.value)


# ── С9 (structural): two nodes, one address ──────────────────────────────────

def test_two_component_nodes_with_the_same_address_in_one_tree_is_fatal():
    """С9: both nodes would place the SAME component and the run would apply
    whichever came last — a load-time fatal naming both."""
    with pytest.raises(ValidationError) as exc:
        _role_tree([_component_node(ref="first",
                                    anchor={"role": "AD_DAC", "sheet": "Channel_0"}),
                    _component_node(ref="second",
                                    anchor={"role": "AD_DAC", "sheet": "Channel_0"})])
    text = str(exc.value)
    assert "two component nodes carry the same address" in text
    assert "first and second" in text


def test_the_same_role_with_a_different_pad_is_a_different_address():
    """The structural check keys the WHOLE address: the same role with another
    pad is not a duplicate here (whether it resolves to the same FOOTPRINT is
    the live check's question — see below)."""
    _role_tree([_component_node(ref="a", anchor={"role": "AD_DAC", "pad": "1"}),
                _component_node(ref="b", anchor={"role": "AD_DAC", "pad": "2"})])


def test_the_structural_check_refuses_in_the_sexp_path_too(tmp_path):
    body = ('(tree (name "t") (anchor (origin))\n'
            '      (node (ref "a") (kind component) (anchor (ref "IC7")))\n'
            '      (node (ref "b") (kind component) (anchor (ref "IC7"))))')
    path = tmp_path / "trees.trees"
    path.write_text("(kicadstamp-trees\n" + body + ")", encoding="utf-8")
    with pytest.raises(ValidationError, match="same address"):
        load_trees(str(path))


# ── С7/С8: materialization ───────────────────────────────────────────────────

def _one_node_tree(*nodes):
    return _cfg(_role_tree(list(nodes)))


def test_materialization_gives_one_transient_coordinate_placement():
    """С7: the node produces EXACTLY one CoordinatePlacement — absolute x/y from
    the node's pose, rotation from the accumulated frame, name == the node ref,
    cluster/role/sheet from the address, and the config keeps NO record of it."""
    cfg = _one_node_tree(_component_node(
        ref="place_1", xy=[12.5, 40.0], rotation=90.0,
        anchor={"role": "AD_DAC", "sheet": "Channel_0", "cluster": "DAC_BUF"}))
    board = _Board({"IC2": ("AD_DAC", "DAC_BUF", (0.0, 0.0), 0.0, [])})
    coords = materialize_component_nodes(board, cfg, {})
    assert len(coords) == 1
    cp = coords[0]
    assert cp.name == "place_1"
    assert (cp.x_mm, cp.y_mm) == (12.5, 40.0)
    assert cp.rotation_deg == 90.0
    assert cp.role == "AD_DAC" and cp.cluster == "DAC_BUF"
    assert cp.sheet == "Channel_0"
    assert cp.anchor == "center" and cp.anchor_pad is None
    # Transient by construction: the config itself gained nothing.
    assert cfg.coordinate_placements == []
    assert cfg.trees[0].nodes[0].kind == "component"


def test_nested_pose_accumulates_and_only_picks_the_node_by_name():
    """С7: the node's pose is composed like any other node's (parent offset +
    its own, rotated into the parent's frame) and `--only <node name>` selects
    exactly it — the same list the pipeline narrows by."""
    cfg = _one_node_tree(
        {"ref": "PARENT", "kind": "component", "xy": [10.0, 0.0], "rotation": 90.0,
         "anchor": {"ref": "IC1"},
         "children": [_component_node(ref="place_child", xy=[5.0, 0.0],
                                      anchor={"ref": "IC2"})]})
    board = _Board({"IC1": ("R_A", "C_A", (0.0, 0.0), 0.0, []),
                    "IC2": ("R_B", "C_B", (0.0, 0.0), 0.0, [])})
    coords = materialize_component_nodes(board, cfg, {})
    by_name = {c.name: c for c in coords}
    assert set(by_name) == {"PARENT", "place_child"}
    assert by_name["PARENT"].rotation_deg == 90.0
    # The child's (5,0) is rotated into the parent's 90° frame — the board's
    # Y-DOWN convention (rotate_offset_mm), so it lands 5 mm along -Y.
    assert by_name["place_child"].x_mm == 10.0
    assert by_name["place_child"].y_mm == pytest.approx(-5.0, abs=1e-9)

    only_one = materialize_component_nodes(board, cfg, {}, only=["place_child"])
    assert [c.name for c in only_one] == ["place_child"]
    assert materialize_component_nodes(board, cfg, {}, only=["nope"]) == []


def test_an_address_that_matches_nothing_is_a_fatal_naming_the_node():
    """The address is resolved by the project's own role resolver, so its honest
    message reaches the user — here the role does not exist on the board."""
    cfg = _one_node_tree(_component_node(
        ref="place_1", anchor={"role": "NOPE", "sheet": "Channel_0"}))
    board = _Board({"IC2": ("AD_DAC", "DAC_BUF", (0.0, 0.0), 0.0, [])})
    with pytest.raises(ValidationError):
        materialize_component_nodes(board, cfg, {})


def test_a_pad_address_seats_the_component_by_that_pad():
    """С8: `pad` means "put the component's PAD on the node's point" — checked
    by NUMBERS: with the pad 1 mm along +X of the footprint's own origin and no
    rotation, the node point (10, 0) must leave the origin at (9, 0)."""
    cfg = _one_node_tree(_component_node(
        ref="place_1", xy=[10.0, 0.0],
        anchor={"role": "AD_DAC", "sheet": "Channel_0", "cluster": "DAC_BUF",
                "pad": "1"}))
    board = _Board({"IC2": ("AD_DAC", "DAC_BUF", (0.0, 0.0), 0.0,
                            [("1", 1.0, 0.0)])})
    coords = materialize_component_nodes(board, cfg, {})
    assert coords[0].anchor == "pad" and coords[0].anchor_pad == "1"

    moves = build_coordinate_moves(board, coords, points={}, sheet_names={})
    assert len(moves) == 1
    assert moves[0].ref == "IC2"
    origin = moves[0].position
    assert origin.x / MM == pytest.approx(9.0, abs=1e-6)
    assert origin.y / MM == pytest.approx(0.0, abs=1e-6)


def test_two_different_addresses_on_one_footprint_is_fatal():
    """С9 (live half): "IC2" addressed both by Role and by refdes are different,
    legal addresses — but they resolve to ONE footprint, and the run would apply
    whichever node came last. The error names BOTH nodes."""
    cfg = _cfg(
        _role_tree([_component_node(ref="by_role",
                                    anchor={"role": "AD_DAC", "sheet": "Channel_0",
                                            "cluster": "DAC_BUF"})], name="a"),
        _role_tree([_component_node(ref="by_ref", anchor={"ref": "IC2"})], name="b"),
    )
    board = _Board({"IC2": ("AD_DAC", "DAC_BUF", (0.0, 0.0), 0.0, [])})
    with pytest.raises(ValidationError) as exc:
        materialize_component_nodes(board, cfg, {})
    text = str(exc.value)
    assert "place the same component" in text
    assert "by_role" in text and "by_ref" in text and "IC2" in text


# ── С10: the rigid group ─────────────────────────────────────────────────────

def test_resolve_order_hands_the_identity_map_to_the_pipeline(monkeypatch):
    """Т4.1 wiring — the same class of hole Э3's В3 closed for the copper
    provider: the map the materializer fills must become THE PIPELINE'S OWN
    field. If it were collected into a throwaway dict, Phase 0 would receive an
    empty map and fall back to the tags, i.e. Ф8 would come back while every
    unit guard above stays green."""
    from kicadstamp import apply_pipeline as ap
    from kicadstamp.apply_pipeline import ApplyPipeline

    cfg = _one_node_tree(_component_node(ref="place_1", anchor={"ref": "U14"}))
    board = _u14_u15_board()
    # The Entity half of the same step is not this guard's subject (it has its
    # own suite) — only the component-node wiring is.
    monkeypatch.setattr(ap, "materialize_entity_placements", lambda *a, **kw: [])

    pipeline = ApplyPipeline("board.yaml", preloaded_cfg=cfg)
    pipeline._full_cfg = cfg
    pipeline.adapter = board
    pipeline._resolve_order()

    assert pipeline._component_identities == {"place_1": "U14"}
    assert [c.name for c in pipeline.cfg.coordinate_placements] == ["place_1"]


def test_a_component_node_rides_the_rigid_group_of_its_parent(monkeypatch):
    """С10/Р2: a component node is a FULL member of the rigid group — the capture
    records its offset from the parent's live frame and the apply re-projects it
    into the parent's NEW frame, exactly like every other node.

    The parent's live base is stubbed (the live-resolution path itself has its
    own suite); the component node's OWN pose comes from the board double, which
    is what makes this test specific to the component node."""
    import kicadstamp.tree_position as tp

    cfg = _cfg(_tree({"name": "t", "anchor": {"ref": "PARENT"},
                      "nodes": [_component_node(ref="place_1",
                                                anchor={"ref": "IC2"})]}))
    board = _Board({"PARENT": ("PARENT", "C", (10.0, 0.0), 0.0, []),
                    "IC2": ("AD_DAC", "DAC_BUF", (13.0, 0.0), 0.0, [])})
    parent_pos = {"value": Vector2.from_xy(int(10.0 * MM), 0)}
    monkeypatch.setattr(tp, "resolve_base_live_position",
                        lambda adapter, cfg, ref, record, points, sheet_names:
                        parent_pos["value"])
    monkeypatch.setattr(tp, "_base_rotation_or_zero",
                        lambda adapter, cfg, ref, record, sheet_names: 0.0)

    linked = link_trees(cfg, cfg.trees)
    assert [t.name for t in linked] == ["t"]
    names, _warnings = curated_redraw_plan_forest(linked, {"place_1"})
    assert names == ["place_1"]

    captures, parent_map = capture_rigid_state(board, cfg, linked[0], names, {})
    capture = captures["place_1"]
    assert capture.local_offset.x / MM == pytest.approx(3.0, abs=1e-6)
    assert capture.local_offset.y == 0

    # The parent moves 10 mm to the right -> the component follows it.
    parent_pos["value"] = Vector2.from_xy(int(20.0 * MM), 0)
    parent_ref, parent_record, _is_anchor = parent_map["place_1"]
    override = apply_rigid_override(board, cfg, parent_ref, parent_record,
                                    capture, {})
    assert override.position.x / MM == pytest.approx(23.0, abs=1e-6)
    assert override.rotation_deg == 0.0


# ── the two gaps the LIVE run of 2026-09-17 exposed ─────────────────────────
# Both are "the record does not exist until the run makes it" problems: the
# config-dict key whitelist knew only a mount node's role anchor, and --only
# could not see a name that no section carries yet.

def test_a_config_file_with_a_component_node_loads_and_is_only_selectable(
        tmp_path):
    """LIVE-FOUND (2026-09-17): a component node addressed by REFDES is a legal
    config key set (the whitelist was mount-only and rejected "ref"), and its
    node name is a legal --only identity even though it exists only as a
    TRANSIENT record created later in the run (the per-node Redraw button's own
    call would otherwise die with "names not found")."""
    path = tmp_path / "config.sexp"
    path.write_text(
        "(kicadstamp-config\n"
        "  (trees\n"
        "    (tree\n"
        '      (name "probe")\n'
        "      (anchor (origin))\n"
        "      (node\n"
        '        (ref "probe_1")\n'
        "        (kind component)\n"
        "        (anchor\n"
        '          (ref "IC7")\n'
        "        )\n"
        "        (xy 12.5 40.0)\n"
        "        (rotation 90.0)\n"
        "      )\n"
        "    )\n"
        "  )\n"
        ")\n", encoding="utf-8")
    cfg, _ctx = load_config(str(path))
    assert cfg.trees[0].nodes[0].anchor.ref == "IC7"
    # --only <node name> is accepted (and narrows nothing: the node is no
    # section entry), while an unrelated name still fails loudly.
    narrowed = apply_only_filter(cfg, ["probe_1"])
    assert narrowed.coordinate_placements == []
    with pytest.raises(Exception):
        apply_only_filter(cfg, ["something_else"])


def test_the_address_grammar_did_not_widen_the_other_kinds(tmp_path):
    """Making a component node's address accept (ref ...) must not widen
    anything else: a tree-anchor-only base on a component node is still a fatal,
    and a mount node's anchor stays role-only.

    NOTE (measured 2026-09-17, both shapes probed): the config-dict KEY check
    (entries._check_tree_node_keys) is the gate that rejected the first refdes
    address — it is kind-aware now — while a base the parser itself rejects
    (here (point ...)) is refused with the parser's own wording."""
    def _write(name, kind, address):
        path = tmp_path / f"{name}.sexp"
        path.write_text(
            "(kicadstamp-config\n"
            "  (trees\n"
            "    (tree\n"
            '      (name "probe")\n'
            "      (anchor (origin))\n"
            "      (node\n"
            '        (ref "n1")\n'
            f"        (kind {kind})\n"
            f"        (anchor ({address}))\n"
            "        (xy 1.0 2.0)\n"
            "      )\n"
            "    )\n"
            "  )\n"
            ")\n", encoding="utf-8")
        return path

    with pytest.raises(ValidationError, match="tree-anchor-only"):
        load_config(str(_write("comp_point", "component", 'point "P1"')))
    with pytest.raises(ValidationError, match="tree-anchor-only"):
        load_config(str(_write("mount_ref", "mount", 'ref "IC7"')))
    # …while both legal component addresses load.
    load_config(str(_write("comp_ref", "component", 'ref "IC7"')))
    load_config(str(_write("comp_role", "component", 'role "R"')))


def test_a_component_node_is_a_vertex_of_the_plan():
    """Т3.5: the node emits its own name into the redraw plan (it is what the
    per-name --only run applies), and it is NOT mistaken for copper."""
    cfg = _cfg(_role_tree([_component_node(ref="place_1",
                                           anchor={"ref": "IC2"})]))
    linked = link_trees(cfg, cfg.trees)
    names, warnings = curated_redraw_plan_forest(linked, {"place_1"})
    assert names == ["place_1"]
    # The one note is the ordinary "your parent is not in the selection" for a
    # top-level node of an ORIGIN-anchored tree — nothing component-specific.
    assert len(warnings) == 1 and "will be redrawn from the current position" in warnings[0]


# ── Э4/Т4.1: the identity side map (plan_2026_09_16, Ф8 defect) ──────────────
# A transient component-node record used to reach Phase 0 with nothing but its
# TAGS, and Phase 0 resolved it a SECOND time by (role, cluster) plus a sheet
# narrowed to the LEAF of the footprint's path. On the very cases an address
# exists for — twins sharing Role/Cluster on same-named sheets of different
# instances, or components with no tags at all — that second search is ambiguous
# and the run died with a false "fix the tagging" fatal (the tags were right).
# The refdes the address already found now travels in a side map, and Phase 0
# takes the footprint by it.

def _u14_u15_board():
    """Ф8's board: two buffers with the SAME Role/Cluster on same-named sheets of
    two DIFFERENT instances — the leaf segment is identical ('Out_A')."""
    return _Board(
        {"U14": ("BUF_SCHMITT", "SIG_OUT", (0.0, 0.0), 0.0, []),
         "U15": ("BUF_SCHMITT", "SIG_OUT", (10.0, 0.0), 0.0, [])},
        sheet_paths={"U14": ("hp0", "out_a", "u14"),
                     "U15": ("hp1", "out_a", "u15")})


_U14_SHEETS = {"hp0": "HP_Channel_0", "hp1": "HP_Channel_1", "out_a": "Out_A"}


def test_a_refdes_address_survives_to_phase_0_through_the_identity_map():
    """Т4.2 п.1 (Ф8): (ref "U14") where U14 and U15 share Role, Cluster AND the
    leaf sheet segment. Step 1 finds U14 by its address; with the map Phase 0
    moves U14. WITHOUT the map the very same call dies on the tag search — that
    is the proof this guard catches the defect (М9 turns it red)."""
    cfg = _one_node_tree(_component_node(ref="place_1", xy=[5.0, 7.0],
                                         anchor={"ref": "U14"}))
    board = _u14_u15_board()
    ids: dict[str, str] = {}
    coords = materialize_component_nodes(board, cfg, _U14_SHEETS,
                                         identity_out=ids)
    assert ids == {"place_1": "U14"}
    assert coords[0].sheet == "Out_A"          # the leaf, as Ф8 measured
    assert coords[0].role == "BUF_SCHMITT" and coords[0].cluster == "SIG_OUT"

    moves = build_coordinate_moves(board, coords, points={},
                                   sheet_names=_U14_SHEETS, identity_by_name=ids)
    assert [m.ref for m in moves] == ["U14"]
    assert moves[0].position.x / MM == pytest.approx(5.0, abs=1e-6)
    assert moves[0].position.y / MM == pytest.approx(7.0, abs=1e-6)

    with pytest.raises(ValidationError, match="expected exactly one"):
        build_coordinate_moves(board, coords, points={}, sheet_names=_U14_SHEETS)


def test_an_untagged_component_survives_to_phase_0_through_the_identity_map():
    """Т4.2 п.2 (Ф8): a (ref "J6") address on components with NO Role/Cluster at
    all. The address is the ONLY identity there is — the tag search has nothing
    to match and fatals ("Role=None, Cluster=None … fix the tagging")."""
    cfg = _one_node_tree(_component_node(ref="place_1", xy=[3.0, 4.0],
                                         anchor={"ref": "J6"}))
    board = _Board({"J6": (None, None, (0.0, 0.0), 0.0, []),
                    "J5": (None, None, (1.0, 0.0), 0.0, [])})
    ids: dict[str, str] = {}
    coords = materialize_component_nodes(board, cfg, {}, identity_out=ids)
    assert ids == {"place_1": "J6"}
    assert coords[0].role is None and coords[0].cluster is None

    moves = build_coordinate_moves(board, coords, points={}, sheet_names={},
                                   identity_by_name=ids)
    assert [m.ref for m in moves] == ["J6"]
    with pytest.raises(ValidationError, match="expected exactly one"):
        build_coordinate_moves(board, coords, points={}, sheet_names={})


def test_a_role_address_narrowed_by_selection_reaches_phase_0_unchanged():
    """Т4.2 п.3: step 1 narrowed two same-Role/same-Cluster candidates by the
    BOARD SELECTION, which Phase 0 does not read at all. The map is filled for a
    ROLE address exactly as for a refdes one (М10 — "only the refdes branch
    fills the map" — turns this red), so Phase 0 moves the footprint the
    selection picked."""
    cfg = _one_node_tree(_component_node(ref="place_1", xy=[8.0, 0.0],
                                         anchor={"role": "OP_AMP"}))
    board = _Board({"A1": ("OP_AMP", "CH_A", (0.0, 0.0), 0.0, []),
                    "A2": ("OP_AMP", "CH_A", (1.0, 0.0), 0.0, [])},
                   selection=["A2"])
    ids: dict[str, str] = {}
    coords = materialize_component_nodes(board, cfg, {}, identity_out=ids)
    assert ids == {"place_1": "A2"}

    moves = build_coordinate_moves(board, coords, points={}, sheet_names={},
                                   identity_by_name=ids)
    assert [m.ref for m in moves] == ["A2"]
    # …and the tag search alone genuinely cannot tell A1 from A2 here.
    with pytest.raises(ValidationError, match="expected exactly one"):
        build_coordinate_moves(board, coords, points={}, sheet_names={})


def test_a_rigid_override_and_the_identity_map_coexist_on_a_component_node():
    """Т4.2 п.4: a HARD redraw (`position_overrides` keyed by the node's name) in
    the U14/U15 scenario must not fatal and must move U14. The override supplies
    the POSE, the map supplies the IDENTITY — the footprint lookup runs before
    the override is read, so before Т4.1 the override path died here too."""
    cfg = _one_node_tree(_component_node(ref="place_1", anchor={"ref": "U14"}))
    board = _u14_u15_board()
    ids: dict[str, str] = {}
    coords = materialize_component_nodes(board, cfg, _U14_SHEETS,
                                         identity_out=ids)
    override = PositionOverride(
        position=Vector2.from_xy(int(1.0 * MM), int(2.0 * MM)), rotation_deg=90.0)
    overrides = {"place_1": override}

    moves = build_coordinate_moves(board, coords, points={},
                                   sheet_names=_U14_SHEETS,
                                   position_overrides=overrides,
                                   identity_by_name=ids)
    assert [m.ref for m in moves] == ["U14"]
    assert moves[0].position.x / MM == pytest.approx(1.0, abs=1e-6)
    assert moves[0].position.y / MM == pytest.approx(2.0, abs=1e-6)
    assert moves[0].angle.degrees == pytest.approx(90.0)

    with pytest.raises(ValidationError, match="expected exactly one"):
        build_coordinate_moves(board, coords, points={},
                               sheet_names=_U14_SHEETS,
                               position_overrides=overrides)


def test_a_coordinate_record_outside_the_map_keeps_the_old_tag_search():
    """Т4.2 п.5 ("byte-for-byte the old behaviour"): every ORDINARY coordinate
    placement has no map entry and is still resolved by its tags — a unique one
    resolves, an ambiguous one is the same fatal as before."""
    cp = CoordinatePlacement(cluster="SIG_OUT", role="BUF_SCHMITT",
                             x_mm=1.0, y_mm=2.0, rotation_deg=0.0, name="an_entry")
    # A map that names OTHER records must not shadow the tag search.
    other_map = {"place_1": "U15"}

    single = _Board({"U15": ("BUF_SCHMITT", "SIG_OUT", (0.0, 0.0), 0.0, [])})
    moves = build_coordinate_moves(single, [cp], points={}, sheet_names={},
                                   identity_by_name=other_map)
    assert [m.ref for m in moves] == ["U15"]

    with pytest.raises(ValidationError, match="expected exactly one"):
        build_coordinate_moves(_u14_u15_board(), [cp], points={}, sheet_names={},
                               identity_by_name=other_map)


def test_a_refdes_that_left_the_board_between_step1_and_phase0_is_a_fatal():
    """Т4.2 п.6: the map names a footprint that is no longer on the board (the
    user deleted or renamed it after materialization). A fatal naming BOTH the
    node and the refdes — never a silent fallback to the tag search, which would
    move whatever else carries those tags."""
    cp = CoordinatePlacement(cluster="SIG_OUT", role="BUF_SCHMITT",
                             x_mm=1.0, y_mm=2.0, rotation_deg=0.0,
                             name="place_1")
    board = _Board({"U14": ("BUF_SCHMITT", "SIG_OUT", (0.0, 0.0), 0.0, [])})
    with pytest.raises(ValidationError) as exc:
        build_coordinate_moves(board, [cp], points={}, sheet_names={},
                               identity_by_name={"place_1": "U99"})
    text = str(exc.value)
    assert "place_1" in text and "U99" in text


def test_the_identity_map_is_narrowed_with_the_records():
    """Т4.1: an --only run gets a map describing exactly the records it returns —
    a dropped record leaves no entry behind, so Phase 0 can never be handed a
    name the run does not apply."""
    cfg = _one_node_tree(
        _component_node(ref="keep_me", anchor={"ref": "U14"}),
        _component_node(ref="drop_me", anchor={"ref": "U15"}))
    board = _u14_u15_board()
    ids: dict[str, str] = {}
    coords = materialize_component_nodes(board, cfg, _U14_SHEETS, only=["keep_me"],
                                         identity_out=ids)
    assert [c.name for c in coords] == ["keep_me"]
    assert ids == {"keep_me": "U14"}

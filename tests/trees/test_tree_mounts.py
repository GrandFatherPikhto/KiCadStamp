# tests/trees/test_tree_mounts.py
"""Сторожа Т2 of plan_2026_11_03_add_mounts_table — the rules a mounts TABLE needs
before it writes anything: is this the same mount, which rows, what is a row
called, may the batch be written.

The two Д3 branches that the live board does NOT exercise (measured in Т1 on
IC1: 145 pads, no repeated number, no paste-only pad) are locked HERE with plain
`Pad` objects instead: a TabPin (one number, two pads) and a paste window
(`copper_layers == ()`). That is deliberate — a rule that only its fixture can
reach must not depend on the profile happening to carry the case.

Every cell asserts the FACT, not the wording, except where the wording IS the
contract: (a) an unknown placeholder refuses in words that speak of a mount name
and not of a clone's `params`, (b) a name colliding with a positioned node is
refused by the LOADER's own sentence (the plan is not allowed to invent a second
rule), (c) a refused batch leaves ZERO nodes (Д7 «всё или ничего»).
"""
import pytest

from kicadstamp.domain.board import BoardLayer, Pad
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.net_resolution import resolve_net
from kicadstamp.tree_mounts import (
    MountAddress,
    MountRow,
    filter_rows,
    mount_key,
    mount_key_of,
    mount_key_of_dict,
    mount_name,
    occupied_pads,
    pad_number,
    pad_rows,
    plan_mount_nodes,
)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

_ORIGIN = Vector2.from_xy(0, 0)
_ADDRESS = MountAddress(role="FPGA", cluster="FPGA", sheet="FPGA")


def _pad(number, net=None, copper=None):
    """A pad as `adapter.get_footprint_pads` returns it. `copper=None` keeps the
    field at its own default (None = UNKNOWN = every copper layer, the historical
    reading); a `()` there is a PASTE-ONLY pad — the shape the live IC1 does not
    have."""
    return Pad(number=number, net_name=net, position=_ORIGIN, copper_layers=copper)


def _mount(ref, *, pad=None, role="FPGA", sheet="FPGA", cluster="FPGA",
           xy=None, polar=None, rotation=0.0, children=None):
    return TreeNode(ref=ref, kind="mount", xy=xy, polar=polar, rotation=rotation,
                    name=None, group=None, children=list(children or []),
                    anchor=TreeAnchor(role=role, is_origin=False, anchor_sheet=sheet,
                                      anchor_cluster=cluster, anchor_pad=pad))


def _tree(*nodes, name="t"):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=list(nodes))


def _rows(*pads):
    return pad_rows(pads)


# ── 1. «тот же маунт»: одна запись, сколько бы способов её ни написали ─────

class TestMountKey:
    """Д-1: the absent `xy`, `(xy 0.0 0.0)` and `polar (0, …)` are ONE mount; a
    non-zero `xy` and a non-zero `polar` are NOT."""

    def test_every_spelling_of_a_zero_offset_gives_one_key(self):
        anchor = TreeAnchor(role="FPGA", anchor_sheet="FPGA", anchor_cluster="FPGA",
                            anchor_pad="117")
        keys = {
            mount_key(anchor),
            mount_key(anchor, xy=(0.0, 0.0)),
            mount_key(anchor, xy=[0, 0]),
            mount_key(anchor, polar=(0.0, 90.0)),
            mount_key(anchor, rotation=0.0),
        }
        assert len(keys) == 1, keys

    def test_absent_rotation_is_zero(self):
        anchor = TreeAnchor(role="FPGA", anchor_pad="1")
        assert mount_key(anchor) == mount_key(anchor, rotation=0.0)
        assert mount_key(anchor) != mount_key(anchor, rotation=90.0)

    def test_a_non_zero_xy_is_never_equated_with_a_non_zero_polar(self):
        """Turning one into the other needs the BASE's angle — that is a placement
        question, not a structural equality, so the key keeps them apart."""
        anchor = TreeAnchor(role="FPGA", anchor_pad="1")
        assert mount_key(anchor, xy=(2.0, 0.0)) != mount_key(anchor, polar=(2.0, 0.0))

    def test_the_anchor_identity_is_part_of_the_key(self):
        base = dict(role="FPGA", anchor_sheet="FPGA", anchor_cluster="FPGA",
                    anchor_pad="117")
        plain = mount_key(TreeAnchor(**base))
        assert plain != mount_key(TreeAnchor(**{**base, "anchor_pad": "118"}))
        assert plain != mount_key(TreeAnchor(**{**base, "anchor_sheet": "OTHER"}))
        assert plain != mount_key(TreeAnchor(**{**base, "anchor_cluster": "OTHER"}))

    def test_not_a_mount_address_at_all(self):
        assert mount_key(None) is None
        assert mount_key(TreeAnchor(role=None, anchor_pad="1")) is None

    def test_a_mount_node_with_written_zeros_equals_one_without_xy(self):
        """THE cell of Д-1: the profile's own mounts are written `(xy 0.0 0.0)`,
        a new node is written with no `xy` at all — and «уже есть» must see them
        as the same mount, or it stays silent exactly on the duplicate it exists
        for."""
        written = _mount("mnt_a", pad="117", xy=(0.0, 0.0))
        canon = _mount("mnt_b", pad="117")
        assert mount_key_of(written) == mount_key_of(canon)

    def test_mount_key_of_ignores_every_other_kind(self):
        record = TreeNode(ref="R1", kind="placement", xy=(0.0, 0.0), polar=None,
                          rotation=0.0, name=None, group=None, children=[])
        auto = TreeNode(ref="R2", kind=None, xy=None, polar=None, rotation=0.0,
                        name=None, group=None, children=[])
        assert mount_key_of(record) is None
        assert mount_key_of(auto) is None

    def test_the_config_dict_shape_gives_the_same_key_as_the_node_shape(self):
        """ONE rule, two shapes: the tool compares config DICTS, the dialog holds
        TreeNodes — a drift between the two halves is exactly how the duplicate
        would survive a cleanup."""
        node = _mount("mnt_a", pad="117", xy=(1.5, -2.0), rotation=90.0)
        as_dict = {"kind": "mount", "ref": "mnt_a",
                   "anchor": {"role": "FPGA", "sheet": "FPGA", "cluster": "FPGA",
                              "pad": "117"},
                   "xy": [1.5, -2.0], "rotation": 90.0}
        assert mount_key_of(node) == mount_key_of_dict(as_dict)

    def test_the_dict_shape_normalizes_zero_pads_of_other_types(self):
        """A hand-written config may hold `"pad": 7` (an int) while the anchor
        side is always a string — and `xy [0, 0]` while a new node has none."""
        node = _mount("m", role="R", pad="7", sheet=None, cluster=None)
        assert (mount_key_of_dict({"kind": "mount", "ref": "m",
                                   "anchor": {"role": "R", "pad": 7},
                                   "xy": [0, 0]}) == mount_key_of(node))

    def test_a_node_of_another_kind_has_no_dict_key(self):
        assert mount_key_of_dict({"kind": "placement", "ref": "R1"}) is None
        assert mount_key_of_dict({}) is None


class TestOccupiedPads:
    """Д6 «Уже есть»: in THIS tree, same anchor, zero offset."""

    def test_a_mount_with_no_offset_occupies_its_pad(self):
        tree = _tree(_mount("mnt_117", pad="117", xy=(0.0, 0.0)))
        assert occupied_pads(tree) == {mount_key_of(tree.nodes[0]): "mnt_117"}

    def test_a_mount_with_an_offset_occupies_nothing_here(self):
        tree = _tree(_mount("shifted", pad="117", xy=(1.0, 0.0)))
        assert len(occupied_pads(tree)) == 1
        address_key = mount_key(TreeAnchor(role="FPGA", anchor_sheet="FPGA",
                                           anchor_cluster="FPGA", anchor_pad="117"))
        assert address_key not in occupied_pads(tree)

    def test_the_scope_is_one_tree(self):
        """The same anchor in ANOTHER tree is a different point of reference —
        the duplicate rule has always been per-tree, and «уже есть» follows it."""
        other = _tree(_mount("mnt_117", pad="117"), name="other")
        mine = _tree(name="mine")
        assert occupied_pads(other) != {}
        assert occupied_pads(mine) == {}

    def test_nested_mounts_are_found(self):
        tree = _tree(_mount("parent", pad="1", children=[_mount("deep", pad="2")]))
        refs = set(occupied_pads(tree).values())
        assert refs == {"parent", "deep"}


# ── 2. строки таблицы ──────────────────────────────────────────────────────

class TestPadRows:
    def test_only_copper_pads_become_rows(self):
        rows = _rows(_pad("1", "/A"), _pad("2", "/B", copper=()))
        assert [row.pad for row in rows] == ["1"]

    def test_a_paste_only_pad_is_not_a_row_at_all(self):
        assert _rows(_pad("1", None, copper=())) == []

    def test_a_tabpin_is_one_row(self):
        """A repeated number is ONE row: the anchor addresses the NUMBER, so two
        physical pads of one number are one mount target (Д3)."""
        rows = _rows(_pad("9", "/A"), _pad("9", "/A"))
        assert len(rows) == 1
        assert rows[0].pads_with_this_number == 2
        assert rows[0].conflicting_nets == ()

    def test_a_tabpin_on_different_nets_says_so(self):
        """The one-row rule HIDES half the truth here; the row names it instead of
        pretending the first net is the whole story."""
        rows = _rows(_pad("9", "/A"), _pad("9", "/B"))
        assert len(rows) == 1
        assert rows[0].conflicting_nets == ("/A", "/B")

    def test_a_pad_without_a_net_keeps_an_empty_cell(self):
        rows = _rows(_pad("1", None, copper=(BoardLayer.BL_F_Cu,)))
        assert rows[0].net == ""

    def test_the_kicad_order_is_kept(self):
        assert [row.pad for row in _rows(_pad("3"), _pad("1"), _pad("2"))] == \
            ["3", "1", "2"]

    @pytest.mark.parametrize("raw,expected", [(1, "1"), (1.0, "1"), ("A3", "A3")])
    def test_a_pad_number_is_a_string(self, raw, expected):
        """kipy reports str/int/float depending on the pad; '1.0' would match no
        anchor and no config pad."""
        assert pad_number(raw) == expected


class TestNetFilter:
    """Д4: «Net contains…», case-insensitive, empty = everything."""

    def _rows(self):
        return [MountRow(pad="1", net="/FPGA/+2V5_VCCA"),
                MountRow(pad="2", net="GND"),
                MountRow(pad="3", net="unconnected-(IC1-IO_1-Pad1)")]

    def test_a_substring_matches_case_insensitively(self):
        assert [r.pad for r in filter_rows(self._rows(), "vcca")] == ["1"]
        assert [r.pad for r in filter_rows(self._rows(), "VCC")] == ["1"]

    def test_an_empty_needle_keeps_every_row(self):
        assert len(filter_rows(self._rows(), "   ")) == 3

    def test_no_match_is_an_empty_table(self):
        assert filter_rows(self._rows(), "nope") == []

    def test_unconnected_rows_are_narrowed_away_by_one_substring(self):
        """The live table of IC1 has 69 such rows (Т1) — this is the filter Denis
        will actually type."""
        assert filter_rows(self._rows(), "unconnected") == [self._rows()[2]]


# ── 3. имя строки ──────────────────────────────────────────────────────────

class TestMountName:
    def test_the_pad_placeholder_is_substituted(self):
        assert mount_name("fpga_vcca_{pad}", "107") == "fpga_vcca_107"

    def test_the_placeholder_may_appear_twice(self):
        assert mount_name("{pad}_of_{pad}", "3") == "3_of_3"

    def test_an_empty_template_names_the_row_by_its_pad(self):
        assert mount_name("", "107") == "107"
        assert mount_name("   ", "107") == "107"

    def test_an_unknown_placeholder_is_refused_in_words_about_a_mount_name(self):
        """Д5: the refusal must not send the user looking for a clone's `params`
        — this flow has no clone and no params mapping."""
        with pytest.raises(ValidationError) as info:
            mount_name("fpga_{net}", "107")
        text = str(info.value)
        assert "mount name" in text
        assert "{pad}" in text
        assert "clone_placement" not in text

    def test_the_net_name_is_never_substituted(self):
        """Denis, Д5: «+2v5_vcca всё название испортит» — the template has no net
        placeholder at all, and asking for one is the refusal above."""
        with pytest.raises(ValidationError):
            mount_name("fpga_{net}_{pad}", "107")


# ── 4. пачка: всё или ничего ───────────────────────────────────────────────

class TestPlanMountNodes:
    def _tree_with_a_record(self):
        record = TreeNode(ref="R_ONE", kind="placement", xy=(0.0, 0.0), polar=None,
                          rotation=0.0, name=None, group=None, children=[])
        return _tree(record)

    def test_only_the_ticked_rows_become_nodes(self):
        rows = _rows(_pad("1"), _pad("2"), _pad("3"))
        plan = plan_mount_nodes("vcca_{pad}", rows, selected=["1", "3"],
                                address=_ADDRESS, tree=self._tree_with_a_record())
        assert plan.ok, plan.refusals
        assert [node.ref for node in plan.nodes] == ["vcca_1", "vcca_3"]
        assert plan.names == [("1", "vcca_1"), ("3", "vcca_3")]

    def test_a_new_node_is_a_childless_mount_with_no_offset(self):
        """Д-2: no `xy`, no `rotation` — the canon the s-expr writer emits for
        defaults; the anchor is the role one, with the pad."""
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS, tree=_tree())
        node = plan.nodes[0]
        assert node.kind == "mount"
        assert node.children == []
        assert node.xy is None and node.polar is None and node.rotation == 0.0
        assert (node.anchor.role, node.anchor.anchor_sheet,
                node.anchor.anchor_cluster, node.anchor.anchor_pad) == \
            ("FPGA", "FPGA", "FPGA", "7")

    def test_a_hand_edited_name_wins_over_the_template(self):
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7"), _pad("8")),
                                selected=["7", "8"], address=_ADDRESS,
                                tree=_tree(), edited={"8": "my_own_name"})
        assert [node.ref for node in plan.nodes] == ["m_7", "my_own_name"]

    def test_nothing_ticked_writes_nothing(self):
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=[],
                                address=_ADDRESS, tree=_tree())
        assert plan.nodes == []
        assert len(plan.refusals) == 1

    def test_a_stale_table_is_refused(self):
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["9"],
                                address=_ADDRESS, tree=_tree())
        assert plan.nodes == []
        assert "9" in plan.refusals[0]

    def test_two_rows_with_one_name_write_nothing(self):
        """A template without `{pad}` over two pads is legal for ONE row and a
        duplicate for two — the common refusal catches it, no separate rule."""
        plan = plan_mount_nodes("vcca", _rows(_pad("7"), _pad("8")),
                                selected=["7", "8"], address=_ADDRESS, tree=_tree())
        assert plan.nodes == []
        assert "vcca" in plan.refusals[0]

    def test_a_template_without_a_placeholder_is_legal_for_one_row(self):
        plan = plan_mount_nodes("vcca", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS, tree=_tree())
        assert plan.ok, plan.refusals
        assert plan.nodes[0].ref == "vcca"

    def test_a_pad_that_already_carries_this_mount_is_refused(self):
        """Д6 behind the greying: even if the dialog lets the tick through, the
        batch must not write the duplicate this whole task exists for."""
        tree = _tree(_mount("mnt_7", pad="7"))
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS, tree=tree)
        assert plan.nodes == []
        assert "mnt_7" in plan.refusals[0]

    def test_a_name_colliding_with_a_positioned_node_is_refused_by_the_loader(self):
        """The mount-name rule is the LOADER's (unique in the tree, no collision
        with a positioned node) — the plan runs it instead of restating it."""
        plan = plan_mount_nodes("R_ONE", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS, tree=self._tree_with_a_record())
        assert plan.nodes == []
        assert "positioned node" in plan.refusals[0]

    def test_a_name_used_in_another_tree_is_not_this_tree_s_business(self):
        """Mount refs are LOCAL names: the same name in another tree is legal, so
        the plan must not refuse it (the forest-wide record rule does not apply)."""
        mine = _tree()
        plan = plan_mount_nodes("shared_name", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS, tree=mine)
        assert plan.ok, plan.refusals

    def test_an_address_without_a_role_is_refused(self):
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                                address=MountAddress(role=""), tree=_tree())
        assert plan.nodes == []
        assert "Role" in plan.refusals[0]

    def test_the_caller_s_tree_is_not_mutated(self):
        tree = self._tree_with_a_record()
        before = list(tree.nodes)
        plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                         address=_ADDRESS, tree=tree)
        assert tree.nodes == before

    def test_the_resulting_tree_loads(self):
        """The plan's own verdict comes from `tree_from_dict`; this is the other
        side of it — what it approved really does load."""
        from kicadstamp.trees import tree_from_dict, tree_to_dict
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7"), _pad("8")),
                                selected=["7", "8"], address=_ADDRESS,
                                tree=self._tree_with_a_record())
        assert plan.ok, plan.refusals
        tree = self._tree_with_a_record()
        tree.nodes.extend(plan.nodes)
        loaded = tree_from_dict(tree_to_dict(tree))
        assert [node.ref for node in loaded.nodes] == ["R_ONE", "m_7", "m_8"]

    # ── Т2.1(б): вердикт загрузчика спрашивается про ТО место, куда писать ──

    def _tree_with_a_copper_container(self):
        container = TreeNode(ref="copper", kind="copper", xy=None, polar=None,
                             rotation=0.0, name=None, group=None, children=[])
        return _tree(container)

    def test_a_parent_the_loader_refuses_writes_nothing(self):
        """Д1 hangs the mounts under a NODE when the command was called on one.
        Under a kind "copper" container the loader allows only net_trace children,
        so the plan must ask about THAT place — a top-level-only probe would have
        said "ok" about a tree the next Save cannot load (Т2.1(б))."""
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS,
                                tree=self._tree_with_a_copper_container(),
                                parent_ref="copper")
        assert plan.nodes == []
        assert "copper container" in plan.refusals[0]

    def test_the_same_batch_under_the_root_is_fine(self):
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS,
                                tree=self._tree_with_a_copper_container())
        assert plan.ok, plan.refusals

    def test_a_parent_that_does_not_exist_is_refused_in_words(self):
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS, tree=_tree(), parent_ref="nope")
        assert plan.nodes == []
        assert "nowhere to hang" in plan.refusals[0]

    def test_a_nested_parent_is_probed_on_a_copy_and_left_untouched(self):
        parent = TreeNode(ref="PARENT", kind="placement", xy=(0.0, 0.0), polar=None,
                          rotation=0.0, name=None, group=None, children=[])
        tree = _tree(parent)
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("7")), selected=["7"],
                                address=_ADDRESS, tree=tree, parent_ref="PARENT")
        assert plan.ok, plan.refusals
        assert parent.children == [], "the caller's tree must not be written by a plan"
        assert tree.nodes == [parent]

    # ── Т2.1(в): якорь, написанный иначе, ловится картой экземпляра ────────

    def test_an_anchor_spelled_differently_is_caught_by_the_occupied_map(self):
        """`mount_key` is structural (the offline tool of Т3 has no board), so
        `(role FPGA)(pad 1)` and `(role FPGA)(sheet FPGA)(cluster FPGA)(pad 1)` are
        two keys — while on a board with one FPGA both are pad 1 of IC1. Д1а
        pre-fills Sheet/Cluster, so a NEW mount is written in full while older ones
        may be shorter; the instance-aware map is what closes that gap."""
        tree = _tree(_mount("old", pad="1"))          # full spelling: sheet+cluster
        address = MountAddress(role="FPGA")           # short spelling: role only
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("1")), selected=["1"],
                                address=address, tree=tree, occupied={"1": "old"})
        assert plan.nodes == []
        assert "old" in plan.refusals[0]

    def test_without_the_occupied_map_the_structural_key_does_not_pretend(self):
        """The other half of the same rule: the key must NOT silently equate the
        two spellings — only the live-resolved map may."""
        tree = _tree(_mount("old", pad="1"))
        plan = plan_mount_nodes("m_{pad}", _rows(_pad("1")), selected=["1"],
                                address=MountAddress(role="FPGA"), tree=tree)
        assert plan.ok, plan.refusals


class TestBrokenTemplateIsARefusal:
    """Т2.1(а): the name field is typed LIVE, so every half-written template on the
    way to `fpga_{pad}` must be a refusal in words — never a raw
    ValueError/IndexError/AttributeError out of `str.format`."""

    BROKEN = ["fpga_{", "fpga_}", "fpga_{}", "fpga_{0}", "fpga_{pad.x}"]

    @pytest.mark.parametrize("template", BROKEN)
    def test_mount_name_refuses_instead_of_crashing(self, template):
        with pytest.raises(ValidationError) as info:
            mount_name(template, "107")
        text = str(info.value)
        assert template in text
        assert "cannot be read as a template" in text

    @pytest.mark.parametrize("template", BROKEN)
    def test_the_config_template_owner_refuses_the_same_way(self, template):
        """The rule lives in the ONE owner, so a config template (a cell's net)
        gets the same answer — no second guard, no different exception."""
        with pytest.raises(ValidationError) as info:
            resolve_net(template, {"pad": "1"}, {})
        assert "cannot be read as a template" in str(info.value)

    def test_a_well_formed_template_is_untouched(self):
        assert mount_name("fpga_{pad}_x", "107") == "fpga_107_x"
        assert resolve_net("net_{pad}", {"pad": "1"}, {}) == "net_1"

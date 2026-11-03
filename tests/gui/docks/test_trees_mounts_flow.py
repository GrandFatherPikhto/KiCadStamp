# tests/gui/docks/test_trees_mounts_flow.py
"""Т4 of plan_2026_11_03_add_mounts_table — the mounts table's READS and WRITE.

What these cells own, and why each one exists:

  * `resolve_instance` — Д2's "which instance": a Ref is only a way to FIND it
    (Role/Cluster from the footprint's fields, the sheet from its own chain), and
    a footprint without a Role is a refusal IN WORDS because the anchor written to
    the file is always the role one. The role path is the resolver's own, so its
    «уточни Sheet» wording is not re-invented here.
  * `occupied_for_instance` — the instance-aware «уже есть» map (Т2.1(в)): a mount
    standing on its pad, of the same Role, whose anchor resolves to THIS footprint.
    A mount of another Role is skipped WITHOUT a resolve — that skip is the whole
    cost claim of the door sign (one resolve per same-role mount, ~3 ms for 25).
  * `start_read_pads` — phase 1 through the door: `socket_busy` first (start_long_op
    does not refuse on a busy socket), then `start_long_op` with the CONNECTION in
    the payload, never an adapter (see the module docstring).
  * `write_plan` — the nodes go exactly where the plan proved them (Д1/Т2.1(б)),
    one `_mark_dirty()` for the batch, and a refused plan writes NOTHING (Д7).

The board is a plain double: a `domain.board.Footprint` per component (the real
dataclass, so the sheet chain is read by the project's ONE reader), an adapter
that answers the three questions `resolve_instance` asks, and an injected
resolver double for the role path — no kipy, no socket, no window.
"""
from types import SimpleNamespace

import pytest

from gui.docks import trees_mounts_flow
from kicadstamp.domain.board import BoardLayer, Footprint, Pad
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _
from kicadstamp.tree_mounts import MountAddress, pad_rows, plan_mount_nodes
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

_ORIGIN = Vector2.from_xy(0, 0)
_SHEET_UUID = "sheet-fpga"
_SYMBOL_UUID = "symbol-ic1"


def _fp(ref, chain=()):
    return Footprint(ref=ref, uuid=f"uuid-{ref}", position=_ORIGIN, angle_deg=0.0,
                     layer=BoardLayer.BL_F_Cu, sheet_path_uuids=tuple(chain))


class _Adapter:
    """The three questions `resolve_instance` asks a live adapter, and nothing
    else — a board double with no IPC at all."""

    def __init__(self, entries):
        self._footprints = {}
        self._fields = {}
        self._pads = {}
        for ref, spec in entries.items():
            chain = spec.get("chain", ())
            self._footprints[ref] = _fp(ref, chain)
            self._fields[ref] = {"Role": spec.get("role"),
                                 "Cluster": spec.get("cluster")}
            self._pads[ref] = list(spec.get("pads", ()))

    def get_footprint(self, ref):
        return self._footprints.get(ref)

    def get_field_value(self, footprint, field_name):
        return self._fields[footprint.ref].get(field_name)

    def get_footprint_pads(self, footprint):
        return list(self._pads[footprint.ref])


class _Resolver:
    """`resolve_anchor_fp` by (role, sheet, cluster) — records every call, so a
    cell can assert WHICH anchors were resolved (the cost claim) and not only the
    answer."""

    def __init__(self, answers):
        self._answers = answers          # (role, sheet, cluster) -> Footprint
        self.calls = []

    def resolve_anchor_fp(self, anchor_ref, role, sheet, cluster, label=""):
        self.calls.append((role, sheet, cluster))
        answer = self._answers.get((role, sheet, cluster))
        if answer is None:
            raise ValidationError(format_fatal_error(
                _("anchor role {role!r} not found").format(role=role), []))
        return answer


def _mount(ref, pad, *, role="FPGA", sheet="FPGA", cluster="FPGA",
           xy=None, rotation=0.0):
    return TreeNode(ref=ref, kind="mount", xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=[],
                    anchor=TreeAnchor(role=role, is_origin=False,
                                      anchor_sheet=sheet, anchor_cluster=cluster,
                                      anchor_pad=pad))


def _tree(*nodes, name="fpga"):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=list(nodes))


# ── Д2: which instance ─────────────────────────────────────────────────────

class TestResolveInstance:
    def test_a_ref_is_only_a_way_to_find_the_instance(self):
        """The Role/Cluster come from the footprint's fields and the sheet from its
        own hierarchical chain — the resolve of the REF path never consults a
        resolver, because the anchor it writes is built from these values."""
        adapter = _Adapter({"IC1": {"role": "FPGA", "cluster": "FPGA",
                                    "chain": (_SHEET_UUID, _SYMBOL_UUID)}})
        resolver = _Resolver({})
        found = trees_mounts_flow.resolve_instance(
            adapter, None, {_SHEET_UUID: "FPGA"}, ref="IC1", resolver=resolver)
        assert (found["ref"], found["role"], found["cluster"], found["sheet"]) == \
            ("IC1", "FPGA", "FPGA", "FPGA")
        assert resolver.calls == []

    def test_a_ref_without_a_role_is_refused_in_words(self):
        """The anchor grammar has no ref anchor for a mount, so a footprint without
        a Role cannot be addressed by the mounts table at all."""
        adapter = _Adapter({"R7": {"role": None, "cluster": "X"}})
        with pytest.raises(ValidationError) as info:
            trees_mounts_flow.resolve_instance(adapter, None, {}, ref="R7",
                                               resolver=_Resolver({}))
        text = str(info.value)
        assert "addressed by a Role" in text
        assert "R7" in text

    def test_a_role_anchor_goes_through_the_resolver_the_redraw_uses(self):
        fp = _fp("IC1")
        adapter = _Adapter({"IC1": {"role": "FPGA", "cluster": "FPGA"}})
        resolver = _Resolver({("FPGA", "FPGA", "FPGA"): fp})
        found = trees_mounts_flow.resolve_instance(
            adapter, None, {}, role="FPGA", cluster="FPGA", sheet="FPGA",
            resolver=resolver)
        assert found["ref"] == "IC1"
        assert resolver.calls == [("FPGA", "FPGA", "FPGA")]

    def test_the_resolvers_own_refusal_reaches_the_caller_unchanged(self):
        """«уточни Sheet» is `role_refusals`' wording — this module must not
        restate it."""
        adapter = _Adapter({})
        with pytest.raises(ValidationError) as info:
            trees_mounts_flow.resolve_instance(
                adapter, None, {}, role="OP_AMP", resolver=_Resolver({}))
        assert "not found" in str(info.value)

    def test_an_empty_request_is_a_refusal(self):
        with pytest.raises(ValidationError):
            trees_mounts_flow.resolve_instance(_Adapter({}), None, {},
                                               resolver=_Resolver({}))


# ── Д6: «уже есть» по экземпляру ──────────────────────────────────────────

class TestOccupiedForInstance:
    def _adapter(self):
        return _Adapter({"IC1": {"role": "FPGA", "cluster": "FPGA"},
                         "IC2": {"role": "FPGA", "cluster": "FPGA"}})

    def test_a_mount_standing_on_its_pad_of_this_instance_occupies_it(self):
        tree = _tree(_mount("mnt_117", "117"),
                     _mount("mnt_5", "5", xy=(1.0, 0.0)),          # moved: free
                     _mount("other", "9", role="MCU"),             # other role
                     _mount("other_instance", "3"))                # resolves elsewhere
        ic1, ic2 = _fp("IC1"), _fp("IC2")
        resolver = _Resolver({("FPGA", "FPGA", "FPGA"): ic1})

        # The channel case: the same role on another instance is a different pad set.
        def answer(anchor_ref, role, sheet, cluster, label=""):
            resolver.calls.append((label, role, sheet, cluster))
            return ic2 if label == "other_instance" else ic1
        resolver.resolve_anchor_fp = answer

        occupied = trees_mounts_flow.occupied_for_instance(
            self._adapter(), None, {}, tree, role="FPGA", instance_ref="IC1",
            resolver=resolver)
        assert occupied == {"117": "mnt_117"}
        assert [call[0] for call in resolver.calls] == ["mnt_117", "other_instance"], \
            "a mount of another Role must be skipped WITHOUT a resolve"

    def test_an_unresolvable_mount_is_skipped_not_fatal(self):
        """This map only greys rows; a stale anchor must not break the read."""
        tree = _tree(_mount("mnt_117", "117"))
        occupied = trees_mounts_flow.occupied_for_instance(
            _Adapter({}), None, {}, tree, role="FPGA", instance_ref="IC1",
            resolver=_Resolver({}))
        assert occupied == {}

    def test_a_mount_without_a_pad_cannot_occupy_a_pad(self):
        tree = _tree(_mount("no_pad", None))
        occupied = trees_mounts_flow.occupied_for_instance(
            _Adapter({}), None, {}, tree, role="FPGA", instance_ref="IC1",
            resolver=_Resolver({}))
        assert occupied == {}


# ── the read: rows + the map, plain data ──────────────────────────────────

class TestReadPads:
    def _adapter(self):
        return _Adapter({"IC1": {"role": "FPGA", "cluster": "FPGA", "pads": [
            Pad(number="1", net_name="/FPGA/+2V5_VCCA", position=_ORIGIN,
                copper_layers=(BoardLayer.BL_F_Cu,)),
            Pad(number="2", net_name=None, position=_ORIGIN, copper_layers=()),
        ]}})

    def test_the_rows_come_from_the_one_row_rule(self):
        result = trees_mounts_flow.read_pads(
            self._adapter(), None, {}, _tree(), ref="IC1",
            resolver=_Resolver({}))
        assert [row.pad for row in result["rows"]] == ["1"]
        assert result["rows"] == pad_rows(self._adapter().get_footprint_pads(
            self._adapter().get_footprint("IC1")))
        # An EMPTY sheet chain is the ROOT sheet, not "unknown": `sheet_of_names`
        # owns that reading (a component standing on the root sheet is addressed
        # by "/"), and the table simply carries what it answers.
        assert result["instance"] == {"ref": "IC1", "role": "FPGA",
                                      "cluster": "FPGA", "sheet": "/"}

    def test_the_payload_carries_no_footprint_object(self):
        result = trees_mounts_flow.read_pads(
            self._adapter(), None, {}, _tree(), ref="IC1",
            resolver=_Resolver({}))
        assert "footprint" not in result["instance"]


class TestWorker:
    def test_the_worker_reads_the_adapter_from_the_connection(self):
        """The door's rule: the payload carries the CONNECTION, and the adapter is
        taken on the worker thread (a fresh one would not even answer the resolve —
        see the module docstring)."""
        adapter = _Adapter({"IC1": {"role": "FPGA", "cluster": "FPGA",
                                    "pads": [Pad(number="1", net_name="GND",
                                                 position=_ORIGIN)]}})
        payload = {"connection": SimpleNamespace(board=SimpleNamespace(adapter=adapter)),
                   "cfg": None, "sheet_names": {}, "tree": _tree(), "ref": "IC1",
                   "resolver": _Resolver({})}
        result = trees_mounts_flow.run_read_pads_worker(payload)
        assert [row.pad for row in result["rows"]] == ["1"]

    def test_no_board_is_a_refusal_in_words(self):
        payload = {"connection": SimpleNamespace(board=None), "cfg": None,
                   "sheet_names": {}, "tree": _tree(), "ref": "IC1"}
        with pytest.raises(ValidationError) as info:
            trees_mounts_flow.run_read_pads_worker(payload)
        assert "no board connection" in str(info.value)


class TestStartReadPads:
    def test_a_busy_socket_starts_nothing(self, monkeypatch):
        import gui.worker as worker_mod

        started = []
        monkeypatch.setattr(worker_mod, "socket_busy", lambda connection: True)
        monkeypatch.setattr(worker_mod, "start_long_op",
                            lambda *a, **k: started.append(a) or object())
        controller = trees_mounts_flow.start_read_pads(
            SimpleNamespace(), cfg=None, sheet_names={}, tree=_tree(),
            ref="IC1", on_ready=lambda _result: None)
        assert controller is None
        assert started == []

    def test_the_read_starts_on_the_worker_with_the_connection(self, monkeypatch):
        import gui.worker as worker_mod

        captured = {}
        controller = object()

        def fake_start(connection, widgets, fn, on_success, on_error, *args, **kwargs):
            captured.update(connection=connection, widgets=widgets, fn=fn,
                            on_success=on_success, args=args, kwargs=kwargs)
            return controller

        monkeypatch.setattr(worker_mod, "socket_busy", lambda connection: False)
        monkeypatch.setattr(worker_mod, "start_long_op", fake_start)
        connection = SimpleNamespace()
        tree = _tree()
        ready = []
        assert trees_mounts_flow.start_read_pads(
            connection, cfg="CFG", sheet_names={"u": "s"}, tree=tree, ref="IC1",
            widgets=("BUTTON",), on_ready=ready.append) is controller
        assert captured["connection"] is connection
        assert captured["fn"] is trees_mounts_flow.run_read_pads_worker
        payload = captured["args"][0]
        assert payload["connection"] is connection
        assert payload["tree"] is tree
        assert "adapter" not in payload, "the door hands the CONNECTION, not an adapter"
        assert captured["widgets"] == ("BUTTON",)


# ── the write ─────────────────────────────────────────────────────────────

class _Dock:
    def __init__(self):
        self.dirty = 0
        self.rebuilt = 0

    def _mark_dirty(self):
        self.dirty += 1

    def _rebuild_tabs(self):
        self.rebuilt += 1


class TestWritePlan:
    def _plan(self, tree, *, selected=("117",)):
        pads = [Pad(number="117", net_name="GND", position=_ORIGIN),
                Pad(number="130", net_name="GND", position=_ORIGIN)]
        return plan_mount_nodes("vccio_{pad}", pad_rows(pads), selected=selected,
                                address=MountAddress(role="FPGA"),
                                tree=tree)

    def test_the_nodes_go_under_the_parent_the_plan_was_checked_against(self):
        """Д1: a command called on a NODE hangs its mounts under that node — and the
        plan's loader verdict was asked about exactly that place (Т2.1(б))."""
        parent = TreeNode(ref="PARENT", kind="placement", xy=(0.0, 0.0), polar=None,
                          rotation=0.0, name=None, group=None, children=[])
        tree = _tree(parent)
        plan = self._plan(tree)
        dock = _Dock()
        assert trees_mounts_flow.write_plan(dock, tree, parent, plan) == 1
        assert [node.ref for node in parent.children] == ["vccio_117"]
        assert tree.nodes == [parent]
        assert (dock.dirty, dock.rebuilt) == (1, 1)

    def test_at_the_root_the_parent_is_None(self):
        tree = _tree()
        plan = self._plan(tree)
        dock = _Dock()
        assert trees_mounts_flow.write_plan(dock, tree, None, plan) == 1
        assert [node.ref for node in tree.nodes] == ["vccio_117"]

    def test_a_refused_plan_writes_nothing_and_stages_nothing(self):
        """Д7 «всё или ничего»: the refusal happens BEFORE the dock is touched."""
        tree = _tree()
        plan = plan_mount_nodes("one_name", pad_rows(
            [Pad(number="117", net_name="GND", position=_ORIGIN),
             Pad(number="130", net_name="GND", position=_ORIGIN)]),
            selected=["117", "130"], address=MountAddress(role="FPGA"), tree=tree)
        assert not plan.ok and plan.nodes == []
        dock = _Dock()
        assert trees_mounts_flow.write_plan(dock, tree, None, plan) == 0
        assert tree.nodes == []
        assert (dock.dirty, dock.rebuilt) == (0, 0)

    def test_a_whole_batch_costs_one_stage(self):
        tree = _tree()
        plan = self._plan(tree, selected=("117", "130"))
        dock = _Dock()
        assert trees_mounts_flow.write_plan(dock, tree, None, plan) == 2
        assert dock.dirty == 1, "N nodes, ONE _mark_dirty (the stage serialises the tree)"


def test_the_module_is_qt_free_at_import():
    """The reading half is plain data in / plain data out — a widget import at
    module level would drag Qt into the worker's process and break these cells."""
    import ast
    from pathlib import Path
    source = Path(trees_mounts_flow.__file__).read_text(encoding="utf-8")
    module_level = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_level.append(ast.get_source_segment(source, node))
    assert not [line for line in module_level if "PyQt" in line or "worker import" in line], \
        module_level

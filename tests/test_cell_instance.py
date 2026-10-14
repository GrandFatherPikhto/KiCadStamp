# tests/test_cell_instance.py
"""Tests for the cell-instance rule now owned by kicadstamp/cell_instance.py
(2026-10-05, plan_2026_10_05_explode_r1_core.md §1).

resolve_context_footprints turns a remembered (Cluster, Sheet) hint into the
live board footprints, through the SAME role_narrowing cascade the project uses
for (Sheet, Cluster) addressing — the CLUSTER gate by cluster_prefix_match, the
SHEET step delegating to narrow_candidates_by_sheet only when it reduces the
set. Moved here together with the function out of
tests/gui/test_cell_edit_context.py, and the monkeypatch target moved with it
(kicadstamp.cell_instance, not gui.cell_edit_context).
"""
import kicadstamp.cell_instance as ci_mod

from kicadstamp.cell_instance import resolve_context_footprints
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Vector2


def _fp(uuid, ref):
    return Footprint(ref=ref, uuid=uuid, position=Vector2.from_xy(0, 0),
                     angle_deg=0.0, layer="F.Cu")


class _FakeAdapter:
    """Duck-typed adapter: in-memory footprints with Cluster field values."""

    def __init__(self, footprints=None):
        self.footprints = footprints or []
        self.field_values = {}

    def set_field(self, fp, name, value):
        self.field_values[(getattr(fp, "uuid", None), name)] = value

    def get_field_value(self, fp, field_name):
        return self.field_values.get((getattr(fp, "uuid", None), field_name))


def _cluster_adapter(cluster):
    """An adapter whose two footprints carry `cluster`, plus one foreign
    footprint on another cluster."""
    a = _fp("fp1", "R1")
    b = _fp("fp2", "R2")
    foreign = _fp("fp3", "U1")
    adapter = _FakeAdapter([a, b, foreign])
    for fp in (a, b):
        adapter.set_field(fp, CLUSTER_FIELD_NAME, cluster)
    adapter.set_field(foreign, CLUSTER_FIELD_NAME, "AD_DAC/IC2")
    return adapter


def test_resolve_context_footprints_cluster_gate():
    """Without a Sheet the context resolves to every footprint of the cluster
    tag (cluster_prefix_match); a cluster absent from the board -> [] (stale),
    never an exception."""
    adapter = _cluster_adapter("PIF_3V3_VDD")
    got = resolve_context_footprints(
        adapter, adapter.footprints, "PIF_3V3_VDD", None, {})
    assert {fp.uuid for fp in got} == {"fp1", "fp2"}
    assert resolve_context_footprints(
        adapter, adapter.footprints, "GHOST", None, {}) == []


def test_resolve_context_footprints_sheet_narrowing_delegates(monkeypatch):
    """The Sheet step delegates to role_narrowing.narrow_candidates_by_sheet
    (the project's ONE (Sheet, Cluster) addressing cascade — no second
    terminology) and applies its result only when it reduces the set."""
    adapter = _cluster_adapter("PIF_3V3_VDD")
    called = []

    def _fake_narrow(candidates, sheet, sheet_names):
        called.append(sheet)
        return [candidates[0]]          # "narrowed" to R1's footprint

    monkeypatch.setattr(ci_mod, "narrow_candidates_by_sheet", _fake_narrow)
    got = resolve_context_footprints(
        adapter, adapter.footprints, "PIF_3V3_VDD", "FPGA", {"x": "y"})
    assert called == ["FPGA"]
    assert [fp.uuid for fp in got] == ["fp1"]


def _two_sheet_roots_adapter(cluster):
    """One cluster tag standing on the ROOT sheet AND on a sub-sheet — the case
    the (Cluster, Sheet) instance addressing must never merge.

    The chain convention is the project's own: the LAST uuid of a footprint's
    path is the symbol itself (resolve_sheet_path_names drops it), so ONE hop =
    the root sheet and TWO hops = a named sub-sheet."""
    root = _fp("fp-root", "H1")
    root.sheet_path_uuids = ("h1-own",)
    sub = _fp("fp-sub", "H2")
    sub.sheet_path_uuids = ("mcu", "h2-own")
    adapter = _FakeAdapter([root, sub])
    for fp in (root, sub):
        adapter.set_field(fp, CLUSTER_FIELD_NAME, cluster)
    return adapter


def test_a_root_request_clamps_to_the_root_components_only():
    """ROOT_SHEET ("/") asks for the components standing on the ROOT sheet of the
    hierarchy. A same-tagged sub-sheet component is NOT part of that instance —
    that is the narrowing the extracted root Entity's own `sheet "/"` carries
    (plan_2026_10_13_root_sheet_clusters Ч1), so the root instance can never
    swallow (or be swallowed by) the sub-sheet one."""
    from kicadstamp.sheet_names import ROOT_SHEET

    adapter = _two_sheet_roots_adapter("MOUNTING_HOLES")
    sheet_names = {"mcu": "MCU"}

    got = resolve_context_footprints(
        adapter, adapter.footprints, "MOUNTING_HOLES", ROOT_SHEET, sheet_names)
    assert [fp.uuid for fp in got] == ["fp-root"]

    # ...and the mirror image: the sub-sheet instance takes only its own part.
    got = resolve_context_footprints(
        adapter, adapter.footprints, "MOUNTING_HOLES", "MCU", sheet_names)
    assert [fp.uuid for fp in got] == ["fp-sub"]


def test_no_sheet_at_all_still_narrows_nothing():
    """The pre-existing convention, kept: an ABSENT sheet (a record that never
    stored one) is not a wildcard and not the root — it just does not narrow."""
    adapter = _two_sheet_roots_adapter("MOUNTING_HOLES")
    got = resolve_context_footprints(
        adapter, adapter.footprints, "MOUNTING_HOLES", None, {"mcu": "MCU"})
    assert {fp.uuid for fp in got} == {"fp-root", "fp-sub"}

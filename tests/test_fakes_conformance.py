# tests/test_fakes_conformance.py
"""The correspondence cell for tests/fakes/ (Ф1.4 of the tests refactor plan).

The plan's warning this file exists for: "Without the correspondence cell a
shared fake lies to twenty files at once." A shared fake is a stand-in for a
REAL interface, so every method it carries must be one the interface (or the
concrete implementation) actually has — a fake that invents a method makes the
tests that use it pin behaviour no production caller can reach.

Both directions are checked, and both are pinned to a written-down reason:
  * a fake invents nothing: its public surface is the seam plus the named gaps;
  * a name in the gap list is a REAL gap: it exists on the concrete adapter.
That second half is what stops SEAM_GAPS from becoming a place to dump any
method a fake happens to grow.
"""
import inspect

import pytest

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.kicad.interfaces import IBoardAdapter
from kicadstamp.placement.planner import PlacementPlanner
from kicadstamp.placement.services.component_resolver import ComponentResolver

from tests.fakes.adapter import FakeAdapter, public_callables
from tests.fakes.board import FakeBoardLayers, FakeBoardOverlay
from tests.fakes.pipeline import PipelineStubLifetime
from tests.fakes.planner import FakePlanner
from tests.fakes.resolver import FakeComponentResolver


def test_fake_adapter_public_surface_is_the_seam_plus_the_named_gaps():
    fake = public_callables(FakeAdapter)
    seam = public_callables(IBoardAdapter)
    # Rule 38: a scan that found nothing must fail, not pass silently.
    assert seam, "public_callables(IBoardAdapter) found nothing — the scan went blind"
    assert fake, "public_callables(FakeAdapter) found nothing — the scan went blind"

    allowed = set(FakeAdapter.SEAM_GAPS) | set(FakeAdapter.FAKE_ONLY)
    extras = fake - seam
    assert extras == allowed, (
        f"FakeAdapter carries {sorted(extras)} beyond the seam; the only names "
        f"allowed there are SEAM_GAPS={sorted(FakeAdapter.SEAM_GAPS)} (real "
        f"adapter methods the ABC omits) and FAKE_ONLY="
        f"{sorted(FakeAdapter.FAKE_ONLY)} (tests-only helpers). Add the method to "
        f"IBoardAdapter, or list it in the right one of the two.")
    assert not (allowed - extras), (
        "a declared extra is not defined on FakeAdapter — the list has rotted")


def test_every_named_seam_gap_exists_on_the_concrete_adapter():
    concrete = public_callables(KiCadBoardAdapter)
    assert concrete, "KiCadBoardAdapter has no public callables — scan went blind"
    missing = set(FakeAdapter.SEAM_GAPS) - concrete
    assert not missing, (
        f"SEAM_GAPS names {sorted(missing)}, which the concrete adapter does not "
        f"have either — those are invented by the fake, not gaps in the seam")


def test_net_combo_doubles_match_their_real_pairs():
    """The net-combo doubles: the adapter stand-in must be a seam read, and the
    DTO stand-in must carry the one field the consumers read."""
    from dataclasses import fields

    from kicadstamp.domain.board import Net

    from tests.fakes.nets import FakeNet, FakeNetAdapter

    surface = public_callables(FakeNetAdapter)
    assert surface, "FakeNetAdapter has no public callables — blind scan"
    assert surface == {"get_all_nets"}, (
        f"FakeNetAdapter carries {sorted(surface)}; the collector reads only "
        f"get_all_nets()")
    assert surface <= public_callables(IBoardAdapter), (
        "get_all_nets is not declared on the seam")

    assert FakeNet("+3V3").name == "+3V3"
    assert "name" in {f.name for f in fields(Net)}, (
        "the consumers read net.name, so the real domain Net must carry it")


def test_sheet_path_doubles_mirror_the_kipy_attribute_names():
    """These doubles stand in for KIPY's KIID_PATH/KIID by ATTRIBUTE NAME, so the
    names the consumers read are pinned here. Kipy itself is deliberately NOT
    imported: a whole-file kipy import in this process would break the cells that
    assert the seam does not pull kipy (they read sys.modules)."""
    from tests.fakes.sheet_path import (FakePath, FakeSheetPathFootprint,
                                        FakeUuid)

    assert FakeUuid("u").value == "u"
    assert [u.value for u in FakePath(["a", "b"]).path] == ["a", "b"]
    fp = FakeSheetPathFootprint(["a", "b"])
    assert [u.value for u in fp.sheet_path.path] == ["a", "b"], (
        "the consumers read fp.sheet_path.path[].value — the double must keep "
        "that shape")


def test_fake_board_surfaces_match_the_seam_layer_reads():
    """Both board families stand in for the board reads the seam declares, so
    neither may invent a method — except the one the seam deliberately omits."""
    seam = public_callables(IBoardAdapter)
    assert seam, "public_callables(IBoardAdapter) found nothing — blind scan"
    for cls in (FakeBoardOverlay, FakeBoardLayers):
        surface = public_callables(cls)
        assert surface, f"{cls.__name__} has no public callables — blind scan"
        allowed = set(getattr(cls, "FAKE_ONLY", ()))
        invented = surface - seam
        assert invented == allowed, (
            f"{cls.__name__} carries {sorted(invented)} beyond the seam's layer "
            f"reads; only {sorted(allowed)} may (get_copper_layer_count is read by "
            f"the tests and by diagnostics/probe_board_copper_layers.py, not by "
            f"gui/ or kicadstamp/)")
        assert not (allowed - invented), f"{cls.__name__}.FAKE_ONLY has rotted"


def test_pipeline_stub_matches_the_real_apply_pipeline_lifetime():
    """The stub stands in for ApplyPipeline AS A CONTEXT MANAGER, so the real
    class must actually be one — otherwise the stub would pin a protocol
    production does not have."""
    for name in ("close", "__enter__", "__exit__"):
        assert callable(getattr(ApplyPipeline, name, None)), (
            f"ApplyPipeline has no {name}() — PipelineStubLifetime stands in for a "
            f"protocol production does not implement")
    # Rule 38: the stub must actually DO the thing, not merely exist.
    #
    # A THROWAWAY SUBCLASS, not PipelineStubLifetime itself: close() writes the
    # counter on `type(self)`, so incrementing the BASE would be inherited by
    # every stand-in subclass in the session and their `closed == 1` cells would
    # read 2. Learned the hard way here (four gui files went red on exactly
    # that) — the shared base must stay 0.
    class _Probe(PipelineStubLifetime):
        pass

    before = _Probe.closed
    with _Probe():
        pass
    assert _Probe.closed == before + 1, "exiting the stub did not count a close()"
    assert PipelineStubLifetime.closed == 0, (
        "the shared base counter must stay 0 — a non-zero base leaks into every "
        "counting subclass that has not set its own yet")


def test_a_fake_only_method_is_not_a_production_method():
    """FAKE_ONLY is for helpers the tests need, so none of them may exist on the
    real adapter — otherwise a test would be pinning a method production has, in
    the wrong list. If production ever grows one of these names, this cell forces
    the decision instead of letting the lists drift."""
    concrete = public_callables(KiCadBoardAdapter)
    seam = public_callables(IBoardAdapter)
    collisions = set(FakeAdapter.FAKE_ONLY) & (concrete | seam)
    assert not collisions, (
        f"{sorted(collisions)} is listed as FAKE_ONLY but exists on the real "
        f"adapter or the seam — move it to SEAM_GAPS (or the ABC) instead")


def test_fake_component_resolver_mirrors_the_real_signatures():
    """Both signatures in tests/fakes/resolver.py have drifted once already (Ф1.4d-2),
    so this cell compares PARAMETER LISTS rather than method names: a rename, or a
    new keyword-only argument, must break it instead of being absorbed by a **kwargs."""

    def shape(fn):
        return [(p.name, p.kind, p.default is inspect.Parameter.empty)
                for p in inspect.signature(fn).parameters.values()
                if p.name != "self"]

    real_ctor = shape(ComponentResolver.__init__)
    fake_ctor = shape(FakeComponentResolver.__init__)
    # Rule 38: a comparison that scanned nothing must FAIL, not pass quietly.
    assert real_ctor, "ComponentResolver.__init__ has no parameters — blind scan"
    assert fake_ctor, "FakeComponentResolver.__init__ has no parameters — blind scan"
    assert fake_ctor == real_ctor, (
        f"FakeComponentResolver.__init__ takes {fake_ctor}, the real resolver takes "
        f"{real_ctor}; the keyword-only `snapshot` (Т2-4а) is exactly why that "
        f"parameter must be NAMED here and not swallowed by a **kwargs")

    real_resolve = shape(ComponentResolver.resolve_anchor_fp)
    fake_resolve = shape(FakeComponentResolver.resolve_anchor_fp)
    assert real_resolve, "resolve_anchor_fp has no parameters — blind scan"
    assert fake_resolve == real_resolve, (
        f"FakeComponentResolver.resolve_anchor_fp takes {fake_resolve}, the real one "
        f"takes {real_resolve}")

    assert not hasattr(ComponentResolver, "RESOLVED_FP_FACTORY"), (
        "RESOLVED_FP_FACTORY is the fake-only injection hook; the real resolver "
        "must not grow it")


def test_the_resolver_base_hands_back_a_fresh_footprint():
    """The hook is a FACTORY because two calls must not share the object they hand
    back — each of the six copies called `_FakeFp()` per call. Also pins the two
    things a caller reads back: the real attribute names, and the `snapshot` keyword."""

    class _Fp:
        position = "pos"

    class _Local(FakeComponentResolver):
        RESOLVED_FP_FACTORY = _Fp

    resolver = _Local("adapter", "cfg", {"S": "Sheet"}, snapshot="SNAP")
    first = resolver.resolve_anchor_fp(None, "R", "Sheet", "C", label="L")
    second = resolver.resolve_anchor_fp(None, "R", "Sheet", "C", label="L")

    assert isinstance(first, _Fp) and isinstance(second, _Fp), (
        "the factory must be CALLED, not returned")
    assert first is not second, "the resolver handed back a SHARED footprint double"
    assert (resolver.adapter, resolver.cfg, resolver.sheet_names) == \
        ("adapter", "cfg", {"S": "Sheet"}), (
            "the attribute names mirror the real __init__ (cfg, not config)")
    assert resolver.snapshot == "SNAP", (
        "the snapshot keyword must be accepted and kept — it is the Т2-4а drift")


def test_the_resolver_base_is_inert_without_a_footprint_factory():
    """A subclass that forgets RESOLVED_FP_FACTORY must fail LOUDLY, not hand back
    None and leave the cell under test failing with a puzzling AttributeError."""
    with pytest.raises(TypeError):
        FakeComponentResolver("a", "c", {}).resolve_anchor_fp(None, "R", None, None)


def test_fake_planner_invents_nothing_and_mirrors_the_real_parameters():
    """gui/docks/placer.py replaces the planner with this fake CLASS-WIDE, so every
    name production calls must exist here with the real parameters — and the fake may
    add none of its own, or it would pin a surface production cannot reach."""
    real_surface = public_callables(PlacementPlanner)
    fake_surface = public_callables(FakePlanner)
    assert real_surface, "PlacementPlanner has no public callables — blind scan"
    assert fake_surface, "FakePlanner has no public callables — blind scan"

    invented = fake_surface - real_surface
    assert not invented, (
        f"FakePlanner carries {sorted(invented)}, which PlacementPlanner does not "
        f"have; plan_item and begin_planning ARE real (planner.py:79 and :69) — "
        f"anything else here is the fake inventing a method")

    def shape(fn):
        return [(p.name, p.kind, p.default is inspect.Parameter.empty)
                for p in inspect.signature(fn).parameters.values()
                if p.name != "self"]

    shapes = {name: shape(getattr(FakePlanner, name)) for name in sorted(fake_surface)}
    # Rule 38, and the first version of this cell got it wrong: a per-method
    # `assert real_shape` FAILS on begin_planning(self), which legitimately takes no
    # parameters. The blind-scan guard belongs on the scan, not on one method — if
    # shape() returned [] for everything, comparing the two would pass vacuously.
    assert sum(1 for s in shapes.values() if s) >= 2, (
        f"every faked method scanned as parameterless, so the comparison below "
        f"cannot fail — the scan went blind: {shapes}")

    for name, fake_shape in shapes.items():
        real_shape = shape(getattr(PlacementPlanner, name))
        assert fake_shape == real_shape, (
            f"FakePlanner.{name} takes {fake_shape}, PlacementPlanner.{name} takes "
            f"{real_shape}")

    for hook in ("PLAN_ITEM_RESULT", "PLAN_ITEMS_RESULT", "PLAN_VIAS_RESULT",
                 "PLAN_TRACKS_RESULT"):
        assert not hasattr(PlacementPlanner, hook), (
            f"{hook} is a fake-only injection hook; the real planner must not grow it")


def test_fake_planner_plans_nothing_by_default():
    """Three install sites put the BASE on an already-built pipeline and need only
    "no moves, no vias, no tracks". The one divergence from the real constructor
    must stay deliberate, so both halves are asserted here."""
    planner = FakePlanner()
    assert planner.plan_items([object()]) == []
    assert planner.plan_vias() == []
    assert planner.plan_tracks() == []
    assert planner.plan_item(object()) == []
    assert planner.begin_planning() is None

    def required(cls):
        return [p.name for p in inspect.signature(cls.__init__).parameters.values()
                if p.default is inspect.Parameter.empty and p.name != "self"]

    assert required(PlacementPlanner) == ["adapter", "config"], (
        f"the real planner now requires {required(PlacementPlanner)}; if that set "
        f"changed, re-read WHY the fake is laxer and update this cell")
    assert required(FakePlanner) == [], (
        "the fake must stay constructible with no arguments — three cells install it "
        "on a pipeline that already exists and have nothing to hand it")


def test_fake_planner_injects_each_result_independently():
    """One knob per read, and the base hands out a COPY: appending to what a cell got
    must not reach the class attribute every later cell reads (the mistake Ф1.4b's
    shared counter made, in a different dress)."""
    class _Local(FakePlanner):
        PLAN_VIAS_RESULT = ("via",)

    planner = _Local()
    assert planner.plan_vias() == ["via"]
    assert planner.plan_items([]) == [] and planner.plan_tracks() == []

    planner.plan_vias().append("extra")
    assert _Local.PLAN_VIAS_RESULT == ("via",), (
        "plan_vias handed out the class attribute itself, so one cell could mutate "
        "what every later cell sees")
    assert FakePlanner.PLAN_VIAS_RESULT == (), "the base must stay empty"

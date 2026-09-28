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
from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.kicad.interfaces import IBoardAdapter

from tests.fakes.adapter import FakeAdapter, public_callables
from tests.fakes.board import FakeBoardLayers, FakeBoardOverlay
from tests.fakes.pipeline import PipelineStubLifetime


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

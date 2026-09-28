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
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.kicad.interfaces import IBoardAdapter

from tests.fakes.adapter import FakeAdapter, public_callables


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

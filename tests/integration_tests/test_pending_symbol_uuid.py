# tests/integration_tests/test_pending_symbol_uuid.py
"""Live-KiCad checks for the refdes/symbol identity guard (2026-08-08).

The unit tests in tests/gui/test_pending_dock.py cover the guard's logic with
fake footprints; these cover the two halves against REAL data:
  - the schematic side parses real .kicad_sch top-level (uuid ...) into
    SchematicComponent.symbol_uuids (deterministic, file-based — runs even
    before KiCad is available, but kept here because it mirrors the board
    check below);
  - the board side reads fp.sheet_path.path[-1] from live kipy objects;
  - compute_pending_edits() runs end-to-end on both and drops identity
    mismatches from Apply's config.

The schematic path is fixed to the in-repo boards/3CH-AWG-TIA project; the
board side uses whatever board is currently open in KiCad (the recon verified
this exact pairing on 3CH-AWG-TIA).
"""
import pytest

from gui.docks.pending import _board_full_path, _board_symbol_uuid, compute_pending_edits, edits_to_fields_cfg
from gui.schema_model import load_schematic_components, load_schematic_instances
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.explore import Selected

SCHEMATIC = "boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch"


def _snapshot(adapter):
    snap = []
    for fp in adapter.get_footprints():
        snap.append(Selected(
            ref=fp.ref,
            role=adapter.get_field_value(fp, ROLE_FIELD_NAME),
            cluster=adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
            sheet=[], nets={}, fp=fp,
        ))
    return snap


@pytest.mark.integration
def test_schematic_symbol_uuids_present():
    """Every real schematic component carries its symbol uuid — the guard's
    schematic half works on the real .kicad_sch files (recon: 291/291)."""
    comps = load_schematic_components(SCHEMATIC)
    assert comps, "expected a non-empty component list from boards/3CH-AWG-TIA"
    assert all(c.symbol_uuids for c in comps), \
        "every component must carry its (symbol ...) top-level (uuid ...)"


@pytest.mark.integration
def test_board_symbol_uuid_readable_from_live_footprints(adapter):
    """_board_symbol_uuid() reads fp.sheet_path.path[-1] off real kipy
    objects — the guard's board half."""
    fps = list(adapter.get_footprints())
    assert fps, "live board has no footprints?"
    missing = [fp.ref for fp in fps
               if _board_symbol_uuid(Selected(ref="x", role=None, cluster=None,
                                              sheet=[], nets={}, fp=fp)) is None]
    # Allow a tiny fraction of board-only/odd footprints (e.g. added straight
    # on the PCB), but the overwhelming majority must resolve.
    assert len(missing) < len(fps) * 0.05, \
        f"too many footprints without a symbol uuid: {missing[:10]}"


@pytest.mark.integration
def test_compute_pending_edits_on_live(adapter):
    """End-to-end: the diff runs on the real schematic + live board (with and
    without the full-path index), and identity-mismatch edits never reach
    Apply's config."""
    comps = load_schematic_components(SCHEMATIC)
    snap = _snapshot(adapter)

    for path_index in (None, load_schematic_instances(SCHEMATIC)):
        edits = compute_pending_edits(comps, snap, path_index)
        assert isinstance(edits, list)

        mismatched = [e for e in edits if e.mismatched]
        cfg = edits_to_fields_cfg(edits)
        mismatched_refs = {e.ref for e in mismatched}
        assert not (mismatched_refs & set(cfg)), \
            "identity-mismatch refs must never be written by Apply"


@pytest.mark.integration
def test_path_index_resolves_live_footprints(adapter):
    """The full-path index from the real schematic resolves a substantial share
    of the live board's footprints (recon measured 358/364 on 3CH-AWG-TIA);
    the leftovers are board-only/odd footprints. Sanity that the index is not
    dead against the actual board, without hard-coding the exact count."""
    index = load_schematic_instances(SCHEMATIC)
    fps = list(adapter.get_footprints())
    assert fps
    resolved = sum(1 for fp in fps if _board_full_path(
        Selected(ref="x", role=None, cluster=None, sheet=[], nets={}, fp=fp)) in index)
    assert resolved >= len(fps) * 0.5, \
        f"full-path index resolved only {resolved}/{len(fps)} of the live board"

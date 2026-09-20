# tests/test_symbol_uuid_shape.py
"""The store's key is read in the SHAPE THE ADAPTER HANDS OVER, not the shape we
imagined — plan_2026_09_20_symbol_uuid_wrong_attribute.md.

The defect (found on the live ControllerESP32 board, 2026-09-20): `symbol_uuid_of`
read `footprint.sheet_path.path`, which is a **kipy** attribute. The adapter hands
out a DOMAIN `kicadstamp.domain.board.Footprint`, whose field is
`sheet_path_uuids`, so the read raised `AttributeError`, `except Exception`
swallowed it and the function returned `None` — for every footprint, on every
board, forever. The override store could therefore never record anything, the
`FieldOverrideAdapter` never found our values on a read either, and Pending's
`mismatched` check never fired.

WHY 5455 GREEN TESTS SAW NOTHING: every one of them built the mock in the kipy
shape (`.sheet_path.path`). That is the trap this file refuses to repeat —
**every object here is built as the adapter builds it** (`domain.board.Footprint`),
and the kipy shape is exercised separately, as the backward-compatibility case
(С2) it is.

Do not "simplify" the fixtures back into `SimpleNamespace(sheet_path=...)`: that
is exactly what hid the defect for a week.
"""
import ast
import logging
import pathlib
from types import SimpleNamespace

import pytest

from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import symbol_uuid_of
from kicadstamp.explore import Selected

REPO = pathlib.Path(__file__).resolve().parent.parent
UUID_SHEET = "fcabc4e8-d656-4628-ac7f-07bf29cf3de0"
UUID_COMP = "f99231f6-c4e4-4ccd-81e2-1d44a2fcfaa2"


# ── the two shapes, built the way their producers build them ────────────────

def adapter_footprint(ref="BZ1", uuids=(UUID_SHEET, UUID_COMP)) -> Footprint:
    """A footprint as `IBoardAdapter.get_footprints()` returns it — the DOMAIN
    dataclass. This is the shape the whole defect is about."""
    return Footprint(ref=ref, uuid=f"fp-{ref}",
                     position=Vector2.from_xy_mm(0.0, 0.0), angle_deg=0.0,
                     layer=BoardLayer.BL_F_Cu, sheet_path_uuids=tuple(uuids))


class _KipyUuid:
    """kipy's BoardLayer uuid-ish object: the value lives in `.value`."""

    def __init__(self, value):
        self.value = value


def kipy_footprint(ref="BZ1", uuids=(UUID_SHEET, UUID_COMP)):
    """A RAW kipy footprint: `fp.sheet_path.path` is a list of uuid objects.
    Still a legal input (the mapper's own source, `fp._kipy`, and the
    diagnostics that talk to kipy directly) — hence С2."""
    return SimpleNamespace(
        ref=ref,
        sheet_path=SimpleNamespace(path=[_KipyUuid(u) for u in uuids]),
    )


def _selected(ref="BZ1", **fp_kwargs) -> Selected:
    return Selected(ref=ref, role="", cluster="", role_field_exists=True,
                    cluster_field_exists=True, sheet=[], nets={},
                    fp=adapter_footprint(ref, **fp_kwargs))


# ── the shape census, read from the SOURCE (С5) ─────────────────────────────
#
# Reading `sheet_path` by hand is what let two implementations of one rule drift
# apart. Only these may do it:
#   * kicadstamp/field_overrides.py — the rule's home (it must accept the kipy
#     shape, so it names the attribute);
#   * kicadstamp/domain/board.py — the ONE kipy -> domain mapper (that is its
#     whole job);
#   * kicadstamp/diagnostics/** — probes that drive raw kipy on purpose.
_SHEET_PATH_WHITELIST = {
    "kicadstamp/field_overrides.py",
    "kicadstamp/domain/board.py",
}


def _sheet_path_attribute_sites():
    """[(relative path, line)] for every `....sheet_path` ATTRIBUTE access in the
    shipping tree (docstrings and diagnostics excluded)."""
    sites = []
    for base in ("kicadstamp", "gui", "mcp_server"):
        for path in sorted((REPO / base).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith("kicadstamp/diagnostics/") or "__pycache__" in rel:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute)
                        and node.attr == "sheet_path"):
                    sites.append((rel, node.lineno))
    return sites


# ── С1 / С2 / С3 / С4: the function itself ─────────────────────────────────

class TestSymbolUuidOf:
    def test_c1_adapter_shape_gives_the_last_uuid(self):
        """С1, the main one: the object the ADAPTER hands over. Red on the base
        (it returned None for every real footprint)."""
        assert symbol_uuid_of(adapter_footprint()) == UUID_COMP

    def test_c1b_single_element_chain_is_that_element(self):
        assert symbol_uuid_of(adapter_footprint(uuids=(UUID_COMP,))) == UUID_COMP

    def test_c2_kipy_shape_still_works(self):
        """С2: the kipy shape keeps working — the mapper's source and the
        diagnostics still pass it."""
        assert symbol_uuid_of(kipy_footprint()) == UUID_COMP

    def test_c3_empty_path_is_a_quiet_none(self, caplog):
        """С3: an empty path is a LEGAL answer (no uuid), not a defect: None and
        not a single warning."""
        with caplog.at_level(logging.WARNING,
                             logger="kicadstamp.field_overrides"):
            assert symbol_uuid_of(adapter_footprint(uuids=())) is None
            assert symbol_uuid_of(kipy_footprint(uuids=())) is None
            assert symbol_uuid_of(None) is None
        assert [r for r in caplog.records
                if r.levelno >= logging.WARNING] == []

    def test_c4_a_foreign_shape_is_named_in_the_log(self, caplog):
        """С4: "the object is not the shape we expect" is a DEFECT, and it must
        be visible: None (never a guess) plus one WARNING naming the type —
        once per type, not once per call (the live flow calls this per row per
        tick)."""
        with caplog.at_level(logging.WARNING,
                             logger="kicadstamp.field_overrides"):
            assert symbol_uuid_of(SimpleNamespace(ref="X")) is None
            assert symbol_uuid_of(SimpleNamespace(ref="Y")) is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, warnings
        assert "SimpleNamespace" in warnings[0].getMessage()

    def test_c5_the_rule_lives_in_one_place(self):
        """С5: no second hand-rolled reader of the uuid/path in the shipping
        tree (pending's copy was the second one, broken identically)."""
        offenders = [f"{rel}:{line}" for rel, line in _sheet_path_attribute_sites()
                     if rel not in _SHEET_PATH_WHITELIST]
        assert offenders == [], (
            "these places read `sheet_path` by hand instead of calling the "
            "shared rule (kicadstamp.field_overrides): " + repr(offenders))

    def test_c5_whitelist_cannot_grow_silently(self):
        """The whitelist itself is pinned — a new entry is a deliberate act."""
        assert _SHEET_PATH_WHITELIST == {
            "kicadstamp/field_overrides.py",
            "kicadstamp/domain/board.py",
        }


# ── С6: the whole way from a board object to a NON-EMPTY store batch ───────

class TestTheStoreBatchIsNoLongerEmpty:
    def test_c6_rows_from_adapter_objects_carry_the_key(self):
        """С6, end to end on the adapter's shape: the role table built from real
        board objects gets a symbol_uuid, and the store batch is NOT empty.
        Red on the base — which is why the store file never appeared."""
        from gui.role_table_model import (
            apply_overrides, build_override_updates, records_from_items,
            rows_from_state)

        records = records_from_items([_selected("BZ1")])
        assert records[0].symbol_uuid == UUID_COMP

        state = {"cluster": "BUZZER", "rows": [{"ref": "BZ1", "role": "BUZZER"}]}
        rows, _cluster = rows_from_state(state, records)
        plan = build_override_updates(apply_overrides(rows, None))
        assert plan.updates == [(UUID_COMP, ROLE_FIELD_NAME, "BUZZER")]
        assert plan.skipped == []


# ── С7: Pending's `mismatched` is distinguishable again ────────────────────

class TestPendingMismatch:
    def test_c7_same_refdes_different_symbol_is_flagged(self):
        """С7: the same refdes carrying DIFFERENT symbol uuids on the two sides
        is the re-annotation/revision desync — it must be marked `mismatched`,
        not silently diffed as a value change. Built with the adapter's shape;
        on the base `_board_symbol_uuid` returned None and the flag never
        appeared."""
        from gui.docks.pending import compute_pending_edits
        from gui.schema_model import SchematicComponent

        component = SchematicComponent(
            ref="BZ1", role="ROLE_A", cluster="CL_A", file="x.kicad_sch",
            block_start=0, divergent=False, symbol_uuids=("some-other-uuid",))
        snapshot = [_selected("BZ1")]

        edits = compute_pending_edits([component], snapshot)

        assert [e for e in edits if e.mismatched], edits
        assert "some-other-uuid" in edits[0].old_value


# ── С8: the overlay's read half (dead for the same reason) ────────────────

class TestTheOverlayFindsOurValue:
    def test_c8_our_value_wins_over_the_board(self):
        """С8: the store's promise is not "a batch gets composed" but "OUR value
        wins on read". The layer keys the lookup by the same uuid, so with the
        adapter's shape it too was blind — the value never won."""
        from kicadstamp.field_overrides import FieldOverrides

        store = FieldOverrides()
        store.set(UUID_COMP, "BZ1", ROLE_FIELD_NAME, "OURS", "test")
        board = SimpleNamespace(
            get_field_value=lambda fp, field: "FROM_BOARD")
        layered = FieldOverrideAdapter(board, store)

        fp = adapter_footprint()
        assert layered.get_field_value(fp, ROLE_FIELD_NAME) == "OURS"
        assert layered.get_field_values(fp, ROLE_FIELD_NAME) == (
            "OURS", "FROM_BOARD")

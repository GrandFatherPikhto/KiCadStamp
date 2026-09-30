# tests/test_overrides_snapshot_and_diff.py
"""Т5г (plan_2026_09_18_field_overrides_store): ONE snapshot carries BOTH truths.

  * `Selected.role`/`.cluster` are the values IN FORCE (our store wins) — which is
    what the GUI reads, so a role noted only in KiCadStamp can be chosen in a
    picker at all (С25).
  * `Selected.board_role`/`.board_cluster` are what PHYSICALLY lies on the board —
    what the diff's Board column must show, or Pending would compare the store
    with itself and a board edit made after the note was taken would vanish (С27).
  * Both come from ONE read: the override layer is the only place where the two
    are in hand together (`get_field_values`), so no second pass over the
    footprints is needed — and a second pass would describe a board that had
    already moved on (С28).

The Components tree's own mark (С26) is guarded in tests/gui/test_role_cluster_tree.py,
where the dock fixture lives.
"""
from types import SimpleNamespace

from gui.docks.pending import compute_pending_edits
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.explore import Board, Selected
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import FieldOverrides, SOURCE_CLI
from kicadstamp.utils.paths import overrides_path_for_config


class _Inner:
    """A board adapter stand-in that COUNTS its field reads — the spy С28 needs.
    `get_field_value` is the single-truth read every bare adapter has."""

    def __init__(self, fields, refs):
        self._fields = fields
        self._refs = refs
        self.field_reads = 0

    def refresh_board(self) -> None:
        pass

    def get_footprints(self):
        return [self._fp(ref) for ref in self._refs]

    @staticmethod
    def _fp(ref):
        # `sheet_path_uuids` is what resolve_sheet_path_names reads for the
        # Selected.sheet chain — a real footprint carries it, so the stand-in
        # does too (otherwise `select()` dies on the sheet fact, not on the pair
        # this file is about).
        return SimpleNamespace(
            ref=ref,
            sheet_path=SimpleNamespace(path=[SimpleNamespace(value=f"uuid-{ref}")]),
            sheet_path_uuids=(f"uuid-{ref}",))

    def get_field_value(self, fp, field):
        self.field_reads += 1
        return self._fields.get((fp.ref, field))

    def has_field(self, fp, field):
        return True

    def get_footprint_pads(self, fp):
        return []


def _store(tmp_path, *records):
    """A real store file — the shape the GUI's poll adapter reads."""
    profile = tmp_path / "prof.sexp"
    profile.write_text("", encoding="utf-8")
    store = FieldOverrides(overrides_path_for_config(str(profile)))
    for symbol_uuid, ref, field, value in records:
        store.set(symbol_uuid, ref, field, value, SOURCE_CLI)
    store.save()
    return store


def _snapshot(adapter):
    board = Board(adapter, sheet_names={})
    board.refresh()          # Board.connect() does this; the constructor does not
    return board


# ── С25 + С27 + С28: one read, two truths ────────────────────────────────

def test_c25_c27_one_snapshot_carries_the_value_in_force_and_the_physical_one(tmp_path):
    """The whole point of Т5г in one assertion pair: `role` is OURS (so a picker
    offers it — С25) while `board_role` is the board's (so the diff keeps showing
    the board — С27)."""
    store = _store(tmp_path, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    inner = _Inner({("R1", ROLE_FIELD_NAME): "ON_BOARD"}, refs=["R1"])
    board = _snapshot(FieldOverrideAdapter(inner, store))

    (selected,) = board.select()

    assert selected.role == "FROM_STORE"          # in force — what a picker shows
    assert selected.board_role == "ON_BOARD"      # physical — what the diff shows
    assert selected.role_from_store is True
    assert selected.cluster == selected.board_cluster  # nothing noted for Cluster


def test_c28_both_truths_come_from_a_single_pass_over_the_footprints(tmp_path):
    """С28: the second value must NOT cost a second reading of the board. Three
    footprints, Role+Cluster each — six reads. A second snapshot for the diff (the
    variant the plan rejected) would double this count AND describe another
    instant of the board."""
    store = _store(tmp_path,
                   ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"),
                   ("uuid-R1", "R1", CLUSTER_FIELD_NAME, "C_FROM_STORE"))
    inner = _Inner({("R1", ROLE_FIELD_NAME): "ON_BOARD",
                    ("R1", CLUSTER_FIELD_NAME): "C_ON_BOARD",
                    ("R2", ROLE_FIELD_NAME): "R2_BOARD",
                    ("R2", CLUSTER_FIELD_NAME): "C2_BOARD",
                    ("R3", ROLE_FIELD_NAME): "R3_BOARD",
                    ("R3", CLUSTER_FIELD_NAME): "C3_BOARD"},
                   refs=["R1", "R2", "R3"])
    board = _snapshot(FieldOverrideAdapter(inner, store))

    selected = board.select()

    assert inner.field_reads == 6            # 3 footprints x 2 fields, ONCE
    by_ref = {s.ref: s for s in selected}
    assert (by_ref["R1"].role, by_ref["R1"].board_role) == ("FROM_STORE", "ON_BOARD")
    assert (by_ref["R1"].cluster, by_ref["R1"].board_cluster) == ("C_FROM_STORE",
                                                                 "C_ON_BOARD")
    assert by_ref["R2"].role == by_ref["R2"].board_role == "R2_BOARD"
    # Re-selecting over the SAME snapshot costs nothing: the caches hold both
    # halves (a poll tick re-selects every time).
    board.select()
    assert inner.field_reads == 6


def test_a_bare_adapter_has_one_truth():
    """No layer (the pre-store world, the golden С2): the physical value IS the
    value in force, so nothing is marked as "from the store" and the diff sees
    exactly what it saw before Т5г."""
    inner = _Inner({("R1", ROLE_FIELD_NAME): "ON_BOARD"}, refs=["R1"])
    board = _snapshot(inner)

    (selected,) = board.select()

    assert selected.role == selected.board_role == "ON_BOARD"
    assert selected.role_from_store is False


# ── С27: the diff's Board column is the physical one ─────────────────────

def test_c27_the_diff_compares_against_the_physical_board_value(tmp_path):
    """A component whose role we noted (ours "OURS") while the board says
    "ON_BOARD": the diff row's board side must be the BOARD's value. With the
    effective value there, the row would compare the store with itself and the
    board's own edit would vanish from the showcase."""
    store = _store(tmp_path, ("uuid-R1", "R1", ROLE_FIELD_NAME, "OURS"))
    component = SimpleNamespace(ref="R1", role="OURS", cluster=None, divergent=False,
                                symbol_uuids=("uuid-R1",))
    selected = Selected(ref="R1", role="OURS", cluster=None, sheet=[], nets={},
                        fp=SimpleNamespace(
                            sheet_path=SimpleNamespace(
                                path=[SimpleNamespace(value="uuid-R1")])),
                        board_role="ON_BOARD", board_cluster=None)

    edits = compute_pending_edits([component], [selected], store=store)

    assert [(e.field, e.new_value, e.our_value) for e in edits] == [
        (ROLE_FIELD_NAME, "ON_BOARD", "OURS")]


def test_the_gui_connection_binds_the_store_without_reconnecting(tmp_path):
    """Т5г/С25, the plumbing: the poll adapter is created once per CONNECTION while
    the profile — and so the store — belongs to the PROJECT. A switch must
    therefore reach an adapter that is already alive, and it must not reopen the
    socket: the layer is REBOUND (`bind_store`, the very mechanism the MCP server
    uses for its per-call profiles), never re-created. This is what the GUI's own
    snapshot reads, so a role noted only in KiCadStamp becomes choosable in the
    pickers (С25) — while the diff keeps the board's value (С27)."""
    from gui.connection import BoardConnection

    profile = tmp_path / "prof.sexp"
    profile.write_text("", encoding="utf-8")
    _store(tmp_path, ("uuid-R1", "R1", ROLE_FIELD_NAME, "FROM_STORE"))
    bound = []

    class _Adapter:
        def bind_store(self, store, *, source=None):
            bound.append((store, source))

    connection = BoardConnection()
    connection.board = SimpleNamespace(adapter=_Adapter())

    connection.set_project_config(profile)

    assert len(bound) == 1                      # rebound, not reconnected
    store, source = bound[0]
    assert store.get("uuid-R1", ROLE_FIELD_NAME) == "FROM_STORE"
    assert source == "registry"


def test_a_hand_built_selected_keeps_the_pre_t5g_meaning():
    """Objects built by hand (tests, other callers) carry ONE truth: the board
    side falls back to the value in force, so every existing caller keeps the
    behaviour it had before Т5г."""
    assert Selected(ref="R1", role="A", cluster="C", sheet=[], nets={},
                    fp=SimpleNamespace()).board_role == "A"

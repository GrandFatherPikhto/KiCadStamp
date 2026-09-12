# tests/gui/test_board_layers.py
"""Э1 and Э7.5 (corrected 2026-09-12) of plan_2026_09_12_cell_layer_dialog.md:
the live board's copper layers, in physical stackup order.

Asserting the ORDER alone proves nothing — the measured values (F.Cu=3, In1.Cu=4,
In2.Cu=5, B.Cu=34) are ascending in stackup order, so a plain sort by value
agrees with the correct implementation and no test could tell the two apart.
The trap Э7.5 pins down is POSITION: on a four-layer board B.Cu is at stack
position 4, not at layer value 34, and the number of positions equals
get_copper_layer_count(). An implementation that confuses the two fails here.

No KiCad and no Qt: the board is a duck-typed fake, like tests/gui/
test_board_overlay.py's.
"""
from types import SimpleNamespace

from kipy.board_types import BoardLayer

from gui import settings
from gui.board_layers import (
    ALL_COPPER_LAYERS,
    READ_LAYERS_KEY,
    CopperLayer,
    copper_layer_order,
    enabled_copper_layers,
    filter_tracks_by_layers,
    layer_choices,
    layers_to_remember,
    live_copper_name,
    remember_read_layers,
    remembered_read_layers,
    selection_layer_names,
)
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer as DomainLayer
from kicadstamp.domain.geometry import Vector2

F = BoardLayer.BL_F_Cu
IN1 = BoardLayer.BL_In1_Cu
IN2 = BoardLayer.BL_In2_Cu
B = BoardLayer.BL_B_Cu
SILK = BoardLayer.BL_F_SilkS
EDGE = BoardLayer.BL_Edge_Cuts
USER = BoardLayer.BL_Dwgs_User

# Enabled layers that are NOT copper: nothing to read, nothing to offer.
NON_COPPER = (SILK, EDGE, USER)

_NAMES = {F: "F.Cu", IN1: "In1.Cu", IN2: "In2.Cu", B: "B.Cu",
          SILK: "F.Silkscreen", EDGE: "Edge.Cuts", USER: "User.Drawings"}
# value -> member name ('BL_In10_Cu'), for the layers the map above leaves out
_LAYER_NAMES = {value: name for name, value in BoardLayer.items()}

FOUR_LAYER = (F, IN1, IN2, B)


def _derived_name(layer) -> str:
    """KiCad's own name for a layer no test renamed: an inner layer is
    'InN.Cu' (the member-name layout, NOT an arithmetic trick on the value —
    the module under test is the one that must not do arithmetic on values)."""
    member = _LAYER_NAMES.get(layer, "")
    if member.startswith("BL_In") and member.endswith("_Cu"):
        return f"In{member[5:-3]}.Cu"
    return member or str(layer)


class _FakeBoard:
    """Duck-typed live board: enabled layers, their names, their visibility."""

    def __init__(self, enabled, visible=None, names=None, copper_count=4):
        self.enabled = list(enabled)
        self.visible = list(enabled if visible is None else visible)
        self.names = dict(names or {})
        self.copper_count = copper_count

    def get_enabled_layers(self):
        return list(self.enabled)

    def get_visible_layers(self):
        return list(self.visible)

    def get_layer_name(self, layer):
        if layer in self.names:
            return self.names[layer]
        return _NAMES.get(layer) or _derived_name(layer)

    def get_copper_layer_count(self):
        return self.copper_count


class TestEnabledCopperLayers:
    def test_stackup_order_and_positions(self):
        """Enabled order is arbitrary (that is how the board reports it) and the
        non-copper layers are interleaved — the answer is F, In1..InK, B."""
        board = _FakeBoard(enabled=[B, IN2, SILK, F, IN1, EDGE])
        rows = enabled_copper_layers(board)
        assert all(isinstance(row, CopperLayer) for row in rows)
        assert [row.copper_name for row in rows] == [
            "F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
        assert [row.position for row in rows] == [1, 2, 3, 4]

    def test_position_is_a_place_not_a_layer_value(self):
        """Э7.5: B.Cu carries the layer value 34 on a FOUR-layer board — its
        place in the stack is 4. This is the assertion a value-as-position
        implementation fails."""
        board = _FakeBoard(enabled=list(FOUR_LAYER))
        rows = enabled_copper_layers(board)
        back = rows[-1]
        assert back.copper_name == "B.Cu"
        assert back.layer == B
        assert back.position == 4
        assert back.position != back.layer
        # No row reports its own layer value as a position, on any layer.
        assert [row.position for row in rows] != [row.layer for row in rows]

    def test_row_count_matches_board_copper_layer_count(self):
        four = _FakeBoard(enabled=list(FOUR_LAYER) + [SILK, EDGE],
                          copper_count=4)
        assert len(enabled_copper_layers(four)) == four.get_copper_layer_count()

        six = _FakeBoard(enabled=[B, BoardLayer.BL_In4_Cu, F,
                                  BoardLayer.BL_In3_Cu, IN1, IN2],
                         copper_count=6)
        rows = enabled_copper_layers(six)
        assert [row.copper_name for row in rows] == [
            "F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
        assert len(rows) == six.get_copper_layer_count()

    def test_display_name_is_the_live_user_name(self):
        """Denis renamed a layer: the UI must show HIS name, while the cell's
        record vocabulary stays canonical — both travel on the same row."""
        board = _FakeBoard(enabled=list(FOUR_LAYER), names={IN1: "GND"})
        inner = enabled_copper_layers(board)[1]
        assert inner.display_name == "GND"
        assert inner.copper_name == "In1.Cu"

    def test_hidden_copper_layer_is_reported_not_dropped(self):
        """A hidden copper layer still comes back (visible=False) — Э2 only
        WARNS about it; the read is never silently narrowed."""
        board = _FakeBoard(enabled=[F, IN1, IN2, B, SILK],
                           visible=[F, IN2, B, SILK])
        rows = enabled_copper_layers(board)
        assert [(row.copper_name, row.visible) for row in rows] == [
            ("F.Cu", True), ("In1.Cu", False), ("In2.Cu", True), ("B.Cu", True)]

    def test_hidden_non_copper_layer_does_not_appear(self):
        board = _FakeBoard(enabled=[F, IN1, IN2, B, SILK],
                           visible=[F, IN1, IN2, B])
        assert [row.copper_name for row in enabled_copper_layers(board)] == [
            "F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    def test_non_copper_layers_never_appear(self):
        board = _FakeBoard(enabled=[SILK, F, EDGE, IN1, USER, IN2, B])
        rows = enabled_copper_layers(board)
        assert [row.layer for row in rows] == list(FOUR_LAYER)

    def test_inner_layers_ordered_numerically_not_lexically(self):
        """In10 sorts before In2 as a string — the stack order is numeric."""
        board = _FakeBoard(enabled=[BoardLayer.BL_In10_Cu, F, IN2, IN1, B],
                           copper_count=4)
        assert [row.copper_name for row in enabled_copper_layers(board)] == [
            "F.Cu", "In1.Cu", "In2.Cu", "In10.Cu", "B.Cu"]


class TestCopperLayerOrder:
    def test_reduces_to_copper_in_stack_order(self):
        assert copper_layer_order([IN2, EDGE, B, IN1, SILK, F]) == [
            F, IN1, IN2, B]

    def test_empty_and_copper_free_inputs(self):
        assert copper_layer_order([]) == []
        assert copper_layer_order(list(NON_COPPER)) == []


class TestLiveCopperName:
    """A live Track carries a DOMAIN layer (kicadstamp.domain.geometry), whose
    numbering is NOT the board's (F.Cu is 0 there, 3 on the board) — the
    canonical NAME is what the filter compares, so both worlds can meet."""

    def test_domain_copper_layers_have_their_canonical_names(self):
        assert live_copper_name(DomainLayer.BL_F_Cu) == "F.Cu"
        assert live_copper_name(DomainLayer.BL_In1_Cu) == "In1.Cu"
        assert live_copper_name(DomainLayer.BL_In30_Cu) == "In30.Cu"
        assert live_copper_name(DomainLayer.BL_B_Cu) == "B.Cu"

    def test_a_board_value_is_not_a_domain_layer(self):
        """The board's F.Cu value is 3 — a number the DOMAIN enum does not name.
        Mixing the two worlds must yield NO name (never a guessed 'In3.Cu')."""
        assert live_copper_name(BoardLayer.BL_F_Cu) is None
        assert live_copper_name(None) is None


class TestFilterTracksByLayers:
    """Э5: the layer set is decided on the UI thread and the workers keep only
    the tracks on it — the filter, not the matcher, is what makes an unchecked
    layer's copper invisible."""

    @staticmethod
    def _track(layer):
        return SimpleNamespace(layer=layer)

    def test_all_layers_keeps_every_track_untouched(self):
        tracks = [self._track(DomainLayer.BL_F_Cu),
                  self._track(DomainLayer.BL_B_Cu)]
        assert filter_tracks_by_layers(tracks, ALL_COPPER_LAYERS) == tracks

    def test_none_is_the_all_layers_answer_and_not_an_empty_read(self):
        """The sentinel is what the fast path passes (P.3.1: no board read), so
        it must mean 'everything', never 'nothing'."""
        one = [self._track(DomainLayer.BL_F_Cu)]
        assert len(filter_tracks_by_layers(one, None)) == 1

    def test_only_the_selected_layers_are_visible(self):
        f_cu = self._track(DomainLayer.BL_F_Cu)
        b_cu = self._track(DomainLayer.BL_B_Cu)
        assert filter_tracks_by_layers([f_cu, b_cu], {"F.Cu"}) == [f_cu]
        assert filter_tracks_by_layers([f_cu, b_cu], {"F.Cu", "B.Cu"}) == [
            f_cu, b_cu]

    def test_inner_layers_are_matched_by_their_own_name(self):
        in1 = self._track(DomainLayer.BL_In1_Cu)
        in2 = self._track(DomainLayer.BL_In2_Cu)
        assert filter_tracks_by_layers([in1, in2], {"In2.Cu"}) == [in2]
        assert filter_tracks_by_layers([in1, in2], {"In3.Cu"}) == []

    def test_an_empty_selection_reads_no_track_at_all(self):
        tracks = [self._track(DomainLayer.BL_F_Cu),
                  self._track(DomainLayer.BL_B_Cu)]
        assert filter_tracks_by_layers(tracks, set()) == []

    def test_original_order_is_preserved(self):
        """Matching pairs records to live items in selection order — the filter
        must not reshuffle what it keeps."""
        first = self._track(DomainLayer.BL_B_Cu)
        second = self._track(DomainLayer.BL_F_Cu)
        third = self._track(DomainLayer.BL_B_Cu)
        assert filter_tracks_by_layers(
            [first, second, third], {"B.Cu"}) == [first, third]

    def test_a_layer_we_cannot_name_is_never_kept(self):
        """Defensive: an item whose layer is not a domain copper layer matches
        no checked name, so it can never join a read by accident."""
        alien = self._track(3)
        assert filter_tracks_by_layers([alien], {"F.Cu"}) == []
        assert filter_tracks_by_layers([alien], {"In3.Cu"}) == []


# ── Э3: the remembered choice (gui_state.json) ──────────────────────────────
# Each test runs against a THROWAWAY gui_state.json (tests/gui/conftest.py's
# autouse isolated_settings), so nothing here touches the developer's own file.

class TestRememberedReadLayers:
    def test_nothing_remembered_yet_is_none(self):
        """None means "the first run" — every layer starts checked; it is NOT an
        empty selection (that would read nothing at all)."""
        assert remembered_read_layers() is None

    def test_round_trip_sorted(self):
        remember_read_layers({"B.Cu", "F.Cu"})
        assert remembered_read_layers() == ["B.Cu", "F.Cu"]
        remember_read_layers(["In1.Cu"])
        assert remembered_read_layers() == ["In1.Cu"]

    def test_a_foreign_value_reads_as_nothing_remembered(self):
        settings.state.set(READ_LAYERS_KEY, {"not": "a list"})
        assert remembered_read_layers() is None


def _selection_track(layer):
    return Track(uuid=f"t-{layer}", net_name="GND",
                 start=Vector2.from_xy_mm(0.0, 0.0),
                 end=Vector2.from_xy_mm(1.0, 0.0),
                 width_mm=0.25, layer=layer)


class TestSelectionLayerNames:
    """Which layers carry copper in the selection — read from the DISTRIBUTED
    items (the ~400ms tick), never from a fresh board call (P.3.4)."""

    def test_only_track_layers_count(self):
        items = [_selection_track(DomainLayer.BL_B_Cu),
                 _selection_track(DomainLayer.BL_F_Cu),
                 _selection_track(DomainLayer.BL_F_Cu)]
        assert selection_layer_names(items) == {"F.Cu", "B.Cu"}

    def test_a_via_is_layerless(self):
        via = Via(uuid="v1", position=Vector2.from_xy_mm(1.0, 1.0),
                  net_name="GND", drill_mm=0.3, diameter_mm=0.6)
        assert selection_layer_names([via]) == set()

    def test_a_component_stands_on_a_side_not_on_a_layer(self):
        """A footprint's own layer must not be mistaken for copper on it."""
        fp = Footprint(ref="R1", uuid="u1", position=Vector2.from_xy_mm(0.0, 0.0),
                       angle_deg=0.0, layer=DomainLayer.BL_B_Cu)
        assert selection_layer_names([fp]) == set()

    def test_empty_or_absent_selection(self):
        assert selection_layer_names([]) == set()
        assert selection_layer_names(None) == set()


def _copper_rows(names, hidden=()):
    """CopperLayer rows the way Э1 hands them over (stack order, 1-based
    positions); the layer VALUE is irrelevant here — every rule works on names."""
    return [CopperLayer(layer=index + 3, copper_name=name, display_name=name,
                        position=index + 1, visible=name not in hidden)
            for index, name in enumerate(names)]


FOUR = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")


class TestLayerChoices:
    """Э3's rule order: the remembered set is the starting point, and only then
    are the layers empty in the current selection taken off."""

    def test_first_run_starts_with_every_layer_checked(self):
        rows = layer_choices(_copper_rows(FOUR), None, set(FOUR))
        assert [row.checked for row in rows] == [True, True, True, True]
        assert [row.empty for row in rows] == [False] * 4
        assert [row.auto_unchecked for row in rows] == [False] * 4

    def test_the_remembered_set_is_the_starting_point(self):
        rows = layer_choices(_copper_rows(FOUR), ["F.Cu", "B.Cu"], set(FOUR))
        assert [row.checked for row in rows] == [True, False, False, True]
        # ... and a manual "off" is not an auto-uncheck.
        assert [row.auto_unchecked for row in rows] == [False] * 4

    def test_layers_empty_in_the_selection_come_off_and_are_flagged(self):
        rows = layer_choices(_copper_rows(FOUR), None, {"F.Cu", "In2.Cu"})
        assert [(row.copper.copper_name, row.checked, row.empty,
                 row.auto_unchecked) for row in rows] == [
            ("F.Cu", True, False, False),
            ("In1.Cu", False, True, True),
            ("In2.Cu", True, False, False),
            ("B.Cu", False, True, True),
        ]

    def test_a_manual_uncheck_is_the_stronger_reason(self):
        """Remembered off AND empty right now: flagged as empty but NOT as an
        auto-uncheck, because the user's own "no" must survive either way."""
        rows = layer_choices(_copper_rows(FOUR), ["F.Cu"], {"F.Cu"})
        in1 = rows[1]
        assert (in1.checked, in1.empty, in1.auto_unchecked) == (False, True, False)

    def test_a_remembered_name_that_is_not_on_this_board_is_ignored(self):
        rows = layer_choices(_copper_rows(("F.Cu", "B.Cu")),
                             ["F.Cu", "In7.Cu"], {"F.Cu", "B.Cu"})
        assert [row.checked for row in rows] == [True, False]


class TestLayersToRemember:
    """The guarantee Э3 spells out: an auto-uncheck must never reach the memory,
    or one narrow selection would silently erase the user's choice."""

    def test_an_auto_unchecked_layer_is_not_lost(self):
        rows = layer_choices(_copper_rows(("F.Cu", "In1.Cu")), None, {"F.Cu"})
        # In1.Cu came off by itself; the user pressed Read without touching it.
        assert layers_to_remember(rows, ["F.Cu"], set()) == ["F.Cu", "In1.Cu"]

    def test_a_touched_auto_unchecked_layer_stays_off(self):
        rows = layer_choices(_copper_rows(("F.Cu", "In1.Cu")), None, {"F.Cu"})
        assert layers_to_remember(rows, ["F.Cu"], {"In1.Cu"}) == ["F.Cu"]

    def test_a_layer_the_user_put_back_is_remembered(self):
        rows = layer_choices(_copper_rows(("F.Cu", "In1.Cu")), None, {"F.Cu"})
        assert layers_to_remember(rows, ["F.Cu", "In1.Cu"], {"In1.Cu"}) == [
            "F.Cu", "In1.Cu"]

    def test_a_manual_uncheck_is_remembered(self):
        rows = layer_choices(_copper_rows(("F.Cu", "B.Cu")), None,
                             {"F.Cu", "B.Cu"})
        assert layers_to_remember(rows, ["F.Cu"], {"B.Cu"}) == ["F.Cu"]

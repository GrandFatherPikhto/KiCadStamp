# tests/test_adapter_bounding_box_positions.py
"""Guard С10 (Э5) of plan_2026_09_15_pad_geometry_thermal_vias.

`KiCadBoardAdapter.get_bounding_boxes` promises a POSITIONAL list, and every
caller unzips it against its own item list (keepout, collisions, the Extract
selection closure, the inter-node copper boundary). kipy's list form cannot keep
that promise: it builds `item_to_bbox.get(item.id.value)` and drops every None
(`if box is not None` — kipy/board.py), so ONE item without a box shortens the
answer and shifts every following box onto the wrong item. The fake board below
reproduces exactly that, and the guards pin the recovery: the adapter asks for the
items one by one (kiPy's single-item form returns one box or None) and never lines
a short answer up by a bare zip.
"""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.domain.geometry import Box2, Vector2
from kicadstamp.kicad.adapter import KiCadBoardAdapter as Adapter

MM = 1_000_000
_LOGGER_NAME = "kicadstamp.kicad.adapter"


def _item(uuid: str):
    """Anything kipy can address by id — `unwrap` passes it through as-is."""
    return SimpleNamespace(id=SimpleNamespace(value=uuid))


def _box(x_mm: float) -> Box2:
    return Box2(pos=Vector2.from_xy_mm(x_mm, 0.0), size=Vector2.from_xy_mm(1.0, 1.0))


class _FakeBoard:
    """kipy's own behaviour, reproduced: the LIST form drops the entries that have
    no box, the SINGLE-item form returns that one box or None."""

    def __init__(self, boxes: dict):
        self.boxes = boxes
        self.calls: list = []

    def get_item_bounding_box(self, target, include_text: bool = False):
        self.calls.append(target)
        if isinstance(target, list):
            return [self.boxes[t.id.value] for t in target
                    if self.boxes.get(t.id.value) is not None]
        return self.boxes.get(target.id.value)


def _adapter(boxes: dict) -> Adapter:
    adapter = Adapter.__new__(Adapter)
    adapter._board = _FakeBoard(boxes)
    return adapter


class TestBoundingBoxesStayPositional:
    def test_a_missing_box_does_not_shift_the_following_ones(self):
        """The middle item has no box, so kipy's list form answers with two boxes
        for three items: the adapter must still return three entries, with the
        missing one in its own place."""
        items = [_item("a"), _item("b"), _item("c")]
        adapter = _adapter({"a": _box(1.0), "b": None, "c": _box(3.0)})

        boxes = adapter.get_bounding_boxes(items)

        assert len(boxes) == 3
        assert boxes[1] is None
        assert boxes[0].pos.x == 1 * MM
        assert boxes[2].pos.x == 3 * MM

    def test_the_recovery_is_one_request_per_item_and_is_logged(self, caplog):
        items = [_item("a"), _item("b"), _item("c")]
        adapter = _adapter({"a": _box(1.0), "b": None, "c": _box(3.0)})

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            adapter.get_bounding_boxes(items)

        assert any("one by one" in r.getMessage() for r in caplog.records)
        # first the batch (a list), then one call per item as a SINGLE item
        assert isinstance(adapter._board.calls[0], list)
        assert [c.id.value for c in adapter._board.calls[1:]] == ["a", "b", "c"]

    def test_a_full_answer_costs_exactly_one_request(self):
        """The counter-guard: no per-item re-reading when nothing is missing."""
        items = [_item("a"), _item("b")]
        adapter = _adapter({"a": _box(1.0), "b": _box(2.0)})

        boxes = adapter.get_bounding_boxes(items)

        assert [b.pos.x for b in boxes] == [1 * MM, 2 * MM]
        assert len(adapter._board.calls) == 1
        assert isinstance(adapter._board.calls[0], list)

    def test_no_boxes_at_all_still_keeps_the_length(self):
        items = [_item("a"), _item("b"), _item("c")]
        adapter = _adapter({})

        boxes = adapter.get_bounding_boxes(items)

        assert boxes == [None, None, None]

    def test_no_items_is_no_request(self):
        adapter = _adapter({"a": _box(1.0)})
        assert adapter.get_bounding_boxes([]) == []
        assert adapter._board.calls == []

    def test_a_non_list_answer_for_a_one_item_request_is_normalised(self):
        """The defensive branch that predates this change: if the answer is not a
        list at all, it is still returned as a one-entry list."""
        board = _FakeBoard({})
        board.get_item_bounding_box = lambda target, include_text=False: _box(5.0)
        adapter = Adapter.__new__(Adapter)
        adapter._board = board

        boxes = adapter.get_bounding_boxes([_item("a")])

        assert len(boxes) == 1
        assert boxes[0].pos.x == 5 * MM

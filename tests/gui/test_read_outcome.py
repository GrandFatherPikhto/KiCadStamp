# tests/gui/test_read_outcome.py
"""Part Г cells (plan ``plan_2026_10_08_narrowing_net_traces_cost``): a read SAYS
what it took and what it left behind, and its items are exactly what the board
selection becomes.

Denis (2026-10-08): «Выделение для того и служит, чтобы было видно, правильно ли
прочиталось» — so the two halves of the rule are pinned here separately:

  * ``items`` — the plan's own inputs (components + the copper that entered the
    read), which the worker hands to ONE ``select_items``;
  * ``line`` — «read N of M selected item(s); skipped K: <reasons>», the reasons
    named with their counts (another cluster / another record's copper /
    inter-node copper / layer off).

A read of nothing builds nothing: an explicit action that found no selection must
not silently CLEAR it (``replaced`` False).
"""
from types import SimpleNamespace

from gui.read_outcome import read_outcome, replace_selection


def _prelude(records=(), net_traces=()):
    return SimpleNamespace(subtracted_records=tuple(records),
                           subtracted_net_traces=tuple(net_traces))


def _fp(name):
    return SimpleNamespace(ref=name, uuid=f"uuid-{name}")


def _item(uuid):
    return SimpleNamespace(uuid=uuid)


def _layer_report(skipped=()):
    return {"read": ["F.Cu"], "skipped_manual": list(skipped), "skipped_empty": []}


def test_a_clean_read_says_how_many_it_read_and_selects_them_back():
    """5 selected, 5 read: no "skipped" part at all, and the items are the plan's
    own inputs in the plan's own order."""
    fps = [_fp("C1"), _fp("C2")]
    via = _item("v1")

    outcome = read_outcome(footprints=fps, vias=[via], raw_tracks=[],
                           plan_footprints=fps, plan_vias=[via], plan_tracks=[],
                           prelude=None, layer_report=_layer_report())

    assert outcome.line == "read 3 of 3 selected item(s)"
    assert outcome.items == (fps[0], fps[1], via)
    assert outcome.skipped == 0 and outcome.replaced


def test_the_line_names_every_reason_with_its_count():
    """One component of ANOTHER cluster, one via another record owns and one track
    on a layer that is not read: each is named with its count, and they add up to
    the difference the user can check (5 selected, 2 read)."""
    mine = _fp("C1")
    foreign = _fp("P1")
    subtracted = _item("v-other-record")
    kept_via = _item("v-kept")
    off_layer = _item("t-off")

    outcome = read_outcome(
        footprints=[mine, foreign], vias=[subtracted, kept_via],
        raw_tracks=[off_layer], plan_footprints=[mine], plan_vias=[kept_via],
        plan_tracks=[], prelude=_prelude(records=(("anchor:C1", 1),)),
        layer_report=_layer_report(skipped=["B.Cu"]))

    assert outcome.skipped == 3
    assert outcome.line == (
        "read 2 of 5 selected item(s); skipped 3: another cluster — 1; "
        "another record's copper — 1; layer off — 1")
    assert outcome.items == (mine, kept_via)


def test_the_inter_node_copper_is_named_as_its_own_reason():
    """A piece a LIVE net_traces record owns is skipped (Р3/subtraction) and named
    as inter-node copper — the reason Denis asked to see in words."""
    mine = _fp("C1")
    bridge = _item("t-bridge")

    outcome = read_outcome(
        footprints=[mine], vias=[], raw_tracks=[bridge], plan_footprints=[mine],
        plan_vias=[], plan_tracks=[],
        prelude=_prelude(net_traces=(("bridge", 1),)),
        layer_report=_layer_report())

    assert outcome.line == (
        "read 1 of 2 selected item(s); skipped 1: inter-node copper — 1")


def test_a_reason_the_caller_cannot_name_is_still_counted():
    """The line may never hide a skip: a deficit the named parts do not explain is
    reported as «not read», with the number."""
    mine = _fp("C1")
    tracks = [_item("t1"), _item("t2"), _item("t3")]

    outcome = read_outcome(footprints=[mine], vias=[], raw_tracks=tracks,
                           plan_footprints=[mine], plan_vias=[], plan_tracks=[],
                           prelude=None, layer_report=_layer_report())

    assert outcome.skipped == 3
    assert outcome.line == (
        "read 1 of 4 selected item(s); skipped 3: not read — 3")


def test_a_read_of_nothing_builds_nothing(tmp_path=None):
    """An empty selection: no line (nothing was read, nothing was skipped) and NO
    items — the caller then leaves the board selection alone instead of clearing
    it."""
    outcome = read_outcome(footprints=[], vias=[], raw_tracks=[],
                           plan_footprints=[], plan_vias=[], plan_tracks=[],
                           prelude=None, layer_report=_layer_report())

    assert outcome.line == ""
    assert outcome.items == ()
    assert outcome.replaced is False


class _SelectionSpy:
    """The ONE board touch of a read: what it hands to ``adapter.select_items``."""

    def __init__(self):
        self.calls = []

    def select_items(self, items):
        self.calls.append(list(items))


def test_a_read_of_nothing_does_not_touch_the_board_selection():
    """C3 of Claude's acceptance: an explicit action that took NOTHING must not
    CLEAR the board selection — the write is the read's own, and it happens only
    when the read has something to select back.

    Mutation: ``if outcome.replaced`` -> always, and the spy records a call with
    an empty list: the user's selection is wiped by a read that took nothing."""
    adapter = _SelectionSpy()

    outcome = replace_selection(
        adapter, footprints=[], vias=[], raw_tracks=[],
        plan_footprints=[], plan_vias=[], plan_tracks=[],
        prelude=None, layer_report=_layer_report())

    assert outcome.replaced is False
    assert adapter.calls == []


def test_a_read_that_took_something_writes_exactly_its_items():
    """The positive half of the same rule — without it a mutation that never
    writes at all (``if False``) would look green."""
    adapter = _SelectionSpy()
    mine = _fp("C1")
    kept = _item("v1")

    outcome = replace_selection(
        adapter, footprints=[mine], vias=[kept], raw_tracks=[],
        plan_footprints=[mine], plan_vias=[kept], plan_tracks=[],
        prelude=None, layer_report=_layer_report())

    assert outcome.items == (mine, kept)
    assert adapter.calls == [[mine, kept]]

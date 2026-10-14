#!/usr/bin/env python3
# tests/schematic/test_sheet_names_address.py
"""The (Cluster, Sheet) ADDRESS predicates of kicadstamp/sheet_names.py — the
ONE owner of the instance identity (plan_2026_10_13_root_sheet_clusters, Ч1).

(The module's OTHER half — LazySheetNameMap's laziness contract — lives in
tests/schematic/test_sheet_names.py; the basename here must stay unique, the
test dirs carry no __init__.py.)

The whole phase rests on ONE distinction this module pins down:

  * an EMPTY chain (no entries at all) = the symbol stands on the ROOT sheet of
    the hierarchy — resolve_sheet_path_names drops the symbol's own uuid and
    there is nothing above it. Its identity is ROOT_SHEET ("/");
  * a chain with entries the dictionary cannot name ([None]) = the sheet did NOT
    resolve. Its identity is None, and it must NEVER pass for the root, or a
    same-tagged sub-sheet cluster would be swallowed by the root one.

Everything else (the root marker's meaning, "absent is not a wildcard") follows
from the same two predicates, which is why this file tests THEM and not the
callers.
"""
from kicadstamp.sheet_names import (
    ROOT_SHEET,
    is_root_sheet,
    resolve_sheet_path_names,
    sheet_in_path,
    sheet_of_names,
)


class _FakeFP:
    """A raw footprint exposing sheet_path_uuids for resolve_sheet_path_names.

    The LAST element of the tuple is the symbol's OWN uuid and is dropped by the
    resolver — the project's own convention (kicadstamp/sheet_names.py)."""

    def __init__(self, uuids):
        self.sheet_path_uuids = uuids


# ── the marker itself ───────────────────────────────────────────────────────

def test_the_root_marker_is_the_slash():
    assert ROOT_SHEET == "/"


def test_only_the_marker_is_the_root_sheet():
    assert is_root_sheet(ROOT_SHEET)
    assert not is_root_sheet("Channel_0")
    assert not is_root_sheet("/MCU")          # a sheet NAMED like the marker
    assert not is_root_sheet(None)
    assert not is_root_sheet("")


# ── the fact the whole phase rests on ───────────────────────────────────────

def test_a_symbol_on_the_root_sheet_resolves_to_an_empty_chain():
    """One hop = the symbol's own uuid → the chain (everything above it) is
    EMPTY. This is what used to be indistinguishable from "did not resolve"."""
    assert resolve_sheet_path_names(_FakeFP(("own-uuid",)), {}) == []


def test_a_sub_sheet_symbol_resolves_to_one_entry():
    """Two hops → one entry, named or not. The DEPTH is a property of the
    symbol's own path, never of the name dictionary."""
    assert len(resolve_sheet_path_names(_FakeFP(("sheet-uuid", "own-uuid")), {})) == 1
    assert resolve_sheet_path_names(_FakeFP(("sheet-uuid", "own-uuid")), {}) == [None]


# ── sheet_in_path: root vs unresolved vs a name ─────────────────────────────

def test_the_root_marker_matches_an_empty_chain():
    assert sheet_in_path([], ROOT_SHEET) is True


def test_the_root_marker_does_not_match_an_unresolved_chain():
    """[None] has an entry the dictionary could not name — that is "did not
    resolve", NOT the root. Without this the map-less board would make every
    sub-sheet component pass for a root one."""
    assert sheet_in_path([None], ROOT_SHEET) is False
    assert sheet_in_path([None, None], ROOT_SHEET) is False


def test_a_named_sheet_never_matches_the_root_marker():
    assert sheet_in_path(["MCU"], ROOT_SHEET) is False
    assert sheet_in_path(["Channel_0", "DAC"], ROOT_SHEET) is False


def test_a_named_sheet_still_matches_any_segment_of_the_chain():
    """The pre-existing rule, untouched: not necessarily the LAST segment (a DAC
    sub-sheet inside Channel_0 is still Channel_0's)."""
    chain = ["Channel_0", "DAC"]
    assert sheet_in_path(chain, "Channel_0") is True
    assert sheet_in_path(chain, "DAC") is True
    assert sheet_in_path(chain, "MCU") is False


def test_an_absent_sheet_is_neither_a_wildcard_nor_the_root():
    """None / "" = "not set": it narrows nothing (the caller decides what that
    means) and it is NOT the root — the marker is the only way to say "root"."""
    assert sheet_in_path(["Channel_0"], None) is False
    assert sheet_in_path(["Channel_0"], "") is False
    assert sheet_in_path([], None) is False


def test_no_chain_at_all_names_nothing():
    """None (the caller has no chain) is not an empty chain — being conservative
    keeps an unknown input from passing for the root."""
    assert sheet_in_path(None, ROOT_SHEET) is False
    assert sheet_in_path(None, "Channel_0") is False


def test_an_empty_chain_does_not_match_a_named_sheet():
    assert sheet_in_path([], "Channel_0") is False


# ── sheet_of_names: the identity derivation ────────────────────────────────

def test_the_identity_of_an_empty_chain_is_the_root_marker():
    assert sheet_of_names([]) == ROOT_SHEET
    assert sheet_of_names(()) == ROOT_SHEET


def test_the_identity_of_an_unresolved_chain_is_none():
    """Denis's rule: "не разрешилось" — only when a uuid IS in the chain and the
    dictionary cannot name it."""
    assert sheet_of_names([None]) is None
    assert sheet_of_names([None, None]) is None


def test_the_identity_is_the_first_named_segment():
    assert sheet_of_names(["Channel_0", "DAC"]) == "Channel_0"
    assert sheet_of_names([None, "DAC"]) == "DAC"
    assert sheet_of_names(["Channel_1"]) == "Channel_1"


def test_the_identity_of_no_chain_at_all_is_unknown():
    assert sheet_of_names(None) is None

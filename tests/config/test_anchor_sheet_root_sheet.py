#!/usr/bin/env python3
"""The ROOT marker `"/"` is not a sheet-dictionary USER — one fact, both branches.

Plan: plan_2026_10_21_root_sheet_anchor_on_flat_schematic (Ч1, cells К1-К10).

What went wrong live (Denis, 21.10, profile Test04 -> board Tests/Test02): the
inter-node copper capture wrote `(anchor_sheet "/")` onto a NEW net_traces
record — legal, `sheet_names.ROOT_SHEET` since 14.10 — and the very next Forest
redraw refused with "anchor_sheet is used but sheet name dictionary is empty",
claiming the config needed `schematic_dir` although it was set and read. Test02
is a SINGLE-PAGE schematic: no sub-sheets, so 0 sheets in the map is the truth,
and "/" needs no map at all (it names the EMPTY chain — `sheet_in_path`).

So the check must separate two questions that were conflated:

  * does this record name a sheet that the (empty) dictionary would have to
    resolve? — only for a NON-root name; `"/"` answers itself;
  * WHY is the map empty? — no schematic source configured at all, or the
    schematics were read and simply have no sub-sheets. The old single hint
    ("you need schematic_dir") is false in the second case.

Every OTHER name keeps refusing on an empty map exactly as before — the
surrounding cells are what keep the fix from becoming "any name passes".
"""
import pytest
from types import SimpleNamespace

from kicadstamp.config import Config
from kicadstamp.config.models import ClonePlacement, NetTrace
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.role_narrowing import narrow_candidates_by_sheet
from kicadstamp.sheet_names import (
    ROOT_SHEET, empty_dictionary_hint, sheet_narrowing_disabled_line,
)
from kicadstamp.validation import check_anchor_sheet_configured

BRANCHES = ("net_traces", "clone_placements")

# The two reasons the map can be empty, as the Config itself knows them.
FLAT = dict(schematic_dir=None, schematic_files=["Test02.kicad_sch"])
UNCONFIGURED = dict(schematic_dir=None, schematic_files=[])
EMPTY_MAP = {}

# A non-empty map: a hierarchical schematic that DOES carry sub-sheets.
FILLED_MAP = {"sheet-uuid-0": "Channel_0"}


def _record(branch: str, sheet, retired: bool = False):
    if branch == "net_traces":
        return NetTrace(net="5p__ldo_p3v3__p5v_in", anchor_role="P5V_IN",
                        anchor_sheet=sheet, retired=retired)
    return ClonePlacement(cluster="P5V_IN", cell="ldo", xy=(0.0, 0.0),
                          anchor_role="P5V_IN", anchor_sheet=sheet,
                          retired=retired)


def _cfg(branch: str, sheet, sources: dict, retired: bool = False) -> Config:
    record = _record(branch, sheet, retired)
    if branch == "net_traces":
        return Config(net_traces=[record], **sources)
    return Config(clone_placements=[record], **sources)


# ── К1 / К2: the root marker is not a dictionary user ───────────────────────

@pytest.mark.parametrize("branch", BRANCHES)
def test_root_sheet_anchor_is_not_a_dictionary_user_on_a_flat_schematic(branch):
    """The live case: a single-page schematic (a file read, 0 sub-sheets) and a
    record carrying "/" — the check must stay silent, because the record
    resolves without the map."""
    cfg = _cfg(branch, ROOT_SHEET, FLAT)

    check_anchor_sheet_configured(cfg, sheet_names=EMPTY_MAP)  # must not raise


@pytest.mark.parametrize("branch", BRANCHES)
def test_root_sheet_anchor_is_silent_even_without_any_schematic_source(branch):
    """К6 — the marker needs NO dictionary at all, so "not configured" is not
    its business either (the root chain is empty whatever the map holds)."""
    cfg = _cfg(branch, ROOT_SHEET, UNCONFIGURED)

    check_anchor_sheet_configured(cfg, sheet_names=EMPTY_MAP)  # must not raise


# ── К3 / К4: a non-root name on a flat schematic — refusal, honest reason ────

@pytest.mark.parametrize("branch", BRANCHES)
def test_a_non_root_anchor_on_a_flat_schematic_is_refused_with_the_single_page_reason(branch):
    """The map is legitimately empty, but a name the schematic does not have can
    never match — the refusal must say THAT, not "you need schematic_dir"."""
    cfg = _cfg(branch, "Channel_0", FLAT)

    with pytest.raises(ValidationError) as exc_info:
        check_anchor_sheet_configured(cfg, sheet_names=EMPTY_MAP)

    text = str(exc_info.value)
    assert "sub-sheets" in text
    assert "you need schematic_dir" not in text


# ── К5: no schematic source at all — the OLD text is the true one ────────────

@pytest.mark.parametrize("branch", BRANCHES)
def test_a_non_root_anchor_without_any_schematic_source_is_refused_with_the_config_reason(branch):
    cfg = _cfg(branch, "Channel_0", UNCONFIGURED)

    with pytest.raises(ValidationError) as exc_info:
        check_anchor_sheet_configured(cfg, sheet_names=EMPTY_MAP)

    assert "you need schematic_dir" in str(exc_info.value)


# ── К7 / К8: a filled dictionary — unchanged behaviour ──────────────────────

@pytest.mark.parametrize("branch", BRANCHES)
@pytest.mark.parametrize("sheet", ["Channel_0", ROOT_SHEET])
def test_a_filled_dictionary_never_refuses(branch, sheet):
    cfg = _cfg(branch, sheet, FLAT)

    check_anchor_sheet_configured(cfg, sheet_names=FILLED_MAP)  # must not raise


# ── К9: a retired record is not a user ──────────────────────────────────────

@pytest.mark.parametrize("branch", BRANCHES)
def test_a_retired_record_never_refuses(branch):
    cfg = _cfg(branch, "Channel_0", FLAT, retired=True)

    check_anchor_sheet_configured(cfg, sheet_names=EMPTY_MAP)  # must not raise


# ── К10: a placeholder is NOT the root ──────────────────────────────────────

@pytest.mark.parametrize("branch", BRANCHES)
def test_a_placeholder_anchor_is_not_the_root(branch):
    """`anchor_sheet: 'Channel_{channel}'` is substituted only at resolve time;
    the check sees the raw text, which is not the marker — so it still refuses,
    exactly as before the fix."""
    cfg = _cfg(branch, "Channel_{channel}", FLAT)

    with pytest.raises(ValidationError):
        check_anchor_sheet_configured(cfg, sheet_names=EMPTY_MAP)


# ── the silence is HONEST: the marker really does select the root candidate ──

def test_the_silence_is_honest_the_marker_still_selects_the_root_candidate():
    """К-честность — the check's silence must not be a lie: with an EMPTY map,
    narrowing by "/" keeps the footprint standing on the ROOT sheet (empty
    chain) and drops the one standing on a sub-sheet (chain [None])."""
    root_fp = SimpleNamespace(ref="C1", sheet_path_uuids=("symbol-uuid-root",))
    sub_fp = SimpleNamespace(ref="C2", sheet_path_uuids=("sheet-uuid-0", "symbol-uuid-sub"))

    narrowed = narrow_candidates_by_sheet([root_fp, sub_fp], ROOT_SHEET, EMPTY_MAP)

    assert [fp.ref for fp in narrowed] == ["C1"]


# ── one wording host for the cause (cells for mutations М3 / М5) ────────────

def test_the_hint_names_the_real_cause():
    """The two empty-map causes must not share one sentence."""
    unconfigured = empty_dictionary_hint(None, [])
    flat = empty_dictionary_hint(None, ["Test02.kicad_sch"])

    assert "you need schematic_dir" in unconfigured
    assert "sub-sheets" in flat
    assert ROOT_SHEET in flat
    assert "you need schematic_dir" not in flat


def test_the_log_line_takes_its_cause_from_the_same_host():
    """The GUI's V.3 line (gui/dock_hub._warn_if_sheet_narrowing_disabled) logs
    this sentence verbatim — so the dock holds no wording of its own and the
    line stops claiming "no schematic_dir" on a flat schematic."""
    flat = sheet_narrowing_disabled_line(None, ["Test02.kicad_sch"])
    unconfigured = sheet_narrowing_disabled_line(None, [])

    assert "sub-sheets" in flat
    assert "no schematic_dir" not in flat
    assert "you need schematic_dir" in unconfigured

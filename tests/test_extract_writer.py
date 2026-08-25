#!/usr/bin/env python3
"""Tests for kicadstamp/extract_writer.py — the pure worker-side logic of
ExtractDock's extract-to-file flow, moved out of gui/docks/extract.py in
Phase 2 of the gui god-file decomposition (see
techdocs/handoff/handoff_2026_08_05_architecture_fixes_roadmap.md).

`extract_fn` is injectable — these tests use a capturing fake (no board, no
Qt), mirroring how the GUI tests monkeypatch
gui.docks.extract.extract_template_from_selection.
"""
import json
from pathlib import Path

import pytest
import yaml

from kicadstamp.exceptions import PlacerError
from kicadstamp.extract_writer import run_extract_to_file


def _load(path: Path):
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class _CapturingExtract:
    """Fake extractor that records what it was called with and returns a
    canned template (optionally appending an annotation to the list the
    caller handed it)."""

    def __init__(self, template=None):
        self.calls = []
        self.template = template if template is not None else {
            "vias": [], "components": [], "tracks": [], "layer": "F.Cu"}
        self.raise_error = None

    def __call__(self, adapter, name, params=None, items=None, net_template_role=None,
                 rule_nets=None, annotations=None, raw_selection=False, **kwargs):
        self.calls.append({
            "adapter": adapter, "name": name, "params": params, "items": items,
            "net_template_role": net_template_role, "rule_nets": rule_nets,
            "annotations": annotations, "raw_selection": raw_selection,
            "kwargs": kwargs,
        })
        if self.raise_error is not None:
            raise self.raise_error
        if annotations is not None:
            annotations.append(("role", "field", "hint"))
        return {name: self.template}


def _run(tmp_path, *, name="cell1", params=None, items=None, net_template_role=None,
         rule_nets=None, origin_kwargs=None, save_profile=False, profile_key="cell1",
         profile_path=None, placer_path=None, raw_selection=False, extract_fn=None,
         adapter="ADAPTER", clone_placements=None):
    """Run run_extract_to_file with a fresh cells target under tmp_path and a
    default capturing extractor (unless one is supplied)."""
    target = tmp_path / "cells.yaml"
    fn = extract_fn if extract_fn is not None else _CapturingExtract()
    result = run_extract_to_file(
        adapter,
        name=name, params=params, items=items, net_template_role=net_template_role,
        rule_nets=rule_nets, origin_kwargs=origin_kwargs or {},
        target_path=target, save_profile=save_profile, profile_key=profile_key,
        profile_path=profile_path, placer_path=placer_path,
        raw_selection=raw_selection, extract_fn=fn,
        clone_placements=clone_placements)
    return target, result, fn


# ── cell write ──────────────────────────────────────────────────────────

def test_writes_cell_and_reports_wrote(tmp_path):
    target, result, fn = _run(tmp_path)

    assert "error" not in result
    assert any("Wrote 'cell1' in" in m for m in result["messages"])
    data = _load(target)
    assert "cell1" in data["cells"]
    assert data["cells"]["cell1"]["layer"] == "F.Cu"


def test_success_returns_the_raw_template_dict(tmp_path):
    """Added so the GUI can summarize net_from_role auto-classification
    (see ExtractDock._summarize_net_from_role) without re-running the
    extractor just to inspect what it produced."""
    template = {"vias": [{"net_from_role": "LDO"}], "components": [], "tracks": [], "layer": "F.Cu"}
    _, result, _ = _run(tmp_path, extract_fn=_CapturingExtract(template=template))

    assert result["template_dict"] == {"cell1": template}


def test_overwrite_reports_overwrote(tmp_path):
    target, _, _ = _run(tmp_path)
    target, result, _ = _run(tmp_path)  # same name again

    assert "error" not in result
    assert any("Overwrote 'cell1' in" in m for m in result["messages"])
    assert not any("Wrote 'cell1' in" in m for m in result["messages"])


def test_preserves_unrelated_top_level_keys(tmp_path):
    target = tmp_path / "cells.yaml"
    target.write_text("clone_placements:\n  cp1:\n    cell: x\n", encoding="utf-8")

    run_extract_to_file(
        "ADAPTER", name="cell1", params=None, items=None, net_template_role=None,
        rule_nets=None, origin_kwargs={}, target_path=target, save_profile=False,
        profile_key="cell1", profile_path=None, placer_path=None,
        extract_fn=_CapturingExtract())

    data = _load(target)
    assert "clone_placements" in data
    assert data["clone_placements"]["cp1"]["cell"] == "x"
    assert "cell1" in data["cells"]


# ── Sub-placements clone_placements write-through (2026-08-25, Задание 1) ──

def test_clone_placements_written_into_the_extracted_cell(tmp_path):
    """Checked Sub-placements reach the written cell as its own
    clone_placements: section — the same geometry is referenced, not copied
    flat (the flat exclusion happens earlier, in the GUI payload building)."""
    target, result, _fn = _run(
        tmp_path, name="dac_buf",
        clone_placements=[{
            "name": "ch0_pif_avdd", "cell": "pif_avdd",
            "xy": [5.0, 2.0], "rotation_deg": 90.0, "mirror": True, "layer": "B.Cu",
        }])

    assert "error" not in result
    data = _load(target)
    assert data["cells"]["dac_buf"]["clone_placements"] == [{
        "name": "ch0_pif_avdd", "cell": "pif_avdd",
        "xy": [5.0, 2.0], "rotation_deg": 90.0, "mirror": True, "layer": "B.Cu",
    }]

def test_no_clone_placements_means_no_section_written(tmp_path):
    """The normal (no Sub-placements) path must not gain an empty
    clone_placements: section — clone_placements=None is a no-op."""
    target, _result, _fn = _run(tmp_path, name="cell1")

    data = _load(target)
    assert "clone_placements" not in data["cells"]["cell1"]


# ── Pure-composite extract (2026-08-25 fix) ────────────────────────────────

def test_pure_composite_does_not_call_extract_fn(tmp_path):
    """A selection FULLY covered by Sub-placements has empty `items` but
    non-empty `clone_placements` — extract_fn (which has no concept of
    clone_placements and fatals "nothing to extract" on empty items) must NOT
    be called; the cell dict is synthesized with empty flat lists instead."""
    fn = _CapturingExtract()
    target, result, fn = _run(
        tmp_path, name="dac_buf", items=[],
        clone_placements=[{"name": "ch0_pif_avdd", "cell": "pif_avdd",
                           "xy": [5.0, 2.0]}],
        extract_fn=fn)

    assert "error" not in result
    assert fn.calls == []  # extract_fn never called for a pure composite
    data = _load(target)
    cell = data["cells"]["dac_buf"]
    assert cell["components"] == []
    assert cell["vias"] == []
    assert cell["tracks"] == []
    assert cell["clone_placements"] == [{
        "name": "ch0_pif_avdd", "cell": "pif_avdd", "xy": [5.0, 2.0]}]
    # default Cell layer when no Sub-placement declares one
    assert cell["layer"] == "F.Cu"


def test_pure_composite_layer_from_first_sub_placement(tmp_path):
    """A Sub-placement that declares its own layer gives the pure-composite
    cell a meaningful layer instead of the 'F.Cu' default."""
    target, result, _fn = _run(
        tmp_path, name="dac_buf", items=[],
        clone_placements=[{"name": "a", "cell": "x", "xy": [0.0, 0.0],
                           "layer": "B.Cu"}])

    assert "error" not in result
    cell = _load(target)["cells"]["dac_buf"]
    assert cell["layer"] == "B.Cu"


def test_empty_items_without_clone_placements_still_calls_extract_fn(tmp_path):
    """The old behavior for a genuinely empty selection is untouched: with no
    clone_placements, extract_fn IS still called (the real extractor fatals
    "nothing to extract"; the fake here just records the call) — only the
    pure-composite case skips it."""
    fn = _CapturingExtract()
    _run(tmp_path, name="cell1", items=[], extract_fn=fn)
    assert len(fn.calls) == 1


def test_cell_write_os_error_returns_error(tmp_path):
    # A directory where the cells file should be -> merge_write raises OSError.
    bad_target = tmp_path / "cells.yaml"
    bad_target.mkdir()

    result = run_extract_to_file(
        "ADAPTER", name="cell1", params=None, items=None, net_template_role=None,
        rule_nets=None, origin_kwargs={}, target_path=bad_target, save_profile=False,
        profile_key="cell1", profile_path=None, placer_path=None,
        extract_fn=_CapturingExtract())

    assert "error" in result
    assert "Write failed" in result["error"]


# ── profile write ───────────────────────────────────────────────────────

def test_profile_entry_construction(tmp_path):
    profile = tmp_path / "profiles.yaml"
    target, result, _ = _run(
        tmp_path, save_profile=True, profile_key="prof_cell",
        params={"a": 1}, net_template_role="C1", rule_nets=["GND", "+5V"],
        origin_kwargs={"origin_component_role": "X", "origin_via_net": "GND"},
        profile_path=profile)

    assert "error" not in result
    entry = _load(profile)["extract_profiles"]["prof_cell"]
    # output is display_path(target) — tmp_path is outside the project, so it
    # is the absolute path.
    assert entry["output"] == str(target)
    assert entry["name"] == "cell1"                      # profile_key != name
    assert entry["params"] == {"a": 1}
    assert entry["net_template_role"] == "C1"
    assert entry["rule_nets"] == ["+5V", "GND"]          # sorted
    assert entry["origin_by_component_role"] == "X"      # origin_* -> origin_by_*
    assert entry["origin_by_via_net"] == "GND"
    # both messages: the cell write AND the profile write
    assert any("profile 'prof_cell' in" in m for m in result["messages"])


def test_profile_omits_defaults_when_profile_key_equals_name(tmp_path):
    profile = tmp_path / "profiles.yaml"
    target, _, _ = _run(tmp_path, save_profile=True, profile_key="cell1", profile_path=profile)

    entry = _load(profile)["extract_profiles"]["cell1"]
    assert set(entry.keys()) == {"output"}               # no name/params/... defaults
    assert entry["output"] == str(target)


def test_profile_write_os_error_returns_error(tmp_path):
    bad_profile = tmp_path / "profiles.yaml"
    bad_profile.mkdir()
    target = tmp_path / "cells.yaml"

    result = run_extract_to_file(
        "ADAPTER", name="cell1", params=None, items=None, net_template_role=None,
        rule_nets=None, origin_kwargs={}, target_path=target, save_profile=True,
        profile_key="cell1", profile_path=bad_profile, placer_path=None,
        extract_fn=_CapturingExtract())

    assert "error" in result
    assert "Cell written, but profile write failed" in result["error"]


# ── placer include wiring ───────────────────────────────────────────────

def test_placer_gets_include_entry_deduped(tmp_path):
    placer = tmp_path / "placer.yaml"
    target, result, _ = _run(tmp_path, placer_path=placer)

    assert "error" not in result
    first = _load(placer)
    assert first["include"] == ["cells.yaml"]
    assert any("added 'cells.yaml' to include:" in m for m in result["messages"])

    # Second run: same resolved target -> no duplicate entry.
    target, result, _ = _run(tmp_path, placer_path=placer)
    assert _load(placer)["include"] == ["cells.yaml"]
    assert not any("added 'cells.yaml' to include:" in m for m in result["messages"])


def test_placer_include_skips_self_reference(tmp_path):
    target = tmp_path / "cells.yaml"
    run_extract_to_file(
        "ADAPTER", name="cell1", params=None, items=None, net_template_role=None,
        rule_nets=None, origin_kwargs={}, target_path=target, save_profile=False,
        profile_key="cell1", profile_path=None, placer_path=target,
        extract_fn=_CapturingExtract())

    assert _load(target).get("include") is None  # no include: written into itself


def test_placer_profile_include_guarded_by_non_includable_keys(tmp_path):
    # Profile file carries a root-config-only key -> include: wiring is
    # skipped with an explanatory message.
    placer = tmp_path / "placer.yaml"
    profile = tmp_path / "profiles.yaml"
    profile.write_text("registry_path: somewhere\ncells: {}\n", encoding="utf-8")

    _, result, _ = _run(
        tmp_path, save_profile=True, profile_key="cell1", profile_path=profile,
        placer_path=placer)

    assert "error" not in result
    # The CELL include is written first (unconditionally); only the PROFILE
    # include is guarded by the non-includable keys check.
    assert _load(placer)["include"] == ["cells.yaml"]
    assert any("skipped adding to include" in m for m in result["messages"])


def test_placer_profile_include_written_for_clean_profile(tmp_path):
    placer = tmp_path / "placer.yaml"
    profile = tmp_path / "profiles.yaml"
    profile.write_text("cells: {}\n", encoding="utf-8")

    _, result, _ = _run(
        tmp_path, save_profile=True, profile_key="cell1", profile_path=profile,
        placer_path=placer)

    assert "error" not in result
    # Cell include written first, then the profile include (clean profile ->
    # not guarded).
    assert _load(placer)["include"] == ["cells.yaml", "profiles.yaml"]
    assert not any("skipped adding to include" in m for m in result["messages"])


def test_placer_wiring_os_error_appended_not_fatal(tmp_path):
    # placer_path is a directory -> add_list_entry raises OSError; the call
    # must still succeed with the message appended.
    bad_placer = tmp_path / "placer.yaml"
    bad_placer.mkdir()

    _, result, _ = _run(tmp_path, placer_path=bad_placer)

    assert "error" not in result
    assert any("placer file wiring failed" in m for m in result["messages"])
    assert any("Wrote 'cell1' in" in m for m in result["messages"])


# ── extractor contract ──────────────────────────────────────────────────

def test_extract_fn_receives_full_payload(tmp_path):
    fn = _CapturingExtract()
    items = ["item1", "item2"]
    _run(
        tmp_path, name="cell1", params={"a": 1}, items=items,
        net_template_role="C1", rule_nets=["GND"],
        origin_kwargs={"origin_component_role": "X"},
        extract_fn=fn, adapter="REAL_ADAPTER")

    assert len(fn.calls) == 1
    call = fn.calls[0]
    assert call["adapter"] == "REAL_ADAPTER"
    assert call["name"] == "cell1"
    assert call["params"] == {"a": 1}
    assert call["items"] == items
    assert call["net_template_role"] == "C1"
    assert call["rule_nets"] == ["GND"]
    assert call["raw_selection"] is False
    # origin defaults to None (caller didn't pre-resolve) — extract_fn then
    # derives it internally, unchanged behavior for CLI/direct callers.
    assert call["kwargs"] == {"origin_component_role": "X", "origin": None}


def test_raw_selection_forwarded_and_persisted(tmp_path):
    """raw_selection=True flows through run_extract_to_file into the extractor
    AND into the saved extract_profiles entry (so a profile can replay it)."""
    profile = tmp_path / "profiles.yaml"
    fn = _CapturingExtract()
    _, result, _ = _run(
        tmp_path, save_profile=True, profile_key="cell1", profile_path=profile,
        raw_selection=True, extract_fn=fn)

    assert "error" not in result
    assert fn.calls[0]["raw_selection"] is True
    entry = _load(profile)["extract_profiles"]["cell1"]
    assert entry["raw_selection"] is True


def test_raw_selection_default_omitted_from_profile(tmp_path):
    """raw_selection=False (default) must not seed a junk key in the profile."""
    profile = tmp_path / "profiles.yaml"
    _, _, _ = _run(tmp_path, save_profile=True, profile_key="cell1", profile_path=profile)

    entry = _load(profile)["extract_profiles"]["cell1"]
    assert set(entry.keys()) == {"output"}


def test_annotations_are_collected_and_returned(tmp_path):
    _, result, _ = _run(tmp_path)
    assert result["annotations"] == [("role", "field", "hint")]


def test_extract_error_returns_error_dict(tmp_path):
    fn = _CapturingExtract()
    fn.raise_error = PlacerError("no such role")
    _, result, _ = _run(tmp_path, extract_fn=fn)

    assert result == {"error": "no such role"}

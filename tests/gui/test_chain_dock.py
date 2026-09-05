# tests/gui/test_chain_dock.py
"""
ChainDock tests (2026-09-01, plan rules_to_chains — replaces the old
test_rules_dock.py, which tested RuleDock's now-removed pads TABLE).

The Chain form (gui/docks/chain.py) is deliberately headless AND board-
mutation-free — same reasoning as tests/gui/test_thermal_via_dock.py.
ApplyPipeline/load_config are monkeypatched with fakes that only check what
ChainDock PASSES them (config_path, only=, and that other already-saved
chains survive into the config handed to the pipeline).

The pads are NOT a table anymore — they are leaves in the Config tree, edited
via the ChainDialog's pad mode. This file therefore tests:
  - chain mode: _build_chain_dict / load_chain / new_chain / _persist_chain;
  - pad mode: _build_spoke_dict / load_pad / new_pad / _persist_pad;
  - the shared redraw/bulk machinery (moved to the tree's context menu).
"""
import dataclasses
from types import SimpleNamespace

import gui.docks.chain as chain_mod
from gui.docks.chain import ChainDock
from kicadstamp.config import (Config, RuntimeContext, chain_effective_name,
                               load_chain as _lc)
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _fill_cell_defaults(data: dict) -> dict:
    """s-expr omits default-valued Cell fields (layer='F.Cu', empty
    vias/components/tracks lists); re-apply them so the raw-dict assertions
    stay identical to the old yaml.safe_load reads."""
    for entry in data.get("cells", {}).values():
        entry.setdefault("layer", "F.Cu")
        entry.setdefault("vias", [])
        entry.setdefault("components", [])
        entry.setdefault("tracks", [])
        entry.setdefault("clone_placements", [])
    return data


def _load(path) -> dict:
    return _fill_cell_defaults(sexp_to_dict(path.read_text(encoding="utf-8"))) or {}


def _make_dock(main_window, tmp_path, data=None):
    target_file = tmp_path / "chains.sexp"
    _write(target_file, data if data is not None else {"chains": []})
    dock = ChainDock(main_window)
    dock.set_root_path(target_file)
    return dock, target_file


def _bulk_graph(tmp_path):
    """A project root that includes two chain files sharing net +3V3 — the
    cross-file scenario Bulk-set Cell for net exists for (a net's chains
    routinely live in different included files)."""
    target = tmp_path / "chains.sexp"
    _write(target, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "17", "cell": "old_a"}]},
    ]})
    sibling = tmp_path / "sibling.sexp"
    _write(sibling, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": [{"pad": "26", "cell": "old_b"}]},
        {"net": "GND", "anchor_role": "FPGA", "spokes": []},
    ]})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["chains.sexp", "sibling.sexp"]})
    return target, sibling, root


# ── Chain mode: building the entry dict ────────────────────────────────────

def test_build_chain_dict_anchor_role_with_sheet_and_cluster(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_widget.load(mode="anchor", role="FPGA", sheet="Channel_1", cluster="PWR_BANK")
    dock.retired_checkbox.setChecked(True)
    dock.skip_checkbox.setChecked(True)

    entry = dock._build_chain_dict()

    assert entry == {
        "net": "+3V3", "anchor_role": "FPGA", "anchor_sheet": "Channel_1",
        "anchor_cluster": "PWR_BANK", "retired": True, "skip": True,
    }


def test_build_chain_dict_point_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("GND")
    dock.origin_widget.load(mode="point", point="fpga_center")

    entry = dock._build_chain_dict()

    assert entry["net"] == "GND"
    assert entry["anchor_point"] == "fpga_center"


def test_net_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    assert dock._build_chain_dict() is None
    assert any("Net is required" in r.message for r in caplog.records)


def test_anchor_ref_and_role_together_is_blocked(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_mode_combo.setCurrentIndex(0)  # anchor
    dock.anchor_ref_edit.setText("U1")
    dock.anchor_role_edit.setCurrentText("FPGA")

    assert dock._build_chain_dict() is None
    assert any("mutually exclusive" in r.message for r in caplog.records)


def test_build_chain_dict_includes_comment(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_widget.load(mode="anchor", role="FPGA")
    dock.comment_edit.setText("a chain note")
    entry = dock._build_chain_dict()
    assert entry["comment"] == "a chain note"


def test_origin_mode_toggles_row_visibility(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)

    def visible(row):
        # isVisibleTo(dock) would also depend on the current page — checking
        # against the row's own immediate parent isolates just
        # AnchorOriginWidget's own setVisible() toggle.
        return row.isVisibleTo(row.parentWidget())

    origin = dock.origin_widget
    dock.origin_mode_combo.setCurrentIndex(0)  # anchor
    assert visible(origin._anchor_row) and not visible(origin._point_row)

    dock.origin_mode_combo.setCurrentIndex(1)  # point
    assert visible(origin._point_row) and not visible(origin._anchor_row)
    assert origin.point_edit.isVisibleTo(origin)


# ── Pad mode: building the spoke dict ──────────────────────────────────────

def _fill_pad_form(dock, **overrides):
    fields = dict(pad="17", cell="cap_pair", shift_x="1.2", shift_y="-0.5",
                  rotation="90", cluster="", retired=False, skip=False)
    fields.update(overrides)
    dock.spoke_pad_edit.setText(fields["pad"])
    dock.spoke_cell_combo.setCurrentText(fields["cell"])
    dock.spoke_shift_x_edit.setText(fields["shift_x"])
    dock.spoke_shift_y_edit.setText(fields["shift_y"])
    dock.spoke_rotation_edit.setText(fields["rotation"])
    dock.spoke_cluster_combo.setCurrentText(fields["cluster"])
    dock.spoke_retired_checkbox.setChecked(fields["retired"])
    dock.spoke_skip_checkbox.setChecked(fields["skip"])


def test_build_spoke_dict_cartesian(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    _fill_pad_form(dock)

    spoke = dock._build_spoke_dict()

    assert spoke == {"pad": "17", "cell": "cap_pair", "shift_x_mm": 1.2,
                     "shift_y_mm": -0.5, "rotation_deg": 90.0}


def test_spoke_pad_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    _fill_pad_form(dock, pad="")
    assert dock._build_spoke_dict() is None
    assert any("Pad is required" in r.message for r in caplog.records)


def test_spoke_cell_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    _fill_pad_form(dock, cell="")
    assert dock._build_spoke_dict() is None
    assert any("Cell is required" in r.message for r in caplog.records)


def test_build_spoke_dict_polar(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    _fill_pad_form(dock, rotation="")  # no rotation in the polar-only expected
    dock.spoke_mode_combo.setCurrentIndex(1)
    dock.spoke_radius_edit.setText("5.0")
    dock.spoke_angle_edit.setText("37")

    spoke = dock._build_spoke_dict()

    assert spoke == {"pad": "17", "cell": "cap_pair", "radius_mm": 5.0,
                     "angle_deg": 37.0}


def test_polar_mode_requires_both_radius_and_angle(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    _fill_pad_form(dock)
    dock.spoke_mode_combo.setCurrentIndex(1)
    dock.spoke_radius_edit.setText("5.0")
    dock.spoke_angle_edit.setText("")

    assert dock._build_spoke_dict() is None
    assert any("needs both Radius and Angle" in r.message for r in caplog.records)


def test_mode_toggle_enables_only_the_active_fields(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.spoke_mode_combo.setCurrentIndex(0)
    assert dock.spoke_shift_x_edit.isEnabled()
    assert not dock.spoke_radius_edit.isEnabled()
    dock.spoke_mode_combo.setCurrentIndex(1)
    assert not dock.spoke_shift_x_edit.isEnabled()
    assert dock.spoke_radius_edit.isEnabled()


# ── Chain mode: persist + load ─────────────────────────────────────────────

def test_save_writes_chains_section_and_preserves_other_keys(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"cells": {"c1": {"components": []}}})
    dock.net_edit.setCurrentText("+3V3")
    dock.origin_widget.load(mode="anchor", role="FPGA")
    dock._on_save()

    data = _load(target)
    assert data["chains"] == [{"net": "+3V3", "anchor_role": "FPGA"}]
    assert data["cells"]["c1"]["components"] == []
    assert any("Wrote" in r.message for r in caplog.records)


def test_save_overwrites_by_name_or_net(main_window, tmp_path, caplog):
    dock, target = _make_dock(main_window, tmp_path, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA", "spokes": []},
    ]})
    dock.load_chain({"net": "+3V3", "anchor_role": "FPGA", "spokes": []})
    dock.net_edit.setCurrentText("+3V3")
    dock.comment_edit.setText("updated")
    dock._on_save()

    data = _load(target)
    assert data["chains"] == [{"net": "+3V3", "anchor_role": "FPGA",
                               "comment": "updated", "spokes": []}]
    assert any("Overwrote" in r.message for r in caplog.records)


def test_comment_saves_and_loads_back(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA", "comment": "a chain note", "spokes": []},
    ]})
    data = _load(target)
    dock.load_chain(data["chains"][0])
    assert dock.comment_edit.text() == "a chain note"


def test_save_without_a_file_picked_shows_error(main_window, caplog):
    dock = ChainDock(main_window)
    dock.net_edit.setCurrentText("+3V3")
    dock._on_save()
    assert any("Set the project root first" in r.message for r in caplog.records)


# ── Pad mode: persist + load ───────────────────────────────────────────────

def test_load_pad_fills_form_and_remembers_parent(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    chain = {"net": "+3V3", "anchor_ref": "U1", "spokes": [
        {"pad": "17", "cell": "fpga", "shift_x_mm": 1.2},
        {"pad": "3", "cell": "cap", "radius_mm": 5.0, "angle_deg": 37.0},
    ]}

    dock.load_pad(chain, 0)

    assert dock._chain_entry == chain
    assert dock._pad_index == 0
    assert dock.spoke_pad_edit.text() == "17"
    assert dock.spoke_cell_combo.currentText() == "fpga"
    assert dock._stack.currentWidget() is dock._pad_page


def test_new_pad_clears_form_and_appends(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    chain = {"net": "+3V3", "anchor_ref": "U1", "spokes": []}

    dock.new_pad(chain, tmp_path)

    assert dock._chain_entry == chain
    assert dock._pad_index is None  # append
    assert dock.spoke_pad_edit.text() == ""
    assert dock._stack.currentWidget() is dock._pad_page


def test_persist_pad_updates_existing_spoke_in_place(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"chains": [
        {"net": "+3V3", "anchor_ref": "U1",
         "spokes": [{"pad": "17", "cell": "fpga"}, {"pad": "3", "cell": "cap"}]},
    ]})
    chain = _load(target)["chains"][0]
    dock.load_pad(chain, 0)
    dock.spoke_cell_combo.setCurrentText("new_cell")
    dock._on_save_pad()

    data = _load(target)
    assert data["chains"][0]["spokes"] == [
        {"pad": "17", "cell": "new_cell"}, {"pad": "3", "cell": "cap"}]


def test_persist_pad_appends_when_new(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path, {"chains": [
        {"net": "+3V3", "anchor_ref": "U1", "spokes": [{"pad": "17", "cell": "fpga"}]},
    ]})
    chain = _load(target)["chains"][0]
    dock.new_pad(chain, target)
    _fill_pad_form(dock, pad="26", cell="cap")
    dock._on_save_pad()

    data = _load(target)
    assert [s["pad"] for s in data["chains"][0]["spokes"]] == ["17", "26"]


def test_save_without_a_parent_chain_shows_error(main_window, caplog):
    dock = ChainDock(main_window)
    dock.set_root_path(None)
    _fill_pad_form(dock)
    dock._on_save_pad()
    assert any("Set the project root first" in r.message for r in caplog.records)


# ── new_chain / load_chain (entry points) ──────────────────────────────────

def test_new_chain_resets_form_and_targets_file(main_window, tmp_path):
    dock, target = _make_dock(main_window, tmp_path)
    dock.net_edit.setCurrentText("stale")
    dock.name_edit.setText("stale")

    dock.new_chain(target)

    assert dock.net_edit.currentText() == ""
    assert dock.name_edit.text() == ""
    assert dock._chain_entry is None
    assert dock._stack.currentWidget() is dock._chain_page


def test_load_chain_anchor_role_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.load_chain({
        "net": "+3V3", "name": "fpga_3v3", "anchor_role": "FPGA", "anchor_sheet": "Channel_1",
        "anchor_cluster": "PWR_BANK", "retired": True, "skip": True, "spokes": [],
    })
    assert dock.net_edit.currentText() == "+3V3"
    assert dock.name_edit.text() == "fpga_3v3"
    assert dock.anchor_role_edit.currentText() == "FPGA"
    assert dock.anchor_sheet_edit.currentText() == "Channel_1"
    assert dock.anchor_cluster_edit.currentText() == "PWR_BANK"
    assert dock.retired_checkbox.isChecked() is True
    assert dock.skip_checkbox.isChecked() is True
    assert dock._stack.currentWidget() is dock._chain_page


def test_load_chain_point_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.load_chain({"net": "+3V3", "anchor_point": "fpga_center", "spokes": []})
    assert dock.point_edit.currentText() == "fpga_center"


# ── set_root_path / refresh_known_* ────────────────────────────────────────

def test_set_root_path_populates_cell_and_point_combos(main_window, tmp_path):
    _write(tmp_path / "sub.sexp", {"cells": {"cap_pair": {}}, "points": {"fpga_center": {"xy": [0, 0]}}})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["sub.sexp"]})
    dock = ChainDock(main_window)
    dock.set_root_path(root)
    cell_texts = [dock.spoke_cell_combo.itemText(i) for i in range(dock.spoke_cell_combo.count())]
    assert "cap_pair" in cell_texts


def test_set_root_path_none_clears_combos(main_window):
    dock = ChainDock(main_window)
    dock.set_root_path(None)
    assert dock._path is None
    assert dock.spoke_cell_combo.count() == 0


def test_refresh_known_roles_populates_anchor_and_spoke_cluster_combos(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    snapshot = [SimpleNamespace(role="FPGA", cluster="PWR_BANK"),
                SimpleNamespace(role="FPGA", cluster="PWR_BANK"),
                SimpleNamespace(role="ADC", cluster="ANALOG")]
    dock.refresh_known_roles(snapshot)
    clusters = [dock.spoke_cluster_combo.itemText(i) for i in range(dock.spoke_cluster_combo.count())]
    assert clusters == ["ANALOG", "PWR_BANK"]


def test_refresh_known_nets_populates_net_combo(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    board = SimpleNamespace(adapter=SimpleNamespace(
        get_all_nets=lambda: [SimpleNamespace(name="+3V3"), SimpleNamespace(name="GND")]))
    dock.refresh_known_nets(board)
    nets = [dock.net_edit.itemText(i) for i in range(dock.net_edit.count())]
    assert nets == ["+3V3", "GND"]


# ── Redraw (driven from the Config tree's context menu) ────────────────────

def test_redraw_chain_preserves_other_entries_for_registry_safety(
        main_window, tmp_path, monkeypatch):
    """Same correctness property as the old RuleDock equivalent: the chain
    being redrawn replaces ITS identity in the config copy, while OTHER
    already-saved chains survive untouched — so registry pruning can't drop
    them (plan rule_spoke_fixes §redraw)."""
    dock, _ = _make_dock(main_window, tmp_path, {"chains": [
        {"name": "saved_other", "net": "GND", "anchor_role": "FPGA", "spokes": []},
    ]})
    fake_cfg = Config(chains=[_lc({"name": "saved_other", "net": "GND",
                                   "anchor_role": "FPGA", "spokes": []})])
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(chain_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    chain_obj = _lc({"net": "+3V3", "anchor_role": "FPGA", "spokes": []})
    payload = dock._collect_redraw_payload([chain_obj])

    names = [chain_effective_name(c) for c in payload["cfg"].chains]
    assert "saved_other" in names
    assert "+3V3" in names


def test_redraw_chain_resolves_cells_via_project_root_not_chain_file(
        main_window, tmp_path):
    """The chain file (self._path) itself carries no cells: key — redraw must
    load the whole project (root include graph) so every spoke's cell is
    found. Uses REAL load_config (not monkeypatched)."""
    sub = tmp_path / "chains.sexp"
    _write(sub, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA",
         "spokes": [{"pad": "17", "cell": "cap_pair"}]},
    ]})
    _write(tmp_path / "cells.sexp", {"cells": {"cap_pair": {"components": []}}})
    root = tmp_path / "root.sexp"
    _write(root, {"include": ["chains.sexp", "cells.sexp"]})
    dock = ChainDock(main_window)
    dock.set_root_path(root)

    chain_obj = _lc({"net": "+3V3", "anchor_role": "FPGA",
                     "spokes": [{"pad": "17", "cell": "cap_pair"}]})
    payload = dock._collect_redraw_payload([chain_obj])

    assert payload is not None
    assert payload["path"] == root


def test_redraw_chains_splices_all_into_one_config(main_window, tmp_path, monkeypatch):
    """The Tools menu's "Redraw chains..." (anchor node) redraws EVERY chain
    under that anchor in ONE ApplyPipeline run — both effective names go into
    the --only filter, both chains spliced into the config copy, no duplicate."""
    dock, _ = _make_dock(main_window, tmp_path)
    fake_cfg = Config()
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(chain_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    chain_a = _lc({"net": "+3V3", "anchor_ref": "U1", "spokes": []})
    chain_b = _lc({"net": "GND", "anchor_ref": "U1", "spokes": []})
    payload = dock._collect_redraw_payload([chain_a, chain_b])

    assert payload["names"] == ["+3V3", "GND"]
    names = [chain_effective_name(c) for c in payload["cfg"].chains]
    assert names == ["+3V3", "GND"]


def test_redraw_spoke_isolates_only_the_selected_spoke(main_window, tmp_path):
    """The one property that makes "Redraw spoke" different from "Redraw
    chain": every OTHER spoke gets a temporary skip=True injected into the
    pipeline's copy (never written back)."""
    dock, _ = _make_dock(main_window, tmp_path)
    base = _lc({"net": "+3V3", "anchor_role": "FPGA", "spokes": [
        {"pad": "17", "cell": "fpga"}, {"pad": "26", "cell": "cap"}]})
    # The same transformation ChainDock.redraw_pad does: skip every spoke but
    # the selected one (index 0).
    isolated = dataclasses.replace(
        base, spokes=[dataclasses.replace(s, skip=(i != 0))
                      for i, s in enumerate(base.spokes)])
    payload = dock._collect_redraw_payload([isolated])
    assert payload is not None
    cfg_chain = payload["cfg"].chains[-1]
    assert cfg_chain.spokes[0].skip is False
    assert cfg_chain.spokes[1].skip is True
    # The in-memory parent chain (from the tree) is untouched.
    assert base.spokes[0].skip is False and base.spokes[1].skip is False


# ── Bulk-set Cell for net ──────────────────────────────────────────────────

def test_bulk_dialog_preview_shows_chains_and_pads(main_window, tmp_path):
    from gui.docks.chain import BulkSetCellDialog
    _target, _sibling, root = _bulk_graph(tmp_path)

    dlg = BulkSetCellDialog(root, main_window)
    dlg.net_combo.setCurrentText("+3V3")
    dlg._refresh_preview()

    assert "+3V3" in dlg.preview_label.text()
    assert "17" in dlg.preview_label.text()
    assert "26" in dlg.preview_label.text()


def test_bulk_set_cell_applies_via_dialog(main_window, tmp_path, monkeypatch):
    """bulk_set_cell(net_hint) opens the dialog, and on Accept applies the
    chosen cell across the whole graph — the write itself is
    _apply_bulk_cell_set (tested separately below)."""
    target, sibling, root = _bulk_graph(tmp_path)
    dock = ChainDock(main_window)
    dock.set_root_path(root)

    import gui.docks.chain as _chain_mod

    def _fake_exec(self):
        self.net_combo.setCurrentText("+3V3")
        self.cell_combo.setCurrentText("new_cell")
        return _chain_mod.QDialog.DialogCode.Accepted

    monkeypatch.setattr(_chain_mod.BulkSetCellDialog, "exec", _fake_exec)
    dock.bulk_set_cell("+3V3")

    assert _load(target)["chains"][0]["spokes"][0]["cell"] == "new_cell"
    assert _load(sibling)["chains"][0]["spokes"][0]["cell"] == "new_cell"


def test_apply_bulk_cell_set_writes_all_chains_on_net_across_files(main_window, tmp_path):
    target, sibling, root = _bulk_graph(tmp_path)
    dock = ChainDock(main_window)
    dock.set_root_path(root)

    dock._apply_bulk_cell_set("+3V3", "new_cell")

    assert _load(target)["chains"][0]["spokes"][0]["cell"] == "new_cell"
    assert _load(sibling)["chains"][0]["spokes"][0]["cell"] == "new_cell"
    # GND chain (other net) untouched.
    assert _load(sibling)["chains"][1]["spokes"] == []


def test_apply_bulk_cell_set_no_chains_on_net_shows_error(main_window, tmp_path, caplog):
    _target, _sibling, root = _bulk_graph(tmp_path)
    dock = ChainDock(main_window)
    dock.set_root_path(root)
    dock._apply_bulk_cell_set("NOPE", "cell")
    assert any("No chains on net 'NOPE'" in r.message for r in caplog.records)


# ── Pad page actions (2026-09-05, design config_qview_chain_entity_pages §4) ─

def test_pad_page_has_apply_and_redraw_buttons(main_window, tmp_path):
    """The pad page (spoke editor, now the Config right-QView page) carries the
    explicit 'Apply' (commit the form) and 'Redraw' (apply the current form to
    the board) actions. Redraw needs an already-saved spoke to isolate — it is
    disabled on a blank/new pad and enabled once an existing pad loads."""
    dock, _ = _make_dock(main_window, tmp_path)
    assert dock.pad_apply_button.text() == "Apply"
    assert dock.pad_redraw_button.text() == "Redraw"
    assert dock.pad_redraw_button.isEnabled() is False  # blank form

    chain = {"net": "+3V3", "anchor_role": "FPGA",
             "spokes": [{"pad": "17", "cell": "fpga"}]}
    dock.load_pad(chain, 0)
    assert dock.pad_redraw_button.isEnabled() is True
    dock.new_pad(chain, tmp_path)
    assert dock.pad_redraw_button.isEnabled() is False  # brand-new, unsaved


def test_pad_apply_button_commits_pad_form(main_window, tmp_path):
    """The pad page 'Apply' button persists the current pad-mode form into the
    config (same write as _on_save_pad/_persist_pad) — it replaces the spoke at
    its index in the parent chain and leaves the other spokes untouched."""
    dock, target = _make_dock(main_window, tmp_path, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA",
         "spokes": [{"pad": "17", "cell": "fpga"}, {"pad": "26", "cell": "cap"}]}]})
    chain = _load(target)["chains"][0]
    dock.load_pad(chain, 0)
    dock.spoke_cell_combo.setCurrentText("new_cell")
    dock.pad_apply_button.click()

    written = _load(target)["chains"][0]["spokes"]
    assert written[0]["cell"] == "new_cell"
    assert written[1]["cell"] == "cap"


def test_pad_redraw_button_applies_current_form_without_writing(main_window, tmp_path, monkeypatch):
    """The pad page 'Redraw' applies the CURRENT form to the board (only this
    spoke, every other spoke skipped) WITHOUT writing the config — the spliced
    chain handed to the redraw path carries the form's cell, and the file on
    disk stays untouched (Placer-style form redraw, not file redraw)."""
    dock, target = _make_dock(main_window, tmp_path, {"chains": [
        {"net": "+3V3", "anchor_role": "FPGA",
         "spokes": [{"pad": "17", "cell": "fpga"}, {"pad": "26", "cell": "cap"}]}]})
    chain = _load(target)["chains"][0]
    dock.load_pad(chain, 0)
    dock.spoke_cell_combo.setCurrentText("new_cell")

    captured = {}
    monkeypatch.setattr(
        dock, "redraw_pad",
        lambda chain_dict, pad_index: captured.update(
            {"chain": chain_dict, "pad_index": pad_index}))
    dock.redraw_pad_form()

    assert captured["pad_index"] == 0
    assert captured["chain"]["spokes"][0]["cell"] == "new_cell"
    assert captured["chain"]["spokes"][1]["cell"] == "cap"
    # Redraw never writes the config — the on-disk spoke is unchanged.
    assert _load(target)["chains"][0]["spokes"][0]["cell"] == "fpga"

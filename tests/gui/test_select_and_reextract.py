# tests/gui/test_select_and_reextract.py
"""
GUI smoke tests for the clone-item resolver's two consumers (handoff
2026-08-25 clone_item_resolver_select_and_reextract):

  - PlacerDock "Select on board" — resolves the current form's placement and
    hands the items to adapter.select_items().
  - ExtractDock "Re-extract from current board state" — pops a placement combo
    for the picked profile/Cell, then re-runs the extract with items= from the
    resolver instead of the GUI selection.

Headless and board-mutation-free: resolve_clone_board_items / run_extract_to_file
are monkeypatched with fakes that only check what the docks PASS them, the same
"fake the seam, never touch a live board" discipline as every other dock test.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from gui.docks.extract import ExtractDock
from kicadstamp.config import (Cell, Config, RuntimeContext, TemplateComponentSlot)
from kicadstamp.config.sexp_format import dict_to_sexp

from tests.gui.test_placer_dock import _make_cell_and_dock


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _root_sexp(tmp_path, out, with_profile=True):
    data = {
        "clone_placements": [{"cluster": "Ch0_PI", "cell": "pi_filter", "xy": [0.0, 0.0]}],
        "cells": {"pi_filter": {
            "components": [{"role": "C_IN"}],
        }},
    }
    if with_profile:
        data["extract_profiles"] = {"myprofile": {"output": str(out), "name": "pi_filter"}}
    root = tmp_path / "root.sexp"
    _write(root, data)
    return root


# ── PlacerDock: Select on board ────────────────────────────────────────────

def test_select_on_board_calls_select_items(main_window, tmp_path, monkeypatch):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setCurrentText("Ch0_PI")
    dock.x_edit.setText("0")
    dock.y_edit.setText("0")

    adapter = MagicMock()
    selected = []
    adapter.select_items.side_effect = lambda items: selected.append(list(items))
    main_window.connection.board = SimpleNamespace(adapter=adapter)

    cell = Cell(name="pi_filter", components=[TemplateComponentSlot(role="C_IN")])
    monkeypatch.setattr(dock, "_load_target_config",
                        lambda silent=False: (Config(cells={"pi_filter": cell}), RuntimeContext()))

    captured = {}

    def fake_resolve(adapter_arg, cfg, ctx, placement, registry_path=None,
                     track_registry_path=None):
        captured["placement"] = placement
        return [MagicMock(), MagicMock()]

    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.resolve_clone_board_items",
        fake_resolve)

    dock._on_select_on_board()

    assert captured["placement"].cluster == "Ch0_PI"
    assert captured["placement"].cell == "pi_filter"
    assert len(selected) == 1
    assert len(selected[0]) == 2


# ── ExtractDock: Re-extract placement combo ────────────────────────────────

def test_re_extract_combo_populates_for_picked_profile(main_window, tmp_path):
    root = _root_sexp(tmp_path, tmp_path / "cells_out.sexp")
    dock = ExtractDock(main_window)
    dock.set_root_path(root)

    dock.pick_profile("myprofile")

    assert dock._re_extract_cell_name == "pi_filter"
    items = [dock.re_extract_placement_combo.itemText(i)
             for i in range(dock.re_extract_placement_combo.count())]
    assert items == ["Ch0_PI"]
    assert dock.re_extract_button.isEnabled()


def test_re_extract_combo_empty_for_unplaced_cell(main_window, tmp_path):
    root = _root_sexp(tmp_path, tmp_path / "cells_out.sexp")
    dock = ExtractDock(main_window)
    dock.set_root_path(root)

    # A cell nobody clones -> no placements -> button stays disabled.
    dock._set_re_extract_target("unplaced_cell", None, {})

    assert dock.re_extract_placement_combo.count() == 0
    assert not dock.re_extract_button.isEnabled()


# ── ExtractDock: re-extract passes resolver items= through ─────────────────

def test_re_extract_passes_resolver_items_to_extract(main_window, tmp_path, monkeypatch):
    root = _root_sexp(tmp_path, tmp_path / "cells_out.sexp", with_profile=False)
    dock = ExtractDock(main_window)
    dock.set_root_path(root)

    adapter = MagicMock()
    fp = MagicMock()
    main_window.connection.board = SimpleNamespace(adapter=adapter)

    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.resolve_clone_board_items",
        lambda adapter_arg, cfg, ctx, clone, registry_path=None,
               track_registry_path=None: [fp])

    captured = {}

    def fake_run(adapter_arg, *, name, params, items, net_template_role, rule_nets,
                 origin_kwargs, target_path, save_profile, profile_key, profile_path,
                 placer_path, raw_selection, extract_fn):
        captured["items"] = items
        captured["name"] = name
        return {"messages": ["ok"], "annotations": [], "template_dict": {}}

    monkeypatch.setattr("gui.docks.extract.run_extract_to_file", fake_run)

    result = dock._run_re_extract({
        "root_path": root,
        "placer_path": root,
        "board": main_window.connection.board,
        "cell_name": "pi_filter",
        "placement_name": "Ch0_PI",
        "profile_key": None,
        "profile_entry": {},
        "target_path": tmp_path / "cells_out.sexp",
    })

    assert result["messages"] == ["ok"]
    assert captured["name"] == "pi_filter"
    assert captured["items"] == [fp]

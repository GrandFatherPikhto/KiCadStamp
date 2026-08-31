# tests/gui/test_profile_import.py
"""Best-effort GUI tests for Edit > Import from profile... (2026-08-31, plan
copy_cell_entity_from_profile) — the picker dialog in gui/docks/profile_import.py.
Modal QMessageBox dialogs are monkeypatched away so the tests cannot block; the
actual copy + collision logic itself is covered thoroughly (and headlessly) by
tests/test_profile_copy.py."""
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

import gui.docks.profile_import as profile_import_mod
from gui.docks.profile_import import ProfileImportDialog, run_import_dialog


def _write_sexp(path: Path, data: dict) -> Path:
    path.write_text(dict_to_sexp(data), encoding="utf-8")
    return path


def _read(path: Path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _silence_boxes(monkeypatch, warnings=None, infos=None):
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: (warnings.append(a[2]) if warnings is not None else None))
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: (infos.append(a[2]) if infos is not None else None))


def test_import_dialog_browse_and_copy_cell_into_root(qapp, tmp_path, monkeypatch):
    source = _write_sexp(tmp_path / "source.sexp", {"cells": {
        "leaf": {"layer": "F.Cu", "components": [{"role": "R1"}]}}})
    root = _write_sexp(tmp_path / "root.sexp", {"cells": {}})
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), "Config files (*.sexp *.json)"))
    _silence_boxes(monkeypatch)

    dlg = ProfileImportDialog(None, root)
    dlg._browse()

    assert dlg._source_path == source
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 0).text() == "Cell"
    assert dlg.table.item(0, 1).text() == "leaf"

    dlg.table.selectRow(0)
    dlg._import()

    assert "leaf" in _read(root)["cells"]
    assert _read(root)["cells"]["leaf"]["components"][0]["role"] == "R1"


def test_import_dialog_collision_shows_error_and_leaves_root_unchanged(qapp, tmp_path, monkeypatch):
    source = _write_sexp(tmp_path / "source.sexp", {"cells": {
        "leaf": {"layer": "F.Cu", "components": [{"role": "R1"}]}}})
    root = _write_sexp(tmp_path / "root.sexp", {"cells": {"leaf": {"layer": "F.Cu"}}})
    before = root.read_bytes()
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))
    warnings = []
    _silence_boxes(monkeypatch, warnings=warnings)

    dlg = ProfileImportDialog(None, root)
    dlg._browse()
    dlg.table.selectRow(0)
    dlg._import()

    assert warnings, "collision must surface as a visible message"
    assert "already exists" in warnings[0]
    assert root.read_bytes() == before  # nothing written


def test_run_import_dialog_without_root_is_graceful(qapp, monkeypatch):
    """No project root -> a clear message, no dialog, no crash."""
    messages = []

    class _RootDock:
        root_path = None

    class _Window:
        root_metadata_dock = _RootDock()

    monkeypatch.setattr(profile_import_mod, "show_message",
                        lambda text, style, log: messages.append(text))

    run_import_dialog(_Window())

    assert messages and "Set the project root first" in messages[0]

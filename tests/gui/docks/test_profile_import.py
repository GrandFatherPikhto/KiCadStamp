# tests/gui/test_profile_import.py
"""Best-effort GUI tests for Edit > Import from profile... (2026-08-31, plan
copy_cell_entity_from_profile) — the picker dialog in gui/docks/profile_import.py.
Modal QMessageBox dialogs are monkeypatched away so the tests cannot block; the
actual copy + collision logic itself is covered thoroughly (and headlessly) by
tests/test_profile_copy.py."""
from pathlib import Path

from PyQt6.QtCore import Qt
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


def _check_row(dlg, row: int) -> None:
    dlg.table.item(row, 0).setCheckState(Qt.CheckState.Checked)


def test_import_dialog_browse_and_check_cell_into_root(qapp, tmp_path, monkeypatch):
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
    assert dlg.table.item(0, 1).text() == "Cell"
    assert dlg.table.item(0, 2).text() == "leaf"
    assert dlg.import_button.isEnabled() is False  # nothing checked yet

    _check_row(dlg, 0)
    assert dlg.import_button.isEnabled() is True
    dlg._import()

    assert "leaf" in _read(root)["cells"]
    assert _read(root)["cells"]["leaf"]["components"][0]["role"] == "R1"


def test_import_dialog_multi_select_imports_all_checked(qapp, tmp_path, monkeypatch):
    source = _write_sexp(tmp_path / "source.sexp", {
        "cells": {
            "c1": {"layer": "F.Cu", "components": [{"role": "R1"}]},
            "c2": {"layer": "F.Cu", "components": [{"role": "R2"}]},
        },
        "entities": [{"name": "E1", "cell": "c1", "nets": {"R1": "/N1"}}],
    })
    root = _write_sexp(tmp_path / "root.sexp", {"cells": {}, "entities": []})
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))
    infos = []
    _silence_boxes(monkeypatch, infos=infos)

    dlg = ProfileImportDialog(None, root)
    dlg._browse()
    assert dlg.table.rowCount() == 3  # c1, c2, E1

    # tick c2 (row 1) and E1 (row 2) — NOT c1
    _check_row(dlg, 1)
    _check_row(dlg, 2)
    dlg._import()

    data = _read(root)
    assert set(data["cells"]) == {"c1", "c2"}  # E1's cell c1 comes via closure
    assert data["entities"] == [{"name": "E1", "cell": "c1", "nets": {"R1": "/N1"}}]
    assert infos, "multi-import must report a summary"
    assert "E1" in infos[0] and "c2" in infos[0]


def test_import_dialog_populates_chain_row_without_crashing(qapp, tmp_path, monkeypatch):
    """Regression: _populate_table() used to KeyError on a chain row —
    _KIND_LABEL still had the pre-rename key "rule" (list_importable/
    copy_items already emit "chain", 2026-09-01 Rule -> Chain rename)."""
    source = _write_sexp(tmp_path / "source.sexp", {
        "cells": {"c1": {"layer": "F.Cu", "components": [{"role": "R1"}]}},
        "chains": [{"net": "+3V3", "spokes": [{"pad": "1", "cell": "c1"}]}],
    })
    root = _write_sexp(tmp_path / "root.sexp", {"cells": {}, "chains": []})
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))
    _silence_boxes(monkeypatch)

    dlg = ProfileImportDialog(None, root)
    dlg._browse()  # must not raise KeyError

    assert dlg.table.rowCount() == 2  # c1, the chain
    labels = {dlg.table.item(r, 1).text() for r in range(dlg.table.rowCount())}
    assert labels == {"Cell", "Chain"}


def _leaf_source_and_root(tmp_path, root_offset):
    source = _write_sexp(tmp_path / "source.sexp", {"cells": {
        "leaf": {"layer": "F.Cu",
                 "components": [{"role": "R1", "offset_along_mm": 9.0}]}}})
    root = _write_sexp(tmp_path / "root.sexp", {"cells": {
        "leaf": {"layer": "F.Cu",
                 "components": [{"role": "R1", "offset_along_mm": root_offset}]}}})
    return source, root


def test_import_dialog_collision_ask_overwrite_replaces(qapp, tmp_path, monkeypatch):
    """A colliding name no longer fails — the dialog asks, and Overwrite makes
    the source version win."""
    source, root = _leaf_source_and_root(tmp_path, root_offset=1.0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))
    asked = []
    _silence_boxes(monkeypatch)

    dlg = ProfileImportDialog(None, root)
    monkeypatch.setattr(dlg, "_ask_collision",
                        lambda collisions: (asked.append(collisions), "overwrite")[1])
    dlg._browse()
    _check_row(dlg, 0)
    dlg._import()

    assert asked and "leaf" in asked[0]["cells"]
    assert _read(root)["cells"]["leaf"]["components"][0]["offset_along_mm"] == 9.0


def test_import_dialog_collision_keep_existing_skips(qapp, tmp_path, monkeypatch):
    source, root = _leaf_source_and_root(tmp_path, root_offset=1.0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))
    _silence_boxes(monkeypatch)

    dlg = ProfileImportDialog(None, root)
    monkeypatch.setattr(dlg, "_ask_collision", lambda collisions: "skip")
    dlg._browse()
    _check_row(dlg, 0)
    dlg._import()

    # the existing entry is kept untouched
    assert _read(root)["cells"]["leaf"]["components"][0]["offset_along_mm"] == 1.0


def test_import_dialog_collision_cancel_is_a_noop(qapp, tmp_path, monkeypatch):
    source, root = _leaf_source_and_root(tmp_path, root_offset=1.0)
    before = root.read_bytes()
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))
    _silence_boxes(monkeypatch)

    dlg = ProfileImportDialog(None, root)

    def _cancel(_collisions):
        raise profile_import_mod._Cancelled()

    monkeypatch.setattr(dlg, "_ask_collision", _cancel)
    dlg._browse()
    _check_row(dlg, 0)
    dlg._import()

    assert root.read_bytes() == before  # cancel -> nothing written, no error box


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

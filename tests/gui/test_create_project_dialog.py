# tests/gui/test_create_project_dialog.py
"""Cells for CreateProjectDialog — 2026-09-24, Denis: "В File будет «Создать
проект». Открываем диалог создания проекта. Там указываем имя, как в кикад,
автоматически создаётся директория с нужной инфраструктурой."

The widget is driven through its OWN methods (_on_ok / _on_browse /
_update_preview) instead of exec(), which would need a modal event loop: every
property asserted here is reachable without one, and a cell that can hang is a
cell nobody runs.

Three layers, three owners, so a failure names its own cause:
  * the Qt-free writing  -> tests/test_project_is_a_directory.py (create_project);
  * THIS FILE            -> the widget: validation, refusal, what it reports;
  * tests/gui/test_root_metadata.py -> the DOCK: it opens what the dialog reports.
"""
import logging

import pytest
from PyQt6.QtWidgets import QDialog, QDialogButtonBox

import gui.docks.create_project_dialog as create_dialog_mod
from gui.docks.create_project_dialog import CreateProjectDialog

# The infrastructure a new project is born with — the literal expectation, NOT
# imported, so this file keeps collecting if the module's list is ever renamed.
# Drift between the two is a cell of its own in tests/test_project_is_a_directory.py.
_INFRA = ("logs", "operational", "overrides", "registry", "tracks")


def _no_boxes(record):
    """Stand-in for QMessageBox.warning that RECORDS the message instead of
    opening a real modal — a modal in an offscreen run is a hang, not a failure."""
    return staticmethod(lambda *a, **k: record.append(a[2]))


def test_the_dialog_starts_in_the_given_folder_and_previews_the_path(qapp, tmp_path):
    """The visible promise of "the directory is created automatically": before the
    user commits, the dialog names the exact config it will create — and while the
    pair is not usable yet it says so instead of showing a half-made path."""
    dialog = CreateProjectDialog(None, start_dir=str(tmp_path))
    assert dialog.folder_edit.text() == str(tmp_path)

    dialog.name_edit.setText("HiPiMS-v099")
    assert str(tmp_path / "HiPiMS-v099" / "HiPiMS-v099.sexp") in dialog.preview_label.text()

    dialog.name_edit.setText("")
    assert "HiPiMS-v099" not in dialog.preview_label.text()


@pytest.mark.parametrize("name, folder_ok, expected", [
    ("Proj", True, True),
    ("", True, False),
    ("   ", True, False),
    ("Proj", False, False),
    ("sub/Proj", True, False),
    ("sub\\Proj", True, False),
], ids=["usable", "empty-name", "blank-name", "missing-folder", "slash", "backslash"])
def test_ok_is_enabled_only_for_a_usable_name_and_folder(
        qapp, tmp_path, name, folder_ok, expected):
    """The button's own state row by row (rule 35): a blank name and a name with a
    separator are DIFFERENT cells — the second one matters because a separator
    would let the project escape the folder the user picked, so it must be dead
    before the click, not merely refused after it."""
    folder = tmp_path if folder_ok else tmp_path / "nope"
    dialog = CreateProjectDialog(None, start_dir=str(folder))
    dialog.name_edit.setText(name)

    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled() is expected


def test_creating_a_project_writes_the_config_and_the_infrastructure(qapp, tmp_path):
    """The whole act, end to end through the widget: name + folder ->
    <folder>/<name>/<name>.sexp plus the five infrastructure directories (Denis,
    2026-09-24), and the dialog reports the config it created."""
    dialog = CreateProjectDialog(None, start_dir=str(tmp_path))
    dialog.name_edit.setText("HiPiMS-v099")

    dialog._on_ok()

    assert dialog.result() == QDialog.DialogCode.Accepted
    project = tmp_path / "HiPiMS-v099"
    assert dialog.created_config_path == project / "HiPiMS-v099.sexp"
    assert sorted(p.name for p in project.iterdir()) == sorted(
        ["HiPiMS-v099.sexp", *_INFRA])


def test_an_existing_project_is_refused_with_a_warning_and_survives(
        qapp, tmp_path, monkeypatch, caplog):
    """The refusal row (Denis, 2026-09-24: "с модалкой": a warning box plus ONE
    ERROR line in the Log). The dialog stays OPEN, the existing config keeps its
    bytes and nothing is added — an overwrite here would destroy a real project."""
    project = tmp_path / "taken"
    (project / "registry").mkdir(parents=True)
    existing = project / "taken.sexp"
    existing.write_text('(kicadstamp-config\n  (layer "B.Cu"))\n', encoding="utf-8")
    before = existing.read_text(encoding="utf-8")

    dialog = CreateProjectDialog(None, start_dir=str(tmp_path))
    dialog.name_edit.setText("taken")
    warned = []
    monkeypatch.setattr(create_dialog_mod.QMessageBox, "warning", _no_boxes(warned))
    caplog.clear()

    dialog._on_ok()

    assert dialog.result() != QDialog.DialogCode.Accepted, "the dialog must stay open"
    assert dialog.created_config_path is None
    assert existing.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in project.iterdir()) == ["registry", "taken.sexp"]
    assert len(warned) == 1 and str(existing) in warned[0]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert str(existing) in errors[0].message


def test_a_name_with_a_path_separator_is_refused_even_if_ok_is_called(
        qapp, tmp_path, monkeypatch):
    """The disabled button is UX; THIS is the guard. _on_ok is called directly, as
    a future refactor (a shortcut, a double-click, a programmatic accept) would,
    and it must still refuse — nothing may be created outside the picked folder."""
    dialog = CreateProjectDialog(None, start_dir=str(tmp_path))
    dialog.name_edit.setText("sub/Proj")
    warned = []
    monkeypatch.setattr(create_dialog_mod.QMessageBox, "warning", _no_boxes(warned))

    dialog._on_ok()

    assert len(warned) == 1
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.created_config_path is None
    assert list(tmp_path.iterdir()) == []


def test_a_missing_folder_is_refused_even_if_ok_is_called(qapp, tmp_path, monkeypatch):
    """The third refusal row: a folder that does not exist must not be created by
    a typo in the Folder field — the user either browses to one or fixes the text."""
    dialog = CreateProjectDialog(None, start_dir=str(tmp_path / "nope"))
    dialog.name_edit.setText("Proj")
    warned = []
    monkeypatch.setattr(create_dialog_mod.QMessageBox, "warning", _no_boxes(warned))

    dialog._on_ok()

    assert len(warned) == 1
    assert not (tmp_path / "nope").exists()
    assert list(tmp_path.iterdir()) == []


def test_browse_fills_the_folder_from_the_directory_dialog(qapp, tmp_path, monkeypatch):
    """Browse... is a plain directory picker (the same one the old New Root flow
    used) whose result is copied into the field — the dialog never creates the
    parent folder itself."""
    chosen = tmp_path / "somewhere"
    chosen.mkdir()
    dialog = CreateProjectDialog(None)
    monkeypatch.setattr(create_dialog_mod.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(chosen)))

    dialog._on_browse()

    assert dialog.folder_edit.text() == str(chosen)


def test_cancel_creates_nothing(qapp, tmp_path):
    """Cancel is a pure no-op: no directory, no config, no report."""
    dialog = CreateProjectDialog(None, start_dir=str(tmp_path))
    dialog.name_edit.setText("Nothing")

    dialog.reject()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.created_config_path is None
    assert list(tmp_path.iterdir()) == []

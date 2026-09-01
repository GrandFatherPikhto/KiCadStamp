# tests/gui/test_project_dialog.py
"""Tests for ProjectDialog (gui/docks/project_dialog.py, 2026-09-01, plan
project_settings_dialogs): the non-modal shell hosting the single live
RootMetadataDock, launched from File > "Project..."."""
from gui.docks.project_dialog import ProjectDialog
from gui.docks.root_metadata import RootMetadataDock


def test_dialog_hosts_the_live_root_metadata_dock(main_window):
    dock = RootMetadataDock(main_window)
    dialog = ProjectDialog(dock, main_window)
    assert dialog.root_metadata_dock is dock
    # The ONE widget in the dialog's layout is the dock itself (thin shell).
    assert dialog.layout().count() == 1
    assert dialog.layout().itemAt(0).widget() is dock


def test_dialog_object_name_and_title(main_window):
    dock = RootMetadataDock(main_window)
    dialog = ProjectDialog(dock, main_window)
    assert dialog.objectName() == "project_dialog"
    assert dialog.windowTitle() == "Project"

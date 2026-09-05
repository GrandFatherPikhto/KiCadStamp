# tests/gui/test_main_window.py
"""Tests for gui/main_window.py's settings persistence — window geometry
(plain ints) and the dock/splitter/tab/floating layout (2026-08-27: Qt's own
saveState(), base64 into the SAME gui_state.json, a documented exception to
the file's human-readability principle — see _persist_settings/_restore_
window_state docstrings). Uses the existing real_main_window fixture (tests/
gui/conftest.py) rather than building a MainWindow by hand, and
gui.settings' state is isolated per-test by the autouse isolated_settings
fixture (so this never touches the developer's real gui_state.json).
"""
import base64

from PyQt6.QtCore import QByteArray

from gui import settings
from gui.main_window import _DOCK_STATE_VERSION
from kicadstamp.config_working_set import WORKING_SET


def test_persist_settings_writes_dock_state(real_main_window):
    """_persist_settings() writes a non-empty base64 "dock_state" that
    decodes without error (a real saveState() blob, not an empty string)."""
    real_main_window._persist_settings()

    raw = settings.state.get("dock_state")
    assert raw
    blob = base64.b64decode(raw)
    assert blob  # a non-trivial Qt state payload, not just the header


def test_dock_state_round_trip_feeds_same_bytes_to_restore_state(
        real_main_window, monkeypatch):
    """restoreState() must receive EXACTLY the bytes _persist_settings()
    saved — a persist -> restore round-trip through the same state object.
    restoreState is stubbed (a real call would mutate live geometry); the
    assertion is on the argument it was handed."""
    real_main_window._persist_settings()
    raw = settings.state.get("dock_state")
    blob = base64.b64decode(raw)

    calls = []
    monkeypatch.setattr(real_main_window, "restoreState",
                        lambda qb, version: calls.append((qb, version)) or True)
    real_main_window._restore_window_state()

    assert calls, "restoreState was never called for a saved dock_state"
    qb, version = calls[0]
    assert isinstance(qb, QByteArray)
    assert bytes(qb) == blob
    assert version == _DOCK_STATE_VERSION


def test_missing_dock_state_never_attempts_restore(real_main_window, monkeypatch):
    """First run — no "dock_state" key at all: _restore_window_state() must
    not crash and must not attempt restoreState on empty data."""
    calls = []
    monkeypatch.setattr(real_main_window, "restoreState",
                        lambda *a, **k: calls.append(a) or True)

    real_main_window._restore_window_state()  # must not raise

    assert not calls


def test_corrupt_dock_state_logs_and_falls_through(real_main_window, caplog):
    """A hand-edited/undecodable "dock_state" must never crash startup — log
    the decode failure and fall through to the default layout."""
    settings.state.set("dock_state", "not-valid-base64!!!")

    real_main_window._restore_window_state()  # must not raise

    assert "Failed to decode saved dock_state" in caplog.text


def test_restore_state_false_returns_logs_and_falls_through(
        real_main_window, monkeypatch, caplog):
    """restoreState() returning False (a version/dock-set mismatch — the
    layout was saved by a future release, or the dock set changed) is just a
    log line, never an exception: the default layout stands."""
    settings.state.set("dock_state", "AAAA")  # decodes to 3 zero bytes
    monkeypatch.setattr(real_main_window, "restoreState",
                        lambda *a, **k: False)

    real_main_window._restore_window_state()  # must not raise

    assert "did not apply" in caplog.text


# ── View menu (2026-08-27, handoff sync_skip_message_and_view_menu) ───────

def _view_menu(real_main_window):
    """The single top-level "View" menu (the app had no menu bar before this
    feature)."""
    view_menus = [a for a in real_main_window.menuBar().actions()
                  if a.text() == "View"]
    assert len(view_menus) == 1
    return view_menus[0].menu()


def test_view_menu_has_one_checkable_action_per_dock(real_main_window):
    """One checkable toggleViewAction per real top-level dock — the only way
    to bring back a closed dock (the app previously had no menu bar at all).
    Count must match DockHub.docks (6 — DetailDock was removed 2026-09-05,
    plan config_qview_placer_nettrace)."""
    menu = _view_menu(real_main_window)
    actions = menu.actions()
    assert len(actions) == len(real_main_window._dock_hub.docks) == 6
    assert all(a.isCheckable() for a in actions)


def test_view_menu_toggle_shows_and_hides_a_dock(real_main_window):
    """toggleViewAction self-tracks shown/hidden: hiding a dock unchecks its
    action, triggering the action on a hidden dock brings it back. Uses
    isHidden() rather than isVisible() — the real main window is never shown()
    in offscreen tests, so isVisible() would be False for every dock."""
    log_dock = real_main_window._dock_hub.log_dock
    log_action = next(a for a in _view_menu(real_main_window).actions()
                      if a.text() == log_dock.windowTitle())

    log_dock.hide()
    assert log_action.isChecked() is False
    assert log_dock.isHidden() is True

    log_action.trigger()
    assert log_dock.isHidden() is False


def test_save_restore_state_preserves_a_hidden_dock(real_main_window):
    """REAL (unmocked) saveState()/restoreState() round-trip — the whole
    point of dock-layout persistence (a8a5d1b) actually working. With a
    stable objectName on every real dock (§0 of handoff
    sync_skip_message_and_view_menu), a dock hidden before saveState stays
    hidden after restoreState. Without the names Qt SILENTLY fails to
    identify the dock (restoreState still returns True but restores
    nothing), so this test guards the names, not just the call."""
    log_dock = real_main_window._dock_hub.log_dock
    # every real top-level dock carries a stable objectName
    assert all(d.objectName() for d in real_main_window._dock_hub.docks)

    log_dock.hide()
    state = real_main_window.saveState(_DOCK_STATE_VERSION)
    log_dock.show()
    assert log_dock.isHidden() is False

    ok = real_main_window.restoreState(state, _DOCK_STATE_VERSION)
    assert ok is True
    assert log_dock.isHidden() is True


# ── File menu (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1b) ─────

def _file_menu(real_main_window):
    """The single top-level "&File" menu (plan: menuBar by FUNCTION — File
    by function, not one menu per dock)."""
    file_menus = [a for a in real_main_window.menuBar().actions()
                  if a.text() == "&File"]
    assert len(file_menus) == 1
    return file_menus[0].menu()


def test_file_menu_has_project_close_quit(real_main_window):
    """&File (2026-09-01, plan project_settings_dialogs): Open/New/Recent
    moved INTO the Project dialog, so the menu now has just &Project..., Save,
    Discard, Close and &Quit."""
    menu = _file_menu(real_main_window)
    texts = [a.text() for a in menu.actions()]
    assert "&Project..." in texts
    assert "Close" in texts
    assert "&Quit" in texts
    assert "Open Root file..." not in texts
    assert "New Root file..." not in texts
    assert not any(a.text() == "Recent" and a.menu() is not None for a in menu.actions())


def test_file_menu_project_opens_the_project_dialog(real_main_window):
    """File > &Project... shows/raises the ONE live ProjectDialog — the non-
    modal shell around RootMetadataDock (gui/docks/project_dialog.py). The
    Ctrl+O/Ctrl+N hotkeys stay app-wide regardless of the dialog's visibility
    (the dock's own build_action actions are still registered on the window)."""
    menu = _file_menu(real_main_window)
    project_action = next(a for a in menu.actions() if a.text() == "&Project...")
    dialog = real_main_window._dock_hub.project_dialog
    dialog.hide()
    project_action.trigger()
    assert dialog.isVisible()
    assert dialog.root_metadata_dock is real_main_window.root_metadata_dock


def test_file_menu_close_calls_set_root_file_none(real_main_window, monkeypatch):
    """File > Close routes through RootMetadataDock.close_project, which drops
    the project root via set_root_file(None). Patching set_root_file records
    the call (a runtime lookup inside close_project — the Qt signal itself
    holds the pre-patch bound close_project, so that one is NOT patchable)."""
    calls = []
    monkeypatch.setattr(real_main_window.root_metadata_dock, "set_root_file",
                        lambda path: calls.append(path))
    close_action = next(a for a in _file_menu(real_main_window).actions()
                        if a.text() == "Close")
    close_action.trigger()
    assert calls == [None]


def test_file_menu_close_guard_respects_unsaved_changes(real_main_window, tmp_path, monkeypatch):
    """Close routes through RootMetadataDock's unsaved-changes guard — a
    refused guard keeps the project open; a confirmed one drops the root."""
    root = tmp_path / "root.sexp"
    root.write_text("(kicadstamp-config)\n", encoding="utf-8")
    real_main_window.root_metadata_dock.set_root_file(root)
    # The guard now covers the whole project's staged working set (2026-09-01):
    # make it dirty with a staged write, not a bare field edit (a field edit
    # only marks _dirty until its commit point stages it).
    WORKING_SET.stage_write(root, {"cells": {"c1": {}}})

    close_action = next(a for a in _file_menu(real_main_window).actions()
                        if a.text() == "Close")
    monkeypatch.setattr(real_main_window.root_metadata_dock,
                        "_confirm_discard_changes", lambda: False)
    close_action.trigger()
    assert real_main_window.root_metadata_dock._path == root

    monkeypatch.setattr(real_main_window.root_metadata_dock,
                        "_confirm_discard_changes", lambda: True)
    close_action.trigger()
    assert real_main_window.root_metadata_dock._path is None


def test_file_menu_quit_calls_quit(real_main_window, monkeypatch):
    calls = []
    monkeypatch.setattr(real_main_window, "_quit", lambda: calls.append(True))
    quit_action = next(a for a in _file_menu(real_main_window).actions()
                       if a.text() == "&Quit")
    quit_action.trigger()
    assert calls == [True]


def test_settings_hotkeys_list_contains_all_dock_actions(real_main_window):
    """After full MainWindow construction, the Settings tab's Hotkeys list
    includes every registered action — the dock actions AND the global File
    actions (project.save / project.discard). DockHub refreshes the list once
    all docks are built (gui/dock_hub.py), and MainWindow refreshes it AGAIN
    after registering its own File actions (2026-09-04, plan
    staged_delete_stale_tree_and_save_hotkey Bug B) so the global Save
    (Ctrl+S default) is present and rebindable. The retired
    root_metadata.save is gone (no misleading second "Save" row)."""
    edits = real_main_window._dock_hub.configurator_dock.hotkey_edits
    assert "root_metadata.open" in edits
    assert "root_metadata.new" in edits
    assert "root_metadata.add_schematic_file" in edits
    assert "root_metadata.remove_schematic_file" in edits
    assert "project.save" in edits
    assert "project.discard" in edits
    assert "root_metadata.save" not in edits

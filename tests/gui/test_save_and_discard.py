# tests/gui/test_save_and_discard.py
"""The two GLOBAL project doors and the open Trees form
(plan_2026_10_23_node_form_without_apply.md, Ч1).

Why these cells exist: an edit in the node/anchor form lives in WIDGETS until
it is accepted, so before this change Save answered "Nothing to save" over a
half-typed rename and Discard threw the draft away without a word. The two
doors answer differently ON PURPOSE:

* Save ACCEPTS first and REFUSES the whole write when the form does not
  validate;
* Discard does NOT accept (the user just asked to throw the unsaved changes
  away) and names the dropped draft in one info line;
* the way OUT of the program accepts BEFORE the unsaved-changes question —
  after it, the guard would have said "nothing to save" over an edit that then
  dirties the set.

The three doors' bodies live in gui/save_and_discard.py and gui/exit_guard.py;
the cells drive those bodies plus the ONE hook in MainWindow._save_project.
"""
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QLabel, QMessageBox

import gui.exit_guard as exit_guard_mod
import gui.main_window as main_window_mod
from gui import save_and_discard
from kicadstamp.trees import TreeNode


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """The stubs build QLabels (the status line and the form's own reason
    label), and a widget with NO QApplication aborts the whole process — the
    same rule the sibling trees cells spell out."""
    return qapp


class _Form:
    """A form with the TWO things the acceptance reads: `_touched` and
    apply() -> bool (plus the label apply() writes its reason into)."""

    def __init__(self, ok=True):
        self._ok = ok
        self._touched = True
        self._existing = TreeNode(ref="N1", kind="placement", xy=(1.0, 2.0),
                                  polar=None, rotation=0.0, name=None,
                                  group=None, children=[])
        self.apply_status_label = QLabel("X: 'oops' is not a number.")
        self.apply_calls = 0

    def apply(self):
        self.apply_calls += 1
        if self._ok:
            self._touched = False
        return self._ok


class _Tabs:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count

    def widget(self, _index):
        return object()


class _Dock:
    """The dock's own page-walk seam, nothing else: `accept_forms` reads the
    pages through these three names and reports through `_show_status`."""

    def __init__(self, form):
        self._form = form
        self.tree_tabs = _Tabs(1)
        self._discarding_working_set = False
        self.status = ""

    def _form_panel_of_page(self, _page):
        return "panel"

    def _panel_page(self, _panel):
        return "page"

    def _embedded_form_of(self, _page):
        return self._form

    def _show_status(self, text):
        self.status = text


def _window(dock=None, **extra):
    window = SimpleNamespace(status_label=QLabel(), **extra)
    if dock is not None:
        window._dock_hub = SimpleNamespace(trees_dock=dock)
    return window


# ── Save ──────────────────────────────────────────────────────────────────

def test_save_refuses_the_write_when_the_form_does_not_validate(monkeypatch):
    """The refusal is a REFUSAL, not a red line and a write anyway: the file
    must never receive a half-edited tree."""
    form = _Form(ok=False)
    window = _window(_Dock(form))
    flushed = []
    monkeypatch.setattr(save_and_discard, "WORKING_SET",
                        SimpleNamespace(is_dirty=lambda: True,
                                        flush=lambda root: flushed.append(root)))
    assert save_and_discard.settle_forms(window, refuse=True) is False
    assert form.apply_calls == 1
    assert "Save refused" in window.status_label.text()
    assert "N1" in window.status_label.text()
    assert flushed == []


def test_save_goes_on_when_the_form_is_accepted():
    form = _Form(ok=True)
    assert save_and_discard.settle_forms(_window(_Dock(form)), refuse=True) is True
    assert form.apply_calls == 1
    assert form._touched is False


def test_a_window_without_a_trees_dock_has_nothing_to_settle():
    assert save_and_discard.settle_forms(_window(), refuse=True) is True


def test_save_asks_the_form_before_it_asks_the_working_set(monkeypatch,
                                                          tmp_path):
    """The hook is the FIRST line of `_save_project` — the acceptor's ordering
    requirement, pinned by watching the two calls.

    The REAL method body runs against a minimal host: both `root_path` and the
    dock are read-only properties of a real window, and this door reads exactly
    three attributes, so a namespace is the honest rig (rather than patching a
    class property for every other window in the session)."""
    order = []
    monkeypatch.setattr(main_window_mod, "settle_forms",
                        lambda _w, refuse: order.append(("settle", refuse)) or True)
    monkeypatch.setattr(main_window_mod.WORKING_SET, "is_dirty",
                        lambda: order.append(("dirty", None)) or False)
    window = SimpleNamespace(
        status_label=QLabel(),
        root_metadata_dock=SimpleNamespace(root_path=tmp_path / "root.sexp"),
        _update_dirty_indicator=lambda: None)
    main_window_mod.MainWindow._save_project(window)
    assert order == [("settle", True), ("dirty", None)]
    assert window.status_label.text() == "Nothing to save"


# ── the way out of the program ────────────────────────────────────────────

def test_the_exit_settles_the_form_before_the_unsaved_question(
        real_main_window, monkeypatch):
    """Acceptance BEFORE `exit_guard`'s question. After it, the guard would
    answer "nothing to save" and the accepted edit would be lost — the exact
    silent loss this change removes."""
    order = []
    monkeypatch.setattr(exit_guard_mod, "settle_forms",
                        lambda _w, refuse: order.append(("settle", refuse)) or True)
    monkeypatch.setattr(real_main_window.root_metadata_dock,
                        "_confirm_discard_changes",
                        lambda: order.append(("question", None)) or True)
    assert exit_guard_mod.confirm_unsaved_before_exit(real_main_window) is True
    assert order == [("settle", False), ("question", None)]


def test_a_failed_exit_accept_only_reports_and_the_exit_goes_on(caplog):
    """A half-typed rename must never become a silent refusal to leave: the red
    line names the node, and the door is still open."""
    form = _Form(ok=False)
    window = _window(_Dock(form))
    with caplog.at_level(logging.ERROR):
        assert save_and_discard.settle_forms(window, refuse=False) is True
    assert any("N1" in r.message and "not applied" in r.message
               for r in caplog.records if r.levelno == logging.ERROR)


# ── Discard / Revert ──────────────────────────────────────────────────────

def test_discard_does_not_accept_and_names_the_dropped_edit(monkeypatch,
                                                           caplog):
    """The ONE door that does NOT accept: the user said "throw the unsaved
    changes away", so the draft goes with the set — but it is NAMED, once."""
    form = _Form(ok=True)
    dock = _Dock(form)
    seen = {}

    def _reload():
        # The rebuild the discard triggers must not accept the draft either.
        seen["discarding"] = dock._discarding_working_set
        seen["touched_when_cleared"] = form._touched

    working_set = SimpleNamespace(is_dirty=lambda: True,
                                  cleared=0)
    working_set.clear = lambda: setattr(working_set, "cleared", 1)
    monkeypatch.setattr(save_and_discard, "WORKING_SET", working_set)
    monkeypatch.setattr(save_and_discard.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard))
    window = SimpleNamespace(status_label=QLabel(),
                             _dock_hub=SimpleNamespace(trees_dock=dock,
                                                       reload_project_from_disk=_reload),
                             _update_dirty_indicator=lambda: None)
    with caplog.at_level(logging.INFO):
        save_and_discard.discard_unsaved(window)
    assert form.apply_calls == 0, "Discard must NOT accept the open form"
    assert seen == {"discarding": True, "touched_when_cleared": True}
    assert working_set.cleared == 1
    assert window.status_label.text() == "Unsaved config changes discarded"
    assert any("N1" in r.message and "discarded with the working set" in r.message
               for r in caplog.records if r.levelno == logging.INFO)


def test_discard_with_a_clean_set_does_nothing(monkeypatch):
    form = _Form(ok=True)
    dock = _Dock(form)
    reloaded = []
    monkeypatch.setattr(save_and_discard, "WORKING_SET",
                        SimpleNamespace(is_dirty=lambda: False))
    window = SimpleNamespace(status_label=QLabel(),
                             _dock_hub=SimpleNamespace(trees_dock=dock,
                                                       reload_project_from_disk=reloaded.append),
                             _update_dirty_indicator=lambda: None)
    save_and_discard.discard_unsaved(window)
    assert reloaded == []
    assert form.apply_calls == 0


# ── the modules stay apart ────────────────────────────────────────────────

def test_main_window_discard_is_a_seam_onto_the_module():
    """The body moved out of the giant (rule 45): the window keeps a one-line
    delegate with the SAME name, because `quit_action`/menus call that name."""
    source = Path(main_window_mod.__file__).read_text(encoding="utf-8")
    assert "discard_unsaved(self)" in source, "the seam onto the module is gone"

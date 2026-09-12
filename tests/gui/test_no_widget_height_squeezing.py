# tests/gui/test_no_widget_height_squeezing.py
"""Guard for the vertical "squeezing" class of bugs (2026-09-12).

Denis, live: "нельзя уменьшать высоту combo-box и текстовых полей, даже если
очень хочется. Тогда уж вертикальный скролл... лишь бы диалог не вылезал за
пределы необходимого", and then, looking at the Trees dock on screen:
"Сжимает. Смотреть аж страшно..." (по вертикали).

diagnostics/probe_min_height_squeeze.py measured the MECHANISM on a synthetic
QFormLayout; diagnostics/probe_trees_dock_form_squeeze.py measured the REAL
Trees dock (offscreen, the same `setMinimumHeight(1)` container trick
gui/dock_hub.py:166 applies to the central QTabWidget). BEFORE the fix:

    container h   first combo   fields below their own floor
            800          25 px                            1
            500          25 px                            5
            320          15 px                            8
            200           0 px                            8
            120           0 px                            8

Two INDEPENDENT invariants live here:

1. STATIC (test_no_height_restriction_on_input_fields) — an ast scan of every
   gui/**/*.py for `setFixedHeight`/`setMaximumHeight` calls whose receiver name
   looks like an input field. The name heuristic is deliberately crude: it
   catches the typical case, it does NOT prove the absence of one (a widget
   stored under a neutral name, `self.w1`, or a receiver built inline from an
   expression list slips through). It is a tripwire for the obvious mistake,
   not a proof.

2. BEHAVIORAL (TestFieldsDoNotSqueeze) — the invariant that actually matters,
   because it checks the real cause: a real dock panel, hosted exactly as the
   app hosts it, has its container squeezed to 120 px, and every visible field
   must keep the height it had when the container was roomy. Parameterized over
   two real panels (the Trees dock's node/anchor form panel and one Config
   right-hand page), never over a synthetic form — synthetic scaffolding is what
   the diagnostics probes are for.
"""
import ast
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QComboBox, QFrame, QLineEdit, QScrollArea,
                             QStackedWidget)

from kicadstamp.config.sexp_format import dict_to_sexp

_GUI_DIR = Path(__file__).resolve().parent.parent.parent / "gui"
_RULE = "docs/gui.md — never height-restrict an input field; scroll the content"

# Calls that pin a widget's height regardless of font/DPI/style.
_HEIGHT_CALLS = {"setFixedHeight", "setMaximumHeight"}
# Receivers whose NAME says "this widget scrolls its own content" — restricting
# their height is legal and IS done in the project (entity_page.py:100,
# fieldstool_window.py:651/:774, root_metadata.py:309). Checked BEFORE the field
# hints, so e.g. `self.text_field` counts as scrollable-widget territory.
_SCROLLABLE_HINTS = ("list", "table", "tree", "text", "log", "stack", "area",
                     "tabs")
# Receivers whose NAME says "this is an input field the user types into".
_FIELD_HINTS = ("combo", "edit", "spin", "field", "button", "check")


def _receiver_tokens(expr: ast.expr) -> set[str]:
    """Every Name/attribute token inside a receiver expression.

    `self.summary_list` -> {"self", "summary_list"}; a call chain such as
    `QLineEdit()` -> {"QLineEdit"}; `self.rows[0]` -> {"self", "rows"}."""
    tokens: set[str] = set()
    pending = [expr]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Attribute):
            tokens.add(node.attr)
            pending.append(node.value)
        elif isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Call):
            pending.append(node.func)
        elif isinstance(node, ast.Subscript):
            pending.append(node.value)
    return tokens


def _height_restricted_input_fields() -> list[str]:
    """[(relative path, line, call, receiver tokens)] for every height call on
    a receiver that looks like an input field."""
    found = []
    for path in sorted(_GUI_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            func = node.func if isinstance(node, ast.Call) else None
            if not isinstance(func, ast.Attribute) or func.attr not in _HEIGHT_CALLS:
                continue
            tokens = {token.lower() for token in _receiver_tokens(func.value)}
            if any(hint in token for token in tokens for hint in _SCROLLABLE_HINTS):
                continue
            if any(hint in token for token in tokens for hint in _FIELD_HINTS):
                found.append(f"{path.relative_to(_GUI_DIR.parent)}:{node.lineno} "
                             f"{func.attr}(...) on {sorted(tokens)}")
    return found


def test_no_height_restriction_on_input_fields():
    """A crude name-based tripwire, not a proof (see the module docstring).

    A field's height is not cosmetics: Qt derives it from the font, the DPI and
    the platform style. Pinned to a number it breaks every user whose settings
    differ from the author's, while looking perfect on the author's machine."""
    scanned = len(list(_GUI_DIR.rglob("*.py")))
    assert scanned > 30, "the gui/ package layout moved — scan found nothing"

    violations = _height_restricted_input_fields()
    assert violations == [], (
        f"height-restricted input fields ({_RULE}):\n  "
        + "\n  ".join(violations))


# ── The behavioural invariant, on the REAL panels ──────────────────────────

# The two real panels: the Trees dock's form panel (the measured victim — no
# QScrollArea anywhere in that dock) and one Config right-hand page (the
# reference implementation, wrapped by ConfigTreeDock._wrap_right_page).
_PANEL_CASES = ("trees_form_panel", "config_right_page")


def _build_panel(case: str, window, tmp_path: Path):
    """(widget to show, the page whose fields are measured) for one case."""
    hub = window._dock_hub
    if case == "trees_form_panel":
        root = tmp_path / "root.sexp"
        root.write_text(dict_to_sexp({
            "trees": [{
                "name": "power_tree", "anchor": {"ref": "CONN_PM5V"},
                "nodes": [{"ref": "AMS1117_REG", "kind": "clone",
                           "xy": [5.0, 2.0]}],
            }],
        }), encoding="utf-8")
        dock = hub.trees_dock
        dock.set_root_file(root)
        return dock, dock._active_form_page()

    dock = hub.config_tree_dock
    dock.set_current_page(hub._points_page)
    return dock, dock.right_page_at(hub._points_page)


def _visible_fields(page) -> list:
    """Every VISIBLE combo/line edit on `page`. A row the current mode hides is
    never laid out, so its height() is stale and says nothing about squeezing."""
    fields = (list(page.findChildren(QComboBox))
              + list(page.findChildren(QLineEdit)))
    return [field for field in fields if field.isVisible()]


def _field_snapshot(page) -> list[tuple[str, int, int]]:
    """[(class, minimumSizeHint height, actual height)] in a stable order."""
    return [(type(field).__name__, field.minimumSizeHint().height(),
             field.height()) for field in _visible_fields(page)]


class TestFieldsDoNotSqueeze:
    """Squeeze the CONTAINER (never the fields) and check they hold their size.

    The container is the real MainWindow's central QTabWidget — the widget
    gui/dock_hub.py:166 pins to a minimum height of 1 so the Log dock can be
    grown past the tabs' content. Clamping it to 120 px reproduces exactly what
    Denis saw live (and what probe_min_height_squeeze.py measured on synthetic
    forms)."""

    @pytest.mark.parametrize("case", _PANEL_CASES)
    def test_fields_keep_their_height_when_the_container_is_squeezed(
            self, case, real_main_window, tmp_path):
        window = real_main_window
        hub = window._dock_hub
        dock, page = _build_panel(case, window, tmp_path)
        assert page is not None, f"{case}: no form page to measure"
        hub.show_left_page(dock)
        window.resize(1500, 900)
        window.show()
        QTest.qWaitForWindowExposed(window)
        QTest.qWait(80)

        natural = _field_snapshot(page)
        assert natural, f"{case}: the panel shows no visible fields to measure"

        central = window.centralWidget()
        central.setMaximumHeight(120)
        try:
            QTest.qWait(150)
            assert central.height() <= 150, f"{case}: the centre was not squeezed"
            assert _field_snapshot(page) == natural, (
                f"{case}: squeezing the container resized its fields — "
                f"{_RULE}")
        finally:
            central.setMaximumHeight(16777215)
            QTest.qWait(60)

    @pytest.mark.parametrize("case", _PANEL_CASES)
    def test_combos_never_go_below_their_own_minimum(
            self, case, real_main_window, tmp_path):
        """The literal rule from the task, checked as its own invariant."""
        window = real_main_window
        dock, page = _build_panel(case, window, tmp_path)
        window._dock_hub.show_left_page(dock)
        window.resize(1500, 900)
        window.show()
        QTest.qWaitForWindowExposed(window)
        QTest.qWait(80)

        central = window.centralWidget()
        central.setMaximumHeight(120)
        try:
            QTest.qWait(150)
            squeezed = [(type(field).__name__, field.height(),
                         field.minimumSizeHint().height())
                        for field in _visible_fields(page)]
            below = [entry for entry in squeezed
                     if entry[1] < entry[2] and entry[0] == "QComboBox"]
            assert below == [], (
                f"{case}: combo boxes below their own minimumSizeHint "
                f"(class, actual, floor): {below}")
        finally:
            central.setMaximumHeight(16777215)
            QTest.qWait(60)


class TestTreesFormPanelIsScrollWrapped:
    """Э1 acceptance (gui/docks/trees_dock.py): the panels that hold a form are
    wrapped exactly like ConfigTreeDock's right pages — minimum height 1 on the
    SCROLL AREA (never on the panel), NoFrame, as-needed bars, and the panel
    stays reachable through the dock's own accessors."""

    def test_panel_is_wrapped_with_the_project_settings(
            self, real_main_window, tmp_path):
        dock, _page = _build_panel("trees_form_panel", real_main_window, tmp_path)
        page_widget = dock.tree_tabs.currentWidget()
        panel = dock._active_form_panel()
        wrapper = page_widget.widget(1)

        assert isinstance(panel, QStackedWidget)
        assert isinstance(wrapper, QScrollArea)
        assert wrapper.widget() is panel
        assert wrapper.widgetResizable() is True
        assert wrapper.minimumHeight() == 1
        assert wrapper.frameShape() == QFrame.Shape.NoFrame
        assert wrapper.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert wrapper.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded

        # The page -> panel unwrap must still hand back the panel itself, or
        # every selection-driven rebuild silently stops finding it.
        assert dock._form_panel_of_page(page_widget) is panel

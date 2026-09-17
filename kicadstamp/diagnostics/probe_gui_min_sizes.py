#!/usr/bin/env python3
"""How wide does each GUI dock/dialog DEMAND to be? (2026-09-17)

Task: techdocs/handoff/deepseek/plan/plan_2026_09_17_cell_dialog_min_width.md (Р3).

Why this probe exists: the Cell dialog would not shrink below 1310 px on Windows,
because Qt never lays a widget out below `minimumSizeHint()` — neither `resize()`
nor `resize_dialog_within_screen` can help, so on a 1366x768 laptop the dialog
hangs over the screen edge and part of the buttons is unreachable. The plan's Р4
sets the acceptable floor at 1000 px. This probe answers the question the plan
could not answer from one measurement: **is the Cell dialog the ONLY offender?**

It builds the docks and dialogs the app builds (offscreen, no KiCad, no
MainWindow — the same technique diagnostics/probe_trees_dock_form_squeeze.py, the
vertical counterpart of this task, uses), prints `minimumSizeHint()` of each, and
for every widget over the limit names its widest DESCENDANTS — the plan's §1
identified the guilty three-button row of CellDock exactly that way.

Both catalogues, because Russian labels are longer and Denis works in Russian
(Р7): `--lang ru`. ORDER MATTERS TWICE, and both orders are honoured below:
`LANGUAGE` must be in the environment BEFORE `gui.*` is imported (those modules do
`from kicadstamp.i18n import _` at import time), and `setup_i18n()` must run
before that same import. `--json` emits the same report machine-readably, which is
what the guard in tests/gui/test_dialog_min_width.py reads for the ru half.

READ-ONLY, and it touches nothing of the user's:

* no KiCad, no board, no socket — the widgets get a stub connection whose `board`
  is None (techdocs/me/door.md: a diagnostics script that needs no board must not
  reach for one);
* `gui.settings.SETTINGS_PATH` (and the fieldstool one) is re-pointed at a
  throwaway file before any widget is built, so no real gui_state.json is read or
  written;
* nothing is ever SHOWN and nothing is ever closed: showing followed by closing a
  dialog would make `_DialogSizeSaver` write a remembered size into gui_state.json
  (gui/ui_utils.py). Measuring `minimumSizeHint()` needs no show.

Usage:
    python -m kicadstamp.diagnostics.probe_gui_min_sizes
    python -m kicadstamp.diagnostics.probe_gui_min_sizes --lang ru
    python -m kicadstamp.diagnostics.probe_gui_min_sizes --json report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# offscreen BEFORE PyQt6 is imported anywhere (a real display would be required
# otherwise); the language is put in place in _select_language(), still before
# gui.* is imported — see the module docstring.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Р4 of the plan: 1000 px, because 1366x768 is the smallest laptop screen the
# project must live on and 1000 leaves room for the window frame and the taskbar.
LIMIT_PX = 1000

# The three buttons the whole task is about (gui/docks/cell_editor.py:501-527).
BUTTON_ATTRS = ("refresh_geometry_button", "import_vias_tracks_button",
                "select_cluster_button")

# How many of the widest descendants to name per offender. Six is enough to show
# a three-button row plus its neighbours.
TOP_GUILTY = 6
# How many over-wide layout ROWS to report per offender (see guilty_rows).
TOP_GUILTY_ROWS = 6


class _StubConnection:
    """The connection attributes the docks read — with NO board behind them.

    Mirrors tests/gui/conftest.py:_FakeConnection: construction paths only ever
    ask whether a board is there, and the answer here is always "no". Anything
    that would go to KiCad is therefore impossible by construction, not by
    convention."""

    board = None
    long_op_active = False
    timeout_ms = 1000

    @property
    def is_connected(self) -> bool:
        return self.board is not None


def _select_language(lang: str) -> str:
    """Put the language into the environment, THEN install gettext.

    Two orders matter and neither is obvious: `LANGUAGE` has to be set before
    `gui.*` is imported (every GUI module binds `_` at import time:
    `from kicadstamp.i18n import _`), and `setup_i18n()` has to run before that
    same import — afterwards is too late, the modules would keep the untranslated
    fallback and every "ru" number would silently be an English one."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        os.environ.pop(var, None)
    os.environ["LANGUAGE"] = "ru" if lang == "ru" else "en"

    from kicadstamp.i18n import setup_i18n
    return setup_i18n()


def _isolate_settings() -> Path:
    """Re-point the GUI settings at a throwaway file (see the module docstring).

    `Settings.path` resolves the module global at CALL time, which is what makes
    this patch effective for every widget built afterwards — the same seam
    tests/gui/conftest.py uses."""
    from gui import settings as gui_settings
    from gui import fieldstool_window

    tmp = Path(tempfile.mkdtemp(prefix="probe_gui_min_sizes_")) / "gui_state.json"
    gui_settings.SETTINGS_PATH = tmp
    fieldstool_window.FIELDSTOOL_SETTINGS_PATH = tmp.with_name("fieldstool_gui_state.json")
    return tmp


def _needs_config(name: str):
    """A builder for a dialog this probe deliberately does NOT measure.

    Those dialogs take a loaded profile (a `cfg` object). Loading one would drag
    a profile into a pure layout measurement, so the gap is REPORTED instead of
    hidden: the report's `not_built` list is the honest scope statement."""
    def refuse():
        raise RuntimeError(f"{name} takes a loaded profile/cfg — not built by this probe")
    return refuse


def _builders(window, built: dict) -> list[tuple[str, object]]:
    """[(name, builder)] — what to measure, in the order to build it.

    Lazy imports: every gui.* module has to be imported AFTER _select_language()
    (see its docstring), so these cannot sit at module level."""
    from gui.docks.cell_anchor_view import CellAnchorView
    from gui.docks.cell_dialog import CellDialog
    from gui.docks.cell_editor import CellDock
    from gui.docks.cell_layers import CellLayersDialog
    from gui.docks.chain import BulkSetCellDialog, ChainDock
    from gui.docks.chains_nav import ChainsNavDock
    from gui.docks.config_tree import ConfigTreeDock
    from gui.docks.configurator import ConfiguratorDock
    from gui.docks.entity_page import EntityInfoDock
    from gui.docks.extract_cluster_dialog import ExtractClusterDialog
    from gui.docks.fieldstool_dock import FieldsToolDock
    from gui.docks.instances_dialog import TreeInstancesDialog
    from gui.docks.log_panel import LogDock
    from gui.docks.net_trace import NetTraceDock
    from gui.docks.pending import PendingChangesDock
    from gui.docks.placer import PlacerDock
    from gui.docks.points import PointsDock
    from gui.docks.profile_import import ProfileImportDialog
    from gui.docks.project_dialog import ProjectDialog
    from gui.docks.role_cluster_tree import RoleClusterTreeDock
    from gui.docks.root_metadata import RootMetadataDock
    from gui.docks.scheme_list import BoundaryNetDialog
    from gui.docks.settings_dialog import SettingsDialog
    from gui.docks.thermal_via import ThermalViaArrayDock
    from gui.docks.tools import ToolsDock
    from gui.docks.tools_dialog import ToolsDialog
    from gui.docks.tree_from_selection_dialog import TreeFromSelectionDialog
    from gui.docks.trees_dock import TreesDock

    connection = window.connection
    throwaway_root = Path(tempfile.mkdtemp(prefix="probe_gui_min_sizes_cfg_")) / "root.sexp"

    def cell_dock():
        return CellDock(window)

    def cell_dialog():
        # The dialog is a thin shell around a live CellDock (gui/docks/cell_dialog.py),
        # so the dock that holds the three buttons is kept for the report — the
        # buttons themselves are the evidence for Р1/Р7.
        dock = CellDock(window)
        built["cell_dock_in_dialog"] = dock
        return CellDialog(dock, window)

    def fieldstool_window():
        # FieldsToolDock is a QObject CONTROLLER (gui/docks/fieldstool_dock.py:51),
        # not a widget — `.window` is the embedded FieldsToolMainWindow. The
        # controller is kept alive in `built`: it wires the window's callbacks and
        # would otherwise be collected while its window lives on.
        controller = FieldsToolDock(window, connection=connection,
                                    pending_dock=PendingChangesDock(window))
        built["fieldstool_dock"] = controller
        return controller.window

    return [
        # The target of this task, first — the report is sorted by width anyway.
        ("CellDialog", cell_dialog),
        ("CellDock", cell_dock),
        # Docks the app builds (MainWindow is not constructed on purpose: it emits
        # its own board-polling worker, and this probe has no business polling KiCad).
        ("RoleClusterTreeDock", lambda: RoleClusterTreeDock(window, connection=connection)),
        ("ConfigTreeDock", lambda: ConfigTreeDock(window)),
        ("TreesDock", lambda: TreesDock(window)),
        ("ChainDock", lambda: ChainDock(window)),
        ("ChainsNavDock", lambda: ChainsNavDock(window)),
        ("PlacerDock", lambda: PlacerDock(window)),
        ("ThermalViaArrayDock", lambda: ThermalViaArrayDock(window)),
        ("EntityInfoDock", lambda: EntityInfoDock(window)),
        ("RootMetadataDock", lambda: RootMetadataDock(window)),
        ("ToolsDock", lambda: ToolsDock(window)),
        ("PendingChangesDock", lambda: PendingChangesDock(window)),
        ("PointsDock", lambda: PointsDock(window, connection=connection)),
        ("NetTraceDock", lambda: NetTraceDock(window, connection=connection)),
        ("ConfiguratorDock", lambda: ConfiguratorDock(window, connection=connection)),
        # The spoke/cell editor page — a Config right-hand page, NOT part of the
        # Cell dialog (gui/dock_hub.py:281); measured so the report can say
        # whether it is a second offender.
        ("CellAnchorView", lambda: CellAnchorView(window, connection=connection)),
        ("FieldsToolDock.window", fieldstool_window),
        ("LogDock", lambda: LogDock(window, verbose=False)),
        # Dialog shells built from live docks the app already owns.
        ("SettingsDialog", lambda: SettingsDialog(
            ConfiguratorDock(window, connection=connection), window)),
        ("ToolsDialog", lambda: ToolsDialog(ToolsDock(window), window)),
        ("ProjectDialog", lambda: ProjectDialog(RootMetadataDock(window), window)),
        # Small dialogs that need no profile at all.
        ("CellLayersDialog", lambda: CellLayersDialog([], parent=window)),
        ("BoundaryNetDialog", lambda: BoundaryNetDialog([], parent=window)),
        ("TreeFromSelectionDialog", lambda: TreeFromSelectionDialog(
            [], [], [], parent=window)),
        ("ExtractClusterDialog", lambda: ExtractClusterDialog(window, [], None, ())),
        ("BulkSetCellDialog", lambda: BulkSetCellDialog(throwaway_root, parent=window)),
        ("ProfileImportDialog", lambda: ProfileImportDialog(window, throwaway_root)),
        # Deliberately NOT measured, reported instead of hidden.
        ("InstantiateCellDialog", _needs_config("InstantiateCellDialog")),
        ("TreeInstancesDialog", _needs_config("TreeInstancesDialog")),
    ]


def _widget_text(widget) -> str | None:
    """The user-visible text of a widget, when it has one (button/label/group)."""
    for getter in ("text", "title"):
        accessor = getattr(widget, getter, None)
        if not callable(accessor):
            continue
        try:
            value = accessor()
        except Exception:  # noqa: BLE001 — a probe must not die on one odd widget
            continue
        if isinstance(value, str) and value:
            return value
    return None


def _widget_path(widget, root) -> str:
    """'row / QPushButton' style location of a descendant, for the report."""
    names: list[str] = []
    node = widget
    while node is not None and node is not root:
        names.append(node.objectName() or type(node).__name__)
        node = node.parentWidget()
    return " / ".join(reversed(names)) or type(widget).__name__


def guilty_descendants(widget, root, limit: int) -> list[dict]:
    """The widest minimumSizeHint() among a widget's DESCENDANTS, widest first.

    This is how the plan's §1 found the three-button row: a container's minimum is
    the sum of its children's, so the children at the top of this list ARE the
    reason it cannot shrink."""
    from PyQt6.QtWidgets import QWidget

    found: list[tuple[int, object]] = []
    for child in widget.findChildren(QWidget):
        width = child.minimumSizeHint().width()
        if width <= 0:
            continue
        found.append((width, child))
    found.sort(key=lambda pair: -pair[0])
    rows = []
    for width, child in found[:TOP_GUILTY]:
        rows.append({
            "class": type(child).__name__,
            "object_name": child.objectName() or None,
            "text": _widget_text(child),
            "min_width": width,
            "where": _widget_path(child, root),
            "over_limit": width > limit,
        })
    return rows


def guilty_rows(widget, root, limit: int) -> list[dict]:
    """Layout ROWS whose items' minimum widths ADD UP past the limit.

    A single descendant does not always explain an offender, and the Cell dialog
    is exactly that case: its 1310 px comes from three buttons SIDE BY SIDE
    (386 + 410 + 494 + spacing), while each button ALONE is narrower than the
    502 px tab widget sitting next to them (plan §1). Only the sum along a row
    shows it — which is the arithmetic §1 did by hand, reproduced here for any
    offender. Grid rows are measured the same way (by real row/column positions,
    not by a flat index: a grid's slots may be sparse)."""
    from PyQt6.QtWidgets import QGridLayout, QHBoxLayout

    def row_of(layout, indexes: list[int]) -> tuple[int, list[dict], object]:
        total = 0
        items = []
        first_holder = None
        for index in indexes:
            item = layout.itemAt(index)
            if item is None:
                continue
            holder = item.widget()
            width = item.minimumSize().width()
            if holder is None and width <= 0:
                continue  # a bare stretch/spacer contributes no minimum
            total += width
            if holder is not None and first_holder is None:
                first_holder = holder
            items.append({
                "class": type(holder).__name__ if holder is not None else "spacer",
                "text": _widget_text(holder) if holder is not None else None,
                "min_width": width,
            })
        return total, items, first_holder

    rows: list[dict] = []
    for layout in widget.findChildren(QHBoxLayout):
        total, items, first_holder = row_of(layout, list(range(layout.count())))
        if total > limit:
            rows.append({
                "layout": "QHBoxLayout",
                "sum_min_width": total,
                "where": (_widget_path(first_holder, root) if first_holder is not None
                          else "?"),
                "items": items,
            })
    for layout in widget.findChildren(QGridLayout):
        by_row: dict[int, list[int]] = {}
        for index in range(layout.count()):
            row_number = layout.getItemPosition(index)[0]
            by_row.setdefault(row_number, []).append(index)
        for row_number in sorted(by_row):
            total, items, first_holder = row_of(layout, by_row[row_number])
            if total > limit:
                rows.append({
                    "layout": f"QGridLayout row {row_number}",
                    "sum_min_width": total,
                    "where": (_widget_path(first_holder, root)
                              if first_holder is not None else "?"),
                    "items": items,
                })
    rows.sort(key=lambda row: -row["sum_min_width"])
    return rows[:TOP_GUILTY_ROWS]


def cell_dialog_buttons(built: dict) -> list[dict]:
    """The three buttons themselves: caption, tooltip, minimum width."""
    dock = built.get("cell_dock_in_dialog")
    rows = []
    if dock is None:
        return rows
    for attr in BUTTON_ATTRS:
        button = getattr(dock, attr, None)
        if button is None:
            rows.append({"attr": attr, "missing": True})
            continue
        rows.append({
            "attr": attr,
            "text": button.text(),
            "tooltip": button.toolTip(),
            "min_width": button.minimumSizeHint().width(),
        })
    return rows


def collect(limit: int, lang: str) -> dict:
    """Measure every builder and assemble the report (see the module docstring)."""
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    settings_path = _isolate_settings()
    window = QMainWindow()
    window.connection = _StubConnection()

    built: dict = {}
    widgets: list[dict] = []
    failures: list[dict] = []
    for name, builder in _builders(window, built):
        try:
            widget = builder()
        except Exception as exc:  # noqa: BLE001 — reported, never fatal
            failures.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not isinstance(widget, QWidget):
            # A controller that owns a widget must hand the WIDGET over (see
            # fieldstool_window() above); saying so beats an AttributeError.
            failures.append({"name": name,
                             "error": f"{type(widget).__name__} is not a QWidget — "
                                      f"the builder must return the widget to measure"})
            continue
        # Qt-parent rule (plan 2а §1.1 / design §5а): a widget with no parent is
        # collected by the garbage collector, which on Windows takes the whole
        # process down. Parented to `window`, it survives the measurement.
        if widget.parent() is None:
            widget.setParent(window)
        hint = widget.minimumSizeHint()
        widgets.append({
            "name": name,
            "class": type(widget).__name__,
            "min_width": hint.width(),
            "min_height": hint.height(),
            "over_limit": hint.width() > limit,
            "guilty": (guilty_descendants(widget, window, limit)
                       if hint.width() > limit else []),
            "guilty_rows": (guilty_rows(widget, window, limit)
                            if hint.width() > limit else []),
        })
    app.processEvents()

    over = [row for row in widgets if row["over_limit"]]
    widgets.sort(key=lambda row: -row["min_width"])
    return {
        "probe": "probe_gui_min_sizes",
        "date": "2026-09-17",
        "task": "techdocs/handoff/deepseek/plan/plan_2026_09_17_cell_dialog_min_width.md",
        "language": lang,
        "language_env": os.environ.get("LANGUAGE"),
        "qt": QT_VERSION_STR,
        "pyqt": PYQT_VERSION_STR,
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "limit": limit,
        "settings_file": str(settings_path),
        "measured": len(widgets),
        "over_limit": [row["name"] for row in over],
        "widgets": widgets,
        "cell_dialog": next((row for row in widgets if row["name"] == "CellDialog"), None),
        "cell_dialog_buttons": cell_dialog_buttons(built),
        "not_built": failures,
    }


def print_report(report: dict) -> None:
    """The human table — same numbers as the JSON, in reading order.

    ASCII punctuation in the table on purpose: this runs in cmd, whose codepage
    has no em dash, and the report is meant to be pasted into techdocs as is."""
    print(f"probe_gui_min_sizes - {report['date']} - {Path(report['task']).name}")
    print(f"Qt {report['qt']} / PyQt {report['pyqt']}, platform "
          f"{report['platform']}, language {report['language']} "
          f"(LANGUAGE={report['language_env']})")
    print(f"limit {report['limit']} px - measured {report['measured']} widget(s), "
          f"over the limit: {len(report['over_limit'])}"
          + (f" ({', '.join(report['over_limit'])})" if report['over_limit'] else ""))

    print("\n  min width  min height  widget")
    for row in report["widgets"]:
        mark = f"OVER by {row['min_width'] - report['limit']}" if row["over_limit"] else ""
        print(f"  {row['min_width']:9d}  {row['min_height']:10d}  "
              f"{row['name']:<26s} {mark}")

    for row in report["widgets"]:
        if not row["over_limit"]:
            continue
        print(f"\nover the limit: {row['name']} "
              f"({row['min_width']} px, guilty rows first: the SUM along a row)")
        for guilty_row in row["guilty_rows"]:
            print(f"  {guilty_row['sum_min_width']:5d} px  {guilty_row['layout']}"
                  f"   at {guilty_row['where']}")
            for item in guilty_row["items"]:
                text = f" {item['text']!r}" if item["text"] else ""
                print(f"           {item['min_width']:5d} px  {item['class']}{text}")
        if not row["guilty_rows"]:
            print("  (no single row adds up past the limit)")
        print("  widest individual descendants:")
        for guilty in row["guilty"]:
            text = f" {guilty['text']!r}" if guilty["text"] else ""
            print(f"  {guilty['min_width']:5d} px  {guilty['class']}{text}"
                  f"   at {guilty['where']}")

    cell = report["cell_dialog"]
    print("\nthe target of this task:")
    if cell is None:
        print("  CellDialog was NOT built — see not_built below")
    else:
        verdict = ("OVER the limit" if cell["over_limit"]
                   else f"OK, {report['limit'] - cell['min_width']} px of slack")
        print(f"  CellDialog  {cell['min_width']} x {cell['min_height']}  {verdict}")
    for button in report["cell_dialog_buttons"]:
        if button.get("missing"):
            print(f"  {button['attr']}: MISSING from CellDock")
            continue
        print(f"  {button['attr']:<26s} {button['min_width']:4d} px  "
              f"text {button['text']!r}")
        print(f"  {'':<26s}          tooltip {button['tooltip']!r}")

    if report["not_built"]:
        print("\nnot built by this probe (scope, not a failure):")
        for failure in report["not_built"]:
            print(f"  {failure['name']}: {failure['error']}")


def _safe_stdout() -> None:
    """Never die on a console that cannot encode a translated caption.

    The Russian labels this task measures are Cyrillic, and cmd's codepage is not
    UTF-8: without this a run would raise UnicodeEncodeError instead of reporting
    the very number it was started for."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:  # noqa: BLE001 — cosmetic only
                pass


def main(argv=None) -> int:
    _safe_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=("en", "ru"), default="en",
                        help="which catalogue to measure (default: en)")
    parser.add_argument("--limit", type=int, default=LIMIT_PX,
                        help=f"the width floor in px (default: {LIMIT_PX})")
    parser.add_argument("--json", default=None, metavar="PATH",
                        help="also write the report as JSON to PATH "
                             "(the guard test reads this for the ru half)")
    args = parser.parse_args(argv)

    lang = _select_language(args.lang)
    report = collect(args.limit, lang)
    print_report(report)

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nJSON report -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

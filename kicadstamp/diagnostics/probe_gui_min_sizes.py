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

Since 08.10.2026 the offender block is the guard's own failure message, not just
a table (plan_2026_10_08_cell_dialog_width_windows, Ш1-2..Ш1-6): the Windows
runner is the only place the Cell dialog reproduces, so the report has to name
the holder in ONE push, without a second round trip. Three properties of that
block were MEASURED before they were promised, and each of them changes the
answer:

* the row sum carries the row's own margins and SPACING, and nothing guessed:
  the CellDock's "Add / Update selected / Remove selected" row is 295 px of items
  + 12 px of gaps = 307 px against a 337 px dialog (the other 30 px are the
  margins of the layouts BETWEEN the row and the offender — measured once, and
  deliberately NOT walked: the chain double-counts a QStackedLayout, see
  guilty_rows). Read as items alone the row stays under a limit the offender is
  over, i.e. invisible;
* the descendants list is FILTERED by `isVisibleTo(offender)`, never by
  `isVisible()`: nothing here is ever shown, so `isVisible()` is False for every
  descendant and the list would be empty (measured 08.10 on the CellDialog: 0
  visible); `isVisibleTo` keeps 45 — and the count is reported, because the rows
  deliberately keep what it drops (a QTabWidget's minimum is computed over ALL
  its pages);
* the share "≥ 60 % of the offender" is a FLAG, not a filter: on the base that
  motivated this probe (ecc29e5) the holders were 386/410/494 of 1310 px —
  29/31/38 % — so the filter would print nothing exactly where it is needed.

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

# How many of the widest VISIBLE descendants to name per offender. Fifteen: a
# whole three-button row plus its neighbours, and still readable in a pytest
# failure message.
TOP_GUILTY = 15
# How many over-wide layout ROWS to report per offender (see guilty_rows).
TOP_GUILTY_ROWS = 10
# The share of the offender's own width above which a descendant is FLAGGED as
# holding it. A flag and never a filter — see widest_descendants for the
# measurement that forbids the filter.
HOLDS_SHARE = 0.60
# Text longer than this is clipped in the report; a failure message is not a dump.
TEXT_LIMIT = 60
# The fixed string of platform_context(): the same text through QFontMetrics on
# every machine, so two runs stay comparable when the fonts differ.
SAMPLE_TEXT = "Select cell components"


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
    from gui.docks.cell_dialog import CellDialog
    from gui.docks.cell_editor import CellDock
    from gui.docks.cell_layers import CellLayersDialog
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
    from gui.docks.imprint import BoundaryNetDialog
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
        # The dialog is a thin shell around a live CellDock
        # (gui/docks/cell_dialog.py) — the offender's own guilt is read off the
        # dialog itself, so nothing has to be kept in `built` for it.
        return CellDialog(CellDock(window), window)

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


def _effective_min_width(widget) -> int:
    """What the LAYOUT obeys: the larger of the hint and an explicit minimum.

    `QWidget.minimumSizeHint()` does NOT include an explicit `setMinimumWidth`;
    a layout's minimum does. Reading the hint alone would let a widget pinned by
    an explicit minimum hold the whole width and stay unnamed. Measured 08.10 on
    the CellDialog: zero such descendants on Linux — which is exactly why the
    number has to travel to the Windows runner, where the offender lives."""
    return max(widget.minimumSizeHint().width(), widget.minimumWidth())


def _clip_text(text: str | None) -> str | None:
    """One line, at most TEXT_LIMIT characters (a failure message, not a dump)."""
    if text is None:
        return None
    one_line = " ".join(text.split())
    if len(one_line) <= TEXT_LIMIT:
        return one_line
    return one_line[:TEXT_LIMIT - 3] + "..."


def _widget_text(widget) -> str | None:
    """The user-visible text of a widget, when it has one.

    Buttons and labels answer `text()`, a window answers `title()` — but the
    widget most likely to hold a width, a `QComboBox` sized to its contents, has
    neither: its evidence is `currentText()` or, with nothing selected, its first
    items (Ш1-5). A long table header is the same kind of evidence, so it is read
    too. Without this the report would name the culprit's class and leave the
    reader to guess which of the dozens of combos it is."""
    from PyQt6.QtCore import Qt

    def call(obj, name):
        accessor = getattr(obj, name, None) if obj is not None else None
        if not callable(accessor):
            return None
        try:
            return accessor()
        except Exception:  # noqa: BLE001 — a probe must not die on one odd widget
            return None

    for name in ("text", "title", "currentText"):
        value = call(widget, name)
        if isinstance(value, str) and value:
            return value

    count = call(widget, "count")
    item_text = getattr(widget, "itemText", None)
    if isinstance(count, int) and callable(item_text):
        items = []
        for index in range(min(count, 3)):
            try:
                items.append(item_text(index))
            except Exception:  # noqa: BLE001 — same reason as above
                continue
        items = [item for item in items if isinstance(item, str) and item]
        if items:
            return " | ".join(items)

    model = call(widget, "model")
    if model is not None:
        columns = getattr(model, "columnCount", None)
        header = getattr(model, "headerData", None)
        if callable(columns) and callable(header):
            names = []
            try:
                for index in range(min(columns(), 4)):
                    names.append(header(index, Qt.Orientation.Horizontal,
                                        Qt.ItemDataRole.DisplayRole))
            except Exception:  # noqa: BLE001 — same reason as above
                names = []
            names = [name for name in names if isinstance(name, str) and name]
            if names:
                return " | ".join(names)
    return None


def _widget_path(widget, root) -> str:
    """'row / QPushButton' style location of a descendant, for the report."""
    names: list[str] = []
    node = widget
    while node is not None and node is not root:
        names.append(node.objectName() or type(node).__name__)
        node = node.parentWidget()
    return " / ".join(reversed(names)) or type(widget).__name__


def widest_descendants(widget, root, limit: int) -> list[dict]:
    """The widest VISIBLE descendants of an offender, widest first (Ш1-2).

    This is how the plan's §1 found the three-button row: a container's minimum
    is the sum of its children's, so the children at the top of this list ARE the
    reason it cannot shrink.

    Each row carries BOTH width numbers, because a culprit can hide behind
    either: `min_hint_width` is what the widget asks for, `minimum_width_prop` is
    what was explicitly SET on it, and the layout obeys `effective_min_width` —
    the larger of the two.

    `holds_60pct` is a FLAG, never a filter: on the base that motivated this probe
    (`ecc29e5`) the width came from three buttons of 386/410/494 out of 1310 px —
    29/31/38 % — so a ">= 60 %" FILTER would have printed an empty list exactly on
    the case the probe exists for. The rows carry the other half of the evidence
    and are filtered by nothing.

    Visibility is `isVisibleTo(root)`, never `isVisible()`: nothing in this probe
    is ever shown (showing would let _DialogSizeSizer write gui_state.json), so
    `isVisible()` is False for EVERY descendant — measured 08.10, not one visible
    — and the list would come out empty. `isVisibleTo` is False for the hidden
    pages of a QTabWidget as well (measured: 45 survive), hence
    `descendants_visibility()` reports the count and `guilty_rows` keeps those
    pages: an empty list has to stay distinguishable from "there was no culprit"."""
    from PyQt6.QtWidgets import QWidget

    offender_width = _effective_min_width(widget)
    found: list[tuple[int, object]] = []
    for child in widget.findChildren(QWidget):
        if not child.isVisibleTo(root):
            continue
        width = _effective_min_width(child)
        if width <= 0:
            continue
        found.append((width, child))
    found.sort(key=lambda pair: -pair[0])

    rows = []
    for width, child in found[:TOP_GUILTY]:
        share = (100.0 * width / offender_width) if offender_width else 0.0
        rows.append({
            "class": type(child).__name__,
            "object_name": child.objectName() or None,
            "text": _clip_text(_widget_text(child)),
            "min_hint_width": child.minimumSizeHint().width(),
            "minimum_width_prop": child.minimumWidth(),
            "effective_min_width": width,
            "share_pct": round(share, 1),
            "holds_60pct": share >= 100.0 * HOLDS_SHARE,
            "hidden": child.isHidden(),
            "where": _widget_path(child, root),
            "over_limit": width > limit,
        })
    return rows


def descendants_visibility(widget, root=None) -> dict:
    """How many descendants the visibility filter keeps and drops (Ш1-3).

    Without these two numbers an EMPTY evidence list would read as "there was no
    culprit" — while it actually means "everything that holds the width is
    hidden", which is a normal state of a QTabWidget (only the current page is
    visible to it)."""
    from PyQt6.QtWidgets import QWidget

    root = widget if root is None else root
    children = widget.findChildren(QWidget)
    return {
        "total": len(children),
        "visible_to_root": sum(1 for child in children if child.isVisibleTo(root)),
    }


def _row_items(layout, indexes: list[int], roles=None) -> tuple[int, list[dict], object]:
    """(the sum of the items' minimum widths, the items, the first holder widget).

    `roles` switches to the QFormLayout API, where a row is addressed by row
    NUMBER plus a role (label / field) instead of a flat item index."""
    total = 0
    items: list[dict] = []
    first_holder = None
    for index in indexes:
        candidates = ([layout.itemAt(index, role) for role in roles] if roles
                      else [layout.itemAt(index)])
        for item in candidates:
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
                "text": _clip_text(_widget_text(holder)) if holder is not None else None,
                "min_width": width,
            })
    return total, items, first_holder


def guilty_rows(widget, root, limit: int) -> list[dict]:
    """Layout ROWS whose items' minimum widths ADD UP past the limit.

    A single descendant does not always explain an offender, and the Cell dialog
    is exactly that case: its 1310 px came from three buttons SIDE BY SIDE
    (386 + 410 + 494 + spacing), while each button ALONE was narrower than the
    502 px tab widget sitting next to them (plan §1). Only the sum along a row
    shows it — the arithmetic §1 did by hand, reproduced here for any offender.
    Grid rows are measured the same way (by real row/column positions, not by a
    flat index: a grid's slots may be sparse).

    Since 08.10 the sum carries two more things, both MEASURED before being
    promised (Ш1-4):

    * the row's own margins and SPACING — the plan's "отступы и промежутки".
      Without them the CellDock's three-button rows read 295 px while their real
      total is 307 px: invisible at a limit the dialog was over;
    * `QFormLayout` rows, which were never scanned at all. The CellDock keeps
      most of its controls in forms, so a form-row culprit used to be reported as
      "no single row adds up past the limit".

    The margins of the layouts BETWEEN a row and the offender are deliberately NOT
    added. They were walked once, and the chain over-counted by 6 px on the
    CellDock (18 px of page margins + 18 px of QStackedLayout margins against a
    real 30 px gap), so the report states only what it measured and lets the
    offender's own number — printed in the same block — show the difference.

    Identical rows are collapsed with a `count`: the CellDock has one
    "Add / Update selected / Remove selected" row PER TAB PAGE, and four identical
    lines would bury the answer this message exists to give.

    Nothing here is filtered by visibility: a QTabWidget's minimum is computed
    over ALL its pages, so the holder often sits on a page this probe never shows
    (isVisibleTo(offender) is False for every page but the current one).

    The list is not filtered by the LIMIT either — only flagged by it. Measured
    08.10 on a synthetic offender built for the occasion (the three historical
    captions in one row; the throwaway check lives in ./diagnostics and is not
    committed): the dialog demands 658 px while its widest row is 636 px, i.e.
    the offender passes the limit on the margins of its layouts ALONE. A
    "total > limit" filter answered "no single row adds up past the limit" and
    hid the 636 px row that IS the answer. The widest rows are therefore always
    returned, each with `over_limit`, and the difference is named by
    format_guilty_rows()."""
    from PyQt6.QtWidgets import QFormLayout, QGridLayout, QHBoxLayout

    rows: list[dict] = []

    def add(kind: str, items_sum: int, items: list[dict], spacing: int,
            layout, where: str) -> None:
        own_margins = layout.contentsMargins()
        own = own_margins.left() + own_margins.right()
        gaps = max(len(items) - 1, 0) * max(spacing, 0)
        total = items_sum + gaps + own
        rows.append({
            "layout": kind,
            "sum_min_width": items_sum,
            "spacing": gaps,
            "own_margins": own,
            "total_width": total,
            "over_limit": total > limit,
            "where": where,
            "items": items,
        })

    def where_of(first_holder) -> str:
        return _widget_path(first_holder, root) if first_holder is not None else "?"

    for layout in widget.findChildren(QHBoxLayout):
        items_sum, items, first_holder = _row_items(layout,
                                                    list(range(layout.count())))
        add("QHBoxLayout", items_sum, items, layout.spacing(), layout,
            where_of(first_holder))

    for layout in widget.findChildren(QGridLayout):
        by_row: dict[int, list[int]] = {}
        for index in range(layout.count()):
            row_number = layout.getItemPosition(index)[0]
            by_row.setdefault(row_number, []).append(index)
        for row_number in sorted(by_row):
            items_sum, items, first_holder = _row_items(layout, by_row[row_number])
            add(f"QGridLayout row {row_number}", items_sum, items,
                layout.horizontalSpacing(), layout, where_of(first_holder))

    for layout in widget.findChildren(QFormLayout):
        roles = (QFormLayout.ItemRole.LabelRole, QFormLayout.ItemRole.FieldRole)
        for row_number in range(layout.rowCount()):
            items_sum, items, first_holder = _row_items(layout, [row_number],
                                                        roles=roles)
            add(f"QFormLayout row {row_number}", items_sum, items,
                layout.horizontalSpacing(), layout, where_of(first_holder))

    rows.sort(key=lambda row: -row["total_width"])
    collapsed: list[dict] = []
    seen_rows: dict[tuple, dict] = {}
    for row in rows:
        key = (row["layout"], row["where"], row["sum_min_width"], row["spacing"],
               row["own_margins"],
               tuple((item["class"], item["text"], item["min_width"])
                     for item in row["items"]))
        if key in seen_rows:
            seen_rows[key]["count"] += 1
            continue
        row["count"] = 1
        seen_rows[key] = row
        collapsed.append(row)
    return collapsed[:TOP_GUILTY_ROWS]


def platform_context(lang: str) -> dict:
    """Style, DPI, scaling, font and a fixed sample string — what the SAME layout
    costs on THIS machine (Ш1-6).

    The numbers this probe reports moved by a factor of three between two
    platforms (Linux 337 px, Windows CI 1106 px, both on 08.10), so a report that
    names a width without the metrics behind it cannot be compared with the next
    one — and "one widget stretched" cannot be told from "the whole Qt changed
    under us", which are fixed in completely different places.
    `sample_text_width` is the comparable part: the same string through
    QFontMetrics on each platform."""
    from PyQt6.QtGui import QFontMetrics
    from PyQt6.QtWidgets import QApplication

    def call(obj, name):
        accessor = getattr(obj, name, None) if obj is not None else None
        if not callable(accessor):
            return None
        try:
            return accessor()
        except Exception:  # noqa: BLE001 — a probe must not die on one odd call
            return None

    app = QApplication.instance()
    font = call(app, "font")
    metrics = QFontMetrics(font) if font is not None else None
    screen = call(app, "primaryScreen")
    style = call(app, "style")
    return {
        "style": type(style).__name__ if style is not None else None,
        "platform_name": call(app, "platformName"),
        "app_device_pixel_ratio": call(app, "devicePixelRatio"),
        "logical_dpi": call(screen, "logicalDotsPerInch"),
        "device_pixel_ratio": call(screen, "devicePixelRatio"),
        "font_family": call(font, "family"),
        "font_point_size": call(font, "pointSizeF"),
        "sample_text": SAMPLE_TEXT,
        "sample_text_width": (metrics.horizontalAdvance(SAMPLE_TEXT)
                              if metrics is not None else None),
        "language": lang,
    }


def format_platform_context(context: dict | None) -> str:
    """The ONE context line a failure message starts with (Ш1-6). ASCII only: the
    same line is printed in cmd, whose codepage has no em dash."""
    if not context:
        return "platform context: NOT in this report (probe older than 2026-10-08)"
    return (f"platform: style {context.get('style')} / {context.get('platform_name')} "
            f"/ logical DPI {context.get('logical_dpi')} / screen DPR "
            f"{context.get('device_pixel_ratio')} / app DPR "
            f"{context.get('app_device_pixel_ratio')} / font "
            f"{context.get('font_family')} {context.get('font_point_size')}pt "
            f"/ sample text {context.get('sample_text_width')} px "
            f"/ language {context.get('language')}")


def format_widest_descendants(rows: list[dict] | None) -> str:
    """The descendant lines of a failure message (Ш1-2). `None` means the report
    does not carry the list at all — which is not the same as an empty list."""
    if rows is None:
        return "  (this report carries no descendant list - it predates 2026-10-08)"
    if not rows:
        return ("  (no VISIBLE descendant to name - read the rows above; hidden "
                "QTabWidget pages hold width too)")
    lines = []
    for row in rows:
        text = f" {row['text']!r}" if row.get("text") else ""
        flag = (f"HOLDS {int(HOLDS_SHARE * 100)}% " if row.get("holds_60pct")
                else "          ")
        hidden = "hidden" if row.get("hidden") else "seen"
        over = " OVER" if row.get("over_limit") else ""
        lines.append(f"  {flag}{row['effective_min_width']:5d} px "
                     f"({row['share_pct']:4.1f}% {hidden}{over}) "
                     f"{row['class']}{text}   at {row['where']}")
    return "\n".join(lines)


def format_guilty_rows(rows: list[dict] | None, limit: int,
                       offender_width: int | None = None) -> str:
    """The layout-row lines of a failure message (Ш1-4), widest total first.

    A row UNDER the limit is printed too, flagged `under`, because it can still
    be the answer: an offender whose overrun comes from the margins of its
    layouts has no row over the limit at all, and a message that stopped at
    "nothing adds up" would hide the 636 px row inside the 658 px dialog
    (measured 08.10, see guilty_rows)."""
    if rows is None:
        return "  (this report carries no row list - it predates 2026-10-08)"
    if not rows:
        return "  (the probe found no layout row in this widget at all)"
    lines = []
    if not any(row.get("over_limit") for row in rows):
        widest = rows[0]["total_width"]
        rest = (f"the offender's own minimum is {offender_width} px, so "
                f"{offender_width - widest} px of it are the margins/spacing of "
                f"the layouts above the row (NOT walked - see guilty_rows)"
                if offender_width else "see the offender's own width above")
        lines.append(f"  NO row passes {limit} px on its own; the widest is "
                     f"{widest} px and {rest}:")
    for row in rows:
        flag = "OVER " if row.get("over_limit") else "under"
        times = f" x{row['count']}" if row.get("count", 1) > 1 else ""
        lines.append(f"  {flag} {row['total_width']:5d} px = "
                     f"{row['sum_min_width']} items + {row['spacing']} gaps "
                     f"+ {row['own_margins']} row margins{times}   "
                     f"({row['layout']})")
        lines.append(f"          at {row['where']}")
        for item in row["items"]:
            text = f" {item['text']!r}" if item.get("text") else ""
            lines.append(f"          {item['min_width']:5d} px  {item['class']}{text}")
    return "\n".join(lines)


def format_widest_widgets(report: dict, top: int = 10) -> str:
    """The app-wide table, top `top` — the context that tells ONE offender from a
    platform-wide change. The Russian half of the guard reads it out of the
    JSON, where the whole table is already present."""
    rows = report.get("widgets") or []
    if not rows:
        return "app-wide table: NOT in this report"
    lines = [f"app-wide widest widgets (top {top} of {len(rows)}), limit "
             f"{report.get('limit')} px:"]
    for row in rows[:top]:
        mark = "OVER" if row.get("over_limit") else "    "
        lines.append(f"  {mark} {row['min_width']:5d} px  {row['name']}")
    return "\n".join(lines)


def format_offender_evidence(*, width: int, limit: int,
                             rows: list[dict] | None = None,
                             widest: list[dict] | None = None,
                             visibility: dict | None = None,
                             context: dict | None = None,
                             extra: str | None = None) -> str:
    """The whole evidence block of an assertion message (Ш1-2..Ш1-6).

    ONE wording for both halves of the guard: the in-process (en) half builds the
    lists in the test process, the Russian half reads them out of the probe's
    JSON — and the two messages must not drift apart, or the next Windows log
    would be read differently for no reason."""
    lines = [
        f"over the limit by {width - limit} px (width {width}, limit {limit}) - "
        f"narrow the ROW below, never the limit",
        format_platform_context(context),
    ]
    if extra:
        lines.append(extra)
    if visibility:
        lines.append(f"descendants of the offender: {visibility.get('total')} "
                     f"total, {visibility.get('visible_to_root')} visible to it "
                     f"(isVisibleTo) - the rest are hidden QTabWidget pages and "
                     f"the like; the ROWS below are not filtered by visibility")
    lines.append(f"layout rows, widest first (OVER = passes {limit} px on its own; "
                 f"top {TOP_GUILTY_ROWS}):")
    lines.append(format_guilty_rows(rows, limit, width))
    lines.append(f"widest visible descendants (top {TOP_GUILTY}); HOLDS means "
                 f">= {int(HOLDS_SHARE * 100)} % of the offender's own width:")
    lines.append(format_widest_descendants(widest))
    return "\n".join(lines)


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
        over = hint.width() > limit
        widgets.append({
            "name": name,
            "class": type(widget).__name__,
            "min_width": hint.width(),
            "min_height": hint.height(),
            "over_limit": over,
            # `widget` — not `window` — is the root of the evidence: a QDialog
            # parented to an unshown QMainWindow is not "visible to" that window
            # (measured 08.10: 0 descendants), while isVisibleTo(offender)
            # answers the question the report is about — what the OFFENDER's own
            # layout can see (45 of 300 on the CellDialog). The en half of the
            # guard builds the same lists with the same root, so the two messages
            # are comparable.
            "widest": (widest_descendants(widget, widget, limit) if over else []),
            "guilty_rows": (guilty_rows(widget, widget, limit) if over else []),
            "descendants": (descendants_visibility(widget, widget) if over else None),
        })
    app.processEvents()

    over = [row for row in widgets if row["over_limit"]]
    widgets.sort(key=lambda row: -row["min_width"])
    return {
        "probe": "probe_gui_min_sizes",
        "date": "2026-10-08",
        "task": "techdocs/handoff/deepseek/plan/plan_2026_10_08_cell_dialog_width_windows.md",
        "language": lang,
        "language_env": os.environ.get("LANGUAGE"),
        "qt": QT_VERSION_STR,
        "pyqt": PYQT_VERSION_STR,
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "limit": limit,
        "settings_file": str(settings_path),
        "platform_context": platform_context(lang),
        "measured": len(widgets),
        "over_limit": [row["name"] for row in over],
        "widgets": widgets,
        "cell_dialog": next((row for row in widgets if row["name"] == "CellDialog"), None),
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
    print(format_platform_context(report.get("platform_context")))
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
        print(f"\nover the limit: {row['name']} ({row['min_width']} px)")
        print(format_offender_evidence(
            width=row["min_width"], limit=report["limit"],
            rows=row.get("guilty_rows"), widest=row.get("widest"),
            visibility=row.get("descendants"),
            context=report.get("platform_context")))

    cell = report["cell_dialog"]
    print("\nthe target of this task:")
    if cell is None:
        print("  CellDialog was NOT built - see not_built below")
    else:
        verdict = ("OVER the limit" if cell["over_limit"]
                   else f"OK, {report['limit'] - cell['min_width']} px of slack")
        print(f"  CellDialog  {cell['min_width']} x {cell['min_height']}  {verdict}")

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

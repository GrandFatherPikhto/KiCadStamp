# gui/docks/checks_report.py
"""The THREE levels of a check's `--json` report: section rows, problem rows, and the two
doors of a problem's QView (Ч2б-2 of plan_2026_10_23_checks_tab_top_level).

Why this module exists: `gui/docks/checks.py` launches the checks and owns the tree; it was
already at 778 lines when this level arrived, and rule 45 says new logic goes to a new module
while the grown file keeps the wiring. So everything a REPORT needs lives here:

  * `load_report` — the ONE reader of the machine file `--json PATH` wrote. A missing,
    unreadable or unknown-schema file gives None, and the page then shows the TEXT only
    (the row's verdict still comes from the exit code): a machine file must never invent a
    report, and an old script that knows no `--json` must keep working;
  * `prepare` — the file THIS run writes, with the previous one removed FIRST. A run that
    writes no report (a refusal; an old script) must not have the previous run's rows shown
    as if they were its own;
  * `visible_sections` — the sections that get a NODE: a section with no problems gets none
    (decision of Denis, 26.10), while it stays in the JSON because the row's summary needs
    the list of what was checked;
  * `CheckReportView` — the QView of one section or one problem: the text, `kind`, `where`,
    `refs`, and the two doors. "К ноде в дереве" exists only when `to_node.node` is set, and
    "Подсветить" only when `refs` is not empty — a door that cannot keep its promise is not
    drawn as one;
  * `ReportRows` — the children of a check's row, rebuilt after every run. The selection
    survives by the problem's stable `key` (the very property `check_report` keeps it for),
    never by the row INDEX, so a re-run that reorders problems keeps the operator on the
    problem they were reading;
  * `jump_to_node` / `highlight_refs` — the two doors' bodies, wired with `partial` so the
    page adds one line per door.

WHAT THE DOORS PROMISE (the decisions this file must not lose):

  * "К ноде в дереве" asks `DockHub.select_tree_node`, which shows the Trees page and lands
    on the node; a miss is SAID in the status line (never a modal — rule 43), because a jump
    that silently lands nowhere is worse than no jump;
  * "Подсветить" marks the named parts on the overlay layer under its OWN namespace
    (`overlay_markers.NS_CHECK`), and it never touches the operator's board selection:
    `select_items` is not called anywhere on this path. The marks of the previous press are
    dropped FIRST, so the layer describes the selected problem and nothing else.

The board is read and written on the WORKER only (techdocs/me/door.md): the payload carries
the CONNECTION, and `connection.board` is read inside the worker — the same shape the copper
duplicates panel uses. Positions are read from the live board, not from the report: the report
names PARTS, and where they stand now is the board's answer.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.check_report import SCHEMA_VERSION
from kicadstamp.i18n import _
from kicadstamp.utils.units import MM

from .. import overlay_markers
from ..worker import start_long_op
from .trees_selection import select_tree_node

logger = logging.getLogger(__name__)

#: The `UserRole` payloads of the two NEW node kinds. A check row carries its script STRING,
#: the copper row carries `DEDUPE_ROW`, a duplicate group carries its own tuple — every kind
#: is told apart by the FIRST element, so the page can dispatch without guessing.
SECTION_ROLE = "check-section"
PROBLEM_ROLE = "check-problem"


# ── the report, as data ─────────────────────────────────────────────────────

def load_report(path) -> Optional[dict]:
    """The report this run wrote, or None.

    None is the ordinary answer for three different things, and the page treats all three
    the same way — text only, verdict from the exit code: no `--json` was written (a
    refusal, a crash, an old script), the file is unreadable, or its `schema` is one this
    build does not know. Guessing a meaning for an unknown schema would be inventing a
    report; saying nothing and showing the text does not."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != SCHEMA_VERSION:
        logger.info("Check report %s: schema %r is not %s — text only",
                    path, data.get("schema"), SCHEMA_VERSION)
        return None
    return data


def prepare(directory: str, script: str) -> str:
    """The file THIS run will write — with the previous one removed FIRST.

    The scripts write the report at the END of a run, so between the start and the end the
    file simply does not exist. Without this removal the previous run's report would be read
    as this run's one whenever this run wrote nothing (a refusal, a crash, a `--write` that
    stopped early) — the page would show a stale tree under a fresh verdict."""
    path = Path(directory) / f"{Path(script).stem}.json"
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:  # noqa: BLE001 — a leftover file must never stop a check
        logger.debug("the previous check report could not be removed: %s", path,
                     exc_info=True)
    return str(path)


def with_json(argv: list, directory: str, script: str, state) -> list:
    """`argv` plus `--json <path>` for THIS run, with `state.json_path` remembering it.

    The path is prepared (the older file removed) HERE, so the page cannot start a check
    with a report still standing from the run before it."""
    state.json_path = prepare(directory, script)
    return list(argv) + ["--json", state.json_path]


def sections_with_problems(report) -> list:
    """`(index into report["sections"], section)` for the sections that get a NODE.

    An empty section gets none: the row already says how many problems there are, and a row
    per "checked this, found nothing" would bury the two that matter. The JSON keeps every
    section the run performed — that is what the row's summary reads."""
    out = []
    for index, section in enumerate((report or {}).get("sections") or ()):
        if isinstance(section, dict) and section.get("problems"):
            out.append((index, section))
    return out


def section_at(report, index) -> Optional[dict]:
    sections = (report or {}).get("sections") or ()
    if not isinstance(index, int) or not 0 <= index < len(sections):
        return None
    section = sections[index]
    return section if isinstance(section, dict) else None


def problem_at(report, section_index, problem_index) -> Optional[dict]:
    section = section_at(report, section_index)
    if section is None:
        return None
    problems = section.get("problems") or ()
    if not isinstance(problem_index, int) or not 0 <= problem_index < len(problems):
        return None
    problem = problems[problem_index]
    return problem if isinstance(problem, dict) else None


def section_label(section: dict) -> str:
    """One row: the section's own title and how many problems it holds."""
    title = section.get("title") or section.get("id") or "?"
    return f"{title} ({len(section.get('problems') or ())})"


def problem_label(problem: dict) -> str:
    """One row: the closed-vocabulary `kind` first (it is what the eye sorts on), then the
    very text the human report printed."""
    return f"{problem.get('kind') or '?'}  {problem.get('text') or ''}"


def refs_of(problem) -> list:
    """The parts a door may mark — strings, in the report's order, de-duplicated."""
    out = []
    for ref in (problem or {}).get("refs") or ():
        text = str(ref)
        if text and text not in out:
            out.append(text)
    return out


def node_of(problem):
    """`(tree, node)` when the report names a node it is SURE about, else None.

    Both halves must be non-empty: a tree without a node is not an address, and neither is a
    node without a tree."""
    to_node = (problem or {}).get("to_node") or {}
    tree, node = to_node.get("tree"), to_node.get("node")
    if not tree or not node:
        return None
    return str(tree), str(node)


def where_text(where) -> str:
    """The `where` block as one line, `key: value` pairs in the report's own order."""
    if not isinstance(where, dict) or not where:
        return ""
    parts = [f"{key}: {value}" for key, value in where.items()
             if value not in (None, "")]
    return "  ".join(parts)


# ── the QView of a section or a problem ─────────────────────────────────────

class CheckReportView(QWidget):
    """The report side of the Checks page: the selected section's problems, or the selected
    problem whole (text, kind, where, refs), with its two doors.

    The buttons are the ONLY doors here, and each is enabled exactly when its promise can be
    kept: a node door needs `to_node.node`, a highlight door needs `refs` (and no busy page —
    one board operation at a time, the page's own rule)."""

    #: (tree, node) — the page turns it into `DockHub.select_tree_node`.
    node_jump_requested = pyqtSignal(str, str)
    #: [ref, …] — the page turns it into an overlay write on the worker.
    highlight_requested = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.heading = QLabel("")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        # 1, not 0 — Qt treats an explicit 0 as "unset" (gui/docks/log_panel.py:157).
        self.text.setMinimumHeight(1)
        layout.addWidget(self.text, 1)

        self.where_label = QLabel("")
        self.where_label.setTextFormat(Qt.TextFormat.PlainText)
        self.where_label.setWordWrap(True)
        layout.addWidget(self.where_label)

        self.refs_label = QLabel("")
        self.refs_label.setTextFormat(Qt.TextFormat.PlainText)
        self.refs_label.setWordWrap(True)
        layout.addWidget(self.refs_label)

        buttons = QHBoxLayout()
        self.node_button = QPushButton(_("Go to the tree node"))
        self.node_button.setToolTip(
            _("Open the Trees page on the node this problem names"))
        self.node_button.clicked.connect(self._on_node_clicked)
        buttons.addWidget(self.node_button)
        self.highlight_button = QPushButton(_("Highlight on board"))
        self.highlight_button.setToolTip(
            _("Mark the parts of this problem on the overlay layer — your own board "
              "selection stays as it is"))
        self.highlight_button.clicked.connect(self._on_highlight_clicked)
        buttons.addWidget(self.highlight_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._problem: Optional[dict] = None
        self._busy = False
        self._update_doors()

    # ── what it shows ──────────────────────────────────────────────────────

    def show_role(self, role, report) -> None:
        """Show what `role` names. A role this view does not own, or an index the report no
        longer has (a stale node left by a failed rebuild), shows the empty page — never an
        exception, and never another problem's text under this problem's name."""
        kind = role[0] if isinstance(role, tuple) and role else None
        if kind == SECTION_ROLE and len(role) >= 3:
            self.show_section(report, role[2])
        elif kind == PROBLEM_ROLE and len(role) >= 4:
            self.show_problem(report, role[2], role[3])
        else:
            self.clear()

    def show_section(self, report, index) -> None:
        """A section: its problems as lines, and NO doors — a section is a list, and both
        doors act on one problem's own address."""
        section = section_at(report, index)
        if section is None:
            self.clear()
            return
        problems = [p for p in section.get("problems") or () if isinstance(p, dict)]
        self.heading.setText(section_label(section))
        self.text.setPlainText("\n".join(p.get("text") or "" for p in problems))
        self.where_label.setText("")
        refs = []
        for problem in problems:
            refs.extend(r for r in refs_of(problem) if r not in refs)
        self.refs_label.setText(
            _("Parts: {refs}").format(refs=", ".join(refs)) if refs else "")
        self._problem = None
        self._update_doors()

    def show_problem(self, report, section_index, problem_index) -> None:
        problem = problem_at(report, section_index, problem_index)
        if problem is None:
            self.clear()
            return
        section = section_at(report, section_index) or {}
        title = section.get("title") or section.get("id") or ""
        kind = problem.get("kind") or "?"
        self.heading.setText(f"{kind}  ·  {title}" if title else kind)
        self.text.setPlainText(problem.get("text") or "")
        self.where_label.setText(where_text(problem.get("where")))
        refs = refs_of(problem)
        self.refs_label.setText(
            _("Parts: {refs}").format(refs=", ".join(refs)) if refs else "")
        self._problem = problem
        self._update_doors()

    def clear(self) -> None:
        self.heading.setText("")
        self.text.setPlainText("")
        self.where_label.setText("")
        self.refs_label.setText("")
        self._problem = None
        self._update_doors()

    def set_busy(self, busy: bool) -> None:
        """The page is busy (a check runs, or another overlay operation does): the highlight
        door stands down — one board operation at a time. The node door stays: it touches no
        board at all."""
        self._busy = bool(busy)
        self._update_doors()

    # ── the doors ──────────────────────────────────────────────────────────

    def _update_doors(self) -> None:
        self.node_button.setEnabled(node_of(self._problem) is not None)
        self.highlight_button.setEnabled(bool(refs_of(self._problem)) and not self._busy)

    def _on_node_clicked(self) -> None:
        node = node_of(self._problem)
        if node is not None:
            self.node_jump_requested.emit(*node)

    def _on_highlight_clicked(self) -> None:
        refs = refs_of(self._problem)
        if refs:
            self.highlight_requested.emit(refs)


# ── the children of one check's row ────────────────────────────────────────

class ReportRows:
    """One SECTION node per non-empty section under a check's row, one PROBLEM node per
    problem under its section.

    The selection survives a rebuild by the problem's `key` — the identity the report keeps
    on purpose (Ч2б-1) — so a re-run that reorders or renames sections keeps the operator on
    the problem they were reading; a problem that is GONE drops them to the check row instead
    of onto another problem's text. `takeChildren()` moves the current item to the parent by
    itself (measured, kicadstamp/diagnostics/probe_take_children_current_2026_10_25.py), so
    the row page comes back through the ordinary selection path; the explicit
    `setCurrentItem(row)` covers the case where the row was already current.

    The key is read from `page.report_last_key`, NOT from the current item: a run always
    starts from the check's ROW (Run is off on a problem node — a problem is not something a
    process can act on), so by the time the new report arrives the problem selection is long
    gone. What the page remembers is the problem the operator LOOKED at last, and that is the
    one a re-run must follow."""

    def __init__(self, page):
        self._page = page

    def finish(self, script: str) -> None:
        """A run is over: read ITS report and rebuild the row's children.

        Called from the page's finish slot BEFORE the queue moves on: a run that wrote
        nothing — a refusal, a crash, an old script — simply has no children, and the tree
        and the verdict can never describe two different runs."""
        state = self._page.state_of(script)
        state.report = load_report(state.json_path)
        self.rebuild(script)

    def rebuild(self, script: str) -> None:
        row = self._page._items[script]
        state = self._page.state_of(script)
        keep = self._page.report_last_key.get(script)
        current = self._page.tree.currentItem()
        inside_row = current is row or (current is not None
                                        and current.parent() is row)
        row.takeChildren()
        for section_index, section in sections_with_problems(state.report):
            section_item = QTreeWidgetItem([section_label(section), ""])
            section_item.setData(0, Qt.ItemDataRole.UserRole,
                                 (SECTION_ROLE, script, section_index))
            row.addChild(section_item)
            for problem_index, problem in enumerate(section.get("problems") or ()):
                problem_item = QTreeWidgetItem([problem_label(problem), ""])
                problem_item.setToolTip(0, problem.get("text") or "")
                problem_item.setData(0, Qt.ItemDataRole.UserRole,
                                     (PROBLEM_ROLE, script, section_index,
                                      problem_index))
                section_item.addChild(problem_item)
            # A section's problems are what the operator came for: they are open.
            section_item.setExpanded(True)
        row.setExpanded(row.childCount() > 0)
        target = self._item_for_key(row, keep) if keep else None
        if target is not None:
            self._page.tree.setCurrentItem(target)
        elif inside_row:
            self._page.tree.setCurrentItem(row)
        self._page._update_buttons()

    def _item_for_key(self, row, key):
        """The problem row whose report key is `key` — None when the finding is gone."""
        for position in range(row.childCount()):
            section_item = row.child(position)
            for index in range(section_item.childCount()):
                item = section_item.child(index)
                role = item.data(0, Qt.ItemDataRole.UserRole)
                if not isinstance(role, tuple) or role[0] != PROBLEM_ROLE:
                    continue
                problem = problem_at(self._page.state_of(role[1]).report,
                                     role[2], role[3])
                if problem is not None and problem.get("key") == key:
                    return item
        return None


# ── wiring, so the page keeps one line per door ────────────────────────────

def attach_report_side(page):
    """Build the report side of the Checks page and wire it to the page's own doors.

    Returns `(view, rows)`: the page puts the view in its pane stack (done here) and calls
    `rows.rebuild(script)` after every finished run."""
    page.report_dir = tempfile.mkdtemp(prefix="kicadstamp-checks-")
    #: The problem the operator last LOOKED at, per check script (see ReportRows).
    page.report_last_key = {}
    view = CheckReportView()
    view.node_jump_requested.connect(partial(jump_to_node, page))
    view.highlight_requested.connect(partial(highlight_refs, page))
    page.pane.addWidget(view)
    return view, ReportRows(page)


def show_role_on_page(page, role) -> bool:
    """Show a report role in the page's pane. False for any other kind of selection, so the
    page's own dispatch keeps one branch per kind and the body lives here (rule 45)."""
    if not isinstance(role, tuple) or not role \
            or role[0] not in (SECTION_ROLE, PROBLEM_ROLE):
        return False
    report = page.state_of(role[1]).report
    page.pane.setCurrentWidget(page.report_view)
    page.report_view.show_role(role, report)
    if role[0] == PROBLEM_ROLE and len(role) >= 4:
        problem = problem_at(report, role[2], role[3])
        if problem is not None and problem.get("key"):
            page.report_last_key[role[1]] = problem["key"]
    page._show_status("", None)
    page._update_buttons()
    return True


def jump_to_node(page, tree: str, node: str) -> None:
    """«К ноде в дереве»: show the Trees page, then land on the node — ONE walk, and the
    page SAYS what happened.

    Two halves, each where it belongs and NEITHER in a giant (rule 45): the page switch is
    `DockHub.show_left_page`, and the selection is `trees_selection.select_tree_node`, which
    takes the dock as an argument instead of being a method ON it. The page never learns that
    the Trees tab and the tree widget are two different things.

    A miss is one line in the status bar, never a modal (rule 43): a jump that silently lands
    nowhere is worse than no jump at all."""
    hub = getattr(page._main_window, "dock_hub", None)
    dock = getattr(hub, "trees_dock", None)
    if dock is None or not tree or not node:
        _say_miss(page, tree, node)
        return
    hub.show_left_page(dock)
    if not select_tree_node(dock, tree, node):
        _say_miss(page, tree, node)
        return
    page._show_status(_("Selected {node} in {tree}.").format(node=node, tree=tree),
                      "success")


def _say_miss(page, tree, node) -> None:
    text = _("The node {node} is not in the tree {tree}.").format(node=node, tree=tree)
    page._show_status(text, "error")
    logger.info("Check report: %s", text)


def highlight_refs(page, refs) -> None:
    """«Подсветить»: mark the problem's parts on the overlay layer.

    A read of their positions and a write of overlay shapes only: the operator's own board
    selection is never touched (`select_items` is not called anywhere on this path), and the
    marks live in the report's OWN namespace, so the duplicate panel's circles and every cell
    anchor stay exactly where they were."""
    wanted = [str(ref) for ref in (refs or ()) if str(ref)]
    connection = getattr(page, "_connection", None)
    if not wanted:
        return
    if not getattr(connection, "is_connected", False):
        page._show_status(_("Connect to KiCad first."), "error")
        return
    start_long_op(connection, (), _highlight_worker,
                  partial(_finish_highlight, page), partial(_failed_highlight, page),
                  {"connection": connection, "refs": wanted},
                  busy_text=_("marking the board"))


# ── the worker half (no widget is touched here) ────────────────────────────

def _highlight_worker(payload) -> dict:
    """Draw one marker per ref, on the worker — the board is read HERE, and the payload
    carries the CONNECTION, never the board (door rule)."""
    connection = payload.get("connection")
    board = getattr(connection, "board", None) if connection is not None else None
    adapter = getattr(board, "adapter", None) if board is not None else None
    if adapter is None:
        return {"error": _("Connect to KiCad first.")}
    owner = overlay_markers.OverlayMarkerOwner()
    # The previous press's marks go FIRST: the layer describes the selected problem, and a
    # set of marks never outlives the report it was drawn from (the Д-2 lesson of Ч2а).
    try:
        owner.remove_namespace(adapter, overlay_markers.NS_CHECK)
    except Exception:  # noqa: BLE001 — a visualisation must never fail the door
        logger.debug("the previous check marks could not be removed", exc_info=True)
    by_ref: dict = {}
    for fp in adapter.get_footprints() or ():
        ref = getattr(fp, "ref", None)
        if ref:
            by_ref.setdefault(ref, fp)
    refs = list(payload.get("refs") or ())
    drawn, missing = 0, []
    for ref in refs:
        fp = by_ref.get(ref)
        position = getattr(fp, "position", None) if fp is not None else None
        if position is None:
            missing.append(ref)
            continue
        key = overlay_markers.check_marker_key(ref)
        if owner.ensure_marker(adapter, key, position.x / MM, position.y / MM):
            drawn += 1
        else:
            missing.append(ref)
    return {"drawn": drawn, "total": len(refs), "missing": missing}


def _finish_highlight(page, result) -> None:
    if result.get("error"):
        page._show_status(result["error"], "error")
        return
    drawn, total = result.get("drawn", 0), result.get("total", 0)
    missing = list(result.get("missing") or ())
    text = _("Marked {drawn} of {total} parts.").format(drawn=drawn, total=total)
    if missing:
        text += " " + _("Not on the board: {refs}.").format(refs=", ".join(missing))
        page._show_status(text, "warn")
        return
    page._show_status(text, "success")


def _failed_highlight(page, message: str) -> None:
    text = _("Marking failed: {error}").format(error=message)
    page._show_status(text, "error")
    logger.warning("Check report: %s", text)

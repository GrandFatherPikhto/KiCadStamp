# tests/gui/test_hub_composition_steps.py
"""Ч1 of plan_2026_10_22_dock_hub_reorganization: the constructor is TEN NAMED STEPS, and the
infrastructure (the root config's log_file: handler, the working set's listener/timer) lives in
`gui/hub_infrastructure.py`.

Cells, by the property each one holds (Ч1-С1…Ч1-С7 of the plan's step table):

  С1  the constructor is named steps and `_finish_composition` comes last
  С2  the constructor holds no logic besides those steps (no if/for/try/with)
  С3  the names the rest of the GUI reads still live on the hub, and `docks == [log_dock]`
  С4  `_log_file_handler` is the seam over the NEW owner (`RootLogFile.current`)
  С5  `RootLogFile` keeps exactly ONE handler across swaps (old one detached)
  С6  a Discard re-peeks through the same infrastructure: still ONE handler, same file
  С7  the dirty indicator hears a root change (the narrow `host` of `on_root_changed`)

Two cells are structural (С1, С2) ON PURPOSE: the whole deliverable of this part is a shape —
a 300-line constructor cut into named steps — and a shape nobody checks is a shape that grows
back. The rest are behavioural and run against a REAL `DockHub` on the bare `main_window`
fixture (a plain QMainWindow), which is the shape the hub is built in during tests.

Existing guards are NOT duplicated or edited here: the eight log-file cells of
`tests/gui/test_phase3_wiring.py` (attach, swap, default path, QueueListener) and the unbound
`DockHub._on_root_changed_for_working_set(_Fake(), …)` cell of
`tests/config/test_config_format3_wiring.py` stay exactly as they were (п. 33) — they are what
says the moved rules still behave, and С4…С7 name the NEW owner they now go through.
"""
import ast
import logging
from pathlib import Path

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_working_set import WORKING_SET, active_graph_root

from gui import hub_infrastructure
from gui.dock_hub import DockHub
from tests.fakes.write_later import write_later
from tests.paths import REPO_ROOT as REPO

HUB = REPO / "gui" / "dock_hub.py"

#: The ten steps, in the order the constructor must call them: every one is ONE contiguous
#: block of the constructor this split came from, so no dock is built in a new order.
STEPS = (
    "_build_components_master_detail",
    "_build_central_tabs",
    "_wire_board_write_hooks",
    "_build_config_pages",
    "_build_record_dialogs",
    "_build_entity_pages",
    "_build_tool_dialogs",
    "_build_bottom_docks",
    "_expose_docks_for_view_menu",
    "_finish_composition",
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _class_methods() -> dict:
    """`{name: node}` for every method of the REAL DockHub class (parsed, not imported)."""
    tree = ast.parse(HUB.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "DockHub")
    return {n.name: n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _self_call_names(node) -> list:
    """Top-level `self.<name>(…)` calls of one function body, in source order."""
    names = []
    for stmt in node.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "self"):
                names.append(func.attr)
    return names


def _write_root(directory: Path, data: dict) -> Path:
    """A root config on disk, written the way the OTHER readers expect (write_later keeps the
    mtime strictly advanced, the Windows lesson of test_phase3_wiring's own `_write`)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "root.sexp"
    write_later(path, dict_to_sexp(data, format_number=2))
    return path


def _file_handlers() -> list:
    return [h for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler)]


def _handlers_for(path: Path) -> int:
    """How many handlers write THIS file. Counting every FileHandler would count the harness:
    pytest installs its own (`/dev/null`) into the root logger for each test."""
    return sum(1 for h in _file_handlers() if Path(h.baseFilename) == path)


def _teardown(hub) -> None:
    """Same leak, same reasoning as test_phase3_wiring's `_teardown_hub`: the Log dock's
    handler and the root's log_file handler both outlive the hub otherwise (and the second one
    holds an open handle into tmp_path, which blocks the delete on Windows)."""
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


# ── С1, С2: the shape ───────────────────────────────────────────────────────

def test_the_constructor_is_named_steps_and_the_finish_comes_last():
    """Ч1-С1. `__init__` is state + exactly these ten calls, in this order."""
    names = _self_call_names(_class_methods()["__init__"])
    assert names == list(STEPS)
    assert names[-1] == "_finish_composition", (
        "the finish must come LAST: it refreshes the hotkeys list and starts the diagnostics "
        "reminder, both of which need EVERY dock already built")
    for step in STEPS:
        assert callable(getattr(DockHub, step)), f"{step} is not a method of DockHub"


def test_the_finish_wires_then_refreshes_hotkeys_then_speaks_about_diagnostics():
    """Ч1-С1 (second half). The tail's own order: signals, hotkeys, diagnostics, working set.

    `refresh_hotkeys()` here is a belt-and-braces call today (ConfiguratorDock refreshes its
    own page at construction and nothing registers a hotkey after it) — see the Ч1 step note.
    What this cell keeps is that the call cannot silently disappear from a tail that was cut
    into one method."""
    node = _class_methods()["_finish_composition"]
    source = "\n".join(HUB.read_text(encoding="utf-8").splitlines()
                       [node.lineno - 1:node.end_lineno])
    order = [source.index("self._wire()"),
             source.index("refresh_hotkeys"),
             source.index("sync_diagnostics_recording"),
             source.index("install_working_set")]
    assert order == sorted(order), f"the tail's order changed: {order}"


def test_the_constructor_holds_no_logic_besides_the_steps():
    """Ч1-С2. State assignments and step calls only — a step body pasted back would show up
    here as an `If`/`For`/`Try`/`With` (or as a call that is not one of the steps)."""
    node = _class_methods()["__init__"]
    for stmt in node.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            continue
        assert isinstance(stmt, (ast.Assign, ast.AnnAssign)), (
            f"__init__ statement {type(stmt).__name__} at line {stmt.lineno} is not a step "
            f"call or an assignment — logic belongs in a step")
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        for target in targets:
            assert (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"), (
                f"__init__ assigns something that is not `self.<name>` at line {stmt.lineno}")


# ── С3: the names the rest of the GUI reads ─────────────────────────────────

def test_the_names_the_rest_of_the_gui_reads_still_live_on_the_hub(main_window):
    """Ч1-С3 (the plan's cell 1, with Denis's correction: `docks == [log_dock]`)."""
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        for name in ("_selection_footprints", "_selection_raw_items",
                     "_scheme_active_op", "_sheet_dir_checked"):
            assert hasattr(hub, name), f"{name} disappeared from the hub"
        for name in ("_on_root_file_changed_for_logging", "_on_root_changed_for_working_set",
                     "_on_working_set_changed", "_refresh_from_working_set",
                     "_update_dirty_indicator", "_safe_call"):
            assert callable(getattr(hub, name)), f"{name} is not callable any more"
        assert hub.docks == [hub.log_dock]
    finally:
        _teardown(hub)


# ── С4…С6: the log file's new owner ────────────────────────────────────────

def test_the_log_file_handler_seam_points_at_the_new_owner(main_window, tmp_path):
    """Ч1-С4. `_log_file_handler` is a property over `RootLogFile.current`, not a field."""
    assert isinstance(DockHub._log_file_handler, property)
    root = _write_root(tmp_path, {"log_file": "logs/run.log"})
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub._log_file_handler is None

        hub.root_metadata_dock.root_changed.emit(root)

        assert hub._log_file_handler is hub._root_log.current
        assert hub._log_file_handler is not None
        assert Path(hub._log_file_handler.baseFilename) == (tmp_path / "logs" / "run.log").resolve()
    finally:
        _teardown(hub)


def test_the_root_log_file_keeps_exactly_one_handler_across_swaps(tmp_path):
    """Ч1-С5. The new owner, WITHOUT a hub: the second swap detaches the first handler."""
    first = _write_root(tmp_path / "first", {"log_file": "logs/first.log"})
    second = _write_root(tmp_path / "second", {"log_file": "logs/second.log"})
    first_path = (tmp_path / "first" / "logs" / "first.log").resolve()
    second_path = (tmp_path / "second" / "logs" / "second.log").resolve()

    root_log = hub_infrastructure.RootLogFile()
    first_handler = None
    try:
        root_log.swap(first)
        first_handler = root_log.current
        assert first_handler is not None
        assert first_handler in logging.getLogger().handlers
        assert _handlers_for(first_path) == 1

        root_log.swap(second)

        assert root_log.current is not first_handler
        assert first_handler not in logging.getLogger().handlers, (
            "the previous FileHandler stayed attached — one process would write two files")
        assert _handlers_for(first_path) == 0
        assert _handlers_for(second_path) == 1
        assert Path(root_log.current.baseFilename) == second_path
    finally:
        for handler in (root_log.current, first_handler):
            if handler is not None:
                logging.getLogger().removeHandler(handler)
                handler.close()


def test_a_discard_leaves_one_handler_and_the_same_working_set_rules(
        main_window, tmp_path, monkeypatch):
    """Ч1-С6. Discard re-runs the startup sync, and the log-file re-peek inside it.

    `root_path` is a READ-ONLY property restored from the settings, so a cell that only
    emits `root_changed` leaves Discard pointing at whatever root the machine last opened
    (measured: the real `profiles/3ch-awg-tia-v103/config.sexp`). The patch makes the cell
    talk about ITS root — and it is a patch, not a product change."""
    root = _write_root(tmp_path, {"log_file": "logs/run.log"})
    log_path = (tmp_path / "logs" / "run.log").resolve()
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    monkeypatch.setattr(type(hub.root_metadata_dock), "root_path", property(lambda _self: root))
    before_handler = None
    try:
        hub.root_metadata_dock.root_changed.emit(root)
        before_handler = hub._root_log.current
        assert _handlers_for(log_path) == 1

        hub.reload_project_from_disk()

        assert _handlers_for(log_path) == 1, "Discard left a second handler (or none)"
        assert hub._root_log.current is not None
        assert Path(hub._root_log.current.baseFilename) == log_path
        assert hub._log_file_handler is hub._root_log.current
    finally:
        _teardown(hub)
        if before_handler is not None and before_handler is not hub._root_log.current:
            before_handler.close()


# ── С7: the narrow host of the moved working-set rule ──────────────────────

class _Host:
    """Exactly what `on_root_changed` may touch besides the module globals."""

    def __init__(self) -> None:
        self.dirty = 0

    def _update_dirty_indicator(self) -> None:
        self.dirty += 1


def test_the_dirty_indicator_hears_a_root_change(tmp_path):
    """Ч1-С7. The moved body still tells the window AND a root switch starts CLEAN.

    The second half is the one no existing cell holds: the format-3 cell asserts
    `active_graph_root` only, so dropping `WORKING_SET.clear()` would leave every leg green
    while a project switch inherited the previous project's staged writes."""
    host = _Host()
    hub_infrastructure.on_root_changed(host, None)
    assert host.dirty == 1
    assert WORKING_SET.enabled is False
    assert active_graph_root() is None

    WORKING_SET.stage_write(tmp_path / "staged.sexp", {"probe": 1})
    assert WORKING_SET.is_dirty()
    host = _Host()
    hub_infrastructure.on_root_changed(host, tmp_path / "root.sexp")
    assert not WORKING_SET.is_dirty(), (
        "a root switch kept the previous project's staged writes")
    assert host.dirty == 1
    assert WORKING_SET.enabled is True
    assert active_graph_root() == tmp_path / "root.sexp"

    # ...and the hub's own delegate is the same rule, on the hub-shaped host
    host = _Host()
    DockHub._on_root_changed_for_working_set(host, None)
    assert host.dirty == 1
    assert not WORKING_SET.is_dirty()

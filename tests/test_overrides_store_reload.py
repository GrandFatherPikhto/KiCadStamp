# tests/test_overrides_store_reload.py
"""Т5г's tail (plan_2026_09_19_poll_adapter_store_reload): the poll adapter
HEARS about a write into the override store.

§0 of the plan, in one sentence: the store is a FILE, several holders keep a
copy of it in memory, and the GUI's own poll adapter was the one holder nobody
told about a write. Everything downstream of that copy — every picker's Role
list, the Components tree's showcase — kept describing the BOARD while the store
held our values, so the very thing Т5г was built for (С25: a role noted in
KiCadStamp can be CHOSEN) silently broke again.

Two facts about the existing guards, kept in mind here:

  * С25/С27 (tests/test_overrides_snapshot_and_diff.py) hands the layer a store
    that is ALREADY FILLED — it proves "a value that was there before the binding
    is visible", never "a value recorded AFTER the binding arrives". Every guard
    below goes the OTHER way: bind EMPTY, record later, notify, look.
  * The event under test is the REAL DockHub._on_overrides_written, called on a
    three-attribute stub (building a real DockHub would construct every Qt dock
    for a method that reaches three attributes). Calling the real method — not a
    copy of it — is the point: deleting its third line must turn С1 red (М1).

The behavioural half of С5 — the whole WIRED chain, real DockHub and real docks —
lives in tests/gui/test_overrides_store_reload_gui.py, where a QApplication
exists. Here С5 is the STRUCTURAL half: the inventory of holders is derived from
the source, so the list cannot drift the way it did in §0.

Файл намеренно без Qt: ни один QWidget здесь не создаётся.
"""
import ast
import types
from pathlib import Path
from types import SimpleNamespace

from gui.connection import BoardConnection
from gui.dock_hub import DockHub
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import (SOURCE_CELL_TABLE, SOURCE_CLI,
                                        load_field_overrides)
from kicadstamp.utils.paths import overrides_path_for_config

REPO = Path(__file__).resolve().parents[1]
GUI_DIR = REPO / "gui"
HUB_SOURCE = GUI_DIR / "dock_hub.py"

UUID_R1 = "uuid-R1"


# ── the pieces under test ─────────────────────────────────────────────────

class _Inner:
    """The board side of the layer. Counts every read, so "the reload read the
    BOARD" is measurable rather than assumed (С3)."""

    def __init__(self, fields=None):
        self._fields = dict(fields or {})
        self.reads = 0

    def get_field_value(self, footprint, field):
        self.reads += 1
        return self._fields.get((footprint.ref, field))


class _Board:
    """The `board` half of a connection: an adapter, and a SPY for every door a
    real Board offers to the socket.

    Recording rather than raising, deliberately: DockHub._safe_call swallows
    whatever a reload throws, so an exception-based spy would let a guard pass
    on a technicality (the log would carry the traceback, the test would not)."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.calls = []

    def select(self, *args, **kwargs):
        self.calls.append("select")
        return []

    def refresh(self, *args, **kwargs):
        self.calls.append("refresh")


class _Holder:
    """One GUI pane holding a copy of the store, reduced to the single thing the
    event asks of it."""

    def __init__(self):
        self.reloads = 0

    def reload_overrides(self) -> None:
        self.reloads += 1


def _event_hub(connection, window=None, cell_editor=None, imprint_page=None):
    """A DockHub-shaped stub the REAL _on_overrides_written runs against.

    Four attributes are all that method reaches — the fieldstool window, the
    cell editor, the imprint page's Roles tab (2026-09-20, Д2) and the poll
    adapter's reload seam — so no Qt dock (and no QApplication) is needed to
    exercise it."""
    hub = SimpleNamespace(
        fieldstool_dock=SimpleNamespace(
            window=window if window is not None else _Holder()),
        cell_anchor_view=cell_editor if cell_editor is not None else _Holder(),
        imprint_dock=imprint_page if imprint_page is not None else _Holder(),
        _reload_poll_store=getattr(connection, "reload_store", None))
    hub._safe_call = types.MethodType(DockHub._safe_call, hub)
    hub._on_overrides_written = types.MethodType(
        DockHub._on_overrides_written, hub)
    return hub


def _footprint():
    """A footprint whose symbol uuid reads out of the identity chain the store is
    keyed by (see kicadstamp.field_overrides.symbol_uuid_of)."""
    return SimpleNamespace(
        ref="R1",
        sheet_path=SimpleNamespace(
            path=[SimpleNamespace(value=UUID_R1)]),
        sheet_path_uuids=(UUID_R1,))


def _profile(tmp_path, *, source=None) -> Path:
    """A profile file — the store hangs off its path (overrides/<stem>.fields.json).
    `source` writes the role_cluster_source switch into it, through the config
    writer's own serializer (a hand-written s-expr is not the format)."""
    profile = tmp_path / "prof.sexp"
    payload = {"role_cluster_source": source} if source else {}
    profile.write_text(dict_to_sexp(payload), encoding="utf-8")
    return profile


def _bound_connection(tmp_path, *, source=None):
    """(connection, adapter, inner, board, profile) with the poll adapter's layer
    bound to the project's store the way the GUI leaves it after a project opens
    — the case С25/С27 never covered: the store is EMPTY at binding time."""
    profile = _profile(tmp_path, source=source)
    inner = _Inner()
    adapter = FieldOverrideAdapter(inner)
    board = _Board(adapter)
    connection = BoardConnection()
    connection.board = board
    connection.set_project_config(profile)
    return connection, adapter, inner, board, profile


def _store_of(profile):
    """Another holder's OWN store object for the same file — exactly the shape
    the Refs table writes through (gui/docks/cell_refs_tab.py: an object of its
    own, saved once)."""
    return load_field_overrides(overrides_path_for_config(str(profile)))


# ── С1: the main guard (RED on the base commit) ───────────────────────────

def test_c1_the_poll_adapter_hears_a_write_made_by_another_holder(tmp_path):
    """Bind EMPTY → another holder records and saves → the write event → the
    poll adapter serves the NEW value.

    This is the hole of §0: the adapter's copy went stale on the write and it had
    no way to learn otherwise, while С25 promises the noted role is choosable in
    a picker built from THIS adapter's snapshot."""
    connection, adapter, _inner, _board, profile = _bound_connection(tmp_path)
    footprint = _footprint()

    assert adapter.store is not None                    # bound, not layered empty
    assert adapter.store.get(UUID_R1, ROLE_FIELD_NAME) is None
    assert adapter.get_field_value(footprint, ROLE_FIELD_NAME) is None

    other = _store_of(profile)
    other.set(UUID_R1, "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CELL_TABLE)
    other.save()

    window, cell_editor = _Holder(), _Holder()
    _event_hub(connection, window, cell_editor)._on_overrides_written()

    assert adapter.get_field_value(footprint, ROLE_FIELD_NAME) == "R_WRITTEN", (
        "the poll adapter still serves the store it was bound to BEFORE the "
        "write: the snapshot every picker and the Components tree read would "
        "show the board's roles (С25 broken again)")
    assert adapter.store.get(UUID_R1, ROLE_FIELD_NAME) == "R_WRITTEN"
    # The two holders that were already on the list are still on it.
    assert (window.reloads, cell_editor.reloads) == (1, 1)


# ── С2: the same event, the other direction (Т6's "forget") ───────────────

def test_c2_a_record_forgotten_by_another_holder_stops_being_served(tmp_path):
    """A reload that only ever ADDS (М2) would keep serving a value the user
    cancelled: after the second event the adapter must fall back to the board."""
    connection, adapter, _inner, _board, profile = _bound_connection(tmp_path)
    footprint = _footprint()

    first = _store_of(profile)
    first.set(UUID_R1, "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CELL_TABLE)
    first.save()
    _event_hub(connection)._on_overrides_written()
    assert adapter.get_field_value(footprint, ROLE_FIELD_NAME) == "R_WRITTEN"

    # The other holder changes its mind and saves an empty table over the file.
    second = _store_of(profile)
    assert second.forget(UUID_R1, ROLE_FIELD_NAME) == 1
    second.save()
    _event_hub(connection)._on_overrides_written()

    assert adapter.store.get(UUID_R1, ROLE_FIELD_NAME) is None, (
        "the forgotten value is still in force")
    assert adapter.get_field_value(footprint, ROLE_FIELD_NAME) is None


# ── С3: a FILE operation, and only that (door §2) ─────────────────────────

def test_c3_the_reload_never_touches_the_board_or_the_socket(tmp_path):
    """No board read, no snapshot rebuild, no long op: the store is a file, and
    the board did not change (door rule 3: one socket owner, via start_long_op).

    The spy records rather than raises (see _Board): "update the snapshot too"
    (М3) must fail HERE, on the recording, not be swallowed on its way out."""
    connection, adapter, inner, board, profile = _bound_connection(tmp_path)
    before_version = connection.snapshot_version

    other = _store_of(profile)
    other.set(UUID_R1, "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CLI)
    other.save()

    _event_hub(connection)._on_overrides_written()

    assert adapter.store.get(UUID_R1, ROLE_FIELD_NAME) == "R_WRITTEN"  # it did work
    assert board.calls == [], (
        "the reload reached the board: {calls}".format(calls=board.calls))
    assert inner.reads == 0, "the reload read a field off the board"
    assert connection.snapshot_version == before_version, (
        "the reload rebuilt the board snapshot — a socket round-trip for a "
        "board nobody changed")
    assert connection.long_op_active is False


# ── С4: no bound store → the event passes silently ────────────────────────

def test_c4_without_a_bound_store_the_event_never_raises(tmp_path, caplog):
    """The three shapes of "nothing to re-read": no project open, a profile whose
    switch says "board", a bare adapter a caller built itself. Each must be a
    SILENT no-op (М4 would raise), and the Log must stay free of the guard's
    exception line."""
    caplog.set_level("DEBUG")

    # (a) a board, but no project was ever opened.
    adapter = FieldOverrideAdapter(_Inner())
    never_opened = BoardConnection()
    never_opened.board = _Board(adapter)
    _event_hub(never_opened)._on_overrides_written()
    assert adapter.store is None

    # (b) the profile's own switch says "board": the layer stays INERT even
    # though the file may well have records — the switch is the switch.
    connection, adapter, _inner, _board, profile = _bound_connection(
        tmp_path, source="board")
    other = _store_of(profile)
    other.set(UUID_R1, "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CELL_TABLE)
    other.save()
    _event_hub(connection)._on_overrides_written()
    assert adapter.store is None, (
        "a profile that says \"board\" must not start serving the store")

    # (c) no board at all, and (d) a bare adapter with no layer to rebind.
    empty = BoardConnection()
    _event_hub(empty)._on_overrides_written()
    bare = BoardConnection()
    bare.board = SimpleNamespace(adapter=SimpleNamespace())
    _event_hub(bare)._on_overrides_written()

    failures = [record for record in caplog.records
                if record.levelno >= 40 and "GUI:" in record.getMessage()]
    assert failures == [], [r.getMessage() for r in failures]


# ── С5, static half: who holds the store, derived from the SOURCE ─────────

def _self_overrides_value(node):
    """Yield the VALUE of every assignment to ``self._overrides`` inside `node`."""
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            targets = child.targets
        elif isinstance(child, ast.AnnAssign) and child.value is not None:
            targets = [child.target]
        else:
            continue
        for target in targets:
            if (isinstance(target, ast.Attribute) and target.attr == "_overrides"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                yield child.value


def _callee_name(func):
    """The dotted name of an attribute chain ('self.fieldstool_dock.window.
    reload_overrides'), or None when the base is not a plain name."""
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    parts.append(func.id)
    return ".".join(reversed(parts))


def _dotted_names(node):
    """Every dotted chain used anywhere inside `node` — as a call target OR as an
    ARGUMENT. The second half is what matters here: _on_overrides_written spells
    its stops as arguments to _safe_call, so scanning call targets alone would
    find none of them and the guard would pass while naming nothing."""
    nested = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Attribute):
            nested.add(id(child.value))
        elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            nested.add(id(child.func))
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and id(child) not in nested:
            name = _callee_name(child)
            if name:
                names.add(name)
    return names


def _store_holders():
    """{module:Class -> {"accepts": [...], "reloads": [...]}} for every class in
    gui/ that keeps a store ON ITSELF (``self._overrides = <not None>``).

    The ``= None`` of every __init__ is skipped on purpose: that is the empty
    state, not a copy of a file. `reloads` names the methods that re-read the
    file (they call load_field_overrides); `accepts` names the methods that take
    a store handed to them."""
    holders = {}
    for path in sorted(GUI_DIR.rglob("*.py")):
        module = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=module)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for method in node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                info = holders.setdefault(
                    "{}:{}".format(module, node.name),
                    {"accepts": set(), "reloads": set()})
                for value in _self_overrides_value(method):
                    if isinstance(value, ast.Constant) and value.value is None:
                        continue
                    info["accepts"].add(method.name)
                    if any(isinstance(call, ast.Call)
                           and _callee_name(call.func) == "load_field_overrides"
                           for call in ast.walk(method)):
                        info["reloads"].add(method.name)
    return {key: {name: sorted(values) for name, values in info.items()}
            for key, info in holders.items() if info["accepts"]}


#: Measured 2026-09-19, base `2b7501b` + this change (Т3's inventory);
#: gui/docks/imprint_refs_tab.py joined 2026-09-20 (Д2 of
#: plan_2026_09_18_scheme_list_to_cell_and_capture.md — the imprint's Roles tab
#: records into the same store).
STORE_HOLDERS = {
    "gui/fieldstool_window.py:MainWindow",
    "gui/docks/cell_anchor_view.py:CellAnchorView",
    "gui/docks/cell_refs_tab.py:RefsTabWidget",
    "gui/docks/imprint_refs_tab.py:ImprintRefsTab",
    "gui/docks/pending.py:PendingChangesDock",
}

#: The stops of the write event, spelled as the source spells them.
EVENT_STOPS = {
    "self.fieldstool_dock.window.reload_overrides",
    "self.cell_anchor_view.reload_overrides",
    "self.imprint_dock.reload_overrides",
    "self._reload_poll_store",
}


def test_c5_the_store_holder_inventory_is_exactly_this():
    """The §0 hole was a LIST nobody re-checked: "both holders re-read the file"
    while the poll adapter was a third. This guard derives the inventory from the
    AST instead of trusting a sentence, so a NEW holder fails here — and that is
    the whole point: it must be a decision (how does it re-read?), not an
    oversight.

    Two ways to satisfy it, both honest: give the new holder a place in
    _on_overrides_written (DockHub) and in this list, or prove it cannot go
    stale and say so next to the list."""
    found = set(_store_holders())
    delta = found.symmetric_difference(STORE_HOLDERS)
    assert found == STORE_HOLDERS, (
        "the set of classes holding a copy of the override store changed: "
        "{delta}. A holder that re-reads nothing keeps serving the values it "
        "loaded at project open — wire it into DockHub._on_overrides_written "
        "(and into EVENT_STOPS below), or document why it cannot go stale."
        .format(delta=sorted(delta)))


def test_c5_every_holder_has_a_way_to_be_given_the_truth():
    """A holder must be reachable: either it re-reads the file itself (a reload
    called by the event) or it accepts the fresh object handed to it by one that
    does (DockHub's list pushes into the window, the window pushes into Pending,
    the cell editor pushes into the Refs tab). The BEHAVIOURAL proof that the
    push actually happens is tests/gui/test_overrides_store_reload_gui.py."""
    for holder, facts in sorted(_store_holders().items()):
        assert facts["reloads"] or facts["accepts"], (
            "{holder} appears to hold a store but has no reload_overrides and "
            "no acceptor — nothing can ever refresh it".format(holder=holder))


def test_c5_the_write_event_names_a_stop_for_every_holder_category():
    """The event itself must carry all three stops: the two panes that re-read
    their own copy and the poll adapter's bound store. Read from the AST rather
    than by grepping the whole file: a stop that survives only in __init__ is not
    a stop (that is exactly М1)."""
    tree = ast.parse(HUB_SOURCE.read_text(encoding="utf-8"))
    body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_overrides_written":
            body = node
            break
    assert body is not None, "_on_overrides_written is gone from gui/dock_hub.py"
    missing = EVENT_STOPS - _dotted_names(body)
    assert missing == set(), (
        "the write event no longer reaches: {missing} — those holders would "
        "keep serving the store they loaded at project open".format(
            missing=sorted(missing)))


def test_c5_the_poll_adapter_layer_keeps_both_halves_of_the_seam():
    """The FIFTH holder — and the one §0 was about — does not live in gui/: it is
    kicadstamp/field_override_adapter.py's FieldOverrideAdapter._store. The AST
    scan above cannot see it, so its two halves are pinned here by hand:
    a readable bound store (whose .path is what gets re-read) and bind_store to
    install the fresh copy without touching the socket."""
    assert isinstance(FieldOverrideAdapter.store, property)
    assert callable(FieldOverrideAdapter.bind_store)
    assert isinstance(BoardConnection.reload_store.__doc__, str)
    assert "bind_store" in BoardConnection.reload_store.__doc__


# ── the seam itself: a bare connection has nothing to reload ─────────────

def test_reload_store_is_a_no_op_without_a_board_or_a_layer():
    """BoardConnection.reload_store on its own — the same silent shapes as С4,
    without an event in the middle."""
    BoardConnection().reload_store()              # never connected
    bare = BoardConnection()
    bare.board = SimpleNamespace(adapter=SimpleNamespace())
    bare.reload_store()                           # an adapter with no layer
    assert CLUSTER_FIELD_NAME  # (imported for the store's field set, kept honest)

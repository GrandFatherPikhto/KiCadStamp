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

import pytest

from gui.connection import BoardConnection
from gui.dock_hub import DockHub
from kicadstamp.adapter_factory import store_for_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import (SOURCE_CELL_TABLE, SOURCE_CLI,
                                        load_field_overrides)
from kicadstamp.utils.paths import overrides_path_for_config
from tests.override_store_board_fixtures import (
    FOOTPRINT_REF, ON_BOARD_ROLE, SYMBOL_UUID, profile_file, wired_board)

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
        # The OTHER door to a board (kicadstamp/kicad/adapter.py:545 — it drops
        # the whole field-map generation), counted on its own since 2026-09-24:
        # the split С3 has to be able to say WHICH of the two doors stayed shut.
        self.refresh_board_calls = 0

    def get_field_value(self, footprint, field):
        self.reads += 1
        return self._fields.get((footprint.ref, field))

    def refresh_board(self) -> None:
        self.refresh_board_calls += 1


class _Board:
    """The `board` half of a connection: an adapter, and a SPY for every door a
    real Board offers to the socket.

    Recording rather than raising, deliberately: DockHub._safe_call swallows
    whatever a reload throws, so an exception-based spy would let a guard pass
    on a technicality (the log would carry the traceback, the test would not).

    `select` and `refresh` are counted SEPARATELY (2026-09-24). One `calls` list
    could not tell them apart, and that is exactly how the old С3 ended up pinning
    two different rules under one name: "the reload rebuilt the snapshot" and "the
    reload re-read the board" are answers to two different questions, and only one
    of them was ever a defect."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.selects = 0
        self.refreshes = 0
        self.forgets = 0

    def select(self, *args, **kwargs):
        self.selects += 1
        return []

    def refresh(self, *args, **kwargs):
        self.refreshes += 1

    def forget_role_cluster_values(self) -> None:
        """The capability BoardConnection asks for before it reprojects (§3.4 of
        plan_2026_09_24_reload_store_snapshot): `override_reprojection_supported`
        is getattr/callable on THIS name, so a stand-in without it is a board that
        simply cannot reproject — and the reload must then stay silent rather than
        raise. Given here, empty of meaning: what the real one drops is the four
        role/cluster caches, which this file's guards never fill."""
        self.forgets += 1


class _Holder:
    """One GUI pane holding a copy of the store, reduced to the single thing the
    event asks of it."""

    def __init__(self):
        self.reloads = 0

    def reload_overrides(self) -> None:
        self.reloads += 1


class _DistributionSpy:
    """The two stops the write event reaches on its way OUT of the reload (§4 of
    plan_2026_09_24_reload_store_snapshot): the Role/Cluster known-value lists and
    the Components tree's rows.

    Recorders, not dock stand-ins: what a cell asks is WHICH snapshot was handed
    over and WHEN. The real docks' own reaction is
    tests/gui/test_overrides_store_reload_gui.py's business.

    They exist on the stub for a mechanical reason as well (§5.1в): without them
    `_safe_call` would swallow an AttributeError and log `GUI: ... failed`, so С4
    would turn red for a technical reason instead of a behavioural one."""

    def __init__(self):
        self.lists = []     # every push_known_lists(snapshot) argument
        self.rows = []      # every tree_dock.set_footprints(snapshot) argument
        self.order = []      # "reload" / "lists" / "rows", in the order called


def _event_hub(connection, window=None, cell_editor=None, imprint_page=None):
    """A DockHub-shaped stub the REAL _on_overrides_written runs against.

    SIX attributes are all that method reaches — the fieldstool window, the cell
    editor, the imprint page's Roles tab (2026-09-20, Д2), the poll adapter's
    reload seam, and (since plan_2026_09_24_reload_store_snapshot §4) the two
    distribution stops — so no Qt dock (and no QApplication) is needed to
    exercise it. The stub carries its own recorders on `hub.spy`.

    The reload seam is wrapped, not replaced: the real connection.reload_store
    still runs, and only its POSITION in the order is recorded — that is the
    "after the rebind, not before" contract of push_known_lists."""
    spy = _DistributionSpy()
    reload_store = getattr(connection, "reload_store", None)

    def _reload():
        spy.order.append("reload")
        if reload_store is not None:
            reload_store()

    def _push_known_lists(snapshot):
        spy.order.append("lists")
        spy.lists.append(snapshot)

    def _set_footprints(selected):
        spy.order.append("rows")
        spy.rows.append(selected)

    hub = SimpleNamespace(
        fieldstool_dock=SimpleNamespace(
            window=window if window is not None else _Holder()),
        cell_anchor_view=cell_editor if cell_editor is not None else _Holder(),
        imprint_dock=imprint_page if imprint_page is not None else _Holder(),
        _connection=connection,
        _reload_poll_store=_reload if reload_store is not None else None,
        push_known_lists=_push_known_lists,
        tree_dock=SimpleNamespace(set_footprints=_set_footprints),
        spy=spy)
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


# ── С3 (split in two, 2026-09-24): a FILE operation, and only that (door §2) ──

def test_a_store_reload_never_touches_the_socket(tmp_path):
    """С3 of `plan_2026_09_19_poll_adapter_store_reload` — the SOCKET half, as it
    stands after the split of 2026-09-24.

    HISTORY, because the old name is gone on purpose (rule 33: a guard is never
    deleted silently — here it was not deleted at all, only separated). It used to
    be ONE test, `test_c3_the_reload_never_touches_the_board_or_the_socket`, which
    pinned two different rules under one name:

      * "the reload does not spend a socket round-trip" — true, kept here for ever;
      * "the reload does not rebuild the snapshot" — the defect fixed by
        `plan_2026_09_24_reload_store_snapshot` (a role recorded in the fieldstool
        reached the pickers only after a manual Refresh).

    The old justification for the second half was a SUBSTITUTION — "rebuilding the
    snapshot would cost a round-trip". True of `Board.refresh()`, FALSE of the
    reprojection, which re-reads footprints the Board already holds (§2.3 of that
    plan; the price is measured in Н3 there).

    Denis gave his explicit word on 2026-09-24 that the guard is SPLIT rather than
    weakened, so nothing is loosened: both halves are now checked harder than the
    single old one, because `_Board` can tell `select` from `refresh`."""
    connection, adapter, inner, board, profile = _bound_connection(tmp_path)

    other = _store_of(profile)
    other.set(UUID_R1, "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CLI)
    other.save()

    _event_hub(connection)._on_overrides_written()

    assert adapter.store.get(UUID_R1, ROLE_FIELD_NAME) == "R_WRITTEN"  # it did work
    assert connection.long_op_active is False, (
        "the reload started a long op: the shared socket has ONE owner, and a "
        "store reload is not it (door rule 3, via start_long_op)")
    assert board.refreshes == 0, (
        "the reload called Board.refresh() — a full IPC re-read of a board "
        "nobody changed")
    assert inner.refresh_board_calls == 0, (
        "the reload dropped the adapter's whole field-map generation, the OTHER "
        "door to the board (kicadstamp/kicad/adapter.py:545)")
    assert inner.reads == 0, "the reload read a field off the board"


def test_a_store_reload_reprojects_the_snapshot_without_a_board_read(tmp_path):
    """С3 of `plan_2026_09_19_poll_adapter_store_reload` — the SNAPSHOT half: the
    assertion that replaced the old `assert connection.snapshot_version ==
    before_version` (it lived under the same name as the socket half above, and
    the reasoning for the split is written out there in full).

    What it pins: the reload DOES rebuild the snapshot — exactly one
    `Board.select()` — and pays for that with no board door at all. The two halves
    are complementary, so a build that satisfies one by breaking the other is
    caught by the other one here."""
    connection, adapter, inner, board, profile = _bound_connection(tmp_path)
    before_version = connection.snapshot_version
    # The DELTA, not the count: binding the store (`_bound_connection` opens the
    # project) reprojects too when the board can — that is §3.3, and a guard
    # asserting an absolute 1 would be measuring the fixture's own setup.
    selects_before = board.selects

    other = _store_of(profile)
    other.set(UUID_R1, "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CLI)
    other.save()

    _event_hub(connection)._on_overrides_written()

    assert connection.snapshot_version == before_version + 1, (
        "the reload did NOT rebuild the board snapshot: the value it just "
        "re-read stays invisible to every picker and to the Components tree "
        "until the user presses Refresh — the live symptom of 2026-09-24")
    assert board.selects == selects_before + 1, (
        "the reload rebuilt the snapshot {n} time(s) instead of once: one select "
        "is what a reprojection is".format(n=board.selects - selects_before))
    assert board.refreshes == 0 and inner.refresh_board_calls == 0, (
        "the reprojection opened a door to the board: it reads the footprints "
        "the Board already holds, and nothing else")


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


# ── Н1..Н8: a write into the store must reach the SNAPSHOT (2026-09-24) ────
#
# `plan_2026_09_24_reload_store_snapshot`. The hole is the one BETWEEN two
# already-guarded places: С1 (the adapter serves the new value) and С25/С27 (a
# snapshot built from that adapter shows the store's values). What nobody covered
# is the snapshot the CONNECTION hands over AFTER a write: it is built once, at
# connect(), and the automatic poll tick is a deliberate no-op
# (gui/main_window.py:983), so a role recorded in the fieldstool reached the
# pickers only after a manual Refresh — the live symptom of §1.
#
# Numbering follows rule 37: the Н-number lives in the docstring TOGETHER with the
# plan's name, while the test's own name says the PROPERTY (a bare "Н3" addresses
# several families in this project).

#: The four `Board` caches a reprojection must leave alone (§3.1's table), named as
#: data so every row of Н4 reports its own failure (rule 35) — and so a later
#: decision to narrow the method cannot quietly pick the pair it must not pick
#: (§5.3а: dropping only the PHYSICAL pair raises KeyError in _board_role).
CACHES_THE_STORE_DOES_NOT_TOUCH = [
    "_role_exists_cache", "_cluster_exists_cache", "_nets_cache", "_sheet_cache",
]


def test_a_store_write_reaches_the_handed_over_snapshot(tmp_path):
    """**Н1** of `plan_2026_09_24_reload_store_snapshot` — the main cell, RED on
    the base commit.

    The store is bound EMPTY (the project opened), another holder records a role
    and saves (its OWN store object over the same file — the shape the Refs table
    writes through), the write event fires — and `connection.snapshot` must serve
    the NEW role. The SNAPSHOT, not `adapter.get_field_value`: that is С1's
    subject, and this hole lies exactly between the two.

    Measured on a COLD board (no snapshot was built before the write) on purpose:
    this cell asks "does the reload rebuild the snapshot AT ALL". The other half of
    the same hole — a rebuild that happens against WARM caches and comes back with
    the previous role — is Н2, and the two must be able to fail separately."""
    wiring = wired_board(tmp_path)
    assert wiring.store is not None                 # bound, not layered empty
    assert wiring.store.get(SYMBOL_UUID, ROLE_FIELD_NAME) is None
    assert wiring.connection.snapshot == []         # cold: nothing built yet

    wiring.write_role("R_WRITTEN")
    _event_hub(wiring.connection)._on_overrides_written()

    assert wiring.role_in_snapshot() == "R_WRITTEN", (
        "the write reached the store but not the snapshot the docks are handed: "
        "the pickers and the Components tree keep showing the board's roles, "
        "which is the live symptom of §1")


def test_a_warm_snapshot_comes_back_with_the_new_role_after_a_write(tmp_path):
    """**Н2** of `plan_2026_09_24_reload_store_snapshot` — the cell on §2.2, and
    the one that makes the NAIVE fix visible.

    "Call `_rebuild_snapshot()` in `reload_store()`" looks right and reports green:
    `Board.select()` walks the footprints it already holds and answers every
    Role/Cluster read out of its own warm `_role_cache`/`_cluster_cache`, so the
    snapshot gets a new version and keeps the OLD roles. This is the production
    shape — the poll built the snapshot at connect() — so the cell builds it the
    same way, writes, fires the event and asks for the role."""
    wiring = wired_board(tmp_path)
    wiring.connection._rebuild_snapshot()           # the poll built it at connect
    assert wiring.role_in_snapshot() == ON_BOARD_ROLE

    wiring.write_role("R_WRITTEN")
    _event_hub(wiring.connection)._on_overrides_written()

    selected = wiring.selected()
    assert selected is not None, (
        "the snapshot came back without the footprint at all: the rebuild "
        "replaced it with something else")
    assert selected.role == "R_WRITTEN", (
        "the snapshot was rebuilt against WARM Board caches: the store's value "
        "is in memory while the picture every picker reads stays the previous "
        "one — the silent miss §2.2 warns about")
    assert selected.board_role == ON_BOARD_ROLE, (
        "the PHYSICAL side of the same read must stay the board's: the store "
        "changes the value IN FORCE, not what lies on the board (С27)")


@pytest.mark.parametrize("door", ["refresh_board", "get_footprints", "field_scan",
                                  "get_footprint_pads"])
def test_the_reprojection_opens_no_board_door(tmp_path, door):
    """**Н3** of `plan_2026_09_24_reload_store_snapshot` — the price: the claim
    §5.1(б) took away from С5's vacuous `snapshot_version` assertion.

    The rows are the four doors of the BOARD, counted BELOW `FieldOverrideAdapter`
    — where the adapter's own `_field_values_cache` lives. Where the number is
    taken matters (Кk, 2026-09-22): counting calls on the wrapper, or on anything
    above the field map, counts the cache HITS that make this cheap and reports a
    figure contradicting the promise. `field_scan` is the row a naive instrument
    gets wrong in the other direction: on a warm adapter the per-footprint scan is
    not repeated at all, and that is what "free" means here."""
    wiring = wired_board(tmp_path)
    wiring.connection._rebuild_snapshot()           # warm, like the production path
    before = wiring.wire.counts()
    version_before = wiring.connection.snapshot_version

    wiring.write_role("R_WRITTEN")
    _event_hub(wiring.connection)._on_overrides_written()

    after = wiring.wire.counts()
    assert after[door] == before[door], (
        "the store write opened the board's {door} door: {before} → {after}. The "
        "reload is a FILE operation plus an in-memory reprojection, and a socket "
        "round-trip here is what request_refresh's docstring forbids"
        .format(door=door, before=before, after=after))
    assert wiring.connection.snapshot_version == version_before + 1, (
        "the snapshot was NOT rebuilt, so the row above is measuring a no-op "
        "rather than a cheap reprojection")
    assert wiring.role_in_snapshot() == "R_WRITTEN"
    assert wiring.connection.long_op_active is False, (
        "a long op was started: the shared socket's owner changed, which is the "
        "one thing a store reload must not do")


#: For each Board cache the store cannot make stale — the QUESTION Board asks when
#: that cache is cold, and therefore the counter that can tell "the cache was
#: dropped" from "the cache was refilled". Counting the cache DICT is not enough,
#: and this is measured, not theorised: Н4 written against the dicts stayed GREEN
#: under a mutation that cleared all eight caches, because rebuilding the snapshot
#: refills them anyway (mutation run, 2026-09-24).
CACHE_QUESTIONS = {
    "_role_exists_cache": "has_field",
    "_cluster_exists_cache": "has_field",
    "_nets_cache": "get_footprint_pads",
    "_sheet_cache": "resolve_sheet_path_names",
}


@pytest.mark.parametrize("cache", CACHES_THE_STORE_DOES_NOT_TOUCH)
def test_the_reprojection_does_not_make_the_board_ask_again(tmp_path, monkeypatch,
                                                            cache):
    """**Н4** of `plan_2026_09_24_reload_store_snapshot` — the caches the store
    cannot make stale, measured as QUESTIONS.

    Role/Cluster VALUES move; nets, sheet chains and the "the field exists AT ALL"
    facts do not depend on the store at all (`FieldOverrideAdapter.has_field`
    answers about the BOARD on purpose —
    kicadstamp/field_override_adapter.py:101), so dropping them would buy a
    re-scan — and, for nets, a `Pad` object per footprint — to learn nothing new.

    WHY A COUNTER, and why it sits where it sits: the property is "the Board did
    not have to ask again", and a rebuild refills a dropped cache anyway — so
    reading the cache dict reports health after ANY reprojection and hides
    "dropped everything at once" (that is exactly how this row was first written,
    and how it stayed green under м3). Each row therefore counts its own cache's
    question one layer BELOW that cache: the adapter's counted face for
    `has_field`/`get_footprint_pads`, and the module's own sheet resolver for the
    chain — the one of the four with no adapter call to count."""
    wiring = wired_board(tmp_path)
    wiring.connection._rebuild_snapshot()           # warm: every cache filled
    assert getattr(wiring.board, cache), (
        "the fixture is not warm to begin with: {cache} is empty after a "
        "snapshot".format(cache=cache))

    question = CACHE_QUESTIONS[cache]
    if question == "resolve_sheet_path_names":
        import kicadstamp.explore as explore_mod
        real_resolve = explore_mod.resolve_sheet_path_names

        def _counting_resolve(footprint, sheet_names, _real=real_resolve):
            wiring.asks["resolve_sheet_path_names"] += 1
            return _real(footprint, sheet_names)

        monkeypatch.setattr(explore_mod, "resolve_sheet_path_names",
                            _counting_resolve)

    before = wiring.asks[question]

    wiring.write_role("R_WRITTEN")
    _event_hub(wiring.connection)._on_overrides_written()

    after = wiring.asks[question]
    assert after == before, (
        "the reprojection dropped {cache}, so the Board had to ask the board side "
        "again ({question}: {before} → {after}) — the four role/cluster caches are "
        "the ONLY ones the store can make stale (§3.1)".format(
            cache=cache, question=question, before=before, after=after))


def test_a_project_switch_reprojects_the_snapshot_too(tmp_path):
    """**Н5** of `plan_2026_09_24_reload_store_snapshot` — the same property on
    the class's OTHER entry point (§3.3): a project switch rebinds the store
    exactly like a write does, so the snapshot the docks read must follow it.

    The cell bumps the snapshot through a plain rebuild FIRST, so a red here means
    "the snapshot is STALE" rather than "there is no snapshot" — telling those two
    apart is Н1's job."""
    wiring = wired_board(tmp_path, name="a.sexp", bind=False)
    wiring.write_role("ROLE_A")                     # profile А's store, saved
    wiring.connection.set_project_config(wiring.profile)   # the project opens
    wiring.connection._rebuild_snapshot()                  # the poll built it
    assert wiring.role_in_snapshot() == "ROLE_A"

    profile_b = profile_file(tmp_path, name="b.sexp")
    holder_b = load_field_overrides(overrides_path_for_config(str(profile_b)))
    holder_b.set(SYMBOL_UUID, FOOTPRINT_REF, ROLE_FIELD_NAME, "ROLE_B",
                 SOURCE_CELL_TABLE)
    holder_b.save()

    wiring.connection.set_project_config(profile_b)

    assert wiring.role_in_snapshot() == "ROLE_B", (
        "the connection switched profiles but the snapshot still answers with "
        "profile А's roles: the GUI shows one project's values while the store in "
        "force is another's")


def test_a_board_that_cannot_reproject_makes_the_reload_a_silent_no_op(
        tmp_path, caplog):
    """**Н6** of `plan_2026_09_24_reload_store_snapshot` — the DIRECT entry point
    half of the cell; the event-shaped half is С5's own assertion in
    tests/gui/test_overrides_store_reload_gui.py, whose docstring says out loud WHY
    it is green.

    A board that cannot reproject (no `select`, no forget-method) is a LEGITIMATE
    case — tests' stand-ins, a bare adapter on a bench — not an error: there is
    simply nothing to reproject, so the reload does its file work and stops.

    GREEN HERE MEANS "the board cannot", never "the reload does not rebuild the
    snapshot" (§3.4). The capability is asked for with getattr/callable rather than
    a try/except around the whole thing, and the difference is visible: an
    unguarded call raises, `_safe_call` catches it on the event path, and a
    `GUI: ... failed` ERROR lands in the Log on every single write."""
    caplog.set_level("DEBUG")
    wiring = wired_board(tmp_path, bind=False)
    adapter = FieldOverrideAdapter(SimpleNamespace())
    connection = BoardConnection()
    connection.board = SimpleNamespace(adapter=adapter)
    store, source = store_for_config(str(wiring.profile))
    adapter.bind_store(store, source=source)

    before_version = connection.snapshot_version
    before_snapshot = list(connection.snapshot)

    connection.reload_store()                       # (a) must simply not raise
    _event_hub(connection)._on_overrides_written()  # (b) ... and stay silent

    assert connection.snapshot_version == before_version, (
        "a reload that had nothing to reproject still touched the snapshot")
    assert list(connection.snapshot) == before_snapshot
    failures = [record for record in caplog.records
                if record.levelno >= 40 and "GUI:" in record.getMessage()]
    assert failures == [], (
        "the reload raised on a board that cannot reproject and the guard "
        "swallowed it: {messages}".format(
            messages=[r.getMessage() for r in failures]))


def test_the_write_event_hands_the_fresh_snapshot_to_the_docks(tmp_path):
    """**Н7** of `plan_2026_09_24_reload_store_snapshot` — the distribution (§4).
    A rebuilt snapshot is INVISIBLE on its own: its only consumer is
    `MainWindow._finish_poll`, which does not run on this path.

    Ordering is proven by CONTENT, not by timestamps: the snapshot the docks are
    handed must already carry the new role, which is only possible if the
    reprojection happened first. The recorded order says the same thing once, from
    the one place that sees all three steps."""
    wiring = wired_board(tmp_path)
    wiring.connection._rebuild_snapshot()           # the poll built it at connect
    wiring.write_role("R_WRITTEN")

    hub = _event_hub(wiring.connection)
    hub._on_overrides_written()

    assert hub.spy.order == ["reload", "lists", "rows"], (
        "the distribution did not happen after the rebind (or did not happen at "
        "all): {order}".format(order=hub.spy.order))
    assert len(hub.spy.lists) == 1, "push_known_lists was not called exactly once"
    assert len(hub.spy.rows) == 1, (
        "tree_dock.set_footprints was not called: the Components tree keeps the "
        "rows it built before the write")

    def roles(selected):
        return {s.ref: s.role for s in selected}

    assert roles(hub.spy.rows[0]).get(FOOTPRINT_REF) == "R_WRITTEN", (
        "the Components tree was handed a snapshot that does not carry the value "
        "just recorded: {rows}".format(rows=hub.spy.rows[0]))
    assert roles(hub.spy.lists[0]).get(FOOTPRINT_REF) == "R_WRITTEN", (
        "the known-value lists were distributed from the pre-write snapshot: "
        "{lists}".format(lists=hub.spy.lists[0]))


def _event_body():
    """The AST of `DockHub._on_overrides_written` — the same reader the С5-half
    guards further up use inline (parse the module, walk it, find the function by
    name), kept as a helper so Н8 cannot drift from them."""
    tree = ast.parse(HUB_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_overrides_written":
            return node
    raise AssertionError("_on_overrides_written is gone from gui/dock_hub.py")


def test_the_write_event_never_falls_into_a_board_refresh():
    """**Н8** of `plan_2026_09_24_reload_store_snapshot` — the NEGATIVE half of
    §4, read from the source rather than through behaviour: the distribution must
    not be `refresh_snapshot_and_push` (a full IPC re-read on a worker) and must
    not be `push_snapshot` (which wants the net-name lists this path does not have
    and would empty four net combos).

    By AST, not by grepping the file: a name surviving only in a docstring is not
    a call, and the prohibition pinned here is stated verbatim in
    `request_refresh`'s docstring (gui/main_window.py:963-967)."""
    names = _dotted_names(_event_body())
    for forbidden in ("self.refresh_snapshot_and_push", "self.push_snapshot",
                      "self._connection.refresh"):
        assert forbidden not in names, (
            "the write event reaches {forbidden}: a store write must not spend a "
            "socket round-trip on a board nobody changed".format(forbidden=forbidden))

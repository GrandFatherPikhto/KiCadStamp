# tests/test_adapter_factory.py
"""Guards for the field-override layer and the adapter factory — Т2 of
plan_2026_09_18_field_overrides_store.md (design
design_2026_09_18_field_overrides_store.md).

The layer is where the store's priority actually happens:

    the effective value of a component's Role/Cluster = our stored value when we
    have one, the board's value otherwise — OUR value wins ALWAYS.

Guards pinned here (no kipy, no Qt — the inner adapter is a fake):

  * С1 — our value wins over a DIFFERENT value on the board, for Role AND
    Cluster, and `has_field` still answers about the BOARD;
  * С2 (golden, structural) — an EMPTY store is not layered at all, and the
    "board" switch is not layered either: the adapter is then the very object the
    pre-store code built, so "empty store == old behaviour" cannot drift;
  * С6 — no file I/O inside the resolution: a lookup is a dictionary hit;
  * С7 — the switch in the profile decides, and `use_store=False` is the NAMED
    bare mode (a probe about the physical board);
  * С9/В2 — a field other than Role/Cluster is never even ASKED of the store;
  * С12's read-side twin — a footprint without a symbol uuid falls back to the
    board instead of guessing a key;
  * С15 — the factory is the ONLY place an adapter is created in the shipping
    code (structural ast scan; `kicadstamp/diagnostics/` and `tools/` join this
    guard in step Т2а);
  * the summary: ONE Log line per adapter lifetime, and only when the store
    actually served something.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

from kicadstamp.adapter_factory import (create_board_adapter,
                                        role_cluster_source_for_config)
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import SOURCE_FIELDSTOOL, FieldOverrides
from kicadstamp.utils.paths import overrides_path_for_config

UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"
ROOT = Path(__file__).resolve().parents[1]


# ── a fake board: the ONLY thing the layer is allowed to talk to ───────────

class _Path:
    def __init__(self, uuids):
        self.path = [type("U", (), {"value": u})() for u in uuids]


class _FakeFootprint:
    def __init__(self, ref, uuid=None, role=None, cluster=None, value=None):
        self.ref = ref
        self.uuid = "fp-" + ref
        if uuid is not None:
            self.sheet_path = _Path([uuid])
        self._fields = {}
        if role is not None:
            self._fields[ROLE_FIELD_NAME] = role
        if cluster is not None:
            self._fields[CLUSTER_FIELD_NAME] = cluster
        if value is not None:
            self._fields["Value"] = value


class _FakeAdapter:
    """Just enough of the seam: values, field presence, a close, and a spy on
    what was asked."""

    def __init__(self):
        self.asked: list[str] = []
        self.closed = False

    def get_field_value(self, footprint, field_name):
        self.asked.append(field_name)
        return footprint._fields.get(field_name)

    def has_field(self, footprint, field_name):
        return field_name in footprint._fields

    def close(self):
        self.closed = True


def _store(*records, path=None) -> FieldOverrides:
    """A store with (symbol uuid, field, value) records — built through the
    public API, so no test can create a shape the store itself would refuse."""
    store = FieldOverrides(path)
    for symbol_uuid, field, value in records:
        store.set(symbol_uuid, "REF", field, value, SOURCE_FIELDSTOOL)
    return store


def _write_store(profile_path) -> Path:
    """Write the store file the factory will look for next to `profile_path`."""
    overrides = Path(overrides_path_for_config(str(profile_path)))
    _store((UUID_A, ROLE_FIELD_NAME, "R_FB"), path=overrides).save()
    return overrides


def _profile(tmp_path, role_cluster_source=None) -> Path:
    from kicadstamp.config.sexp_format import dict_to_sexp
    data = {"layer": "B.Cu"}
    if role_cluster_source is not None:
        data["role_cluster_source"] = role_cluster_source
    path = tmp_path / "profile.sexp"
    path.write_text(dict_to_sexp(data), encoding="utf-8")
    return path


def _fake_factory(monkeypatch) -> None:
    """Every factory test runs against the fake seam, never kipy."""
    monkeypatch.setattr("kicadstamp.kicad.adapter.KiCadBoardAdapter",
                        lambda timeout_ms=None: _FakeAdapter())


# ── С1: our value wins, always ─────────────────────────────────────────────

def test_c1_our_value_wins_over_a_different_board_value():
    board = _FakeAdapter()
    store = _store((UUID_A, ROLE_FIELD_NAME, "R_FB"),
                   (UUID_A, CLUSTER_FIELD_NAME, "OUR_BANK"))
    fp = _FakeFootprint("R1", uuid=UUID_A, role="R_IN", cluster="BOARD_BANK")

    layer = FieldOverrideAdapter(board, store)

    assert layer.get_field_value(fp, ROLE_FIELD_NAME) == "R_FB"
    assert layer.get_field_value(fp, CLUSTER_FIELD_NAME) == "OUR_BANK"


def test_c1_a_component_we_have_no_value_for_keeps_the_board_value():
    board = _FakeAdapter()
    store = _store((UUID_A, ROLE_FIELD_NAME, "R_FB"))
    other = _FakeFootprint("R2", uuid=UUID_B, role="R_OTHER")

    layer = FieldOverrideAdapter(board, store)

    assert layer.get_field_value(other, ROLE_FIELD_NAME) == "R_OTHER"


def test_has_field_still_answers_about_the_board():
    """В5: the field's EXISTENCE is a board fact (Pending changes compares the
    two sides on it), so the layer must not pretend our value creates one."""
    board = _FakeAdapter()
    store = _store((UUID_A, ROLE_FIELD_NAME, "R_FB"))
    fp = _FakeFootprint("R1", uuid=UUID_A)  # no Role field on the footprint

    layer = FieldOverrideAdapter(board, store)

    assert layer.get_field_value(fp, ROLE_FIELD_NAME) == "R_FB"
    assert layer.has_field(fp, ROLE_FIELD_NAME) is False


def test_a_footprint_without_a_symbol_uuid_falls_back_to_the_board():
    board = _FakeAdapter()
    store = _store((UUID_A, ROLE_FIELD_NAME, "R_FB"))
    fp = _FakeFootprint("R1", uuid=None, role="R_IN")  # no sheet_path at all

    layer = FieldOverrideAdapter(board, store)

    assert layer.get_field_value(fp, ROLE_FIELD_NAME) == "R_IN"


# ── С9/В2: other fields never reach the store ─────────────────────────────

def test_c9_another_field_is_never_asked_of_the_store():
    board = _FakeAdapter()
    asked: list[tuple] = []

    class RogueStore(FieldOverrides):
        def get(self, symbol_uuid, field):
            asked.append((symbol_uuid, field))
            return "10k"       # would corrupt the BOM if it were believed

    fp = _FakeFootprint("R1", uuid=UUID_A, value="4k7")
    layer = FieldOverrideAdapter(board, RogueStore())

    assert layer.get_field_value(fp, "Value") == "4k7"
    assert asked == []
    # ... and the same read for Role DOES consult it (the layer is alive).
    layer.get_field_value(fp, ROLE_FIELD_NAME)
    assert asked == [(UUID_A, ROLE_FIELD_NAME)]


# ── С6: a lookup does no file I/O ─────────────────────────────────────────

def test_c6_a_lookup_does_no_file_io(monkeypatch):
    board = _FakeAdapter()
    store = _store((UUID_A, ROLE_FIELD_NAME, "R_FB"))
    layer = FieldOverrideAdapter(board, store)
    fp = _FakeFootprint("R1", uuid=UUID_A, role="R_IN")

    opened: list = []
    real_open = open

    def _spy_open(*args, **kwargs):
        opened.append(args[0] if args else None)
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", _spy_open)
    for _ in range(50):
        assert layer.get_field_value(fp, ROLE_FIELD_NAME) == "R_FB"

    assert opened == []


# ── the summary line ──────────────────────────────────────────────────────

def test_one_summary_line_per_lifetime_and_only_when_the_store_served(caplog):
    board = _FakeAdapter()
    store = _store((UUID_A, ROLE_FIELD_NAME, "R_FB"))
    layer = FieldOverrideAdapter(board, store)
    fp = _FakeFootprint("R1", uuid=UUID_A, role="R_IN")

    layer.get_field_value(fp, ROLE_FIELD_NAME)
    layer.get_field_value(fp, ROLE_FIELD_NAME)
    with caplog.at_level(logging.INFO):
        layer.close()

    lines = [r.message for r in caplog.records if "override store" in r.message]
    assert len(lines) == 1
    assert "2" in lines[0]
    assert board.closed is True


def test_no_summary_line_when_the_store_served_nothing(caplog):
    board = _FakeAdapter()
    layer = FieldOverrideAdapter(board, FieldOverrides())

    with caplog.at_level(logging.INFO):
        layer.close()

    assert not [r for r in caplog.records if "override store" in r.message]


# ── bind_store: one adapter, many profiles (the MCP case) ─────────────────

def test_bind_store_switches_which_profile_is_in_force():
    board = _FakeAdapter()
    fp = _FakeFootprint("R1", uuid=UUID_A, role="R_IN")
    layer = FieldOverrideAdapter(board, None)
    assert layer.get_field_value(fp, ROLE_FIELD_NAME) == "R_IN"

    layer.bind_store(_store((UUID_A, ROLE_FIELD_NAME, "R_FB")))
    assert layer.get_field_value(fp, ROLE_FIELD_NAME) == "R_FB"

    layer.bind_store(None)
    assert layer.get_field_value(fp, ROLE_FIELD_NAME) == "R_IN"


# ── С7/С2: what the FACTORY returns ───────────────────────────────────────

def test_c2_an_empty_store_is_not_layered(monkeypatch, tmp_path):
    """The golden property made STRUCTURAL: with nothing stored, the factory
    hands back the plain adapter object — the very thing the pre-store code
    built — so "an empty store changes nothing" cannot drift."""
    _fake_factory(monkeypatch)

    adapter = create_board_adapter(config_path=str(_profile(tmp_path)))

    assert not isinstance(adapter, FieldOverrideAdapter)


def test_c7_the_board_switch_is_not_layered_even_with_records(monkeypatch, tmp_path):
    _fake_factory(monkeypatch)
    profile = _profile(tmp_path, role_cluster_source="board")
    _write_store(profile)

    adapter = create_board_adapter(config_path=str(profile))

    assert not isinstance(adapter, FieldOverrideAdapter)


def test_c7_a_store_with_records_is_layered(monkeypatch, tmp_path):
    _fake_factory(monkeypatch)
    profile = _profile(tmp_path)
    _write_store(profile)

    adapter = create_board_adapter(config_path=str(profile))

    assert isinstance(adapter, FieldOverrideAdapter)
    assert adapter.get_field_value(
        _FakeFootprint("R1", uuid=UUID_A, role="R_IN"), ROLE_FIELD_NAME) == "R_FB"


def test_the_named_bare_mode_ignores_the_profile(monkeypatch, tmp_path):
    _fake_factory(monkeypatch)
    profile = _profile(tmp_path)
    _write_store(profile)

    adapter = create_board_adapter(config_path=str(profile), use_store=False)

    assert not isinstance(adapter, FieldOverrideAdapter)


def test_use_store_true_layers_even_an_empty_store(monkeypatch):
    """The MCP case: the layer must EXIST so that bind_store can install the
    store of the current call."""
    _fake_factory(monkeypatch)

    adapter = create_board_adapter(use_store=True)

    assert isinstance(adapter, FieldOverrideAdapter)
    assert adapter.store is None


def test_the_switch_is_read_from_the_profile_not_from_gui_state(tmp_path):
    """С18's core: the switch is the PROFILE's, so a GUI-state value (which the
    CLI cannot see) must have no effect on it."""
    from gui import settings
    settings.state.set("role_cluster_source", "board")
    try:
        assert role_cluster_source_for_config(str(_profile(tmp_path))) == "registry"
        assert role_cluster_source_for_config(
            str(_profile(tmp_path, role_cluster_source="board"))) == "board"
    finally:
        settings.state.set("role_cluster_source", None)


def test_an_unknown_switch_reads_as_the_default_with_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        value = role_cluster_source_for_config(
            str(_profile(tmp_path, role_cluster_source="registryy")))

    assert value == "registry"
    assert any("Unknown role_cluster_source" in r.message for r in caplog.records)


def test_a_missing_profile_reads_as_the_default(tmp_path):
    assert role_cluster_source_for_config(str(tmp_path / "absent.sexp")) == "registry"
    assert role_cluster_source_for_config(None) == "registry"


# ── С15: the factory is the only place an adapter is born ─────────────────

def _adapter_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "KiCadBoardAdapter"):
            lines.append(node.lineno)
    return lines


def test_c15_shipping_code_creates_adapters_only_through_the_factory():
    """The structural scan. A direct `KiCadBoardAdapter(...)` outside the factory
    is exactly the regression this guard exists for: one missed call site and
    part of the resolution silently ignores the store.

    Scope: the SHIPPING code (kicadstamp/, gui/, mcp_server/). The 27 probes of
    kicadstamp/diagnostics/ and the two of tools/ join in step Т2а; the
    gitignored root ./diagnostics/ is deliberately out of scope (it is Denis's
    own sandbox, a different set of files on each machine)."""
    factory = ROOT / "kicadstamp" / "adapter_factory.py"
    offenders: list[str] = []
    for base in ("kicadstamp", "gui", "mcp_server"):
        for path in (ROOT / base).rglob("*.py"):
            if path == factory:
                continue          # the ONE place: it must call the real class
            if "diagnostics" in path.parts:
                continue          # step Т2а
            for line in _adapter_calls(path):
                offenders.append(f"{path.relative_to(ROOT)}:{line}")

    assert offenders == [], (
        "adapters must be created through kicadstamp.adapter_factory."
        f"create_board_adapter: {offenders}")

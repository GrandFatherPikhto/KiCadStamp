# tests/test_overrides_golden.py
"""Т7 (plan_2026_09_18_field_overrides_store): the store changes NOTHING where it
has nothing to say — and the golden guards that keep it that way.

С2 is the plan's own "golden" guard: with no store, everything must behave the way
it did before this feature existed, down to the resolved plan. С7 is its other
face: the profile's `role_cluster_source: board` switch returns that behaviour
COMPLETELY, records or no records. С18 and С19 finish the thought — one profile
must give ONE answer in the CLI, in the GUI and over MCP.

The "plan" measured here is the one the resolver actually acts on: for every
footprint, the Role/Cluster the adapter hands out. That is what a redraw, a clone
resolution and every picker walk away with, so comparing it byte-for-byte IS the
golden property — and comparing it is cheap, because the layer's whole contract
lives in `get_field_value`.

No board, no kipy: the factory's real adapter class is replaced by the fake seam
every factory test uses (tests/test_adapter_factory.py).
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicadstamp.adapter_factory import (create_board_adapter,
                                        role_cluster_source_for_config)
from kicadstamp.constants import (CLUSTER_FIELD_NAME, ROLE_FIELD_NAME,
                                  ROLE_CLUSTER_SOURCE_BOARD)
from kicadstamp.field_overrides import SOURCE_FIELDSTOOL, FieldOverrides
from kicadstamp.utils.paths import overrides_path_for_config

UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"

BOARD = {
    "R1": ("R_BOARD", "bank_board"),
    "R2": ("R_OTHER", None),
}


class _Board:
    """The lowest seam: values and field facts, nothing else — what the layer is
    allowed to talk to."""

    def __init__(self):
        self.asked: list[str] = []

    def get_field_value(self, footprint, field_name):
        self.asked.append(field_name)
        return footprint.fields.get(field_name)

    def has_field(self, footprint, field_name):
        return field_name in footprint.fields

    def refresh_board(self):
        pass

    def close(self):
        pass


def _install_board(monkeypatch) -> None:
    """The factory builds OUR fake instead of a real kipy adapter (the seam
    tests/test_adapter_factory.py uses)."""
    monkeypatch.setattr("kicadstamp.kicad.adapter.KiCadBoardAdapter",
                        lambda timeout_ms=None: _Board())


def _footprints():
    out = []
    for ref, (role, cluster) in BOARD.items():
        fields = {ROLE_FIELD_NAME: role}
        if cluster is not None:
            fields[CLUSTER_FIELD_NAME] = cluster
        out.append(SimpleNamespace(
            ref=ref, fields=fields,
            sheet_path=SimpleNamespace(
                path=[SimpleNamespace(value=f"uuid-{ref}")])))
    return out


def _plan(adapter) -> dict:
    """What the resolver would act on: Role AND Cluster per footprint."""
    return {fp.ref: (adapter.get_field_value(fp, ROLE_FIELD_NAME),
                     adapter.get_field_value(fp, CLUSTER_FIELD_NAME))
            for fp in _footprints()}


def _profile(tmp_path, name="profile.json", **keys) -> Path:
    import json
    path = tmp_path / name
    path.write_text(json.dumps(keys), encoding="utf-8")
    return path


def _write_store(profile, *records) -> Path:
    overrides = Path(overrides_path_for_config(str(profile)))
    store = FieldOverrides(overrides)
    for symbol_uuid, field, value in records:
        store.set(symbol_uuid, "R1", field, value, SOURCE_FIELDSTOOL)
    store.save()
    return overrides


# ── С2: no store, no change ───────────────────────────────────────────────

def test_c2_with_no_store_the_plan_is_the_pre_store_plan_byte_for_byte(
        monkeypatch, tmp_path):
    """The golden property, at the level that matters: with nothing stored, the
    layer hands out EXACTLY what the bare adapter does — same values, same
    absence of values, nothing rewritten.

    The bare adapter is asked for the same footprints through the NAMED bare mode
    (`use_store=False`), which is the pre-store world spelled out."""
    _install_board(monkeypatch)
    profile = _profile(tmp_path)

    bare = create_board_adapter(timeout_ms=1, use_store=False)
    through_profile = create_board_adapter(timeout_ms=1, config_path=str(profile))

    assert _plan(through_profile) == _plan(bare)


def test_c2_the_golden_guard_is_not_vacuous_records_do_change_the_plan(
        monkeypatch, tmp_path):
    """The negative that gives С2 its meaning: the SAME measurement with a record
    written must differ — otherwise the equality above would hold for a layer that
    never works at all."""
    _install_board(monkeypatch)
    profile = _profile(tmp_path)
    _write_store(profile, ("uuid-R1", ROLE_FIELD_NAME, "R_OURS"))

    bare = _plan(create_board_adapter(timeout_ms=1, use_store=False))
    layered = _plan(create_board_adapter(timeout_ms=1, config_path=str(profile)))

    assert bare["R1"] == ("R_BOARD", "bank_board")
    assert layered["R1"] == ("R_OURS", "bank_board")
    assert layered != bare


def test_c2_a_component_the_store_does_not_know_is_untouched(monkeypatch, tmp_path):
    """The golden property, per component: a record for R1 must not move R2 by so
    much as a field lookup (and R2's Cluster ABSENCE must stay an absence)."""
    _install_board(monkeypatch)
    profile = _profile(tmp_path)
    _write_store(profile, ("uuid-R1", ROLE_FIELD_NAME, "R_OURS"))

    assert _plan(create_board_adapter(timeout_ms=1, config_path=str(profile)))["R2"] \
        == _plan(create_board_adapter(timeout_ms=1, use_store=False))["R2"]


# ── С7: the switch returns the pre-store behaviour COMPLETELY ─────────────

def test_c7_the_board_switch_gives_the_pre_store_plan_with_records_lying_around(
        monkeypatch, tmp_path):
    """`role_cluster_source: board` means the records are NOT in force — so the
    plan must equal the bare one even though a record exists and even though it
    would have won. Leaving the layer switched on here would be the quietest
    possible lie: the switch says one thing and the resolver does another."""
    _install_board(monkeypatch)
    profile = _profile(tmp_path, role_cluster_source=ROLE_CLUSTER_SOURCE_BOARD)
    _write_store(profile, ("uuid-R1", ROLE_FIELD_NAME, "R_OURS"),
                 ("uuid-R1", CLUSTER_FIELD_NAME, "bank_ours"))

    bare = _plan(create_board_adapter(timeout_ms=1, use_store=False))
    switched = _plan(create_board_adapter(timeout_ms=1, config_path=str(profile)))

    assert switched == bare
    assert role_cluster_source_for_config(str(profile)) == ROLE_CLUSTER_SOURCE_BOARD


def test_c7_with_the_board_switch_the_store_file_is_not_even_read(
        monkeypatch, tmp_path):
    """The switch is not only "do not serve it" but "do not look": with the board
    as the source the factory returns before it opens anything. That matters on
    the hot paths (an adapter per cascade click, per apply run), and it is the
    observable half of the early return that the layer's own gate would otherwise
    make invisible."""
    import kicadstamp.adapter_factory as factory

    _install_board(monkeypatch)
    profile = _profile(tmp_path, role_cluster_source=ROLE_CLUSTER_SOURCE_BOARD)
    _write_store(profile, ("uuid-R1", ROLE_FIELD_NAME, "R_OURS"))
    reads: list = []
    original = factory.load_field_overrides
    monkeypatch.setattr(factory, "load_field_overrides",
                        lambda path: reads.append(path) or original(path))

    create_board_adapter(timeout_ms=1, config_path=str(profile))

    assert reads == []


def test_c7_a_layer_bound_with_the_board_source_stays_inert(tmp_path):
    """С7's "ПОЛНОСТЬЮ", at the layer itself: the factory refuses to LAYER a
    "board" profile (pinned structurally in tests/test_adapter_factory.py), and
    the layer refuses to SERVE a store it was handed with that source.

    Two gates, one rule — and the second one is not decoration: the MCP server
    binds a store per CALL (an explicit argument, Т3/С19), so a source travelling
    beside a store is a real state, and a store that quietly wins while the switch
    says "board" is exactly the lie С7 exists to prevent."""
    from kicadstamp.field_override_adapter import FieldOverrideAdapter
    from kicadstamp.field_overrides import load_field_overrides

    profile = _profile(tmp_path)
    _write_store(profile, ("uuid-R1", ROLE_FIELD_NAME, "R_OURS"))
    store = load_field_overrides(overrides_path_for_config(str(profile)))
    adapter = FieldOverrideAdapter(_Board(), None,
                                   source=ROLE_CLUSTER_SOURCE_BOARD)

    adapter.bind_store(store, source=ROLE_CLUSTER_SOURCE_BOARD)

    assert _plan(adapter) == {fp.ref: (fp.fields.get(ROLE_FIELD_NAME),
                                       fp.fields.get(CLUSTER_FIELD_NAME))
                              for fp in _footprints()}


def test_c7_the_switch_is_the_same_answer_for_the_loader_and_the_factory(
        tmp_path):
    """Two readers of one key: the validated loader (which FATALS on a typo) and
    the factory's cheap reader (which falls back with a warning). They must agree
    on a well-formed profile — a drift between them would be a profile that
    resolves one way in a probe and another way in the pipeline."""
    from kicadstamp.config.loader import load_config

    profile = _profile(tmp_path, role_cluster_source=ROLE_CLUSTER_SOURCE_BOARD)

    cfg, _ctx = load_config(str(profile))

    assert cfg.role_cluster_source == role_cluster_source_for_config(str(profile))


# ── С18: one profile, one answer — and the GUI's own state has no say ─────

def test_c18_the_switch_lives_in_the_profile_and_gui_state_cannot_override_it(
        monkeypatch, tmp_path):
    """The switch is in the CONFIG on purpose (plan Т3): a switch living in GUI
    state would be invisible to the CLI, and one profile would resolve two ways.

    So a GUI-state file saying the opposite must change NOTHING — that is what
    makes "the CLI and the GUI agree" a fact rather than a hope (mutation М18:
    read the switch from gui_state.json)."""
    import json

    from gui import settings

    # The whole GUI-state file, at the path the GUI actually uses, saying the
    # OPPOSITE of the profile.
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "gui_state.json")
    (tmp_path / "gui_state.json").write_text(
        json.dumps({"role_cluster_source": "registry"}), encoding="utf-8")

    _install_board(monkeypatch)
    profile = _profile(tmp_path, role_cluster_source=ROLE_CLUSTER_SOURCE_BOARD)
    _write_store(profile, ("uuid-R1", ROLE_FIELD_NAME, "R_OURS"))

    assert role_cluster_source_for_config(str(profile)) == ROLE_CLUSTER_SOURCE_BOARD
    assert _plan(create_board_adapter(timeout_ms=1, config_path=str(profile))) \
        == _plan(create_board_adapter(timeout_ms=1, use_store=False))


# ── С19: over MCP the CALL's profile store is what is in force ────────────

def test_c19_the_mcp_call_runs_the_shared_pipeline_on_its_own_config(monkeypatch):
    """`kicadstamp_apply_config` deliberately does NOT use the shared
    ConnectionManager adapter: it runs the SAME validated pipeline the CLI runs
    (`run_apply`), on the config the CALL named. That is what makes С19 true —
    one pipeline, one resolution rule — so the link is pinned here rather than
    left to a docstring."""
    import mcp_server.handlers as handlers
    import kicadstamp.apply_pipeline as pipeline

    seen = {}

    def _fake_run_apply(options, *args, **kwargs):
        seen["config_path"] = options.config_path
        seen["dry_run"] = options.dry_run
        return ["plan"]

    monkeypatch.setattr(pipeline, "run_apply", _fake_run_apply)

    report = handlers.apply_config("/somewhere/profile.sexp", dry_run=True)

    assert seen == {"config_path": "/somewhere/profile.sexp", "dry_run": True}
    assert report == "plan"


def test_c19_the_pipeline_builds_its_adapter_through_the_factory_with_that_config(
        monkeypatch, tmp_path):
    """...and the pipeline's own adapter is created by the ONE factory WITH the
    call's config_path — the half that actually layers the store (Т2). Without
    this argument the pipeline would resolve the board's values while the profile
    says otherwise, and the MCP report would not match the CLI's."""
    import kicadstamp.apply_pipeline as pipeline

    profile = _profile(tmp_path)
    seen = {}

    def _factory(timeout_ms=None, **kwargs):
        seen["timeout_ms"] = timeout_ms
        seen.update(kwargs)
        return _Board()

    monkeypatch.setattr(pipeline, "create_board_adapter", _factory)
    run = pipeline.ApplyPipeline(config_path=str(profile), timeout_ms=7)

    run._connect_adapter()

    assert seen == {"timeout_ms": 7, "config_path": str(profile)}
    run.close()


def test_c19_a_typo_in_the_switch_is_fatal_for_the_pipeline_not_silent(tmp_path):
    """The loader is the authority for the switch (the factory only falls back for
    its own cheap path): a misspelled value must stop a RUN, never resolve the
    whole board one way while the file says another."""
    from kicadstamp.config.loader import load_config
    from kicadstamp.exceptions import ValidationError

    profile = _profile(tmp_path, role_cluster_source="reigstry")

    with pytest.raises(ValidationError):
        load_config(str(profile))

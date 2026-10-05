# tests/registry/test_registry_match.py
"""СЦ-1 guard (plan_2026_10_05_select_cell_split): the ONE read/claim matching
routine ``kicadstamp.registry_match.match_planned_copper``.

Property under test: the ADOPTION path (``registry.adopt_matching_unowned``) and
the READ path (``match_planned_copper``) call the SAME routine, so their verdicts
can never disagree — one fake registry, both callers, compared cell by cell.
The read path W writes nothing; the adoption path writes only what it claims.

Uses a tiny fake registry (no file IO) whose ``_live_matches`` is key equality,
so the matching rule is isolated from kipy/geometry.
"""
import pytest

from kicadstamp.registry_match import (
    TIER_GEOMETRY,
    TIER_REGISTRY,
    match_planned_copper,
)


class _Cmd:
    def __init__(self, key, owner="r", registry_key="same"):
        # ``key`` is the GEOMETRY token this fake's _live_matches compares;
        # ``registry_key`` is the registry identity (can be None independently).
        self.key = key
        self.registry_key = key if registry_key == "same" else registry_key
        self.owner_ref = owner


class _Live:
    def __init__(self, uuid, key):
        self.uuid = uuid
        self.key = key


class _Entry:
    def __init__(self, uuid):
        self.uuid = uuid


class _FakeRegistry:
    """The BaseRegistry members the matcher and adoption actually use."""

    def __init__(self, entries=None, live=()):
        self.entries = dict(entries or {})
        self._live = list(live)
        self.save_calls = 0

    def _get_live_items(self):
        return list(self._live)

    def _live_matches(self, live, cmd):
        return live.key == cmd.key

    def _build_entry(self, cmd, uuid):
        return _Entry(uuid)

    def _save_entries(self, entries):
        self.save_calls += 1


# ── the two tiers ───────────────────────────────────────────────────────────

def test_registry_uuid_wins_over_geometry():
    """Tier 1: an entry whose uuid is live is found BY REGISTRY, not geometry."""
    reg = _FakeRegistry(entries={"k": _Entry("v1")},
                        live=[_Live("v1", "k")])
    (m,) = match_planned_copper(reg, [_Cmd("k")])
    assert m.tier == TIER_REGISTRY
    assert m.live.uuid == "v1"


def test_geometry_finds_unregistered_copper():
    """Tier 2: a command no entry knows is found by exact geometry."""
    reg = _FakeRegistry(live=[_Live("v1", "k")])
    (m,) = match_planned_copper(reg, [_Cmd("k")])
    assert m.tier == TIER_GEOMETRY
    assert m.live.uuid == "v1"


def test_registry_hit_is_not_doubled_by_geometry():
    """A registry-tier hit consumes the live item, so a second command with the
    same geometry does NOT get it again (no doubling)."""
    reg = _FakeRegistry(entries={"k": _Entry("v1")},
                        live=[_Live("v1", "k")])
    matches = match_planned_copper(reg, [_Cmd("k"), _Cmd("k2")])
    assert [m.tier for m in matches] == [TIER_REGISTRY, None]
    assert matches[1].live is None


# ── no stealing ─────────────────────────────────────────────────────────────

def test_owned_copper_is_never_stolen_by_geometry():
    """A live item owned by ANOTHER key is foreign: geometry must not take it.
    (СЦ-2-4: the read path must not highlight another record's copper.)"""
    reg = _FakeRegistry(entries={"other": _Entry("v1")},
                        live=[_Live("v1", "k")])
    (m,) = match_planned_copper(reg, [_Cmd("k")])
    assert m.live is None
    assert m.tier is None


def test_a_live_item_is_taken_only_once():
    """Two commands with identical geometry match ONE live item: only the first
    gets it (the taken set)."""
    reg = _FakeRegistry(live=[_Live("v1", "k")])
    a, b = match_planned_copper(reg, [_Cmd("k"), _Cmd("k")])
    assert a.tier == TIER_GEOMETRY and a.live.uuid == "v1"
    assert b.live is None


# ── read path writes nothing ────────────────────────────────────────────────

def test_match_is_read_only():
    reg = _FakeRegistry(entries={"k": _Entry("v1")},
                        live=[_Live("v1", "k"), _Live("v2", "k2")])
    match_planned_copper(reg, [_Cmd("k"), _Cmd("k2")])
    assert reg.save_calls == 0
    assert set(reg.entries) == {"k"}           # no key added/removed
    assert reg.entries["k"].uuid == "v1"       # untouched


# ── adoption and the read path agree, cell by cell (СЦ-1, guard 6) ──────────

def test_adoption_claims_exactly_the_geometry_tier():
    """On ONE fake, adoption claims exactly the commands the matcher reports as
    TIER_GEOMETRY (that are not already keyed), and nothing else."""
    reg = _FakeRegistry(
        entries={"known": _Entry("v_known"), "other": _Entry("v_other")},
        live=[_Live("v_known", "known"),      # registry tier — nothing to claim
              _Live("v_other", "nope"),       # owned by 'other' — foreign
              _Live("v_geo", "geo")])         # geometry tier — claim
    cmds = [_Cmd("known"), _Cmd("nope"), _Cmd("geo"), _Cmd("missing")]

    verdicts = match_planned_copper(reg, cmds)
    geometry_keys = {v.command.registry_key for v in verdicts
                     if v.tier == TIER_GEOMETRY}

    from kicadstamp.registry import adopt_matching_unowned
    adopted = adopt_matching_unowned(reg, cmds, live_items=reg._get_live_items())

    assert geometry_keys == {"geo"}
    assert adopted == 1
    assert "geo" in reg.entries and reg.entries["geo"].uuid == "v_geo"
    # 'nope' (foreign-owned) and 'missing' (absent) are never claimed
    assert "nope" not in reg.entries
    assert "missing" not in reg.entries
    # the registry-tier key is untouched (its entry was never re-adopted)
    assert reg.entries["known"].uuid == "v_known"


def test_adoption_writes_only_when_it_claims():
    """Nothing to claim -> the registry file is not rewritten (steady-state
    no-op preserved)."""
    reg = _FakeRegistry(entries={"known": _Entry("v_known")},
                        live=[_Live("v_known", "known")])
    from kicadstamp.registry import adopt_matching_unowned
    adopted = adopt_matching_unowned(
        reg, [_Cmd("known")], live_items=reg._get_live_items())
    assert adopted == 0
    assert reg.save_calls == 0


def test_adoption_skips_a_stale_registered_command():
    """СЦ-4-1: a command whose key IS in the registry (stale uuid) must NOT enter
    the matcher — else it consumes the live item by GEOMETRY and a DIFFERENT
    record's keyless-in-registry command with the SAME geometry goes unadopted,
    and the redraw draws a DUPLICATE. X (key 'X', stale uuid) + Y (key 'Y', not
    in the registry), same geometry -> Y is adopted."""
    reg = _FakeRegistry(entries={"X": _Entry("v_stale")},
                        live=[_Live("v_live", "g")])
    x = _Cmd("g", registry_key="X")     # in the registry, its uuid is stale
    y = _Cmd("g", registry_key="Y")     # same geometry, key NOT in the registry
    from kicadstamp.registry import adopt_matching_unowned
    adopted = adopt_matching_unowned(reg, [x, y])
    assert adopted == 1
    assert reg.entries.get("Y") is not None and reg.entries["Y"].uuid == "v_live"
    assert reg.entries["X"].uuid == "v_stale"   # untouched

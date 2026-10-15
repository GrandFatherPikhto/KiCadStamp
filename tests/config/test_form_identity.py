# tests/config/test_form_identity.py
"""The pure rule behind part 0 of plan_2026_10_05_uuid_tails —
`kicadstamp.config.form_identity.identify`: a form-built record gets the identity
of the record it replaces (its own `uuid` + every reference's `<field>_uuid`).

Qt-free: these cells build a Config in Python and call the rule directly. The
dock-level half (the five redraw paths) lives in
tests/gui/docks/test_form_identity_redraw.py.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.config.form_identity import (FormReferenceMissing,
                                             STORAGE_ONLY_KEYS, identify,
                                             keep_identity, node_ref_uuid,
                                             replace_entry, section_uuids)
from kicadstamp.config.format3 import _F3_NODE_KIND_TARGET
from kicadstamp.config.models import Cell, Config, ThermalViaArrayConfig
from kicadstamp.config.points import Point


def _cfg() -> Config:
    return Config(
        cells={"c1": Cell(name="c1", uuid="U-CELL")},
        points={"p1": Point(name="p1", anchor_role="FPGA", uuid="U-POINT")},
        thermal_via_arrays=[
            ThermalViaArrayConfig(name="tva1", pad="1", uuid="U-TVA")],
    )


def test_section_uuids_reads_both_dict_and_list_sections():
    cfg = _cfg()
    assert section_uuids(cfg, "cells") == {"c1": "U-CELL"}
    assert section_uuids(cfg, "points") == {"p1": "U-POINT"}
    assert section_uuids(cfg, "thermal_via_arrays") == {"tva1": "U-TVA"}


def test_own_uuid_is_inherited_from_the_record_with_the_same_name():
    out = identify({"name": "tva1", "pad": "2"}, "thermal_via_arrays", cfg=_cfg())
    assert out["uuid"] == "U-TVA"


def test_a_rename_keeps_the_remembered_uuid():
    """The form was loaded from record U-TVA; typing a new name must still edit
    THAT record, so the remembered uuid wins over a name lookup that now misses."""
    out = identify({"name": "renamed", "pad": "2"}, "thermal_via_arrays",
                   cfg=_cfg(), remembered_uuid="U-TVA")
    assert out["uuid"] == "U-TVA"


def test_a_brand_new_record_reuses_the_draft_uuid():
    first = identify({"name": "fresh", "pad": "1"}, "thermal_via_arrays", cfg=_cfg())
    again = identify({"name": "fresh", "pad": "1"}, "thermal_via_arrays",
                     cfg=_cfg(), draft_uuid=first["uuid"])
    assert first["uuid"]
    assert again["uuid"] == first["uuid"]


def test_an_existing_uuid_on_the_entry_is_kept():
    out = identify({"name": "x", "pad": "1", "uuid": "U-KEEP"},
                   "thermal_via_arrays", cfg=_cfg())
    assert out["uuid"] == "U-KEEP"


def test_a_name_hint_resolves_the_reference_uuid():
    out = identify({"name": "tva1", "pad": "1", "anchor_point": "p1"},
                   "thermal_via_arrays", cfg=_cfg())
    assert out["anchor_point_uuid"] == "U-POINT"


def test_a_reference_name_with_no_target_is_refused():
    with pytest.raises(FormReferenceMissing) as e:
        identify({"name": "tva1", "pad": "1", "anchor_point": "typo"},
                 "thermal_via_arrays", cfg=_cfg())
    assert "typo" in str(e.value)


def test_a_target_without_a_uuid_carries_nothing_and_is_not_a_refusal():
    """A format-2 graph (or a fake) may hold the target WITHOUT a uuid: the name
    resolves, there is simply nothing to carry across."""
    cfg = _cfg()
    cfg.points = {"p1": Point(name="p1", anchor_role="FPGA")}   # no uuid
    out = identify({"name": "tva1", "pad": "1", "anchor_point": "p1"},
                   "thermal_via_arrays", cfg=cfg)
    assert out.get("anchor_point_uuid") is None


# ── node_ref_uuid: the ref_uuid a TREE NODE must carry ─────────────────────

# One record per section `_F3_NODE_KIND_TARGET` can point at: (name, uuid).
# Plain objects on purpose — the rule reads ONLY `.name`/`.uuid` (a dict section
# is keyed by the name), which is exactly what the loader fills a loaded Config
# with (`section_uuids`).
_RECORDS = {
    "entities": ("E1", "U-ENT"),
    "coordinate_placements": ("K1", "U-COORD"),
    "net_traces": ("NT1", "U-NT"),
    "points": ("P1", "U-POINT"),
}


def _node_cfg() -> Config:
    lists = {section: [SimpleNamespace(name=name, uuid=uuid)]
             for section, (name, uuid) in _RECORDS.items() if section != "points"}
    point_name, point_uuid = _RECORDS["points"]
    return Config(points={point_name: SimpleNamespace(uuid=point_uuid)}, **lists)


def test_every_kind_target_section_has_a_record_in_this_rig():
    """Rule 35: the cell below walks the ONE table, so a NEW kind/section added
    to `_F3_NODE_KIND_TARGET` must not quietly skip the rig."""
    assert set(_F3_NODE_KIND_TARGET.values()) <= set(_RECORDS)


@pytest.mark.parametrize("kind", sorted(_F3_NODE_KIND_TARGET))
def test_node_ref_uuid_resolves_every_record_kind(kind):
    """A node's `ref_uuid` is the uuid of the record its NEW ref names, resolved
    through the SAME table the loader and the writer stamp walk."""
    section = _F3_NODE_KIND_TARGET[kind]
    name, uuid = _RECORDS[section]
    assert node_ref_uuid(_node_cfg(), kind, name) == uuid


@pytest.mark.parametrize("kind", ["module", "mount", "copper", "component",
                                  "external", None])
def test_node_ref_uuid_is_none_for_a_kind_without_a_record(kind):
    """A kind that references no record carries NO uuid — and the caller must
    CLEAR a stale one (the loader fatals on it beside a local kind)."""
    assert node_ref_uuid(_node_cfg(), kind, "anything") is None


def test_node_ref_uuid_refuses_a_ref_naming_no_record():
    """The refusal the writer would reach only at write time — here at Apply, so
    a mistyped Ref cannot silently roll the node back on the next load."""
    with pytest.raises(FormReferenceMissing) as e:
        node_ref_uuid(_node_cfg(), "placement", "typo")
    assert "typo" in str(e.value)
    assert "entities" in str(e.value)


def test_node_ref_uuid_resolves_a_target_without_a_uuid_to_none():
    """A format-2 graph (or a fake) may hold the record WITHOUT a uuid: the name
    resolves, there is simply nothing to carry across."""
    cfg = _node_cfg()
    cfg.entities = [SimpleNamespace(name="E1", uuid=None)]
    assert node_ref_uuid(cfg, "placement", "E1") is None


# ── keep_identity: the dict-level twin (2026-10-15) ────────────────────────
# The copper writers rebuild a record from a live CAPTURE and hold the dict they
# REPLACE, not the loaded Config — so they carry the storage-only keys across
# instead of asking `identify` which uuid the record has.

def test_keep_identity_carries_the_stored_uuid_onto_a_fresh_dict():
    """С10 — the whole point: the dict the writers splice in carries no `uuid`
    (net_trace_to_dict does not emit one), and the replaced dict's identity must
    travel with it."""
    old = {"name": "nt1", "uuid": "U-OLD"}
    out = keep_identity(old, {"name": "nt1", "track_count": 3})
    assert out["uuid"] == "U-OLD"


def test_keep_identity_never_overwrites_a_uuid_the_entry_already_has():
    """С11 — the form path answered first (it calls `identify`): its uuid wins,
    so a second answer here cannot drag the record to another identity."""
    out = keep_identity({"name": "nt1", "uuid": "U-OLD"},
                        {"name": "nt1", "uuid": "U-FORM"})
    assert out["uuid"] == "U-FORM"


def test_keep_identity_invents_nothing_when_the_old_record_has_no_uuid():
    """С12 — a format-2 record (or a fake) has no uuid: the name resolves, there
    is simply nothing to carry across (the stamp mints one at write time)."""
    out = keep_identity({"name": "nt1"}, {"name": "nt1"})
    assert out.get("uuid") is None


def test_keep_identity_copies_and_carries_only_the_declared_keys():
    """С13 — rule 35 discipline in both directions: the returned dict is a COPY
    (the caller's input is untouched), and ONLY `STORAGE_ONLY_KEYS` travel — a
    "carry everything left over" would resurrect stale state (retired/skip) that
    the record the writer built deliberately does not have."""
    old = {"name": "nt1", "uuid": "U-OLD", "retired": True, "comment": "note"}
    new = {"name": "nt1"}
    out = keep_identity(old, new)

    assert out is not new
    assert new == {"name": "nt1"}          # the input was not touched
    assert out["uuid"] == "U-OLD"
    assert "retired" not in out and "comment" not in out
    assert STORAGE_ONLY_KEYS == ("uuid",)


# ── replace_entry: the whole in-place step of a list section ───────────────

def test_replace_entry_replaces_in_place_keeping_the_stored_identity():
    """С14 — what the three copper writers do: the record carrying this identity
    is replaced where it stands, and the replaced dict's uuid travels with it."""
    entries = [{"name": "a", "uuid": "U-A"}, {"name": "b", "uuid": "U-B"}]

    replaced = replace_entry(entries, {"name": "b", "track_count": 2},
                             identity="b", identity_of=lambda e: e.get("name"))

    assert replaced is True
    assert [e["name"] for e in entries] == ["a", "b"]      # position kept
    assert entries[1]["uuid"] == "U-B"


def test_replace_entry_appends_when_no_record_carries_the_identity():
    """С15 — a genuinely new record is appended and carries NOTHING: there is no
    old dict to take an identity from (the stamp mints one at write time)."""
    entries = [{"name": "a", "uuid": "U-A"}]

    replaced = replace_entry(entries, {"name": "c"}, identity="c",
                             identity_of=lambda e: e.get("name"))

    assert replaced is False
    assert [e["name"] for e in entries] == ["a", "c"]
    assert entries[1].get("uuid") is None


def test_replace_entry_never_matches_a_non_dict_entry():
    """С16 — a hand-edited list can hold a stray scalar: it is never "the record
    with this name" (and its own read of `identity_of` is never attempted)."""
    entries: list = ["stray"]

    assert replace_entry(entries, {"name": "a", "uuid": "U-A"}, identity="a",
                         identity_of=lambda e: e.get("name")) is False
    assert entries[0] == "stray"
    assert entries[1]["uuid"] == "U-A"

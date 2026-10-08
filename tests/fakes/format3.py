# tests/fakes/format3.py
"""Fixtures for exercising format 3 (UUID, step 2->3) — plan §У1.4/У1.5.

``CURRENT_FORMAT`` stays 2 in the product, so a format-3 file is refused by the
reader unless a test pins the build to 3. ``format3`` does exactly that by
substituting the MODULE attribute (never a from-import — both
``format_version.current_format()`` and ``refuse_newer()`` read
``CURRENT_FORMAT`` at call time; the pattern is test_config_format_version.py:171).

``det_uuid`` mints in the PRODUCT migration namespace (У3.2 / Р-У3.4), so a
fixture-built format-3 graph carries the SAME UUIDs the real converter writes;
deterministic, so byte snapshots do not drift.
"""
import copy

import pytest

from kicadstamp.config import format_version
from kicadstamp.config.uuids import migration_folder_uuid, migration_uuid


def det_uuid(n) -> str:
    """A deterministic UUID in the PRODUCT migration namespace (У3.2).

    The key follows this stub's own `<section>:<full name>` convention
    (`det_uuid("cells:cap")`), so the stub and the real 2 -> 3 step mint the SAME
    uuid for the same record — ONE seed for the product and the tests, never a
    private `…00ab` namespace (Р-У3.4). A key WITHOUT a colon is a helper value
    (a dangling uuid, a hand-written fixture) and stays deterministic in the
    same namespace.
    """
    section, sep, name = str(n).partition(":")
    if sep:
        return migration_uuid(section, name)
    return migration_uuid("", str(n))


def identity_value(name: str, uuid: str | None) -> str:
    """The record-identity VALUE a registry key uses under the CURRENT format:
    the uuid under the format-3 gate (Р-У5.1/Р-У5.2), the name in format 2.

    A test helper so a key-shape cell can pass under BOTH CURRENT_FORMAT = 2
    (today) and 3 (after the switch) without being pinned to either."""
    from kicadstamp.config.format_version import current_format
    return uuid if current_format() >= 3 else name


def registry_schema() -> int:
    """The registry schema the CURRENT format gate EXPECTS — the same number the
    product's writer stamps (``registry._registry_schema_version_for_write``).

    3 under format 3 (Д2 — the spoke copper keys are detached), 1 in format 2.
    EVERY fixture that writes a registry by hand must use this instead of a
    literal, so the number can never drift from the product again (it silently
    did when Д2 raised the format-3 schema from 2 to 3)."""
    from kicadstamp.config.format_version import current_format
    from kicadstamp.persistence import (REGISTRY_SCHEMA_VERSION,
                                        REGISTRY_SCHEMA_VERSION_FORMAT3)
    return (REGISTRY_SCHEMA_VERSION_FORMAT3 if current_format() >= 3
            else REGISTRY_SCHEMA_VERSION)


def without_identity(value):
    """Deep copy with every uuid / ``*_uuid`` key and the ``folders`` table
    dropped (У3.5 К3, row 8 helper).

    A cell whose SUBJECT is NOT the uuid still reads back a written graph, which
    under format 3 carries a ``uuid`` on every §0 record and a ``*_uuid`` sibling
    on every reference. This lets such a cell compare the SHAPE it cares about
    without pinning ``CURRENT_FORMAT``; under format 2 the drop is a no-op, so one
    cell body passes under both formats. Rule 35: use it ONLY where the uuid is
    not what the cell is about — a cell about the uuid itself must see it.
    """
    if isinstance(value, dict):
        return {k: without_identity(v) for k, v in value.items()
                if k != "folders" and k != "uuid" and not k.endswith("_uuid")}
    if isinstance(value, list):
        return [without_identity(v) for v in value]
    return value


def stamp_config(cfg):
    """Assign `det_uuid` to every §0 record of a Config that has none.

    A TEST helper (У3.5 К3, rule 35): model-built records default to
    `uuid=None`. Under CURRENT_FORMAT = 3 the registry-key builders refuse a
    record without a uuid (Р-У5.7), so a hand-built Config a planner is driven
    with must carry one. The uuid is keyed by the record's identity exactly like
    `det_uuid("<section>:<identity>")`, so a cell can spell the expected key.

    Only records of the §0 sections are touched; a nested CellPlacement (no uuid
    of its own) and anchored branches keep their deterministic name.
    """
    def _one(rec, section, identity):
        if rec is not None and getattr(rec, "uuid", None) is None and identity:
            rec.uuid = det_uuid(f"{section}:{identity}")

    def _link(rec):
        """A reference to a points: record also carries its uuid under the gate
        (the `point:` anchor branch, Р-У5.1)."""
        point = getattr(rec, "anchor_point", None)
        if point and not getattr(rec, "anchor_point_uuid", None):
            rec.anchor_point_uuid = det_uuid(f"points:{point}")

    for name, rec in (getattr(cfg, "cells", None) or {}).items():
        _one(rec, "cells", name)
    for name, rec in (getattr(cfg, "points", None) or {}).items():
        _one(rec, "points", name)
        _link(rec)
    for rec in (getattr(cfg, "clone_placements", None) or []):
        _one(rec, "clone_placements", getattr(rec, "name", None) or rec.cluster)
        _link(rec)
    for rec in (getattr(cfg, "thermal_via_arrays", None) or []):
        _one(rec, "thermal_via_arrays", rec.name)
        _link(rec)
    for rec in (getattr(cfg, "coordinate_placements", None) or []):
        _one(rec, "coordinate_placements", rec.name)
        _link(rec)
    for rec in (getattr(cfg, "entities", None) or []):
        _one(rec, "entities", rec.name)
    for rec in (getattr(cfg, "chains", None) or []):
        _one(rec, "chains", rec.name)
        _link(rec)
    for rec in (getattr(cfg, "net_traces", None) or []):
        _one(rec, "net_traces", rec.name or rec.net)
    return cfg


# ── §0 record sections (mirror of config/loader's tables) ──────────────────
# Deliberately duplicated HERE: this is a TEST stub and the product must never
# import it. The reference walk itself is NOT duplicated — it reuses the
# product's `loader._f3_refs`, so the test cannot drift from the shipped form
# table.
_DICT_SECTIONS = ("cells", "points")
_LIST_SECTIONS = ("chains", "clone_placements", "thermal_via_arrays",
                  "coordinate_placements", "net_traces", "entities", "imprints")
_FREE_SECTIONS = ("extract_profiles", "clone_profiles", "sheet_templates")


def _add_name(index: dict, name, record_uuid) -> None:
    """Index one alias -> uuid, marking a duplicate alias AMBIGUOUS (None) —
    two net_traces on one net must not silently resolve to the first."""
    if not name:
        return
    if name in index and index[name] != record_uuid:
        index[name] = None
    else:
        index[name] = record_uuid


def mint_format3(data: dict) -> dict:
    """Raw FORMAT-2 dict -> a valid FORMAT-3 dict (plan §4, У2.4).

    A single-dict convenience over :func:`mint_format3_files`."""
    return mint_format3_files({"<single>": data})["<single>"]


def mint_format3_files(raw_by_path: dict) -> dict:
    """{path: raw FORMAT-2 dict} -> {path: FORMAT-3 dict} for ONE include graph.

    A TEST STUB, never imported by the product. Two passes, because a reference
    in one file may target a record in another: first stamp `det_uuid` on every
    §0 record and build the GLOBAL name -> UUID index, then fill every
    reference's UUID sibling (using the product's own `_f3_refs` form table, so
    the test cannot drift from the shipped forms). An UNNAMED list record is
    called out loudly: a format-3 record needs a name (В36 mints one during the
    real conversion)."""
    from kicadstamp.config.loader import _f3_refs  # test-only, product untouched

    out = {p: copy.deepcopy(d) for p, d in raw_by_path.items()}
    index: dict[str, dict[str, str]] = {}
    for data in out.values():
        for section in _DICT_SECTIONS + _FREE_SECTIONS:
            for key, rec in (data.get(section) or {}).items():
                record_uuid = det_uuid(f"{section}:{key}")
                if isinstance(rec, dict):
                    rec["uuid"] = record_uuid
                _add_name(index.setdefault(section, {}), key, record_uuid)
        for section in _LIST_SECTIONS:
            for i, rec in enumerate(data.get(section) or []):
                name = rec.get("name")
                if not name:
                    raise ValueError(
                        f"mint_format3: unnamed {section}[{i}] — a format-3 "
                        "record needs a name (В36 mints one during the real "
                        "conversion)")
                record_uuid = det_uuid(f"{section}:{name}")
                rec["uuid"] = record_uuid
                section_index = index.setdefault(section, {})
                for alias in (name, rec.get("net"), rec.get("cluster"),
                              (f"{rec.get('cluster')}/{rec.get('role')}"
                               if rec.get("cluster") and rec.get("role") else None)):
                    _add_name(section_index, alias, record_uuid)
    for data in out.values():
        for ref in _f3_refs(data):
            name = ref.holder.get(ref.name_field)
            resolved = index.get(ref.target, {}).get(name)
            if resolved is None:
                raise ValueError(
                    f"mint_format3: {ref.label} names no unique {ref.target} "
                    f"record ({name!r})")
            ref.holder[ref.uuid_field] = resolved

    # Folder rows (В39) — the mirror of the real step: every path prefix of every
    # record name, seeded by the PRODUCT folder builder, so a stub-minted graph
    # carries the folder table the converter would write and one path in two
    # files keeps ONE uuid. `_folder_prefixes` is the product's own walk (test
    # import only), so the stub cannot drift from В39.
    from kicadstamp.config.format3 import _folder_prefixes  # test-only

    for data in out.values():
        rows: dict = {}
        for section in _DICT_SECTIONS + _FREE_SECTIONS:
            for key in (data.get(section) or {}):
                for prefix in _folder_prefixes(key):
                    rows.setdefault(section, {}).setdefault(
                        prefix, migration_folder_uuid(section, prefix))
        for section in _LIST_SECTIONS:
            for rec in (data.get(section) or []):
                for prefix in _folder_prefixes(rec.get("name") or ""):
                    rows.setdefault(section, {}).setdefault(
                        prefix, migration_folder_uuid(section, prefix))
        if not rows:
            continue
        # Merge, never clobber: an existing row keeps its uuid, exactly like the
        # real step (a hand-written folder row passed in must survive).
        table = data.setdefault("folders", {})
        for section, path_map in rows.items():
            section_table = table.setdefault(section, {})
            for prefix, folder_uuid in path_map.items():
                section_table.setdefault(prefix, folder_uuid)
    return out


def lie_hints(data: dict) -> dict:
    """Replace every RECORD reference's NAME hint with garbage, keeping the UUID
    — the 'подсказка врёт' variant of the equivalence probe (У2.4(3)).

    TREE NODES are deliberately left honest: their `ref` is BOTH a §0 reference
    (restored by the UUID) and a LOCAL name other local references (a self
    anchor's `self_ref`, `pivot_ref`) point at — and those locals have no UUID
    to be restored from, so lying the node ref would break the tree's internal
    consistency rather than exercise the normalization. The node's lying-hint
    resolution is covered by its own cell in test_format3_normalize.py."""
    from kicadstamp.config.loader import _f3_refs  # test-only

    out = copy.deepcopy(data)
    for ref in _f3_refs(out):
        if ref.name_field == "ref":      # a tree node ref — see docstring
            continue
        record_uuid = ref.holder.get(ref.uuid_field)
        if record_uuid:
            ref.holder[ref.name_field] = "x_" + str(record_uuid)[:8]
    return out


@pytest.fixture
def format3(monkeypatch):
    """Pin this build's CURRENT_FORMAT to 3 (plan §У1.4).

    With CURRENT_FORMAT = 3 the reader accepts ``(version 3)`` and the writer
    stamps it; since У3.1 a format-2 file is LIFTED by ``_step_2_to_3`` (the
    Р-1 seed). A cell whose SUBJECT is format 2 (a byte-for-byte comparison of a
    format-2 write, the number converter) must therefore pin the constant back
    to 2 itself, or use data that already carries UUIDs."""
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    return 3


@pytest.fixture
def active_root():
    """Set the process-wide ACTIVE GRAPH ROOT for one test (У4.1), restoring the
    previous value at teardown.

    Usage: ``active_root(tmp_path / "root.sexp")`` — returns the path it set.
    The format-3 writer stamp resolves reference UUIDs against this root, so a
    test that exercises the stamp must point it at its own graph."""
    from kicadstamp.config_working_set import active_graph_root, set_active_graph_root

    previous = active_graph_root()

    def _set(root):
        set_active_graph_root(root)
        return root

    yield _set
    set_active_graph_root(previous)

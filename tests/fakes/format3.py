# tests/fakes/format3.py
"""Fixtures for exercising format 3 (UUID, step 2->3) — plan §У1.4/У1.5.

``CURRENT_FORMAT`` stays 2 in the product, so a format-3 file is refused by the
reader unless a test pins the build to 3. ``format3`` does exactly that by
substituting the MODULE attribute (never a from-import — both
``format_version.current_format()`` and ``refuse_newer()`` read
``CURRENT_FORMAT`` at call time; the pattern is test_config_format_version.py:171).

``det_uuid`` gives deterministic UUIDs so byte snapshots do not drift.
"""
import copy
import uuid

import pytest

from kicadstamp.config import format_version

# A fixed namespace: det_uuid(n) is stable across runs and machines.
_NS = uuid.UUID("00000000-0000-0000-0000-0000000000ab")


def det_uuid(n) -> str:
    """A deterministic UUID for a small integer/name — one per record/target."""
    return str(uuid.uuid5(_NS, str(n)))


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

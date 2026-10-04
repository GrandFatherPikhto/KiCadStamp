# tests/config/test_config_format3_writer.py
"""У4.1/У4.2/У4.3 — the format-3 WRITER STAMP (plan §5).

Every stamp cell runs under the ``format3`` fixture (CURRENT_FORMAT = 3) AND an
active graph root. The product (CURRENT_FORMAT = 2) never reaches the stamp; the
gate cell at the bottom proves the bytes are unchanged there.

Rule 35 table (section x direction), all under format 3:
  * п.1 record without uuid -> uuid4 (s-expr + JSON);
  * п.2 new reference -> target UUID by exact full name (chain spoke, tree node);
  * п.3 reference with UUID -> hint rewritten; dangling -> whole write refused;
  * п.4 folder minted / reused across files;
  * п.5 tree node ref + tree anchor point;
  * unknown new name -> refused; duplicate full name -> refused;
  * set_reference drops the sibling UUID;
  * copy inside the graph -> new UUID;
  * gate: CURRENT_FORMAT=2 -> byte-identical (no uuid, still version 2).
"""
import json
import re
from pathlib import Path

import pytest

from kicadstamp.config import format_version
from kicadstamp.config import load_config
from kicadstamp.config import loader as _loader
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_writer import (
    add_list_entry,
    append_tree_child_node,
    merge_write,
    read_data,
    set_reference,
    upsert_clone_placement,
    upsert_entity,
    upsert_entity_placement,
    upsert_list_entry,
    upsert_tree_instances,
    write_config_file,
)
from kicadstamp.config_working_set import set_active_graph_root
from kicadstamp.utils.file_cache import invalidate_graph_path, invalidate_path
from tests.fakes.format3 import (  # noqa: F401  (fixtures)
    active_root,
    det_uuid,
    format3,
    mint_format3_files,
)


def _write_graph(tmp_path: Path, files: dict) -> dict:
    """Write one include graph as valid FORMAT 3 (uuid5 per record + ref uuids)."""
    minted = mint_format3_files(files)
    for name, d in minted.items():
        (tmp_path / name).write_text(
            dict_to_sexp(d, format_number=3), encoding="utf-8")
    return minted


def _read(path: Path) -> dict:
    invalidate_path(path)
    invalidate_graph_path(path)
    return read_data(path)


# ── п.1: a record without a uuid gets uuid4 ────────────────────────────────

def test_new_record_gets_uuid4(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"cells": {"cap": {}}}})
    active_root(root)

    merge_write(root, {"cells": {"cap2": {}}}, section="cells")

    data = _read(root)
    assert data["cells"]["cap"]["uuid"] == det_uuid("cells:cap"), "existing kept"
    assert data["cells"]["cap2"]["uuid"]
    assert data["cells"]["cap2"]["uuid"] != data["cells"]["cap"]["uuid"]


def test_new_record_gets_uuid4_json(format3, active_root, tmp_path):
    root = tmp_path / "root.json"
    root.write_text(json.dumps({"version": 3, "cells": {"cap": {"uuid": det_uuid("c")}}}),
                    encoding="utf-8")
    active_root(root)

    merge_write(root, {"cells": {"cap2": {}}}, section="cells")

    raw = json.loads(root.read_text(encoding="utf-8"))
    assert raw["cells"]["cap"]["uuid"] == det_uuid("c")
    assert raw["cells"]["cap2"]["uuid"]


# ── п.2: a new reference resolves by exact full name ───────────────────────

def test_new_reference_resolves_by_full_name(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"cells": {"cap": {}}, "chains": []}})
    active_root(root)

    upsert_list_entry(root, "chains",
                      {"name": "c1", "spokes": [{"cell": "cap", "pad": "1"}]})

    chain = _read(root)["chains"][0]
    assert chain["spokes"][0]["cell_uuid"] == det_uuid("cells:cap")


def test_unknown_new_reference_refuses_the_whole_write(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"cells": {"cap": {}}, "chains": []}})
    active_root(root)
    before = root.read_text(encoding="utf-8")

    with pytest.raises(OSError):
        upsert_list_entry(root, "chains",
                          {"name": "c1", "spokes": [{"cell": "ghost", "pad": "1"}]})

    assert root.read_text(encoding="utf-8") == before, "file untouched"


def test_new_tree_node_ref_resolves_to_its_entity(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {
        "cells": {"cap": {}}, "entities": [{"name": "e1", "cell": "cap"}]}})
    active_root(root)

    upsert_entity_placement(root, "e1", {"mode": "xy", "x": 1.0, "y": 2.0})

    node = _read(root)["trees"][0]["nodes"][0]
    assert node["ref"] == "e1"
    assert node["ref_uuid"] == det_uuid("entities:e1")


# ── п.3: a reference with a UUID keeps it and rewrites the hint ────────────

def test_lying_hint_is_rewritten_from_the_uuid(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {
        "cells": {"cap_a": {}, "cap_b": {}},
        "entities": [{"name": "e1", "cell": "cap_a"}]}})
    active_root(root)

    data = _read(root)
    data["entities"][0]["cell"] = "cap_b"          # hint lies, UUID is cap_a
    write_config_file(root, data)

    e = _read(root)["entities"][0]
    assert e["cell"] == "cap_a"
    assert e["cell_uuid"] == det_uuid("cells:cap_a")


def test_dangling_reference_refuses_the_whole_write(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {
        "cells": {"cap_a": {}}, "entities": [{"name": "e1", "cell": "cap_a"}]}})
    active_root(root)

    data = _read(root)
    data["entities"][0]["cell_uuid"] = "00000000-0000-0000-0000-0000000000ff"
    before = root.read_text(encoding="utf-8")

    with pytest.raises(OSError):
        write_config_file(root, data)

    assert root.read_text(encoding="utf-8") == before


def test_tree_anchor_point_gets_and_keeps_its_uuid(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"points": {"p1": {}}}})
    active_root(root)

    write_config_file(root, {
        "points": {"p1": {"uuid": det_uuid("points:p1")}},
        "trees": [{"name": "t1", "anchor": {"point": "p1"}, "nodes": []}],
    })

    anchor = _read(root)["trees"][0]["anchor"]
    assert anchor["point_uuid"] == det_uuid("points:p1")


# ── п.4: folders ───────────────────────────────────────────────────────────

def test_folder_rows_are_minted_for_path_prefixes(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"cells": {}}})
    active_root(root)

    merge_write(root, {"cells": {"Power/LDO/cap": {}}}, section="cells")

    folders = _read(root)["folders"]["cells"]
    assert set(folders) == {"Power", "Power/LDO"}
    assert folders["Power"] != folders["Power/LDO"]


def test_existing_folder_path_reuses_its_uuid_across_files(format3, active_root, tmp_path):
    sub_uuid = det_uuid("folder Power")
    _write_graph(tmp_path, {
        "root.sexp": {"include": ["sub.sexp"], "cells": {}},
        "sub.sexp": {"cells": {"Power/existing": {}},
                     "folders": {"cells": {"Power": sub_uuid}}},
    })
    root = tmp_path / "root.sexp"
    active_root(root)

    merge_write(root, {"cells": {"Power/other": {}}}, section="cells")

    cfg, _ctx = load_config(str(root))
    assert cfg.folders["cells"]["Power"] == sub_uuid, "one path, one UUID (В39)"
    raw = _read(root)
    assert raw.get("folders", {}).get("cells", {}).get("Power") in (None, sub_uuid)


def test_duplicate_full_name_across_files_refuses(format3, active_root, tmp_path):
    _write_graph(tmp_path, {
        "root.sexp": {"include": ["sub.sexp"], "cells": {"cap": {}}},
        "sub.sexp": {"cells": {}},
    })
    active_root(tmp_path / "root.sexp")
    sub = tmp_path / "sub.sexp"
    before = sub.read_text(encoding="utf-8")

    with pytest.raises(OSError):
        merge_write(sub, {"cells": {"cap": {}}}, section="cells")

    assert sub.read_text(encoding="utf-8") == before


# ── У4.2: set_reference and copies ─────────────────────────────────────────

def test_set_reference_drops_the_uuid_sibling():
    holder = {"cell": "a", "cell_uuid": "u1", "pad": "1"}
    set_reference(holder, "cell", "b")
    assert holder["cell"] == "b"
    assert "cell_uuid" not in holder
    assert holder["pad"] == "1", "other fields untouched"


def test_changed_reference_points_at_the_new_target(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {
        "cells": {"cap_a": {}, "cap_b": {}},
        "entities": [{"name": "e1", "cell": "cap_a"}]}})
    active_root(root)

    data = _read(root)
    set_reference(data["entities"][0], "cell", "cap_b")
    write_config_file(root, data)

    e = _read(root)["entities"][0]
    assert e["cell"] == "cap_b"
    assert e["cell_uuid"] == det_uuid("cells:cap_b"), "the NEW target, not cap_a"


def test_copy_inside_the_graph_gets_a_new_uuid(format3, active_root, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"cells": {"cap": {"comment": "x"}}}})
    active_root(root)

    original = _read(root)["cells"]["cap"]
    copy = dict(original, comment="copy")
    copy.pop("uuid", None)                 # a copy is written WITHOUT uuid
    merge_write(root, {"cells": {"cap_copy": copy}}, section="cells")

    data = _read(root)
    assert data["cells"]["cap"]["uuid"] == det_uuid("cells:cap")
    assert data["cells"]["cap_copy"]["uuid"] not in (None, data["cells"]["cap"]["uuid"])


# ── the gate: CURRENT_FORMAT = 2 (no fixture) leaves bytes unchanged ───────

def test_gate_current_format_2_leaves_bytes_unchanged(monkeypatch, tmp_path):
    # Gate cell (У3.5 К3): its SUBJECT is the format-2 write, so it pins the
    # constant (a cell that needs format 3 requests the `format3` fixture).
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    set_active_graph_root(None)
    root = tmp_path / "r.sexp"
    root.write_text(dict_to_sexp({"cells": {"cap": {}}}), encoding="utf-8")

    merge_write(root, {"cells": {"cap2": {}}}, section="cells")

    text = root.read_text(encoding="utf-8")
    assert "(version 2)" in text
    assert "uuid" not in text


def test_records_without_a_root_are_refused(format3, tmp_path):
    set_active_graph_root(None)
    root = tmp_path / "r.sexp"
    _write_graph(tmp_path, {"r.sexp": {"cells": {"cap": {}}}})

    with pytest.raises(OSError):
        merge_write(root, {"cells": {"cap2": {}}}, section="cells")


def test_record_free_write_needs_no_root(format3, tmp_path):
    set_active_graph_root(None)
    fresh = tmp_path / "new.sexp"

    write_config_file(fresh, {})

    assert fresh.exists()
    assert "(version 3)" in fresh.read_text(encoding="utf-8")


# ── У4.2: flatten through the one serializer ───────────────────────────────

def test_flatten_preserves_uuids_and_references(format3, active_root, tmp_path):
    """flatten goes through serialize_config with the SOURCE graph root, so a
    flat file loads in format 3 and its references resolve to the same targets
    (plan §5, У4.2)."""
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {
        "cells": {"Power/cap": {}},
        "entities": [{"name": "e1", "cell": "Power/cap"}]}})
    active_root(root)
    flat = tmp_path / "flat.sexp"

    from kicadstamp.flatten import flatten_config

    flatten_config(root=str(root), output=str(flat))

    raw = _read(flat)
    assert raw["cells"]["Power/cap"]["uuid"] == det_uuid("cells:Power/cap")
    assert raw["entities"][0]["cell_uuid"] == det_uuid("cells:Power/cap")
    cfg, _ctx = load_config(str(flat))
    assert cfg.entities[0].cell_uuid == det_uuid("cells:Power/cap")


# ── У4.1: end-to-end — a writer without the stamp reddens here ─────────────

def _graph_root(tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {
        "cells": {"cap": {}},
        "entities": [{"name": "e1", "cell": "cap"}],
        "trees": [{"name": "t1", "anchor": {"origin": True}, "nodes": []}],
    }})
    return root


@pytest.mark.parametrize("writer, expect_load", [
    pytest.param(lambda r: merge_write(r, {"cells": {"c2": {}}}, section="cells"),
                 True, id="merge_write"),
    pytest.param(lambda r: upsert_list_entry(
        r, "chains", {"name": "c1", "net": "GND", "anchor_ref": "IC1",
                      "spokes": [{"cell": "cap", "pad": "1"}]}),
        True, id="upsert_list_entry"),
    pytest.param(lambda r: upsert_entity(r, {"name": "e2", "cell": "cap"}),
                 True, id="upsert_entity"),
    pytest.param(lambda r: upsert_clone_placement(
        r, {"name": "cp1", "cluster": "CP", "cell": "cap", "anchor_ref": "IC1"}),
        True, id="upsert_clone_placement"),
    pytest.param(lambda r: upsert_entity_placement(r, "e1", {"mode": "xy", "x": 1.0, "y": 2.0}),
                 True, id="upsert_entity_placement"),
    pytest.param(lambda r: append_tree_child_node(
        r, "t1", None, {"ref": "e1", "kind": "placement", "xy": [1, 2]}),
        True, id="append_tree_child_node"),
    pytest.param(lambda r: upsert_tree_instances(r, "t1", [{"name": "i1", "sheet": "/"}]),
                 False, id="upsert_tree_instances_no_load"),
    pytest.param(lambda r: add_list_entry(r, "include", "sub.sexp"),
                 False, id="add_list_entry_no_load"),
])
def test_every_writer_leaves_a_stamped_file(format3, active_root, tmp_path,
                                            writer, expect_load):
    """Every config_writer writer, through the one serializer: after the call no
    §0 record lacks a UUID, and (for the writers that produce a loadable graph)
    the graph loads. A new writer added without the stamp reddens here."""
    root = _graph_root(tmp_path)
    active_root(root)

    writer(root)

    raw = _read(root)
    missing = [(s, n) for s, n, u, _i in _loader._f3_records(raw) if not u]
    assert missing == [], f"records without a uuid after the write: {missing}"
    if expect_load:
        load_config(str(root))


# ── К3.5: a reference WITH a uuid stays on ONE line ────────────────────────
# Pure serializer-layout cells (no fixture): `format_number` is passed
# explicitly, so they are independent of CURRENT_FORMAT.

def test_a_format3_reference_with_a_uuid_is_written_on_one_line():
    """Subject: the reference node's LAYOUT. A format-3 reference keeps its uuid
    on the SAME line — `(cell "cap" (uuid "…"))` — instead of the four-line
    column `_dumps` would otherwise produce (plan §7 К3.5, Денис 04.10)."""
    text = dict_to_sexp(
        {"cells": {"cap": {"uuid": "u-cap"}},
         "entities": [{"name": "e1", "cell": "cap", "cell_uuid": "u-cap"}]},
        format_number=3)
    # The reference stands as ONE line at the entity record's indentation …
    assert re.search(r'^      \(cell "cap" \(uuid "u-cap"\)\)$', text, re.M)
    # … and it is NOT wrapped: no bare `(cell` line at THAT indentation (the
    # cells record's own node is indented two spaces less and is irrelevant).
    assert not re.search(r'^      \(cell$', text, re.M)


def test_a_format3_tree_node_ref_with_a_uuid_is_written_on_one_line():
    """Subject: the TREE-NODE ref layout. К3.5 inlined a RECORD's reference
    FIELDS only — a tree node's `ref` was missed, so a format-3 lift rewrote
    every node as a three-line column (`(ref` / `"x"` / `)`). It now goes
    through the SAME helper (`sexp_format._ref_field_to_sexp`), so
    `(ref "e1" (uuid "u-node"))` stands on one line (plan §7 К4, Денис 04.10)."""
    text = dict_to_sexp(
        {"trees": [{"name": "t", "anchor": {"origin": True},
                    "nodes": [{"ref": "e1", "kind": "placement",
                               "xy": [1.0, 2.0], "ref_uuid": "u-node"}]}]},
        format_number=3)
    assert re.search(r'^        \(ref "e1" \(uuid "u-node"\)\)$', text, re.M)
    assert not re.search(r'^        \(ref$', text, re.M)


def test_a_format2_tree_node_snapshot_is_unchanged():
    """Subject: the format-2 tree-node LAYOUT — no uuid, a plain all-atom node
    already on one line; the fix must not move a byte (Денис: «снимки формата 2
    — байт в байт»)."""
    text = dict_to_sexp(
        {"trees": [{"name": "t", "anchor": {"origin": True},
                    "nodes": [{"ref": "e1", "kind": "placement",
                               "xy": [1.0, 2.0]}]}]},
        format_number=2)
    assert text == (
        "(kicadstamp-config\n"
        "  (version 2)\n"
        "  (trees\n"
        "    (tree\n"
        '      (name "t")\n'
        "      (anchor\n"
        "        (origin)\n"
        "      )\n"
        "      (node\n"
        '        (ref "e1")\n'
        "        (kind placement)\n"
        "        (xy 1.0 2.0)\n"
        "      )\n"
        "    )\n"
        "  )\n"
        ")\n"
    )


def test_a_format2_reference_snapshot_is_unchanged():
    """Subject: the format-2 LAYOUT. Without a uuid every reference is a plain
    all-atom node, already on one line; the К3.5 change must not move a byte.
    `format_number=2` is written explicitly: the subject is format 2 itself."""
    text = dict_to_sexp({"cells": {"cap": {}},
                         "entities": [{"name": "e1", "cell": "cap"}]},
                        format_number=2)
    assert text == (
        "(kicadstamp-config\n"
        "  (version 2)\n"
        "  (cells\n"
        '    (cell "cap")\n'
        "  )\n"
        "  (entities\n"
        "    (entity\n"
        '      (name "e1")\n'
        '      (cell "cap")\n'
        "    )\n"
        "  )\n"
        ")\n"
    )


def test_a_format3_non_reference_nested_node_keeps_the_column_layout():
    """Subject: the К3.5 exception is NARROW — only a reference node is inlined;
    every other multi-child node (here a record's own `(uuid …)` child) keeps
    the multi-line column layout."""
    text = dict_to_sexp({"cells": {"Power/cap": {"uuid": "u1"}}}, format_number=3)
    assert '(cell\n      "Power/cap"\n      (uuid "u1")\n    )' in text


# ── К4: the product's OWN constant (no `format3` fixture) ──────────────────
# Deliberately WITHOUT the `format3` fixture: after the 2 -> 3 flip the fixture
# would be a no-op, so only a cell that reads the PRODUCT's own constant can
# notice the constant and the writer drifting apart (Денис, 04.10.2026).

def test_a_fresh_config_is_written_in_the_current_format_with_a_uuid_per_record(
        tmp_path):
    """К4: a NEW config written by the product is born in the format the build
    writes — `(version CURRENT)` — with a UUID on EVERY record."""
    root = tmp_path / "fresh.sexp"
    set_active_graph_root(root)   # the stamp resolves references against it
    try:
        write_config_file(root, {"cells": {"cap": {}},
                                 "entities": [{"name": "e1", "cell": "cap"}]})
    finally:
        set_active_graph_root(None)

    text = root.read_text(encoding="utf-8")
    assert f"(version {format_version.CURRENT_FORMAT})" in text
    data = read_data(root)
    assert data["cells"]["cap"]["uuid"], "the cell record carries an identity"
    assert data["entities"][0]["uuid"], "the entity record carries an identity"
    assert data["entities"][0]["cell_uuid"] == data["cells"]["cap"]["uuid"]

# tests/config/test_net_trace_write_keeps_uuid.py
"""`write_net_trace` — an in-place `net_traces:` edit keeps the record's stored
identity (its `uuid`), through `form_identity.keep_identity`.

The rewritten record is built from a live CAPTURE, so `net_trace_to_dict` never
emits a `uuid`; replacing the old dict WHOLE used to drop it and the format-3
writer stamp refused the whole write on the tree node that still named the old
uuid (plan_2026_10_15_reread_copper_uuid_and_selection_any). Each cell therefore
writes a graph a node actually REFERENCES — on a graph without the node the cell
would stay green on the broken code, because nothing would dangle.

Cells (rule 35):
  * С6 — the end-to-end path (the stamp's own inheritance intact);
  * С6b — the SAME write with the stamp's inheritance TAKEN AWAY: only the
    writer's own carry can satisfy it. This is the cell that actually guards
    `keep_identity` — measured: without it С6 stays green when the helper stops
    carrying (the stamp inherits the uuid by name from the file it just read),
    and a cell that cannot fail proves nothing (rule 39);
  * С8 — a brand-new record is APPENDED and the stamp mints its identity.

The LEGACY (nameless) record is NOT here: it fails for a second reason — the
stamp's `_index_add_record` keys the name map by `rec.get("name")` and stores
`None`, so the node's ref reads as dangling. Its cell belongs with that fix
(commit 3 of the plan).
"""
from pathlib import Path

import pytest

from kicadstamp.config import NetTrace, load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_writer import read_data
from kicadstamp.net_trace_extract import net_trace_to_dict, write_net_trace
from tests.fakes.format3 import active_root, format3  # noqa: F401  (fixtures)

UUID_NAMED = "16f70780-0000-4000-8000-000000000001"
NAME = "heater_01__mcu__signal_out"
NET = "/MCU/HEATER_01"


@pytest.fixture
def no_stamp_inheritance(monkeypatch):
    """Take away the SECOND mechanism. On a healthy cache the stamp inherits the
    uuid from the file's previous content by (section, name), so a writer cell
    runs green even with the writer's own carry removed. Emptying the previous
    content leaves the writer's carry as the only source of the identity."""
    from kicadstamp.config import format3

    monkeypatch.setattr(format3, "_load_previous_file", lambda path: {})


def _write_graph(tmp_path: Path, set_active_graph_root, record: dict,
                 node_ref: str, node_uuid: str) -> Path:
    path = tmp_path / "root.sexp"
    path.write_text(dict_to_sexp({
        "net_traces": [record],
        "trees": [{"name": "mcu", "anchor": {"origin": True},
                   "nodes": [{"ref": node_ref, "kind": "net_trace",
                              "ref_uuid": node_uuid}]}],
    }, format_number=3), encoding="utf-8")
    set_active_graph_root(path)
    return path


def _named_graph(tmp_path, set_active_graph_root) -> Path:
    return _write_graph(tmp_path, set_active_graph_root,
                        {"name": NAME, "net": NET, "anchor_role": "U1",
                         "uuid": UUID_NAMED, "tracks": [], "vias": []},
                        NAME, UUID_NAMED)


def _records(path: Path) -> list[dict]:
    return list(read_data(path).get("net_traces") or [])


def test_a_named_record_keeps_its_uuid_on_a_re_extract(format3, active_root,
                                                       tmp_path):
    """С6 — Denis' live case, end to end: the write goes through AND the record
    keeps the uuid the tree node names."""
    path = _named_graph(tmp_path, active_root)

    write_net_trace(str(path), NetTrace(net=NET, anchor_role="U1",
                                        name=NAME, tracks=[], vias=[]))

    records = _records(path)
    assert len(records) == 1
    assert records[0]["uuid"] == UUID_NAMED
    load_config(str(path))          # the node reference still resolves


def test_the_writer_itself_carries_the_uuid_without_the_stamp(
        format3, active_root, no_stamp_inheritance, tmp_path):
    """С6b — the identity rule on its OWN: with the stamp's inheritance emptied,
    a bare `net_traces[i] = entry` is refused outright (the node's uuid dangles)
    and only `form_identity.keep_identity` can satisfy the write."""
    path = _named_graph(tmp_path, active_root)

    write_net_trace(str(path), NetTrace(net=NET, anchor_role="U1",
                                        name=NAME, tracks=[], vias=[]))

    assert _records(path)[0]["uuid"] == UUID_NAMED
    load_config(str(path))


def test_a_brand_new_record_still_gets_a_fresh_uuid(format3, active_root,
                                                    tmp_path):
    """С8 — nobody replaces anybody: the record is APPENDED and the stamp mints
    its identity, exactly as before (the carried-over path must not leak onto an
    append, and the old record must keep its own uuid)."""
    path = _named_graph(tmp_path, active_root)

    write_net_trace(str(path), NetTrace(net="/MCU/HEATER_02", anchor_role="U1",
                                        name="other_bridge", tracks=[],
                                        vias=[]))

    records = _records(path)
    assert len(records) == 2
    by_name = {r.get("name"): r for r in records}
    assert by_name[NAME]["uuid"] == UUID_NAMED
    assert by_name["other_bridge"]["uuid"] not in (None, UUID_NAMED)
    load_config(str(path))


def test_the_serializer_still_emits_no_uuid_for_a_captured_record():
    """The PREMISE of the cells above, stated as its own assertion: a record
    built from a live capture carries no uuid in its dict — which is why an
    in-place edit must carry it over (if this ever changes, the carried-over
    path becomes a no-op and this cell says so)."""
    assert net_trace_to_dict(NetTrace(net=NET, anchor_role="U1", name=NAME,
                                      tracks=[], vias=[])).get("uuid") is None

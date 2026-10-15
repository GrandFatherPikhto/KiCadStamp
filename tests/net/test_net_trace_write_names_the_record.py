# tests/net/test_net_trace_write_names_the_record.py
"""A format-3 record MUST have a name — nothing may write one that doesn't.

Two layers, both from plan_2026_10_15_reread_copper_uuid_and_selection_any
(commit 3), and both measured live before they were written:

  * F2, the defect: `write_net_trace` of a NAMELESS record — the two product
    paths into it are the Net trace tab's «Extract» (`gui/docks/net_trace.py`)
    and the CLI's `extract-net`; both build a `NetTrace` without a name. In a
    format-3 file the stamp minted a uuid and let it through, and the FILE ITSELF
    became unloadable (П2: "record without a name — net_traces[0]").
  * and the reverse side of the same rule: П2 — a format-3 record needs a name
    (Р43) — is ONE function, `config/format3.check_record_has_a_name`, called by
    the loader's graph check AND by the stamp, so a write the loader would refuse
    never reaches the disk (it protects every writer and every list section, not
    only the copper ones).

Cells (rule 35), per the plan's table: no record of the net / one named record
(the live shape) / one record named by the converter on lift (В36) / two records
of the net / the stamp handed an unnamed record directly / format 2 unchanged.
"""
from pathlib import Path

import pytest

from kicadstamp.config import NetTrace, load_config
from kicadstamp.config import format_version
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.net_trace_extract import NetTraceNetAmbiguous, write_net_trace
from tests.fakes.format3 import active_root, format3  # noqa: F401  (fixtures)

UUID_ONE = "16f70780-0000-4000-8000-000000000001"
NET = "/MCU/HEATER_01"
NET_LEAF = "heater_01"          # what generate_trace_name makes of NET


def _write_root(tmp_path: Path, set_active_graph_root, data: dict) -> Path:
    path = tmp_path / "root.sexp"
    path.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")
    set_active_graph_root(path)
    return path


def _records(path: Path) -> list[dict]:
    return list(read_data(path).get("net_traces") or [])


def _loads(path: Path) -> None:
    """The whole point of the guard: the file we just wrote must still LOAD."""
    load_config(str(path))


# ── layer 2: the writer gives the name ─────────────────────────────────────

def test_a_nameless_capture_of_a_new_net_is_written_named(format3, active_root,
                                                          tmp_path):
    """С17 — the F2 reproduction. Before the fix the write went through and this
    cell's `load_config` failed with "record without a name"."""
    path = _write_root(tmp_path, active_root, {"net_traces": []})

    write_net_trace(str(path), NetTrace(net=NET, anchor_role="U1"))

    records = _records(path)
    assert len(records) == 1
    assert records[0]["name"] == NET_LEAF     # the copper-name owner's own rule
    assert records[0]["uuid"]
    _loads(path)


def test_a_nameless_capture_replaces_the_one_record_of_that_net(
        format3, active_root, tmp_path):
    """С18 — the live re-extract: ONE record of this net → its own name is taken,
    so the record is REPLACED (uuid kept, the tree node referencing it intact),
    not duplicated."""
    path = _write_root(tmp_path, active_root, {
        "net_traces": [{"name": "heater_01__mcu__signal_out", "net": NET,
                        "anchor_role": "U1", "uuid": UUID_ONE}],
        "trees": [{"name": "mcu", "anchor": {"origin": True},
                   "nodes": [{"ref": "heater_01__mcu__signal_out",
                              "kind": "net_trace", "ref_uuid": UUID_ONE}]}],
    })

    write_net_trace(str(path), NetTrace(net=NET, anchor_role="U1"))

    records = _records(path)
    assert len(records) == 1
    assert records[0]["name"] == "heater_01__mcu__signal_out"
    assert records[0]["uuid"] == UUID_ONE
    _loads(path)


def test_a_name_minted_by_the_converter_counts_as_the_records_name(
        format3, active_root, tmp_path):
    """С19 — a record named by В36 on lift (`net_trace_001`) is the record of this
    net: it is replaced, and no second entry appears."""
    path = _write_root(tmp_path, active_root, {
        "net_traces": [{"name": "net_trace_001", "net": NET,
                        "anchor_role": "U1", "uuid": UUID_ONE}]})

    write_net_trace(str(path), NetTrace(net=NET, anchor_role="U1"))

    records = _records(path)
    assert len(records) == 1
    assert records[0]["name"] == "net_trace_001"
    assert records[0]["uuid"] == UUID_ONE
    _loads(path)


def test_several_records_of_the_net_are_refused_and_the_file_is_untouched(
        format3, active_root, tmp_path):
    """С20 — two bridges of one net: which to update is the user's call. The
    refusal names them, and NOTHING is written."""
    path = _write_root(tmp_path, active_root, {"net_traces": [
        {"name": "bridge_a", "net": NET, "anchor_role": "U1", "uuid": UUID_ONE},
        {"name": "bridge_b", "net": NET, "anchor_role": "U1",
         "uuid": "16f70780-0000-4000-8000-000000000003"}]})
    before = path.read_text(encoding="utf-8")

    with pytest.raises(NetTraceNetAmbiguous) as e:
        write_net_trace(str(path), NetTrace(net=NET, anchor_role="U1"))

    assert "bridge_a" in str(e.value) and "bridge_b" in str(e.value)
    assert path.read_text(encoding="utf-8") == before


def test_format_2_still_writes_a_nameless_record(format3, active_root,
                                                monkeypatch, tmp_path):
    """С21 — the gate is the STAMP's gate: format 2 works exactly as before, so
    the naming cannot change a format-2 profile by accident."""
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    path = tmp_path / "root.sexp"
    path.write_text(dict_to_sexp({"net_traces": []}, format_number=2),
                    encoding="utf-8")
    active_root(path)

    entry = write_net_trace(str(path), NetTrace(net=NET, anchor_role="U1"))

    assert entry.get("name") is None
    assert "(name " not in path.read_text(encoding="utf-8")


# ── layer 1: the stamp refuses what the loader would refuse ────────────────

def test_the_stamp_refuses_an_unnamed_record_and_leaves_the_file_alone(
        format3, active_root, tmp_path):
    """С22 — the backstop for EVERY writer: an unnamed record handed straight to
    the ONE serializer is refused with the loader's own П2 text (one function,
    one message), and the file keeps its previous content."""
    path = _write_root(tmp_path, active_root, {"net_traces": []})
    before = path.read_text(encoding="utf-8")

    with pytest.raises(OSError) as e:
        write_data(path, {"net_traces": [{"net": NET, "anchor_role": "U1"}]})

    message = str(e.value)
    assert "record without a name" in message
    assert "net_traces[0]" in message
    assert path.read_text(encoding="utf-8") == before

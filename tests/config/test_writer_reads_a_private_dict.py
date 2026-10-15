# tests/config/test_writer_reads_a_private_dict.py
"""`read_data` — the ONE reader every config writer takes its dict from — hands
out the CALLER'S OWN dict (kicadstamp/config_writer.py::_read_data deepcopies).

Why it matters (measured live 2026-10-15, plan
plan_2026_10_15_reread_copper_uuid_and_selection_any; Denis' profile): the
format-3 writer stamp reads the file's PREVIOUS content from the same in-process
cache (kicadstamp/config/format3.py::_load_previous_file) to inherit a record's
uuid on an in-place edit. When a writer edited the cache's OWN object, the stamp
read the edit as "the previous version of this file", found no uuid for the
record and minted a new one — every tree node referencing the record then
dangled and the WHOLE write was refused:

    FATAL ERROR: format 3: cannot write — 1 reference(s) point at a uuid that
    is missing from the graph
      ✗ ...: tree 'mcu' node 'heater_01__mcu__signal_out' -> uuid … (net_traces)

The defect needed a WARM cache (a miss returns an object the cache only keeps a
copy of, so the in-place edit hit nothing) — which is the GUI's normal state,
the file having been read long before the edit. Hence every cell warms the
cache first: the cold-cache cells would be green on the broken code.

Cells (rule 35): property (no write) / the two writer modes (physical and
staged) / the staged read / negative control that the cache itself still shares.
"""
from pathlib import Path

import pytest

from kicadstamp.config import NetTrace
from kicadstamp.config.includes import _load_config_file
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.net_trace_extract import net_trace_to_dict
from kicadstamp.utils.file_cache import cached_file_read
from tests.fakes.format3 import active_root, format3  # noqa: F401  (fixtures)

RECORD_UUID = "16f70780-0000-4000-8000-000000000001"
NAME = "heater_01__mcu__signal_out"


@pytest.fixture
def working_set():
    """The GUI's staged mode (the working set is OFF by default: CLI and the
    other cells here hit the physical path)."""
    previous = WORKING_SET.enabled
    WORKING_SET.clear()
    WORKING_SET.enabled = True
    yield WORKING_SET
    WORKING_SET.clear()
    WORKING_SET.enabled = previous


def _write_root(tmp_path: Path, set_active_graph_root) -> Path:
    """A minimal format-3 root: one NAMED net_traces record and a tree node
    (kind net_trace) referencing it by name AND uuid."""
    path = tmp_path / "root.sexp"
    path.write_text(dict_to_sexp({
        "net_traces": [{"name": NAME, "net": "/MCU/HEATER_01",
                        "anchor_role": "U1", "uuid": RECORD_UUID,
                        "tracks": [], "vias": []}],
        "trees": [{"name": "mcu", "anchor": {"origin": True},
                   "nodes": [{"ref": NAME, "kind": "net_trace",
                              "ref_uuid": RECORD_UUID}]}],
    }, format_number=3), encoding="utf-8")
    set_active_graph_root(path)
    return path


def _warm(path: Path) -> None:
    """The GUI's normal state: this file was read long before the edit."""
    read_data(path)
    read_data(path)


def _record(data: dict) -> dict:
    for entry in data.get("net_traces") or []:
        if (entry.get("name") or entry.get("net")) == NAME:
            return entry
    raise AssertionError("the net_traces record vanished")


def _splice_without_uuid(data: dict) -> None:
    """Byte for byte what the three copper writers do: a dict built from the
    MODEL replaces the record IN PLACE. `net_trace_to_dict` emits no `uuid`, so
    the record's identity survives only if the writer (or the stamp's
    inheritance) carries it across."""
    fresh = net_trace_to_dict(NetTrace(net="/MCU/HEATER_01", anchor_role="U1",
                                       name=NAME, tracks=[], vias=[]))
    for i, entry in enumerate(data.get("net_traces") or []):
        if (entry.get("name") or entry.get("net")) == NAME:
            data["net_traces"][i] = fresh
            return
    raise AssertionError("the net_traces record vanished")


# ── 1. the property itself: no write involved ──────────────────────────────

def test_editing_what_read_data_returned_does_not_reach_the_cache(
        format3, active_root, tmp_path):
    """С1 (plan_2026_10_15_reread_copper_uuid_and_selection_any). The dict is
    the caller's: splicing a record into it must not change what the NEXT
    reader sees. Before the fix the SAME object the cache held was edited, so
    the next `read_data` answered with the writer's un-persisted edit."""
    path = _write_root(tmp_path, active_root)
    _warm(path)

    data = read_data(path)
    _splice_without_uuid(data)
    assert _record(data).get("uuid") is None      # the edit DID happen

    assert _record(read_data(path)).get("uuid") == RECORD_UUID


# ── 2. the reproduction: the log's fatal, on the writers' own path ─────────

def test_a_writer_splice_keeps_the_uuid_on_a_warm_cache(
        format3, active_root, tmp_path):
    """С2 — the reproduction of the live message. With the cache warm and the
    record spliced without a uuid, the stamp inherits the uuid from the
    (un-corrupted) previous content and the write is ACCEPTED. On the broken
    code this raised the format-3 "cannot write — 1 reference(s) point at a
    uuid that is missing from the graph" fatal."""
    path = _write_root(tmp_path, active_root)
    _warm(path)

    data = read_data(path)
    _splice_without_uuid(data)
    write_data(path, data)                        # must not raise

    assert _record(read_data(path)).get("uuid") == RECORD_UUID


def test_a_writer_splice_keeps_the_uuid_under_the_working_set(
        format3, active_root, working_set, tmp_path):
    """С3 — the same in the GUI's staged mode: the working set holds the
    stamped content, and the record keeps its uuid there too."""
    path = _write_root(tmp_path, active_root)
    _warm(path)

    data = read_data(path)
    _splice_without_uuid(data)
    write_data(path, data)                        # stages

    assert _record(read_data(path)).get("uuid") == RECORD_UUID


# ── 3. the staged branch and the cache contract around it ──────────────────

def test_editing_a_staged_read_does_not_touch_the_staged_content(
        format3, active_root, working_set, tmp_path):
    """С4 — a read served from the working set is a copy as well (it was one
    before this fix too; the cell keeps it from being folded back)."""
    path = _write_root(tmp_path, active_root)
    write_data(path, read_data(path))             # stage the file as it is
    assert working_set.staged_content(str(path.resolve())) is not None

    data = read_data(path)                        # served from the working set
    _splice_without_uuid(data)

    assert _record(read_data(path)).get("uuid") == RECORD_UUID


def test_the_file_cache_itself_still_hands_out_the_shared_object(
        format3, active_root, tmp_path):
    """С5 — negative control: the copy lives at `read_data`, NOT in the cache.
    Making the cache copy instead would hide the writers' bug behind a second
    one (and tax every read-only graph walk)."""
    path = _write_root(tmp_path, active_root)

    cached_file_read(path, _load_config_file)     # first call: a MISS
    second = cached_file_read(path, _load_config_file)
    third = cached_file_read(path, _load_config_file)

    assert second is third

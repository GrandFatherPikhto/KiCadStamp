# diagnostics/deepseek_probe_reread_uuid_cache_2026_10_15.py
"""Probe (no board, no Qt): WHICH mechanism makes an in-place `net_traces` edit
lose its uuid and the format-3 writer refuse the whole write with

    format 3: cannot write — N reference(s) point at a uuid that is missing
    from the graph
      tree 'mcu' node 'heater_01__mcu__signal_out' -> uuid ... (net_traces)

Denis' profile has a NAMED record (`name "heater_01__mcu__signal_out"`), so the
"nameless records never inherit" hole is NOT his case. The candidates left, told
apart by switching them off one at a time:

  * CACHE   — `read_data` returns the SHARED object of `cached_file_read` (no
              copy outside staged mode), and the writer mutates it in place
              (`entries[i] = entry`); the stamp then reads that very object as
              "the previous version of this file" and finds no uuid.
  * WRITER  — the writer never carries the old record's uuid onto the new dict
              (`net_trace_to_dict` has no `uuid`).
  * STAMP   — the inheritance in `stamp_format3` keyed by (section, name) from
              the previous content of the same file.

Scenarios, each printed with its verdict:

  1. cold cache, working set OFF   — is the write accepted, which uuid lands?
  2. warm cache, working set OFF   — the same, with the cache warmed by a prior
                                     `read_data` (this is the GUI's normal state)
  3. warm cache, working set ON    — the GUI's staged mode
  4. cold cache + uuid carried by the writer (the FIX, simulated) — control that
                                     the reproduction really is about the cache

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_probe_reread_uuid_cache_2026_10_15.py`
"""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from kicadstamp.config import NetTrace, TemplateTrack  # noqa: F401 (shape check)
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_working_set import WORKING_SET, set_active_graph_root
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.net_trace_extract import net_trace_to_dict

RECORD_UUID = "16f70780-0000-4000-8000-000000000001"
NODE_UUID = RECORD_UUID  # the node references the record by its uuid
NAME = "heater_01__mcu__signal_out"


def _root_dict(with_uuid: bool = True) -> dict:
    """A minimal format-3 root: one NAMED net_traces record + one tree node
    (kind net_trace) referencing it by name AND uuid."""
    record = {
        "name": NAME,
        "net": "/MCU/HEATER_01",
        "anchor_role": "U1",
        "tracks": [],
        "vias": [],
    }
    if with_uuid:
        record["uuid"] = RECORD_UUID
    return {
        "net_traces": [record],
        "trees": [
            {
                "name": "mcu",
                "anchor": {"origin": True},
                "nodes": [
                    {
                        "ref": NAME,
                        "kind": "net_trace",
                        "ref_uuid": NODE_UUID,
                    }
                ],
            }
        ],
    }


def _fresh_renamed_dict() -> dict:
    """What the three copper writers actually splice in: a dict built from the
    MODEL, with the record's identity refreshed and NO uuid (net_trace_to_dict
    does not emit it)."""
    return net_trace_to_dict(NetTrace(net="/MCU/HEATER_01", anchor_role="U1",
                                      name=NAME, tracks=[], vias=[]))


def _scenario(label: str, *, warm: bool, staged: bool, carry_uuid: bool) -> None:
    print(f"\n=== {label} ===")
    prev_enabled = WORKING_SET.enabled
    prev_root = None
    try:
        from kicadstamp.config_working_set import active_graph_root
        prev_root = active_graph_root()
    except Exception:  # noqa: BLE001
        pass
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "root.sexp"
        target.write_text(dict_to_sexp(_root_dict(), format_number=3),
                          encoding="utf-8")
        set_active_graph_root(target)
        WORKING_SET.enabled = staged
        WORKING_SET.clear()
        try:
            if warm:
                # The GUI's normal state: the file has been read long ago.
                read_data(target)
                read_data(target)
            data = read_data(target)
            old = None
            for e in data.get("net_traces", []):
                if isinstance(e, dict) and (e.get("name") or e.get("net")) == NAME:
                    old = e
            print(f"  read_data -> record has uuid: "
                  f"{bool(old and old.get('uuid'))}")
            # The writers' in-place replacement, byte for byte what
            # _stage_net_traces / dock_hub / write_net_trace do.
            new = _fresh_renamed_dict()
            if carry_uuid and old and old.get("uuid"):
                new["uuid"] = old["uuid"]
            for i, e in enumerate(data.get("net_traces", [])):
                if isinstance(e, dict) and (e.get("name") or e.get("net")) == NAME:
                    data["net_traces"][i] = new
                    break
            try:
                write_data(target, copy.deepcopy(data) if staged else data)
                print("  write_data -> ACCEPTED")
            except Exception as exc:  # noqa: BLE001
                print("  write_data -> REFUSED; message:")
                for line in str(exc).splitlines():
                    print(f"      | {line}")
            # Did the shared cache itself get corrupted by the in-place edit?
            after = read_data(target)
            rec = (after.get("net_traces") or [{}])[0]
            print(f"  cache/disk after write: uuid present = "
                  f"{bool(rec.get('uuid'))}, uuid = {rec.get('uuid')}")
        finally:
            WORKING_SET.clear()
            WORKING_SET.enabled = prev_enabled
            try:
                set_active_graph_root(prev_root)
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    print("uuid of the record on disk:", RECORD_UUID)
    _scenario("1. cold cache, working set OFF", warm=False, staged=False,
              carry_uuid=False)
    _scenario("2. WARM cache, working set OFF", warm=True, staged=False,
              carry_uuid=False)
    _scenario("3. WARM cache, working set ON (GUI)", warm=True, staged=True,
              carry_uuid=False)
    _scenario("4. control: uuid carried by the writer", warm=True, staged=False,
              carry_uuid=True)


if __name__ == "__main__":
    main()

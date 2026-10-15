# tests/gui/docks/test_reread_net_trace_identity.py
"""`TreesDock._stage_net_traces` — the re-read's staging step, and the place
Denis' live refusal came from:

    FATAL ERROR: format 3: cannot write — 1 reference(s) point at a uuid that is
    missing from the graph
      ✗ ...: tree 'mcu' node 'heater_01__mcu__signal_out' -> uuid c1e8f716-…
    (entered at gui/docks/trees_dock.py:3375 _finish_reread_internode_copper)

Cells (rule 35):

  * С9 — the end-to-end path with a NAMED record whose copper changed: the write
    goes through and the tree node that references the record still resolves (a
    graph with no referencing node would stay green on the broken code: nothing
    would dangle);
  * С9b — the SAME write with the stamp's inheritance taken away, so that only
    `form_identity.keep_identity` (called by the dock) can carry the uuid. This
    is the cell that guards the DOCK's call: measured, С9 alone stays green when
    the carry is removed, because the stamp inherits by name from the file.

EMPTY CELL, by decision: DockHub's Extract-tree splice (gui/dock_hub.py,
`_extract_tree_from_selection_now`) is the third writer of the same rule. It is
NOT guarded here — its splice lives inline in a ~180-line Qt flow that needs a
live board, a selection and a tree dialog, so a cell would be a test of that flow
rather than of the identity rule. The rule is one call to
form_identity.keep_identity, guarded by tests/config/test_form_identity.py (the
helper) and by the two writer cells here / in
tests/config/test_net_trace_write_keeps_uuid.py.

The dock is NEVER constructed here: `_stage_net_traces` reads exactly one
attribute (`_root_path`), so the cells call the method on a stand-in and stay
Qt-free (no widgets, no `qapp`).
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicadstamp.config import NetTrace, load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_writer import read_data
from tests.fakes.format3 import active_root, format3  # noqa: F401  (fixtures)

UUID_NAMED = "16f70780-0000-4000-8000-000000000001"
NAME = "heater_01__mcu__signal_out"
NET = "/MCU/HEATER_01"


@pytest.fixture
def no_stamp_inheritance(monkeypatch):
    """The stamp's own inheritance (by section+name from the file's previous
    content) emptied, so the dock's carry is the only source of the identity —
    see the module docstring."""
    from kicadstamp.config import format3

    monkeypatch.setattr(format3, "_load_previous_file", lambda path: {})


def _graph(tmp_path: Path, set_active_graph_root) -> Path:
    path = tmp_path / "root.sexp"
    path.write_text(dict_to_sexp({
        "net_traces": [{"name": NAME, "net": NET, "anchor_role": "U1",
                        "uuid": UUID_NAMED, "tracks": [], "vias": []}],
        "trees": [{"name": "mcu", "anchor": {"origin": True},
                   "nodes": [{"ref": NAME, "kind": "net_trace",
                              "ref_uuid": UUID_NAMED}]}],
    }, format_number=3), encoding="utf-8")
    set_active_graph_root(path)
    return path


def _reread(path: Path) -> None:
    """The dock's staging step, called on a stand-in: it reads `_root_path` and
    nothing else."""
    from gui.docks.trees_dock import TreesDock

    captured = NetTrace(net=NET, anchor_role="U1", name=NAME, tracks=[], vias=[])
    TreesDock._stage_net_traces(SimpleNamespace(_root_path=path), [captured])


def test_a_reread_keeps_the_uuid_of_the_record_a_tree_node_references(
        format3, active_root, tmp_path):
    """С9 — the live case: the re-read splices a freshly CAPTURED record (no uuid
    in its dict) in place of the stored one; the stored identity must survive."""
    path = _graph(tmp_path, active_root)

    _reread(path)

    record = (read_data(path).get("net_traces") or [])[0]
    assert record["uuid"] == UUID_NAMED
    load_config(str(path))          # the node reference still resolves


def test_the_reread_carries_the_uuid_without_the_stamp(
        format3, active_root, no_stamp_inheritance, tmp_path):
    """С9b — the dock's own carry, with the stamp's inheritance emptied: a bare
    `entries[i] = entry` is refused here (the node's uuid dangles)."""
    path = _graph(tmp_path, active_root)

    _reread(path)

    record = (read_data(path).get("net_traces") or [])[0]
    assert record["uuid"] == UUID_NAMED
    load_config(str(path))

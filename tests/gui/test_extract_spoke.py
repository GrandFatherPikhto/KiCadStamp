#!/usr/bin/env python3
"""Guards of the read/write layer behind Tools -> "Extract spoke..." (stage 5,
plan_2026_09_17_spoke_s5_extract_spoke.md §3 Door, §4 Т5.3).

No KiCad and no QApplication: gui/docks/extract_spoke.py is Qt-free by design,
so the whole flow — the ONE board read that fills the dialog and the write OK
performs — is pinned here on a stub adapter over the REAL domain dataclasses
(kicadstamp.domain.board.Footprint/Pad) and a real config file on disk.

What these guards defend, in the plan's own terms:
  * the board is refreshed before the selection is read (Door П3.1);
  * the plane rule and the nearest anchor pad (С3/С4) hold on the real read;
  * a MIRRORED existing cell is refused, not silently written (ManualSpoke has
    no mirror field — spoke_layout.py);
  * Р6/С12: a selection that changed since the dialog was read writes NOTHING;
  * the extraction writes the cell into the ROOT config and the spoke into its
    chain's own file, with the spoke LAST (Ф8.1's order).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kicadstamp.domain.board import Footprint, Pad, BoardLayer            # noqa: E402
from kicadstamp.config_writer import read_data, write_data                # noqa: E402
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME      # noqa: E402
from kicadstamp.domain.geometry import Vector2                            # noqa: E402
from kicadstamp.spoke_extraction import SpokeOffset, spoke_entry          # noqa: E402
from kicadstamp.utils.units import MM                                     # noqa: E402

from gui.docks import extract_spoke as mod                                # noqa: E402

NET = "+3V3_VDD"
CLUSTER = "MCU_PWR_BANK"
BULK = "C_OUT_BULK"
BYPASS = "C_OUT_BYPASS"
CELL = "mcu_pair"

# The pair stands at (11, 5) and (9, 5) mm; the cell's stored offsets put its
# origin at (10, 5) — so the frame read from the board is exactly that point.
ORIGIN = (10.0, 5.0)


def _fp(ref, layer=BoardLayer.BL_F_Cu, x=0.0, y=0.0):
    return Footprint(ref=ref, uuid=f"uuid-{ref}",
                     position=Vector2.from_xy(int(x * MM), int(y * MM)),
                     angle_deg=0.0, layer=layer, sheet_path_uuids=())


def _pad(number, net, x=0.0, y=0.0):
    return Pad(number=str(number), net_name=net,
               position=Vector2.from_xy(int(x * MM), int(y * MM)))


class _Adapter:
    """The board, as gui/docks/extract_spoke.py reads it — nothing else."""

    def __init__(self, footprints, pads_by_ref, roles, clusters, selected):
        self._fps = list(footprints)
        self._pads = {ref: list(pads) for ref, pads in pads_by_ref.items()}
        self._roles = dict(roles)
        self._clusters = dict(clusters)
        self._selected = list(selected)
        self.refreshed = 0

    def refresh_board(self):
        self.refreshed += 1

    def get_selected_items(self):
        return list(self._selected)

    def get_footprints(self):
        return list(self._fps)

    def get_footprint(self, ref):
        return next((fp for fp in self._fps if fp.ref == ref), None)

    def get_field_value(self, fp, field):
        if field == ROLE_FIELD_NAME:
            return self._roles.get(fp.ref)
        if field == CLUSTER_FIELD_NAME:
            return self._clusters.get(fp.ref)
        return None

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, ()))

    def get_pad_by_number(self, fp, number):
        return next((p for p in self._pads.get(fp.ref, ())
                     if str(p.number) == str(number)), None)


def _cell_entry(roles=(BULK, BYPASS), layer="F.Cu"):
    return {"components": [{"role": roles[0], "offset_along_mm": 1.0,
                            "offset_across_mm": 0.0},
                           {"role": roles[1], "offset_along_mm": -1.0,
                            "offset_across_mm": 0.0}],
            "vias": [], "tracks": [], "layer": layer}


def _board(layer=BoardLayer.BL_F_Cu, plane_members=41):
    """The pair + its anchor (U5, pads 12 and 48 on the rail) + a GND plane big
    enough to be dropped by the plane rule + the pad-1 pair already placed.

    The pair stands at (11, 5) and (9, 5) mm — the cell's stored offsets are
    (1, 0) and (-1, 0) from ORIGIN, so the frame read from the board is exactly
    ORIGIN and the fit has a real direction to work with."""
    fps = [_fp("C41", layer, ORIGIN[0] + 1.0, ORIGIN[1]),
           _fp("C42", layer, ORIGIN[0] - 1.0, ORIGIN[1]),
           _fp("U5"), _fp("C43", layer), _fp("C44", layer)]
    pads = {"C41": [_pad("1", NET, *ORIGIN), _pad("2", "GND", *ORIGIN)],
            "C42": [_pad("1", NET, *ORIGIN), _pad("2", "GND", *ORIGIN)],
            # pad 12 is listed FIRST on purpose: the nearest one is 48 (С4).
            "U5": [_pad("12", NET, 40.0, 5.0), _pad("48", NET, 11.0, 4.5)],
            "C43": [_pad("1", NET, 30.0, 5.0), _pad("2", "GND", 30.0, 5.0)],
            "C44": [_pad("1", NET, 30.0, 5.0), _pad("2", "GND", 30.0, 5.0)]}
    roles = {"C41": BULK, "C42": BYPASS, "C43": BULK, "C44": BYPASS, "U5": "VDD_PIN"}
    clusters = {ref: CLUSTER for ref in ("C41", "C42", "C43", "C44")}
    # The plane: members on GND only, no role of the pair.
    for index in range(plane_members):
        ref = f"R{index + 1}"
        fps.append(_fp(ref))
        pads[ref] = [_pad("1", "GND", float(index), 0.0)]
    adapter = _Adapter(fps, pads, roles, clusters,
                       selected=[fps[0], fps[1]])
    return adapter


@pytest.fixture
def root(tmp_path):
    """A root config with the MCU Vdd chain (one spoke on pad 1) and the
    mcu_pair cell — written by the project's OWN writer, so the file is in the
    shape its reader expects (no hand-rolled s-expr to drift)."""
    path = tmp_path / "config.sexp"
    write_data(path, {
        "cells": {CELL: _cell_entry()},
        "chains": [{"net": NET, "name": "MCU Vdd", "anchor_ref": "U5",
                    "spokes": [{"pad": "1", "cell": CELL}]}],
    })
    return path


def _cfg(root):
    from kicadstamp.config import load_config
    cfg, ctx = load_config(str(root))
    return cfg, dict(ctx.sheet_names or {})


# ── the read ───────────────────────────────────────────────────────────────

class TestReadSpokeContext:
    def test_the_board_is_refreshed_before_the_selection_is_read(self, root):
        """Door П3.1/K.1: the poll tick is a no-op while connected, so a cached
        board would answer "is this a spoke?" with a stale role multiplicity."""
        adapter = _board()
        cfg, sheet_names = _cfg(root)
        mod.read_spoke_context(adapter, cfg, sheet_names, root)
        # At least one refresh of its own; the frame read refreshes internally
        # too (live_position._live_cluster_frame), which is the same discipline.
        assert adapter.refreshed >= 1

    def test_c3_the_plane_net_is_dropped_and_the_rail_is_kept(self, root):
        adapter = _board()
        cfg, sheet_names = _cfg(root)
        data = mod.read_spoke_context(adapter, cfg, sheet_names, root)
        assert data.problems == ()
        assert data.selection.cluster == CLUSTER
        assert [c.net for c in data.chains] == [NET]
        assert [net for net, _count in data.planes] == ["GND"]

    def test_c4_the_nearest_anchor_pad_is_offered_first(self, root):
        adapter = _board()
        cfg, sheet_names = _cfg(root)
        data = mod.read_spoke_context(adapter, cfg, sheet_names, root)
        chain = data.chains[0]
        assert chain.anchor_ref == "U5" and chain.name == "MCU Vdd"
        assert chain.pads[0].pad == "48"          # not the first pad listed (12)
        assert {p.pad for p in chain.pads} == {"12", "48"}

    def test_the_pair_and_the_spoke_evidence_are_reported(self, root):
        adapter = _board()
        cfg, sheet_names = _cfg(root)
        data = mod.read_spoke_context(adapter, cfg, sheet_names, root)
        assert data.selection.role_to_ref == {BULK: "C41", BYPASS: "C42"}
        # C43/C44 carry the same roles in the same cluster -> a spoke.
        assert data.repeated == {BULK: 2, BYPASS: 2}
        assert [c.ref for c in data.pair] == ["C41", "C42"]

    def test_the_cell_combo_offers_the_new_cell_and_the_existing_match(self, root):
        adapter = _board()
        cfg, sheet_names = _cfg(root)
        data = mod.read_spoke_context(adapter, cfg, sheet_names, root)
        assert data.cells[0].is_new is True
        assert [c.name for c in data.cells[1:]] == [CELL]
        frame = data.cells[1].frame
        assert frame is not None and frame.mirror is False
        assert (frame.origin_x_mm, frame.origin_y_mm) == pytest.approx(ORIGIN)
        assert frame.rotation_deg == pytest.approx(0.0)
        assert data.cells[1].problem is None

    def test_a_mirrored_instance_of_the_cell_is_refused(self, root):
        """ManualSpoke has no mirror field, so a spoke could never put a
        mirrored pair back where it is — the choice says so instead of writing a
        config entry the redraw cannot honour."""
        adapter = _board(layer=BoardLayer.BL_B_Cu)
        cfg, sheet_names = _cfg(root)
        data = mod.read_spoke_context(adapter, cfg, sheet_names, root)
        mirrored = next(c for c in data.cells if c.name == CELL)
        assert mirrored.frame is not None and mirrored.frame.mirror is True
        assert mirrored.problem and "MIRRORED" in mirrored.problem

    def test_the_pool_order_comes_back_as_data(self, root):
        """The dialog re-answers "what pair will this spoke get" on the UI
        thread, so the pool's CONSUMPTION order must travel as plain data."""
        adapter = _board()
        cfg, sheet_names = _cfg(root)
        data = mod.read_spoke_context(adapter, cfg, sheet_names, root)
        pools = data.chains[0].pools[CLUSTER]
        assert pools[BULK] == ["C41", "C43"]      # natural order, pool order
        assert pools[BYPASS] == ["C42", "C44"]

    def test_a_selection_that_is_not_one_pair_is_refused_with_sentences(self, root):
        adapter = _board()
        adapter._selected = [fp for fp in adapter.get_footprints()
                             if fp.ref in ("C41", "C43")]     # same role twice
        cfg, sheet_names = _cfg(root)
        data = mod.read_spoke_context(adapter, cfg, sheet_names, root)
        assert data.selection is None and data.problems
        assert any("twice" in p for p in data.problems)
        assert data.chains == () and data.cells == ()


# ── the write ──────────────────────────────────────────────────────────────

def _spoke(cell=CELL):
    return spoke_entry("48", cell, SpokeOffset(0.0, 0.0, 0.0), cluster=CLUSTER)


class TestWriteSpokeExtraction:
    def test_c12_a_changed_selection_writes_nothing(self, root):
        """Р6/С12: the dialog's verdicts describe the OLD pair, and a config
        written from them is a lie already in the file — refused, nothing
        written, and the source file is untouched."""
        before = root.read_text(encoding="utf-8")
        result = mod.write_spoke_extraction(
            _board(), root_path=root, cell_name=CELL, cell_is_new=False,
            chain_file=root, chain_entry={"net": NET, "name": "MCU Vdd",
                                          "anchor_ref": "U5",
                                          "spokes": [{"pad": "1", "cell": CELL}]},
            spoke=_spoke(), replace=False, expected_refs=("C41", "C99"))
        assert result.ok is False and result.stale is True
        assert result.messages and "C99" in result.messages[0]
        assert root.read_text(encoding="utf-8") == before

    def test_a_new_cell_and_its_spoke_are_written_to_the_right_files(self, root, monkeypatch):
        """The extraction goes to the ROOT config's cells:, the spoke into its
        chain's own file — and the spoke lands LAST in the chain."""
        monkeypatch.setattr(
            mod, "extract_template_from_selection",
            lambda adapter, name, **kw: {name: _cell_entry()})
        result = mod.write_spoke_extraction(
            _board(), root_path=root, cell_name="new_pair", cell_is_new=True,
            chain_file=root,
            chain_entry={"net": NET, "name": "MCU Vdd", "anchor_ref": "U5",
                         "spokes": [{"pad": "1", "cell": CELL}]},
            spoke=_spoke("new_pair"), replace=False,
            origin_role=BULK, expected_refs=("C41", "C42"))
        assert result.ok is True, result.messages
        assert result.cell_new is True and result.chain_written is True
        data = read_data(root)
        assert "new_pair" in data["cells"]
        spokes = data["chains"][0]["spokes"]
        assert [s["pad"] for s in spokes] == ["1", "48"]
        assert spokes[-1]["cell"] == "new_pair"
        assert spokes[-1]["cluster"] == CLUSTER

    def test_an_existing_cell_is_not_rewritten(self, root):
        result = mod.write_spoke_extraction(
            _board(), root_path=root, cell_name=CELL, cell_is_new=False,
            chain_file=root,
            chain_entry={"net": NET, "name": "MCU Vdd", "anchor_ref": "U5",
                         "spokes": [{"pad": "1", "cell": CELL}]},
            spoke=_spoke(), replace=False, expected_refs=("C41", "C42"))
        assert result.ok is True, result.messages
        data = read_data(root)
        assert list(data["cells"]) == [CELL]
        assert [s["pad"] for s in data["chains"][0]["spokes"]] == ["1", "48"]

    def test_c7_an_occupied_pad_is_refused_without_replace_and_written_with_it(self, root):
        chain = {"net": NET, "name": "MCU Vdd", "anchor_ref": "U5",
                 "spokes": [{"pad": "1", "cell": CELL},
                            {"pad": "48", "cell": "old_pair"}]}
        refused = mod.write_spoke_extraction(
            _board(), root_path=root, cell_name=CELL, cell_is_new=False,
            chain_file=root, chain_entry=chain, spoke=_spoke(), replace=False,
            expected_refs=("C41", "C42"))
        assert refused.ok is False and refused.messages
        assert "old_pair" in refused.messages[0]
        assert read_data(root)["chains"][0]["spokes"][-1]["cell"] == CELL  # untouched

        replaced = mod.write_spoke_extraction(
            _board(), root_path=root, cell_name=CELL, cell_is_new=False,
            chain_file=root,
            chain_entry={"net": NET, "name": "MCU Vdd", "anchor_ref": "U5",
                         "spokes": [{"pad": "1", "cell": CELL},
                                    {"pad": "48", "cell": "old_pair"}]},
            spoke=_spoke(), replace=True, expected_refs=("C41", "C42"))
        assert replaced.ok is True and replaced.replaced is True
        spokes = read_data(root)["chains"][0]["spokes"]
        assert [s["pad"] for s in spokes] == ["1", "48"]    # replaced IN PLACE
        assert spokes[1]["cell"] == CELL


# ── a failed two-file write (Х4 / С8) ──────────────────────────────────────

class TestFailedWriteRollback:
    """§9 X4: the cell goes into the ROOT config and the spoke into its chain's
    OWN file — two files are never atomic together, so a failure between them
    must not leave the cell behind (rollback), and a rollback that itself fails
    must SAY the config is half-updated."""

    def _chain_file(self, tmp_path):
        path = tmp_path / "chains.sexp"
        write_data(path, {"chains": [{"net": NET, "name": "MCU Vdd",
                                      "anchor_ref": "U5",
                                      "spokes": [{"pad": "1", "cell": CELL}]}]})
        return path

    def _write(self, root, chain_file):
        return mod.write_spoke_extraction(
            _board(), root_path=root, cell_name="new_pair", cell_is_new=True,
            chain_file=chain_file,
            chain_entry={"net": NET, "name": "MCU Vdd", "anchor_ref": "U5",
                         "spokes": [{"pad": "1", "cell": CELL}]},
            spoke=_spoke("new_pair"), replace=False,
            origin_role=BULK, expected_refs=("C41", "C42"))

    def test_c8_a_failed_chain_write_rolls_the_cell_back(self, root, tmp_path,
                                                         monkeypatch):
        """С8/М8: the chain write fails with OSError AFTER the cell landed —
        both files must be back at their pre-write content, and the message says
        so instead of the old bare "Write failed"."""
        chain_file = self._chain_file(tmp_path)
        monkeypatch.setattr(mod, "extract_template_from_selection",
                            lambda adapter, name, **kw: {name: _cell_entry()})

        def boom(*_a, **_k):
            raise OSError("disk full")
        monkeypatch.setattr(mod, "upsert_list_entry", boom)
        before = read_data(root)

        result = self._write(root, chain_file)

        assert result.ok is False
        assert "disk full" in result.messages[0]
        assert "nothing was written" in result.messages[0]
        assert read_data(root) == before
        assert "new_pair" not in read_data(root)["cells"]
        assert read_data(chain_file)["chains"] == before["chains"]

    def test_c8_a_failed_rollback_names_the_half_state(self, root, tmp_path,
                                                       monkeypatch):
        """When the cell cannot be taken back, the message must NAME the half
        state: the user has to know the config already changed."""
        chain_file = self._chain_file(tmp_path)
        monkeypatch.setattr(mod, "extract_template_from_selection",
                            lambda adapter, name, **kw: {name: _cell_entry()})

        def boom(*_a, **_k):
            raise OSError("disk full")
        monkeypatch.setattr(mod, "upsert_list_entry", boom)

        real_write = mod.write_data
        calls = []

        def failing_restore(path, data):
            calls.append(Path(path))
            if len(calls) == 2:          # the rollback's write of the root
                raise OSError("no space left")
            return real_write(path, data)

        monkeypatch.setattr(mod, "write_data", failing_restore)

        result = self._write(root, chain_file)

        assert result.ok is False
        assert "new_pair" in result.messages[0]
        assert "half-updated" in result.messages[0]
        assert "new_pair" in read_data(root)["cells"]   # it really is half-updated

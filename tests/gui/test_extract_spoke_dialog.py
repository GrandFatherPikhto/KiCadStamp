#!/usr/bin/env python3
"""Guards of the "Extract spoke..." DIALOG and its DockHub wiring (stage 5 of the
spoke work, plan_2026_09_17_spoke_s5_extract_spoke.md §2 Р3/Р4, §3 Door, §5).

Offscreen Qt (tests/gui/conftest.py), no KiCad: the dialog renders records that
gui/docks/extract_spoke.py's worker read produced, so these guards pin the three
outcomes of the pool rule and the two refusals that must never be overridable:

  * С2  — unique roles: an ordinary cluster, OK locked and "Extract cluster..."
          named, instead of a silent mode switch;
  * Ф8.1 — the pool gives exactly the selected pair: OK free;
  * С10 — another pair: OK locked until the explicit "Components may swap"
          checkbox, with both pairs and the owner in the sentence;
  * С11 — the pool is drained: OK locked, and the checkbox does not help;
  * С7  — an occupied pad: OK locked until "Replace";
  * a mirrored cell candidate is refused BY NAME (ManualSpoke has no mirror);
  * С8  — after a successful write DockHub remembers the identified pair (Р8),
          which is what makes the cell editor open on THIS pair;
  * С9  — a busy socket refuses BEFORE the worker starts.

Widgets are given a Qt parent (the project's own rule: a parentless widget is
collected and, on Windows, takes the whole run down).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMainWindow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.dock_hub import DockHub                                            # noqa: E402
from gui.cell_edit_context import remembered_cell_refs                     # noqa: E402
from gui.docks.extract_spoke import (                                      # noqa: E402
    CellChoice, CellFrame, ChainChoice, PairComponent, SpokeWriteResult,
    SpokeContext,
)
from gui.docks.extract_spoke_dialog import ExtractSpokeDialog              # noqa: E402
from kicadstamp.spoke_extraction import PadPoint, SpokeSelection           # noqa: E402

NET = "+3V3_VDD"
CLUSTER = "MCU_PWR_BANK"
BULK = "C_OUT_BULK"
BYPASS = "C_OUT_BYPASS"
CELL = "mcu_pair"
SPOKE_PAD = "48"
ANCHOR = "U5"


@pytest.fixture
def parent(qapp):
    """A Qt parent for every dialog built here — see the module docstring."""
    window = QMainWindow()
    yield window
    window.deleteLater()


def _pair():
    return (PairComponent(ref="C41", role=BULK, x_mm=11.0, y_mm=5.0,
                          pads=(PadPoint(pad="1", x_mm=11.0, y_mm=5.0),)),
            PairComponent(ref="C42", role=BYPASS, x_mm=9.0, y_mm=5.0,
                          pads=(PadPoint(pad="1", x_mm=9.0, y_mm=5.0),)))


def _context(*, repeated=None, spokes=(), pools=None, cells=None, problems=(),
             selection=None, chains=None):
    if selection is None:
        selection = SpokeSelection(refs=("C41", "C42"), cluster=CLUSTER,
                                   role_to_ref={BULK: "C41", BYPASS: "C42"})
    if pools is None:
        pools = {CLUSTER: {BULK: ["C41", "C43"], BYPASS: ["C42", "C44"]}}
    if cells is None:
        cells = (CellChoice(name="", is_new=True),
                 CellChoice(name=CELL, is_new=False,
                            frame=CellFrame(origin_x_mm=10.0, origin_y_mm=5.0,
                                            rotation_deg=0.0, mirror=False)))
    if chains is None:
        chains = (ChainChoice(
            net=NET, name="MCU Vdd",
            entry={"net": NET, "name": "MCU Vdd", "anchor_ref": ANCHOR,
                   "spokes": [dict(s) for s in spokes]},
            file=None, anchor_ref=ANCHOR, anchor_role=None, anchor_cluster=None,
            anchor_sheet=None,
            # The anchor's pads on the rail, nearest first: 48 (the pair's pin)
            # then the further ones a chain may already use.
            pads=(PadPoint(pad=SPOKE_PAD, x_mm=11.0, y_mm=4.5),
                  PadPoint(pad="12", x_mm=40.0, y_mm=5.0),
                  PadPoint(pad="7", x_mm=45.0, y_mm=5.0),
                  PadPoint(pad="9", x_mm=50.0, y_mm=5.0)),
            spokes=tuple(dict(s) for s in spokes), pools=pools),)
    return SpokeContext(problems=tuple(problems), selection=selection,
                        repeated=dict(repeated or {}), pair=_pair(),
                        cells=tuple(cells), chains=tuple(chains))


def _cfg():
    return SimpleNamespace(cells={
        CELL: SimpleNamespace(components=[
            SimpleNamespace(role=BULK), SimpleNamespace(role=BYPASS)])})


def _dialog(parent, tmp_path, data, cfg=None):
    dialog = ExtractSpokeDialog(None, None, tmp_path / "config.sexp",
                               parent=parent)
    dialog.set_context(data, cfg if cfg is not None else _cfg())
    return dialog


# ── the criterion and the three outcomes of the pool (Ф8/Р3) ───────────────

class TestOutcomesGateOK:
    def test_c2_unique_roles_are_an_ordinary_cluster_and_OK_is_locked(self, parent, tmp_path):
        dialog = _dialog(parent, tmp_path, _context(repeated={}))
        assert dialog.ok_allowed is False
        assert "ordinary cluster" in dialog.criterion_label.text()
        assert "Extract cluster" in dialog.outcome_label.text()

    def test_f8_1_the_matching_pair_leaves_OK_free(self, parent, tmp_path):
        dialog = _dialog(parent, tmp_path, _context(repeated={BULK: 2, BYPASS: 2}))
        assert dialog.ok_allowed is True
        assert "will get C41, C42" in dialog.outcome_label.text()
        assert "NOT the selected pair" not in dialog.outcome_label.text()

    def test_c10_another_pair_needs_the_swap_checkbox(self, parent, tmp_path):
        """С10/М10: the pool gives this spoke C43/C44 because the pair it was
        read from stands on pad 12 — OK must wait for the explicit checkbox, and
        the sentence must name both pairs and the owning spoke."""
        dialog = _dialog(parent, tmp_path, _context(
            repeated={BULK: 2, BYPASS: 2},
            spokes=[{"pad": "12", "cell": CELL, "cluster": CLUSTER}]))
        assert dialog.ok_allowed is False
        assert dialog.swap_check.isHidden() is False
        text = dialog.outcome_label.text()
        assert "C43, C44" in text and "C41, C42" in text
        assert "NOT the selected pair" in text
        assert "MCU Vdd" in text and "pad 12" in text
        dialog.swap_check.setChecked(True)
        assert dialog.ok_allowed is True

    def test_c11_a_drained_pool_locks_OK_for_good(self, parent, tmp_path):
        """С11/М11: two pairs, two spokes — a third spoke has nothing to place,
        so no checkbox may unlock it."""
        dialog = _dialog(parent, tmp_path, _context(
            repeated={BULK: 2, BYPASS: 2},
            spokes=[{"pad": "12", "cell": CELL, "cluster": CLUSTER},
                    {"pad": "7", "cell": CELL, "cluster": CLUSTER}]))
        assert dialog.ok_allowed is False
        assert "No free pair left" in dialog.outcome_label.text()
        # The numbers name what EXISTS: two pairs, two consuming spokes. The
        # refused spoke is what "for one more spoke" says.
        assert "2 pairs, 2 spokes" in dialog.outcome_label.text()
        assert dialog.swap_check.isHidden() is True
        dialog.swap_check.setChecked(True)
        assert dialog.ok_allowed is False

    def test_c7_an_occupied_pad_needs_replace(self, parent, tmp_path):
        """С7/М7 at the dialog: pad 48 already carries a spoke, so OK waits for
        "Replace" — and the checkbox names the cell it would overwrite."""
        dialog = _dialog(parent, tmp_path, _context(
            repeated={BULK: 2, BYPASS: 2},
            spokes=[{"pad": SPOKE_PAD, "cell": "old_pair", "cluster": CLUSTER}]))
        assert dialog.ok_allowed is False
        # isHidden(), not isVisible(): the dialog is never shown in a test, and a
        # child of an unshown parent reports isVisible() == False either way.
        assert dialog.replace_check.isHidden() is False
        assert "old_pair" in dialog.replace_check.text()
        assert "Replace" in dialog.status_label.text()
        dialog.replace_check.setChecked(True)
        assert dialog.ok_allowed is True

    def test_a_mirrored_cell_candidate_is_refused_by_name(self, parent, tmp_path):
        """ManualSpoke has no mirror field: a mirrored instance could never be
        reproduced by the redraw, so the choice says so and OK stays locked."""
        mirrored = CellChoice(name=CELL, is_new=False,
                              frame=CellFrame(10.0, 5.0, 0.0, mirror=True),
                              problem="this cell's instance stands MIRRORED, and a "
                                      "spoke has no mirror field")
        dialog = _dialog(parent, tmp_path, _context(
            repeated={BULK: 2, BYPASS: 2}, cells=(CellChoice(name="", is_new=True), mirrored)))
        dialog.cell_combo.setCurrentIndex(1)
        assert dialog.ok_allowed is False
        assert "MIRRORED" in dialog.outcome_label.text()
        assert "unusable" in dialog.cell_combo.currentText()

    def test_an_empty_selection_is_reported_not_guessed(self, parent, tmp_path):
        # Built directly: _context() always fills a usable selection, and this
        # case is exactly the read that came back with nothing to work with.
        data = SpokeContext(problems=("nothing is selected",), selection=None)
        dialog = _dialog(parent, tmp_path, data)
        assert dialog.ok_allowed is False
        assert "nothing is selected" in dialog.criterion_label.text()


# ── what OK hands to the worker ────────────────────────────────────────────

class TestWriteRequest:
    def test_the_request_names_the_records_the_dialog_showed(self, parent, tmp_path):
        dialog = _dialog(parent, tmp_path, _context(repeated={BULK: 2, BYPASS: 2}))
        request = dialog.write_request()
        assert request is not None
        assert request["cell_name"] == "mcu_pwr_bank"
        assert request["cell_is_new"] is True
        assert request["spoke"]["pad"] == SPOKE_PAD
        assert request["spoke"]["cluster"] == CLUSTER
        assert request["chain_entry"]["net"] == NET
        assert request["expected_refs"] == ("C41", "C42")
        assert request["replace"] is False

    def test_an_existing_cell_uses_its_own_frame_for_the_shift(self, parent, tmp_path):
        """Ф2: with an EXISTING cell the shift is the frame's origin minus the
        pad (10−11, 5−4.5), while a NEW cell uses the pair's own origin role."""
        dialog = _dialog(parent, tmp_path, _context(repeated={BULK: 2, BYPASS: 2}))
        dialog.cell_combo.setCurrentIndex(1)          # the existing mcu_pair
        spoke = dialog.write_request()["spoke"]
        assert spoke["shift_x_mm"] == pytest.approx(-1.0)
        assert spoke["shift_y_mm"] == pytest.approx(0.5)
        assert spoke["cell"] == CELL

    def test_the_gate_is_authoritative(self, parent, tmp_path):
        """A request exists only when OK is allowed — the worker is never handed
        something the dialog refused to confirm."""
        dialog = _dialog(parent, tmp_path, _context(
            repeated={BULK: 2, BYPASS: 2},
            spokes=[{"pad": SPOKE_PAD, "cell": "old_pair", "cluster": CLUSTER}]))
        assert dialog.write_request() is None
        dialog.replace_check.setChecked(True)
        assert dialog.write_request() is not None


# ── the DockHub half: Р8 (С8) and the door guard (С9) ──────────────────────

class _Tree:
    def __init__(self):
        self.refreshed = 0

        class _Signal:
            def emit(self):
                pass

        self.graph_changed = _Signal()

    def refresh(self):
        self.refreshed += 1


class _DialogStub:
    def __init__(self, identification=None):
        self._identification = identification
        self.status = None
        self.accepted = 0

    def identification(self):
        return self._identification

    def show_status(self, text, error=False):
        self.status = text

    def show_success(self, text):
        self.status = text

    def accept(self):
        self.accepted += 1


def _hub(dialog, root_path, adapter=None):
    """A DockHub stand-in carrying ONLY what these two methods touch."""
    hub = SimpleNamespace(
        _spoke_dialog=dialog,
        _spoke_request={"chain_entry": {"net": NET, "name": "MCU Vdd",
                                        "anchor_ref": ANCHOR, "spokes": []}},
        root_metadata_dock=SimpleNamespace(root_path=root_path),
        config_tree_dock=_Tree(),
        main_window=SimpleNamespace(
            connection=SimpleNamespace(board=SimpleNamespace(adapter=adapter))),
    )
    hub._finish_spoke_write = DockHub._finish_spoke_write.__get__(hub)
    hub._read_spoke_context = DockHub._read_spoke_context.__get__(hub)
    return hub


class TestHubWiring:
    def test_c8_after_OK_the_identified_pair_is_remembered(self, tmp_path, monkeypatch):
        """С8/М8: the write is only half the job — without the remembered pair
        the cell editor opens on a spoke cluster and cannot say WHICH pair, which
        is the whole bug stage 1 fixed."""
        root = tmp_path / "config.sexp"
        root.write_text("(kicadstamp-config)\n", encoding="utf-8")
        data = _context(repeated={BULK: 2, BYPASS: 2})
        dialog = ExtractSpokeDialog(None, None, root)      # no parent: never shown
        dialog.set_context(data, _cfg())
        stub = _DialogStub(identification=dialog.identification())
        hub = _hub(stub, root)

        hub._finish_spoke_write(SpokeWriteResult(
            ok=True, cell_name="mcu_pwr_bank", cell_new=True, pad=SPOKE_PAD,
            chain_written=True))

        assert remembered_cell_refs(root, "mcu_pwr_bank") == {BULK: "C41",
                                                              BYPASS: "C42"}
        assert hub.config_tree_dock.refreshed == 1
        assert stub.accepted == 1
        assert "mcu_pwr_bank" in (stub.status or "")
        dialog.deleteLater()

    def test_c8_a_failed_write_remembers_nothing(self, tmp_path):
        root = tmp_path / "config.sexp"
        root.write_text("(kicadstamp-config)\n", encoding="utf-8")
        stub = _DialogStub(identification=None)
        hub = _hub(stub, root)
        hub._finish_spoke_write(SpokeWriteResult(ok=False, stale=True,
                                                 messages=("the selection changed",)))
        assert remembered_cell_refs(root, "mcu_pwr_bank") is None
        assert hub.config_tree_dock.refreshed == 0
        assert stub.accepted == 0
        assert stub.status == "the selection changed"

    def test_c9_a_busy_socket_starts_no_read(self, tmp_path, monkeypatch):
        """С9/М9: interleaving a second request into the shared kipy socket is
        how "Operation canceled" starts — refuse and say so, queue nothing."""
        started: list = []
        monkeypatch.setattr("gui.worker.start_long_op",
                            lambda *a, **kw: started.append(a))
        monkeypatch.setattr("gui.worker.socket_busy", lambda connection: True)
        stub = _DialogStub()
        # A path that does NOT exist, on purpose: with the guard gone the flow
        # would fall through to load_config and report ITS failure instead —
        # which is exactly how this guard was decoration at first (the error
        # message carried this test's own tmp_path, and its name contains
        # "busy"), so the message is compared EXACTLY, not by substring.
        hub = _hub(stub, tmp_path / "config.sexp", adapter=object())
        hub._read_spoke_context()
        assert started == []
        assert stub.status == "the board is busy — press “Refresh” in a moment"

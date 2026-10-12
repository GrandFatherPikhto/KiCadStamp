# kicadstamp/diagnostics/deepseek_probe_duplicate_refdes_paths_2026_10_12.py
"""Probe: which paths OUTSIDE the apply pipeline are vulnerable to a duplicated
refdes (plan_2026_10_12_duplicate_refdes_guard, Т5 / Т3).

WHY. The guard sits in ONE door — ``ApplyPipeline`` — and refuses a record whose
resolved refdes stands on the board twice. The plan asks for the OTHER paths,
the ones that do not pass through that door, to be MEASURED and NAMED (fault, or
not) rather than silently assumed safe. A negative result is not turned into a
positive fact (rule 39): each line below is a measurement on a FAKE board, with
the exact call the path makes.

WHAT IS MEASURED (no board, no Qt, no writes):
  1. "Select cell" (gui/select_cell_copper.py) — ``adapter.get_footprint(ref)``
     BY REFDES: with two C35 the call can only answer one of them, so the path IS
     vulnerable (it highlights a coin flip).
  2. Explode (kicadstamp/explode.py) — ``_append_move`` addresses by UUID
     (``Move.uuid``; ``ref`` rides along for the report only): NOT vulnerable to
     moving the wrong footprint when the two copies have different uuids.
  3. Board overlay (gui/dock_hub.py::highlight_selection) —
     ``by_ref = {s.ref: s for s in snapshot}`` BY REFDES: the dict keeps ONE
     entry per refdes, so the path IS vulnerable (the last copy wins).

Run:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_probe_duplicate_refdes_paths_2026_10_12
"""
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.explode import _append_move


def _fp(ref, uuid, x_mm, y_mm, chain=("sheet", "own")):
    return Footprint(ref=ref, uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm),
                     angle_deg=0.0, layer=BoardLayer.BL_F_Cu,
                     sheet_path_uuids=tuple(chain))


class _Board:
    """The ONE adapter seam every measured path reads: ``get_footprint(ref)`` —
    the LIVE adapter answers by refdes, so with two copies it returns ONE."""

    def __init__(self, footprints):
        self._footprints = list(footprints)

    def get_footprint(self, ref):
        for fp in self._footprints:
            if fp.ref == ref:
                return fp
        return None


def _measured_select_cell(board):
    """gui/select_cell_copper.py:49 — ``fp = adapter.get_footprint(ref)``."""
    fp = board.get_footprint("C35")
    return None if fp is None else fp.uuid


def _measured_explode(board):
    """kicadstamp/explode.py:440 — the move carries the UUID it shifts."""
    moves: list = []
    _append_move(moves, board.get_footprint("C35"), 1000, 2000)
    return [(m.uuid, m.ref) for m in moves]


def _measured_overlay(snapshot):
    """gui/dock_hub.py:1211 — ``{s.ref: s for s in self.connection.snapshot}``."""
    by_ref = {s.ref: s for s in snapshot}
    return None if "C35" not in by_ref else by_ref["C35"].fp.uuid


def main() -> int:
    real = _fp("C35", "uuid-real", 203.0, 55.0)
    orphan = _fp("C35", "uuid-orphan", 208.075, 57.005, chain=())
    board = _Board([real, orphan])
    snapshot = [type("S", (), {"ref": fp.ref, "fp": fp})() for fp in (real, orphan)]

    print("duplicated refdes on the fake board: C35 x2 "
          f"({real.uuid} at 203.000, 55.000 mm; {orphan.uuid} at 208.075, 57.005 mm)\n")

    selected = _measured_select_cell(board)
    print(f"1. Select cell  -> adapter.get_footprint('C35') = {selected!r}")
    print("   VULNERABLE: the path addresses by REFDES, so it can only reach one copy.\n")

    moves = _measured_explode(board)
    print(f"2. Explode      -> moves addressed by uuid: {moves}")
    print("   NOT vulnerable: the move identity is the UUID; `ref` rides for the "
          "report only.\n")

    overlaid = _measured_overlay(snapshot)
    print(f"3. Board overlay-> {{ref: s}}['C35'] = {overlaid!r}")
    print("   VULNERABLE: the dict keeps ONE entry per refdes (the last copy wins).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

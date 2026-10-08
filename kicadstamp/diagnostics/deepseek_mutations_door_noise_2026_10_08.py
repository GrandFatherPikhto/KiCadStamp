# kicadstamp/diagnostics/deepseek_mutations_door_noise_2026_10_08.py
"""Acceptance mutations for plan_2026_10_08_door_noise_on_connect.md — the two
red "Reading the live board from the UI thread without a sign" lines Denis saw on
EVERY connect (gui/docks/cell_editor.py:1755, gui/dock_hub.py:3285), DeepSeek,
2026-10-08.

Grown from deepseek_mutations_entities_under_cells_2026_10_08.py, which grew from
deepseek_mutations_select_enclosed_copper_2026_10_05.py (rule 38) — the SAME
machinery, imported under its own name because `main()` reads `MUTATIONS` from
ITS OWN globals: the basename-resolved test list under tests/, `_drop_pyc`, the
`original.count(old) != 1` refusal (a non-unique template is a miss, not a kill),
ПРОМАХ on zero reds, and a control that MUST survive.

WHAT IS BEING PROVEN. The guards are:
  * tests/gui/test_board_door_offenders.py — the connect path with the door
    ARMED in the rig's mode (raise): the two offenders of 08.10, the overlay
    worker's adapter (taken on the worker's OWN thread), and the six CellDock
    buttons that follow the connection;
  * tests/gui/test_overlay_markers.py — WHICH function travels to the worker and
    WHAT it is handed (the row the same plan strengthened).

  * M1 `CellDock._update_refresh_enabled` asks the door again  -> refusal
  * M2 `reconcile_overlay` takes the handle on the UI thread   -> refusal
  * M3 the worker is handed None instead of the connection     -> no adapter
  * K1 a cosmetic comment (control)                            -> MUST survive

M1/M2 are the two lines of the DEFECT, restored one at a time: each one alone
used to be a red Log line on every connect.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_door_noise_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

# Basenames, resolved under tests/ by the rig itself (rule 38: a blind or
# ambiguous name is a finding, never a silent skip).
DOOR = ["test_board_door_offenders.py"]
MARKERS = ["test_overlay_markers.py"]

EDITOR = "gui/docks/cell_editor.py"
HUB = "gui/dock_hub.py"

ROWS = [
    # 1 — the presence check opens the door again (the cell_editor.py:1755 line).
    ("M1 CellDock asks the DOOR, not the connection", EDITOR,
     "        connected = bool(getattr(connection, \"is_connected\", False))\n",
     "        board = getattr(connection, \"board\", None)  # MUTATION\n"
     "        connected = board is not None\n",
     "die", DOOR, ()),
    # 2 — the UI thread captures the adapter again (the dock_hub.py:3285 line).
    # The marker row joins the test list because it asserts WHAT travels to the
    # worker: under this mutation the adapter — not the connection — is handed
    # over, so that row reddens too.
    ("M2 UI thread takes the handle, not the worker", HUB,
     "        if not getattr(connection, \"is_connected\", False) \\\n"
     "                or getattr(connection, \"long_op_active\", False):\n",
     "        board = getattr(connection, \"board\", None)  # MUTATION\n"
     "        adapter = getattr(board, \"adapter\", None) if board is not None else None\n"
     "        if adapter is None or getattr(connection, \"long_op_active\", False):\n",
     "die", DOOR + MARKERS, ()),
    # 3 — the worker is handed None, so no adapter ever arrives.
    ("M3 worker handed None instead of the connection", HUB,
     "            lambda _message: self.refresh_explode_state(), connection)\n",
     "            lambda _message: self.refresh_explode_state(), None)  # MUTATION\n",
     "die", DOOR, ()),
    # control — a cosmetic comment MUST survive.
    ("K1 cosmetic comment (control)", HUB,
     "def reconcile_overlay_worker(connection) -> dict:",
     "def reconcile_overlay_worker(connection) -> dict:  # control",
     "survive", DOOR, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()

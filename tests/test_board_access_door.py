# tests/test_board_access_door.py
"""Э4 of plan_2026_09_13_board_access_door: the guards for the ONE door to the
live board.

Four independent things are pinned here:

  1. the adapter's private ``_board`` is never reached into outside
     ``kicadstamp/kicad/adapter.py`` and ``kicadstamp/diagnostics/`` — a
     source-tree watchdog, the same genre as tools/_check_i18n.py (Э4.1);
  2. the four new adapter reads delegate to the live kipy handle (Э4.2);
  3. ``connection.board`` is a transparent property — reads and writes behave
     exactly like the attribute it replaced, and the value lands in ``_board``
     (Э4.3);
  4. the read probe is OFF by default (reading the board makes no record) and,
     when diagnostics turns it on, records thread + call site (Э4.4/Э4.5).

No KiCad and no Qt: the adapter reads run against a MagicMock handle, and the
connection's fake board is a plain object.
"""
import json
import re
import threading

from pathlib import Path
from unittest.mock import MagicMock

from gui import connection as connection_mod
from gui.connection import BoardConnection
from kicadstamp.kicad.adapter import KiCadBoardAdapter as Adapter


# ── Э4.1: the source-tree watchdog ──────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHIP_DIRS = ("gui", "kicadstamp")
_ALLOWED_FILES = {(_REPO_ROOT / "kicadstamp" / "kicad" / "adapter.py").resolve()}
_ALLOWED_DIRS = ((_REPO_ROOT / "kicadstamp" / "diagnostics").resolve(),)

# `adapter._board` (or any other object's `_board`), and the string form used by
# `getattr(x, "_board")` — the exact bypass found in gui/docks/cell_editor.py
# before Э1 closed it. `self._board` is NOT matched: that is BoardConnection's
# own storage for `connection.board` (Э2), a different attribute entirely.
_FORBIDDEN = re.compile(r"(?<!self)\._board\b|[\"']_board[\"']")


def _scanned_files():
    for top in _SHIP_DIRS:
        for path in (_REPO_ROOT / top).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            resolved = path.resolve()
            if resolved in _ALLOWED_FILES:
                continue
            if any(str(resolved).startswith(str(d)) for d in _ALLOWED_DIRS):
                continue
            yield path


def test_private_board_is_not_reached_into_outside_the_adapter_and_probes():
    """Э4.1 — the adapter's private `_board` stops escaping the adapter.

    Mutation check: re-add `return adapter._board` to gui/board_overlay.py (or
    any `getattr(adapter, "_board")`) and this test fails, naming file and line.
    """
    offenders = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8")
        for match in _FORBIDDEN.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{path.relative_to(_REPO_ROOT).as_posix()}:{line}: "
                f"{match.group(0)!r}")
    assert offenders == [], (
        "the adapter's private `_board` is reached into outside "
        "kicadstamp/kicad/adapter.py and kicadstamp/diagnostics/ — go through "
        "the adapter's own methods (plan_2026_09_13_board_access_door Э1):\n  "
        + "\n  ".join(offenders))


# ── Э4.2: the four reads delegate to the live handle ────────────────────────

def test_adapter_layer_and_shape_reads_delegate_to_the_board():
    """The four reads return exactly what the kipy handle returns — no mapping,
    no caching (that is the whole point: they are a DOOR, not a layer)."""
    adapter = Adapter.__new__(Adapter)
    board = MagicMock()
    adapter._board = board
    board.get_layer_name.return_value = "User.KiCadStamp"
    board.get_enabled_layers.return_value = [3, 4, 34]
    board.get_visible_layers.return_value = [3, 34]
    board.get_shapes.return_value = ["shape-1", "shape-2"]

    assert adapter.get_layer_name(5) == "User.KiCadStamp"
    board.get_layer_name.assert_called_once_with(5)
    assert adapter.get_enabled_layers() == [3, 4, 34]
    assert adapter.get_visible_layers() == [3, 34]
    assert adapter.get_shapes() == ["shape-1", "shape-2"]


def test_interface_declares_the_four_reads():
    """They must be on the seam, so every adapter surface is checked by name."""
    for name in ("get_layer_name", "get_enabled_layers", "get_visible_layers",
                 "get_shapes"):
        assert hasattr(Adapter, name), name


# ── Э4.3: connection.board is a transparent property ────────────────────────

def test_board_property_is_transparent_for_read_and_write():
    connection = BoardConnection()
    assert connection.board is None
    assert connection.is_connected is False

    fake = object()
    connection.board = fake
    assert connection.board is fake            # the read survives the rename
    assert connection._board is fake           # storage really moved to _board
    assert connection.is_connected is True

    connection.board = None
    assert connection.board is None
    assert connection._board is None
    assert connection.is_connected is False


def test_existing_internal_reads_still_see_the_board():
    """`self.board` reads inside connection.py must keep working through the
    property — refresh()/disconnect() use them."""
    connection = BoardConnection()
    board = MagicMock()
    connection.board = board

    assert connection.refresh() is None        # board.refresh() ran via the getter
    board.refresh.assert_called_once()
    assert connection.snapshot_version == 1    # _rebuild_snapshot used self.board

    connection.disconnect()
    assert connection.board is None


# ── Э4.4/Э4.5: the read probe ───────────────────────────────────────────────

def test_read_probe_is_off_by_default():
    """Э4.4 — with no probe installed, reading the board must do NOTHING.

    Mutation check: drop the `if probe is not None` guard in the getter and this
    test fails (it would try to call None)."""
    assert connection_mod.board_read_probe is None
    connection = BoardConnection()
    connection.board = object()
    assert connection.board is not None        # a plain read, no work, no crash


def test_disabled_probe_records_no_reads(tmp_path):
    """Э4.4 — once diagnostics disables the probe, further reads record nothing.

    Mutation check: remove the getter's flag test and the "after" read is
    recorded too, so `after == before` fails."""
    from kicadstamp.diagnostics import board_read_probe

    path = tmp_path / "reads.jsonl"
    board_read_probe.enable(str(path))
    try:
        connection = BoardConnection()
        connection.board = object()
        _ = connection.board                   # recorded (probe is ON)
    finally:
        board_read_probe.disable()

    before = path.read_text(encoding="utf-8").count("\n")
    assert before == 1

    connection.board = object()
    _ = connection.board                       # NOT recorded (probe is OFF)
    after = path.read_text(encoding="utf-8").count("\n")
    assert after == before == 1


def test_enabled_probe_records_thread_and_call_site(tmp_path):
    """Э4.5 — the enabled probe writes the thread name and the CALLER's site."""
    from kicadstamp.diagnostics import board_read_probe

    path = tmp_path / "reads.jsonl"
    board_read_probe.enable(str(path))
    try:
        connection = BoardConnection()
        connection.board = object()
        _ = connection.board
    finally:
        board_read_probe.disable()

    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["thread"] == threading.current_thread().name
    # The recorded site is the CALLER of `connection.board` — this test file,
    # never the getter or the probe module itself.
    assert "test_board_access_door.py:" in rows[0]["site"]


def test_probe_ignores_the_connections_own_internal_reads(tmp_path):
    """The connection's own `self.board` reads (refresh/disconnect) are not
    CONSUMERS of the door, so they are not recorded — otherwise they would show
    up as UI-thread noise in the report."""
    from kicadstamp.diagnostics import board_read_probe

    path = tmp_path / "reads.jsonl"
    board_read_probe.enable(str(path))
    try:
        connection = BoardConnection()
        connection.board = MagicMock()
        connection.disconnect()            # internal reads only
    finally:
        board_read_probe.disable()

    assert path.read_text(encoding="utf-8") == ""

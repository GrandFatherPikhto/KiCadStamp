"""Acceptance probe for the reload-store entry (35ab638): the reprojection is a
THIRD snapshot rebuilder, and unlike the other two it runs on the UI thread with
no socket gate.

Written by Claude during the acceptance of plan_2026_09_24_reload_store_snapshot.
Built from tests/override_store_board_fixtures.py (rule 38 — from the existing
rig), so the stack under test is the production composition:
Wire -> CachedAdapter -> FieldOverrideAdapter -> Board -> BoardConnection.

WHAT THIS IS ABOUT. Before the entry, BoardConnection._rebuild_snapshot() had
exactly two callers, both reached from a WORKER thread and both serialised by
the shared-socket token: gui/main_window.py's poll worker and gui/worker.py's
_refresh_snapshot_worker (via start_long_op). The entry adds a third, reached
from the UI thread through DockHub._on_overrides_written -> reload_store, and
that path asks neither long_op_active nor socket_busy — measured, zero
occurrences in the method. So two rebuilders can now run at once.

Two questions, both answered by measurement rather than by argument:

  A. can two concurrent rebuilders LOSE a snapshot_version increment?
     It matters because snapshot_version is documented as "a cheap, stable
     identity for 'the board data changed since my last look'" and is the dedup
     key of the selection tick (gui/main_window.py) — a lost bump is a repaint
     that never happens.

  B. can a snapshot come back TORN — some values resolved before a concurrent
     Board.refresh(), some after? design_2026_09_22_cache_first_board_access §5a
     requires the swap to be ATOMIC: "UI must see either wholly the old or
     wholly the new, never a half-built one."

     **B is an EMPTY CELL, by construction of the rig, and says so when run.**
     wired_board() composes exactly ONE footprint, and tearing is not expressible
     with one: it needs at least two, so that one can resolve before the intruding
     refresh() and the other after. The interleaving below IS forced and the
     intruder DOES run (refresh_board rises in the counters), but the single
     value it can report is necessarily self-consistent. Answering B needs a
     two-footprint variant of the rig — not written here, and named as missing
     rather than left to look green.

READ THE RESULT CAREFULLY (rule 39). A green A is "my probe did not catch a lost
increment in N rounds under CPython's GIL", NOT "there is no race".
"""
import pathlib
import sys
import tempfile
import threading

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from override_store_board_fixtures import wired_board  # noqa: E402

ROUNDS = 2000


def probe_a_lost_version_increment(rounds: int = ROUNDS):
    """Two threads rebuild the snapshot; count how many increments survive.

    `self._snapshot_version += 1` is a read-modify-write, so it is NOT atomic
    across bytecodes — two threads landing in it can collapse two increments
    into one."""
    with tempfile.TemporaryDirectory() as tmp:
        wired = wired_board(pathlib.Path(tmp))
        before = wired.connection.snapshot_version

        def rebuild():
            for _ in range(rounds):
                wired.connection._rebuild_snapshot()

        threads = [threading.Thread(target=rebuild) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return rounds * 2, wired.connection.snapshot_version - before


def probe_b_torn_snapshot():
    """Force the exact interleaving instead of hoping for it: the other
    rebuilder's Board.refresh() lands in the MIDDLE of select(), between two
    per-footprint resolutions. Deterministic, so the answer is about the shape
    of the code rather than about luck."""
    with tempfile.TemporaryDirectory() as tmp:
        wired = wired_board(pathlib.Path(tmp))
        wired.role_in_snapshot()                  # warm the caches, as a poll would
        wired.write_role("R_WRITTEN")             # a pane records and saves
        wired.rebind_store()                      # the reload's own first half

        board = wired.board
        board.forget_role_cluster_values()

        real_role = board._role
        landed = {"done": False}

        def role_with_intruder(fp):
            value = real_role(fp)
            if not landed["done"]:
                landed["done"] = True
                board.refresh()                   # the worker thread lands HERE
            return value

        board._role = role_with_intruder
        try:
            snapshot = board.select()
        finally:
            board._role = real_role
        return [s.role for s in snapshot], wired.asks


def main() -> None:
    expected, seen = probe_a_lost_version_increment()
    lost = expected - seen
    print(f"A. snapshot_version: ожидалось +{expected}, получено +{seen} — "
          f"{'ПОТЕРИ ЕСТЬ, ' + str(lost) + ' потеряно' if lost else 'потерь не поймано'}")
    print("   (зелёное здесь означает «не поймалось этим зондом», а не «гонки нет»)")

    roles, asks = probe_b_torn_snapshot()
    print(f"B. select() с вклинившимся refresh(): роли {roles}, вопросы к плате {asks}")
    print("   ПУСТАЯ КЛЕТКА: в оснастке ОДИН футпринт, а рваный снимок требует "
          "минимум двух —")
    print("   одного до вклинившегося refresh(), другого после. Вклинивание "
          "состоялось (refresh_board вырос),")
    print("   но одно значение не может быть несогласованным само с собой. "
          "Нужен двухфутпринтовый вариант оснастки.")


if __name__ == "__main__":
    main()

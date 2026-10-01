# tests/fakes/write_later.py
"""A write a reader cannot mistake for the PREVIOUS one — a later write, the way
any real second write looks to a cache keyed by mtime.

Why this helper exists (W2 of the Ф3.1 разбор, Ф3.6, extended in Ф3.7,
01.10.2026). Several rigs write a config file with a bare `write_text` and then
re-read it through the product's readers. Those readers are cached by
`(path, mtime_ns)`, so a second write that lands on the SAME `mtime_ns` is a
false HIT on the first write's content, and the cell then measures the cache
instead of the code. Windows makes that the rule rather than the exception —
measured 01.10.2026 (Ф3.6): `st_mtime_ns` granularity there is ~0.5 ms, and
344/500 (C:) and 409/500 (repo disk D:) back-to-back write pairs landed on the
same tick. Measured the same day by Claude under a forced one-tick filesystem
(the probe `handoff/probe_2026_10_01_frozen_mtime_plugin.py`): **11 cells** in
`tests/` failed on Linux that never fail there naturally, the CI failure of
Actions #656 (`tests/kicad/test_adapter_factory.py::test_the_switch_is_read_from
_the_profile_not_from_gui_state`) among them.

How this differs from resetting the readers' caches — the earlier
`write_behind_writer`, DELETED in Ф3.7 in favour of this one. Two measured
reasons:

* A cache reset BYPASSES the very property some of those cells exist to
  measure. `tests/gui/docks/test_trees_dock.py::test_reload_trees_picks_up_
  external_write` and `tests/gui/docks/test_config_tree.py::test_refresh_picks_
  up_a_change_made_on_disk` assert that the PRODUCT notices an external edit
  through the file's mtime. Invalidating the caches from the rig makes such a
  cell green while measuring nothing — exactly what rule 33 forbids (a rig
  weakened for green is worse than no rig: an absent rig is visible, a false
  green is not).
* A reset list has to name EVERY cache the product keeps, and that list rots.
  W4 of Ф3.6 added a third one (`format_version._probe_cache`) and the rig's
  hand-kept copy of the list had to be extended by hand. `write_later` keeps no
  list at all: it reproduces the FILESYSTEM fact a later write leaves behind, so
  every reader — the three that exist today and any added tomorrow — can only
  answer from it honestly.

What it does NOT do: it does not sleep, it does not touch the product, and it
does not know what a config file is. It writes the bytes and, ONLY when the
filesystem handed back the same-or-earlier stamp, moves mtime one millisecond
past the PREVIOUS stamp — precisely what a real later write would have left.

Known, declared gap of the whole family: `open()`-based writes are invisible to
the `--coarse-mtime` probe (`tests/conftest.py`), which patches only
`Path.write_text` / `Path.write_bytes`. The probe found these cells under that
same limitation, so the coverage this helper is claimed to give is exactly the
coverage that was measured.
"""
from __future__ import annotations

import os
from pathlib import Path


def write_later(path, text: str) -> int:
    """Write TEXT to PATH as UTF-8 and return the resulting ``st_mtime_ns``.

    A file that did not exist gets a plain write: its stamp is new by
    construction, and any cache keyed by ``(path, mtime_ns)`` is a miss on the
    path alone.

    An EXISTING file gets the same write plus one correction: if its stamp did
    not advance (the same-tick case, forced on Linux by ``--coarse-mtime``), the
    stamp is set to the PREVIOUS one plus 1 ms. The previous stamp is read
    BEFORE the write, so the result does not depend on what the filesystem
    handed back afterwards — and a write that legitimately advanced the clock is
    left alone (``<=``, not ``==``: a clock that went backwards is the same kind
    of "not later" for a reader).

    atime is restored alongside, so the file looks like one written by a real
    later write rather than one whose metadata was patched.
    """
    target = Path(path)
    try:
        prev = os.stat(target)
        prev_stamps = (prev.st_atime_ns, prev.st_mtime_ns)
    except OSError:
        # Missing path (or unreadable metadata): nothing to be "later" than.
        prev_stamps = None

    target.write_text(text, encoding="utf-8")

    if prev_stamps is None:
        return os.stat(target).st_mtime_ns

    prev_atime_ns, prev_mtime_ns = prev_stamps
    mtime_ns = os.stat(target).st_mtime_ns
    if mtime_ns <= prev_mtime_ns:
        os.utime(target, ns=(prev_atime_ns, prev_mtime_ns + 1_000_000))
        # Re-stat instead of trusting the arithmetic: on a filesystem whose
        # stamps are coarser than a nanosecond this returns what the file
        # REALLY carries, so a caller asserting "later" fails loudly rather
        # than reading an invented number back.
        mtime_ns = os.stat(target).st_mtime_ns
    return mtime_ns

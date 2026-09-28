# tests/fakes/sheet_path.py
"""The minimal kipy-shaped doubles the two `pending` GUI tests need.

Ф1.4d. `_FakeUuid` / `_FakePath` / `_FakeFp` were repeated VERBATIM in
tests/gui/test_pending_dock.py and tests/gui/test_pending_three_sided.py — the
second file even says so in a comment ("the same minimal fakes
tests/gui/test_pending_dock.py uses"). They mirror KIPY's shape:
`FootprintInstance.sheet_path` is a KIID_PATH whose `.path` is a list of KIID,
each carrying `.value`.

Kipy is deliberately NOT imported here, nor in the conformance cell: a whole-file
kipy import in the test process would break the cells that assert the seam does
not pull kipy (they read sys.modules). So the cell pins the ATTRIBUTE NAMES the
consumers read, not the kipy classes.
"""
from __future__ import annotations


class FakeUuid:
    """Mirrors kipy's KIID: one attribute, ``.value``."""

    def __init__(self, value) -> None:
        self.value = value


class FakePath:
    """Mirrors kipy's KIID_PATH: ``.path`` is a list of KIID."""

    def __init__(self, uuids) -> None:
        self.path = [FakeUuid(u) for u in uuids]


class FakeSheetPathFootprint:
    """The board footprint double: exposes ``fp.sheet_path.path`` (a list of
    uuids) so ``compute_pending_edits``' identity and full-path checks can read a
    board symbol uuid / the full chain without a live KiCad."""

    def __init__(self, uuids) -> None:
        self.sheet_path = FakePath(uuids)


__all__ = ["FakePath", "FakeSheetPathFootprint", "FakeUuid"]

# tests/fakes/nets.py
"""The three `_FakeNet*` doubles, repeated byte-for-byte in two GUI dock tests.

Ф1.4d — a family by the plan's rule (the same classes in ≥2 files). They serve the
"net combo fill" path: the dock hands `board.adapter` to a collector that reads
`get_all_nets()`.

Real pairs, and why only two of them are asserted by the conformance cell:
  * :class:`FakeNetAdapter` — the ADAPTER. Its whole surface is the seam read
    `get_all_nets()`, and the cell pins exactly that against `IBoardAdapter`.
  * :class:`FakeNet` — the domain `kicadstamp.domain.board.Net` DTO by its ONE
    used field, `.name`; the cell checks the real DTO carries that field.
  * :class:`FakeNetBoard` — a `connection.board` wrapper exposing `.adapter`.
    That shape mirrors `gui/connection.py`'s BoardConnection, but `.adapter` is an
    ATTRIBUTE, so it is documented here instead of being pinned by a
    method-surface cell.
"""
from __future__ import annotations


class FakeNet:
    """Mirrors the one field the consumers read on the domain Net."""

    def __init__(self, name) -> None:
        self.name = name


class FakeNetAdapter:
    """The adapter surface the net collector uses."""

    def __init__(self, nets) -> None:
        self._nets = nets

    def get_all_nets(self):
        return self._nets


class FakeNetBoard:
    """A `connection.board` stand-in: the collector reads `board.adapter`."""

    def __init__(self, nets) -> None:
        self.adapter = FakeNetAdapter(nets)


__all__ = ["FakeNet", "FakeNetAdapter", "FakeNetBoard"]

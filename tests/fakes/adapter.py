# tests/fakes/adapter.py
"""One configurable stand-in for the board-adapter seam.

Replaces the per-file copies that had drifted apart (Ф0.6 of the tests refactor
plan): `_FakeAdapter` ×20 and `FakeAdapter` ×10. The four MCP files that shared
the least divergent copies are migrated to this one; the rest follow later.

Correspondence cell: tests/test_fakes_conformance.py asserts two things, so this
class cannot quietly drift:
  * every public method here is either declared on `IBoardAdapter` or named in
    `SEAM_GAPS` below — the fake invents nothing;
  * every name in `SEAM_GAPS` really exists on the CONCRETE `KiCadBoardAdapter`,
    so the gap list stays a description of the seam, not a dumping ground.

The gap is documented in the seam itself
(`kicadstamp/kicad/interfaces.py`): `get_board_filename` and `get_version` have
lived on the concrete adapter since the 2026-08-20 board-identity check without
ever being declared on the ABC. `ping`/`close` are the connection-lifecycle pair
the MCP `ConnectionManager` calls. All four are read by production code, so a
stand-in that lacks them stands in for a different adapter.
"""
from __future__ import annotations

#: Distinguishes "argument not given" from "argument given as None": an explicit
#: board_name=None means "NO board is open" (test_board_identity_disconnected),
#: which must not collapse into the default name.
_UNSET = object()


def _resolve_name(name, board_name):
    if board_name is not _UNSET:
        return board_name  # may be None on purpose
    if name is not _UNSET:
        return name
    return "fake.kicad_pcb"


class FakeAdapter:
    """Duck-typed adapter for unit tests. No IPC, no kipy import.

    Failure injection, one of:
      * ``fail_first=N`` — the first N calls of ``ping`` / ``get_board_filename``
        raise :class:`ConnectionError` (the historical "link dropped once" shape);
      * ``fail_on="method"`` + ``error=<exc>`` — that ONE named method raises
        ``error`` on every call (``None`` = healthy), the shape the MCP
        error-contract cells need to place a failure exactly where production
        puts it.

    Observability: ``refresh_count``, ``close_count``, ``updated``.
    """

    #: Methods production callers use that the `IBoardAdapter` ABC does NOT
    #: declare, but the CONCRETE adapter does. The conformance cell pins this
    #: list in both directions.
    SEAM_GAPS = ("close", "get_board_filename", "get_version")

    #: Methods this fake carries for TESTS only. `execute(lambda a: a.ping())`
    #: needs an arbitrary callable body, and `ping` is not a board-adapter
    #: method at all (production pings the KIPY CLIENT: kicad.ping()). The cell
    #: asserts none of these exists on the concrete adapter, so a test-only name
    #: can never shadow a production method.
    FAKE_ONLY = ("ping",)

    def __init__(self, *, name=_UNSET, board_name=_UNSET,
                 version: str = "10.0.6", footprints=(), tracks=(), vias=(),
                 nets=(), selected=(), pads_by_ref: dict | None = None,
                 fields: dict | None = None,
                 project: tuple[str, str] | None = ("fake_project", "/tmp/fake_project"),
                 fail_first: int = 0, fail_on: str | None = None,
                 error: BaseException | None = None) -> None:
        self._name = _resolve_name(name, board_name)
        self._version = version
        self._project = project
        self._footprints = list(footprints)
        self._tracks = list(tracks)
        self._vias = list(vias)
        self._nets = list(nets)
        self._selected = list(selected)
        self._pads_by_ref = pads_by_ref or {}
        self._fields = fields or {}
        self._fail_remaining = fail_first
        self._fail_on = fail_on
        self._error = error
        self.refresh_count = 0
        self.close_count = 0
        self.updated: list = []
        #: Every uuid passed to remove_by_id, in order — the observability a
        #: dedupe cell needs ("was the extra deleted, or was nothing touched?").
        self.removed: list[str] = []

    # ── failure injection ────────────────────────────────────────────────────
    def _raise_if_failing(self, method: str) -> None:
        if self._fail_on == method:
            raise self._error

    def _raise_if_flaky(self) -> None:
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise ConnectionError("kipy socket closed")

    # ── the seam (all of IBoardAdapter this fake implements) ─────────────────
    def refresh_board(self) -> None:
        self.refresh_count += 1
        self._raise_if_failing("refresh_board")

    def get_footprints(self) -> list:
        self._raise_if_failing("get_footprints")
        return list(self._footprints)

    def get_footprint(self, ref: str):
        return next((f for f in self._footprints if f.ref == ref), None)

    def get_field_value(self, fp, field_name: str):
        return self._fields.get((fp.ref, field_name))

    def get_footprint_pads(self, fp) -> list:
        return list(self._pads_by_ref.get(fp.ref, []))

    def get_selected_items(self) -> list:
        return list(self._selected)

    def get_all_nets(self) -> list:
        return list(self._nets)

    def get_tracks(self) -> list:
        return list(self._tracks)

    def get_vias(self) -> list:
        return list(self._vias)

    def update_items(self, items) -> None:
        for dto in items:
            for i, stored in enumerate(self._footprints):
                if stored.ref == dto.ref:
                    self._footprints[i] = dto  # store the mutated DTO
        self.updated.append(items)

    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool:
        work_fn()
        return True

    def remove_by_id(self, uuid_str: str) -> bool:
        """Delete a via/track by uuid — the seam write `kicadstamp dedupe
        --apply` and the "Дубли меди" panel go through (declared on
        `IBoardAdapter`, so it is NOT a seam gap). Records EVERY call in
        ``self.removed`` and answers whether the item was really there, so a
        cell can tell "deleted the extras" from "touched the board at all"."""
        self._raise_if_failing("remove_by_id")
        self.removed.append(uuid_str)
        for seq in (self._vias, self._tracks):
            for i, item in enumerate(seq):
                if getattr(item, "uuid", None) == uuid_str:
                    del seq[i]
                    return True
        return False

    # ── the documented seam gaps (see SEAM_GAPS) ─────────────────────────────
    def get_board_filename(self):
        self._raise_if_failing("get_board_filename")
        self._raise_if_flaky()
        return self._name

    def get_board_project(self):
        """The project half of the board identity (plan_2026_09_24_project_identity
        _from_ipc Т2/Т3). Declared on the seam, so it is NOT in SEAM_GAPS."""
        self._raise_if_failing("get_board_project")
        return self._project

    def get_version(self):
        self._raise_if_failing("get_version")
        return self._version

    def ping(self) -> str:
        self._raise_if_failing("ping")
        self._raise_if_flaky()
        return "pong"

    def close(self) -> None:
        self.close_count += 1


def public_callables(cls: type) -> set[str]:
    """Names of the public callables of *cls* — the one scan both the fake and
    the conformance cell use, so they cannot disagree about what is public."""
    out: set[str] = set()
    for name in dir(cls):
        if name.startswith("_"):
            continue
        if callable(getattr(cls, name, None)):
            out.add(name)
    return out


__all__ = ["FakeAdapter", "public_callables"]

# kicadstamp/field_override_adapter.py
"""The field-override LAYER — our stored Role/Cluster values, on top of the board.

Design: `design_2026_09_18_field_overrides_store.md` §1/§2.3; plan
`plan_2026_09_18_field_overrides_store.md`, task Т2.

The rule (design §0), implemented here and nowhere else:

    the effective value of a component's Role/Cluster = our stored value when we
    have one, the board's value otherwise — OUR value wins ALWAYS, not only when
    the board happens to be empty.

Why a layer and not a change inside `KiCadBoardAdapter`: the adapter must stay a
clean door to KiCad and know nothing about profiles or stores. A wrapper also
lets a TEST (or the MCP server) put a fake adapter underneath without touching
the seam.

Decisions worth naming:

  * a WRAPPER, not a subclass — the seam is not modified, and `isinstance`
    checks over it do not exist anywhere in the shipping code (verified
    2026-09-18), so substituting the object changes nothing for the callers;
  * ONLY `get_field_value` is overridden. `has_field` keeps answering about the
    BOARD (В5: resolution never consults it, and Pending changes needs the honest
    fact); `set_field_value`/`set_field_values_bulk` still write to the BOARD —
    the store is written through its own API (Т5), never by tagging;
  * the field NAME is checked FIRST: anything but Role/Cluster goes straight to
    the board without touching the store. Cheaper (the overwhelming majority of
    the tens of thousands of calls per apply are about other fields) and safer
    (Value/Reference/Datasheet belong to foreign tools — BOM, assembly);
  * a lookup is ONE dict hit: no file I/O and no scanning inside a resolution
    (guard С6; measured need: 52170 `get_field_value` calls on a single 20-item
    apply, 2026-09-13);
  * hits are COUNTED and reported as ONE Log line per adapter lifetime
    (`close()`), never one line per call. For every worker adapter — Apply,
    Redraw, every GUI worker, every CLI command — one lifetime IS one operation;
    the long-lived GUI poll adapter reports on disconnect instead (documented
    there), because a line per 2 s poll tick would drown the Log.
"""
from __future__ import annotations

import logging

from .constants import (CLUSTER_FIELD_NAME, ROLE_CLUSTER_SOURCE_REGISTRY,
                        ROLE_FIELD_NAME)
from .field_overrides import OVERRIDABLE_FIELD_NAMES, symbol_uuid_of
from .i18n import _

logger = logging.getLogger(__name__)


class FieldOverrideAdapter:
    """`IBoardAdapter` with the store consulted for Role/Cluster reads only.

    `store=None` is legal and means "no values yet" — every read passes through
    to the board unchanged. That is how the MCP server starts: ONE adapter for
    the whole process, a different profile per tool call, so the store is
    installed per call with `bind_store()` (plan Т3/С19)."""

    def __init__(self, inner, store=None, *, source: str = ROLE_CLUSTER_SOURCE_REGISTRY):
        self._inner = inner
        self._store = store
        self._source = source
        self._hits = 0

    # ── the one overridden read ───────────────────────────────────────────

    def get_field_value(self, footprint, field_name: str):
        """Our value for (this symbol, Role/Cluster) when we have one — the
        board's value otherwise. Everything else is the board's, untouched."""
        board_value = self._inner.get_field_value(footprint, field_name)
        if field_name not in OVERRIDABLE_FIELD_NAMES:
            return board_value
        if self._store is None or self._source != ROLE_CLUSTER_SOURCE_REGISTRY:
            return board_value
        symbol_uuid = symbol_uuid_of(footprint)
        if not symbol_uuid:
            return board_value
        ours = self._store.get(symbol_uuid, field_name)
        if ours is None:
            return board_value
        self._hits += 1
        return ours

    def has_field(self, footprint, field_name: str) -> bool:
        """Still about the BOARD, deliberately: "the field exists physically"
        is a different question from "which value is in force", and Pending
        changes compares the two sides on the strength of this answer."""
        return self._inner.has_field(footprint, field_name)

    # ── per-call rebinding (MCP: one adapter, many profiles) ──────────────

    def bind_store(self, store, *, source: str = None) -> None:
        """Point the layer at another profile's store — the MCP server's case:
        the adapter is created once per process, while the profile (and so the
        store) is a property of the CALL. `store=None` unbinds."""
        self._store = store
        if source is not None:
            self._source = source

    @property
    def store(self):
        return self._store

    @property
    def source(self) -> str:
        return self._source

    @property
    def inner(self):
        """The wrapped adapter — for the code that needs the door itself."""
        return self._inner

    # ── the summary line ──────────────────────────────────────────────────

    @property
    def hits(self) -> int:
        """How many values the store served so far — the number the summary
        line prints, and the marker Pending changes can show ("the registry is
        in force")."""
        return self._hits

    def take_hits(self) -> int:
        """The hits so far, and reset the counter."""
        hits, self._hits = self._hits, 0
        return hits

    def log_summary(self) -> int:
        """ONE Log line about the store hits (nothing when there were none),
        and reset the counter. Called from close(); callable directly by a
        caller that wants the line at a different boundary."""
        hits = self.take_hits()
        if hits:
            logger.info(
                _("{count} Role/Cluster value(s) came from the override store, "
                  "not from the board").format(count=hits))
        return hits

    def close(self) -> None:
        """The wrapper's lifetime is the operation's: report the store hits as
        ONE summary line, then hand the socket back."""
        self.log_summary()
        self._inner.close()

    # ── everything else is the wrapped adapter ────────────────────────────

    def __getattr__(self, name):
        # Guarded against recursion: before __init__ finished there may be no
        # _inner at all (copy/pickle probe __getattr__ first).
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

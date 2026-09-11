# gui/overlay_markers.py
"""The OWNER of the board-overlay marker/bbox map — one class, keyed,
idempotent, reconciled against the live layer.

Why this file exists: gui/board_overlay.py is a LOW-LEVEL layer — it draws one
primitive and knows nothing about who owns it. Until now the uuid bookkeeping
lived in a flat `{root: {cell: {"marker": uuid, "bbox": uuid}}}` map inside
gui_state.json (board_overlay.OVERLAY_STATE_KEY) and was spread across the one
consumer (gui/docks/cell_anchor_view.py). A point from the flat `points:` list
has neither a root nor a cell, so it had nowhere to live in that shape
(design_2026_09_11_trees_and_overlay.md §О.2.1).

This module replaces that shape with a NAMESPACED KEY map:

    cell-anchor/<root>/<cell>/marker   -> uuid
    cell-anchor/<root>/<cell>/bbox     -> uuid
    point/<name>                       -> uuid   (next task)
    tree-inner/<tree>                  -> uuid   (later)

The KEY — not the uuid — is the identity; the uuid never leaves the owner. The
first key segment is the namespace, which is what "forget everything I own"
(remove_namespace) and "never touch someone else's keys" operate on (§О.3.1).

Idempotency is the point (§О.3.2, E.2.2): ensure_marker/ensure_bbox make a key
own EXACTLY ONE shape. Two circles for one key become impossible by
construction, not by caller discipline — the "мы это так и не исправили"
complaint. The positional check (§О.3.3) survives ONLY for orphans: an unkeyed
circle within `orphan_tolerance_mm()` of the point we are about to draw is a
crash leftover, so it is replaced rather than double-stacked (Р5 cancelled the
positional policy for KEYED duplicates, not for leftovers).

Reconciliation (E.2.4) runs on connect / manual refresh and makes the map agree
with the live layer: a key whose shape is gone is dropped; a shape no key owns
is an ORPHAN — logged and left ALONE (E.1), never auto-deleted, because it may
be something the user drew on the same layer.

Everything is safe without an adapter: a visualisation must never raise and
never show a modal (E.2.6). All IPC runs on whatever worker thread calls these
methods; the map itself lives in gui_state.json via gui.settings.

Raw kipy objects never appear here — this module only ever sees uuids,
millimetres and board_overlay.OverlayShape records.
"""
import logging
from typing import Any, Optional

from kicadstamp.i18n import _

from . import board_overlay, settings

logger = logging.getLogger(__name__)

# gui_state.json key holding the namespaced {key: uuid} map this owner keeps.
OVERLAY_MARKERS_KEY = "overlay_markers"

# Namespace slugs — the FIRST segment of every key. Adding a consumer means
# adding a slug here, never a new state key (E.2.1).
NS_CELL_ANCHOR = "cell-anchor"
NS_POINT = "point"
NS_TREE_INNER = "tree-inner"
NS_TREE_OUTER = "tree-outer"

_SLOT_MARKER = "marker"
_SLOT_BBOX = "bbox"
_KEY_SEP = "/"


def orphan_tolerance_mm() -> float:
    """How close an UNKEYED circle must be to the point we are about to draw
    to count as a leftover of that same shape. E.1: half the marker radius,
    read through the settings accessor — never a magic number at a call site."""
    return board_overlay.overlay_marker_radius_mm() / 2.0


def cell_anchor_key(root: Any, cell: Any, slot: str) -> str:
    """The key one cell-anchor overlay shape owns — `slot` is "marker" or
    "bbox". `root` is the project config path and may contain '/' itself; the
    key is never parsed back, only its FIRST segment is ever inspected."""
    return _KEY_SEP.join((NS_CELL_ANCHOR, str(root), str(cell), str(slot)))


def cell_anchor_scope(root: Any, cell: Any) -> str:
    """The scope PREFIX that owns exactly this cell's marker and bbox — pass
    it to remove_scope() to drop just those two keys (never a sibling cell's)."""
    return _KEY_SEP.join((NS_CELL_ANCHOR, str(root), str(cell)))


def _in_scope(key: str, scope: str) -> bool:
    """True for `scope` itself or any key strictly UNDER it. The trailing
    separator is included so 'cell-anchor/a/b' never matches the sibling
    'cell-anchor/a/bX/marker'."""
    return key == scope or key.startswith(scope + _KEY_SEP)


def _in_namespace(key: str, namespace: str) -> bool:
    return key == namespace or key.startswith(namespace + _KEY_SEP)


def migrate_legacy_state(state=None) -> int:
    """One-time migration of the pre-owner map
    `{root: {cell: {"marker": uuid, "bbox": uuid}}}` (board_overlay.
    OVERLAY_STATE_KEY) into the namespaced keys of OVERLAY_MARKERS_KEY.

    Returns how many keys were written. NEVER raises (E.2.5): the worst allowed
    outcome of malformed legacy state is a couple of orphans, which
    reconciliation reports. The legacy key is READ, not deleted, so no uuid can
    be lost by this migration. No-op once the new key exists."""
    state = state if state is not None else settings.state
    try:
        if state.get(OVERLAY_MARKERS_KEY, None) is not None:
            return 0
        legacy = state.get(board_overlay.OVERLAY_STATE_KEY, None)
    except Exception:  # noqa: BLE001 — a state read must never fail startup
        return 0
    if not isinstance(legacy, dict):
        return 0
    migrated: dict = {}
    for root, per_cell in legacy.items():
        if not isinstance(per_cell, dict):
            continue
        for cell, slots in per_cell.items():
            if not isinstance(slots, dict):
                continue
            for slot in (_SLOT_MARKER, _SLOT_BBOX):
                value = slots.get(slot)
                if value:
                    migrated[cell_anchor_key(root, cell, slot)] = str(value)
    if not migrated:
        return 0
    try:
        state.set(OVERLAY_MARKERS_KEY, migrated)
    except Exception:  # noqa: BLE001 — best-effort migration
        logger.debug("overlay-marker migration could not be persisted",
                     exc_info=True)
        return 0
    return len(migrated)


def _distinct_orphan_positions(orphans, tol_mm: float) -> int:
    """How many DISTINCT orphan spots there are — circles within `tol_mm` of an
    already-counted spot count once. Non-circles (no centre) count
    individually. Used only for the Log line, never to delete anything."""
    positions: list = []
    for s in orphans:
        if s.center_mm is None:
            positions.append(None)
            continue
        if any(p is not None
               and abs(p[0] - s.center_mm[0]) <= tol_mm
               and abs(p[1] - s.center_mm[1]) <= tol_mm
               for p in positions):
            continue
        positions.append(s.center_mm)
    return len(positions)


class OverlayMarkerOwner:
    """Owns the namespaced key -> uuid map and every operation that keeps it in
    agreement with the live board. The map is the shared gui_state.json entry,
    so instances are interchangeable; `state` is injectable for tests."""

    def __init__(self, state=None):
        self._state = state if state is not None else settings.state

    # ── map access (never raises) ─────────────────────────────────────────

    def _mapping(self) -> dict:
        migrate_legacy_state(self._state)
        try:
            raw = self._state.get(OVERLAY_MARKERS_KEY, {}) or {}
        except Exception:  # noqa: BLE001 — a state read must never fail
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if v}

    def _store(self, mapping: dict) -> None:
        try:
            self._state.set(OVERLAY_MARKERS_KEY, dict(mapping))
        except Exception:  # noqa: BLE001 — best-effort by design
            logger.debug("overlay key map could not be persisted", exc_info=True)

    def has_key(self, key: str) -> bool:
        return bool(self._mapping().get(key))

    def uuid_for(self, key: str) -> Optional[str]:
        return self._mapping().get(key)

    def keys(self) -> list:
        return sorted(self._mapping())

    def all_uuids(self) -> list:
        """Every uuid the map owns, de-duplicated — the GUI-exit fast path and
        the whole-layer sweep's map-clearing counterpart."""
        return list(dict.fromkeys(u for u in self._mapping().values() if u))

    # ── idempotent draw / remove (E.2.2) ──────────────────────────────────

    def ensure_marker(self, adapter, key: str, x_mm: float, y_mm: float,
                      *, layer_name: Optional[str] = None) -> Optional[str]:
        """Make `key` own exactly ONE marker circle at (x_mm, y_mm). Returns
        the shape uuid (an implementation detail callers ignore), or None when
        there is no live board. The key's previous shape and any UNKEYED circle
        within orphan_tolerance_mm() of the point are removed in the same
        operation, so nothing can end up stacked under the new marker."""
        if adapter is None:
            logger.info(_("No live board — the overlay marker {key!r} was not drawn.").format(key=key))
            return None
        layer = board_overlay.require_overlay_layer(
            adapter, layer_name or board_overlay.overlay_layer_name())
        mapping = self._mapping()
        doomed: list = []
        existing = mapping.get(key)
        if existing:
            doomed.append(existing)
        doomed.extend(
            self._orphan_circle_uuids(adapter, layer, mapping, x_mm, y_mm))
        if doomed:
            self._remove_silently(adapter, list(dict.fromkeys(doomed)))
        uuid = board_overlay.draw_marker(
            adapter, layer, x_mm, y_mm,
            board_overlay.overlay_marker_radius_mm(),
            board_overlay.overlay_marker_stroke_mm())
        mapping[key] = uuid
        self._store(mapping)
        return uuid

    def ensure_bbox(self, adapter, key: str, x1_mm: float, y1_mm: float,
                    x2_mm: float, y2_mm: float,
                    *, layer_name: Optional[str] = None) -> Optional[str]:
        """Make `key` own exactly ONE bbox rectangle. Same replacement
        semantics as ensure_marker; rectangles carry no centre, so the orphan
        tolerance does not apply to them."""
        if adapter is None:
            logger.info(_("No live board — the overlay bbox {key!r} was not drawn.").format(key=key))
            return None
        layer = board_overlay.require_overlay_layer(
            adapter, layer_name or board_overlay.overlay_layer_name())
        mapping = self._mapping()
        existing = mapping.get(key)
        if existing:
            self._remove_silently(adapter, [existing])
        uuid = board_overlay.draw_bbox(
            adapter, layer, x1_mm, y1_mm, x2_mm, y2_mm,
            board_overlay.overlay_bbox_stroke_mm())
        mapping[key] = uuid
        self._store(mapping)
        return uuid

    def read_position(self, adapter, key: str) -> Optional[tuple]:
        """The CURRENT centre (world mm) of the circle `key` owns — the "Read
        position" call after the user dragged it in KiCad. None when there is
        no adapter, no such key, or the shape is gone."""
        if adapter is None:
            return None
        uuid = self._mapping().get(key)
        if not uuid:
            return None
        return board_overlay.read_marker(adapter, uuid)

    def forget_key(self, key: str) -> Optional[str]:
        """State-only twin of remove_key(): pop the key and return its uuid
        WITHOUT touching the board. The widget uses it to update its buttons
        synchronously on the UI thread, then dispatches the IPC removal of the
        returned uuid on a worker thread (the old `_marker_uuid = None` +
        start_long_op(remove_overlay) split)."""
        mapping = self._mapping()
        uuid = mapping.pop(key, None)
        if uuid is None:
            return None
        self._store(mapping)
        return uuid

    def forget_scope(self, scope: str) -> list:
        """State-only twin of remove_scope(): pop every key at or under
        `scope` and return their uuids, WITHOUT touching the board."""
        mapping = self._mapping()
        uuids: list = []
        for key in [k for k in mapping if _in_scope(k, scope)]:
            uuid = mapping.pop(key, None)
            if uuid:
                uuids.append(uuid)
        if uuids:
            self._store(mapping)
        return list(dict.fromkeys(uuids))

    def remove_key(self, adapter, key: str) -> None:
        """Forget `key` and, with a live board, delete its shape. Safe without
        an adapter — the key is still forgotten."""
        uuid = self.forget_key(key)
        if uuid is not None and adapter is not None:
            board_overlay.remove_overlay(adapter, [uuid])

    def remove_scope(self, adapter, scope: str) -> None:
        """Forget every key AT or UNDER `scope` (one cell's marker+bbox) and
        delete their shapes when a board is available."""
        uuids = self.forget_scope(scope)
        if uuids and adapter is not None:
            board_overlay.remove_overlay(adapter, uuids)

    def remove_namespace(self, adapter, namespace: str) -> None:
        """Forget every key in `namespace` (the FIRST key segment) — what a
        consumer runs when it closes its page. Keys of other namespaces are
        untouched (E.2.1)."""
        self._remove_keys(adapter, [k for k in self._mapping()
                                    if _in_namespace(k, namespace)])

    def forget_all(self, adapter=None) -> list:
        """Drop the WHOLE map, optionally deleting every shape it owns. The
        "the owner forgot everything" operation the settings sweep and the GUI
        exit use. Returns the uuids that were forgotten."""
        mapping = self._mapping()
        uuids = list(dict.fromkeys(u for u in mapping.values() if u))
        self._store({})
        if adapter is not None and uuids:
            board_overlay.remove_overlay(adapter, uuids)
        return uuids

    # ── reconciliation (E.2.4) ────────────────────────────────────────────

    def reconcile(self, adapter, *, layer_name: Optional[str] = None) -> dict:
        """Make the map agree with what is REALLY on the overlay layer: drop
        keys whose shape is gone; log — never delete — the shapes no key owns.

        Safe without an adapter and never raises: a reconcile runs in the
        background, so a dead socket or a disabled layer is a Log line, not an
        error. Returns a small report dict for the caller's Log line."""
        if adapter is None:
            return {"skipped": "no-board"}
        wanted = layer_name or board_overlay.overlay_layer_name()
        layer = board_overlay.resolve_overlay_layer(adapter, wanted)
        if layer is None:
            logger.info(_("Overlay reconcile: layer {layer!r} is not enabled on this board — nothing to reconcile.").format(layer=wanted))
            return {"skipped": "no-layer", "layer": wanted}
        try:
            shapes = board_overlay.list_overlay_shapes(adapter, layer)
        except Exception as e:  # noqa: BLE001 — a background read must not raise
            logger.warning(_("Overlay reconcile: could not read layer {layer!r}: {error}").format(layer=wanted, error=e))
            return {"skipped": "read-failed", "layer": wanted}
        live = {s.uuid for s in shapes}
        mapping = self._mapping()
        keyed = set(mapping.values())
        dead = [k for k, v in mapping.items() if v not in live]
        for key in dead:
            mapping.pop(key, None)
        if dead:
            self._store(mapping)
        orphans = [s for s in shapes if s.uuid not in keyed]
        orphan_count = _distinct_orphan_positions(orphans, orphan_tolerance_mm())
        if orphan_count:
            logger.info(_("Overlay reconcile: {count} shape(s) on layer {layer!r} have no owner key — left untouched (use the overlay sweep to remove stale shapes).").format(count=orphan_count, layer=wanted))
        return {"layer": wanted, "dropped": len(dead), "orphans": orphan_count}

    # ── internals ─────────────────────────────────────────────────────────

    def _remove_keys(self, adapter, keys: list) -> None:
        if not keys:
            return
        mapping = self._mapping()
        uuids: list = []
        for key in keys:
            uuid = mapping.pop(key, None)
            if uuid:
                uuids.append(uuid)
        self._store(mapping)
        if adapter is not None and uuids:
            board_overlay.remove_overlay(adapter, list(dict.fromkeys(uuids)))

    def _remove_silently(self, adapter, uuids: list) -> None:
        """Best-effort removal of shapes we are about to replace — an IPC
        error (a shape already gone, a dead socket) must never block the draw
        that follows, exactly like the view's old `_remove_overlay_silently`."""
        try:
            board_overlay.remove_overlay(adapter, uuids)
        except Exception:  # noqa: BLE001 — the draw that follows is the point
            logger.debug("overlay replacement could not remove %s", uuids,
                         exc_info=True)

    def _orphan_circle_uuids(self, adapter, layer, mapping: dict,
                             x_mm: float, y_mm: float) -> list:
        """Unkeyed circles on `layer` within orphan_tolerance_mm() of (x, y) —
        crash leftovers that must be replaced, not stacked. A board read that
        fails here must never block the draw that follows."""
        keyed = set(mapping.values())
        tol = orphan_tolerance_mm()
        found: list = []
        try:
            shapes = board_overlay.list_overlay_shapes(adapter, layer)
        except Exception:  # noqa: BLE001 — drawing must not depend on the scan
            return found
        for s in shapes:
            if s.uuid in keyed or s.kind != "circle" or s.center_mm is None:
                continue
            if (abs(s.center_mm[0] - x_mm) <= tol
                    and abs(s.center_mm[1] - y_mm) <= tol):
                found.append(s.uuid)
        return found


# One shared owner — the map is the shared gui_state.json entry, so instances
# are interchangeable; this saves call sites an allocation.
owner = OverlayMarkerOwner()

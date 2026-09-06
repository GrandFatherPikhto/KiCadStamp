# kicadstamp/placement/anchor_identity.py
"""Shared pure helpers for the "self-anchor duplicate" detection — a tree whose
EXPLICIT (role ...) anchor subject is ALSO placed as one of its own top-level
placement nodes (plan_2026_09_05_tree_root_rotation_drift.md).

Root problem: when a tree's (role ...) anchor is read LIVE from a physical part
and the SAME part is also materialized as one of the tree's placement nodes,
the round trip read -> write is not an identity. The node's absolute angle =
`anchor_rot + node.rotation`, and the cell's own component then sits at
`slot.angle_deg + node_abs_angle`; if `slot.angle_deg != 0` (e.g. -90.0 for
cell conn_pm5v under the CONN_PM5V anchor of tree "power") the next redraw
reads the ALREADY rotated part as the anchor and rotates it again -> a compound
drift of exactly `slot.angle_deg` per redraw (position drifts too when the slot
has a non-zero local offset).

SCOPE — explicit role anchors ONLY. An is_auto tree's single root placement
Entity is its anchor source BY CONSTRUCTION (there is no other part to anchor
on) and typically carries its own copper (e.g. cell fpga under an auto-anchored
fpga tree); it must keep materializing, so it is deliberately NOT treated as a
removable "duplicate" here (and the extract flow must keep such a node too,
`build_tree_from_clusters` — a structural requirement of `_auto_anchor_base`).
Only a redundant EXPLICIT (role ...)-anchor duplicate (like conn_pm5v_power
under the role-anchored "power" tree) is the bug this module describes.

Pure dataclass logic over Entity/Cell/TreeAnchor/Config — no live board, no
tree linking, no i18n, so both `kicadstamp.placement.entity_placement` and the
`gui/` modules can import it without cycles.
"""
from typing import Any

from ..config.models import Entity
from ..trees import TreeAnchor

__all__ = [
    "entity_anchor_identity",
    "entity_matches_role_anchor",
    "entity_cell_has_copper",
    "entity_is_self_anchor",
]


def entity_anchor_identity(entity: Entity, cfg: Any) -> tuple[str, str | None, str | None] | None:
    """(role, sheet, cluster) of the Entity's OWN live anchor subject — the
    "mount" identity the tree (role ...) anchor and the auto-anchor both read
    (`_anchor_base` / `_entity_own_zero_slot_live_position`). role is the
    cell's anchor_role when set (design_2026_09_05 v2), else the role of the
    single zero-offset (local (0,0)) component (the legacy "zero slot"), else
    the first component's role. sheet/cluster are the Entity's own sheet/
    cluster.

    Returns None when no role can be derived (missing cell or no components) —
    such an Entity can never BE an anchor subject, so it is never a duplicate
    self-anchor node."""
    if cfg is None:
        return None
    cell = cfg.cells.get(entity.cell)
    if cell is None:
        return None
    role = cell.anchor_role
    if role is None:
        slots = [c for c in cell.components
                 if c.offset_along_mm == 0.0 and c.offset_across_mm == 0.0] \
            or cell.components
        if not slots:
            return None
        role = slots[0].role
    return role, entity.sheet, entity.cluster


def _field_matches(value: str | None, narrowing: str | None) -> bool:
    """Anchor narrowing semantics: an absent narrower never constrains (the
    live resolver `resolve_anchor_fp` only uses sheet/cluster to disambiguate);
    an equal value matches; a conflict (both set, different) does not."""
    return narrowing is None or value is None or value == narrowing


def entity_matches_role_anchor(entity: Entity, cfg: Any,
                               anchor: TreeAnchor | None) -> bool:
    """True when Entity `entity` IS the anchor subject of a (role ...)-anchored
    tree: its own live-anchor identity (role, sheet, cluster) equals the
    anchor's (role, sheet-narrowing, cluster-narrowing). False for auto/origin/
    ref/point anchors (the auto root is structural — see the module docstring's
    SCOPE note)."""
    if anchor is None or anchor.role is None:
        return False
    identity = entity_anchor_identity(entity, cfg)
    if identity is None:
        return False
    role, sheet, cluster = identity
    return (role == anchor.role
            and _field_matches(sheet, anchor.anchor_sheet)
            and _field_matches(cluster, anchor.anchor_cluster))


def entity_cell_has_copper(entity: Entity, cfg: Any) -> bool:
    """True when the Entity's cell carries its OWN vias/tracks (copper the
    materializer would generate). A self-anchor duplicate Entity WITH such
    copper must NOT be excluded from materialization — its tracks/vias would
    silently stop being reconciled on redraw (the open question in plan
    2026_09_05_tree_root_rotation_drift §Открытый вопрос). Live profile scan
    2026-09-06: the only self-anchor duplicate cell (conn_pm5v) has none."""
    if cfg is None:
        return False
    cell = cfg.cells.get(entity.cell)
    return bool(cell and (cell.vias or cell.tracks))


def entity_is_self_anchor(entity: Entity, cfg: Any,
                          anchor: TreeAnchor | None) -> bool:
    """True when Entity `entity` is a redundant duplicate of an EXPLICIT
    (role ...) tree anchor — the case the runtime materializer never writes
    back over itself, the extract-by-selection builder refuses to create, and
    the GUI highlights. Equals entity_matches_role_anchor for role anchors;
    always False for auto/origin/ref/point anchors (an auto root is the tree's
    anchor source by construction, not a removable duplicate — see the module
    docstring's SCOPE note).

    Pure config logic — no live board read is needed for THIS check."""
    return entity_matches_role_anchor(entity, cfg, anchor)

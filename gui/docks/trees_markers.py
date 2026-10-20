# gui/docks/trees_markers.py
"""The OVERLAY names this dock's tree/mount markers own, and the tree-marker
WORKER that draws them.

Extracted from gui/docks/trees_dock.py by the "giants only shrink" rule (the
Ч1-А доделка of plan_2026_10_15_honest_live_read.md): the dock kept growing, so
this self-contained naming/drawing half left it. The NAMES stay re-exported
from trees_dock (and `.trees_mount_point` still reaches them through it), so
every existing caller and test seam keeps working — only the definitions moved.

Two consumers, one owner (gui/overlay_markers.py): the tree's anchor+base
circles and a mount node's point+base pair. Each consumer owns its own slug; the
owner only reserves the namespace.
"""
from kicadstamp.i18n import _
from kicadstamp.tree_position import _anchor_base_live_position, tree_layout_base
from kicadstamp.utils.units import MM

from .. import board_overlay, overlay_markers

# ── Overlay circles for a tree's anchor and base (З, 2026-09-12) ──────────
# The overlay owner (gui/overlay_markers.py, task Е) owns the key -> uuid map;
# these two namespaced keys are what the tree-settings form draws through it.
# The slugs live HERE rather than in the owner because the owner reserves
# `tree-inner` / `tree-outer` — the design §О.3.1 pair this task deliberately
# REPLACES: the inner (suspension) point lands EXACTLY on the anchor by
# construction (resolve_module_effective_base makes the pivot land on the
# marker), so two circles there would coincide. The pair worth drawing is the
# ANCHOR (where the tree hangs — what it is moved and rotated by) and the BASE
# (the origin of its local frame, from which every node's xy is measured and
# which is invisible today); the vector between them IS the rotated suspension
# point made visible.
_TREE_ANCHOR_NS = "tree-anchor"
_TREE_BASE_NS = "tree-base"


def _tree_anchor_key(name) -> str:
    """Overlay key of the circle showing where tree `name` hangs."""
    return f"{_TREE_ANCHOR_NS}/{name}"


def _tree_base_key(name) -> str:
    """Overlay key of the circle showing the origin of tree `name`'s own local
    frame."""
    return f"{_TREE_BASE_NS}/{name}"


def _tree_marker_keys(name) -> tuple:
    """BOTH overlay keys one tree's toggle owns, anchor first."""
    return (_tree_anchor_key(name), _tree_base_key(name))


# ── The mount-point marker (plan_2026_09_16_mount_point_marker Э1) ───────────
# A kind "mount" node's offset used to be typed as two numbers only. The pair
# below is the third consumer of the overlay owner on this dock (after the tree
# anchor/base circles): a DRAGGABLE circle at the node's own point and a SQUARE
# at the base it is measured from — deliberately NOT a circle, so "what moves"
# can never be confused with "what it is measured against" (Р2).
#
# The slug lives HERE, next to the tree pair, for the same reason (Ф3): the
# consumer owns its slug, the owner only reserves the namespace.
_MOUNT_POINT_NS = "mount-point"


def _mount_point_key(tree_name, ref) -> str:
    """Overlay key of the DRAGGABLE circle at mount node `ref`'s own point."""
    return f"{_MOUNT_POINT_NS}/{tree_name}/{ref}/point"


def _mount_base_key(tree_name, ref) -> str:
    """Overlay key of the SQUARE marking the base the circle is measured from."""
    return f"{_MOUNT_POINT_NS}/{tree_name}/{ref}/base"


def _mount_marker_keys(tree_name, ref) -> tuple:
    """BOTH overlay keys one mount node's "show" owns, point first."""
    return (_mount_point_key(tree_name, ref), _mount_base_key(tree_name, ref))


def _tree_marker_points(adapter, cfg, tree, sheet_names, forest) -> tuple:
    """(anchor_mm, base_mm) for `tree` — the two LIVE positions the circles
    show, in world mm. anchor = `_anchor_base_live_position` (every anchor
    mode); base = `tree_layout_base` (that same raw anchor pose plus the tree's
    own suspension point and angle). No new maths is introduced: both functions
    already exist and are only converted from nm to mm here. Raises
    ValidationError when the anchor cannot resolve — the caller turns that into
    one Log line."""
    anchor_pos, _anchor_rot = _anchor_base_live_position(
        adapter, cfg, tree, sheet_names)
    base_pos, _base_rot = tree_layout_base(
        adapter, cfg, tree, sheet_names, forest)
    return ((anchor_pos.x / MM, anchor_pos.y / MM),
            (base_pos.x / MM, base_pos.y / MM))


def _show_tree_markers_worker(payload) -> dict:
    """WORKER thread — no widget access anywhere in here.

    Draw ONE tree's anchor circle and (when it does not coincide with the
    anchor) a second circle at its local-frame base, through the shared overlay
    owner. The owner's ensure_marker is idempotent BY KEY (Е.2.2), so pressing
    the toggle again MOVES the two circles instead of stacking new ones.

    З.2.3: when base and anchor are closer than
    `overlay_markers.orphan_tolerance_mm()` (a zero suspension point at a zero
    angle — the first row of the task's own measurement) ONLY the anchor is
    drawn and a stale `tree-base` key is removed, so two circles can never end
    up in one spot, even if the user enabled the toggle at a different pivot
    first.

    Never raises (Е.2.6): a circle is a visualisation, so a failed anchor
    resolve or a disabled overlay layer degrades to one Log line returned in
    the result dict — never to an exception or a modal."""
    adapter = payload["adapter"]
    tree = payload["tree"]
    try:
        anchor_mm, base_mm = _tree_marker_points(
            adapter, payload["cfg"], tree, payload["sheet_names"],
            payload["forest"])
    except Exception as e:  # noqa: BLE001 — a resolve failure is a Log line
        return {"error": _("Tree {name!r}: the anchor did not resolve: {error}")
                .format(name=tree.name, error=e)}
    try:
        overlay_markers.owner.ensure_marker(
            adapter, _tree_anchor_key(tree.name), anchor_mm[0], anchor_mm[1])
    except Exception:  # noqa: BLE001 — drawing never breaks the caller
        return {"error": _(
            "Tree {name!r}: the anchor marker was not drawn — the overlay "
            "layer {layer!r} is not enabled on this board, or the board read "
            "failed.").format(name=tree.name,
                              layer=board_overlay.overlay_layer_name())}
    tol = overlay_markers.orphan_tolerance_mm()
    if (abs(base_mm[0] - anchor_mm[0]) <= tol
            and abs(base_mm[1] - anchor_mm[1]) <= tol):
        # No second circle in the same spot — and drop a base key left over
        # from an earlier pivot (З.2.3).
        overlay_markers.owner.remove_key(adapter, _tree_base_key(tree.name))
        return {"anchor_mm": anchor_mm, "base_mm": None}
    try:
        overlay_markers.owner.ensure_marker(
            adapter, _tree_base_key(tree.name), base_mm[0], base_mm[1])
    except Exception:  # noqa: BLE001 — the anchor circle is already down
        return {"anchor_mm": anchor_mm, "base_mm": None, "warning": _(
            "Tree {name!r}: the base marker was not drawn — the overlay layer "
            "{layer!r} is not enabled on this board, or the board read "
            "failed.").format(name=tree.name,
                              layer=board_overlay.overlay_layer_name())}
    return {"anchor_mm": anchor_mm, "base_mm": base_mm}

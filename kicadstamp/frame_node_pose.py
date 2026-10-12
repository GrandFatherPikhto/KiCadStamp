# kicadstamp/frame_node_pose.py
"""The TREE NODE's own pose for a re-read cell frame (2026-10-12).

WHY (plan_2026_10_12_reread_frame_mirror): a re-read frame's rotation is honest
only while the live cluster is a RIGID copy of the cell. On a rearranged cluster
the fit is a guess, and the guess is what mirrored Denis's `p5v_in` re-read and
turned its 0°/180° slots. The instance's own TREE NODE, however, carries an
EXPLICIT pose — Р11–Р13: an angle is only ever a number a human wrote — and Apply
places the cell by exactly that pose. So the frame follows the NODE, and the
re-read becomes the inverse of Apply again.

The pose is the number APPLY uses, NOT the node's own `rotation` field: Apply
composes `base_rot + node.rotation` down the tree (entity_placement._walk) and the
base comes from the tree's live anchor, so the pose is the `abs_rot` of
`tree_position.layout_tree_from_anchor`. That is the project's own host for
"a tree laid out from its live anchor" — this module invents no geometry and no
second resolver; it only answers WHICH node places the instance (`trees.
find_role_placement_matches`, the predicate the mount guard already shares) and
refuses, NAMED, whenever the answer is not unique or not cheap.

Best-effort and honest, never a guess (п.39): no node, several nodes, a tree that
is also embedded through a module node, a `mount` node on the node's path, an
anchor that cannot be read — each is a REASON the caller prints in the Log while
keeping the fitted rotation.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .cell_frame import CellFrame, normalize_deg
from .i18n import _
from .tree_position import layout_tree_from_anchor
from .trees import find_role_placement_matches


@dataclass(frozen=True)
class NodePose:
    """The pose Apply would give ONE instance: its rotation in the project's
    (-180, 180] convention, plus WHERE the number came from — the Log names the
    node, so a reader can go and look at it."""
    rotation_deg: float
    tree: str
    node_ref: str

    @property
    def where(self) -> str:
        return f"{self.tree}/{self.node_ref}"


def _embedders(cfg) -> dict[str, set[str]]:
    """{tree name -> the trees that EMBED it through a kind "module" node}.

    A node reached through such an embedding is posed by the OUTER tree's layout,
    while the very same node is also posed by its own tree — two different
    numbers for one node, and nothing in the config says which one the next
    Apply picks. That is an ambiguity, and it is cheaper to SEE here (a walk over
    the config, no board) than to guess a pose."""
    out: dict[str, set[str]] = {}
    for tree in getattr(cfg, "trees", None) or ():
        stack = list(tree.nodes)
        while stack:
            node = stack.pop()
            if node.kind == "module" and node.ref:
                out.setdefault(node.ref, set()).add(tree.name)
            stack.extend(node.children)
    return out


def node_pose_for_instance(cfg, adapter, sheet_names, *, cell_name, roles,
                           cluster=None, sheet=None):
    """(NodePose | None, honest reason | None) — the pose Apply would give the
    instance of `cell_name` standing at (cluster, sheet).

    `roles` are the cell's OWN role names: the node is found through
    `find_role_placement_matches`, the project's one predicate "which tree node
    places a cell carrying this role", narrowed by the instance's address (the
    entity's own sheet/cluster — the same fields every other narrowing reads).
    `cluster`/`sheet` left None widen the search, and a second instance of the
    same cell then shows up as SEVERAL nodes — a reason, not a coin toss."""
    if cfg is None:
        return None, _("no config is loaded, so no tree can place this instance")
    if not cell_name:
        return None, _("this read does not know the cell's name")
    matches = []
    seen: set[int] = set()
    for tree in getattr(cfg, "trees", None) or ():
        for role in roles or ():
            for match in find_role_placement_matches(cfg, tree, role,
                                                     sheet=sheet, cluster=cluster):
                if getattr(match.entity, "cell", None) != cell_name:
                    continue
                if id(match.node) in seen:      # the same node, reached twice
                    continue
                seen.add(id(match.node))
                matches.append(match)
    if not matches:
        return None, _("no tree node places this instance")
    if len(matches) > 1:
        where = ", ".join(sorted(f"{m.tree.name}/{m.node.ref}" for m in matches))
        return None, _("{count} tree nodes place this instance ({where}) — which "
                       "one is the source of truth is not something this read "
                       "may guess").format(count=len(matches), where=where)
    match = matches[0]
    embedders = _embedders(cfg).get(match.tree.name)
    if embedders:
        return None, _("its tree {tree!r} is also embedded by {trees} — the two "
                       "layouts pose the node differently").format(
            tree=match.tree.name, trees=", ".join(sorted(embedders)))
    if any(node.kind == "mount" for node in match.path):
        return None, _("the node hangs under a mount node, whose base is read "
                       "live (or internally) — its pose is a separate story")
    forest = {t.name: t for t in getattr(cfg, "trees", None) or ()}
    try:
        layout = layout_tree_from_anchor(match.tree, forest, adapter=adapter,
                                         cfg=cfg, sheet_names=sheet_names or {})
    except Exception as error:      # noqa: BLE001 — an honest live-read refusal
        return None, _("its tree's anchor could not be read ({error})").format(
            error=" ".join(str(error).split()))
    posed = layout.get(match.node.ref)
    if posed is None:
        return None, _("the tree lays out no pose for node {node!r}").format(
            node=match.node.ref)
    return NodePose(rotation_deg=normalize_deg(posed[1]), tree=match.tree.name,
                    node_ref=match.node.ref), None


@dataclass(frozen=True)
class FrameReadContext:
    """What the CALLER knows about the instance being read, for the frame: the
    cell's own layer (the physical-side rule of cell_frame.mirror_for_layer) and
    the cell's name + (cluster, sheet) address with the loaded config and its
    sheet map (the tree node's pose). One object, so every builder hands the SAME
    facts to the frame — a keyword lost on one path is a defect the two paths
    cannot hide from each other."""
    cell_layer: str | None = None
    cell_name: str | None = None
    cluster: str | None = None
    sheet: str | None = None
    config: object | None = None
    sheet_names: dict | None = None


def follow_the_tree_node(frame, context, adapter, roles):
    """(frame, Log lines) — a NON-RIGID frame takes the node's rotation.

    The fit is honest only for a rigid copy, so that is the only case left alone:
    with a rigid cluster the fitted rotation IS the placement's, and asking the
    tree would cost a live anchor read on every re-read for a diagnostic nobody
    needs. (К15 of the plan — "a rigid frame that disagrees with its node" — is
    therefore left EMPTY on purpose, not by oversight: the same class of signal
    arrives the moment the cluster stops being rigid, which is when the frame
    actually depends on the answer.)

    A rearranged cluster with a known node follows the node, and the line names
    BOTH numbers when they differ. Without a node (or with an ambiguous one) the
    fitted rotation stays and the line says it was GUESSED, with the reason —
    never a silent coin toss.

    The line is said for EVERY non-rigid frame that has no node, whatever the
    caller happens to know: "this read does not know the cell's name" is one of
    node_pose_for_instance's own reasons, not a licence to stay quiet (Г3 of the
    plan's acceptance — the reason is ALWAYS named)."""
    if frame is None or frame.is_rigid or context is None:
        return frame, []
    pose, reason = node_pose_for_instance(
        context.config, adapter, context.sheet_names or {},
        cell_name=context.cell_name, roles=roles, cluster=context.cluster,
        sheet=context.sheet)
    if pose is None:
        return frame, [_("the rotation was guessed from the layout ({reason})")
                       .format(reason=reason)]
    lines: list[str] = []
    if normalize_deg(pose.rotation_deg) != normalize_deg(frame.rotation_deg):
        lines.append(_("the cluster was rearranged; the frame follows the tree "
                       "node {where} ({node}°), the fit suggested {fit}°").format(
            where=pose.where, node=f"{pose.rotation_deg:g}",
            fit=f"{normalize_deg(frame.rotation_deg):g}"))
    return dataclasses.replace(frame, rotation_deg=pose.rotation_deg), lines

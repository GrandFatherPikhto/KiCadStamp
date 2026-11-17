# gui/docks/trees_reread_board.py
"""«Reread positions from board…» — the wiring: ONE board pass, then decisions that
need no board at all (plan_2026_11_16_reread_forest_from_board, commit 1).

The case (16.11.2026): the Grid Origin was moved twice on an already routed board,
and a Redraw of one module moved four cells by 3.08 mm and tore hand routing apart.
Reading one node at a time does not scale to a forest of dozens of nodes, and Redraw
never asks. This module is the half that is allowed to talk to the board:
`read_forest` collects RAW numbers, and `build_plan` turns them into a plan.

WHY TWO HALVES. The dialog's mode switch («Origin moved — absorb in anchors» /
«Blocks moved by hand — keep anchors») and the per-tree «anchor follows … / don't
touch» overrides decide the answer, and they are the user's to flip back and forth.
A second board read per flip would be a second chance to read a different board, so
`read_forest` runs ONCE (in the worker, under one snapshot) and `build_plan` is
pure arithmetic over its result: flipping the switch touches no board.

WHAT IS READ, per STANDALONE tree (one that no `module` node places):

* ONE layout — `layout_tree_from_anchor(..., module_bases=...)` — gives both the
  predicted poses (the voters' side of the comparison) and the module bases, so a
  module's frame is what REDRAW uses BY CONSTRUCTION, never a rebuild of the
  composition on the reader's side (Ч0 п.4);
* VOTES for the anchor: `collect_voters` (a top-level placement/component plus the
  same kinds inside an embedded tree, recursively — §1 of 16.11) and each voter's
  live pose ABSOLUTELY, through `child_live_pose`, the very reader the offset read
  subtracts a base from. Δ = live − predicted, in the BOARD frame;
* NODES: `_resolve_live_offset` for a top-level node (parent None = the tree's own
  anchor — the SAME call the node Read makes), the same call with the parent node
  for a child. Content of a module is read against the module's EFFECTIVE base
  (`module_bases` EFF, not MARKER), because that is the frame the content's stored
  numbers live in.

THE ANCHOR'S RAW POINT (Claude, 16.11). `_anchor_base_live_position` returns the
anchor point WITH the anchor's own shift already added
(`anchor_shift_offset_nm`). `sync_embedded_anchor` wants the point WITHOUT it, or
the shift is counted twice — so the raw point is computed by SUBTRACTING that same
function's answer, never by a second formula.

MODULE RULE (plan п.3, decided in `build_plan` because it depends only on what was
read): when ALL top-level content of a module moved together (one delta within
`MM_TOLERANCE`), the MODULE's `xy` absorbs `content − anchor` and the content's
`xy`s are not written at all (they are already right relative to the module). When
the content moved every which way, the module's `xy` stays and each content node is
read on its own from EFF. «Keep anchors» does NOT switch this rule off: it is about
anchors, modules are nodes.

SYNC ORDER (Claude, 16.11, mandatory): owner anchor → re-layout with the new shift →
module `xy` → marker → sync. The marker an embedded tree is synced to is therefore
the OLD marker moved by the owner's anchor delta AND by the module's own delta —
both already in the read, so no second layout is needed.

PARITY CAVEAT (Claude, 16.11): `_resolve_live_offset(parent=None)` measures from
the RAW anchor (its angle, no pivot, no tree angle), while the layout measures from
the EFFECTIVE base (`tree_effective_base`). On a tree with its own `rotation` or a
pivot they diverge — that divergence is measured and written up in
`done_2026_11_16_tree_anchor_read_s1.md` and is a separate task. A parity cell must
say so in words, or a green cell on a convenient tree lies about the general case.
"""
from __future__ import annotations

import logging
from typing import Optional

from kicadstamp.exceptions import read_only_verdict
from kicadstamp.forest_reread import (MODE_ABSORB, NodeRead, Skip, SyncRow, Vote,
                                      group_votes, plan_anchor, plan_forest,
                                      sync_embedded_anchor)
from kicadstamp.i18n import _
from kicadstamp.tree_anchor_read import MM_TOLERANCE
from kicadstamp.tree_position import (anchor_shift_offset_nm,
                                      board_offset_to_local_mm,
                                      layout_tree_from_anchor,
                                      local_offset_to_board_mm,
                                      relative_rotation_deg)
from kicadstamp.utils.units import MM

from .trees_anchor_read import collect_voters
from .trees_base_pose import child_live_pose, node_offset_of
from .trees_embedding import embedding_marker, module_nodes

logger = logging.getLogger(__name__)


# ── the two seams to the giants (patched by the cells, never re-implemented) ──

def _live_offset(*args, **kwargs):
    """`trees_dock._resolve_live_offset` — imported lazily because that module is a
    giant that imports this package's widgets; the cells patch THIS name."""
    from .trees_dock import _resolve_live_offset
    return _resolve_live_offset(*args, **kwargs)


def _anchor_pose(adapter, cfg, tree, sheet_names, *, snapshot=None):
    """`tree_position._anchor_base_live_position` through the same lazy seam."""
    from .trees_dock import _anchor_base_live_position
    return _anchor_base_live_position(adapter, cfg, tree, sheet_names,
                                     snapshot=snapshot)


def _mm(vector) -> tuple[float, float]:
    return (vector.x / MM, vector.y / MM)


def _shift_of(tree) -> tuple[float, float]:
    """The anchor's stored OWN shift (config frame, mm) — (0, 0) when it has none."""
    anchor = getattr(tree, "anchor", None)
    shift = getattr(anchor, "shift_xy", None) if anchor is not None else None
    if shift is None:
        return (0.0, 0.0)
    return (float(shift[0]), float(shift[1]))


def _raw_anchor_point(tree, base_pos, base_deg) -> tuple[float, float]:
    """The anchor point WITHOUT its own shift — the value `sync_embedded_anchor`
    subtracts from the module's marker. `_anchor_base_live_position` already added
    the shift, so it comes back off here, through the SAME function that added it:
    a second formula is exactly how the shift gets counted twice (Claude, 16.11)."""
    shift = anchor_shift_offset_nm(getattr(tree, "anchor", None), base_deg)
    return ((base_pos.x - shift.x) / MM, (base_pos.y - shift.y) / MM)


def _skip(out: dict, tree: str, ref: str, reason: str) -> None:
    """ONE skip per (tree, ref) — never a second copy of the same fact.

    Two markers naming one tree (Ф5) reach the same content twice; without this the
    table would report the same skip once per marker."""
    for existing in out["skips"]:
        if (existing["tree"], existing["ref"]) == (tree, ref):
            return
    out["skips"].append({"tree": tree, "ref": ref, "reason": reason})


def _stored(node) -> Optional[tuple[float, float]]:
    """The node's stored offset as a flat mm pair, or None when it stores none
    (`xy` or `polar` only — `plan_forest` reports the latter as a skip)."""
    if node.xy is None and node.polar is None:
        return None
    offset = node_offset_of(node)
    return (float(offset[0]), float(offset[1]))


def _anchor_marker(trees, tree):
    """Every (owner name, marker ref) whose module node names `tree` — the plan
    refuses a tree embedded by TWO markers, so the whole list is needed, not just
    the first answer `embedding_marker` gives."""
    found = []
    for owner in trees:
        for marker in module_nodes(owner):
            if marker.ref == tree.name:
                found.append((owner.name, marker.ref))
    return found


# ── the read half (worker; every board access happens here, once) ─────────────

def read_forest_now(*, cfg, trees, sheet_names, adapter, snapshot=None) -> dict:
    """Raw numbers for the whole forest — see the module docstring. Plain data in,
    plain data out (no widget, no Tree, nothing live): the result survives a thread
    boundary and is what `build_plan` later decides over."""
    trees = list(trees or [])
    forest = {t.name: t for t in trees}
    out: dict = {"angles": {}, "shifts": {}, "votes": {}, "nodes": [],
                 "modules": [], "embedded": [], "skips": [], "bases": {}}
    ctx = {"cfg": cfg, "adapter": adapter, "sheet_names": dict(sheet_names or {}),
           "snapshot": snapshot, "forest": forest, "module_bases": {},
           # Where each embedded tree's module put it, from the ONE layout that
           # read it — the sync takes the marker from here, not from a rebuild.
           "module_poses": {},
           # Ф5: a tree placed by TWO module nodes would be walked once per marker —
           # two rows for one (tree, ref). Its content is not read at all, exactly
           # like its own anchor is not synced (one shift cannot land on both).
           "doubled": {t.name for t in trees if len(_anchor_marker(trees, t)) > 1}}

    # 1. The OWN anchor of every tree, standalone or embedded: only its ANGLE is
    #    used for a standalone tree (the shift is stored in that frame), while an
    #    embedded tree needs its raw point for the sync. One failure drops that
    #    tree and leaves the rest of the forest readable.
    for tree in trees:
        try:
            pos, deg = _anchor_pose(adapter, cfg, tree, ctx["sheet_names"],
                                    snapshot=snapshot)
        except Exception as exc:          # noqa: BLE001 — reported, never fatal
            _skip(out, tree.name, "", _(
                "tree {tree!r}: its own anchor could not be read — {error}"
            ).format(tree=tree.name, error=exc))
            continue
        out["bases"][tree.name] = pos
        out["angles"][tree.name] = float(deg or 0.0)

    # 2. Every STANDALONE tree, through one layout and one visit.
    for tree in trees:
        if tree.name not in out["bases"]:
            continue
        if embedding_marker(trees, tree) is not None:
            continue                      # read through its owner, step 4
        _read_standalone(out, ctx, tree)

    # 3. Every EMBEDDED tree: its own shift is chosen so that its own base lands
    #    on the module that places it (plan п.1 as revised 16.11 evening).
    for tree in trees:
        if tree.name not in out["bases"]:
            continue
        if embedding_marker(trees, tree) is None:
            continue
        out["embedded"].append(_embedded_record(out, trees, ctx, tree))
    return out


def _read_standalone(out: dict, ctx: dict, tree) -> None:
    """One standalone tree: the layout (which also hands out the module bases),
    the votes, then the nodes."""
    module_bases: dict = {}
    try:
        predicted = layout_tree_from_anchor(
            tree, ctx["forest"], adapter=ctx["adapter"], cfg=ctx["cfg"],
            sheet_names=ctx["sheet_names"], snapshot=ctx["snapshot"],
            module_bases=module_bases)
    except Exception as exc:              # noqa: BLE001 — one skip, not a crash
        _skip(out, tree.name, "", _(
            "tree {tree!r}: its content could not be laid out — {error}"
        ).format(tree=tree.name, error=exc))
        return

    out["shifts"][tree.name] = _shift_of(tree)
    out["votes"][tree.name] = []

    voters, refusal = collect_voters(tree, ctx["forest"])
    if refusal is not None:
        # No shift can be derived, but the tree's NODES are still worth reading —
        # the owner's other trees must not go unmeasured because of this one.
        _skip(out, tree.name, "", refusal)
    else:
        for label, node in voters:
            _vote(out, ctx, tree, label, node, predicted)

    ctx["module_bases"] = module_bases
    _visit(out, ctx, tree, tree, list(tree.nodes),
           ("anchor", float(out["angles"][tree.name])), None)


def _vote(out: dict, ctx: dict, tree, label: str, node, predicted: dict) -> None:
    """One voter's Δ (live − layout), board frame. An unreadable or mirrored voter
    is a SKIP with its reason, never a silent absence from the vote."""
    pose = predicted.get(node.ref)
    if pose is None:
        _skip(out, tree.name, label, _(
            "node {ref!r} is not in the tree layout — its pose cannot be predicted, "
            "so it cannot vote").format(ref=label))
        return
    try:
        live_pos, _live_deg, mirrored = child_live_pose(
            ctx["cfg"], ctx["adapter"], ctx["sheet_names"], node.ref, node.kind,
            component_address=node.anchor if node.kind == "component" else None,
            snapshot=ctx["snapshot"])
    except Exception as exc:              # noqa: BLE001 — reported, never fatal
        _skip(out, tree.name, label, str(exc))
        return
    if mirrored:
        _skip(out, tree.name, label, _(
            "this node's instance is MIRRORED on the live board"))
        return
    live = _mm(live_pos)
    at = _mm(pose[0])
    out["votes"][tree.name].append(
        (label, live[0] - at[0], live[1] - at[1]))


def _visit(out: dict, ctx: dict, owner, tree, nodes: list, base: tuple,
           inside: Optional[str], depth: int = 0) -> None:
    """Every node of `tree` under `owner`, each read against its OWN base.

    `base` says where this level's frame comes from, and its LAST element is always
    that frame's angle (the absorb of the plan is expressed in it):
      * `("anchor", rot)` — the tree's own anchor, `_resolve_live_offset(parent=None)`;
      * `("node", parent, rot)` — the live pose of the parent node (a child);
      * `("pose", mm, rot)` — a frame handed in by the caller: a module's MARKER for
        the nodes that hang under a module node, its EFF for the embedded content.
    `inside` names the module the level belongs to (the absorb rule of the plan)."""
    for node in nodes:
        if node.kind == "mount":
            # A mount node's base is its OWN live anchor, never the parent frame and
            # never a module's frame (Ф2): `_emit` reads it through `_live_offset`
            # with its own `base_anchor` at ANY level, exactly as the layout places
            # it (`mount_node_poses`), and it therefore absorbs nothing.
            _emit(out, ctx, owner, tree, node, base, inside)
            _visit(out, ctx, owner, tree, list(node.children or []),
                   ("node", node, float(base[-1])), inside, depth + 1)
            continue
        if node.kind == "module":
            pose = ctx["module_bases"].get(node.ref)
            if pose is None:
                continue                    # the layout placed no such module node
            if inside is not None or depth != 0:
                # Ф3: a module under a node/mount or inside another module's content
                # has its marker on a LIVE parent. The «moved together» rule is
                # written for TOP modules only, so its content is a SKIP with the
                # reason — never a row with the wrong absorb.
                _skip(out, owner.name, node.ref, _(
                    "nested module {ref!r} — its marker hangs off a live parent, so "
                    "its content is not read in this pass").format(ref=node.ref))
                continue
            _module_record(out, ctx, owner, node, pose)
            marker = _mm(pose[1])
            eff = _mm(pose[3])
            # Stage 1 of the layout: the module node's OWN children hang from its
            # marker (before the embedded tree's inner point is inverted).
            _visit(out, ctx, owner, tree, list(node.children or []),
                   ("pose", marker, float(pose[2])), node.ref, depth + 1)
            child = ctx["forest"].get(node.ref)
            if child is not None and child.name in ctx["doubled"]:
                _skip(out, child.name, "", _(
                    "tree {tree!r} is placed by more than one module node — its "
                    "content is not read in this pass").format(tree=child.name))
                child = None
            if child is None:
                continue
            # Stage 2: the embedded tree's content is stored in the EFF frame.
            _visit(out, ctx, owner, child, list(child.nodes),
                   ("pose", eff, float(pose[4])), node.ref, depth + 1)
            continue
        _emit(out, ctx, owner, tree, node, base, inside)
        _visit(out, ctx, owner, tree, list(node.children or []),
               ("node", node, float(base[-1]) if base[0] != "pose"
                else float(base[2])), inside, depth + 1)


def _emit(out: dict, ctx: dict, owner, tree, node, base: tuple,
          inside: Optional[str]) -> None:
    """Read ONE node's live offset and record it with the frame it was read in.

    `source` is what `build_plan` looks up to know which base's move the node can
    absorb, and it follows the READER, not the nesting: `_live_offset` measures
    against a LIVE base, whose move the node does not need to absorb at all. So a
    node read against its PARENT (`base[0] == "node"`) and a MOUNT read against its
    own live ROLE absorb NOTHING — whoever they hang under, and at any depth. Only a
    frame the CALLER computed (a module's MARKER or EFF) or the tree's own anchor can
    move without the node following it, and only those set `source`."""
    stored = _stored(node)
    rotation: Optional[float] = None
    offset: Optional[tuple[float, float]] = None
    error: Optional[str] = None
    if base[0] == "pose" and node.kind != "mount":
        _, base_mm, base_rot = base
        try:
            live_pos, live_deg, mirrored = child_live_pose(
                ctx["cfg"], ctx["adapter"], ctx["sheet_names"], node.ref, node.kind,
                component_address=node.anchor if node.kind == "component" else None,
                snapshot=ctx["snapshot"])
        except Exception as exc:          # noqa: BLE001 — one skip, not a crash
            error = str(exc)
        else:
            if mirrored:
                error = _("this node's instance is MIRRORED on the live board")
            else:
                live = _mm(live_pos)
                offset = board_offset_to_local_mm(
                    (live[0] - base_mm[0], live[1] - base_mm[1]), base_rot)
                rotation = (relative_rotation_deg(live_deg, base_rot)
                            if live_deg is not None else None)
    else:
        parent = base[1] if base[0] == "node" else None
        try:
            offset, rotation = _live_offset(
                ctx["cfg"], ctx["adapter"], ctx["sheet_names"], tree, parent,
                node.ref, node.kind,
                base_anchor=node.anchor if node.kind == "mount" else None,
                component_address=node.anchor if node.kind == "component" else None,
                snapshot=ctx["snapshot"], verdict=read_only_verdict())
        except Exception as exc:          # noqa: BLE001 — one skip, not a crash
            error = str(exc)
    # Ф1: the ORDER matters. A child is measured from its LIVE parent and a mount
    # from its OWN live role, so neither rides the anchor or a module — checking
    # `inside` first gave every one of them a false row of ≈ +3 mm on a moved
    # Origin (85 nodes in the live STM32 profile).
    if base[0] == "node" or node.kind == "mount":
        source = "none"
    elif inside is not None:
        source = ["module", inside]
    elif base[0] == "anchor":
        source = "anchor"
    else:
        source = "none"
    out["nodes"].append({
        "tree": tree.name, "anchor_tree": owner.name, "ref": node.ref,
        "offset": tuple(offset) if offset is not None else None,
        "rotation": rotation, "stored": stored,
        "stored_rotation": float(node.rotation or 0.0),
        "polar": node.polar is not None,
        "base_rot": float(base[-1]),
        "source": source, "error": error,
    })


def _module_record(out: dict, ctx: dict, owner, node, pose: tuple) -> None:
    """The module NODE itself: its `xy` lives in the frame it is laid out from,
    which is the marker's angle minus its own rotation. `top` lists the embedded
    tree's top-level nodes — the set the «moved together» rule must cover WHOLE."""
    child = ctx["forest"].get(node.ref)
    stored = _stored(node)
    if child is None or stored is None:
        return
    if any(item["ref"] == node.ref for item in out["modules"]):
        return                              # two markers, ONE module row (Ф5)
    marker_rot = float(pose[2])
    out["modules"].append({
        "tree": owner.name, "ref": node.ref, "content": child.name,
        "stored": stored, "polar": node.polar is not None,
        "rotation": float(node.rotation or 0.0),
        "parent_rot": marker_rot - float(node.rotation or 0.0),
        "eff_rot": float(pose[4]),
        "top": [n.ref for n in child.nodes],
    })
    ctx["module_poses"].setdefault(
        child.name, (owner.name, node.ref, _mm(pose[1]), marker_rot))


def _embedded_record(out: dict, trees: list, ctx: dict, tree) -> dict:
    """One EMBEDDED tree, ready for `sync_embedded_anchor` — or the refusal that
    keeps its anchor alone, said in words."""
    rec: dict = {"tree": tree.name, "owner": "", "marker": "", "refusal": None,
                 "angle": out["angles"].get(tree.name, 0.0),
                 "shift": _shift_of(tree)}
    markers = _anchor_marker(trees, tree)
    if not markers:
        rec["refusal"] = _(
            "embedded anchor kept: no module node places tree {tree!r}").format(
                tree=tree.name)
        return rec
    rec["owner"], rec["marker"] = markers[0]
    if len(markers) > 1:
        rec["refusal"] = _(
            "embedded anchor kept: tree {tree!r} is placed by more than one module "
            "node ({places}) — one shift cannot land on both").format(
                tree=tree.name,
                places=", ".join("{}:{}".format(o, m) for o, m in markers))
        return rec
    anchor = getattr(tree, "anchor", None)
    has_shift_frame = (getattr(anchor, "is_origin", False)
                       or getattr(anchor, "point", None) is not None)
    if anchor is None or not has_shift_frame:
        rec["refusal"] = _(
            "embedded anchor kept: only an Origin or a point anchor can absorb a "
            "shift — this tree's own anchor cannot")
        return rec
    pose = ctx["module_poses"].get(tree.name)
    if pose is None:
        rec["refusal"] = _(
            "embedded anchor kept: the tree that places {tree!r} was not read").format(
                tree=tree.name)
        return rec
    _owner, _marker, marker_mm, marker_rot = pose
    rec["raw_point"] = _raw_anchor_point(tree, out["bases"][tree.name],
                                         rec["angle"] or None)
    rec["marker_mm"] = marker_mm
    rec["marker_rot"] = marker_rot
    return rec


def read_forest(payload: dict) -> dict:
    """`start_long_op` worker entry point — plain data in, plain data out. Builds
    no adapter of its own: the dock's SHARED one travels in the payload (exactly
    like `run_node_reread_worker`), and one `snapshot` rides along."""
    return read_forest_now(cfg=payload["cfg"],
                           trees=payload.get("trees") or (),
                           sheet_names=payload.get("sheet_names") or {},
                           adapter=payload["adapter"],
                           snapshot=payload.get("snapshot"))


# ── the decision half (UI thread; no board access at all) ─────────────────────

def _common(deltas: list) -> Optional[tuple[float, float]]:
    """The one delta ALL of them share within the tolerance, or None — the «moved
    together» test of the module rule. Strictly all, never a majority."""
    if not deltas:
        return None
    first = deltas[0]
    for delta in deltas[1:]:
        if (abs(delta[0] - first[0]) > MM_TOLERANCE
                or abs(delta[1] - first[1]) > MM_TOLERANCE):
            return None
    return first


def build_plan(reads_raw: dict, *, mode: str = MODE_ABSORB,
               overrides: Optional[dict] = None):
    """`ForestPlan` from what `read_forest` brought back — NO board access, so the
    dialog's mode switch and its per-tree overrides are free to flip.

    Order matters and is the one the plan insists on: the anchors first (their Δ
    drives every base), then the module rule (which needs the content's Δ and the
    anchor's Δ), then the node absorbs, then the embedded syncs (whose marker sits
    at the NEW place — owner's anchor Δ AND the module's own Δ)."""
    overrides = dict(overrides or {})
    anchors = []
    delta_board: dict = {}
    for name, votes in (reads_raw.get("votes") or {}).items():
        angle = float(reads_raw["angles"].get(name, 0.0))
        was = tuple(reads_raw["shifts"].get(name) or (0.0, 0.0))
        groups = group_votes([Vote(label, (dx, dy), name)
                              for label, dx, dy in votes])
        row = plan_anchor(name, was, groups, anchor_rot_deg=angle, mode=mode,
                          override=overrides.get(name))
        anchors.append(row)
        delta_board[name] = local_offset_to_board_mm(
            (row.now[0] - row.was[0], row.now[1] - row.was[1]), angle)

    by_key = {(rec["tree"], rec["ref"]): rec for rec in reads_raw.get("nodes") or []}
    delta_module: dict = {}
    for module in reads_raw.get("modules") or []:
        deltas = []
        for ref in module["top"]:
            rec = by_key.get((module["content"], ref))
            if rec is None or rec.get("offset") is None or rec.get("stored") is None:
                deltas = []
                break
            deltas.append((rec["offset"][0] - rec["stored"][0],
                           rec["offset"][1] - rec["stored"][1]))
        common = _common(deltas)
        if common is None:
            delta_module[module["ref"]] = (0.0, 0.0)     # every which way
            continue
        # «Together»: the module's xy takes the difference between where the
        # content really stands and where the owner's anchor already moved to, so
        # the content's own xy needs no rewrite at all.
        content = local_offset_to_board_mm(common, module["eff_rot"])
        anchor = delta_board.get(module["tree"], (0.0, 0.0))
        delta_module[module["ref"]] = (content[0] - anchor[0],
                                       content[1] - anchor[1])

    reads = []
    for rec in reads_raw.get("nodes") or []:
        source = rec.get("source")
        anchor = delta_board.get(rec.get("anchor_tree"), (0.0, 0.0))
        if isinstance(source, list):
            moved = delta_module.get(source[1], (0.0, 0.0))
            base_moved = (anchor[0] + moved[0], anchor[1] + moved[1])
        elif source == "anchor":
            base_moved = anchor
        else:
            base_moved = (0.0, 0.0)
        # The absorb is always expressed in the NODE'S OWN base frame (the angle it
        # was read against) — one formula for a top node, a module child and the
        # content of an embedded tree.
        absorb = board_offset_to_local_mm(base_moved,
                                          float(rec.get("base_rot") or 0.0))
        reads.append(NodeRead(
            tree=rec["tree"], ref=rec["ref"], offset_mm=rec.get("offset"),
            rotation_deg=rec.get("rotation"), stored_offset=rec.get("stored"),
            stored_rotation=rec.get("stored_rotation"), polar=bool(rec.get("polar")),
            absorb_mm=absorb, error=rec.get("error")))

    for module in reads_raw.get("modules") or []:
        moved = board_offset_to_local_mm(delta_module.get(module["ref"], (0.0, 0.0)),
                                        float(module["parent_rot"]))
        if moved == (0.0, 0.0):
            continue                        # nothing to write, nothing to report
        reads.append(NodeRead(
            tree=module["tree"], ref=module["ref"],
            offset_mm=(module["stored"][0] + moved[0],
                       module["stored"][1] + moved[1]),
            rotation_deg=module["rotation"], stored_offset=tuple(module["stored"]),
            stored_rotation=module["rotation"], polar=bool(module["polar"])))

    synced = []
    extra_skips = []
    for emb in reads_raw.get("embedded") or []:
        if emb.get("refusal"):
            extra_skips.append(Skip(emb["tree"], "", emb["refusal"]))
            continue
        anchor = delta_board.get(emb["owner"], (0.0, 0.0))
        moved = delta_module.get(emb["marker"], (0.0, 0.0))
        marker = (emb["marker_mm"][0] + anchor[0] + moved[0],
                  emb["marker_mm"][1] + anchor[1] + moved[1])
        row = sync_embedded_anchor(
            emb["tree"], tuple(emb["raw_point"]), anchor_rot_deg=float(emb["angle"]),
            module_marker_mm=marker, module_rot_deg=float(emb["marker_rot"]),
            current_shift=tuple(emb["shift"]))
        if isinstance(row, SyncRow):
            synced.append(row)
        else:
            extra_skips.append(row)

    plan = plan_forest(reads, anchors=anchors, synced=synced)
    plan.skipped.extend(extra_skips)
    for rec in reads_raw.get("skips") or []:
        plan.skipped.append(Skip(rec["tree"], rec["ref"], rec["reason"]))
    return plan

# kicadstamp/config/tree_instance_root.py
"""`root` — instantiate a SUBTREE instead of a whole tree (Н6, 07.11).

A declaration may name ONE placement node of its template:

    (tree_instance
      (template "switch_a")
      (root (ref "driver_sw_a" (uuid "…")))
      (name "switch_b")
      (sheet "SW B")
      (anchor (point "Origin") (shift 0.0 40.0)))

The copy then holds that node and everything under it; the rest of the template
is neither copied nor touched. This module owns the four questions that answer
needs, all pure dict work (the expansion runs BEFORE any per-entry parse — see
`tree_instances`'s module docstring):

1. is the `root` mapping well formed, and does it name a placement node of the
   template (`parse_root` / `find_subtree`);
2. where does that node STAND relative to the template's base — the pose along
   the path from the top level, with the rotations accumulated exactly as every
   tree walk composes them (`subtree_pose`);
3. which ROLES does the copied subtree place — read off the records' cells
   (`subtree_roles`);
4. which references of the copied part point OUTSIDE it — a fatal each, because
   the copy stands on another sheet where those parts will not exist
   (`outward_problem`).

Deliberately NOT here: the label wording (that is the expansion's), and any
renaming (`_expand_node`'s `__{instance}` suffix runs AFTER the cut, so the
ref map covers exactly the copied refs).
"""
from __future__ import annotations

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

# NOTE: the two geometry helpers are imported INSIDE the functions on purpose.
# `kicadstamp.domain.geometry` lives in a package whose `__init__` re-exports the
# board DTOs (and those pull kipy), and `cli` must stay kipy-free at import time
# (tests/cli/test_cli_lazy_imports.py). A module-level import here would make the
# whole CLI pay for kipy just because a config module is loaded.

__all__ = ["check_outward", "find_subtree", "outward_problem", "parse_root",
           "subtree_pose", "subtree_roles"]


def _walk_nodes(nodes: list) -> list[dict]:
    """Every node of the subtree, depth-first (the copy's own refs)."""
    out: list[dict] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        out.append(node)
        out.extend(_walk_nodes(node.get('children') or []))
    return out


def parse_root(raw, instance_name: str) -> dict | None:
    """The declaration's `root` -> {ref, ref_uuid} or None; a malformed shape is
    a fatal naming the instance (never a silent skip)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(format_fatal_error(
            _("tree_instance {name!r}: root: must be a mapping").format(
                name=instance_name),
            [_("write (root (ref \"...\")) — the placement node of the template "
               "this instance copies, together with everything under it")]))
    ref = raw.get('ref')
    if not isinstance(ref, str) or not ref:
        raise ValidationError(format_fatal_error(
            _("tree_instance {name!r}: root: names no node").format(
                name=instance_name),
            [_("write (root (ref \"...\")) — the placement node of the template "
               "this instance copies")]))
    out = {"ref": ref}
    if raw.get('ref_uuid'):
        out["ref_uuid"] = raw.get("ref_uuid")
    return out


def find_subtree(nodes: list, ref: str) -> tuple[dict, list[dict]] | None:
    """(the named node, the path from the top level down to it) or None.

    The path is what `subtree_pose` needs; a node found at the top level has a
    one-element path. Refs are unique inside one tree (`trees._validate_local_
    refs`), so the FIRST match is the only match."""
    def walk(children: list, path: list[dict]):
        for node in children:
            if not isinstance(node, dict):
                continue
            here = path + [node]
            if node.get('ref') == ref:
                return node, here
            found = walk(node.get('children') or [], here)
            if found is not None:
                return found
        return None

    return walk(nodes or [], [])


def _offset_mm(node: dict) -> tuple[float, float]:
    """A raw node's own offset in mm — `xy` flattened, `polar` turned into the
    offset vector (the same `local_to_absolute` primitive `tree_position.
    node_offset` uses; a raw dict is all we have before the parse)."""
    xy = node.get('xy')
    if isinstance(xy, (list, tuple)) and len(xy) == 2:
        return float(xy[0]), float(xy[1])
    polar = node.get('polar')
    if isinstance(polar, (list, tuple)) and len(polar) == 2:
        from ..domain.geometry import Vector2
        from ..geometry.spoke_layout import local_to_absolute
        from ..utils.units import MM

        vector = local_to_absolute(Vector2.from_xy(0, 0), float(polar[0]), 0.0,
                                   float(polar[1]))
        return vector.x / MM, vector.y / MM
    return 0.0, 0.0


def subtree_pose(path: list[dict]) -> tuple[float, float, float]:
    """(x_mm, y_mm, rotation_deg) of the LAST node of `path`, in the template's
    own base frame — the composition every walk uses: a child's offset is
    rotated into the parent's frame and added, and the rotations accumulate.

    The copy keeps the template's anchor (a role/self/point one) or replaces it
    with the declaration's own, and in BOTH cases the root node must stand where
    it stood in the template — otherwise the declaration's shift would move it
    away from its own place (§7.3 of the plan)."""
    x = y = 0.0
    rot = 0.0
    for node in path:
        ox, oy = _offset_mm(node)
        if rot:
            from ..geometry.spoke_layout import rotate_local_offset
            from ..utils.units import MM

            vector = rotate_local_offset(ox, oy, rot)
            ox, oy = vector.x / MM, vector.y / MM
        x += ox
        y += oy
        rot += float(node.get('rotation') or 0.0)
    return x, y, rot


def subtree_roles(nodes: list, entities_by_name: dict, cells: dict) -> set[str]:
    """Every ROLE the given placement nodes place, read from their records'
    CELLS — the config-side answer to "will this part exist on the copy's
    sheet?" (a record names no role itself; its cell does)."""
    roles: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get('kind')
        if kind is None or kind == 'placement':
            entity = entities_by_name.get(node.get('ref'))
            cell = (cells or {}).get(entity.get('cell')) if isinstance(entity, dict) else None
            for comp in (cell or {}).get('components') or []:
                if isinstance(comp, dict) and comp.get('role'):
                    roles.add(comp["role"])
        roles |= subtree_roles(node.get('children') or [], entities_by_name, cells)
    return roles


def _net_trace_roles(record: dict) -> set[str]:
    roles: set[str] = set()
    if record.get('anchor_role'):
        roles.add(record["anchor_role"])
    for kind in ('tracks', 'vias'):
        for item in record.get(kind) or []:
            if isinstance(item, dict) and item.get("net_from_role"):
                roles.add(item["net_from_role"])
    return roles


def check_outward(template: dict, nodes: list, root_ref: str,
                  old_sheet: str | None, entities_by_name: dict,
                  net_traces_by_name: dict, cells: dict, template_name: str,
                  instance_name: str) -> None:
    """Refuse when the copied part references something OUTSIDE it — the
    template's own inner references first (they are not names of the copy at
    all), then every node of the subtree.

    A `pivot_ref` or a `(self (ref ...))` naming a node LEFT BEHIND cannot be
    renamed by the walk, so the generic "names no node of the template tree"
    would blame the template for a mistake the `root` made; the wording here
    says "outside the copied subtree". A missing pivot in the subtree could
    never be resolved later, so this is a refusal at EXPANSION time, never a
    surprise during a live redraw."""
    subtree = _walk_nodes(nodes)
    refs = {n.get('ref') for n in subtree if n.get('ref')}
    pivot_ref = template.get('pivot_ref')
    if pivot_ref is not None and pivot_ref not in refs:
        raise ValidationError(format_fatal_error(
            _("tree_instance {name!r}: template {template!r} pivot_ref {ref!r} "
              "is outside the copied subtree").format(
                  name=instance_name, template=template_name, ref=pivot_ref),
            [_("a tree's inner point must be a node of the same tree — with a "
               "`root` it must also be inside the copied subtree")]))
    anchor = template.get('anchor')
    self_ref = None
    if isinstance(anchor, dict) and isinstance(anchor.get('self'), dict):
        self_ref = anchor["self"].get("ref")
    if self_ref is not None and self_ref not in refs:
        raise ValidationError(format_fatal_error(
            _("tree_instance {name!r}: template {template!r} self anchor ref "
              "{ref!r} is outside the copied subtree").format(
                  name=instance_name, template=template_name, ref=self_ref),
            [_("the self anchor's (ref ...) must be a node of the same tree — "
               "with a `root` it must also be inside the copied subtree")]))
    roles = subtree_roles(nodes, entities_by_name, cells or {})
    for node in subtree:
        problem = outward_problem(node, refs, roles, old_sheet,
                                  net_traces_by_name, template_name,
                                  instance_name)
        if problem is not None:
            raise ValidationError(format_fatal_error(problem, [
                _("with a `root` the copy holds only that subtree, so a part "
                   "outside it is not there under the instance's sheet — name a "
                   "bigger root, or take the reference out of the subtree")]))


def outward_problem(node: dict, subtree_refs: set[str], roles: set[str],
                    old_sheet: str | None, net_traces_by_name: dict,
                    template_name: str, instance_name: str) -> str | None:
    """The refusal for ONE copied node whose reference points OUTSIDE the
    subtree, or None.

    Rules (plan §6.4, one per kind — no guessing):
      * a MOUNT node whose anchor's sheet is the template's own looks INSIDE: its
        role must belong to the copied subtree, or the copy would look for a
        part that stayed behind in ANOTHER sheet's namespace;
      * a COMPONENT node with a (role ...) address obeys the same rule;
      * a NET_TRACE node copies its record, whose anchor/elements name roles —
        every one of them must be inside, or the copy's copper would attach to
        nothing;
      * a node whose anchor names a SHEET other than the template's own is a
        board-wide reference and is kept verbatim (as before) — no check."""
    kind = node.get('kind')
    ref = node.get('ref')
    if kind in ('mount', 'component'):
        anchor = node.get('anchor')
        anchor = anchor if isinstance(anchor, dict) else {}
        sheet = anchor.get('sheet')
        role = anchor.get('role')
        if role and sheet and old_sheet is not None and sheet == old_sheet \
                and role not in roles:
            return _(
                "tree_instance {name!r}: template {template!r} node {ref!r} "
                "anchors role {role!r} that the copied subtree does not place"
            ).format(name=instance_name, template=template_name, ref=ref,
                     role=role)
        return None
    if kind == 'net_trace':
        record = net_traces_by_name.get(ref) or {}
        missing = sorted(_net_trace_roles(record) - roles)
        if missing:
            return _(
                "tree_instance {name!r}: template {template!r} node {ref!r} "
                "copies a net_traces record whose role(s) {roles} the copied "
                "subtree does not place"
            ).format(name=instance_name, template=template_name, ref=ref,
                     roles=", ".join(repr(r) for r in missing))
    return None

# kicadstamp/tree_mounts.py
"""The rules of a kind "mount" node that are NOT geometry — plan_2026_11_03_add_mounts_table.

Four questions the mounts table asks before it writes anything, answered HERE and
nowhere else:

  1. WHEN ARE TWO MOUNTS THE SAME ONE (`mount_key`)? A mount is addressed by its
     anchor (role, sheet, cluster, pad) plus its own offset (xy/polar), rotation
     and shift. Two nodes with all of that equal are ONE point of reference
     written twice — the 22.10 duplicate (`fpga_107_vcca` and `mnt_fpga_vcca_107`
     on pad 107 of IC1) that `tools/merge_duplicate_mounts.py` cleans up.
     The rule lives HERE now (plan Д6: "the rule is ONE"): that tool imports it
     instead of keeping its own copy.

     NORMALIZATION (Д-1 of the 03.11 review, measured on the live profile): the
     s-expr writer OMITS default-valued fields, so the SAME mount can be spelled
     `(xy 0.0 0.0)` — as the HiPiMS profile spells both of its mounts — or with no
     `xy` at all, which is the canon a new node gets (Д-2). Both decode to a zero
     offset, so both must give ONE key, or Д6's «уже есть» would stay silent
     exactly on the duplicate it exists for. Therefore: an absent `xy`, an
     `xy (0, 0)` and a `polar` with radius 0 are all "no offset", and EVERY ANGLE
     is taken MODULO 360 (`normalized_rotation`, Т3.1 of the same review): a
     mount's `rotation` and a `polar` angle are frame angles for the nodes below,
     never a count of turns, so 360 ≡ 0 and −90 ≡ 270. That is not cosmetic — the
     3ch-awg-tia-v103 profile carries `rotation 360.0` on three mounts, and a
     duplicate written once as 360 and once as 0 would otherwise survive the very
     check that exists to find it.
     A NON-ZERO `xy` and a NON-ZERO `polar` are deliberately NOT equated with
     each other: turning one into the other needs the base's own angle, which is
     a placement question, not a structural equality.

  2. WHICH ROWS THE TABLE SHOWS (`pad_rows`, `filter_rows`): copper pads only
     (`domain.board.pad_has_copper` — a paste window is not a pad), ONE row per
     pad NUMBER because an anchor is addressed by the number (Д3: a TabPin
     repeats a number, and the anchor cannot tell the copies apart), the net
     shown but never edited (rule 26), and the net filter Д4 asks for.

     A repeated number is ONE row, and that can HIDE a real difference: the
     copies may sit on different nets. Such a row says so (`conflicting_nets`)
     instead of silently showing the first one.

  3. WHAT A ROW IS CALLED (`mount_name`, Д5): the template with `{pad}`
     substituted — through `net_resolution.resolve_placeholder`, the ONE owner of
     "`{placeholder}` with a refusal for an unknown one". The NET name is never
     substituted into a node name (Denis: «+2v5_vcca всё название испортит»).

  4. WHETHER THE BATCH MAY BE WRITTEN (`plan_mount_nodes`): all-or-nothing (Д7).
     The name rule of a mount is NOT the record rule — a mount's ref is a LOCAL
     NAME: unique inside its tree and not colliding with a positioned node's ref
     of that same tree (`trees._validate_local_refs`, the loader's own check).
     That check is therefore RUN here, on a copy of the target tree with the
     candidate nodes already attached, through `tree_from_dict` — the loader
     itself, with its own words — instead of a second copy of the rule that would
     drift from what the next Save/load enforces.

Qt-free on purpose: the dialog, the worker and the tool all read these answers,
so the rules cannot live in a widget.
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from .domain.board import pad_has_copper
from .exceptions import ValidationError
from .i18n import _
from .net_resolution import resolve_placeholder
from .trees import Tree, TreeAnchor, TreeNode, tree_from_dict, tree_to_dict

logger = logging.getLogger(__name__)

__all__ = [
    "MountAddress",
    "MountPlan",
    "MountRow",
    "filter_rows",
    "mount_key",
    "mount_key_of",
    "mount_key_of_dict",
    "mount_name",
    "mount_nodes",
    "normalized_rotation",
    "occupied_pads",
    "pad_number",
    "pad_rows",
    "plan_mount_nodes",
    "sits_on_its_pad",
]


# ── 1. "the same mount" ────────────────────────────────────────────────────

def pad_number(value) -> str:
    """A pad number as the anchor stores it — a STRING ('1', '17', 'A3').

    kipy hands a pad's number over as str/int/float depending on the source
    (`domain.board.Pad.number` says so), and a float that IS an integer would
    otherwise read '1.0' — a name that matches no anchor and no config pad.
    The anchor side is always a string (`trees.TreeAnchor.anchor_pad`), so this
    is the one place the two shapes meet."""
    number = getattr(value, "number", value)
    if isinstance(number, float) and number.is_integer():
        return str(int(number))
    return str(number)


#: Rounding of a normalized angle, in decimal places. Six is far below any angle a
#: user or a KiCad file expresses (1e-6°) and far above the float noise a rotate /
#: un-rotate round trip leaves behind (measured order: 1e-10 — see
#: tests/kicad/test_kipy_vector_rotate.py's own tolerance), so `359.9999999` from
#: arithmetic becomes the 0 it means, while `359.5` stays its own angle.
_ANGLE_DECIMALS = 6


def normalized_rotation(rotation) -> float:
    """An ANGLE as the mount rules compare them — taken MODULO 360.

    Т3.1 of plan_2026_11_03_add_mounts_table (found in the Т3 review): a mount's
    `rotation` is the FRAME angle its children hang in, never a count of turns, so
    `360` and `0` are the same orientation — and 3ch-awg-tia-v103 really carries
    `rotation 360.0` on three mounts. Without this the key saw a duplicate written
    once as 360 and once as 0 as two different mounts: exactly the duplicate the
    whole check exists for, and the one a live table would have created.

    Absent/None reads as 0.0 (the writer omits a zero), and `-0.0` becomes `0.0`
    (they compare equal, but the KEY is a JSON string and `-0.0` would print
    differently from `0.0`). The same function normalizes a `polar` angle, because
    that is an angle too — leaving it raw would recreate the very hole this closes
    in the other spelling.

    Public (not `_normalized_rotation`) because the mounts worker filters its
    instance-aware `occupied` map with THIS function — a second copy of the angle
    rule would drift the moment one of them changes."""
    angle = round(float(rotation or 0.0) % 360.0, _ANGLE_DECIMALS)
    # `% 360.0` never returns 360.0 itself, but rounding can: 359.9999999 % 360.0
    # is 359.9999999, and six decimals turn that back into a full turn.
    if angle == 360.0 or angle == 0.0:
        return 0.0
    return angle


def _normalized_anchor(anchor) -> Optional[list]:
    """(role, sheet, cluster, pad) of an anchor — a TreeAnchor OR the config
    dict's `{"role": ..., "sheet": ..., "cluster": ..., "pad": ...}` shape, both
    reduced to one list. None when this is not an anchor at all (or carries no
    role: a mount anchor without a role cannot be loaded)."""
    if anchor is None:
        return None
    if isinstance(anchor, Mapping):
        role = anchor.get("role")
        parts = (anchor.get("sheet"), anchor.get("cluster"), anchor.get("pad"))
    else:
        role = getattr(anchor, "role", None)
        parts = (getattr(anchor, "anchor_sheet", None),
                 getattr(anchor, "anchor_cluster", None),
                 getattr(anchor, "anchor_pad", None))
    if not role:
        return None
    sheet, cluster, pad = parts
    return [str(role), sheet, cluster,
            None if pad is None else pad_number(pad)]


def _normalized_offset(xy, polar) -> Optional[list]:
    """The offset of a mount node, normalized: None for "no offset" in ANY of the
    three spellings the grammar allows (absent, `xy (0, 0)`, `polar (0, …)`).

    A zero radius makes the angle meaningless — there is no direction to turn —
    so `polar (0, 90)` IS no offset, exactly like `xy (0, 0)`. A non-zero radius
    is kept verbatim, and its ANGLE goes through `normalized_rotation` (the
    offset's angle is an angle too — `polar (5, 360)` and `polar (5, 0)` point the
    same way). The two FORMS are never converted between each other: that
    conversion depends on the base's angle (see the module docstring)."""
    if xy is not None and len(xy) == 2:
        x, y = float(xy[0]), float(xy[1])
        if x or y:
            return ["xy", x, y]
    if polar is not None and len(polar) == 2:
        radius, angle = float(polar[0]), normalized_rotation(polar[1])
        if radius:
            return ["polar", radius, angle]
    return None


def mount_key(anchor, xy=None, polar=None, rotation=None, shift=None) -> Optional[str]:
    """Everything that decides where a mount node stands, as ONE comparable
    string — or None when this is not a mount address (no anchor, no role).

    Returned as a JSON string rather than a tuple so it is hashable, readable in
    a failure message, and identical to what the config-file tool has always
    compared (its own `_mount_key`, whose place this took).

    `shift` is the anchor's OWN local-mm shift (`(shift x y)`, tree-anchor-only
    per `trees._parse_anchor_shift`). It never reaches a mount node's anchor —
    the loader fatals on it — so `mount_key_of` passes nothing for it. It stays
    a parameter because the CONFIG-level spelling is what this key describes, and
    a key that silently stopped covering a field of its own input is exactly the
    kind of quiet change this module exists to prevent."""
    identity = _normalized_anchor(anchor)
    if identity is None:
        return None
    return json.dumps(
        [identity, _normalized_offset(xy, polar),
         normalized_rotation(rotation),
         None if shift is None else [float(shift[0]), float(shift[1])]],
        sort_keys=True)


def mount_key_of(node: TreeNode) -> Optional[str]:
    """`mount_key` of ONE TreeNode — None for every kind but "mount"."""
    if getattr(node, "kind", None) != "mount":
        return None
    return mount_key(node.anchor, node.xy, node.polar, node.rotation)


def mount_key_of_dict(node: Mapping) -> Optional[str]:
    """`mount_key` of ONE config-dict node — the tool's half (its nodes are dicts
    straight out of `config_writer.read_data`, never TreeNodes)."""
    if not isinstance(node, Mapping) or node.get("kind") != "mount":
        return None
    return mount_key(node.get("anchor"), node.get("xy"), node.get("polar"),
                     node.get("rotation", 0.0), node.get("shift"))


def mount_nodes(tree) -> list:
    """Every kind "mount" node of ONE tree, depth-first — the ONE walk over a
    tree's mounts. The table, the duplicate rule and the worker's `occupied` map
    all ask "which mounts does this tree have", and a second walk would be a
    second answer the day the nesting rules change."""
    out: list = []

    def walk(nodes) -> None:
        for node in nodes or ():
            if getattr(node, "kind", None) == "mount":
                out.append(node)
            walk(getattr(node, "children", ()))

    walk(getattr(tree, "nodes", ()))
    return out


def sits_on_its_pad(node) -> bool:
    """Does this mount STAND ON its anchor's pad — no offset and no turn?

    THE predicate of that question (Т4/§19 of the review: the worker's `occupied`
    map and `occupied_pads` below must not each spell the test out with the
    normalizers, or the two answers drift). Both halves are the module's own
    normalizers, so every spelling counts the same way the key does: an absent
    `xy`, `(xy 0, 0)` and `polar (0, …)` are all "no offset", and `rotation 360`
    / `-0.0` is no turn.

    False for any node that is not a mount: «стоит на паде» is a question about a
    mount, and answering it for a record node would let a caller use this
    predicate as a general "has no offset" test it was never meant to be."""
    if getattr(node, "kind", None) != "mount":
        return False
    offset = _normalized_offset(getattr(node, "xy", None),
                               getattr(node, "polar", None))
    return offset is None and normalized_rotation(getattr(node, "rotation", 0.0)) == 0.0


def occupied_pads(tree) -> dict[str, str]:
    """{mount_key: node ref} for every mount node of ONE tree THAT SITS on its pad
    — what Д6 asks «уже есть» about, scoped exactly as the duplicate rule is
    scoped: inside one tree. The same anchor in ANOTHER tree is a different point
    of reference and must not grey a row here.

    Only `sits_on_its_pad` nodes go in (Т4/§16): a mount with an offset on the same
    pad is a DIFFERENT point, so it does not occupy the row — the §8 cell «тот же
    якорь с НЕНУЛЕВЫМ смещением → свободна» is exactly this filter.

    First node wins on a repeat (which is itself a duplicate the loader refuses at
    the next Save); the caller only ever asks "is THIS key taken, and by what"."""
    out: dict[str, str] = {}
    for node in mount_nodes(tree):
        if not sits_on_its_pad(node):
            continue
        key = mount_key_of(node)
        if key is not None:
            out.setdefault(key, node.ref)
    return out


# ── 2. the table's rows ────────────────────────────────────────────────────

@dataclass(frozen=True)
class MountRow:
    """One row of the mounts table — a pad NUMBER (Д3), its net for DISPLAY only
    (rule 26: nets are never edited here), and the two facts that must not be
    swallowed by the one-row-per-number rule:

      * `pads_with_this_number` — how many physical pads carry it (a TabPin or a
        split pad; the anchor addresses the number, so they are one row);
      * `conflicting_nets` — those copies sit on DIFFERENT nets, so the single
        net column can only show one of them. Non-empty means the row is shown
        with a mark instead of pretending."""
    pad: str
    net: str = ""
    pads_with_this_number: int = 1
    conflicting_nets: tuple[str, ...] = ()


def pad_rows(pads: Iterable) -> list[MountRow]:
    """The table's rows for ONE footprint's pads, in the order the pads come
    (KiCad's own order — for a QFP that is the number order), copper only, one
    row per number.

    `pad_has_copper` (the ONE predicate of "is this a pad at all") drops a
    paste-only window and a TO-252 tab opening: an anchor cannot be seated on
    them, so offering them in the table would offer a mount that cannot resolve."""
    groups: dict[str, list] = {}
    for pad in pads or ():
        if not pad_has_copper(pad):
            continue
        groups.setdefault(pad_number(pad), []).append(pad)
    rows: list[MountRow] = []
    for number, group in groups.items():
        nets = sorted({str(p.net_name) for p in group if getattr(p, "net_name", None)})
        rows.append(MountRow(
            pad=number,
            net=str(getattr(group[0], "net_name", "") or ""),
            pads_with_this_number=len(group),
            conflicting_nets=tuple(nets) if len(nets) > 1 else ()))
    return rows


def filter_rows(rows: Sequence[MountRow], net_contains: str) -> list[MountRow]:
    """The rows whose NET contains `net_contains`, case-insensitively (Д4's
    "Net contains…"). An empty needle keeps every row — the filter is a
    narrowing, never a hidden default."""
    needle = (net_contains or "").strip().casefold()
    if not needle:
        return list(rows)
    return [row for row in rows if needle in row.net.casefold()]


# ── 3. the row's name ──────────────────────────────────────────────────────

def mount_name(template: str, pad: str) -> str:
    """The node name for `pad` from the name template (Д5), e.g.
    `fpga_vcca_{pad}` -> `fpga_vcca_107`.

    An EMPTY template means "name it by the pad number" — the shortest useful
    name, and the one Denis's own existing mounts use (`fpga_vccio_117` is a
    template of `fpga_vccio_{pad}`). Everything else goes through
    `resolve_placeholder`, the ONE owner of `{placeholder}` substitution and of
    the refusal for an unknown one — so `{net}` is refused in words here exactly
    as it is refused in a cell's net template, but with a hint that speaks of a
    mount name (there is no clone and no params mapping in this flow)."""
    text = (template or "").strip()
    if not text:
        return pad_number(pad)
    return resolve_placeholder(
        text, {"pad": pad_number(pad)}, what=_("mount name"),
        hint=_("a mount name knows only the {pad} placeholder — the pad number; "
               "the net name is not substituted, write its meaning into the "
               "template yourself"))


# ── 4. the batch ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MountAddress:
    """The instance every new mount of one batch is anchored to — the role anchor
    the file will carry (plan Д2: a Ref is only a way to FIND the instance; the
    anchor written is always the role one). `cluster`/`sheet` narrow the role
    exactly as they do in the config."""
    role: str
    cluster: Optional[str] = None
    sheet: Optional[str] = None


@dataclass
class MountPlan:
    """The answer of `plan_mount_nodes`: the nodes to append and, when the batch
    cannot be written, WHY — never half of both (Д7 "всё или ничего": a single
    refusal leaves `nodes` empty, so the caller cannot write a partial batch even
    by accident)."""
    nodes: list[TreeNode] = field(default_factory=list)
    names: list[tuple[str, str]] = field(default_factory=list)   # (pad, name)
    refusals: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals


def _mount_node(name: str, pad: str, address: MountAddress) -> TreeNode:
    """ONE new mount node: the role anchor, NO offset and NO rotation (the canon
    the s-expr writer emits for defaults — Д-2), no children.

    No offset rather than `xy (0, 0)`: `trees._node_to_sexp` omits default-valued
    fields, so a zero written explicitly would read back as the same mount but
    would make the file carry two spellings of one fact. `mount_key` treats them
    as equal either way — this is about not writing the noise in the first place."""
    return TreeNode(
        ref=name, kind="mount", xy=None, polar=None, rotation=0.0,
        name=None, group=None, children=[],
        anchor=TreeAnchor(role=address.role, is_origin=False,
                          anchor_sheet=address.sheet,
                          anchor_cluster=address.cluster,
                          anchor_pad=pad))


def _node_with_ref(nodes, ref: str):
    """The node whose ref is `ref`, at ANY depth — or None. Mount refs and record
    refs are unique per tree (`trees._validate_local_refs` / rule 2), so the
    first match is THE match."""
    for node in nodes or ():
        if getattr(node, "ref", None) == ref:
            return node
        found = _node_with_ref(getattr(node, "children", ()), ref)
        if found is not None:
            return found
    return None


def plan_mount_nodes(template: str, rows: Sequence[MountRow], *,
                     selected: Iterable[str], address: MountAddress,
                     tree: Tree, edited: Optional[Mapping[str, str]] = None,
                     parent_ref: Optional[str] = None,
                     occupied: Optional[Mapping[str, str]] = None
                     ) -> MountPlan:
    """The whole batch, validated BEFORE anything is handed to the writer.

    `selected` are the ticked pad numbers (as `MountRow.pad`), `edited` are the
    name cells the user typed by hand — those WIN over the template (Д5: a
    hand-edited cell is never overwritten by a template change), which is also
    why the plan can simply not re-name them.

    `parent_ref` — WHERE the batch will be attached (Д1: the command adds mounts
    under the tree's root, or under a NODE when it was called on one). None means
    the top level. This is not cosmetic (Т2.1(б) of the review): a mount node hung
    under a kind "copper" container is refused by the LOADER ("may hold only kind
    net_trace"), so a plan that only tried the top level would say "ok" about a
    tree the next Save cannot load. The candidates are therefore attached to a
    DEEP COPY of the tree at exactly this place, and the caller writes `nodes`
    where the plan tried them.

    `occupied` — {pad number: mount node ref} of THIS instance's already-taken
    pads, for the anchors whose sheet/cluster are spelled differently (Т2.1(в)).
    `mount_key` is STRUCTURAL on purpose (the offline tool of Т3 has no board and
    must not pretend otherwise): `(role FPGA)(pad 1)` and
    `(role FPGA)(sheet FPGA)(cluster FPGA)(pad 1)` are two keys, while on a board
    with one FPGA both are pad 1 of IC1. So the dialog resolves the tree's own
    mount anchors against the live board and hands the answer in here; the
    structural key stays the offline truth, and this map is the instance-aware
    one. A pad in it is refused with the same words as a structural hit.

    Refusals, in the order they are detected (the first one stops the batch —
    there is nothing to report about nodes that will not exist):

      * nothing ticked;
      * a ticked pad that is not a row of this table (a stale table);
      * two rows resolving to ONE name — a hand edit colliding with the template,
        or a template without `{pad}` over two pads;
      * a pad that already carries THIS mount in this tree (Д6: the dialog greys
        such a row, and this is the guard behind the greying — a batch must not
        be able to write the duplicate the whole задача is about), structurally
        OR by the instance-aware `occupied` map;
      * a `parent_ref` the tree does not have — nowhere to hang the new mounts;
      * anything the LOADER itself would refuse about the resulting tree — the
        local-name rule of a mount (unique in its tree, no collision with a
        positioned node's ref) and the copper container's "only net_trace
        children" rule, run through `tree_from_dict` on the COPY with the
        candidates attached at `parent_ref`, so the refusal is the loader's own
        wording and the next Save cannot discover a rule this plan did not know."""
    plan = MountPlan()
    if not address.role:
        plan.refusals.append(_(
            "the mounts cannot be addressed: no Role — a mount anchor is a role "
            "anchor, so pick an instance that carries one"))
        return plan

    by_pad = {row.pad: row for row in rows or ()}
    ticked = [pad_number(pad) for pad in (selected or ())]
    if not ticked:
        plan.refusals.append(_(
            "no pad is ticked — tick the pads this table should create mounts for"))
        return plan
    unknown = [pad for pad in ticked if pad not in by_pad]
    if unknown:
        plan.refusals.append(_(
            "pad(s) {pads} are not rows of this table — read the pads again").format(
                pads=", ".join(unknown)))
        return plan

    taken = occupied_pads(tree)
    edited = dict(edited or {})
    names: list[tuple[str, str]] = []
    owning: dict[str, list[str]] = {}
    for pad in ticked:
        name = (edited.get(pad) or "").strip() or mount_name(template, pad)
        names.append((pad, name))
        owning.setdefault(name, []).append(pad)
    clashes = {name: pads for name, pads in owning.items() if len(pads) > 1}
    if clashes:
        plan.refusals.append(_(
            "name(s) {names} would be used for more than one pad — a mount node's "
            "ref must identify exactly one node in the tree").format(
                names="; ".join("{name!r} (pads {pads})".format(
                    name=name, pads=", ".join(pads))
                    for name, pads in sorted(clashes.items()))))
        return plan

    nodes: list[TreeNode] = []
    for pad, name in names:
        node = _mount_node(name, pad, address)
        key = mount_key_of(node)
        existing = taken.get(key) or (occupied or {}).get(pad)
        if existing is not None:
            plan.refusals.append(_(
                "pad {pad!r} already carries mount node {ref!r} of tree {tree!r} — "
                "the same anchor with no offset cannot be added twice").format(
                    pad=pad, ref=existing, tree=getattr(tree, "name", "?")))
            return plan
        nodes.append(node)

    # THE LOADER'S OWN VERDICT on the tree that would be written (see docstring).
    # A DEEP copy at the caller's `parent_ref`: the candidates must be tried
    # exactly where they will be written (Д1), and a nested parent's own rules
    # (the copper container's "only net_trace children") are part of the verdict.
    probe = copy.deepcopy(tree)
    if parent_ref is None:
        probe.nodes.extend(nodes)
    else:
        parent = _node_with_ref(probe.nodes, parent_ref)
        if parent is None:
            plan.refusals.append(_(
                "tree {tree!r} has no node {ref!r} — the new mounts have nowhere "
                "to hang").format(tree=getattr(tree, "name", "?"), ref=parent_ref))
            return plan
        parent.children.extend(nodes)
    try:
        tree_from_dict(tree_to_dict(probe))
    except ValidationError as exc:
        plan.refusals.append(str(exc))
        return plan

    plan.nodes = nodes
    plan.names = names
    return plan

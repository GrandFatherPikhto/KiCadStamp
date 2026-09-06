# kicadstamp/cell_placement_copy.py
"""
cell_placement_copy.py — "Copy placement from cell": graft the PLACEMENT of a
source (donor) cell onto a target cell being edited in CellDock (plan
techdocs/handoff/deepseek/plan_2026_09_06_copy_placement_from_cell.md).

The real scenario (2026-09-06): the -5V PI filter cell `pif_n5v` was extracted
with its components but NO copper (0 vias / 0 tracks), while its structurally
identical positive twin `pif_p5v` carries the full copper. Copying pif_p5v's
placement into pif_n5v restores the missing copper from a known-good layout
instead of a fiddly live-board re-selection.

Semantics (confirmed with Denis; the offline, board-free counterpart of
"Import/Update from selection", a one-shot action, NOT a live link):
  1. Components — geometry OVERLAY by role: the source slot's geometric keys
     (offset_along_mm / offset_across_mm / angle_deg / layer) plus its
     per-component `vias` are overlaid onto the TARGET slot with the SAME role.
     The target's own net_template / net_template_pad / net_template_same_as_role
     are NEVER touched — those are rail-correct for the target (pif_n5v keeps
     its -5V literals) and baking a donor's foreign rail literals is exactly the
     bug net_autoresolve.md §4.3 warns about.
  2. Copper — ADDITIVE append of deep copies of the source's cell-level
     vias/tracks to the target (existing target copper untouched, Import-style).
     Nets are NOT resolved here: copied copper must be net_from_role(-pad)
     (role-relative -> the actual net is defined by the placing ENTITY instance
     at apply time, net_autoresolve.md §4.1) or a rule-net literal (GND,
     rail-independent). Any literal non-rule / parametrized / net-less copper
     record cannot be transported and makes the WHOLE copy a collected fatal —
     never a silent copy of garbage.

This module is PURE and Qt/board-free: it works on the same list-of-dicts
representation CellDock keeps (gui/docks/cell_editor.py's
self._components/_vias/_tracks), never mutates its inputs, and raises a single
collected ValidationError (format_fatal_error) on ANY structural problem —
matching build_refresh_plan/build_import_plan in cell_geometry_refresh.py.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterator

from .exceptions import ValidationError, format_fatal_error
from .i18n import _
from .net_resolution import RULE_NETS

__all__ = [
    "PlacementCopyPlan",
    "build_placement_copy_plan",
]

# Effective (model) defaults for the geometric keys a component overlay always
# rewrites — a stored 0 offset/angle is normally omitted from the cell by the
# extractor, and the loader reads a missing key as its default.
_GEO_DEFAULTS = {
    "offset_along_mm": 0.0,
    "offset_across_mm": 0.0,
    "angle_deg": 0.0,
}


@dataclass
class PlacementCopyPlan:
    """What build_placement_copy_plan computed for one source->target copy.

    component_updates — [(target_slot_dict, new_geo_dict)]: the target slot is
    the SAME dict object the caller passed in (from CellDock's _components);
    new_geo holds ONLY the geometric keys that actually differ (applying it as
    `record.update(new_geo)` can never touch a semantic key). Only slots whose
    donor geometry genuinely differs are listed — an identical overlay (e.g.
    pif_p5v -> pif_n5v components) contributes nothing, so Apply is a no-op on
    components and the copy is purely additive copper.
    new_via_records / new_track_records — brand-new deep copies of the donor's
    cell-level vias/tracks to APPEND (extend, never replace). Never the donor's
    own dict objects.
    skipped_roles — donor component roles absent from the target that are NOT
    referenced by the copied copper's net_from_role (harmless; reported in the
    preview, not a fatal).
    """
    component_updates: list[tuple[dict, dict]]
    new_via_records: list[dict] = field(default_factory=list)
    new_track_records: list[dict] = field(default_factory=list)
    skipped_roles: list[str] = field(default_factory=list)


def _iter_source_copper(source_components: list[dict], source_vias: list[dict],
                        source_tracks: list[dict]) -> Iterator[tuple[str, dict, str]]:
    """Yield (kind, record, where) for every copper record the copy would
    transport: the cell's own vias/tracks AND each component slot's
    per-component vias (they travel as part of that slot's overlay). `where`
    labels the record for error/preview messages: a top-level via/track or the
    per-component via of a named role."""
    for v in source_vias:
        yield "via", v, _("cell-level via")
    for t in source_tracks:
        yield "track", t, _("cell-level track")
    for c in source_components:
        role = c.get("role", "?")
        for v in c.get("vias") or []:
            yield "via", v, _("via of component {role!r}").format(role=role)


def _copper_net_ok(record: dict, kind: str) -> tuple[bool, str]:
    """Is one donor copper record's net definition safe to transport?

    Returns (ok, description). A record whose net is defined by net_from_role
    (role-relative -> auto-recalc on the target instance at apply) or by a
    rule-net literal (GND, rail-independent) is copyable as-is. A literal
    non-rule / parametrized / net-less record cannot — its net would either
    bake a foreign rail or be unresolvable — so the caller refuses the whole
    copy (never a silent garbage transport)."""
    role = record.get("net_from_role")
    if role:
        pad = record.get("net_from_role_pad")
        return True, f"net_from_role={role!r}" + (f"/pad:{pad}" if pad else "")
    net = record.get("net")
    if net is None:
        return False, _("{kind} has no net (net: null) — it can only inherit a "
                        "chain's rule net, which a copied cell cannot assume").format(kind=kind)
    if net in RULE_NETS:
        return True, f"net: {net} (rule net)"
    if "{" in str(net):
        return False, _("{kind} has a parametrized literal net {net!r} — "
                        "instance params cannot be transported by a placement copy").format(kind=kind, net=net)
    return False, _("{kind} has a literal net {net!r} that is rail-specific — "
                    "not copyable as-is").format(kind=kind, net=net)


def _geo_value(record: dict, field_name: str) -> Any:
    """A record's EFFECTIVE value for a geometric key — a stored float, or the
    model default when the key was never written (extract omits 0 offsets /
    angles; the loader reads a missing key as its default)."""
    return record.get(field_name, _GEO_DEFAULTS.get(field_name))


def _geo_differs(target: dict, field_name: str, new_value: Any) -> bool:
    """True when writing new_value would actually change the target slot — used
    so an identical donor geometry contributes NO update row (and Apply stays a
    no-op on components)."""
    if field_name == "layer":
        return str(target.get("layer")) != str(new_value)
    old = _geo_value(target, field_name)
    try:
        return abs(float(old) - float(new_value)) >= 1e-9
    except (TypeError, ValueError):
        return old != new_value


def _vias_equal(a: Any, b: Any) -> bool:
    try:
        return bool(a) == bool(b) and (not a or a == b)
    except Exception:  # noqa: BLE001 — defensive: never raise from a diff check
        return False


def _component_overlay(source: dict) -> dict:
    """The new_geo dict an overlay of `source` writes — explicit values for the
    always-present geometric keys (offset/angle, defaults when the source never
    wrote them) so a donor angle/offset fully replaces the target's, plus layer
    and per-component vias when the source carries them. NEVER includes a
    net_template* key."""
    new_geo: dict = {}
    for fld, default in _GEO_DEFAULTS.items():
        new_geo[fld] = source.get(fld, default)
    if source.get("layer") is not None:
        new_geo["layer"] = source["layer"]
    if source.get("vias"):
        new_geo["vias"] = deepcopy(source["vias"])
    return new_geo


def _component_overlay_differs(target: dict, source: dict, new_geo: dict) -> bool:
    """Does overlaying `source` onto `target` change anything? Checks every key
    new_geo would write (offset/angle/layer/per-component vias)."""
    for fld, new_value in new_geo.items():
        if fld == "vias":
            if not _vias_equal(target.get("vias"), new_value):
                return True
            continue
        if _geo_differs(target, fld, new_value):
            return True
    return False


def build_placement_copy_plan(source_components: list[dict],
                              source_vias: list[dict],
                              source_tracks: list[dict],
                              target_components: list[dict]) -> PlacementCopyPlan:
    """Build the full copy plan for one loaded target cell.

    source_components/source_vias/source_tracks — the donor cell's lists (as
    read from the config graph); target_components — the TARGET cell's current
    component slots (CellDock's own dict objects). Copper that would be copied
    (source vias/tracks and per-component vias) must be net_from_role(-pad) or a
    rule-net literal; every net_from_role role must exist among the target's
    component roles — otherwise a collected ValidationError
    (format_fatal_error), the same "never a silent copy of garbage" gate as
    Import/Refresh. Raises when the target has no components, or when there is
    genuinely nothing to copy. NEVER mutates its inputs; new records are
    deep copies (never the donor's own dicts).

    Component overlays are computed only for roles present in BOTH cells; a
    donor role absent from the target that the copied copper does not reference
    lands in PlacementCopyPlan.skipped_roles (reported, not fatal)."""
    target_by_role = {c.get("role"): c for c in target_components}
    if not target_by_role:
        raise ValidationError(format_fatal_error(
            _("cannot copy placement into a cell without components"),
            [_("the target cell (the one being edited) has no component slots — "
               "copy placement needs its roles to overlay the donor geometry and "
               "to resolve the copied copper's net_from_role")]))

    # ── Copper net audit: every record the copy would transport must be
    #    role-relative (net_from_role) or a rule-net literal. ─────────────
    problems: list[str] = []
    role_refs: list[str] = []      # roles copied copper references via net_from_role
    for kind, record, where in _iter_source_copper(
            source_components, source_vias, source_tracks):
        ok, description = _copper_net_ok(record, kind)
        if not ok:
            problems.append(f"{where}: {description}")
            continue
        role = record.get("net_from_role")
        if role:
            role_refs.append(str(role))

    missing_roles = sorted({r for r in role_refs if r not in target_by_role})
    for role in missing_roles:
        problems.append(_("copper net_from_role references role {role!r}, but "
                          "the target cell has no such component — copy would "
                          "be unresolvable garbage").format(role=role))

    if problems:
        raise ValidationError(format_fatal_error(
            _("cannot copy placement from this cell as-is"),
            problems))

    # ── Component geometry overlay by role ───────────────────────────────
    source_by_role = {c.get("role"): c for c in source_components}
    component_updates: list[tuple[dict, dict]] = []
    for role, target_slot in target_by_role.items():
        source_slot = source_by_role.get(role)
        if source_slot is None:
            continue
        new_geo = _component_overlay(source_slot)
        if new_geo and _component_overlay_differs(target_slot, source_slot, new_geo):
            component_updates.append((target_slot, new_geo))

    # ── Copper append ─────────────────────────────────────────────────────
    new_via_records = [deepcopy(v) for v in source_vias]
    new_track_records = [deepcopy(t) for t in source_tracks]

    if not component_updates and not new_via_records and not new_track_records:
        raise ValidationError(format_fatal_error(
            _("nothing to copy from this cell"),
            [_("the source cell has no overlapping component roles to overlay "
               "and no vias/tracks to add — pick a donor whose geometry differs "
               "or that carries copper")]))

    skipped_roles = sorted(set(source_by_role) - set(target_by_role) - set(missing_roles))
    return PlacementCopyPlan(
        component_updates=component_updates,
        new_via_records=new_via_records,
        new_track_records=new_track_records,
        skipped_roles=skipped_roles,
    )

# kicadstamp/cell_geometry_refresh.py
"""
cell_geometry_refresh.py — "Refresh geometry from selection": re-read an
EXISTING Cell's geometry from the live board selection (plan
techdocs/handoff/deepseek/plan_2026_09_03_cell_geometry_refresh.md).

The whole task works on the SAME list-of-dicts representation CellDock
(gui/docks/cell_editor.py) keeps in memory — self._components/_vias/_tracks.
Only the geometric keys of each matched record are recomputed
(offset_along_mm/offset_across_mm/angle_deg for components, plus
start/end/width for tracks); every other dict key (role, net*, params,
layer, per-component vias, ...) is copied over unchanged — the caller
applies a change as `record.update(new_geo)` on the SAME dict object, so the
invariant is structural, not just "don't forget".

Pure Qt-free module (same style as gui/docks/tree_from_selection.py's pure
helpers): no widgets, no Config, no cfg.chains/Entity — matching never needs
the Rule/Chain context (see plan §1.4.1: refresh lives in the
Entity/Tree/ClonePlacement world, mixing Chain/ManualSpoke would be wrong).
The live adapter is used ONLY to read Role fields and to resolve
net_from_role via net_resolution.resolve_net_from_role; nothing is ever
written through it.

Matching identity:
  - Components — by role (symmetric check: a role in the cell but not in the
    selection, OR in the selection but not in the cell, is a collected fatal).
  - Vias/tracks — by RESOLVED net, in deterministic tiers (plan §1.4.2):
      1. Parametrized literal nets (containing a str.format {placeholder},
         written at extract time via --net-template/--param) are matched BY
         TEMPLATE SHAPE (net_template_regex) — never by resolving the value,
         which the cell cannot know.
      2. Concrete nets (net_from_role resolved live, or a plain literal) —
         exact-string grouping.
      3. net: null records (rule-net convention, no stored net at all) —
         pure POSITIONAL elimination against whatever live items remain
         unclaimed after tiers 1-2 (no net lookup whatsoever; their net stays
         null).
      4. Any live item left unclaimed after 1-3 is extra copper not described
         by the cell -> collected fatal.
  Within each matching group of size > 1, a greedy nearest-neighbour by the
  record's CURRENT absolute position (origin + stored offset) pairs records to
  live items. Origin is the cell's single zero-offset component's live
  footprint position (the project's "zero-offset slot" convention, see
  placement/entity_placement.py) — a pure point, no rotation.

build_refresh_plan never mutates its inputs and collects EVERY structural
problem into a single ValidationError (format_fatal_error), never raising on
the first one.
"""
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .constants import ROLE_FIELD_NAME
from .domain.board import Footprint, Track, Via
from .domain.geometry import Vector2
from .exceptions import ValidationError, format_fatal_error
from .i18n import _
from .net_resolution import resolve_net_from_role
from .template_extraction import _selection_role_nets, _suggest_net_from_role
from .utils.units import MM

__all__ = [
    "ImportPlan",
    "RefreshPlan",
    "build_import_plan",
    "build_refresh_plan",
    "cell_content_bbox",
    "cell_zero_slot_role",
    "match_components",
    "net_template_regex",
    "normalize_cell_anchor_frame",
    "rebase_cell_anchor",
]

# One str.format placeholder, as resolve_placeholder (net_resolution.py)
# consumes them: {name}. Everything between the braces is irrelevant to the
# shape match — we only need to KNOW a placeholder occupies one net segment.
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")


def cell_zero_slot_role(components: list[dict]) -> str:
    """The role of the cell's own (local 0,0) component — the "zero-offset
    slot" convention (design §3), the same one entity_placement's
    _entity_own_zero_slot_live_position uses for the auto tree anchor. The
    component whose offset_along_mm/offset_across_mm are both 0.0 (missing
    keys default to 0.0, same as a freshly-extracted slot that never wrote
    them). Fatal when there is no such component or more than one — the
    refresh cannot pick its origin by guessing."""
    zero = [c for c in components
            if c.get("offset_along_mm", 0.0) == 0.0
            and c.get("offset_across_mm", 0.0) == 0.0]
    if not zero:
        raise ValidationError(format_fatal_error(
            _("no zero-offset component to use as the refresh origin"),
            [_("cell has no component with offset_along_mm/offset_across_mm both "
               "equal to 0 (its local (0,0)) to act as the origin of the refresh — "
               "select a cell whose origin component is at (0,0)")]
        ))
    if len(zero) > 1:
        raise ValidationError(format_fatal_error(
            _("{n} zero-offset components — the refresh origin is ambiguous")
            .format(n=len(zero)),
            [_("exactly one component must sit at the cell's local (0,0); found "
               "{n} — fix the cell so a single component has offset 0/0")
             .format(n=len(zero))]
        ))
    return zero[0]["role"]


def match_components(components: list[dict], role_to_ref: dict[str, str]
                     ) -> tuple[list[dict], list[str], list[str]]:
    """Symmetric component matching by role. Returns
    (matched_records, missing_roles, extra_roles):
      - matched_records — the cell's component dicts whose role IS present in
        role_to_ref (in cell order), i.e. the ones a refresh update applies to.
      - missing_roles — cell roles absent from the selection (sorted).
      - extra_roles — selection roles absent from the cell (sorted).
    Fatality is the caller's job: ANY non-empty missing/extra list makes the
    whole refresh fatal (design §2.3), with the lists spelled out in the
    message."""
    cell_roles = {c.get("role") for c in components}
    selection_roles = set(role_to_ref)
    missing = sorted(cell_roles - selection_roles)
    extra = sorted(selection_roles - cell_roles)
    matched = [c for c in components if c.get("role") in role_to_ref]
    return matched, missing, extra


def _is_parametrized(net: Any) -> bool:
    """A literal net containing a str.format placeholder (the syntax
    resolve_placeholder / parametrize_net write, see net_resolution.py) cannot
    be resolved to a concrete net without the instance's own params:, which
    the cell does not carry. net is None (the rule-net convention) is NOT
    parametrized."""
    return isinstance(net, str) and "{" in net


def net_template_regex(template: str) -> re.Pattern[str]:
    """Turn a parametrized net_template literal like '/Channel_{channel}/DAC/DB0'
    into a regex recognizing ANY concrete instantiation of it — WITHOUT ever
    resolving what a placeholder's actual value is. Each {name} becomes one
    capture of a single net-hierarchy segment ([^/]+ — a net path segment
    never itself contains '/'), everything else is regex-escaped literally,
    and the whole pattern is anchored (^...$) so an extra/missing segment is
    NOT a match."""
    parts = _PLACEHOLDER_RE.split(template)
    placeholder_count = len(_PLACEHOLDER_RE.findall(template))
    pattern = "".join(
        re.escape(part) + ("([^/]+)" if i < placeholder_count else "")
        for i, part in enumerate(parts)
    )
    return re.compile(f"^{pattern}$")


# ── Live-position helpers (nm) ──────────────────────────────────────────────

def _record_offset(record: dict, origin: Vector2, along_key: str,
                   across_key: str) -> tuple[int, int]:
    """The CURRENT absolute (nm) position of a cell record's point: origin +
    its stored mm offset. round() guards float drift; extract itself rounds to
    micrometre precision."""
    return (origin.x + int(round(float(record.get(along_key, 0.0)) * MM)),
            origin.y + int(round(float(record.get(across_key, 0.0)) * MM)))


def _live_point(item: Any, which: str) -> tuple[int, int]:
    """A live Via.position / Track.start / Track.end as a plain (x, y) nm
    tuple."""
    pt = item.position if isinstance(item, Via) else getattr(item, which)
    return (pt.x, pt.y)


def _point_dist_sq(a: tuple[int, int], b: tuple[int, int]) -> int:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def _greedy_nearest(records: list[dict], live_items: list[Any],
                    origin: Vector2, kind: str) -> list[tuple[dict, Any]]:
    """Greedy nearest-neighbour pairing (O(n^2), n is a handful): each record
    (in fixed order) claims the closest still-unclaimed live item. Distance for
    a via is its single point; for a track, the sum of both endpoints' squared
    distances, minimised over start/end orientation (a re-routed segment may
    have flipped direction). Returns [(record, live_item), ...]."""
    if kind == "via":
        rec_pts = [_record_offset(r, origin, "offset_along_mm", "offset_across_mm")
                   for r in records]
        live_pts = [_live_point(i, "position") for i in live_items]
    else:
        rec_pts = [(_record_offset(r, origin, "start_along_mm", "start_across_mm"),
                    _record_offset(r, origin, "end_along_mm", "end_across_mm"))
                   for r in records]
        live_pts = [(_live_point(i, "start"), _live_point(i, "end"))
                    for i in live_items]

    def _dist(rp, lp) -> int:
        if kind == "via":
            return _point_dist_sq(rp, lp)
        (rs, re_), (ls, le) = rp, lp
        return min(_point_dist_sq(rs, ls) + _point_dist_sq(re_, le),
                   _point_dist_sq(rs, le) + _point_dist_sq(re_, ls))

    taken = [False] * len(live_items)
    pairs: list[tuple[dict, Any]] = []
    for rp, rec in zip(rec_pts, records):
        best_idx = None
        best_d = None
        for j, lp in enumerate(live_pts):
            if taken[j]:
                continue
            d = _dist(rp, lp)
            if best_d is None or d < best_d:
                best_d, best_idx = d, j
        if best_idx is not None:
            taken[best_idx] = True
            pairs.append((rec, live_items[best_idx]))
    return pairs


def _mm(value: float) -> float:
    """nm -> mm at the extractor's rounding precision (4 decimals)."""
    return round(value / MM, 4)


def _via_new_geo(live: Via, origin: Vector2) -> dict:
    return {
        "offset_along_mm": _mm(live.position.x - origin.x),
        "offset_across_mm": _mm(live.position.y - origin.y),
    }


def _track_new_geo(live: Track, origin: Vector2) -> dict:
    return {
        "start_along_mm": _mm(live.start.x - origin.x),
        "start_across_mm": _mm(live.start.y - origin.y),
        "end_along_mm": _mm(live.end.x - origin.x),
        "end_across_mm": _mm(live.end.y - origin.y),
        "width_mm": round(live.width_mm, 4),
    }


def _component_new_geo(fp: Footprint, origin: Vector2) -> dict:
    return {
        "offset_along_mm": _mm(fp.position.x - origin.x),
        "offset_across_mm": _mm(fp.position.y - origin.y),
        "angle_deg": fp.angle_deg,
    }


# ── Copper tiers (shared by vias and tracks, kept separate) ────────────────

def _match_copper(records: list[dict], live_items: list[Any], origin: Vector2,
                  role_to_ref: dict[str, str], adapter: Any, kind: str,
                  *,
                  leftover_is_fatal: bool = True,
                  ) -> tuple[list[tuple[dict, dict]], list[str], list[Any]]:
    """Match one copper section (kind: 'via' | 'track') through the four tiers
    of plan §1.4.2. Returns (updates, problems, leftover) — never raises here;
    the caller raises once with ALL problems collected. Does not mutate
    `records` or `live_items`.

    leftover_is_fatal — the tier-4 treatment of whatever live items the named
    tiers 1-3 left unclaimed:
      - True (Refresh): leftover is "extra copper not described by the cell"
        — every leftover item becomes a collected problem, `leftover` is [].
      - False (Import vias/tracks): leftover is RETURNED as the third element
        — the caller (build_import_plan) turns each into a NEW record instead
        of a fatal. The count-mismatch fatals of tiers 1-3 are NOT softened
        in this mode: only tier 4 changes, never a named-net check.
    """
    updates: list[tuple[dict, dict]] = []
    problems: list[str] = []
    # A mutable pool of live items, claimed as tiers consume them.
    pool = list(live_items)

    def _claim(matched: list[Any]) -> None:
        """Drop exactly the matched live items from the pool (identity-based;
        the same item object appears in pool only once)."""
        claimed = {id(m) for m in matched}
        pool[:] = [i for i in pool if id(i) not in claimed]

    # Split the cell's records into the three net families.
    parametrized: list[tuple[str, dict]] = []   # (template, record)
    concrete: list[dict] = []
    net_null: list[dict] = []
    for rec in records:
        if rec.get("net_from_role") is not None:
            concrete.append(rec)
        elif _is_parametrized(rec.get("net")):
            parametrized.append((rec["net"], rec))
        elif rec.get("net") is None:
            net_null.append(rec)
        else:
            concrete.append(rec)

    # ── Tier 1: parametrized templates — match by SHAPE, stable order ─────
    # Records with the SAME template string are one group (several vias can
    # share one parametrized net). Groups processed in first-appearance order;
    # whoever claims a live item first owns it (no double use by construction).
    by_template: dict[str, list[dict]] = {}
    for template, rec in parametrized:
        by_template.setdefault(template, []).append(rec)
    for template, group in by_template.items():
        regex = net_template_regex(template)
        candidates = [i for i in pool if i.net_name is not None
                      and regex.fullmatch(i.net_name)]
        if len(group) != len(candidates):
            problems.append(_("template {pattern!r}: {n} record(s) in the cell, "
                              "{m} live item(s) matching its shape")
                            .format(pattern=template, n=len(group), m=len(candidates)))
            continue
        for rec, live in _greedy_nearest(group, candidates, origin, kind):
            new_geo = _via_new_geo(live, origin) if kind == "via" \
                else _track_new_geo(live, origin)
            updates.append((rec, new_geo))
        _claim(candidates)

    # ── Tier 2: concrete nets (net_from_role resolved, or plain literal) ──
    existing_by_net: dict[str, list[dict]] = {}
    for rec in concrete:
        if rec.get("net_from_role") is not None:
            try:
                net = resolve_net_from_role(
                    rec["net_from_role"], rec.get("net_from_role_pad"),
                    role_to_ref, adapter)
            except ValidationError as e:
                problems.append(str(e))
                continue
        else:
            net = rec["net"]
        if net is None:
            # A net_from_role that resolved to nothing (unreachable — that
            # resolver fatals) — defensive; treat as rule-net fallback.
            net_null.append(rec)
            continue
        existing_by_net.setdefault(net, []).append(rec)

    live_by_net: dict[str, list[Any]] = {}
    for i in pool:
        if i.net_name is not None:
            live_by_net.setdefault(i.net_name, []).append(i)

    for net in sorted(existing_by_net):
        existing = existing_by_net[net]
        live = live_by_net.get(net, [])
        if len(existing) != len(live):
            problems.append(_("net {net!r}: {n} record(s) in the cell, "
                              "{m} live item(s)")
                            .format(net=net, n=len(existing), m=len(live)))
            continue
        for rec, live_item in _greedy_nearest(existing, live, origin, kind):
            new_geo = _via_new_geo(live_item, origin) if kind == "via" \
                else _track_new_geo(live_item, origin)
            updates.append((rec, new_geo))
        _claim(live)

    # ── Tier 3: net: null records — pure positional elimination ───────────
    # Whatever remains in the pool after tiers 1-2 is copper not claimed by any
    # NAMED net; net:null records have no name at all, so if their count equals
    # the leftover count they are the leftover's counterparts (matched by
    # position only, no net involved — their net stays null).
    if net_null:
        if len(net_null) != len(pool):
            problems.append(_("rule-net (net: null — inherits the enclosing "
                              "Rule's own net): {n} record(s) in the cell, "
                              "{m} unclaimed live item(s) after resolving every "
                              "named net")
                            .format(n=len(net_null), m=len(pool)))
        else:
            for rec, live in _greedy_nearest(net_null, pool, origin, kind):
                new_geo = _via_new_geo(live, origin) if kind == "via" \
                    else _track_new_geo(live, origin)
                updates.append((rec, new_geo))
            _claim(pool)

    # ── Tier 4: anything still unclaimed ──────────────────────────────────
    # Refresh: extra copper (collected fatal, nothing imported). Import:
    # returned as leftover — the caller turns each into a NEW record.
    if leftover_is_fatal:
        for i in pool:
            problems.append(_("extra copper in selection: {desc} — not described "
                              "by any net/template/net:null record of this cell")
                            .format(desc=_live_description(i, kind)))
        return updates, problems, []
    return updates, problems, list(pool)


def _live_description(item: Any, kind: str) -> str:
    net = item.net_name
    net_desc = net if net is not None else _("(no net)")
    return _("{kind} on {net}").format(kind=kind, net=net_desc)


@dataclass
class RefreshPlan:
    """What build_refresh_plan computed — pairs of (record_dict, new_geo_dict).
    record_dict is the SAME dict object the caller passed in (component/via/
    track record from CellDock's lists); new_geo_dict holds ONLY the geometric
    keys to write onto it (offset/angle/width) — applying it as
    `record.update(new_geo)` can never touch a semantic key. The caller owns
    the actual mutation + undo/preview story."""
    component_updates: list[tuple[dict, dict]]
    via_updates: list[tuple[dict, dict]]
    track_updates: list[tuple[dict, dict]]


@dataclass
class ImportPlan:
    """What build_import_plan computed — brand-NEW via/track records (already
    shaped like extract_template_from_selection writes a single via/track, with
    geometry relative to the same zero-offset origin RefreshPlan uses) plus no
    change to any existing record. The caller (CellDock) appends these to its
    _vias/_tracks on Apply — extend, never replace."""
    new_via_records: list[dict]
    new_track_records: list[dict]


def _cell_selection_context(components: list[dict], footprints: list[Footprint],
                            adapter: Any, action_label: str,
                            origin_role: str | None = None,
                            ) -> tuple[dict[str, str], list[dict], Vector2 | None,
                                       list[str]]:
    """Shared origin/role prelude for BOTH refresh and import (plan §B.2:
    "Import ТРЕБУЕТ тот же чистый матчинг компонентов по ролям, что и
    Refresh" — a missing/extra role is the same "wrong/incomplete cluster"
    fatal in both).

    Returns (role_to_ref, matched_components, origin_or_None, problems).
    Never raises itself; a zero/multiple zero-slot cell raises via
    cell_zero_slot_role (a cell-local defect, nothing can proceed without a
    known origin). Collected role problems are the caller's to merge with its
    copper-tier problems into ONE ValidationError."""
    problems: list[str] = []
    # role uniqueness among the selected footprints (mirrors extract's own
    # fatal — a duplicated Role cannot be matched 1:1).
    role_to_ref: dict[str, str] = {}
    for fp in footprints:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            problems.append(_("{ref}: no {field!r} field — every selected "
                              "component must have a Role for a {action}")
                            .format(ref=fp.ref, field=ROLE_FIELD_NAME,
                                    action=action_label))
            continue
        if role in role_to_ref:
            problems.append(_("role {role!r} appears twice in selection: "
                              "{ref1!r} and {ref2!r} — roles must be unique")
                            .format(role=role, ref1=role_to_ref[role], ref2=fp.ref))
            continue
        role_to_ref[role] = fp.ref

    matched, missing, extra = match_components(components, role_to_ref)
    for role in missing:
        problems.append(_("role {role!r} is in the cell but not in the "
                          "selection — {action} needs the whole cluster")
                        .format(role=role, action=action_label))
    for role in extra:
        problems.append(_("role {role!r} is in the selection but not in the "
                          "cell — {action} cannot add components")
                        .format(role=role, action=action_label))

    # Origin surrogate — the live reference the whole refresh is measured from.
    #   - origin_role set (v2: the cell's anchor_role, the MOUNT component): the
    #     surrogate KEEPS its stored offset (frame-preserving) — every recomputed
    #     offset = surrogate_stored + (live − live_surrogate), so the cell's own
    #     frame and its anchor stay truthful even when the mount is NOT at the
    #     stored (0,0).
    #   - origin_role None (legacy): the single zero-offset slot component
    #     (cell_zero_slot_role) is the surrogate and lands on stored (0,0),
    #     exactly as before design_2026_09_05.
    # A zero/multiple zero-slot LEGACY cell raises immediately
    # (cell_zero_slot_role). An origin ROLE absent from the selection is a
    # structural problem (wrong cluster selected), collected like the rest.
    if origin_role is not None:
        comp = next((c for c in components if c.get("role") == origin_role), None)
        if comp is None:
            problems.append(_("role {role!r} (the cell's anchor origin) is not a "
                              "component of this cell").format(role=origin_role))
            surrogate_along = surrogate_across = 0.0
        else:
            surrogate_along = float(comp.get("offset_along_mm", 0.0))
            surrogate_across = float(comp.get("offset_across_mm", 0.0))
        origin_label = _("role {role!r} (the cell's anchor origin)").format(
            role=origin_role)
    else:
        origin_role = cell_zero_slot_role(components)
        surrogate_along = surrogate_across = 0.0
        origin_label = _("role {role!r} (the cell's zero-offset origin)").format(
            role=origin_role)
    origin_ref = role_to_ref.get(origin_role)
    origin: Vector2 | None = None
    ref_to_fp = {fp.ref: fp for fp in footprints}
    if origin_ref is None:
        problems.append(_("{label} is not in the current selection").format(
            label=origin_label))
    else:
        origin_fp = ref_to_fp.get(origin_ref)
        if origin_fp is None:
            problems.append(_("footprint {ref!r} ({label}) not found in the "
                              "selection").format(ref=origin_ref, label=origin_label))
        else:
            # Effective origin = live surrogate − its stored offset, so every
            # recomputed offset = (live − origin) puts the surrogate back on its
            # STORED offset (frame preserved); legacy (0,0) keeps old behaviour.
            origin = Vector2.from_xy(
                origin_fp.position.x - int(surrogate_along * MM),
                origin_fp.position.y - int(surrogate_across * MM))
    return role_to_ref, matched, origin, problems


def build_refresh_plan(components: list[dict], vias: list[dict], tracks: list[dict],
                       footprints: list[Footprint], raw_via_items: list[Via],
                       raw_track_items: list[Track], adapter: Any,
                       net_template_map: dict[str, str] | None = None,
                       origin_role: str | None = None,
                       ) -> RefreshPlan:
    """Build the full refresh plan for one loaded cell.

    components/vias/tracks — the CURRENT lists of dicts (CellDock's own
    representation); footprints — the selected live Footprint DTOs (as
    adapter.get_selected_items() returns); raw_via_items/raw_track_items — the
    selected Via/Track DTOs split by kind (vias and tracks are independent,
    never matched against each other). adapter reads Role fields and resolves
    net_from_role.

    Raises ValidationError (format_fatal_error, EVERY problem collected into
    one message — missing/extra roles AND every per-net/template/net:null
    count mismatch AND every bit of extra copper) on any structural mismatch.
    Never mutates its inputs. net_template_map is accepted for signature
    symmetry with extract_template_from_selection but unused in v1: the cell
    does not store params, so existing parametrized literals are handled by
    template-shape matching (§1.4), which needs no map.
    """
    role_to_ref, matched, origin, problems = _cell_selection_context(
        components, footprints, adapter,
        _("refresh"), origin_role)

    # Components — every matched role recomputed from its own live footprint.
    # Built only once the origin is known (its position is the reference for
    # every recomputed offset); matched is [] when role problems were found,
    # so a pure-role problem run yields no half-built geometry.
    component_updates: list[tuple[dict, dict]] = []
    if origin is not None and matched:
        ref_to_fp = {fp.ref: fp for fp in footprints}
        for rec in matched:
            fp = ref_to_fp[role_to_ref[rec["role"]]]
            component_updates.append((rec, _component_new_geo(fp, origin)))

    # Vias / tracks — independent sections. Run only when the origin resolved
    # (the nearest-match it feeds needs a reference point); an unresolvable
    # origin is already reported loudly above as the wrong-cluster problem.
    via_updates: list[tuple[dict, dict]] = []
    track_updates: list[tuple[dict, dict]] = []
    via_problems: list[str] = []
    track_problems: list[str] = []
    if origin is not None:
        via_updates, via_problems, _via_leftover = _match_copper(
            vias, raw_via_items, origin, role_to_ref, adapter, "via")
        track_updates, track_problems, _track_leftover = _match_copper(
            tracks, raw_track_items, origin, role_to_ref, adapter, "track")

    # EVERY problem collected into ONE message (design §2.3-2.5) — role
    # mismatches and every per-net/template/net:null count problem and every
    # extra-copper item, never the first one only.
    all_problems = problems + via_problems + track_problems
    if all_problems:
        raise ValidationError(format_fatal_error(
            _("cannot refresh cell geometry from the current selection"),
            all_problems))

    return RefreshPlan(
        component_updates=component_updates,
        via_updates=via_updates,
        track_updates=track_updates,
    )


def _import_via_record(live: Via, origin: Vector2) -> dict:
    """New via dict in the exact shape extract_template_from_selection writes
    (template_extraction.py:531-541) — geometry + physical, net filled by the
    caller via _classify_import_net (which needs role nets + cell components)."""
    return {
        "offset_along_mm": _mm(live.position.x - origin.x),
        "offset_across_mm": _mm(live.position.y - origin.y),
        "drill_mm": round(live.drill_mm, 4),
        "diameter_mm": round(live.diameter_mm, 4),
    }


def _import_track_record(live: Track, origin: Vector2) -> dict:
    """New track dict in the exact shape extract writes (template_extraction.py:
    570-583) — geometry + width; net filled by _classify_import_net."""
    return {
        "start_along_mm": _mm(live.start.x - origin.x),
        "start_across_mm": _mm(live.start.y - origin.y),
        "end_along_mm": _mm(live.end.x - origin.x),
        "end_across_mm": _mm(live.end.y - origin.y),
        "width_mm": round(live.width_mm, 4),
    }


def _classify_import_net(record: dict, live: Via | Track, role_nets: dict,
                         components: list[dict]) -> dict:
    """Fill the net field(s) of a NEW record by reusing the extractor's own
    classifier 1:1 (plan §B.2 — never a parallel implementation): build
    selection_role_nets once, then for each live item call _suggest_net_from_role
    the SAME way extract_template_from_selection's via/track loops do
    (template_extraction.py:516-541). live_points = the record's local
    geometry (via: 1 point, track: both endpoints), components = the CELL's
    component dicts (the geometric tiebreak reference, same as extract's
    already-built slot list).

    Import NEVER writes `net: null` — structurally impossible here, not just
    off by default: Import works exclusively in the ClonePlacement/Entity world
    where via.net=None is fatal always (clone_geometry.py), regardless of the
    cell's context. rule_nets is deliberately NOT threaded through (unlike
    ordinary extraction, where net:null = "inherit the Chain's net" is
    legitimate); the shared classifier is handed an EMPTY rule-nets set so its
    `if net in rule_nets: return None, None` short-circuit can never fire.
    Every live net therefore ends as net_from_role(+pad) when a selected
    role's pad genuinely carries it, or as a plain literal `net:` otherwise —
    never None.
    Never raises: _suggest_net_from_role swallows ambiguity into (None, None),
    which means "literal net" here — same graceful fallback as extract."""
    if live.net_name is None:
        return record  # no-net live item — leave net unset (blank rule-net)
    if "start_along_mm" in record:  # a track — two endpoints for the tiebreak
        points = [(float(record["start_along_mm"]), float(record["start_across_mm"])),
                  (float(record["end_along_mm"]), float(record["end_across_mm"]))]
    else:  # a via — its single point
        points = [(float(record["offset_along_mm"]), float(record["offset_across_mm"]))]
    role, pad = _suggest_net_from_role(
        role_nets, live.net_name, set(), points, components)
    if role is not None:
        record["net_from_role"] = role
        if pad is not None:
            record["net_from_role_pad"] = pad
    else:
        record["net"] = live.net_name
    return record


def build_import_plan(components: list[dict], vias: list[dict], tracks: list[dict],
                      footprints: list[Footprint], raw_via_items: list[Via],
                      raw_track_items: list[Track], adapter: Any,
                      origin_role: str | None = None) -> ImportPlan:
    """Build the plan for "Import vias/tracks from selection": append NEW via/
    track records to an EXISTING cell for live copper its current records do
    not describe — the additive counterpart of build_refresh_plan (Refresh
    cannot ADD a record by design; Import never MODIFIES/removes an existing
    one, plan §B.2).

    Existing records are matched through the SAME tiers as refresh
    (_match_copper), but tier 4 (extra copper) is NOT fatal here: whatever
    live via/track tiers 1-3 leave unclaimed is turned into a NEW record
    instead. Count-mismatch fatals of tiers 1-3 and the symmetric component
    role match are NOT softened — both stay fatal, exactly like refresh.

    Every new record's net is classified by the extractor's own heuristic
    (_selection_role_nets + _suggest_net_from_role, plan §B.2), but Import
    NEVER writes `net: null` — structurally impossible, not merely disabled by
    default: Import is exclusively a ClonePlacement/Entity-world feature, where
    via.net=None is fatal ALWAYS (clone_geometry.py), independent of any
    specific cell's context. rule_nets is deliberately not threaded through
    (unlike ordinary extraction, where net:null = "inherit the Chain's net" is
    legitimate). A net a selected role's pad carries -> net_from_role(+pad);
    anything else -> a plain literal `net:` — never None. Never mutates its
    inputs.
    """
    role_to_ref, _matched, origin, problems = _cell_selection_context(
        components, footprints, adapter,
        _("import"), origin_role)

    via_leftover: list[Any] = []
    track_leftover: list[Any] = []
    via_problems: list[str] = []
    track_problems: list[str] = []
    if origin is not None:
        _via_updates, via_problems, via_leftover = _match_copper(
            vias, raw_via_items, origin, role_to_ref, adapter, "via",
            leftover_is_fatal=False)
        _track_updates, track_problems, track_leftover = _match_copper(
            tracks, raw_track_items, origin, role_to_ref, adapter, "track",
            leftover_is_fatal=False)

    all_problems = problems + via_problems + track_problems
    if all_problems:
        raise ValidationError(format_fatal_error(
            _("cannot import vias/tracks from the current selection"),
            all_problems))

    # Net classification — build the role -> pad -> nets map ONCE from the
    # selection (extractor's _selection_role_nets), then classify each leftover
    # via/track against the CELL's own components (geometric tiebreak).
    role_nets = _selection_role_nets(adapter, footprints) if origin is not None else {}
    new_via_records = []
    for live in via_leftover:
        rec = _import_via_record(live, origin)
        _classify_import_net(rec, live, role_nets, components)
        new_via_records.append(rec)
    new_track_records = []
    for live in track_leftover:
        rec = _import_track_record(live, origin)
        _classify_import_net(rec, live, role_nets, components)
        new_track_records.append(rec)

    return ImportPlan(
        new_via_records=new_via_records,
        new_track_records=new_track_records,
    )


def rebase_cell_anchor(entry: dict, ax_mm: float, ay_mm: float,
                       role: str, pad: str | None = None) -> dict:
    """Shift EVERY local along/across offset in a Cell's dict representation
    (entry — the SAME shape cell_editor's _build_cell_dict()/load_cell()
    builds: components/vias/tracks/clone_placements keys) by (-ax_mm, -ay_mm),
    so that the point formerly at (ax_mm, ay_mm) becomes the new local (0,0).
    This is the "assign/move a Cell's REAL internal anchor" operation (design
    2026-09-04_cell_internal_anchor.md §2.2): a one-shot MUTATION of the
    stored offsets instead of a change to any placement formula — every
    existing resolver (materialization, ClonePlacement/Rules) just keeps
    reading the (now shifted) local offsets and sees the new origin for free.

    Records role/pad into entry["anchor_role"]/entry["anchor_pad"] afterwards —
    the same bookkeeping fields the extractor/cell editor write, now TRUTHFULLY
    describing the origin again — and clears any stale anchor_xy/anchor_pad a
    PREVIOUS anchor left behind (the three anchor forms are mutually exclusive;
    load_cell() fatals on their mixture).

    Pure data transform — no adapter, no live board, no Config. Returns a NEW
    dict (copy.deepcopy), NEVER mutates the caller's dict in place — the same
    "don't mutate your inputs" discipline build_refresh_plan already follows.
    """
    roles = {c.get("role") for c in entry.get("components", [])}
    if not role:
        raise ValidationError(format_fatal_error(
            _("rebase cell anchor: role is required"),
            [_("pick which of the cell's own components the new local (0,0) "
               "should sit at")]))
    if role not in roles:
        raise ValidationError(format_fatal_error(
            _("rebase cell anchor: role {role!r} is not a component of this cell")
            .format(role=role),
            [_("anchor_role must name one of the cell's own components: {roles}")
             .format(roles=sorted(r for r in roles if r))]))

    out = deepcopy(entry)

    def _shift(record: dict) -> None:
        """Subtract (ax_mm, ay_mm) from a local along/across record."""
        record["offset_along_mm"] = round(record.get("offset_along_mm", 0.0) - ax_mm, 9)
        record["offset_across_mm"] = round(record.get("offset_across_mm", 0.0) - ay_mm, 9)

    # Components (+ each component's OWN vias).
    for c in out.get("components", []):
        _shift(c)
        for via in c.get("vias", []):
            _shift(via)
    # Cell-level vias.
    for via in out.get("vias", []):
        _shift(via)
    # Cell-level tracks — both endpoints.
    for t in out.get("tracks", []):
        t["start_along_mm"] = round(t.get("start_along_mm", 0.0) - ax_mm, 9)
        t["start_across_mm"] = round(t.get("start_across_mm", 0.0) - ay_mm, 9)
        t["end_along_mm"] = round(t.get("end_along_mm", 0.0) - ax_mm, 9)
        t["end_across_mm"] = round(t.get("end_across_mm", 0.0) - ay_mm, 9)
    # Nested clone_placements (CellPlacement): Cartesian xy only — no polar
    # fields exist on this type (config/entries.py's _CELL_PLACEMENT_KNOWN_KEYS
    # whitelist has no radius_mm/angle_deg; any would fatal at load), so shift
    # xy when present and skip a record without it (loader-defaulted (0,0) is
    # not a stored field worth inventing here).
    for cp in out.get("clone_placements", []):
        if "xy" in cp and cp["xy"] is not None:
            cp["xy"] = [round(cp["xy"][0] - ax_mm, 9),
                        round(cp["xy"][1] - ay_mm, 9)]

    out["anchor_role"] = role
    if pad:
        out["anchor_pad"] = pad
    else:
        out.pop("anchor_pad", None)
    out.pop("anchor_xy", None)
    return out


# ── bbox-frame normalization / migration (design_2026_09_05 v2, plan S8) ───

_TOL_MM = 1e-6


def cell_content_bbox(entry: dict) -> tuple[float, float, float, float] | None:
    """(min_along, max_along, min_across, max_across) over EVERY stored local
    point of the cell dict (component centres + their vias, cell vias, track
    endpoints, nested clone_placement xy) — None when the cell has no geometry.
    Pure; the content extents `normalize_cell_anchor_frame` re-roots against."""
    pts: list[tuple[float, float]] = []

    def _add(along: float, across: float) -> None:
        pts.append((float(along), float(across)))

    for c in entry.get("components", []):
        _add(c.get("offset_along_mm", 0.0), c.get("offset_across_mm", 0.0))
        for v in c.get("vias", []):
            _add(v.get("offset_along_mm", 0.0), v.get("offset_across_mm", 0.0))
    for v in entry.get("vias", []):
        _add(v.get("offset_along_mm", 0.0), v.get("offset_across_mm", 0.0))
    for t in entry.get("tracks", []):
        _add(t.get("start_along_mm", 0.0), t.get("start_across_mm", 0.0))
        _add(t.get("end_along_mm", 0.0), t.get("end_across_mm", 0.0))
    for cp in entry.get("clone_placements", []):
        if cp.get("xy") is not None:
            _add(cp["xy"][0], cp["xy"][1])
    if not pts:
        return None
    along = [p[0] for p in pts]
    across = [p[1] for p in pts]
    return (min(along), max(along), min(across), max(across))


def normalize_cell_anchor_frame(entry: dict) -> tuple[dict, bool]:
    """One-time migration to the bbox-anchored frame (design_2026_09_05 v2 /
    plan S8): returns (new_entry, changed) where new_entry's stored offsets
    live in the canonical frame (content in along>=0 / across<=0 measured from
    the board-lower-left corner) and the anchor fields truthfully describe the
    mount. NEVER mutates the input; boards do not move (geometry subtracts the
    anchor A, so the mount stays on the placement origin).

      - already carries anchor fields (a v2-written cell) -> untouched;
      - canonical, single component at stored (0,0) (the legacy "zero slot")
        -> that role is recorded as anchor_role (identity) so Trees /
        auto-anchor / live reads keep working through anchor_role;
      - non-canonical (the mount is NOT the bbox corner) -> every stored offset
        is re-rooted to the corner (min_along, max_across) and anchor_xy
        (= the old mount in the new frame) is recorded, plus anchor_role when a
        component sat on the old mount.
    """
    if entry.get("anchor_xy") or entry.get("anchor_role") or entry.get("anchor_pad"):
        return dict(entry), False
    bbox = cell_content_bbox(entry)
    if bbox is None:
        return dict(entry), False
    min_along, _max_along, _min_across, max_across = bbox
    components = entry.get("components", [])

    def _at_zero(rec: dict) -> bool:
        return (abs(float(rec.get("offset_along_mm", 0.0))) <= _TOL_MM
                and abs(float(rec.get("offset_across_mm", 0.0))) <= _TOL_MM)

    if min_along >= -_TOL_MM and max_across <= _TOL_MM:
        # Canonical already: (0,0) is the bbox corner. Annotate the legacy
        # zero-slot identity (exactly one component on (0,0)).
        zero = [c for c in components if _at_zero(c)]
        if len(zero) == 1:
            out = dict(entry)
            out["anchor_role"] = zero[0]["role"]
            return out, True
        return dict(entry), False

    # Non-canonical: re-root every stored offset to the corner C =
    # (min_along, max_across) — content becomes along>=0/across<=0 — and
    # record the old mount (stored (0,0)) as anchor_xy in the new frame
    # (= -C; the old mount lands at that offset after the shift).
    out = deepcopy(entry)
    dx, dy = min_along, max_across

    def _shift(record: dict) -> None:
        record["offset_along_mm"] = round(
            float(record.get("offset_along_mm", 0.0)) - dx, 9)
        record["offset_across_mm"] = round(
            float(record.get("offset_across_mm", 0.0)) - dy, 9)

    for c in out.get("components", []):
        _shift(c)
        for v in c.get("vias", []):
            _shift(v)
    for v in out.get("vias", []):
        _shift(v)
    for t in out.get("tracks", []):
        t["start_along_mm"] = round(float(t.get("start_along_mm", 0.0)) - dx, 9)
        t["start_across_mm"] = round(float(t.get("start_across_mm", 0.0)) - dy, 9)
        t["end_along_mm"] = round(float(t.get("end_along_mm", 0.0)) - dx, 9)
        t["end_across_mm"] = round(float(t.get("end_across_mm", 0.0)) - dy, 9)
    for cp in out.get("clone_placements", []):
        if cp.get("xy") is not None:
            cp["xy"] = [round(float(cp["xy"][0]) - dx, 9),
                        round(float(cp["xy"][1]) - dy, 9)]
    anchor_xy = [round(-dx, 9), round(-dy, 9)]
    out["anchor_xy"] = anchor_xy
    mount_comp = next((c for c in components if _at_zero(c)), None)
    if mount_comp is not None:
        out["anchor_role"] = mount_comp["role"]
    return out, True

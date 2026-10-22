# kicadstamp/cell_origin.py
"""The cell's ORIGIN, in the two places it decides anything: which matched role
the refresh measures its translation from, and whether the plan leaves the cell
measurable at all.

WHY this module exists (2026-10-21, plan_2026_10_21_cell_refresh_origin_role_renamed).
On 3CH-AWG-TIA-v103 the board's role `OSC` was renamed to `XTAL`; refreshing the
cell `xtal_32khz` from the WHOLE cluster refused with "role 'OSC' (the cell's
zero-offset origin) is not in the current selection". The rule Denis stated: a
whole cluster in the selection means the cell is simply updated — whether the
old origin role still exists on the board is irrelevant. The missing role was
never a measurement problem: the translation can be taken from ANY matched role,
because `CellFrame.from_reference` derives the placement origin from that role
and the fitted rotation (`cell_frame.py`), and the cell's own mount is read from
the CONFIG.

So the rule "which role anchors the translation" has ONE host (here), and the
giant (`cell_geometry_refresh.py`) plus the nested-cell reader both ask it —
before this module the preference was written twice.

The second rule is the write PRECONDITION: a legacy cell (no `anchor_xy`) is
read back by `cell_zero_slot_role`, so the plan must leave EXACTLY one component
at the cell's local (0, 0). The check uses the very same predicate the read uses
(`zero_slots`) and the values the plan already rounds to four decimals — a
tolerance of its own would allow a write whose next read refuses. When no
component ends at (0, 0) (the old origin's part has MOVED), the honest answer is
a refusal in words: silently re-rooting the cell would shift every instance of
it on every board.

Cells of the new shape (`anchor_role`/`anchor_xy`, v2) are exempt: their origin
is the stored `anchor_xy`, the refresh never touches it, and `cell_zero_slot_role`
is not what reads them.

The THIRD rule (2026-10-22, Д-2 of the same plan): a v2 cell's `anchor_role` may
outlive its part. Rename the mount role on the board, refresh once — the plan
legitimately deletes the old role from the cell, the cell editor keeps the LOADED
`anchor_role`, and the next refresh of the same selection refuses "role 'M' is not
a component of this cell". That is the very write the legacy precondition exists
to prevent, on the path it exempts. So the plan must say WHICH role takes the
anchor over: the one standing EXACTLY on the saved mount point (`mount_slots` —
the same exactness as `zero_slots`, because the next read compares with 0.0), and
only if that part really carries the cell's `anchor_pad` when it has one (standing
on the same POINT is not the same FOOTPRINT). Zero candidates, several, or a
missing pad — a refusal in words, never a guess.
"""
from __future__ import annotations

from .exceptions import ValidationError, format_read_fatal
from .i18n import _

__all__ = ["cell_slot_dicts", "cell_zero_slot_role", "mount_of", "mount_slots",
           "plan_components", "refresh_origin", "require_measurable_origin",
           "require_one_zero_origin", "zero_slots"]


def zero_slots(components: list[dict]) -> list[dict]:
    """Every slot sitting on the cell's local (0, 0) — THE predicate both the
    read (`cell_zero_slot_role`) and the write precondition
    (`require_one_zero_origin`) use, so "written" and "read back" cannot
    disagree. Missing offset keys default to 0.0, exactly as a freshly
    extracted slot that never wrote them."""
    return [c for c in components
            if c.get("offset_along_mm", 0.0) == 0.0
            and c.get("offset_across_mm", 0.0) == 0.0]


def cell_zero_slot_role(components: list[dict]) -> str:
    """The role of the cell's own (local 0,0) component — the "zero-offset
    slot" convention (design §3), the same one entity_placement's
    _entity_own_zero_slot_live_position uses for the auto tree anchor. The
    component whose offset_along_mm/offset_across_mm are both 0.0. Fatal when
    there is no such component or more than one — the refresh cannot pick its
    origin by guessing.

    Moved here from cell_geometry_refresh (2026-10-21) and re-exported there:
    the predicate belongs with the origin rules, and the giant must not grow."""
    zero = zero_slots(components)
    if not zero:
        raise ValidationError(format_read_fatal(
            _("no zero-offset component to use as the refresh origin"),
            [_("cell has no component with offset_along_mm/offset_across_mm both "
               "equal to 0 (its local (0,0)) to act as the origin of the refresh — "
               "select a cell whose origin component is at (0,0)")]
        ))
    if len(zero) > 1:
        raise ValidationError(format_read_fatal(
            _("{n} zero-offset components — the refresh origin is ambiguous")
            .format(n=len(zero)),
            [_("exactly one component must sit at the cell's local (0,0); found "
               "{n} — fix the cell so a single component has offset 0/0")
             .format(n=len(zero))]
        ))
    return zero[0]["role"]


def refresh_origin(components: list[dict], role_to_ref: dict[str, str],
                   origin_role: str | None = None,
                   require_canonical: bool = True,
                   ) -> tuple[str, float, float] | None:
    """(role, stored_along_mm, stored_across_mm) of the role the refresh measures
    its TRANSLATION from, or None when no role of the cell is matched.

    The cell's OWN origin role wins when it is in the selection — legacy: the
    single zero-offset slot (`cell_zero_slot_role`, which still raises for a cell
    that has none or several); v2: the `anchor_role`. Then the refresh keeps
    today's frame byte for byte.

    When that role is NOT in the selection, ANY matched role serves: the cell's
    own mount is read from the CONFIG (`mount`), and `CellFrame.from_reference`
    derives the placement origin from the chosen role plus the fitted rotation,
    so on a rigid instance the choice cannot change the plan. The pick is the
    FIRST matched role in the CELL's order — deterministic, not a guess
    (2026-10-21: a renamed origin role must not stop the update).

    `require_canonical=False` — for a caller that only READS geometry (the
    nested-cell reader): a legacy cell with no single zero-offset slot is then
    answered by the first matched role instead of `cell_zero_slot_role`'s fatal.
    The refresh keeps the default True: it WRITES cells, so a broken origin there
    is exactly what must stop it."""
    own = (origin_role if origin_role is not None
           else cell_zero_slot_role(components) if require_canonical
           else None)
    for component in components:
        role = component.get("role")
        if role == own and role in role_to_ref:
            return (role,
                    float(component.get("offset_along_mm", 0.0)),
                    float(component.get("offset_across_mm", 0.0)))
    for component in components:
        role = component.get("role")
        if role in role_to_ref:
            return (role,
                    float(component.get("offset_along_mm", 0.0)),
                    float(component.get("offset_across_mm", 0.0)))
    return None


def plan_components(current: list[dict],
                    updates: list[tuple[dict, dict]],
                    new_records: list[dict],
                    removed: list[dict]) -> list[dict]:
    """The cell's component list AS THE PLAN LEAVES IT — the cell's order kept,
    the recomputed geometry applied, the removed roles dropped, the added roles
    appended.

    ONE builder, because two consumers need exactly this list and must not drift:
    the write precondition (`require_one_zero_origin`) and the copper classifier
    that re-creates a departed role's copper (`_classify_import_net`: the NEW
    role's slot has to be visible, or the geometric tiebreak cannot pick it)."""
    by_id = {id(record): geo for record, geo in updates}
    out: list[dict] = []
    for component in current:
        if any(component is gone for gone in removed):
            continue
        geo = by_id.get(id(component))
        out.append(dict(component, **geo) if geo is not None else component)
    out.extend(new_records)
    return out


def cell_slot_dicts(cell) -> list[dict]:
    """A loaded ``Cell``'s slots in the list-of-dicts shape the frame and the cell
    readers take (role + the two STORED offsets + the slot's own ``angle_deg``) —
    the INPUT twin of `plan_components`, and the shape `mount_of`/`zero_slots`
    read. The frame needs the angle to recover a SINGLE-slot instance's rotation
    (2026-10-13, plan_2026_10_13_single_slot_rotation: cell_frame.
    single_slot_rotation), and a caller that carries no angle gets 0.0, exactly as
    before.

    Moved here from cell_geometry_refresh (2026-10-22, Д-2): the giant only
    shrinks, and the dict-shape face of a cell now lives in one module."""
    return [{"role": getattr(slot, "role", None),
             "offset_along_mm": float(getattr(slot, "offset_along_mm", 0.0)),
             "offset_across_mm": float(getattr(slot, "offset_across_mm", 0.0)),
             "angle_deg": float(getattr(slot, "angle_deg", 0.0))}
            for slot in (getattr(cell, "components", None) or ())]


def mount_of(components: list[dict],
             origin_role: str | None = None) -> tuple[tuple[float, float],
                                                      str | None]:
    """((along_mm, across_mm), problem_or_None) — the cell's MOUNT A in its own
    stored frame, for the paths that work with the cell's component DICTS (the
    loaded-Cell twin is `geometry.cell_anchor.cell_mount_offset`).

    Read from the CONFIG, never from the selection: a v2 cell stores A as its
    `anchor_role`'s offset, a legacy cell's mount IS its stored (0, 0). The one
    problem it can report is the anchor role naming no component of the cell —
    a record written by an older version, or a role renamed on the board. The
    caller merges it with its other problems; the mount answer is still (0, 0),
    exactly as before (nothing may be measured from a role that is not there)."""
    if origin_role is None:
        return (0.0, 0.0), None
    comp = next((c for c in components if c.get("role") == origin_role), None)
    if comp is None:
        return (0.0, 0.0), _("role {role!r} (the cell's anchor origin) is not a "
                             "component of this cell").format(role=origin_role)
    return ((float(comp.get("offset_along_mm", 0.0)),
             float(comp.get("offset_across_mm", 0.0))), None)


def mount_slots(components: list[dict], mount: tuple[float, float]) -> list[dict]:
    """Every slot sitting EXACTLY on the cell's mount point — the exactness twin of
    `zero_slots`, and exact for the same reason: the refresh rounds every offset to
    four decimals, and the next read compares with 0.0. Used by the v2 anchor
    transfer (`require_measurable_origin`), where the mount point is the only place
    the new anchor role can come from."""
    along, across = mount
    return [c for c in components
            if c.get("offset_along_mm", 0.0) - along == 0.0
            and c.get("offset_across_mm", 0.0) - across == 0.0]


def _the_pad_is_there(adapter, footprints, role_to_ref, role: str,
                      pad: str) -> bool:
    """Does the live footprint of `role` carry `pad`? — asked of the live board,
    because a role standing on the same POINT may be a replacement part with
    another footprint (Denis, 2026-10-22)."""
    ref = role_to_ref.get(role)
    fp = next((candidate for candidate in footprints if candidate.ref == ref), None)
    return fp is not None and adapter.get_pad_by_number(fp, pad) is not None


def require_measurable_origin(planned: list[dict], current: list[dict],
                              origin_role: str | None = None,
                              anchor_pad: str | None = None,
                              adapter=None, role_to_ref: dict | None = None,
                              footprints: list | None = None,
                              ) -> tuple[str, str] | None:
    """The role that takes the cell's origin over after this plan — `(role,
    log_line)` for a TRANSFER, None when nothing moves — or a READ-ONLY refusal.

    Legacy cell (`origin_role is None`): the (0, 0) precondition, unchanged
    (`require_one_zero_origin`). v2 cell: nothing to do while the anchor role is
    still a component of the PLAN; when the plan removed it, exactly ONE component
    must stand on the saved mount point (`mount_slots`, read off `current` — the
    mount is a CONFIG fact and the anchor role may be gone from `planned`), and
    that role takes the anchor over. `anchor_pad`, when the cell has one, must
    still exist on the new role's footprint: "stands on the same point" is not
    "the same part" (`OSC` -> `XTAL` may have been a package swap, Denis).

    Zero candidates, several, or a missing pad — a refusal in words with the
    read-only verdict, no write: a silently re-rooted v2 cell would be
    unmeasurable on its very next read."""
    if origin_role is None:
        require_one_zero_origin(planned)
        return None
    if any(c.get("role") == origin_role for c in planned):
        return None
    mount, _problem = mount_of(current, origin_role)
    slots = mount_slots(planned, mount)
    roles = ", ".join(repr(c.get("role")) for c in slots)
    if len(slots) > 1:
        problem = _("{n} components would stand on the cell's saved mount point "
                    "after this update ({roles}) — the anchor role {old!r} left "
                    "with its part and the new anchor is ambiguous; nothing was "
                    "written — set the cell's anchor in Cell anchor"
                    ).format(n=len(slots), roles=roles, old=origin_role)
    elif not slots:
        problem = _("no component would stand on the cell's saved mount point "
                    "after this update — the anchor role {old!r} is not in the "
                    "plan, so the cell would not be measurable; nothing was "
                    "written — include that role in the selection, or set the "
                    "cell's anchor in Cell anchor").format(old=origin_role)
    else:
        new_role = slots[0]["role"]
        if anchor_pad is None or _the_pad_is_there(adapter, footprints or [],
                                                   role_to_ref or {}, new_role,
                                                   anchor_pad):
            return new_role, _(
                "the cell's anchor role {old!r} is gone — the anchor passes to "
                "{new!r}, which stands exactly on the saved mount point; the cell "
                "keeps its origin").format(old=origin_role, new=new_role)
        problem = _("the anchor role {old!r} is gone and {new!r} stands on its "
                    "mount point, but that part has no pad {pad!r} — it is a "
                    "replacement, not a rename; nothing was written — set the "
                    "cell's anchor in Cell anchor").format(
                        old=origin_role, new=new_role, pad=anchor_pad)
    raise ValidationError(format_read_fatal(
        _("cannot refresh cell geometry from the current selection"), [problem]))


def require_one_zero_origin(components: list[dict]) -> str:
    """The role that will sit on the cell's local (0, 0) after the write — or a
    READ-ONLY refusal in words.

    The read path (`cell_zero_slot_role`) compares with 0.0 exactly, so this
    check uses the same predicate (`zero_slots`) on the values the plan already
    rounded: any tolerance here would let a write through whose very next read
    refuses. No re-rooting on purpose: moving the origin would move EVERY
    instance of the cell on the board (2026-10-21, Denis)."""
    zero = zero_slots(components)
    if len(zero) == 1:
        return zero[0]["role"]
    if not zero:
        problem = _("no component would stand at the cell's local (0, 0) after "
                    "this update — the origin went away with the removed role; "
                    "nothing was written, and the cell needs a new origin "
                    "(extract the cluster again)")
    else:
        problem = _("{n} components would stand at the cell's local (0, 0) after "
                    "this update — the origin would be ambiguous; nothing was "
                    "written, and the cell needs a single origin (extract the "
                    "cluster again)").format(n=len(zero))
    raise ValidationError(format_read_fatal(
        _("cannot refresh cell geometry from the current selection"), [problem]))

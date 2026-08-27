# kicadstamp/config/models.py
"""
config/models.py — all configuration dataclasses (cells, ClonePlacement,
Rule, Config, etc.) WITHOUT any YAML loading/validation logic — this is purely
a description of the data shape. Loading is in config/loader.py.

Split from monolithic config.py by refactoring. The public interface of the
package remains unchanged — kicadstamp/config/__init__.py re‑exports everything
from here and from loader.py, so `from kicadstamp.config import Config, ClonePlacement, load_config`
continues to work exactly as before.
"""
from dataclasses import dataclass, field
from typing import Any

from ..trees import Tree
from .points import Point


@dataclass
class ThermalViaArrayConfig:
    """Configuration for one thermal via array under an IC thermal pad.
    Config.thermal_via_arrays: list[ThermalViaArrayConfig] — any number of
    these, each independently named/anchored/retired/skipped, same shape as
    rules/clone_placements (2026-08-02: generalized from a single field once
    a second real IC needing thermal vias — AD9707 — showed up; see
    handoff_2026_08_02_thermal_via_arrays_list.md).

    name — for --only (see kicadstamp_cli.py), and to disambiguate one array
    from another. REQUIRED in YAML for every entry in thermal_via_arrays:
    (see config/loader.py — fatal, not a silent fallback), and must be unique
    across the whole list (also fatal at load, same reasoning as rules'
    --only collision check). Here in the dataclass it's Optional only because
    tests/internal code that construct ThermalViaArrayConfig() directly in
    Python (bypassing the YAML loader) don't need a name — the requirement
    applies ONLY to human input via YAML, not to the data structure itself.

    skip — orthogonal to retired (default False). retired: true means "does
    not exist on the board" (registry pruned, see drop_disabled_rules/
    known_anchor_ids in kicadstamp/apply_pipeline.py). skip: true means "skip
    this run only" — same effect as being excluded by --only/--cluster, but
    written inline instead of on the command line: existing via/tracks stay
    protected in the registry (still counted in known_anchor_ids), just not
    (re)planned this run. See drop_inactive_items in kicadstamp/apply_pipeline.py.
    """
    retired: bool = False
    anchor_ref: str | None = None
    anchor_role: str | None = None
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    # Alternative to anchor_ref/anchor_role — name of a points: entry (see
    # config/points.py). Mutually exclusive with anchor_ref/anchor_role
    # (fatal if combined — see config/loader.py). MUST resolve to a
    # footprint (the referenced Point may not have a shift or be xy-literal
    # — thermal_via_array needs a live component to look up `pad` from, a
    # bare coordinate is not enough; checked at load time, see loader.py).
    anchor_point: str | None = None
    pad: str = ""
    net: str = "GND"
    rows: int = 4
    cols: int = 4
    margin_mm: float = 0.5
    pattern: str = "grid"
    drill_mm: float = 0.3
    diameter_mm: float = 0.5
    name: str | None = None
    skip: bool = False


def thermal_via_array_effective_name(tva: "ThermalViaArrayConfig") -> str | None:
    """Single point for reading the name for --only. Just tva.name — the loader
    guarantees it is set for any thermal_via_array that actually came from YAML;
    None only for manually constructed in tests."""
    return tva.name


@dataclass
class CoordinatePlacement:
    """The "dumb placer" (Denis, 2026-08-12): moves an EXISTING footprint —
    matched by Cluster+Role, Role already unique within one Cluster instance
    by the project's own established Role/Cluster convention (see
    docs/architecture on Role vs Cluster) — to an explicit board position and
    rotation. No template, no offsets, no via/track creation: unlike
    ClonePlacement/Rule, this never touches registry.py (which only tracks
    via/track UUIDs between runs) — a move is idempotent by construction,
    the same way Rule/ClonePlacement's own component moves already are
    (apply_pipeline.py's Phase 1 move loop has no registry reconciliation
    either, it just re-applies the target position every run).

    sheet — OPTIONAL, narrows Cluster+Role to one physical instance when the
    same sheet is cloned/reused (e.g. one PI-filter section instantiated per
    channel) and Cluster alone is identical across copies. Distinct from
    `anchor_sheet` (narrows the OTHER, anchor component in anchor-relative
    mode) — the same (Sheet, Cluster, Role) addressing convention as the rest
    of the project, this time completing it for CoordinatePlacement's own
    identity.

    Position — EXACTLY ONE of THREE mutually exclusive modes (fatal at load
    if more than one applies or none is fully specified, see config/entries.py):
      - Cartesian (absolute): x_mm/y_mm — absolute board position for the
        anchor point (see `anchor` below). rotation_deg is then REQUIRED
        (no implicit angle to fall back on).
      - Polar (absolute, around a fixed centre): center_x_mm/center_y_mm/
        radius_mm/angle_deg — position is center + radius at angle_deg
        (board coordinates, KiCad's native Y-down convention, same as
        everywhere else in this codebase). angle_deg ALSO becomes
        rotation_deg by default (spoke-style: the component points outward
        from the centre) — set rotation_deg explicitly too to override just
        the component's own orientation without changing where the angle
        places it.
      - Anchor-relative (2026-08-12, Group 0 consolidation — the mode that
        used to live in ClonePlacement's role:/cluster: variant, migrated
        1:1 here): one of anchor_ref/anchor_role(+anchor_sheet/anchor_cluster)
        or anchor_point identifies a DIFFERENT, stationary component/point,
        and x_mm/y_mm (Cartesian offset) OR radius_mm/angle_deg (polar
        offset) become the OFFSET from that anchor (or from its anchor_pad)
        instead of an absolute position — mirroring ClonePlacement's own
        "absolute without anchor, flat offset with anchor" xy duality.
        rotation_deg defaults to angle_deg in polar-offset mode (spoke-style,
        same as the fixed-centre polar) and to 0.0 in Cartesian-offset mode.
        This is the answer to Denis's original question — placing a resistor
        "relative to a specific pad of the FPGA".

    anchor / anchor_pad — 'center' (default) or 'pad': in the ABSOLUTE modes,
    whether the resolved target point lands on the footprint's own origin, or
    on ONE SPECIFIC PAD of the SAME footprint being moved (anchor_pad required
    iff anchor == 'pad', fatal if anchor_pad is set without anchor == 'pad').
    Self-referential — see coordinate_position_calculator.py's
    resolve_self_pad_anchor() for the geometry. In the ANCHOR-RELATIVE mode
    this self-referential concept has no meaning (the target is literally
    "anchor + offset", nothing to land on) — `anchor: pad` is fatal there —
    and anchor_pad instead takes the ClonePlacement/Rule meaning: the pad OF
    THE ANCHOR component the offset is measured from (same field name, same
    semantics as Rule/ClonePlacement, per the Group 0 plan; the two meanings
    never coexist because the modes are mutually exclusive).

    retired/skip — same convention as Rule/ClonePlacement/
    ThermalViaArrayConfig (retired: true = "does not exist this run",
    always wins over --only/--cluster; skip: true = "skip just this run").
    Neither has any registry-pruning effect here (there is no registry
    involvement at all for this type) — purely a run/don't-run switch.
    """
    cluster: str
    role: str
    sheet: str | None = None
    name: str | None = None
    x_mm: float | None = None
    y_mm: float | None = None
    center_x_mm: float | None = None
    center_y_mm: float | None = None
    radius_mm: float | None = None
    angle_deg: float | None = None
    rotation_deg: float | None = None
    anchor: str = 'center'
    anchor_pad: str | None = None
    # OTHER-component anchor (anchor-relative mode): anchor_ref/anchor_role
    # (+ anchor_sheet/anchor_cluster narrowing) or anchor_point. Mutually
    # exclusive with the fixed-centre polar fields (center_x_mm/center_y_mm)
    # and with the self-referential anchor: pad — see the docstring above.
    anchor_ref: str | None = None
    anchor_role: str | None = None
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    anchor_point: str | None = None
    retired: bool = False
    skip: bool = False


def coordinate_placement_effective_name(cp: "CoordinatePlacement") -> str:
    """Single point for reading the name for --only/--cluster and for
    duplicate-name detection at load. Unlike Rule/ThermalViaArrayConfig
    (name required in YAML, fatal if missing), a CoordinatePlacement's name
    is OPTIONAL and defaults to f"{cluster}/{role}" — Role is already
    unique within a Cluster by convention, so that pair is already a good,
    low-typing-cost identity for a "dumb", high-volume, table-entered list
    (Denis, 2026-08-12: minimizing typing for bulk table entry was an
    explicit goal)."""
    return cp.name or f"{cp.cluster}/{cp.role}"


@dataclass
class TemplateVia:
    """
    Via slot in a cell — coordinates are ALWAYS along/across from the SPOKE
    origin (not from the component pad, even if the slot belongs to a specific
    component role) — same formula (local_to_absolute) as for the component
    position. net=None means "use the rule net" (rule.net).

    CHANGED (KiCadStamp): previously power_via was the only field at the spoke
    level, while the GND via of a component was computed from the REAL pad of
    the already‑placed component (required reading the live board after commit).
    Now both concepts are the same slot — pure cell geometry, independent
    of the live board. There can be any number of lists at both levels
    (spoke.vias and component.vias).

    net_from_role — OPTIONAL live net resolution instead of a static net:
    the role whose real pad net this via should take, resolved at apply time
    (see net_from_role_resolver.py and
    net_resolution.resolve_net_from_role). Mutually exclusive with net — both
    set at once is a load-time fatal; "both None" keeps the existing rule-net
    convention (spoke_layout) / fatal-on-apply (clone_geometry). Unlike net,
    this is only meaningful for ClonePlacement (apply has a live role_to_ref;
    ManualSpoke ignores it — same as net_template on TemplateComponentSlot).
    net_from_role_pad — OPTIONAL pad number for a multi-net role (LDO VIN/VOUT):
    the net of THIS pad is taken. Without it the role must carry exactly one
    non-rule net (lemma 2). Validation of the pad number is live/apply-time
    only, not at load.
    """
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    net: str | None = None
    net_from_role: str | None = None
    net_from_role_pad: str | None = None
    drill_mm: float = 0.3
    diameter_mm: float = 0.6


@dataclass
class TemplateComponentSlot:
    """
    One component slot in a cell — a role ('HEAVY'/'LIGHT'/'XTAL'/
    'LOAD_CAP_1', etc.), not a specific ref. Roles MUST be unique within a
    single cell (checked fatally during loading, see _load_cell).
    The actual ref is selected during placement from the component pool:
    all footprints whose REAL pad sits on the rule net (rule.net) and whose
    custom Role field matches this role (see placement/services/component_pool.py).
    Coordinates are local (along/across) — from the SPOKE origin, not from the
    component itself. Vias in this slot use the same local system (see TemplateVia).

    net_template — OPTIONAL, for TemplatePlacer (role matching by nets, not by
    selection): the expected net of this component, with the same placeholder
    syntax as TemplateVia.net (see net_resolution.py). Not used at all for
    ManualSpoke/component_pool.py — there the role is looked up by (rule.net, Role)
    without any field here.

    net_template_pad — OPTIONAL, only meaningful together with net_template:
    which pad of the resolved candidate carries the role's net, for roles
    whose real component has MORE than one non-rule net (a multi-pin part —
    regulator/diode/inductor/etc). Without it, the "by nets" resolver
    (resolve_roles_by_nets) is unaffected (it already searches by an
    ALREADY-KNOWN expected net, pad count doesn't matter there) — this field
    only helps suggest_role_nets_from_cluster (GUI auto-fill BEFORE nets:/
    params: exist) pick the right pad deterministically instead of requiring
    "exactly one non-rule net total" on the candidate.

    net_template_same_as_role — OPTIONAL, ALTERNATIVE to net_template_pad, same
    purpose (disambiguate a multi-net candidate for suggest_role_nets_from_cluster),
    different mechanism: names ANOTHER role in this SAME cell whose own resolved
    net (safely, via lemma 2 — that role must have exactly one non-rule net) is
    electrically IDENTICAL to this role's identifying net. Prefer this over
    net_template_pad whenever possible — a PAD NUMBER is only a reliable
    cross-instance identifier for components with a fixed physical pinout (ICs,
    diodes, polarized caps); for electrically symmetric 2-pin parts (plain R/C)
    which pad ends up "1" vs "2" is an arbitrary ROUTING choice, independently
    made per instance — found live 2026-08-16 (R_FB_TOP's identifying net sat on
    pad 2 in one routed instance, pad 1 in another, despite IDENTICAL component
    position/orientation — verified geometrically, not a rotation/mirror
    artifact). Electrical topology (which nets are the same node) is exactly what
    IS guaranteed to survive between clone instances; pad numbering is not.
    Mutually exclusive with net_template_pad (fatal to set both — pick one
    mechanism per role). Like net_template_pad, only meaningful together with
    net_template, and does NOT affect resolve_roles_by_nets (apply-time by-nets
    resolution already works purely by net VALUE, immune to this whole class of
    problem — see plan_2026_08_16_net_template_pad.md's own scope note).
    """
    role: str
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    angle_deg: float = 0.0
    vias: list[TemplateVia] = field(default_factory=list)
    net_template: str | None = None
    net_template_pad: str | None = None
    net_template_same_as_role: str | None = None
    # Layer of the slot — FACT, absolute: 'F.Cu' | 'B.Cu'. None = inherit from
    # cell layer. Written by extract only for components that deviate from
    # the cell layer.
    layer: str | None = None


@dataclass
class TemplateTrack:
    """
    Straight copper track segment in the cell — same local coordinate
    system (along/across from spoke origin) as TemplateVia. No association with
    roles/pads: like a via, a track has no user fields, so we trust geometry
    (all cell elements are moved/rotated/mirrored by the same formula,
    see geometry/clone_geometry.py).

    A polyline is simply MULTIPLE TemplateTrack segments in sequence, joined
    end‑to‑end (exactly as kipy.board_types.Track stores them inside KiCad —
    there is no separate "polyline" entity). ArcTrack is deliberately not
    supported — not needed for PI‑filters; could be added later if needed.

    Collisions (whether the track crosses other copper/components in the new
    location) are NOT checked by this tool — deliberate decision (see chat
    discussion): we rely on KiCad DRC after placement, not on our own segment‑
    vs‑segment geometry checker.

    net_from_role / net_from_role_pad — same as TemplateVia.net_from_role /
    net_from_role_pad: the track's net is taken live from the resolved role's
    real pad net instead of a static net (see TemplateVia docstring for the
    mutual-exclusion and live-apply rules).
    """
    start_along_mm: float = 0.0
    start_across_mm: float = 0.0
    end_along_mm: float = 0.0
    end_across_mm: float = 0.0
    width_mm: float = 0.25
    net: str | None = None
    net_from_role: str | None = None
    net_from_role_pad: str | None = None
    # Layer — same pattern as TemplateComponentSlot.layer: None = inherit from
    # cell layer, when mirroring it is inverted by the same rule.
    layer: str | None = None


@dataclass
class CellPlacement:
    """
    A nested reference to another cell (or a single role), INSIDE a
    composite Cell definition — recursion, see Cell.clone_placements below.

    Closed boundary (see handoff_2026_07_31_blocks.md §3): nothing inside a
    cell may reference anything outside it. Deliberately has NO anchor_ref/
    anchor_role/anchor_sheet/anchor_cluster/anchor_pad/by_selection/
    ignore_selection at all — those only make sense for a top-level,
    board-attached ClonePlacement. xy/rotation_deg are ALWAYS relative to
    the parent cell's own local (0,0), never to anything live on the board.

    sheet/cluster (2026-08-26) — the placement's OWN identity for INTERNAL
    role narrowing (role_narrowing.py), NOT an external anchor: anchor_sheet/
    anchor_cluster remain deliberately absent (see the closed-boundary note
    above — a nested CellPlacement has no external anchor by design, its
    position comes from the parent). Same semantics as ClonePlacement.sheet/
    .cluster below — a reused hierarchical sheet clones IDENTICAL Cluster/Role
    fields onto every instance, so only these two can tell identical physical
    copies apart when narrowing a shared-net role (e.g. +3V3 on a PI-filter).

    sheet — when None, inherits the resolved sheet of the PARENT placement
    (the enclosing ClonePlacement or CellPlacement one level up), chained
    through arbitrarily deep nesting — see clone_position_calculator.py::
    _resolve_one_level. Set explicitly only when this specific nested placement
    genuinely lives on a different sheet than its parent (rare); the common
    case (a reusable composite cell cloned once per channel/section) should
    leave it unset and rely on inheritance.

    No param scoping — params/nets/net_overrides/refs here are the ONLY
    ones this nested placement sees, never inherited from the parent cell's
    own placement (same convention ClonePlacement.params already has today,
    deliberately kept, not a new rule for nesting specifically).

    cell OR role (mutually exclusive, exactly one required) — same meaning
    as ClonePlacement.cell/role.
    """
    name: str
    cell: str | None = None
    role: str | None = None
    xy: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = 0.0
    mirror: bool = False
    layer: str | None = None
    # Own identity for internal role narrowing (role_narrowing.py), NOT an
    # external anchor — anchor_sheet/anchor_cluster remain deliberately absent,
    # see the closed-boundary note above. When None, inherits the resolved
    # sheet of the parent placement (chained) — see the docstring above.
    sheet: str | None = None
    cluster: str | None = None
    nets: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    net_overrides: dict[str, str] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)


@dataclass
class Cell:
    """
    Cell — all geometry is local and rotation‑invariant:
    described once at rotation_deg=0 (the reference board orientation), then
    each actual spoke rotates it as a whole. Any list may be empty — e.g. a
    spoke with no vias, or a cell with just one component.

    Recursive (2026-07-31): a cell may ALSO carry clone_placements — nested
    references to other cells (or single roles), positioned relative to
    THIS cell's own local (0,0) — see CellPlacement above. A cell can be a
    pure leaf (only vias/components/tracks), a pure composite (only
    clone_placements), or both at once — nothing about the two is mutually
    exclusive.

    anchor_xy/anchor_role(+anchor_pad) — added 2026-08-06 for the cell
    editor (Denis: "cell редактор уже необходимость"), DISPLAY-ONLY
    metadata, mutually exclusive, both optional: marks which point of the
    cell's own local (0,0) already IS by construction (offset_along_mm=
    offset_across_mm=0.0 is always the origin — these fields do not change
    that, and are never read by clone_position_calculator.py/any resolver).
    Exists purely so the cell editor can show a crosshair/"distance from
    anchor" while hand-authoring offsets, instead of them being an untracked
    fact only the original extractor run knew. anchor_role must name one of
    this cell's own components (role); anchor_pad narrows to a specific pad
    of it and is only meaningful together with anchor_role.
    """
    name: str
    vias: list[TemplateVia] = field(default_factory=list)
    components: list[TemplateComponentSlot] = field(default_factory=list)
    tracks: list[TemplateTrack] = field(default_factory=list)
    clone_placements: list[CellPlacement] = field(default_factory=list)
    # Cell layer — FACT, absolute: 'F.Cu' | 'B.Cu', as extracted
    # (written automatically). Components without their own layer inherit it.
    # No automatic guesswork: the cell is placed verbatim; to flip the whole
    # thing, use explicit mirror on the placement.
    layer: str = 'F.Cu'
    anchor_xy: tuple[float, float] | None = None
    anchor_role: str | None = None
    anchor_pad: str | None = None


@dataclass
class ManualSpoke:
    """
    A specific spoke on a specific FPGA pad. Position — EXACTLY ONE of two
    mutually exclusive modes, with the same field names as CoordinatePlacement
    for consistency across the project. Fatal at load if BOTH are set, or if
    the polar pair is only half-filled (one of radius_mm/angle_deg without the
    other — see config/entries.py); the absence of both is simply the default
    Cartesian zero shift:

      - Cartesian (default): shift_x_mm/shift_y_mm — a plain translation from
        the pad centre to the spoke origin, WITHOUT rotation (raw shift, no
        parent_rotation — spokes have no parent frame).
      - Polar: radius_mm/angle_deg — the spoke origin is the pad centre plus
        "radius_mm along the X axis, rotated by angle_deg", i.e.
        origin = local_to_absolute(pad_position, radius_mm, 0.0, angle_deg),
        the exact same primitive/convention as CoordinatePlacement's polar
        mode and every cell's own along/across offsets — so angle_deg's
        rotation direction is guaranteed consistent with rotation_deg's
        meaning everywhere else.

    rotation_deg (both modes) is ALWAYS in KiCad board coordinates (not local),
    tuned visually for the specific board. Order: first shift (shift_x/shift_y
    or radius/angle) from the pad centre to the spoke origin, then rotation of
    the resulting origin (and all cell contents) by rotation_deg. Unlike
    CoordinatePlacement, polar angle_deg does NOT become rotation_deg — it only
    positions the origin; the component/cell orientation is a separate, already
    existing rotation_deg concern.

    IMPORTANT: no component refs here anymore — concrete components are
    automatically selected from the pool (see placement/services/component_pool.py)
    by matching the actual rule net (rule.net) and the custom Role field on the
    component, in the order of spokes in this list.

    skip — see ThermalViaArrayConfig.skip for the retired-vs-skip distinction;
    here it lets you narrow work down to a single spoke within a rule without
    touching retired (which would prune its registry entries).
    """
    pad: str
    cell: str
    shift_x_mm: float = 0.0
    shift_y_mm: float = 0.0
    rotation_deg: float = 0.0
    radius_mm: float | None = None
    angle_deg: float | None = None
    retired: bool = False
    cluster: str | None = None
    skip: bool = False


@dataclass
class Rule:
    """Rule: a group of spokes around ONE anchor component, all on one net.
    anchor_ref OR anchor_role (mutually exclusive, exactly one required) —
    whose pads are listed in spokes. anchor_sheet/anchor_cluster narrow
    ambiguity of anchor_role, same principle as in ClonePlacement.

    name — OPTIONAL, for --only. Defaults to net when not set (see
    rule_effective_name). An explicit name is only needed to give a rule a
    more readable label than its net; it is NOT a grouping mechanism — do not
    reuse the same name across several rules to "bundle" them for --only, use
    a shared Cluster (anchor_cluster / spoke.cluster) for that instead. The
    loader fatals if two rules resolve to the same effective name (see
    config/loader.py) — add a distinguishing name: to one of them.

    retired — whole‑rule switch (default False), same convention as
    ManualSpoke.retired/ClonePlacement.retired/ThermalViaArrayConfig.retired.
    Always wins over --only/--cluster: a retired rule is dropped before any
    CLI selection is applied, it cannot be resurrected by naming it explicitly
    on the command line — retired: true means "does not exist on the board
    right now", not "excluded from this particular run".

    skip — see ThermalViaArrayConfig.skip. Unlike retired, skip: true does
    NOT prune this rule's via/tracks from the registry, it only skips
    (re)planning them this run — the inline, per-item equivalent of --only/
    --cluster, for narrowing work without retyping CLI flags each time (see
    drop_inactive_items in kicadstamp/apply_pipeline.py, added 2026-07-29).

    sheet — OPTIONAL own-identity sheet (2026-08-21, anchor dependency tree
    plan §1.0): mirrors ClonePlacement.sheet / CoordinatePlacement.sheet —
    the same (Sheet, Cluster, Role) addressing convention completed for Rule.
    It narrows the roles THIS rule produces (its spokes' cell component roles)
    for the STATIC anchor graph / dependency tree grouping, and is DISTINCT
    from anchor_sheet (which narrows only the OTHER, anchor component).
    Deliberately NOT consumed by the live spoke resolver (ComponentPool has
    no sheet axis) — a rule's produced roles are matched on the board by
    net + role + cluster only, so this field is config bookkeeping + graph
    grouping, not live resolution.
    """
    net: str
    spokes: list[ManualSpoke]
    anchor_ref: str | None = None
    anchor_role: str | None = None
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    # Alternative to anchor_ref/anchor_role — see ThermalViaArrayConfig.anchor_point
    # for the mutual-exclusion/footprint-required rules, same here (Rule looks
    # up spoke.pad on the resolved component, a bare coordinate isn't enough).
    anchor_point: str | None = None
    sheet: str | None = None
    name: str | None = None
    retired: bool = False
    skip: bool = False


def rule_effective_name(rule: "Rule") -> str:
    """Single point for reading the identity used for --only: the explicit
    name if set, otherwise the net (net is guaranteed present on any Rule)."""
    return rule.name or rule.net


@dataclass
class NetTrace:
    """One net's copper (tracks + vias), captured as LOCAL offsets from an
    anchor pad, re-resolved LIVE on every apply/redraw — same anchor fields
    serve BOTH the extraction-time origin AND the apply-time anchor (this is
    deliberately NOT split into a Cell+ClonePlacement pair: net_traces are
    single-instance by design, no reuse-at-multiple-anchors need, so the
    usual two-layer indirection would be pure overhead — see
    techdocs/handoff/deepseek/plan_2026_08_21_net_traces.md §0 for the full
    reasoning).

    net — the network name. This is the SAVE/--only identity (net_trace_
    effective_name) AND, being unique per record, the registry's
    template_name (see net_trace_anchor_id in kicadstamp/net_trace_planner.py).
    One net_traces: record per net — fatal at load if two records share a net
    (see config/loader.py).

    anchor_role — REQUIRED. Resolves the anchor footprint over the WHOLE live
    board (never the mouse selection) at BOTH extract time (origin) and apply
    time (anchor) — the same resolve_footprint_by_role search Rule/
    ClonePlacement already use. anchor_sheet/anchor_cluster optionally narrow
    that role's ambiguity, anchor_pad optionally moves the anchor point from
    the footprint centre to a specific pad's centre (same semantics as
    ClonePlacement.anchor_pad).

    tracks/vias — the copper as LOCAL (along/across) offsets from the anchor
    point, reusing the exact TemplateTrack/TemplateVia shape Cell.tracks/
    Cell.vias use (same local_to_absolute formula at apply time, just always
    with rotation_deg=0 — a net trace is a translation-following bundle, not
    a rotatable cell). Track/via net is ALWAYS written explicitly (the whole
    record is about ONE net; there is no enclosing Rule to inherit a net
    from, unlike Cell contents).

    retired/skip — the same convention as every other section (Rule/
    ClonePlacement/ThermalViaArrayConfig): retired: true = "does not exist on
    the board right now" (registry protection dropped, see
    _compute_all_anchor_ids in apply_pipeline.py); skip: true = "skip just
    this run" (registry protection kept). Both simply mean "don't plan this
    record" — a net trace never moves components, only creates/adopts copper.
    """
    net: str
    anchor_role: str
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    anchor_pad: str | None = None
    tracks: list[TemplateTrack] = field(default_factory=list)
    vias: list[TemplateVia] = field(default_factory=list)
    retired: bool = False
    skip: bool = False


def net_trace_effective_name(nt: "NetTrace") -> str:
    """Single point for reading the --only/SAVE identity of a NetTrace — the
    net itself (guaranteed present on any NetTrace, and unique per record by
    a load-time check, so it is a safe, low-typing-cost identity for --only,
    exactly like Rule's net-derived default name)."""
    return nt.net


@dataclass
class ClonePlacement:
    """
    Applying a cell at a new location (TemplatePlacer/Cloner) — unlike
    ManualSpoke (anchor = IC pad), the anchor here is just a name, not tied to
    any specific component (anchor_id in registry = f"name:{name}"). Two
    positioning modes:
      - an anchor set (anchor_ref/anchor_role/anchor_point): origin = centre
        of anchor_pad (or footprint centre if anchor_pad omitted, or the
        point's own position for anchor_point), xy is an optional FLAT shift
        from the anchor (without rotation, like shift in ManualSpoke),
        rotation_deg rotates only the cell contents.
      - no anchor set: xy is an ABSOLUTE point on the board (required).

    Offset — EITHER Cartesian xy (the default) OR polar radius_mm/angle_deg
    (an OPTIONAL alternative, both required together; fatal if BOTH xy and
    radius_mm/angle_deg are set — see config/entries.py). Same field names as
    CoordinatePlacement for consistency. Polar describes the shift vector as
    "radius_mm along the X axis, rotated by angle_deg" — and, crucially for
    nested Cells (Phase 4 recursion), it passes through the SAME
    parent_rotation_deg composition as xy does, i.e.
    shift = rotate_local_offset(radius_mm, 0.0, angle_deg + parent_rotation_deg)
    (see clone_geometry.py). Like xy, polar angle_deg does NOT become
    rotation_deg — it only positions the origin; the cell's own orientation
    stays under rotation_deg.

    cell — REQUIRED (2026-08-12, Group 0 consolidation): a reference to a Cell
    from cfg.cells. The role:/cluster: single-component modes that used to live
    here were migrated 1:1 to CoordinatePlacement's anchor-relative mode (see
    CoordinatePlacement's docstring) — ClonePlacement is once again pure
    template cloning, cell: is mandatory, no cell-OR-role-OR-cluster branch.

    Role→ref mapping — EITHER via the current selection on the board (for rare,
    one‑off sections like a single MCU), OR via explicit nets
    (params/nets/net_overrides — for repeated sections like PI‑filters or DAC
    channels). Presence of params OR nets means "by nets" mode; absence means
    "by selection".

    skip — see ThermalViaArrayConfig.skip for the retired-vs-skip distinction.

    ignore_selection — per-item counterpart of the CLI's --no-selection
    (kicadstamp_cli.py), default False. When True, this clone_placement's own
    anchor resolution and role resolution (by_selection mode, and the
    selection-narrowing step shared with by-nets ambiguity resolution) treat
    the live PCB editor selection as empty, regardless of --no-selection —
    OR-composes with it, does not override it off. Useful when only SOME
    clone_placements are bothered by a stray leftover GUI selection, while
    others in the same run genuinely rely on it (e.g. a one-off MCU placed
    "by selection" — see resolve_roles_by_selection's docstring in
    clone_role_resolver.py).
    """
    # Cluster TAG — the physical cluster this placement writes onto the
    # board's components (PlacerDock's Cluster field), read by
    # role_narrowing.py as the placement's OWN Cluster for internal role
    # narrowing (placement/services/role_narrowing.py). NOT the save/--only
    # identity — that is `name` below. Renamed 2026-08-24 from `name`, which
    # used to be BOTH this Cluster tag and the identity fallback — the
    # conflation that made two instances of one reused hierarchical sheet
    # (same Cluster, legitimately) falsely collide under the old
    # name-uniqueness check in validation.py.
    cluster: str
    xy: tuple[float, float]
    cell: str
    radius_mm: float | None = None
    angle_deg: float | None = None
    rotation_deg: float = 0.0
    nets: dict[str, str] = field(default_factory=dict)      # role -> net (literal)
    params: dict[str, Any] = field(default_factory=dict)    # for {placeholder} in net cells
    net_overrides: dict[str, str] = field(default_factory=dict)  # final override of resolved name
    retired: bool = False
    skip: bool = False
    ignore_selection: bool = False
    anchor_ref: str | None = None
    anchor_pad: str | None = None
    # Alternative to anchor_ref — anchor by the Role field on the board, not by
    # refdes (survives re‑annotation). Mutually exclusive with anchor_ref (fatal
    # if both are set — see _load_clone_placement). anchor_sheet — ONLY narrows
    # ambiguity when there are 2+ candidates with the same anchor_role
    # (comparison by prefix of LOCAL hierarchical net name, e.g. '/Channel_0/...' —
    # NOT via sheet_path/UUID, which was empirically broken — see chat scripts).
    # Meaningless without anchor_role.
    anchor_role: str | None = None
    anchor_sheet: str | None = None
    # Cluster — second custom field (see constants.CLUSTER_FIELD_NAME),
    # physical instance/cluster, independent of anchor_ref/anchor_role.
    # Used in ONE place: narrowing the search for anchor_role only (like
    # anchor_sheet, but via a different field — see resolve_footprint_by_role
    # in clone_role_resolver.py). Cluster-based narrowing of roles INSIDE the
    # cell instead uses this placement's own `name` (see role_narrowing.py::
    # _narrow_ambiguous_candidates) — split 2026-08-14, the two were conflated
    # into this one field before (Denis: printing the Cluster twice was redundant).
    # Comparison is by PREFIX segments ('Channel_1' matches both 'Channel_1'
    # and 'Channel_1/1V2_PLL_PI_FILTER'), not by exact equality — hierarchy
    # and flat names work with the same code.
    anchor_cluster: str | None = None
    # Own-identity sheet — split 2026-08-15 from anchor_sheet (the second
    # half of the 08-14 anchor_cluster split, mirroring exactly what name is
    # for Cluster): this field narrows roles INSIDE the cell
    # (role_narrowing.py::_narrow_ambiguous_candidates), while `anchor_sheet`
    # narrows ONLY the external anchor search (resolve_footprint_by_role).
    # Same (Sheet, Cluster, Role) addressing convention as the rest of the
    # project, this time completing it — a reused hierarchical sheet clones
    # IDENTICAL Cluster/Role fields onto every instance, so only the sheet
    # can tell two physical copies apart (Denis, live: AD_DAC/IC2 exists
    # identically on every channel's cloned sheet). Optional — only needed
    # when Cluster+Role alone is ambiguous.
    sheet: str | None = None
    # Save/--only identity — split 2026-08-15 from the Cluster tag (then named
    # placer_name, renamed 2026-08-24 to name after the Cluster tag moved into
    # its own `cluster:` field above): upsert_clone_placement's key_fn and
    # PlacerDock's Redraw replace-by-name filter both match on this instead of
    # the raw Cluster tag, so renaming/re-tagging Cluster on an already-saved
    # entry no longer creates a duplicate config entry (Denis, live: renaming
    # Cluster back and forth on PIF_AVDD/CH0_PIF_AVDD kept spawning a second
    # entry, because the field WAS both the tag and the save key). Optional —
    # None means "same as cluster", the fallback every existing
    # clone_placement (none of which set this) keeps using.
    name: str | None = None
    # Alternative to anchor_ref/anchor_role — name of a points: entry (see
    # config/points.py). Mutually exclusive with anchor_ref/anchor_role
    # (fatal if combined — see config/loader.py). Unlike Rule/
    # ThermalViaArrayConfig, ClonePlacement only ever needs a coordinate
    # (not a footprint) — a shifted or xy-literal Point works fine here.
    anchor_point: str | None = None
    # Placement layer — FACT: None = cell layer (place verbatim).
    # mirror — OPERATION, always manual: flip the whole construction
    # (geometry mirrored, angles 180°−φ, all layers inverted).
    # Contradiction between the two is fatal at load: mirror without layer change
    # or layer change without mirror is physically meaningless.
    layer: str | None = None
    mirror: bool = False
    # Explicit override role -> ref (highest priority, bypassing net‑based search):
    # last resort when candidates are electrically indistinguishable
    # (e.g. three identical filters in one sheet).
    refs: dict[str, str] = field(default_factory=dict)
    # Explicit request for selection mode — NOT inferred from absence of nets/params
    # (that implicit behaviour remains the default for backward compatibility,
    # see clone_uses_selection_mode). Needed separately from implicit because
    # params is ALSO used for resolving placeholders in via/track nets
    # (apply_clone_geometry calls resolve_net regardless of the role mode) —
    # without this flag, a params intended only for via net resolution would
    # silently switch the whole clone_placement to "by nets" mode, breaking roles
    # resolved by selection. by_selection: true + non‑empty nets is fatal at load
    # (contradiction: nets has no meaning in selection mode).
    by_selection: bool = False


def clone_placement_effective_name(clone: "ClonePlacement") -> str:
    """Single point for reading the SAVE/--only identity of a
    ClonePlacement — name if set, else cluster (the Cluster tag).
    See ClonePlacement.name's own comment for why these two were split."""
    return clone.name or clone.cluster


@dataclass
class Config:
    """Main configuration object."""
    # Spoke layer (ManualSpoke path): 'F.Cu' | 'B.Cu'. clone_placements have
    # their own layer/mirror per placement; this field does not affect them.
    layer: str = 'F.Cu'
    cells: dict[str, Cell] = field(default_factory=dict)
    points: dict[str, Point] = field(default_factory=dict)
    thermal_via_arrays: list[ThermalViaArrayConfig] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    clone_placements: list[ClonePlacement] = field(default_factory=list)
    coordinate_placements: list[CoordinatePlacement] = field(default_factory=list)
    net_traces: list[NetTrace] = field(default_factory=list)
    # trees: — optional curated-redraw trees (list section, Tree.name unique
    # across the whole include graph). Loaded by config/entries.py::_load_tree
    # (a wrapper over trees.py's tree_from_dict), serialized by
    # sexp_format.py's special trees branch (design_2026_08_27_trees_in_config_file.md).
    trees: list[Tree] = field(default_factory=list)
    place_components: bool = True
    skip_existing_components: bool = False
    # Free‑space search parameters — currently used only for thermal vias
    # (power/GND vias are placed manually, no search).
    via_keepout_clearance_mm: float = 0.2
    via_search_step_mm: float = 0.1
    via_search_max_radius_mm: float = 3.0
    via_search_n_directions: int = 8
    # For anchor_sheet (see ClonePlacement) — dict {uuid: Sheetname}
    # built by directly parsing *.kicad_sch (sexpdata, same format as cloner),
    # NOT through kipy — see discussion: sheet_path.path_human_readable is broken
    # in this KiCad version, and UUID from kipy (path[:-1]) empirically matches
    # the sheet UUIDs in .kicad_sch. schematic_dir — folder containing all
    # *.kicad_sch of the project (path relative to the YAML config itself);
    # schematic_files — extra files for sheets outside schematic_dir.
    schematic_dir: str | None = None
    schematic_files: list[str] = field(default_factory=list)
    # GUI-only (fieldstool's schematic-vs-board Pending changes diff, see
    # gui/schema_model.py's load_schematic_components) — the single root
    # .kicad_sch to walk the WHOLE sheet hierarchy from, unrelated to
    # schematic_dir/schematic_files above (those build a uuid->name lookup
    # via a flat directory scan, not a hierarchy walk). Not read by the CLI
    # (apply/extract have no use for it). Used to live purely in the GUI's
    # own QSettings, keyed globally rather than per-project — found live
    # 2026-08-07: switching projects silently kept the previous project's
    # root sheet, so Pending changes matched nothing against a schematic it
    # was never told changed. Path relative to this YAML, like schematic_dir.
    root_sheet: str | None = None
    # Explicit override for registry file paths — by default they are derived
    # from the CONFIG file name itself (registry_path_for_config), which changes
    # when the config is renamed. Paths are relative to this YAML.
    registry_path: str | None = None
    track_registry_path: str | None = None
    # Path to log file for `apply` of this config (relative to this YAML,
    # like registry_path) — useful to avoid passing --log-file manually each time
    # for the same board profile. CLI flag --log-file, if given, TAKES PRIORITY
    # over this field (see main() in kicadstamp_cli.py).
    log_file: str | None = None
    # Directory for undo operation logs (operation_*.json), relative to this
    # YAML like registry_path/log_file — the single source of truth for where
    # `apply` writes and `undo` reads, instead of both silently depending on the
    # process CWD. When unset, OperationLogger/cmd_undo fall back to
    # DEFAULT_LOG_DIR ("logs" next to the CWD) for backward compatibility.
    operation_log_dir: str | None = None
    # Which board (.kicad_pcb) this config targets — e.g. '3CH-AWG-TIA-v102'
    # (with or without the '.kicad_pcb' extension). Optional (opt-in): when set,
    # validation's check_board_identity() fatals if the board actually open in
    # KiCad has a DIFFERENT name — the explicit "you opened the wrong board"
    # guard added 2026-08-20 after a real incident where a stale schematic_dir
    # pointed at a previous board revision and the mismatch surfaced only as an
    # unrelated-looking fatal deep in Extract. Compared case-insensitively by
    # basename stem ONLY (never the full path): the config and the live board
    # live in unrelated directory trees, and paths differ across Denis's
    # Windows/Linux machines. Unlike every other string field above this is NOT
    # a path — deliberately not resolved relative to the YAML.
    board_name: str | None = None
    @property
    def anchor_refs(self) -> set:
        """All anchor refs in the config: spoke rules + thermal via arrays."""
        out = {r.anchor_ref for r in self.rules if r.anchor_ref}
        out |= {tva.anchor_ref for tva in self.thermal_via_arrays
                if not tva.retired and tva.anchor_ref}
        return out

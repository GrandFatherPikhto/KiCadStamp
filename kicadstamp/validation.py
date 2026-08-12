# kicadstamp/validation.py
"""
validation.py — fatal pre‑validation checks, executed BEFORE planning and any
board modifications. If a problem is found, a ValidationError is raised with a
clear, consolidated message listing all issues at once (not one error per run).

CHANGED (KiCadStamp 4.0): previously explicit refs in config
(component1_ref/component2_ref) were checked — they no longer exist; components
are selected from ComponentPool by (real net, role). The main protection is now
built into ComponentPool.pop() itself (fatal on shortage), but here we do the
same accounting IN ADVANCE to see all shortages at once, rather than stopping
at the first spoke.
"""
import logging
import difflib


from .config import Config
from .geometry.clone_geometry import clone_shift_mm
from .kicad.adapter import KiCadBoardAdapter
from .exceptions import ValidationError, format_fatal_error
from .net_resolution import resolve_net
from .placement.services.component_pool import ComponentPool
from .placement.services.clone_role_resolver import (
    clone_uses_selection_mode,
    resolve_footprint_by_role,
)
from .i18n import _

logger = logging.getLogger(__name__)


def check_cells_and_pads_exist(adapter: KiCadBoardAdapter, cfg: Config, sheet_names=None) -> None:
    """
    Every spoke must reference an existing cell and an existing pad of the
    target component — otherwise the spoke is simply skipped silently (which
    would make it easy to miss a typo in the cell name/pad number).
    """
    problems = []
    anchors = {}
    for rule in cfg.rules:
        # Resolve anchor: either anchor_ref or anchor_role
        if rule.anchor_ref is not None:
            fp = adapter.get_footprint(rule.anchor_ref)
            if fp is None:
                problems.append(_("rule (net {net!r}): anchor {anchor!r} not found on board")
                                .format(net=rule.net, anchor=rule.anchor_ref))
            anchors[rule.anchor_ref] = fp
        else:
            try:
                fp = resolve_footprint_by_role(
                    adapter,
                    rule.anchor_role,
                    rule.anchor_sheet,
                    rule.anchor_cluster,
                    sheet_names or {},
                    label=_("rule (net {net!r})").format(net=rule.net)
                )
                anchors[f"role:{rule.anchor_role}"] = fp
            except ValidationError as e:
                problems.append(str(e))

    for rule in cfg.rules:
        if rule.anchor_ref is not None:
            target_fp = anchors.get(rule.anchor_ref)
        else:
            target_fp = anchors.get(f"role:{rule.anchor_role}")
        if target_fp is None:
            continue
        for spoke in rule.spokes:
            if spoke.retired:
                continue
            if spoke.cell not in cfg.cells:
                problems.append(_("spoke (pad {pad}, net {net!r}): cell {cell!r} not found in cells")
                                .format(pad=spoke.pad, net=rule.net, cell=spoke.cell))
                continue
            pad = adapter.get_pad_by_number(target_fp, spoke.pad) if target_fp else None
            if target_fp is not None and pad is None:
                anchor_name = rule.anchor_ref if rule.anchor_ref is not None else rule.anchor_role
                problems.append(_("spoke (cell {cell!r}, net {net!r}): {anchor!r} has no pad {pad!r}")
                                .format(cell=spoke.cell, net=rule.net,
                                        anchor=anchor_name, pad=spoke.pad))

    if problems:
        raise ValidationError(format_fatal_error(
            _("spoke references a non‑existent cell or pad"),
            problems
        ))
    logger.debug(_("Cell/pad checks for spokes: all references valid"))


def check_role_pool_sufficiency(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    For each rule net, pre‑counts how many components of each role are required
    by all its spokes for each cluster, and checks against the actual number of
    components on the board (same net + Role field + Cluster field) — fatal with
    a list of all shortages at once.
    """
    problems = []

    for rule in cfg.rules:
        # Collect all roles needed for this rule
        roles_needed = set()
        for spoke in rule.spokes:
            if spoke.retired:
                continue
            cell = cfg.cells.get(spoke.cell)
            if cell is None:
                continue
            for slot in cell.components:
                roles_needed.add(slot.role)

        if not roles_needed:
            continue

        # Collect all clusters used in spokes (including None)
        clusters_needed = set()
        for spoke in rule.spokes:
            if spoke.retired:
                continue
            clusters_needed.add(spoke.cluster)  # None is allowed

        # Initialise requirement dictionary per cluster
        needed_by_cluster: dict[str | None, dict[str, int]] = {
            cluster: {role: 0 for role in roles_needed}
            for cluster in clusters_needed
        }

        # Fill requirements
        for spoke in rule.spokes:
            if spoke.retired:
                continue
            cell = cfg.cells.get(spoke.cell)
            if cell is None:
                continue
            cluster = spoke.cluster
            for slot in cell.components:
                needed_by_cluster[cluster][slot.role] += 1

        # For each cluster, check sufficiency
        for cluster, needed_counts in needed_by_cluster.items():
            if not any(needed_counts.values()):
                continue

            pool = ComponentPool(adapter, rule.net, roles=sorted(roles_needed), cluster=cluster)
            for role, needed in needed_counts.items():
                if needed == 0:
                    continue
                available = pool.remaining_count(role)
                if available < needed:
                    cluster_label = _(" (cluster {cluster!r})").format(cluster=cluster) if cluster is not None else ""
                    problems.append(
                        _("net {net!r}, role {role!r}{cluster}: need {needed}, found {available} "
                          "(check the Role and Cluster fields in the schematic and the actual net connection)")
                        .format(net=rule.net, role=role, cluster=cluster_label,
                                needed=needed, available=available)
                    )

    if problems:
        raise ValidationError(format_fatal_error(
            _("not enough components for cell roles"),
            problems
        ))
    logger.debug(_("Role pool sufficiency checks passed"))


def check_clone_cells_exist(cfg: Config) -> None:
    """
    Every ClonePlacement with cell (not role) must reference an existing
    cell — pure config check, does not require the live board. role‑based
    placements are skipped: their cell is intentionally None, and
    ClonePositionCalculator synthesises a single‑component cell on the fly,
    so there is nothing to check in cfg.cells.

    Also walks every Cell's OWN clone_placements (nested CellPlacement
    entries, see config/models.py — recursive cells, 2026-07-31) and checks
    their cell references the same way — one place for "does every
    reference into cfg.cells resolve", not a second, separate check.
    """
    problems = []
    for clone in cfg.clone_placements:
        if clone.retired or clone.cell is None:
            continue
        if clone.cell not in cfg.cells:
            problems.append(_("clone_placement {name!r}: cell {cell!r} not found in cells")
                            .format(name=clone.name, cell=clone.cell))
    for cell in cfg.cells.values():
        for nested in cell.clone_placements:
            if nested.cell is None:
                continue
            if nested.cell not in cfg.cells:
                problems.append(
                    _("cell {owner!r}: nested clone_placement {name!r}: cell {cell!r} not found in cells")
                    .format(owner=cell.name, name=nested.name, cell=nested.cell))
    if problems:
        raise ValidationError(format_fatal_error(
            _("clone_placement references a non‑existent cell"),
            problems
        ))
    logger.debug(_("Clone cell existence checks passed"))


def check_no_cell_definition_cycles(cfg: Config) -> None:
    """
    "Occurs check" over cell DEFINITIONS (not placements) — a cell that,
    directly or through nested clone_placements, ends up containing itself
    has no well‑founded geometry (infinite recursion) and must be rejected
    at load time, before any recursive resolution is ever attempted (see
    ClonePositionCalculator's recursive resolver).

    Pure config check, no live board needed. Deliberately a SEPARATE concern
    from dependency_order.py's cycle detection: that one is about the order
    of PLACEMENT within one apply run (rules/clone_placements/points
    anchored on each other's live output); this one is about the tree of
    cell DEFINITIONS itself, resolved once at load time, well before any
    board is even connected to.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in cfg.cells}
    path: list[str] = []

    def visit(name: str) -> None:
        color[name] = GREY
        path.append(name)
        cell = cfg.cells[name]
        for nested in cell.clone_placements:
            if nested.cell is None or nested.cell not in cfg.cells:
                continue  # unknown-cell case already reported by check_clone_cells_exist
            child_color = color[nested.cell]
            if child_color == GREY:
                cycle_start = path.index(nested.cell)
                cycle = path[cycle_start:] + [nested.cell]
                raise ValidationError(format_fatal_error(
                    _("cycle among cell definitions"),
                    [_("{cycle} — a cell cannot contain itself, directly or through nesting")
                     .format(cycle=" -> ".join(cycle))]
                ))
            if child_color == WHITE:
                visit(nested.cell)
        path.pop()
        color[name] = BLACK

    for name in cfg.cells:
        if color[name] == WHITE:
            visit(name)
    logger.debug(_("Cell definition cycle checks passed"))


def check_no_duplicate_clone_anchors(cfg: Config) -> None:
    """
    Pure config check (does not require live board):
      1. clone_placements[].name must be unique — this is the primary identifier
         for anchor‑less placements (see clone_anchor_id) and good hygiene.
      2. (content, anchor_ref, anchor_pad, xy) among
         clone_placements with anchor_ref set must be unique — this mirrors
         the identity used by the registry (registry.py, see clone_anchor_id).
         If two different clone_placements accidentally point to the same
         physical anchor AND the same offset, the registry will confuse their
         vias/tracks. This is almost certainly a copy‑paste typo (forgot to
         change anchor_pad or xy in the second block),
         not intentional. "Content" is cell OR role — two different roles
         on the same anchor are NOT duplicates (different components at the
         same point is normal), so we use what is actually set, not
         clone.cell (which is None for role‑based placements, and two
         different roles would collapse into one key if we only used
         cell). xy is included (found 2026-07-27)
         because it's legitimate for two clones to share an anchor and differ
         only by this offset (e.g. a positive/negative filter pair mirrored
         off the same connector pad) — without it in the key, that legitimate
         case was indistinguishable from a real duplicate, both to this check
         and to the registry itself. anchor_cluster is included in the
         anchor_role key (found 2026-07-28, same reasoning): p5v_led_spoke/
         n5v_led_spoke share identical anchor_role/anchor_sheet/anchor_pad/
         origin and differ ONLY by anchor_cluster (Pos vs Neg, the field that
         actually picks which physical component the anchor resolves to) —
         without it here, this check false-positived on that legitimate pair.
      3. Same check for anchor_point (found 2026-08-06, same gap as
         clone_anchor_id's missing anchor_point branch): two clones anchored
         on the same Point with the same offset are just as confusable to the
         registry as two anchor_ref/anchor_role duplicates would be.
    """
    problems = []
    seen_names = {}
    seen_point_anchors = {}
    seen_ref_anchors = {}
    seen_role_anchors = {}
    for clone in cfg.clone_placements:
        if clone.retired:
            continue
        if clone.name in seen_names:
            problems.append(_("name {name!r} appears twice in clone_placements — names must be unique")
                            .format(name=clone.name))
        seen_names[clone.name] = True

        content_id = clone.cell if clone.cell is not None else _("role:{role}").format(role=clone.role)
        ox, oy = clone_shift_mm(clone)
        origin = (round(ox, 4), round(oy, 4))

        if clone.anchor_point is not None:
            key = (content_id, clone.anchor_point, origin)
            if key in seen_point_anchors:
                problems.append(
                    _("{this!r} and {other!r} both point to the same anchor with the same offset "
                      "(cell/role={content!r}, anchor_point={point!r}, origin=({ox}, {oy}) mm) — "
                      "the registry would confuse their vias/tracks; likely a copy‑paste typo (if "
                      "this is intentional, give them different xy)")
                    .format(this=clone.name, other=seen_point_anchors[key], content=content_id,
                            point=clone.anchor_point, ox=origin[0], oy=origin[1])
                )
            seen_point_anchors[key] = clone.name

        if clone.anchor_ref is not None:
            key = (content_id, clone.anchor_ref, clone.anchor_pad, origin)
            if key in seen_ref_anchors:
                problems.append(
                    _("{this!r} and {other!r} both point to the same anchor with the same offset "
                      "(cell/role={content!r}, anchor_ref={ref!r}, anchor_pad={pad!r}, "
                      "origin=({ox}, {oy}) mm) — the registry would confuse their vias/tracks; "
                      "likely a copy‑paste typo (if this is intentional, give them different "
                      "xy)")
                    .format(this=clone.name, other=seen_ref_anchors[key], content=content_id,
                            ref=clone.anchor_ref, pad=clone.anchor_pad, ox=origin[0], oy=origin[1])
                )
            seen_ref_anchors[key] = clone.name

        if clone.anchor_role is not None:
            key = (content_id, clone.anchor_role, clone.anchor_sheet, clone.anchor_cluster,
                   clone.anchor_pad, origin)
            if key in seen_role_anchors:
                problems.append(
                    _("{this!r} and {other!r} both point to the same anchor with the same offset "
                      "(cell/role={content!r}, anchor_role={role!r}, anchor_sheet={sheet!r}, "
                      "anchor_cluster={cluster!r}, anchor_pad={pad!r}, origin=({ox}, {oy}) mm) — "
                      "the registry would confuse their vias/tracks; likely a copy‑paste typo (if "
                      "this is intentional, give them different xy)")
                    .format(this=clone.name, other=seen_role_anchors[key], content=content_id,
                            role=clone.anchor_role, sheet=clone.anchor_sheet, cluster=clone.anchor_cluster,
                            pad=clone.anchor_pad, ox=origin[0], oy=origin[1])
                )
            seen_role_anchors[key] = clone.name

    if problems:
        raise ValidationError(format_fatal_error(
            _("clone_placements with ambiguous identity"),
            problems
        ))
    logger.debug(_("Duplicate clone anchor checks passed"))


def check_anchor_sheet_configured(cfg: Config, sheet_names=None) -> None:
    """
    Pure config check. anchor_sheet is resolved via sheet_names.
    If sheet_names is empty, it means neither schematic_dir nor schematic_files
    were set (or none of the .kicad_sch files could be parsed), and anchor_sheet
    will NEVER narrow anything — it will silently do nothing, and later ambiguity
    of anchor_role will fail with a less helpful fatal. Better to say it upfront.
    """
    _sn = sheet_names or {}
    users = [c.name for c in cfg.clone_placements if not c.retired and c.anchor_sheet]
    if users and not _sn:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet is used but sheet name dictionary is empty"),
            [_("clone_placements with anchor_sheet: {users}").format(users=users),
             _("you need schematic_dir (or schematic_files) at the root of the config — "
               "path to the folder with *.kicad_sch files, relative to this YAML")]
        ))
    logger.debug(_("anchor_sheet/sheet_names check passed"))


def check_clone_nets_exist_on_board(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Resolves via.net for EACH clone_placement (both spoke‑level and those nested
    in components[i].vias — see apply_clone_geometry) and checks the result
    against the real board nets (adapter.get_all_nets()).

    Why separate from resolve_roles_by_nets: role‑to‑ref mapping already checks
    itself (candidates are searched among real pads, a non‑existent net simply
    yields no candidates — which is fatal). But via.net goes straight into
    ViaCommand without such checking — a typo in net_overrides or params that
    yields a syntactically valid string (e.g. "+3V3_DVD" instead of "+3V3_DVDD")
    would quietly create a via on the wrong net, with no fatal along the way.
    This check is that missing dictionary.

    via.net=None is not checked here — that is already fatal in clone_geometry.py
    (ClonePlacement has no default net), no need to duplicate.
    """
    problems = []
    real_nets = {n.name for n in adapter.get_all_nets()}

    def _check_via(via, clone, where: str):
        if via.net is None:
            return
        try:
            resolved = resolve_net(via.net, clone.params, clone.net_overrides)
        except ValidationError:
            return  # missing parameter — already a fatal error higher up
        if resolved not in real_nets:
            hint = difflib.get_close_matches(resolved, real_nets, n=1)
            suggestion = _(" — did you mean {suggestion!r}?").format(suggestion=hint[0]) if hint else ""
            problems.append(
                _("{name!r}, {where}: via.net {net_name!r} resolves to {resolved!r}, "
                  "but that net does not exist on the board{suggestion}")
                .format(name=clone.name, where=where, net_name=via.net,
                        resolved=resolved, suggestion=suggestion)
            )

    for clone in cfg.clone_placements:
        if clone.retired:
            continue
        cell = cfg.cells.get(clone.cell)
        if cell is None:
            continue  # already caught by check_clone_cells_exist
        for via in cell.vias:
            _check_via(via, clone, _("spoke‑level via"))
        for slot in cell.components:
            for via in slot.vias:
                _check_via(via, clone, _("via of role {role!r}").format(role=slot.role))

    if problems:
        raise ValidationError(format_fatal_error(
            _("resolved via net references a non‑existent board net"),
            problems
        ))
    logger.debug(_("clone via.net checks against real board nets passed"))


def check_single_selection_based_clone(cfg: Config) -> None:
    """
    In KiCad only ONE selection is present at any moment — therefore you cannot
    process more than one ClonePlacement in "by selection" mode (no nets, no params)
    in a single run. If more than one, fatal with a hint to either retire the
    extras (retired: true) or run apply separately for each with --only NAME.
    """
    selection_based = [c.name for c in cfg.clone_placements if not c.retired and clone_uses_selection_mode(c)]
    if len(selection_based) > 1:
        raise ValidationError(format_fatal_error(
            _("multiple clone_placements in 'by selection' mode in one run"),
            [_("found {count}: {names} — KiCad has only one selection at a time, "
               "so processing all at once is impossible").format(
                   count=len(selection_based), names=selection_based),
             _("solution: either set retired: true on all but one, or run apply "
               "separately for each using --only NAME")]
        ))
    logger.debug(_("Single selection‑based clone check passed"))


def run_all_checks(adapter: KiCadBoardAdapter, cfg: Config, sheet_names=None) -> None:
    """Runs all checks in order — from cheap to more comprehensive."""
    _sn = sheet_names or {}
    logger.info(_("Running pre‑validation checks..."))
    check_clone_cells_exist(cfg)
    check_no_cell_definition_cycles(cfg)
    check_no_duplicate_clone_anchors(cfg)
    check_anchor_sheet_configured(cfg, sheet_names=_sn)
    check_single_selection_based_clone(cfg)
    check_cells_and_pads_exist(adapter, cfg, sheet_names=_sn)
    check_role_pool_sufficiency(adapter, cfg)
    check_clone_nets_exist_on_board(adapter, cfg)
    logger.info(_("All pre‑validation checks passed"))
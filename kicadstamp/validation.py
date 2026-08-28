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
from pathlib import Path


from .config import (
    Config, Rule, ManualSpoke, clone_placement_effective_name,
    coordinate_placement_effective_name, rule_effective_name,
)
from .geometry.clone_geometry import clone_shift_mm
from .kicad.adapter import KiCadBoardAdapter
from .exceptions import ValidationError, format_fatal_error
from .net_resolution import resolve_net
from .placement.services.component_pool import ComponentPool
from .placement.services.clone_role_resolver import (
    _prefix_remap_local_net,
    clone_uses_selection_mode,
    resolve_footprint_by_role,
)
from .placement.services.coordinate_position_calculator import resolve_footprint_by_cluster_role
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
        if clone.retired:
            continue
        if clone.cell not in cfg.cells:
            problems.append(_("clone_placement {name!r}: cell {cell!r} not found in cells")
                            .format(name=clone_placement_effective_name(clone), cell=clone.cell))
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
      1. clone_placements' effective identity (name-or-cluster) must be unique —
         this is the save/--only identity, and is also the fallback identifier
         for anchor‑less placements (see clone_anchor_id).
      2. (content, anchor_ref, anchor_pad, xy) among
         clone_placements with anchor_ref set must be unique — this mirrors
         the identity used by the registry (registry.py, see clone_anchor_id).
         If two different clone_placements accidentally point to the same
         physical anchor AND the same offset, the registry will confuse their
         vias/tracks. This is almost certainly a copy‑paste typo (forgot to
         change anchor_pad or xy in the second block),
         not intentional. "Content" is the (mandatory) cell — a role:/cluster:
         single‑component variant used to exist too but was migrated 1:1 to
         coordinate_placements on 2026‑08‑12 (Group 0), so this is now always
         clone.cell. xy is included (found 2026-07-27)
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
    seen_effective_names = {}
    seen_point_anchors = {}
    seen_ref_anchors = {}
    seen_role_anchors = {}
    for clone in cfg.clone_placements:
        if clone.retired:
            continue

        # Effective SAVE/--only identity must be unique (2026-08-15, plan
        # clone_placement_placer_name_split; 2026-08-24 the Cluster tag moved
        # to its own `cluster:` field): two entries CAN share a Cluster tag
        # (legitimately — a reused hierarchical sheet clones identical Cluster
        # onto every instance) and still be distinct, so only the identity
        # (name, falling back to cluster) is checked. The old duplicate check
        # on the Cluster tag itself was REMOVED 2026-08-24 — it was the false
        # positive behind "name 'PIF_DVDD' appears twice in clone_placements".
        effective = clone_placement_effective_name(clone)
        if effective in seen_effective_names:
            problems.append(
                _("Name {name!r} appears twice in clone_placements — "
                  "used by {users}; name identities must be unique")
                .format(name=effective, users=", ".join(sorted(seen_effective_names[effective] + [clone.cluster]))))
        seen_effective_names.setdefault(effective, []).append(clone.cluster)

        content_id = clone.cell
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
                    .format(this=clone_placement_effective_name(clone), other=seen_point_anchors[key],
                            content=content_id, point=clone.anchor_point, ox=origin[0], oy=origin[1])
                )
            seen_point_anchors[key] = clone_placement_effective_name(clone)

        if clone.anchor_ref is not None:
            key = (content_id, clone.anchor_ref, clone.anchor_pad, origin)
            if key in seen_ref_anchors:
                problems.append(
                    _("{this!r} and {other!r} both point to the same anchor with the same offset "
                      "(cell/role={content!r}, anchor_ref={ref!r}, anchor_pad={pad!r}, "
                      "origin=({ox}, {oy}) mm) — the registry would confuse their vias/tracks; "
                      "likely a copy‑paste typo (if this is intentional, give them different "
                      "xy)")
                    .format(this=clone_placement_effective_name(clone), other=seen_ref_anchors[key],
                            content=content_id, ref=clone.anchor_ref, pad=clone.anchor_pad,
                            ox=origin[0], oy=origin[1])
                )
            seen_ref_anchors[key] = clone_placement_effective_name(clone)

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
                    .format(this=clone_placement_effective_name(clone), other=seen_role_anchors[key],
                            content=content_id, role=clone.anchor_role, sheet=clone.anchor_sheet,
                            cluster=clone.anchor_cluster, pad=clone.anchor_pad, ox=origin[0], oy=origin[1])
                )
            seen_role_anchors[key] = clone_placement_effective_name(clone)

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
    _sn = dict(sheet_names) if sheet_names is not None else {}
    users = [clone_placement_effective_name(c)
             for c in cfg.clone_placements if not c.retired and c.anchor_sheet]
    if users and not _sn:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet is used but sheet name dictionary is empty"),
            [_("clone_placements with anchor_sheet: {users}").format(users=users),
             _("you need schematic_dir (or schematic_files) at the root of the config — "
               "path to the folder with *.kicad_sch files, relative to this YAML")]
        ))
    net_trace_users = [nt.net for nt in cfg.net_traces if not nt.retired and nt.anchor_sheet]
    if net_trace_users and not _sn:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet is used but sheet name dictionary is empty"),
            [_("net_traces with anchor_sheet: {users}").format(users=net_trace_users),
             _("you need schematic_dir (or schematic_files) at the root of the config — "
               "path to the folder with *.kicad_sch files, relative to this YAML")]
        ))
    logger.debug(_("anchor_sheet/sheet_names check passed"))


def _spoke_roles(cfg: Config, spoke: ManualSpoke) -> set:
    """The set of component Role fields a spoke's cell needs — the SAME roles
    ComponentPool is built with (see manual_position_calculator.py:112-118)."""
    cell = cfg.cells.get(spoke.cell)
    if cell is None:
        return set()
    return {slot.role for slot in cell.components}


def check_no_candidate_pool_collisions(cfg: Config) -> None:
    """Pure config check (no live board). Two rules sharing one net and
    consuming the SAME (role, spoke-cluster) candidate pool will silently
    steal each other's components: ComponentPool is rebuilt per rule
    (manual_position_calculator.py), filtering candidates ONLY by net + Role +
    (if set) spoke cluster (component_pool.py) — no ownership registry, no
    distance heuristic — so whichever rule plans later pops the same
    candidates first (natural order). Cluster is the documented mechanism for
    splitting several rules on one net (Rule's own docstring) but nothing
    enforced/checked it.

    Reported on the FULL config (before --only/--cluster narrow it) — this is
    exactly when it must fire: a Redraw (--only=<one rule>) would otherwise
    hide the collision by filtering the sibling rule out before it is ever
    seen (apply_only_filter), which is the live incident that motivated this
    check. Fatal (never a proximity heuristic): each problem names both rules,
    the net, the shared role and the spoke cluster (or "no cluster"); the hint
    points at the standard fix — a distinguishing spoke cluster: on one of
    them."""
    problems = []
    seen: set[tuple] = set()
    rules_by_net: dict[str, list[Rule]] = {}
    for rule in cfg.rules:
        if rule.retired or rule.skip:
            continue  # drop_inactive_items drops these before planning
        rules_by_net.setdefault(rule.net, []).append(rule)

    for net, rules in rules_by_net.items():
        if len(rules) < 2:
            continue
        for i in range(len(rules)):
            for j in range(i + 1, len(rules)):
                a, b = rules[i], rules[j]
                for sa in a.spokes:
                    if sa.retired or sa.skip:
                        continue
                    roles_a = _spoke_roles(cfg, sa)
                    if not roles_a:
                        continue
                    for sb in b.spokes:
                        if sb.retired or sb.skip:
                            continue
                        if sa.cluster != sb.cluster:
                            continue
                        shared = roles_a & _spoke_roles(cfg, sb)
                        if not shared:
                            continue
                        for role in sorted(shared):
                            key = (rule_effective_name(a), rule_effective_name(b),
                                   role, sa.cluster)
                            if key in seen:
                                continue
                            seen.add(key)
                            cluster_hint = (_(" (cluster {cluster!r})").format(cluster=sa.cluster)
                                            if sa.cluster is not None else _(" (no cluster)"))
                            problems.append(
                                _("rules {a!r} and {b!r} on net {net!r} both consume role "
                                  "{role!r} from the same component pool{cluster_hint}")
                                .format(a=rule_effective_name(a), b=rule_effective_name(b),
                                        net=net, role=role, cluster_hint=cluster_hint)
                            )

    if problems:
        raise ValidationError(format_fatal_error(
            _("rules on the same net compete for the same component pool"),
            problems + [_("fix: give a distinguishing cluster: to the spokes of one of them, "
                          "so each rule takes components from its own pool — see Rule's "
                          "docstring (Cluster is the standard mechanism for splitting several "
                          "rules on one net); a rule redrawn alone (Redraw / --only) will "
                          "otherwise silently take components meant for its neighbour")]
        ))
    logger.debug(_("Candidate-pool collision checks passed"))


def check_clone_nets_exist_on_board(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Resolves the nets a clone's apply would USE and checks each against the real
    board nets (adapter.get_all_nets()):
      - via.net for EACH clone_placement (both spoke‑level and those nested in
        components[i].vias — see apply_clone_geometry);
      - (Phase 2 step 4.1) each cell ROLE's expected net — the net the by-nets
        resolution would use: the explicit clone.nets[role] override or the
        cell's net_template, resolved via params/net_overrides and then
        prefix-remapped for a literal /Channel_N/... net (derive_role_nets
        priority 2, TwinMap.twin_net semantics). Roles with NO explicit net
        source auto-derive from the live board (live_pad, steps 2.1/2.2) — those
        exist by construction and are not re-checked.

    Why separate from resolve_roles_by_nets: role‑to‑ref mapping already checks
    itself (candidates are searched among real pads, a non‑existent net simply
    yields no candidates — which is fatal). But via.net goes straight into
    ViaCommand without such checking — a typo in net_overrides or params that
    yields a syntactically valid string (e.g. "+3V3_DVD" instead of "+3V3_DVDD")
    would quietly create a via on the wrong net, with no fatal along the way.
    The role expected-net check catches the same class of typo in nets: /
    net_template / the prefix-remap input before apply even starts.

    via.net=None is not checked here — that is already fatal in clone_geometry.py
    (ClonePlacement has no default net), no need to duplicate. net_overrides
    participate exactly as in apply (manual overrides only).
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
                .format(name=clone_placement_effective_name(clone), where=where, net_name=via.net,
                        resolved=resolved, suggestion=suggestion)
            )

    def _check_role_net(expected_template, clone, where: str):
        """Check the role's EXPECTED net (the by-nets resolution target) exists
        on the board — same typo protection as the via.net check, for the role
        side (Phase 2 step 4.1). expected_template is the raw template
        (clone.nets[role] or slot.net_template)."""
        try:
            resolved = resolve_net(expected_template, clone.params, clone.net_overrides)
        except ValidationError:
            return  # missing parameter — already a fatal error higher up
        remapped = _prefix_remap_local_net(resolved, clone)
        final = remapped if remapped is not None else resolved
        if final not in real_nets:
            hint = difflib.get_close_matches(final, real_nets, n=1)
            suggestion = _(" — did you mean {suggestion!r}?").format(suggestion=hint[0]) if hint else ""
            problems.append(
                _("{name!r}, {where}: expected net resolves to {final!r}, "
                  "but that net does not exist on the board{suggestion}")
                .format(name=clone_placement_effective_name(clone), where=where,
                        final=final, suggestion=suggestion)
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
            # Phase 2 step 4.1 — the role's expected net (explicit override or
            # the cell's net_template), after params/net_overrides + prefix_remap.
            if slot.role in clone.nets:
                _check_role_net(clone.nets[slot.role], clone,
                                _("role {role!r} (explicit nets:)").format(role=slot.role))
            elif slot.net_template is not None:
                _check_role_net(slot.net_template, clone,
                                _("role {role!r} (cell net_template)").format(role=slot.role))

    if problems:
        raise ValidationError(format_fatal_error(
            _("clone references a non‑existent board net"),
            problems
        ))
    logger.debug(_("clone via.net checks against real board nets passed"))


def check_single_selection_based_clone(cfg: Config, adapter=None,
                                       sheet_names=None) -> None:
    """
    In KiCad only ONE selection is present at any moment — therefore you cannot
    process more than one ClonePlacement in "by selection" mode in a single run.
    If more than one, fatal with a hint to either retire the extras
    (retired: true) or run apply separately for each with --only NAME.

    Phase 2 step 2.3: when a live adapter is given, clone_uses_selection_mode is
    asked adaptively — an implicit clone (no nets/params/by_selection) whose
    cell auto-derives on the live board is BY-NETS and does NOT need a selection,
    so it is not counted here (two such clones can run together). Without an
    adapter the legacy pure default applies (implicit = by-selection) — the
    config-only caller's view.
    """
    sheet_names = sheet_names or {}
    selection_based = []
    for c in cfg.clone_placements:
        if c.retired:
            continue
        cell = cfg.cells.get(c.cell)
        if clone_uses_selection_mode(
                c, adapter=adapter, cell=cell, sheet_names=sheet_names):
            selection_based.append(clone_placement_effective_name(c))
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


def check_config_structure(cfg: Config, sheet_names=None) -> None:
    """The subset of pre‑validation that is about the config's own DEFINITIONS
    (identity, cycles) — pulled out of run_all_checks so ApplyPipeline can run
    it on the FULL config, before --only/--cluster narrow cfg.clone_placements
    down to the requested subset (see apply_pipeline.ApplyPipeline._filter_config).

    Why this matters: check_no_duplicate_clone_anchors compares clone_placements
    PAIRWISE — a duplicate identity between clone A (requested via --only) and
    clone B (not requested) is a real config defect (the registry would still
    confuse their vias/tracks whenever B is eventually applied too) regardless
    of which one this particular run happens to touch. Running these checks
    only on the --only-filtered cfg (the previous behaviour) made B invisible
    to the check whenever a run requested only A — found 2026-08-12, a
    genuine copy‑paste duplicate went unreported because the GUI run
    requested a single clone_placement by name.

    Deliberately EXCLUDES check_single_selection_based_clone: that check is
    about THIS RUN's selection‑mode clones, not the config's definitions —
    --only NAME is its own documented fix ("run apply separately for each
    using --only NAME"), which only works if the check runs AFTER --only
    narrows cfg. Running it here, pre‑filter, would defeat that fix and
    fatal on two selection‑based clones that were never meant to run
    together (found 2026-08-12, immediately after this function shipped).
    """
    _sn = sheet_names or {}
    check_clone_cells_exist(cfg)
    check_no_cell_definition_cycles(cfg)
    check_no_duplicate_clone_anchors(cfg)
    check_anchor_sheet_configured(cfg, sheet_names=_sn)
    check_no_candidate_pool_collisions(cfg)


def _fatal_title_line(e: ValidationError) -> str:
    """The 'FATAL ERROR: <title>' line of a formatted ValidationError, without
    the '=' box/borders — so one check's error can be embedded as a clean
    problem line inside another's consolidated fatal (2026-08-12, Group 2)."""
    text = str(e)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FATAL ERROR:"):
            return stripped[len("FATAL ERROR:"):].strip()
    return text


def check_coordinate_placements_exist(adapter: KiCadBoardAdapter, cfg: Config,
                                      sheet_names: dict[str, str] | None = None) -> None:
    """Pre-flight existence check for coordinate_placements (2026-08-12,
    Group 2 fix): each non-retired entry's Cluster+Role must resolve to EXACTLY
    ONE live footprint. Previously coordinate_placements validated lazily,
    INSIDE build_coordinate_moves (called from deep inside _dry_run/_execute),
    so a missing/ambiguous match surfaced mid-execution instead of the standard
    "Placement stopped, board not modified" pre-flight error every other
    placement kind gets. Like every other check in this module, ALL problems
    are COLLECTED and raised as ONE consolidated fatal (not one error per
    run — see this module's docstring)."""
    problems = []
    for cp in cfg.coordinate_placements:
        if cp.retired:
            continue
        label = coordinate_placement_effective_name(cp)
        try:
            resolve_footprint_by_cluster_role(adapter, cp.cluster, cp.role, label,
                                              sheet=cp.sheet, sheet_names=sheet_names)
        except ValidationError as e:
            problems.append(_("coordinate_placements {label!r}: {msg}")
                            .format(label=label, msg=_fatal_title_line(e)))
    if problems:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements cannot resolve their target component(s)"),
            problems
        ))


def _path_basename_stem(path: str) -> str:
    """Return the basename stem of a path string, treating BOTH '/' and '\\'
    as directory separators.

    pathlib.Path in OS-default mode only understands the separator of the
    current OS: on Linux (PosixPath) a Windows-style path with backslashes is
    treated as ONE filename, so Path(live).stem would yield the whole path
    instead of the board stem (the very failure this helper exists to avoid).
    Normalizing separators first keeps the comparison independent of the OS
    the interpreter runs on. The caller passes either get_board_filename()
    output or cfg.board_name, which may be a bare filename or a full path."""
    basename = path.replace("\\", "/").rsplit("/", 1)[-1]
    return Path(basename).stem


def check_board_identity(cfg: Config, adapter: KiCadBoardAdapter) -> None:
    """Opt-in "config targets board X, but board Y is open in KiCad" guard
    (2026-08-20). Fatal EARLY, before any other check, because a board mismatch
    makes every other check's result misleading — the real incident: a config
    whose schematic_dir pointed at a stale board revision while a different
    revision was open live surfaced as an unrelated-looking fatal deep in
    Extract ("anchor_sheet is used but sheet name dictionary is empty"), not as
    a clear "wrong board" message.

    Skipped (never fatal) when:
      - cfg.board_name is unset (the field is opt-in, old profiles don't get
        this protection); or
      - the adapter isn't connected yet (get_board_filename() -> None — a
        different check already covers "no board at all").

    Compares the BASENAME STEM case-insensitively, never the full path: the
    config and the live board live in unrelated directory trees, and paths
    differ across Denis's Windows/Linux machines (see Config.board_name)."""
    if cfg.board_name is None:
        return  # opt-in, skip if not declared
    live = adapter.get_board_filename()
    if live is None:
        return  # not connected yet — a different check already covers this
    live_stem = _path_basename_stem(live)
    expected_stem = _path_basename_stem(cfg.board_name)
    if live_stem.lower() != expected_stem.lower():
        raise ValidationError(format_fatal_error(
            _("connected board does not match this config"),
            [_("config expects board {expected!r}").format(expected=cfg.board_name),
             _("but KiCad currently has {live!r} open").format(live=live),
             _("open the right board in KiCad, or fix board_name: in the config")]
        ))


def run_all_checks(adapter: KiCadBoardAdapter, cfg: Config, sheet_names=None) -> None:
    """Runs all checks in order — from cheap to more comprehensive.

    Re‑runs check_config_structure on cfg (typically already --only/--cluster
    filtered by the caller) — cheap and idempotent, and keeps this the single
    entry point that fully validates a config on its own for callers that
    don't go through ApplyPipeline (tests, direct scripted use).
    check_single_selection_based_clone runs only here, on the (possibly
    filtered) cfg passed in — see check_config_structure's docstring for why
    it must not run on the unfiltered config."""
    _sn = sheet_names or {}
    logger.info(_("Running pre‑validation checks..."))
    # FIRST, before check_config_structure and everything else: if the board
    # open in KiCad is not the board this config targets, every other check
    # would validate against the WRONG board and report misleading results
    # (see check_board_identity's docstring for the real incident this guards).
    check_board_identity(cfg, adapter)
    check_config_structure(cfg, sheet_names=_sn)
    check_single_selection_based_clone(cfg, adapter=adapter, sheet_names=_sn)
    check_cells_and_pads_exist(adapter, cfg, sheet_names=_sn)
    check_role_pool_sufficiency(adapter, cfg)
    check_clone_nets_exist_on_board(adapter, cfg)
    check_coordinate_placements_exist(adapter, cfg, sheet_names=_sn)
    logger.info(_("All pre‑validation checks passed"))
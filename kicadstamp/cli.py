# kicadstamp/cli.py
"""Thin CLI command wrappers for the kicadstamp package.

Each wrapper owns the CLI-only concerns: reading an argparse.Namespace,
interactive input() prompts, connecting to KiCad. The business logic lives in
the library core (e.g. kicadstamp/cli_extract.py); invalid input is reported
by raising PlacerError, which the entry point (kicadstamp_cli.py) translates
into process exit codes. No sys.exit / print here.
"""

import logging
from pathlib import Path


# kipy-bearing imports (KiCadBoardAdapter, cli_extract, undo) are deliberately
# NOT at module level — each command imports them lazily inside its own body,
# so non-IPC commands like `flatten` never pay for kipy+protobuf+pynng at import.
from kicadstamp.constants import DEFAULT_LOG_DIR
from kicadstamp.exceptions import PlacerError
from kicadstamp.flatten import flatten_config
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)


def cmd_extract(args) -> None:
    """Extract a spoke cell from the current selection on the board.

    Thin CLI wrapper: turns argparse.Namespace (and an optional interactive
    prompt) into explicit arguments for
    kicadstamp.cli_extract.extract_template, raising PlacerError on invalid
    input (the entry point maps it to exit code 1).
    """
    from kicadstamp.kicad.adapter import KiCadBoardAdapter
    from kicadstamp.cli_extract import (load_profile, extract_template,
                                        EXTRACT_PROFILE_KNOWN_KEYS)
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    direct_args_given = bool(args.name or args.output or args.param or args.net_template
                             or args.net_template_role or args.rule_net
                             or args.origin_by_via_net or args.origin_by_component_role
                             or args.origin_by_component_pad or args.raw_selection)
    if args.profile and direct_args_given:
        raise PlacerError(_("[error] --profile cannot be combined with --name/--output/--param/--net-template/"
                            "--net-template-role/--rule-net/--raw-selection/--origin-by-*: either all from "
                            "profile or all as explicit flags, not mixed."))

    if args.profile:
        if not args.profiles:
            raise PlacerError(_("[error] --profile given without --profiles (profiles file)"))
        prof = load_profile(args.profiles, "extract_profiles", args.profile, root_defaults=["output"],
                            known_keys=EXTRACT_PROFILE_KNOWN_KEYS)
        if "output" not in prof:
            raise PlacerError(_("[error] profile {profile!r} missing required field {field!r}")
                              .format(profile=args.profile, field="output"))
        # name: defaults to the profile's own key — only set it explicitly when
        # the cell name must differ from the profile name (e.g. several
        # profiles feeding the same shared cell, like cap_pair_standard).
        name = prof.get("name", args.profile)
        output = prof["output"]
        params = dict(prof.get("params", {}) or {})
        net_template_map = dict(prof.get("net_template", {}) or {})
        net_template_role = dict(prof.get("net_template_role", {}) or {})
        rule_nets = set(prof.get("rule_nets", []) or [])
        origin_via_net = prof.get("origin_by_via_net")
        origin_component_role = prof.get("origin_by_component_role")
        origin_component_pad = prof.get("origin_by_component_pad")
        raw_selection = bool(prof.get("raw_selection", False))
        logger.info(_("Profile {profile!r} from {profiles}: name={name}, output={output}")
                    .format(profile=args.profile, profiles=args.profiles, name=name, output=output))
    else:
        name = args.name
        output = args.output
        if not name:
            try:
                name = input(_("Cell name (key under cells:): ")).strip()
            except EOFError:
                name = ""
        if not name or not output:
            raise PlacerError(_("[error] need --name and --output (or --profiles/--profile instead)"))
        params = {}
        for item in (args.param or []):
            if "=" not in item:
                raise PlacerError(_("--param {item!r} — need format KEY=VALUE").format(item=item))
            k, v = item.split("=", 1)
            params[k] = v

        net_template_map = {}
        for item in (args.net_template or []):
            if "=" not in item:
                raise PlacerError(_("--net-template {item!r} — need format LITERAL=PATTERN").format(item=item))
            literal, pattern = item.split("=", 1)
            net_template_map[literal] = pattern

        net_template_role = {}
        for item in (args.net_template_role or []):
            if "=" not in item:
                raise PlacerError(_("--net-template-role {item!r} — need format ROLE=LITERAL").format(item=item))
            role_key, literal = item.split("=", 1)
            net_template_role[role_key] = literal
        rule_nets = set(args.rule_net or [])
        origin_via_net = args.origin_by_via_net
        origin_component_role = args.origin_by_component_role
        origin_component_pad = args.origin_by_component_pad
        raw_selection = args.raw_selection

    extract_template(adapter, name=name, output=output, params=params,
                     net_template_map=net_template_map, net_template_role=net_template_role,
                     rule_nets=rule_nets,
                     origin_via_net=origin_via_net,
                     origin_component_role=origin_component_role,
                     origin_component_pad=origin_component_pad,
                     raw_selection=raw_selection)


def cmd_extract_net(args) -> None:
    """Extract one net's copper (tracks + vias) as a net_traces: record.

    Thin CLI wrapper for kicadstamp.net_trace_extract.extract_net_trace: the
    net is searched over the WHOLE live board (not the mouse selection) and the
    anchor footprint by anchor_role — the same resolve_footprint_by_role search
    Rule/ClonePlacement use. Raises PlacerError on invalid input (the entry
    point maps it to exit code 1). sheet_names stays empty (extract-net is a
    standalone command with no config), so --anchor-sheet narrowing requires
    the anchor_role to be unambiguous without it — use --anchor-cluster for a
    second narrowing axis that does not need a schematic_dir.
    """
    if not (args.net and args.anchor_role and args.output):
        raise PlacerError(_("[error] need --net, --anchor-role and --output"))
    from kicadstamp.kicad.adapter import KiCadBoardAdapter
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    from kicadstamp.net_trace_extract import (extract_net_trace, read_net_trace_flags,
                                              write_net_trace)
    # A re-extract refreshes the GEOMETRY but must not silently clear the
    # hand-set retired:/skip: of an already-saved record (review fix
    # 2026-08-21) — carry the existing flags into the new extraction.
    existing_retired, existing_skip = read_net_trace_flags(args.output, args.net)
    nt = extract_net_trace(
        adapter,
        net=args.net,
        anchor_role=args.anchor_role,
        anchor_sheet=args.anchor_sheet,
        anchor_cluster=args.anchor_cluster,
        anchor_pad=args.anchor_pad,
        sheet_names={},
        retired=existing_retired,
        skip=existing_skip,
    )
    write_net_trace(args.output, nt)
    logger.info(_("✅ Net trace for {net!r} (anchor role {role!r}): "
                  "{tracks} tracks, {vias} vias -> {output}")
                .format(net=nt.net, role=nt.anchor_role,
                        tracks=len(nt.tracks), vias=len(nt.vias), output=args.output))


def cmd_clone_extract(args) -> None:
    """Snapshot a channel to s-expr (file-based cloner, no IPC).

    Thin CLI wrapper: turns argparse.Namespace into explicit arguments for
    kicadstamp.cloner.extract.extract_channel, raising PlacerError on invalid
    input (the entry point maps it to exit code 1). The success summary is
    logged (INFO) instead of print()ed — the module owns no stdout writes.
    """
    from kicadstamp.cli_extract import load_profile, CLONE_EXTRACT_PROFILE_KNOWN_KEYS
    direct_given = bool(args.net or args.pcb or args.channel or args.output)
    if args.profile and direct_given:
        raise PlacerError(_("[error] --profile cannot be combined with --net/--pcb/--channel/--output"))

    if args.profile:
        if not args.profiles:
            raise PlacerError(_("[error] --profile given without --profiles (profiles file)"))
        prof = load_profile(args.profiles, "clone_profiles", args.profile,
                            known_keys=CLONE_EXTRACT_PROFILE_KNOWN_KEYS)
        for required in ("net", "pcb", "channel", "output"):
            if required not in prof:
                raise PlacerError(_("[error] profile {profile!r} missing required field {field!r}")
                                  .format(profile=args.profile, field=required))
        net_path, pcb_path, channel, output = prof["net"], prof["pcb"], prof["channel"], prof["output"]
    else:
        if not (args.net and args.pcb and args.channel and args.output):
            raise PlacerError(_("[error] need --net/--pcb/--channel/--output (or --profiles/--profile)"))
        net_path, pcb_path, channel, output = args.net, args.pcb, args.channel, args.output

    from kicadstamp.cloner.extract import extract_channel
    d = extract_channel(net_path, pcb_path, channel, output)
    s = d['summary']
    logger.info(_("[{channel}] footprints: {fp}, segments: {seg}, vias: {vias} -> {output}")
                .format(channel=channel, fp=s['footprints'], seg=s['segments'],
                        vias=s['vias'], output=output))


def cmd_clone_plan(args) -> None:
    """Generate a ready `clone_placements:` block for a channel clone (Phase 3
    step 3.1) — file-based cloner, no IPC. Reads net + pcb, auto-derives the
    role→net mapping via TwinMap + net_matching (SCC ambiguity = diagnostics,
    never a stop), and writes the block to YAML (or logs it).

    Thin CLI wrapper: turns argparse.Namespace into explicit arguments for
    kicadstamp.cloner.plan.plan_clone_placements, raising PlacerError on invalid
    input (the entry point maps it to exit code 1).
    """
    from kicadstamp.cloner.netlist import parse_netlist, build_twin_map
    from kicadstamp.cloner.pcb import PcbDocument
    from kicadstamp.cloner.plan import plan_clone_placements, clone_placements_to_dict
    if not (args.net and args.pcb and args.source and args.cell):
        raise PlacerError(_("[error] need --net/--pcb/--source/--cell (and --output to write the .sexp block)"))

    comps, local_by_ch, _global_nets = parse_netlist(args.net)
    twin = build_twin_map(comps, local_by_ch)
    doc = PcbDocument(args.pcb)

    targets = [t.strip() for t in (args.targets or "").split(",") if t.strip()] or None
    xy = None
    if args.xy:
        try:
            x_str, y_str = args.xy.split(",", 1)
            xy = (float(x_str), float(y_str))
        except ValueError:
            raise PlacerError(_("[error] --xy needs X,Y in mm (e.g. --xy 120.0,80.0)"))

    placements, diagnostics = plan_clone_placements(
        twin=twin, doc=doc, source_channel=args.source, cell=args.cell,
        cluster=args.cluster, xy=xy, anchor_role=args.anchor_role,
        anchor_sheet=args.anchor_sheet, target_channels=targets)
    for d in diagnostics:
        logger.warning(d)
    names = ", ".join(p.get("name") or p.get("cluster") or "?" for p in placements)

    if args.output:
        # The main config is s-expr (.sexp) — the generated block is serialized
        # with the same dict->s-expr converter the rest of the config uses.
        from kicadstamp.config.sexp_format import dict_to_sexp
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(dict_to_sexp(clone_placements_to_dict(placements)))
        logger.info(_("clone_placements for {channels} (cell {cell!r}) written: {output}")
                    .format(channels=names, cell=args.cell, output=args.output))
    else:
        logger.info(_("clone_placements for {channels} (cell {cell!r}) — rerun with --output to write the .sexp block")
                    .format(channels=names, cell=args.cell))


def cmd_channel_copy(args) -> list[str] | None:
    """Copy a whole channel's placement (variant B) via the live twin map.

    Thin CLI wrapper: turns argparse.Namespace into explicit arguments for
    kicadstamp.channel_copy.channel_copy, raising PlacerError on invalid
    input (the entry point maps it to exit code 1). Returns the dry-run report
    (list of lines) when --dry-run is set, None after a real run — the entry
    point prints the returned report, same as cmd_apply. A repeated --dst is
    copied in one run, one report section per channel.
    """
    from kicadstamp.kicad.adapter import KiCadBoardAdapter
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    def parse_pair(raw, flag):
        if not raw:
            return None
        try:
            x, y = (float(v) for v in raw.split(","))
        except (ValueError, TypeError):
            raise PlacerError(_("[error] {flag} must be X,Y (two comma-separated numbers)")
                              .format(flag=flag))
        return (x, y)

    offset = parse_pair(args.offset, "--offset") or (0.0, 0.0)
    target_dst = parse_pair(args.target_dst, "--target-dst")
    src_point = parse_pair(args.src_point, "--src-point")
    dst_point = parse_pair(args.dst_point, "--dst-point")

    from kicadstamp.channel_copy import channel_copy, format_channel_copy_report
    report: list[str] = []
    for dst in args.dst:
        plan = channel_copy(
            adapter,
            src=args.src, dst=dst,
            pivot=args.pivot, pivot_role=args.pivot_role, pivot_pad=args.pivot_pad,
            offset=offset, target_dst=target_dst,
            src_point=src_point, dst_point=dst_point,
            angle_deg=args.angle, mirror=args.mirror,
            include_global=args.include_global,
            dry_run=args.dry_run,
            check_collisions=not args.no_collision_check,
        )
        if args.dry_run:
            report.extend(format_channel_copy_report(plan))
    return report or None


def cmd_flatten(args) -> list[str] | None:
    """Consolidate a multi-file include: project into one self-contained file.

    Thin CLI wrapper for kicadstamp.flatten.flatten_config — a pure file
    operation (no IPC, no board access). Returns the report (list of lines)
    for the entry point to print, same shape as cmd_apply/cmd_channel_copy.
    """
    return flatten_config(root=args.root, output=args.output, dry_run=args.dry_run)


def cmd_undo(args, log_dir: str | None = None) -> None:
    """Undo the last operation.

    Thin CLI wrapper: finds the newest operation_*.json in the operation-log
    directory and undoes it via kicadstamp.undo.undo_last_operation. The log
    directory comes from, in priority order: the explicit log_dir argument,
    args.operation_log_dir, or DEFAULT_LOG_DIR — never a hard-coded CWD "logs"
    path. This is the reading side of П.7: the config's operation_log_dir
    (resolved relative to the config file, like registry_path/log_file) is
    where `apply` writes, so `undo` must be told the same directory instead of
    assuming CWD. Raises PlacerError when there is nothing to undo (the entry
    point maps it to exit code 1) — an error that used to silently exit 0.
    `args` is accepted for a uniform Namespace signature across cmd_* wrappers
    (--log-file is wired up by the entry point's setup_logging, not here).
    """
    resolved = log_dir or getattr(args, "operation_log_dir", None) or DEFAULT_LOG_DIR
    log_path = Path(resolved)
    if not log_path.exists():
        raise PlacerError(_("logs directory not found."))

    files = sorted(log_path.glob("operation_*.json"), key=lambda p: p.stat().st_ctime)
    if not files:
        raise PlacerError(_("No operation files to undo."))

    last_file = files[-1]
    logger.info(_("Undoing operation from {file}").format(file=last_file.name))
    from kicadstamp.undo import undo_last_operation
    success = undo_last_operation(last_file)
    if success:
        logger.info(_("✅ Operation successfully undone."))
    else:
        raise PlacerError(_("❌ Failed to undo operation."))

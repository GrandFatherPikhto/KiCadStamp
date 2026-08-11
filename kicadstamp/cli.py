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


from kicadstamp.cli_extract import (load_profile, extract_template,
                                    EXTRACT_PROFILE_KNOWN_KEYS,
                                    CLONE_EXTRACT_PROFILE_KNOWN_KEYS)
from kicadstamp.constants import DEFAULT_LOG_DIR
from kicadstamp.exceptions import PlacerError
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.i18n import _
from kicadstamp.undo import undo_last_operation

logger = logging.getLogger(__name__)


def cmd_extract(args) -> None:
    """Extract a spoke cell from the current selection on the board.

    Thin CLI wrapper: turns argparse.Namespace (and an optional interactive
    prompt) into explicit arguments for
    kicadstamp.cli_extract.extract_template, raising PlacerError on invalid
    input (the entry point maps it to exit code 1).
    """
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    direct_args_given = bool(args.name or args.output or args.param or args.net_template
                             or args.net_template_role or args.rule_net
                             or args.origin_by_via_net or args.origin_by_component_role
                             or args.origin_by_component_pad)
    if args.profile and direct_args_given:
        raise PlacerError(_("[error] --profile cannot be combined with --name/--output/--param/--net-template/"
                            "--net-template-role/--rule-net/--origin-by-*: either all from profile or all as "
                            "explicit flags, not mixed."))

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

    extract_template(adapter, name=name, output=output, params=params,
                     net_template_map=net_template_map, net_template_role=net_template_role,
                     rule_nets=rule_nets,
                     origin_via_net=origin_via_net,
                     origin_component_role=origin_component_role,
                     origin_component_pad=origin_component_pad)


def cmd_clone_extract(args) -> None:
    """Snapshot a channel to YAML (file-based cloner, no IPC).

    Thin CLI wrapper: turns argparse.Namespace into explicit arguments for
    kicadstamp.cloner.extract.extract_channel, raising PlacerError on invalid
    input (the entry point maps it to exit code 1). The success summary is
    logged (INFO) instead of print()ed — the module owns no stdout writes.
    """
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
    success = undo_last_operation(last_file)
    if success:
        logger.info(_("✅ Operation successfully undone."))
    else:
        raise PlacerError(_("❌ Failed to undo operation."))

# kicadstamp/cli_main.py
"""Package entry point for the KiCadStamp CLI (see pyproject.toml
[project.scripts]: ``kicadstamp = kicadstamp.cli_main:main``).

The repo-root script kicadstamp_cli.py is a thin wrapper that adds the repo
root to sys.path and calls this module's main(), so the ``python
kicadstamp_cli.py`` dev workflow keeps working unchanged.
"""

import argparse
import difflib
import sys

# Explicit i18n init (P1-1, 2026-08-25): kicadstamp/__init__.py no longer calls
# setup_i18n() at import. Entry points set up gettext BEFORE importing the
# modules that bind `_` at import time, so those bindings see the translated
# function.
from kicadstamp.i18n import setup_i18n

setup_i18n()

from kicadstamp import __version__
from kicadstamp.cli import (cmd_channel_copy, cmd_clone_extract, cmd_extract,
                            cmd_extract_net, cmd_flatten, cmd_undo)
from kicadstamp.cli_common import peek_log_file, run_cli
from kicadstamp.logging_setup import setup_logging
from kicadstamp.constants import DEFAULT_TIMEOUT_MS, DEFAULT_BATCH_SIZE
from kicadstamp.i18n import _


def cmd_apply(*args, **kwargs):
    """Lazy import wrapper for :func:`kicadstamp.apply_pipeline.cmd_apply`.

    ``apply`` is the only command whose import chain pulls
    ``kicadstamp.kicad.adapter`` (kipy + protobuf + pynng). Deferring that
    import to call time keeps the whole CLI entry point — and therefore
    non-IPC commands like ``flatten`` — from paying for kipy at import.
    """
    from kicadstamp.apply_pipeline import cmd_apply as _real_cmd_apply
    return _real_cmd_apply(*args, **kwargs)

# Translated/typographic text (em dashes, non-breaking hyphens, degree signs, ...)
# can't be encoded by legacy console codepages (e.g. Windows cp1251/cp866), which
# crashes the logging StreamHandler mid-run with UnicodeEncodeError.  UTF-8 can
# encode any codepoint, so this removes the crash regardless of the terminal;
# whether it also *displays* correctly still depends on the terminal itself.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# Real subcommands the CLI can dispatch to. Any other first argument (that is
# not a flag) is treated as a bare config path for 'apply' — see
# _rewrite_bare_config_to_apply().
_SUBCOMMANDS = ("apply", "undo", "extract", "extract-net", "clone-extract",
                "channel-copy", "flatten")


def _looks_like_misspelled_subcommand(token: str) -> bool:
    """True when `token` is lexically close to a real subcommand name but is
    probably not a config path (no path separators / config extension).

    Lets argparse produce its clean "invalid choice: 'aply' (choose from ...)"
    instead of the confusing "apply: error: unrecognized arguments" that the
    bare-config rewrite would otherwise cause for a misspelled subcommand.
    """
    if any(sep in token for sep in ("/", "\\")) or token.endswith((".yaml", ".yml")):
        return False
    return bool(difflib.get_close_matches(token, _SUBCOMMANDS, n=1, cutoff=0.6))


def _rewrite_bare_config_to_apply(argv) -> bool:
    """Bare-config shorthand: `kicadstamp_cli.py config.yaml` is the same as
    `kicadstamp_cli.py apply config.yaml`.

    An unknown first argument (not a known subcommand and not --version/-V) is
    rewritten to be the config path of the 'apply' subcommand — unless it looks
    like a misspelled subcommand (e.g. `aply`), in which case nothing is
    rewritten so argparse reports "invalid choice" with the real subcommand
    list. Returns True if a rewrite happened; main() then adds a hint on parse
    errors.
    """
    if len(argv) > 1 and argv[1] not in _SUBCOMMANDS and argv[1] not in ("--version", "-V"):
        if _looks_like_misspelled_subcommand(argv[1]):
            return False
        argv.insert(1, "apply")
        return True
    return False


def main() -> int:
    # --version/-V exempted from the bare-config-path -> 'apply' rewrite
    # below, same as the other real subcommands — otherwise it would be
    # silently rewritten to 'apply --version' and fail as an unknown apply
    # argument instead of printing the version.
    rewritten = _rewrite_bare_config_to_apply(sys.argv)

    parser = argparse.ArgumentParser(
        description=_("KiCad Decap Placer – capacitor placement (manual strategy)"),
        epilog=_("Example: kicadstamp_cli.py config.yaml --dry-run")
    )
    parser.add_argument("--version", "-V", action="version",
                        version=f"kicadstamp {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, help=_("Subcommand"))

    apply_parser = subparsers.add_parser("apply", help=_("Apply placement"))
    apply_parser.add_argument("config", help=_("YAML configuration file"))
    apply_parser.add_argument("--dry-run", action="store_true", help=_("Only print the plan, do not apply"))
    apply_parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help=_("IPC timeout in ms"))
    apply_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=_("Batch size for commits"))
    apply_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    apply_parser.add_argument("--log-file", help=_("File to save logs"))
    apply_parser.add_argument("--no-collision-check", action="store_true", help=_("Disable collision checking"))
    apply_parser.add_argument("--no-selection", action="store_true",
                              help=_("Ignore the current PCB editor selection for the whole run — "
                                     "role-based ClonePlacements (role: without nets:/params:) and "
                                     "ambiguity narrowing normally fall back to whatever is selected in "
                                     "KiCad; a stray leftover selection then either fatals or silently "
                                     "changes the resolved candidate. With this flag every such lookup "
                                     "behaves as if nothing were selected."))
    apply_parser.add_argument("--collision-margin", type=float, default=0.2, help=_("Extra clearance for collision check in mm"))
    apply_parser.add_argument("--only", action="append", metavar="NAME",
                              help=_("Process only rules/clone_placements/thermal_via_arrays/"
                                     "coordinate_placements with this identity (rule name if set, else "
                                     "its net; clone_placement/thermal_via_arrays entry name; "
                                     "coordinate_placements entry name, or its default cluster/role pair "
                                     "if name wasn't set). Repeatable and/or comma-separated "
                                     "(--only a,b --only c). Everything else is ignored in this run."))
    apply_parser.add_argument("--cluster", action="append", metavar="PATH",
                              help=_("Process only spokes/clone_placements/thermal_via_arrays/"
                                     "coordinate_placements entries whose Cluster (anchor_cluster / spoke "
                                     "cluster / coordinate_placements' own cluster) matches this path or "
                                     "prefix (segment-wise, e.g. 'Channel_0' also matches "
                                     "'Channel_0/DAC_OA'). Repeatable and/or comma-separated. "
                                     "Combines with --only via AND (run apply twice for OR)."))

    undo_parser = subparsers.add_parser("undo", help=_("Undo last operation"))
    undo_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    undo_parser.add_argument("--log-file", help=_("File to save logs"))
    undo_parser.add_argument("--operation-log-dir", metavar="DIR",
                             help=_("Directory with operation_*.json undo logs "
                                    "(default: logs/ next to the current working directory)"))

    clone_extract = subparsers.add_parser(
        "clone-extract",
        help=_("Snapshot a channel to YAML (file‑based cloner, no IPC)")
    )
    clone_extract.add_argument("--net", help=_("Path to .net file"))
    clone_extract.add_argument("--pcb", help=_("Path to .kicad_pcb file"))
    clone_extract.add_argument("--channel", help=_("Channel name, e.g. Channel_0"))
    clone_extract.add_argument("--output", help=_("YAML snapshot file"))
    clone_extract.add_argument("--profiles", metavar="FILE",
                               help=_("YAML file with named profiles for clone-extract"))
    clone_extract.add_argument("--profile", metavar="NAME",
                               help=_("Take net/pcb/channel/output from profile NAME in --profiles file "
                                      "(cannot combine with explicit flags)"))
    clone_extract.add_argument("-v", "--verbose", action="store_true", help=_("Verbose output"))

    extract_parser = subparsers.add_parser("extract", help=_("Extract spoke cell from current selection"))
    extract_parser.add_argument("--name", help=_("Cell name (key in cells:)"))
    extract_parser.add_argument("--output", help=_("Output YAML/JSON file"))
    extract_parser.add_argument("--profiles", metavar="FILE",
                                help=_("YAML file with named profiles for extract"))
    extract_parser.add_argument("--profile", metavar="NAME",
                                help=_("Take name/output/param/net-template/origin-by-* from profile NAME "
                                       "in --profiles file (cannot combine with explicit flags)"))
    extract_parser.add_argument("--timeout-ms", type=int, default=20000, help=_("IPC timeout in ms"))
    extract_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    extract_parser.add_argument("--log-file", help=_("File to save logs"))
    extract_parser.add_argument("--param", action="append", metavar="KEY=VALUE",
                                help=_("Parameter for --net-template round-trip verification (e.g. channel=1); "
                                       "can be repeated; not written to the cell, only round-trip check. "
                                       "OPTIONAL now — via/track nets resolve from roles (net_from_role) and "
                                       "channel patterns are auto-discovered, so params are only needed when "
                                       "you still override nets via --net-template"))
    extract_parser.add_argument("--net-template", action="append", metavar="LITERAL=PATTERN",
                                help=_("Mapping real net -> pattern with {placeholder} "
                                       "(e.g. 'DAC1_DB1=DAC{channel}_DB1'); can be repeated; "
                                       "fills net_template for roles and parametrizes via.net at extraction. "
                                       "OPTIONAL now — bridging roles auto-derive net_template and channel "
                                       "patterns are auto-discovered; kept as an explicit override"))
    extract_parser.add_argument("--net-template-role", action="append", metavar="ROLE=LITERAL",
                                help=_("For components with multiple nets from --net-template on pads "
                                       "(ferrite/inductor/fuse between two rails) – override WHICH net is "
                                       "the role's net_template (e.g. 'PI_FILTER_FB=+5V_DIRTY'). OPTIONAL now: "
                                       "extract auto-derives a designated net_template for bridging roles; "
                                       "this flag only changes the designated net. "
                                       "Fatal if the role does not actually have that net on its pads, "
                                       "or if the literal is not registered in --net-template/params."))
    extract_parser.add_argument("--rule-net", action="append", metavar="LITERAL",
                                help=_("Write this via/track net as null instead of its literal name "
                                       "(e.g. '+3V3') — at apply time a ManualSpoke-placed cell's via/"
                                       "track with net: null inherits the enclosing Rule's own net "
                                       "(spoke_layout.py's 'via.net or rule_net'), so this makes the "
                                       "cell reusable across Rules on different nets. Only needed for "
                                       "ManualSpoke-reused cells — net_from_role cells need none of this. "
                                       "Can be repeated. Fatal if the same net is also in --param/--net-template."))
    extract_parser.add_argument("--raw-selection", action="store_true",
                                help=_("Take the current selection as tracks/vias as-is, without the "
                                       "pad-connectivity filter (every selected track/via goes into the "
                                       "cell, no 'connected to a kept footprint's pad' check)"))
    origin_group = extract_parser.add_mutually_exclusive_group()
    origin_group.add_argument("--origin-by-via-net", metavar="NET",
                              help=_("Template origin — position of via on this net (instead of bbox); "
                                     "fatal if no such via in selection or more than one"))
    origin_group.add_argument("--origin-by-component-role", metavar="ROLE",
                              help=_("Template origin — position of component with this role "
                                     "(instead of bbox); fatal if role not found in selection"))
    extract_parser.add_argument("--origin-by-component-pad", metavar="PAD",
                                help=_("Refine --origin-by-component-role: origin is the position of "
                                       "the specific pad of that component, not its centre. "
                                       "Fatal without --origin-by-component-role."))

    extract_net_parser = subparsers.add_parser(
        "extract-net",
        help=_("Capture one net's copper (tracks + vias) as a net_traces: record, "
               "anchored to a Role-resolved footprint over the whole board")
    )
    extract_net_parser.add_argument("--net", required=True, metavar="NET",
                                    help=_("Network name to capture (e.g. DAC_DB0; local hierarchical "
                                           "nets keep their full '/Channel_0/...' form)"))
    extract_net_parser.add_argument("--anchor-role", required=True, metavar="ROLE",
                                    help=_("Role field of the anchor footprint (resolved over the whole "
                                           "board, same search Rule/ClonePlacement use)"))
    extract_net_parser.add_argument("--anchor-sheet", metavar="SHEET",
                                    help=_("Narrow the anchor_role search by sheet (needs schematic_dir "
                                           "in the target config at apply time; extract-net itself has no "
                                           "config, so prefer --anchor-cluster for disambiguation)"))
    extract_net_parser.add_argument("--anchor-cluster", metavar="CLUSTER",
                                    help=_("Narrow the anchor_role search by Cluster field (prefix match)"))
    extract_net_parser.add_argument("--anchor-pad", metavar="PAD",
                                    help=_("Anchor point = this pad's centre instead of the footprint centre"))
    extract_net_parser.add_argument("--output", required=True, help=_("Output YAML/JSON file"))
    extract_net_parser.add_argument("--timeout-ms", type=int, default=20000, help=_("IPC timeout in ms"))
    extract_net_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    extract_net_parser.add_argument("--log-file", help=_("File to save logs"))

    channel_copy_parser = subparsers.add_parser(
        "channel-copy",
        help=_("Copy a whole channel's placement (components + vias + tracks) "
               "from --src to --dst via a live twin map (variant B)")
    )
    channel_copy_parser.add_argument("--src", required=True,
                                     help=_("Source channel name, e.g. Channel_0"))
    channel_copy_parser.add_argument("--dst", action="append", required=True, metavar="CHANNEL",
                                     help=_("Destination channel name, e.g. Channel_1; "
                                            "repeatable to copy to several channels in one run"))
    pivot_group = channel_copy_parser.add_mutually_exclusive_group()
    pivot_group.add_argument("--pivot", metavar="REF",
                             help=_("Pivot component refdes on the source channel"))
    pivot_group.add_argument("--pivot-role", metavar="ROLE",
                             help=_("Pivot by Role field on the source channel (survives re-annotation)"))
    channel_copy_parser.add_argument("--pivot-pad", metavar="PAD",
                                     help=_("Anchor on this pad of the pivot instead of its centre"))
    channel_copy_parser.add_argument("--offset", metavar="DX,DY",
                                     help=_("Extra shift added to the pivot's destination position"))
    channel_copy_parser.add_argument("--target-dst", metavar="X,Y",
                                     help=_("Explicit destination anchor point (when the pivot "
                                            "twin is not placed yet)"))
    channel_copy_parser.add_argument("--src-point", metavar="X,Y",
                                     help=_("Points mode: source anchor point"))
    channel_copy_parser.add_argument("--dst-point", metavar="X,Y",
                                     help=_("Points mode: destination anchor point"))
    channel_copy_parser.add_argument("--angle", type=float, default=0.0,
                                     help=_("Rotation of the whole construction (degrees)"))
    channel_copy_parser.add_argument("--mirror", action="store_true",
                                     help=_("Mirror the whole construction (all layers inverted)"))
    channel_copy_parser.add_argument("--include-global", action="store_true",
                                     help=_("Also copy foreign (global-net) copper inside the source bbox"))
    channel_copy_parser.add_argument("--dry-run", action="store_true",
                                     help=_("Only print the plan, do not write to the board"))
    channel_copy_parser.add_argument("--no-collision-check", action="store_true",
                                     help=_("Disable collision checking"))
    channel_copy_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    channel_copy_parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                                     help=_("IPC timeout in ms"))
    channel_copy_parser.add_argument("--log-file", help=_("File to save logs"))

    flatten_parser = subparsers.add_parser(
        "flatten",
        help=_("Merge an include: project into one self-contained file")
    )
    flatten_parser.add_argument("--root", required=True, metavar="FILE",
                                help=_("Root config file to flatten (the whole include: graph "
                                       "is resolved from it)"))
    flatten_parser.add_argument("--output", metavar="FILE",
                                help=_("Output file. Default: overwrite the root file in place; "
                                       "an explicit path writes a NEW file and leaves the root "
                                       "untouched"))
    flatten_parser.add_argument("--dry-run", action="store_true",
                                help=_("Print the consolidation plan (sections and target path) "
                                       "without writing anything"))

    try:
        args = parser.parse_args()
    except SystemExit as e:
        if rewritten and e.code == 2:
            print(_("Note: the first argument was taken as a config path for 'apply' "
                    "(bare-config shorthand). If you meant a subcommand, spell it exactly: "
                    "apply, undo, extract, extract-net, clone-extract, channel-copy, flatten."),
                  file=sys.stderr)
        raise

    # Pick up log_file from the config before setup_logging() — but WITHOUT a
    # full validated load here. That happens exactly once, inside the apply
    # pipeline (run_apply), where errors surface properly through run_cli.
    # peek_log_file() reads only the root YAML's log_file key and never raises
    # (it logs a warning instead) — the old code ran a full load_config() here
    # and swallowed every exception: it wasted a parse+include-resolution on
    # the failure path and silently dropped why the config's log_file wasn't
    # honored.
    log_file = getattr(args, "log_file", None)
    if log_file is None and args.command == "apply":
        log_file = peek_log_file(args.config)

    listener = setup_logging(verbose=getattr(args, "verbose", False), log_file=log_file)

    def _dispatch() -> None:
        if args.command == "apply":
            report = cmd_apply(args)
            if report:
                print("\n".join(report))
        elif args.command == "undo":
            cmd_undo(args)
        elif args.command == "clone-extract":
            cmd_clone_extract(args)
        elif args.command == "extract":
            cmd_extract(args)
        elif args.command == "extract-net":
            cmd_extract_net(args)
        elif args.command == "channel-copy":
            report = cmd_channel_copy(args)
            if report:
                print("\n".join(report))
        elif args.command == "flatten":
            report = cmd_flatten(args)
            if report:
                print("\n".join(report))
        else:
            parser.print_help()
            sys.exit(1)

    # Exception → exit-code translation is delegated to cli_common.run_cli —
    # the single owner of exit codes, shared with author_cli.cli_main().
    # The QueueListener's thread must be stopped on EVERY exit path (normal
    # return AND exceptions) so buffered records aren't lost at the end of a
    # run — see techdocs/handoff/plan_2026_08_15_queue_based_logging.md.
    try:
        return run_cli(_dispatch)
    finally:
        listener.stop()


if __name__ == "__main__":
    sys.exit(main())

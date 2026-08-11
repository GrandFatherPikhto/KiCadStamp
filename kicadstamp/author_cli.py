# kicadstamp/author_cli.py
"""CLI front-end for kicadstamp/author.py — split out of author.py so the
author module stays a pure library (no argparse / sys.exit / CLI exit-code
concerns). See author.py's module docstring for the two library entry points
(dump_clone_placements/dump_rules/dump_template + apply_config); this module
owns only the `if __name__ == "__main__":` boilerplate for
boards/<board>/scripts/*.py generators: --apply/--dry-run parsing, writing the
built ClonePlacements via dump_clone_placements(), and (with --apply) loading
the root config and applying via author.apply_config(). Exception -> exit-code
translation is delegated to cli_common.run_cli — the single owner of exit
codes, shared with kicadstamp_cli.py.
"""
from collections.abc import Callable
import argparse
import sys
from pathlib import Path

from .author import apply_config, dump_clone_placements
from .cli_common import run_cli
from .config import ClonePlacement, load_config


def cli_main(build_fn: Callable[[], list[ClonePlacement]], output_path: str,
             root_config_path: str, *, description: str | None = None,
             argv: list[str] | None = None) -> None:
    """Standard `if __name__ == "__main__":` body for a
    boards/<board>/scripts/*.py generator: parses --apply/--dry-run, writes
    build_fn()'s ClonePlacements to output_path via dump_clone_placements(),
    and — only with --apply — loads root_config_path and applies via
    apply_config(). One place for this boilerplate, so every subsystem
    script behaves identically instead of each one copy-pasting its own
    argparse block (single source of truth for the apply-gating logic).

    root_config_path (NOT output_path) is what gets loaded/applied — it's
    the one that carries schematic_dir and (via include:) picks up
    output_path, and it's what registry identity is keyed off (see
    apply_config's own docstring) — it must be the SAME config every run.

    Without --apply (the default), this only ever writes output_path —
    never touches the live board. --dry-run only has an effect together
    with --apply (see apply_config).

    argv — for tests; None (default) reads sys.argv, like any CLI tool.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--apply", action="store_true",
                        help="also apply to the live board via kicadstamp.author.apply_config() "
                             "(connects to KiCad over IPC) after writing the YAML")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply, only print the plan, don't touch the board")
    parser.add_argument("--verbose", action="store_true",
                        help="DEBUG-level logging (default: INFO)")
    args = parser.parse_args(argv)

    # Local import: without this, nothing configures a logging handler when a board script
    # is run directly (as opposed to through kicadstamp_cli.py's own main()),
    # so every logger.info/debug — including the role-resolver's ambiguity
    # narrowing cascade, exactly what you need to see when a role fails to
    # resolve — is silently dropped instead of printed.
    from kicadstamp.logging_setup import setup_logging
    setup_logging(verbose=args.verbose)

    # Exception → exit-code translation is delegated to cli_common.run_cli
    # (the single owner of exit codes, shared with kicadstamp_cli.py). Without
    # this, a ValidationError/PlacerError from apply_config() (e.g. a role
    # ambiguity fatal — format_fatal_error's boxed message, already the
    # useful part) would propagate as a raw Python traceback instead, burying
    # the actual message under a wall of stack frames — found live debugging
    # dac_pi_filter.py's role-resolution ambiguity. Only a non-zero code is
    # turned into sys.exit() — a successful run returns normally, exactly as
    # before — so bare `cli_main(...)` calls in scripts still propagate
    # failure exit codes while tests can exercise the success path directly.
    def _run() -> None:
        clones = build_fn()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        dump_clone_placements(clones, output_path)
        print(f"wrote {len(clones)} clone_placements to {output_path}")

        if args.apply:
            cfg, ctx = load_config(root_config_path)
            report = apply_config(cfg, root_config_path, ctx=ctx, dry_run=args.dry_run)
            if report:
                print("\n".join(report))

    _code = run_cli(_run)
    if _code:
        sys.exit(_code)

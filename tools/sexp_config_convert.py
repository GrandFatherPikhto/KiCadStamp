#!/usr/bin/env python3
"""Bidirectional yaml <-> s-expr config converter (the parallel .sexp config
format, 2026-08-27).

Reads a kicadstamp config profile's raw dict (what yaml.safe_load returns)
and writes it in the OTHER format:

  yaml -> s-expr  reads  <file>.yaml, writes <file>.sexp (via dict_to_sexp)
  s-expr -> yaml  reads  <file>.sexp, writes <file>.yaml (via sexp_to_dict + yaml.dump)

The direction is inferred from the input file's extension, overridable with
--to-sexp / --to-yaml. Both directions:
  - back up an existing OUTPUT file before overwriting it (the input is never
    modified, so it needs no backup — the same backup-the-write-target
    convention as entity_delete.py::backup_file());
  - run a round-trip self-verify (the written output parsed back equals the
    original dict, default-stripped — the format omits default-valued fields)
    and only then report success, so a broken conversion never silently
    produces a corrupt profile.

Example:
  tools/sexp_config_convert.py profiles/3ch-awg-tia-v103/3ch-awg-tia.yaml
      # -> writes 3ch-awg-tia.sexp next to it
  tools/sexp_config_convert.py profiles/3ch-awg-tia-v103/3ch-awg-tia.sexp
      # -> writes 3ch-awg-tia.yaml back
  tools/sexp_config_convert.py --all-profiles
      # mass mode: generate a .sexp next to every profiles/**/*.yaml
      # (YAML is kept — parallel format, nothing migrates)

YAML remains the default config format; this is purely a convenience
translator for hand-off between formats.
"""
import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

from kicadstamp.config.aliases import normalize_section_aliases
from kicadstamp.config.sexp_format import _strip_defaults, dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.yaml_loader import safe_load


def _eq(a, b) -> bool:
    """Type-strict structural comparison — same as the round-trip tests."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return set(a) == set(b) and all(_eq(a[k], b[k]) for k in a)
    if isinstance(a, tuple):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return a == b


def _read_dict(path: Path) -> dict:
    if path.suffix.lower() == ".sexp":
        return sexp_to_dict(path.read_text(encoding="utf-8")) or {}
    # normalize_section_aliases: legacy `rules:` key -> `chains:` (2026-09-01
    # rename) so a YAML profile still carrying the old key converts to the
    # canonical `(chains ...)` sexp and the round-trip self-verify passes.
    return normalize_section_aliases(safe_load(path.read_text(encoding="utf-8")) or {})


def _write_dict(path: Path, data: dict) -> None:
    if path.suffix.lower() == ".sexp":
        path.write_text(dict_to_sexp(data), encoding="utf-8")
    else:
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False), encoding="utf-8")


def convert_file(path: Path, to_sexp: Optional[bool] = None) -> Path:
    """Convert one config file to the opposite format. Returns the written
    path. `to_sexp` overrides the extension-inferred direction; None means
    "read the extension". Backs up an existing OUTPUT file before overwriting
    it and self-verifies the output before returning (raises on a failed
    round-trip)."""
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")

    if to_sexp is None:
        to_sexp = path.suffix.lower() != ".sexp"

    out_suffix = ".sexp" if to_sexp else ".yaml"
    out_path = path.with_suffix(out_suffix)
    if out_path == path:
        raise ValueError(f"cannot convert {path} to itself (already {out_suffix})")

    data = _read_dict(path)

    # Back up whatever is about to be overwritten (out_path) — NOT the input,
    # which this function never modifies. Only when out_path already exists
    # (a fresh conversion has nothing to lose). Matches entity_delete.py's
    # backup_file() convention: back up the write target, unconditionally
    # relative to its own existence, before writing.
    if out_path.exists():
        bak_path = out_path.with_name(out_path.name + ".bak")
        shutil.copy2(out_path, bak_path)
    else:
        bak_path = None

    _write_dict(out_path, data)

    # self-verify: the freshly written file parses back to the same dict
    # (default-stripped canonical form — the s-expr format omits
    # default-valued fields; YAML writes what it's given verbatim).
    back = _read_dict(out_path)
    expected = _strip_defaults(data) if out_suffix == ".sexp" else data
    if not _eq(back, expected):
        raise ValueError(
            f"round-trip self-verify FAILED for {out_path} (input {path}) — "
            f"refusing to report success; .bak kept at {bak_path}")
    return out_path


def convert_all_profiles(root: Path) -> list[Path]:
    """Mass mode: write a .sexp next to every profiles/**/*.yaml (YAML kept).
    Returns the list of written paths."""
    written = []
    for yaml_path in sorted(root.glob("**/*.yaml")):
        out = yaml_path.with_suffix(".sexp")
        if out.exists():
            continue  # already converted — parallel format, don't clobber
        written.append(convert_file(yaml_path, to_sexp=True))
    return written


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bidirectional yaml <-> s-expr config converter "
                    "(parallel .sexp config format).")
    ap.add_argument("paths", nargs="*", help="config file(s) to convert")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--to-sexp", action="store_true",
                       help="force yaml -> s-expr direction")
    group.add_argument("--to-yaml", action="store_true",
                       help="force s-expr -> yaml direction")
    ap.add_argument("--all-profiles", action="store_true",
                    help="mass mode: generate a .sexp next to every "
                         "profiles/**/*.yaml (does not touch the YAML)")
    ap.add_argument("--profiles-root", default="profiles",
                    help="root to scan in --all-profiles mode")
    args = ap.parse_args()

    to_sexp = True if args.to_sexp else (False if args.to_yaml else None)

    try:
        written: list[Path] = []
        if args.all_profiles:
            written = convert_all_profiles(Path(args.profiles_root))
        else:
            if not args.paths:
                ap.error("provide input file(s), or use --all-profiles")
            for p in args.paths:
                written.append(convert_file(Path(p), to_sexp=to_sexp))
    except (OSError, ValidationError, ValueError) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    for p in written:
        print(f"written: {p}")
    print(f"{len(written)} file(s) converted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

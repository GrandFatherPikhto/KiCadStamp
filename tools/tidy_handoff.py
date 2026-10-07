#!/usr/bin/env python3
# tools/tidy_handoff.py
"""Recursively tidy a handoff-style documentation tree.

A recursive, settings-driven generalisation of
`techdocs/handoff/deepseek/categories.sh`: it walks ``root`` (default
``techdocs/``) and, in *every* directory it visits,

* moves files named ``<prefix>_<rest>.<ext>`` into a ``<prefix>/`` subdirectory
  of that same directory (``plan_2026_09_30_x.md`` ->
  ``plan/plan_2026_09_30_x.md``), creating the directory when it is missing;
* makes sure each category directory has an ``arch/`` subdirectory;
* moves category files older than ``archive_after_days`` days into that
  ``arch/`` (age from mtime, or from the ``YYYY_MM_DD`` in the file name when
  ``archive_date_source: filename``).

The prefix map, extensions, age source and age limit all live in
``tools/tidy_handoff.yaml`` (see ``--config``). The run is idempotent and never
overwrites an existing destination; use ``--dry-run`` to preview.

Usage:
    tools/tidy_handoff.py --dry-run -v
    tools/tidy_handoff.py
    tools/tidy_handoff.py --root techdocs/handoff/deepseek --days 14
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a project dependency
    sys.exit("PyYAML is required (pip install PyYAML)")


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).resolve().with_suffix(".yaml")
ARCH_DIR = "arch"
# A category token: starts with a letter, then letters/digits (plan, bug3, f1, i18n).
CATEGORY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_(.+)$")
# A date embedded in a file name, as the handoff docs write it.
DATE_RE = re.compile(r"(\d{4})[_-](\d{2})[_-](\d{2})")

DEFAULT_PREFIXES: Dict[str, str] = {
    "plan_": "plan",
    "handoff_": "handoff",
    "design_": "design",
    "done_": "done",
    "note_": "note",
    "prompt_": "prompt",
    "backlog_": "backlog",
    "diag_": "diag",
    "brainstorm_": "brainstorm",
    "step_": "step",
    "idea_": "idea",
}


@dataclass
class Settings:
    root: Path
    archive_after_days: Optional[int]
    archive_date_source: str
    extensions: Tuple[str, ...]
    prefixes: Dict[str, str]
    auto_prefixes: bool
    ignore_prefixes: Set[str]
    skip_hidden: bool
    dry_run: bool


@dataclass
class Report:
    moved_to_category: List[Tuple[Path, Path]] = field(default_factory=list)
    archived: List[Tuple[Path, Path]] = field(default_factory=list)
    created_dirs: List[Path] = field(default_factory=list)
    skipped: List[Tuple[Path, str]] = field(default_factory=list)


# ─────────────────────────────── settings ───────────────────────────────


def _normalise_prefixes(mapping: Dict[str, object]) -> Dict[str, str]:
    """Return {prefix-with-trailing-underscore: target-dir-name}."""
    out: Dict[str, str] = {}
    for key, value in mapping.items():
        prefix = str(key).strip()
        if not prefix.endswith("_"):
            prefix += "_"
        target = str(value).strip() if value else prefix[:-1]
        if prefix != "_" and target:
            out[prefix] = target
    return out


def load_settings(
    config_path: Path,
    *,
    cli_root: Optional[str],
    cli_days: Optional[int],
    cli_dry_run: bool,
) -> Settings:
    data: Dict[str, object] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise SystemExit(f"{config_path}: top level must be a mapping")
        data = loaded
    else:
        print(
            f"[warn] config {config_path} not found; using built-in defaults",
            file=sys.stderr,
        )

    # root: CLI paths resolve against CWD, config paths against the repo root.
    if cli_root is not None:
        root = Path(cli_root).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
    else:
        root = Path(str(data.get("root", "techdocs"))).expanduser()
        if not root.is_absolute():
            root = (REPO_ROOT / root).resolve()

    days: Optional[int] = data.get("archive_after_days", 30)  # type: ignore[assignment]
    if cli_days is not None:
        days = cli_days
    if days is not None:
        days = int(days)

    extensions = tuple(
        (e if str(e).startswith(".") else "." + str(e)).lower()
        for e in (data.get("extensions") or [".md"])
    )

    merged: Dict[str, object] = {**DEFAULT_PREFIXES, **(data.get("prefixes") or {})}  # type: ignore[arg-type]
    prefixes = _normalise_prefixes(merged)

    source = str(data.get("archive_date_source", "mtime")).lower()
    if source not in ("mtime", "filename"):
        raise SystemExit(
            f"archive_date_source must be 'mtime' or 'filename', got {source!r}"
        )

    return Settings(
        root=root,
        archive_after_days=days,
        archive_date_source=source,
        extensions=extensions,
        prefixes=prefixes,
        auto_prefixes=bool(data.get("auto_prefixes", True)),
        ignore_prefixes={str(p).strip().strip("_") for p in (data.get("ignore_prefixes") or [])},
        skip_hidden=bool(data.get("skip_hidden", True)),
        dry_run=bool(data.get("dry_run", False)) or cli_dry_run,
    )


# ───────────────────────────── filesystem ops ─────────────────────────────


def _is_pruned_dir(name: str, settings: Settings) -> bool:
    if name == ARCH_DIR:
        return True
    if settings.skip_hidden and name.startswith("."):
        return True
    return False


def _mkdir(path: Path, settings: Settings, report: Report) -> None:
    if path.exists() or path in report.created_dirs:
        return
    report.created_dirs.append(path)
    if not settings.dry_run:
        path.mkdir(parents=True, exist_ok=True)


def _move(src: Path, dst: Path, settings: Settings, report: Report) -> bool:
    if dst.exists():
        report.skipped.append((src, f"target already exists: {dst}"))
        return False
    if not settings.dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return True


def _has_extension(filename: str, extensions: Sequence[str]) -> bool:
    return Path(filename).suffix.lower() in extensions


def category_for(filename: str, settings: Settings) -> Optional[str]:
    """Target directory name for a file, or None when it is not a category file."""
    for prefix in sorted(settings.prefixes, key=len, reverse=True):
        if filename.startswith(prefix) and len(filename) > len(prefix):
            return settings.prefixes[prefix]
    if settings.auto_prefixes:
        match = CATEGORY_RE.match(filename)
        if match:
            token = match.group(1)
            if token not in settings.ignore_prefixes:
                return token
    return None


# ─────────────────────────────── passes ───────────────────────────────


def sort_pass(settings: Settings, report: Report) -> Set[Path]:
    """Move "<prefix>_*" files into per-prefix subdirs of their own directory."""
    category_dirs: Set[Path] = set()
    known_targets = set(settings.prefixes.values())

    for dirpath, dirnames, filenames in os.walk(settings.root, topdown=True):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not _is_pruned_dir(d, settings)]

        # Pre-existing category dirs must still get an arch/, even with no moves.
        for d in dirnames:
            if d in known_targets:
                category_dirs.add(here / d)

        for filename in filenames:
            if settings.skip_hidden and filename.startswith("."):
                continue
            if not _has_extension(filename, settings.extensions):
                continue
            target = category_for(filename, settings)
            if target is None:
                continue
            if here.name == target:
                continue  # already inside its own category directory
            src = here / filename
            if not src.is_file():
                continue
            target_dir = here / target
            dst = target_dir / filename
            if dst == src:
                continue
            if _move(src, dst, settings, report):
                report.moved_to_category.append((src, dst))
                category_dirs.add(target_dir)
            _mkdir(target_dir, settings, report)

    return category_dirs


def detect_category_dirs(root: Path, settings: Settings) -> Set[Path]:
    """Directories that look like a category dir: name is a category token and
    they hold at least one ``<name>_*`` file (or are a configured target)."""
    found: Set[Path] = set()
    known_targets = set(settings.prefixes.values())
    token_re = re.compile(r"^([A-Za-z][A-Za-z0-9]*)$")

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if not _is_pruned_dir(d, settings)]
        here = Path(dirpath)
        name = here.name
        if name in known_targets:
            found.add(here)
            continue
        match = token_re.match(name)
        if not match:
            continue
        prefix = name + "_"
        if any(f.startswith(prefix) and len(f) > len(prefix) for f in filenames):
            found.add(here)
    return found


def _age_days(path: Path, settings: Settings, now: float) -> Optional[float]:
    if settings.archive_date_source == "filename":
        match = DATE_RE.search(path.name)
        if match:
            try:
                embedded = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                embedded = None
            if embedded is not None:
                return float((date.today() - embedded).days)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return (now - mtime) / 86400.0


def archive_pass(category_dirs: Set[Path], settings: Settings, report: Report) -> None:
    """Create <category>/arch/ and move category files older than the limit in."""
    now = time.time()
    for cat in sorted(category_dirs):
        arch = cat / ARCH_DIR
        _mkdir(arch, settings, report)
        if settings.archive_after_days is None or not cat.is_dir():
            continue
        for entry in sorted(cat.iterdir()):
            if entry.name == ARCH_DIR or not entry.is_file():
                continue
            if settings.skip_hidden and entry.name.startswith("."):
                continue
            age = _age_days(entry, settings, now)
            if age is None or age <= settings.archive_after_days:
                continue
            if _move(entry, arch / entry.name, settings, report):
                report.archived.append((entry, arch / entry.name))


# ─────────────────────────────── report ───────────────────────────────


def print_report(settings: Settings, report: Report, verbose: bool) -> None:
    tag = "DRY-RUN " if settings.dry_run else ""
    if verbose:
        for src, dst in report.moved_to_category:
            print(f"{tag}sort   {src} -> {dst}")
        for src, dst in report.archived:
            print(f"{tag}arch   {src} -> {dst}")
    for path, why in report.skipped:
        print(f"{tag}skip   {path}: {why}")
    days = settings.archive_after_days
    print(
        f"{tag}root: {settings.root} | archive_after_days: {days} "
        f"({settings.archive_date_source}) | extensions: {', '.join(settings.extensions)}"
    )
    print(
        f"{tag}created dirs: {len(report.created_dirs)} | "
        f"sorted files: {len(report.moved_to_category)} | "
        f"archived: {len(report.archived)} | skipped: {len(report.skipped)}"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tidy_handoff.py",
        description="Recursively sort handoff docs into <prefix>/ dirs and archive old files.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="settings YAML (default: tools/tidy_handoff.yaml)",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="root to walk, overriding the config (relative to the current directory)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="override archive_after_days",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only report what would happen",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print every move")
    args = parser.parse_args(argv)

    settings = load_settings(
        args.config.expanduser(),
        cli_root=args.root,
        cli_days=args.days,
        cli_dry_run=args.dry_run,
    )

    if not settings.root.is_dir():
        print(f"root is not a directory: {settings.root}", file=sys.stderr)
        return 2

    report = Report()
    category_dirs = sort_pass(settings, report)
    category_dirs |= detect_category_dirs(settings.root, settings)
    archive_pass(category_dirs, settings, report)
    print_report(settings, report, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())

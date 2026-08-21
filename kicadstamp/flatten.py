# kicadstamp/flatten.py
"""
flatten.py — one-shot consolidation of a multi-file include: project into a
single self-contained YAML file (CLI `flatten`, see
techdocs/handoff/deepseek/plan_2026_08_21_flatten_and_single_file_gui.md).

Pure file operation with no IPC: reads the root file, resolves the whole
include: graph (config/includes.py's resolve_includes — the same first step
load_config() performs), and writes the fully merged content back out. The
`include:` key itself is dropped (it has been resolved), so the output has no
external dependencies left; every list section (rules:/clone_placements:/
thermal_via_arrays:/coordinate_placements:/net_traces:) is concatenated and
every dict section (cells:/points:/extract_profiles:/clone_profiles:/
sheet_templates:) is merged by key, exactly as load_config() would see them.

Deliberately NOT a full load_config(): flatten is a pure consolidation, and a
full validated load also builds the schematic sheet-name map (reads
*.kicad_sch files) which is irrelevant here and may simply not be available
on the machine where the consolidation is run. Name collisions across files
still cannot slip through: resolve_includes() fatals on a duplicate dict-
section key across files the same way load_config() does, so a config that
resolves here has no collisions left to re-handle by construction.

Never deletes anything: the old subsystem files stay exactly where they were
(removing them after verifying the merge is a deliberate manual step), and
--output to a different path never touches the root file.
"""
import datetime
import logging
from pathlib import Path
from typing import Any, List, Optional

import yaml

from kicadstamp.config.includes import (
    _DICT_SECTIONS,
    _LIST_SECTIONS,
    _load_yaml_file,
    resolve_includes,
    walk_include_tree,
)
from kicadstamp.i18n import _
from kicadstamp.utils.file_cache import cached_file_read, invalidate_path

logger = logging.getLogger(__name__)

# The union of every section include: can contribute, in resolve_includes()'s
# own order (list sections first, then dict sections) — read from
# config/includes.py, not duplicated here, so a section added there is
# automatically covered here too.
_SECTION_NAMES = (*_LIST_SECTIONS, *_DICT_SECTIONS)


def _entry_count(value: Any) -> int:
    """Number of entries in one section's merged value — len() works for both
    list sections (a list of entry dicts) and dict sections (name -> entry)."""
    return len(value) if isinstance(value, (list, dict)) else 0


def _count_files(root_path: Path) -> int:
    """Number of DISTINCT physical files in the include: graph rooted at
    `root_path` (the root plus every reachable include). walk_include_tree
    deliberately does not dedupe a diamond (the same file reached from two
    branches), so dedupe here via a visited set — a true cycle is already
    fatal inside walk_include_tree."""
    seen: set[Path] = set()

    def visit(node) -> None:
        seen.add(node.path)
        for child in node.children:
            if child.path not in seen:
                visit(child)

    visit(walk_include_tree(str(root_path)))
    return len(seen)


def flatten_config(root: str, output: Optional[str] = None,
                   dry_run: bool = False) -> List[str]:
    """Consolidate the include: project rooted at `root` into one file.

    `output` — where to write the merged YAML. None (the default) overwrites
    the root file in place; an explicit path writes a NEW file and leaves the
    root (and every included file) untouched. `dry_run` returns the report
    without writing anything. Returns the report lines (also returned for a
    real write — the CLI entry point prints them).

    Raises ValidationError on the same include-graph problems load_config()
    would raise here (missing include, cycle, duplicate dict-section key
    across files, section shape errors) — a config that resolves is safe to
    flatten with no extra collision handling.
    """
    root_path = Path(root).resolve()

    # Same first step load_config() performs: read the root YAML, then resolve
    # the whole include: graph into one merged dict.
    data = cached_file_read(root_path, _load_yaml_file)
    merged = resolve_includes(str(root_path), data)

    # Serialize the merged content. Empty sections (resolve_includes()
    # initializes every section, even empty ones) carry no information and
    # would only add noise — load_config() treats an absent section the same
    # as an empty one, so dropping them changes nothing semantically.
    section_names = set(_SECTION_NAMES)
    out: dict[str, Any] = {}
    for key, value in merged.items():
        if key in section_names:
            if value:
                out[key] = value
        else:
            out[key] = value

    file_count = _count_files(root_path)
    target = Path(output).resolve() if output else root_path

    report: List[str] = [
        _("Flatten {files} file(s) from {root}").format(files=file_count, root=root_path),
    ]
    for section in _SECTION_NAMES:
        if section in out:
            report.append("  {section}: {count}".format(
                section=section, count=_entry_count(out[section])))
    root_keys = sorted(k for k in out if k not in section_names)
    if root_keys:
        report.append(_("Root keys: {keys}").format(keys=", ".join(root_keys)))

    if dry_run:
        report.append(_("Would write to: {path}").format(path=target))
        return report

    # Memo comment at the top of the output file — English, like every other
    # comment in the project's generated YAML (file artifacts are not gettext
    # strings). The yaml.dump settings match config_writer._write_data().
    header = ("# flattened by kicadstamp flatten on {date} "
              "from {files} file(s)\n").format(
                  date=datetime.date.today().isoformat(), files=file_count)

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump(out, f, allow_unicode=True, sort_keys=False,
                  default_flow_style=False)
    invalidate_path(target)

    report.append(_("Written to: {path}").format(path=target))
    logger.info(_("flatten: wrote {path} ({files} files consolidated)")
                .format(path=target, files=file_count))
    return report

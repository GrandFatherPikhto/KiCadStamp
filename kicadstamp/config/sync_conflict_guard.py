# kicadstamp/config/sync_conflict_guard.py
"""Р-У3.5: refuse the on-disk format upgrade when Syncthing conflict files are
present.

``techdocs/`` and ``profiles/`` travel by SYNCTHING, not by git (rule 41 of
``techdocs/me/deepseek.md``). When two nodes write the same place, Syncthing does
not merge: it leaves a second copy next to the original, named
``<name>.sync-conflict-<date>-<node>.<ext>``. Such a file is a SIGNAL, not
version "b" — it means the profile was written from two sides at once, and the
owner resolves it by hand. The rule (В35) is therefore: while one is present, the
format upgrade of the profile does not run AT ALL, so not one config or registry
file is rewritten on top of a state two machines are still arguing about.

Two areas are scanned, and they are different on purpose:

1. **the profile directory** — ``Path(config_path).parent``, recursively, and
   ALWAYS. This is the directory whose files the sweep would rewrite, and it is
   always known (the config path was handed to us);
2. **the KiCad project directory** — ``Path(root_sheet).parent``, recursively,
   where ``root_sheet`` is read from the ROOT config and resolved against the
   config's own directory through
   :func:`kicadstamp.utils.paths.resolve_config_relative_path` (rule 41: the path
   is asked of the profile, never hard-coded). Four live profiles have NO
   ``root_sheet`` at all, and the resolved directory may simply not exist — in
   BOTH cases the KiCad half is SKIPPED, not an error: only the profile half is
   mandatory. A config that cannot be parsed at all also skips the KiCad half
   (the lift will fatal on that file by itself, with its own message).

The refusal is a FATAL (``ValidationError`` built by ``format_fatal_error``),
like every other refusal of the format machinery, and it NAMES every conflict
file it found — a refusal that does not say WHAT it found would send the owner
hunting. Every user-facing line goes through ``_()`` (plan §7 Р-У3.5: "громко,
со списком").
"""
from __future__ import annotations

from pathlib import Path

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from ..utils.paths import resolve_config_relative_path

# The marker Syncthing puts into the name of the copy it could not merge.
SYNC_CONFLICT_MARKER = "sync-conflict"


def _conflicts_under(directory: Path) -> list[Path]:
    """Every FILE whose name carries the marker, anywhere under `directory`."""
    if not directory.is_dir():
        return []
    return [p for p in directory.rglob(f"*{SYNC_CONFLICT_MARKER}*") if p.is_file()]


def _kicad_project_dir(config_path: Path) -> Path | None:
    """``Path(root_sheet).parent`` — the KiCad project directory of this profile,
    or None when there is no ``root_sheet`` (or the config cannot be parsed)."""
    from .format_version import parse_raw_file

    try:
        data, _version = parse_raw_file(config_path)
    except (OSError, ValueError, ValidationError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("root_sheet")
    if not raw:
        return None
    resolved = resolve_config_relative_path(config_path.parent, str(raw))
    return Path(resolved).parent


def sync_conflict_files(config_path: str | Path) -> list[Path]:
    """Every ``*.sync-conflict-*`` file the upgrade of this profile must refuse
    on: the profile directory (recursive, always) plus the KiCad project
    directory (recursive, when ``root_sheet`` resolves). Deduped and sorted by
    path, so the fatal's list is stable and a diamond cannot name one file
    twice."""
    root = Path(config_path)
    found: dict[str, Path] = {}
    for path in _conflicts_under(root.parent):
        found[str(path.resolve())] = path
    project = _kicad_project_dir(root)
    if project is not None and project.is_dir():
        for path in _conflicts_under(project):
            found[str(path.resolve())] = path
    return [found[key] for key in sorted(found)]


def refuse_on_sync_conflicts(config_path: str | Path) -> None:
    """Raise a FATAL naming every Syncthing conflict file, or return quietly.

    Called by the on-disk sweep BEFORE its first write (its sibling
    ``upgrade_registries_on_disk`` runs in the same ``load_config``, AFTER it, so
    raising here protects the WHOLE lift — config graph and registries alike:
    neither one writes a byte)."""
    conflicts = sync_conflict_files(config_path)
    if not conflicts:
        return
    raise ValidationError(format_fatal_error(
        _("the format upgrade of {path} is refused: Syncthing conflict files are present").format(
            path=str(config_path)),
        [_("conflict file: {file}").format(file=str(f)) for f in conflicts]
        + [_("resolve these conflicts first — they are NOT merged or deleted automatically")]))

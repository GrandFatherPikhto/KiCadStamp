# gui/include_recovery.py
"""GUI recovery for a missing `include:` file (2026-09-12, plan
techdocs/handoff/deepseek/plan/plan_2026_09_12_three_old_tails.md, Э1.2).

The loader's fatal for a missing include STAYS. A fabricated include is a real
breakage — a typo in the name, or a profile that was not copied whole — and
silently skipping it would hide the problem; the whole config format is built
on loud errors. What this module adds is a chance to REPAIR the graph when the
GUI opens the project, instead of the user having to read the formatted fatal
and hand-edit the root .sexp.

Three outcomes, all decided here:
  * CREATE — write an empty config at the missing path. This is exactly the
    state ensure_scheme_list_storage() creates on first write, and the right
    answer for "I deleted scheme_lists.sexp". For that one name it is also the
    dialog's DEFAULT action: that include line was written by us.
  * REMOVE — drop every `include:` entry pointing at the missing path from the
    file that carries it (its path comes from the exception) and reload.
  * CANCEL — touch nothing; the ordinary fatal path surfaces as before.

Every repair here is PHYSICAL (a direct write plus both cache invalidations),
deliberately NOT routed through config_writer's staged write helpers:
DockHub._on_root_changed_for_working_set() CLEARS the working set on the very
root change this recovery runs under, so a staged repair would be wiped before
the docks re-read the graph — and a staged CREATE could not satisfy the
loader's own `include_path.exists()` gate anyway, since it never touches disk.

The reload retry is EXACTLY ONE: if the second walk fails — the same error on
another file, or any other error — it propagates as an ordinary fatal, never as
another recovery dialog. There is no "fix, fail, fix again" loop.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QMessageBox

from kicadstamp.config.includes import walk_include_tree
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import MissingIncludeError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.utils.file_cache import invalidate_graph_path, invalidate_path

logger = logging.getLogger(__name__)

# The three outcomes recover_missing_include() distinguishes. String constants
# (not an Enum) so the injectable `ask` callback used by tests stays trivial.
CREATE = "create"
REMOVE = "remove"
CANCEL = "cancel"


def walk_with_recovery(parent, root_path):
    """walk_include_tree(root_path), offering ONE repair round when the walk
    raises MissingIncludeError.

    Returns the include tree. Re-raises when the user cancels, or when the
    retry after a repair fails (the caller's usual broken-config handling then
    takes over). `parent` is the QWidget the dialog is modal to."""
    try:
        return walk_include_tree(str(root_path))
    except MissingIncludeError as error:
        if not recover_missing_include(parent, error):
            raise
        # Exactly one retry — a second failure propagates untouched.
        return walk_include_tree(str(root_path))


def recover_missing_include(parent, error: MissingIncludeError, *, ask=None) -> bool:
    """Offer to repair `error`; return True only when the graph was actually
    repaired (the caller should then retry the load once), False when nothing
    was changed (Cancel, or a repair that could not be performed).

    `ask` is the choice callback — (parent, error) -> CREATE/REMOVE/CANCEL —
    injectable so tests can exercise all three outcomes without a live modal;
    the default builds the real three-button dialog."""
    if not isinstance(error, MissingIncludeError):  # never guess on any other error
        return False
    choice = (ask or _ask_choice)(parent, error)
    if choice == CREATE:
        return _create_empty(error)
    if choice == REMOVE:
        return _remove_include_line(error)
    return False


def _scheme_list_storage_names() -> tuple:
    """SCHEME_LIST_STORAGE_NAME / LEGACY_SCHEME_LIST_STORAGE_NAME, imported
    lazily: gui.docks.scheme_list imports PyQt and much of the config graph,
    and this module is imported from a dock's own module top — a module-level
    import here would be a cycle."""
    from .docks.scheme_list import (LEGACY_SCHEME_LIST_STORAGE_NAME,
                                    SCHEME_LIST_STORAGE_NAME)
    return SCHEME_LIST_STORAGE_NAME, LEGACY_SCHEME_LIST_STORAGE_NAME


def _default_choice(include_entry: str) -> str:
    """The dialog's default action for a given include name. 'Create empty
    file' is the default ONLY for the Scheme List side file — that include was
    written by US (see ensure_scheme_list_storage), and an empty file is
    equivalent to "no records yet". For any other name the safe default is
    Cancel: the name is far more likely a typo than a file the user wants
    invented. Pure and separate so the rule is unit-testable without a modal."""
    if Path(include_entry).name in _scheme_list_storage_names():
        return CREATE
    return CANCEL


def _ask_choice(parent, error: MissingIncludeError) -> str:
    """The real three-button dialog. See _default_choice for the default."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(_("Missing include file"))
    box.setText(_("The profile includes a file that does not exist: {name}")
                .format(name=error.include_entry))
    box.setInformativeText(
        _("Expected at {path} (referenced from {source})")
        .format(path=error.missing_path, source=error.source_path))
    create = box.addButton(_("Create empty file"), QMessageBox.ButtonRole.AcceptRole)
    remove = box.addButton(_("Remove from include:"), QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(_("Cancel"), QMessageBox.ButtonRole.RejectRole)
    if _default_choice(error.include_entry) == CREATE:
        box.setDefaultButton(create)
    else:
        box.setDefaultButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is create:
        return CREATE
    if clicked is remove:
        return REMOVE
    return CANCEL


def _create_empty(error: MissingIncludeError) -> bool:
    """Write dict_to_sexp({}) at the missing path — the exact empty config a
    fresh include file starts as (the same text ensure_scheme_list_storage
    writes). Creates no directories: a missing PARENT directory is far more
    likely a wrong path than a directory worth inventing, so the write fails
    and the outcome collapses to Cancel."""
    path = error.missing_path
    try:
        path.write_text(dict_to_sexp({}), encoding="utf-8")
    except OSError as e:
        logger.warning("include recovery: could not create %s: %s", path, e)
        return False
    invalidate_path(path)
    invalidate_graph_path(path)
    logger.info("include recovery: created empty %s", path)
    return True


def _read_physical(path: Path) -> Optional[dict]:
    """Read an existing config file's raw dict straight from disk, ignoring
    the working set (see the module docstring). Only .sexp/.json — the two
    formats config/includes._load_config_file accepts, so the source file of a
    missing include is always one of them. None on a read/parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("include recovery: could not read %s: %s", path, e)
        return None
    try:
        if path.suffix.lower() == ".json":
            return json.loads(text) or {}
        if path.suffix.lower() == ".sexp":
            return sexp_to_dict(text) or {}
    except (ValueError, ValidationError) as e:
        logger.warning("include recovery: could not parse %s: %s", path, e)
        return None
    return None


def _entry_target(entry, base_dir: Path) -> Optional[Path]:
    """Resolved path an include: entry points at, or None for a malformed one
    — the same resolution rule as config/includes._parse_include_entry."""
    entry_str = entry if isinstance(entry, str) else (entry or {}).get("path")
    return (base_dir / entry_str).resolve() if entry_str else None


def _write_physical(path: Path, data: dict) -> None:
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(dict_to_sexp(data), encoding="utf-8")


def _remove_include_line(error: MissingIncludeError) -> bool:
    """Physically drop every include: entry pointing at the missing path from
    the file that carries it, then invalidate both caches so the retry sees the
    new content immediately. False when the source cannot be read or no
    matching entry is found (nothing to repair)."""
    source = error.source_path
    data = _read_physical(source)
    if data is None:
        return False
    items = data.get("include")
    if not isinstance(items, list):
        return False
    base_dir = source.parent
    kept = [entry for entry in items
            if _entry_target(entry, base_dir) != error.missing_path]
    if len(kept) == len(items):
        return False
    if kept:
        data["include"] = kept
    else:
        data.pop("include", None)
    try:
        _write_physical(source, data)
    except OSError as e:
        logger.warning("include recovery: could not rewrite %s: %s", source, e)
        return False
    invalidate_path(source)
    invalidate_graph_path(source)
    logger.info("include recovery: dropped include %r from %s",
                error.include_entry, source)
    return True

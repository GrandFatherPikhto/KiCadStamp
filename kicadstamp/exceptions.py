# kicadstamp/exceptions.py

import difflib
from kicadstamp.i18n import _

class PlacerError(Exception):
    """Base exception for all placer errors."""
    pass

class BoardNotFoundError(PlacerError):
    """Failed to obtain board from KiCad."""
    pass

class ComponentNotFoundError(PlacerError):
    """Component not found on the board."""
    pass

class GeometryError(PlacerError):
    """Geometry calculation error."""
    pass

class ValidationError(PlacerError):
    """
    Fatal pre‑validation error — detected BEFORE planning/moves,
    program stops without modifying the board.
    """
    pass


class FieldsToolError(Exception):
    """
    Fatal error from the schematic-editing tools (schematic_set_fields.py/
    schematic_rename_fields.py, fieldstool_cli.py, gui/fieldstool_window.py)
    — deliberately NOT a PlacerError subclass: a different risk domain
    (.kicad_sch text splicing, no board/KiCad-IPC involved at all), so
    catching PlacerError must never accidentally swallow one of these too.
    """
    pass


def yaml_removed_config_error(path) -> "ValidationError":
    """ValidationError for a `.yaml`/`.yml` config file fed to a CORE config
    function: YAML support was removed from the config graph (2026-08-28,
    core_yaml_removal) — the project's config format is s-expr (.sexp) or
    .json, and a legacy .yaml must be converted first. Shared by
    config_writer (which wraps this in OSError for the GUI docks' `except
    OSError` contract) and config/includes._load_config_file (which raises it
    directly, matching load_config's ValidationError contract)."""
    return ValidationError(format_fatal_error(
        _("{path}: YAML config support has been removed — convert this file "
          "with `tools/sexp_config_convert.py` first").format(path=path),
        [_("the project's config format is s-expr (.sexp) or .json")]))


def unknown_extension_config_error(path, suffix: str) -> "ValidationError":
    """ValidationError for a config file whose extension is neither a
    supported config format (.sexp/.json) nor a legacy .yaml/.yml — e.g. a
    typo (`.sepx`) or a file with no extension at all. Same sharing story as
    yaml_removed_config_error."""
    return ValidationError(format_fatal_error(
        _("{path}: unrecognized config file extension {suffix!r}")
        .format(path=path, suffix=suffix),
        [_("use .sexp (the project's main format) or .json")]))


def format_fatal_error(title: str, problems: list) -> str:
    """
    Common fatal error formatter – used both in config.py (checks at YAML load)
    and validation.py (checks after connecting to KiCad). Lives here to avoid
    circular imports (validation.py imports config.py).
    """
    lines = [
        "",
        "=" * 70,
        _("  FATAL ERROR: {title}").format(title=title),
        "=" * 70,
    ]
    for p in problems:
        lines.append(f"  ✗ {p}")
    lines.append("=" * 70)
    lines.append(_("Placement stopped, board not modified. Fix the config and run again."))
    lines.append("")
    return "\n".join(lines)


def check_unknown_keys(data: dict, known_keys: set, title: str, extra_hint: str = "") -> None:
    """
    Fatal if data has keys outside known_keys. Every YAML block in this
    project (clone_placements, rules, extract_profiles, clone_profiles) is
    read field-by-field via dict.get() — a typo'd or wrong-separator key
    (e.g. 'origin-by-via-net' instead of 'origin_by_via_net') is otherwise
    silently ignored, no error at all: a real class of bug hit live on
    boards/3ch-awg-tia (dash in anchor_sheet/origin_by_via_net). Suggests the
    closest known key via difflib, same UX as _CLONE_PLACEMENT_KNOWN_KEYS's
    original check (config/loader.py) this generalises from.

    extra_hint — optional extra parenthetical appended after "quiet bugs"
    and before the colon, e.g. " (e.g. 'pad' won't work; use 'anchor_pad')".
    """
    unknown = set(data.keys()) - known_keys
    if not unknown:
        return
    problems = []
    for key in sorted(unknown):
        close = difflib.get_close_matches(key, known_keys, n=1)
        if not close:
            close = [k for k in sorted(known_keys) if key in k or k in key]
        hint = _(" — did you mean {suggestion!r}?").format(suggestion=close[0]) if close else ""
        problems.append(f"{key!r}{hint}")
    raise ValidationError(format_fatal_error(
        title,
        [_("unrecognised keys are silently ignored – common source of quiet bugs{extra}: {problems}")
         .format(extra=extra_hint, problems=', '.join(problems))]
    ))
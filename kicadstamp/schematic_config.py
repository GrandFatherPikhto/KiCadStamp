# kicadstamp/schematic_config.py
"""
Shared loader for the fieldstool YAML config files (root_sheet + one
section). Both schematic_set_fields.py (fields:) and
schematic_rename_fields.py (renames:) used to carry a near-identical copy
of this logic — extracted here 2026-08-02 so the "what is a valid config"
rule lives in exactly one place.

Error messages deliberately stay generic ("root_sheet",
"config's {section}: is empty (or missing)") — they are the contract the
CLI tests assert on via pytest.raises(match=...), and the section name in
the message doubles as the caller's own section label.
"""
from pathlib import Path

from .exceptions import (
    FieldsToolError,
    unknown_extension_config_error,
    yaml_removed_config_error,
)


def load_fields_config(path: Path, section: str) -> tuple[str, dict[str, dict[str, str]]]:
    """(root_sheet, section_entries) from a config at `path` — s-expr ONLY
    (2026-08-28, yaml_removal_tooling: fieldstool configs migrate to .sexp
    too, closing the open question left by the CORE plan). Raises
    FieldsToolError if the file has no root_sheet or no non-empty `section`
    (the two fatal conditions both callers recognize), or if the extension
    is unsupported — .yaml/.yml gets the "YAML removed" fatal, anything else
    the unrecognized-extension fatal (same helpers config_writer uses),
    wrapped in FieldsToolError so this function's existing contract for its
    callers (schematic_set_fields/schematic_rename_fields/config_rename) is
    unchanged.

    sexp_to_dict is imported here (function-level), not at module top, to
    avoid a circular import (sexp_format.py imports _LIST_SECTIONS/
    _DICT_SECTIONS from config/includes.py at its own module level) — the
    same reason config/includes.py::_load_config_file does it."""
    with open(path, encoding='utf-8') as f:
        suffix = path.suffix.lower()
        if suffix == '.sexp':
            from .config.sexp_format import sexp_to_dict
            data = sexp_to_dict(f.read()) or {}
        elif suffix in ('.yaml', '.yml'):
            err = yaml_removed_config_error(path)
            raise FieldsToolError(str(err)) from err
        else:
            err = unknown_extension_config_error(path, suffix)
            raise FieldsToolError(str(err)) from err
    root_sheet = data.get('root_sheet')
    if not root_sheet:
        raise FieldsToolError("config has no root_sheet")
    entries = data.get(section) or {}
    if not entries:
        raise FieldsToolError(f"config's {section}: is empty (or missing)")
    return root_sheet, entries

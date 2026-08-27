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

from .exceptions import FieldsToolError
from .utils.yaml_loader import safe_load


def load_fields_config(path: Path, section: str) -> tuple[str, dict[str, dict[str, str]]]:
    """(root_sheet, section_entries) from a config at `path` (YAML by default,
    or the parallel .sexp format by extension — 2026-08-27); raises
    FieldsToolError if the file has no root_sheet or no non-empty `section`
    (the two fatal conditions both callers recognize).

    sexp_to_dict is imported here (function-level), not at module top, to
    avoid a circular import (sexp_format.py imports _LIST_SECTIONS/
    _DICT_SECTIONS from config/includes.py at its own module level) — the
    same reason config/includes.py::_load_config_file does it."""
    with open(path, encoding='utf-8') as f:
        if path.suffix.lower() == '.sexp':
            from .config.sexp_format import sexp_to_dict
            data = sexp_to_dict(f.read()) or {}
        else:
            data = safe_load(f) or {}
    root_sheet = data.get('root_sheet')
    if not root_sheet:
        raise FieldsToolError("config has no root_sheet")
    entries = data.get(section) or {}
    if not entries:
        raise FieldsToolError(f"config's {section}: is empty (or missing)")
    return root_sheet, entries

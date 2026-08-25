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
    """(root_sheet, section_entries) from a YAML config at `path`; raises
    FieldsToolError if the file has no root_sheet or no non-empty `section`
    (the two fatal conditions both callers recognize)."""
    with open(path, encoding='utf-8') as f:
        data = safe_load(f) or {}
    root_sheet = data.get('root_sheet')
    if not root_sheet:
        raise FieldsToolError("config has no root_sheet")
    entries = data.get(section) or {}
    if not entries:
        raise FieldsToolError(f"config's {section}: is empty (or missing)")
    return root_sheet, entries

# kicadstamp/config/aliases.py
"""Legacy-key read aliases kept for backward compatibility.

Two renames are covered here, both with the same discipline ("read both
spellings, write the new one, never a silent merge"):

- 2026-09-01 `rules:` -> `chains:` (Rule -> Chain rename,
  plan_2026_09_01_rules_to_chains.md);
- 2026-09-20 `scheme_lists:` -> `imprints:` (Scheme List -> Imprint rename,
  plan_2026_09_18_scheme_list_to_cell_and_capture.md, D7).

`normalize_section_aliases()` renames legacy SECTION keys to their canonical
form in a RAW parsed config dict, BEFORE any section processing (list/dict
checks in includes.py, dataclass loading in loader.py, sexp conversion). It is
called by every raw-dict reader so the whole pipeline only ever sees `chains:`
/ `imprints:`:

- config/includes.py::_load_config_file (load_config + walk_include_tree)
- config_writer.py::_read_data (GUI dock write paths)
- gui/config_io.py::load_data (read-only browsing)

`normalize_entity_aliases()` does the same one level down for an ENTITY
record's own key: the Imprint rename also renamed the reference
`scheme_list: <name>` to `imprint: <name>`, and that is a record field, not a
section — see config/entries.py::_load_entity and the s-expr record parser,
which map it before the "unknown key" check.

Fatal (never a silent merge) when a dict carries BOTH the legacy key and the
canonical one — that is ambiguous, same reasoning as the --only identity
collision the loader fatals on.
"""
from __future__ import annotations

from typing import Any

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

# Legacy section key -> canonical key.
#   2026-09-01: `rules`   -> `chains`   (Rule -> Chain rename)
#   2026-09-20: `scheme_lists` -> `imprints` (Scheme List -> Imprint rename)
_SECTION_ALIASES: dict[str, str] = {
    "rules": "chains",
    "scheme_lists": "imprints",
}

# Optional per-alias pointer at the tool that migrates such a file on disk.
_SECTION_ALIAS_TOOLS: dict[str, str] = {
    "rules": "tools/convert_rules_to_chains.py",
}

# Legacy ENTITY record key -> canonical key (2026-09-20 Imprint rename).
# Single source of truth for BOTH the raw-dict loader (config/entries.py) and
# the s-expr record parser (config/sexp_format.py): a legacy `scheme_list:`
# entity must keep loading, wherever the config came from.
_ENTITY_KEY_ALIASES: dict[str, str] = {
    "scheme_list": "imprint",
}


def _fatal_both_keys(legacy: str, canonical: str) -> ValidationError:
    """The one fatal both spellings of a renamed key share: a file (or an
    ENTITY record) that carries the old and the new key at once is ambiguous —
    refuse loudly rather than pick a winner in silence."""
    hint = _("the legacy {legacy} key was renamed to {canonical} — keep one: "
             "move the old value into {canonical} and remove {legacy}").format(
        legacy=legacy + ":", canonical=canonical + ":")
    tool = _SECTION_ALIAS_TOOLS.get(legacy)
    if tool is not None:
        # A file name is not translated — appended as-is.
        hint = f"{hint} ({tool})"
    return ValidationError(format_fatal_error(
        _("both {legacy} and {canonical} present in the same file").format(
            legacy=legacy + ":", canonical=canonical + ":"),
        [hint],
    ))


def normalize_section_aliases(data: dict[str, Any]) -> dict[str, Any]:
    """Rename legacy section keys (`rules` -> `chains`, `scheme_lists` ->
    `imprints`) in a raw parsed config dict, in place, and return the same
    dict. Fatal if a file has BOTH the legacy and the canonical key
    (ambiguous — which one wins?)."""
    for legacy, canonical in _SECTION_ALIASES.items():
        if legacy in data:
            if canonical in data:
                raise _fatal_both_keys(legacy, canonical)
            data[canonical] = data.pop(legacy)
    return data


def normalize_entity_aliases(data: dict[str, Any]) -> dict[str, Any]:
    """Rename the legacy `scheme_list:` ENTITY key to `imprint:` in a raw entity
    dict, in place, and return the same dict. Fatal when a record carries BOTH
    spellings — the same "never a silent merge" rule as the section aliases."""
    for legacy, canonical in _ENTITY_KEY_ALIASES.items():
        if legacy in data:
            if canonical in data:
                raise _fatal_both_keys(legacy, canonical)
            data[canonical] = data.pop(legacy)
    return data


def read_entity_field(data: dict[str, Any], key: str = "imprint") -> Any:
    """Value of an ENTITY field from a RAW dict, tolerating the legacy spelling
    of the 2026-09-20 rename (`scheme_list` for `imprint`).

    The GUI pages that render a record straight out of the file (without going
    through the loader) must read it through here, so a profile written before
    the rename shows its Imprint instead of an empty row. This is the ONE place
    in the codebase that still knows the legacy spelling outside the shim
    parsers; the rename guard whitelists it by name."""
    if key in data:
        return data[key]
    for legacy, canonical in _ENTITY_KEY_ALIASES.items():
        if canonical == key and legacy in data:
            return data[legacy]
    return None

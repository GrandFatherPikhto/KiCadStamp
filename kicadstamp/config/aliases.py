# kicadstamp/config/aliases.py
"""Section-key read aliases (2026-09-01, plan_2026_09_01_rules_to_chains.md).

The `rules:` -> `chains:` rename keeps reading OLD profiles that still use the
legacy `rules:` key (the converter tools/convert_rules_to_chains.py migrates
them on disk; the alias makes them loadable until then — and even afterwards,
harmlessly, since no file will contain `rules:` anymore).

normalize_section_aliases() renames legacy keys to their canonical form in a
RAW parsed config dict, BEFORE any section processing (list/dict checks in
includes.py, dataclass loading in loader.py, sexp conversion). It is called by
every raw-dict reader so the whole pipeline only ever sees `chains:`:

- config/includes.py::_load_config_file (load_config + walk_include_tree)
- config_writer.py::_read_data (GUI dock write paths)
- gui/yaml_io.py::load_data (read-only browsing)

Fatal (never a silent merge) when a file carries BOTH the legacy key and the
canonical one — that is ambiguous, same reasoning as the --only identity
collision the loader fatals on.
"""
from __future__ import annotations

from typing import Any

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

# Legacy section key -> canonical key (2026-09-01 rename).
_SECTION_ALIASES: dict[str, str] = {
    "rules": "chains",
}


def normalize_section_aliases(data: dict[str, Any]) -> dict[str, Any]:
    """Rename legacy section keys (`rules` -> `chains`) in a raw parsed config
    dict, in place, and return the same dict. Fatal if a file has BOTH the
    legacy and the canonical key (ambiguous — which one wins?)."""
    for legacy, canonical in _SECTION_ALIASES.items():
        if legacy in data:
            if canonical in data:
                raise ValidationError(format_fatal_error(
                    _("both {legacy}: and {canonical}: present in the same config file")
                    .format(legacy=legacy, canonical=canonical),
                    [_("the legacy {legacy}: key was renamed to {canonical}: (2026-09-01) — "
                       "keep one. Move the {legacy}: entries into {canonical}: (or run "
                       "tools/convert_rules_to_chains.py) and remove the old key")
                     .format(legacy=legacy, canonical=canonical)]
                ))
            data[canonical] = data.pop(legacy)
    return data

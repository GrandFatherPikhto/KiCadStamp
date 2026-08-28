# gui/yaml_io.py
"""
Small shared YAML read helpers for the docks that browse an already-written
config file's contents (ExtractDock's "Existing cells:"/"Existing profiles:"
lists, PlacerDock's Cell list). Split out once PlacerDock needed the exact
same "read this file, give me its top-level (or nested-section) keys"
logic ExtractDock already had — see gui/docks/extract.py's _load_data()/
_existing_keys() history.
"""
import json
import logging
from pathlib import Path
from typing import Optional, Set

from kicadstamp.config.sexp_format import sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.file_cache import cached_file_read

logger = logging.getLogger(__name__)


def load_data(path: Optional[Path]) -> dict:
    """Read a config file's .sexp/.json content ({} when missing/malformed,
    or when the file is any other format — YAML support was removed from the
    config graph, 2026-08-28, core_yaml_removal).

    Routed through cached_file_read (2026-08-21, see
    techdocs/handoff/deepseek/plan_2026_08_21_startup_graph_level_cache.md's
    "actual bottleneck" finding): this was the ONE raw reader the 2026-08-15
    single-file cache missed, so RootMetadataDock's set_target_file() re-parsed
    the root YAML from disk right before every dock's walk_include_tree()/
    load_config() re-parsed the SAME bytes through the cache — ~1.8s of pure
    redundant parse per startup. The contract is UNCHANGED ({} for a missing
    or malformed file); malformed files still never enter the cache (the
    loader raises before cached_file_read stores anything)."""
    if path is None or not path.exists():
        return {}

    def _uncached_read(p: Path) -> dict:
        with open(p, "r", encoding="utf-8") as f:
            if p.suffix.lower() == ".json":
                return json.load(f) or {}
            if p.suffix.lower() == ".sexp":
                return sexp_to_dict(f.read()) or {}
            return {}  # .yaml/.yml and any other extension — not a supported config format

    try:
        return cached_file_read(path, _uncached_read)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return {}


def existing_keys(path: Optional[Path], section: Optional[str] = None) -> Set[str]:
    data = load_data(path)
    if section is not None:
        data = data.get(section) or {}
    return set(data.keys())

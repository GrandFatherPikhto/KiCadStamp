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

import yaml

from kicadstamp.utils.file_cache import cached_file_read

logger = logging.getLogger(__name__)


def load_data(path: Optional[Path]) -> dict:
    """Read a config file's YAML/JSON content ({} when missing/malformed).

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
            return (json.load(f) if p.suffix.lower() == ".json" else yaml.safe_load(f)) or {}

    try:
        return cached_file_read(path, _uncached_read)
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return {}


def existing_keys(path: Optional[Path], section: Optional[str] = None) -> Set[str]:
    data = load_data(path)
    if section is not None:
        data = data.get(section) or {}
    return set(data.keys())

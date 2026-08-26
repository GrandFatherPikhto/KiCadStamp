# kicadstamp/runtime_context.py
"""
runtime_context.py — runtime-computed data that is NOT part of the YAML config.

Separated from Config so that Config stays a pure description of the YAML
schema. Currently only holds sheet_names (parsed from *.kicad_sch files during
load_config). May be extended with adapter, log_dir, etc. in the future.

DELIBERATE EXCEPTION (arch review item 6, 2026-08-11 — comment-only, NOT
refactored): load_config() pulls the *.kicad_sch parser
(sheet_names.build_sheet_name_map) into config/loader.py, which technically
breaks config's "pure YAML-schema description" goal. This is a conscious
trade-off, not an oversight:

  - The YAML schema ITSELF references schematic sheets: anchor_sheet (on
    Rule/ClonePlacement/Point/ThermalViaArrayConfig) narrows anchor_role
    ambiguity, and resolving that anchor needs the real {uuid: Sheetname} map.
  - That map is runtime-computed data (parsed from schematic files, not from
    YAML) — which is exactly why it lives HERE in RuntimeContext, not on Config.
  - Building it once, at load, alongside the config is the single construction
    point shared by every downstream consumer (validation, dependency_order,
    the planners, clone_role_resolver, template_extraction). Deferring or
    rebuilding it per consumer would spread the logic and risk divergence.
  - The cost is bounded: sheet_names.py is a leaf (imports only
    exceptions/i18n), so config gains no cycle and no heavier dependency
    (no geometry/placement/adapter).
"""
from collections.abc import Mapping
from dataclasses import dataclass, field



@dataclass
class RuntimeContext:
    """
    Runtime-computed data that complements the static YAML Config.

    Created by load_config() and threaded through the pipeline alongside Config
    wherever sheet_names (or the resolved path fields below) are needed.

    sheet_names — {uuid: Sheetname} built from *.kicad_sch by load_config();
    this field is the reason load_config() deliberately imports sheet_names
    (see the module docstring): anchor_sheet in the YAML references real
    schematic sheets, and resolving it needs this runtime map.

    registry_path / track_registry_path / log_file / operation_log_dir /
    root_sheet — the RESOLVED absolute paths of the corresponding Config
    fields (P1-3, 2026-08-25): Config stores the RAW values exactly as the
    user wrote them in the YAML (relative to that YAML), while load_config()
    resolves them against the config file's directory and stores the RESULTS
    here. Consumers that actually open/read/write these files read from
    RuntimeContext, never re-resolving or re-deriving the path themselves.
    Each is None when the YAML leaves the key unset.
    """
    sheet_names: Mapping[str, str] = field(default_factory=dict)
    registry_path: str | None = None
    track_registry_path: str | None = None
    log_file: str | None = None
    operation_log_dir: str | None = None
    root_sheet: str | None = None

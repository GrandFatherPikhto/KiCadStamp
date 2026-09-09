# gui/cell_edit_context.py
"""Remember the (Cluster, Sheet) context a cell was last created/edited in —
Phase E of plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md
(implements "Идея D" of
note_2026_09_08_cell_anchor_selection_and_coordinate_converter.md).

A Cell is an abstract, deliberately cluster-agnostic template (the template-zoo
design): the same slug-named cell can be created from any placed Cluster
instance and then used on many boards/channels. The cell's own entry therefore
stores NO cluster/sheet (that would make it non-portable) — the (Cluster,
Sheet) pair that created it is captured in GUI-local state instead, as a HINT
for the UI. It is NEVER a source of truth and NEVER a hard dependency.

gui_state.json layout (settings.state, key "cell_edit_context"):

    "cell_edit_context": {
        "<abs path of the root config>": {
            "<cell name>": {"cluster": "PIF_3V3_VDD", "sheet": "FPGA"}
        }
    }

- The per-root-config scope is REQUIRED: the cell name is a slug of its
  Cluster tag (gui/docks/reead.py's _slugify), so the same pif_3v3_vdd in two
  profiles points at DIFFERENT boards. The per-root scope copies the existing
  config_tree_collapsed key; the nested-per-name dict mirrors the trees_dock
  key — both patterns are already in gui_state.json, nothing is invented here.
- The record is "last used": overwritten whenever the user works with the same
  cell through another structurally identical cluster (a re-extract, or the
  cell-anchor page's "Read from selection", which brings a fresh Cluster).

Hard rule (§E.5): remembered values rot in practice (real gui_state.json files
carry stale paths from before project renames), so every reader degrades
SILENTLY — a remembered Cluster/Sheet that no longer resolves on the current
live board leaves the UI fields empty / does nothing, never a fatal.

Live-resolution helpers live here too (shared by the two consumers):
  - cluster_present_on_board(...) — the prefill gate (cell_anchor_view);
  - resolve_context_footprints(...) — the actual board footprints of the
    remembered (Cluster, Sheet) instance, gathered through the SAME
    role_narrowing cascade the whole project uses for (Sheet, Cluster)
    addressing (no second terminology), used by CellDock's "Select cluster on
    the board" button.
"""
import logging
from typing import Optional

from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.placement.services.role_narrowing import narrow_candidates_by_sheet

from . import settings

logger = logging.getLogger(__name__)

# The gui_state.json key holding the per-(root, cell) remembered contexts.
CELL_EDIT_CONTEXT_KEY = "cell_edit_context"


def _state() -> dict:
    """The current cell_edit_context map (or {} when absent/malformed) —
    never raises."""
    try:
        raw = settings.state.get(CELL_EDIT_CONTEXT_KEY, {})
    except Exception:  # noqa: BLE001 — a state read must never break a caller
        return {}
    return raw if isinstance(raw, dict) else {}


def remember_cell_edit_context(root_path, cell_name: str, cluster, sheet) -> None:
    """Record the last-used (Cluster, Sheet) of `cell_name` under the root
    config `root_path`. Best-effort and never raises (a state write must never
    break a Save / cell creation). An empty cluster or missing cell name/root
    writes nothing."""
    if not cell_name or not cluster or root_path is None:
        return
    try:
        state = _state()
        per_root = state.setdefault(str(root_path), {})
        per_root[cell_name] = {
            "cluster": str(cluster),
            "sheet": str(sheet) if sheet else None,
        }
        settings.state.set(CELL_EDIT_CONTEXT_KEY, state)
    except Exception:  # noqa: BLE001 — state is a hint; never fatal
        logger.warning("Failed to remember cell %r (cluster %r) — state write "
                       "skipped", cell_name, cluster)


def remembered_cell_edit_context(root_path, cell_name: str) -> tuple[
        Optional[str], Optional[str]]:
    """(Cluster, Sheet) remembered for `cell_name` under `root_path`, or
    (None, None) when nothing is recorded or the stored shape is malformed.
    Never raises — the missing/stale case is the everyday case here."""
    if root_path is None or not cell_name:
        return (None, None)
    try:
        per_root = _state().get(str(root_path)) or {}
        if not isinstance(per_root, dict):
            return (None, None)
        entry = per_root.get(cell_name)
        if not isinstance(entry, dict):
            return (None, None)
        cluster = entry.get("cluster")
        sheet = entry.get("sheet")
        return (str(cluster) if cluster else None,
                str(sheet) if sheet else None)
    except Exception:  # noqa: BLE001 — best-effort read, never fatal
        return (None, None)


def cluster_present_on_board(adapter, cluster: str) -> bool:
    """True when at least one LIVE footprint carries `cluster` in its Cluster
    field (cluster_prefix_match). The prefill gate (§E.5): a remembered cluster
    that no longer exists on the current board must leave the UI fields empty.
    False when the adapter is unavailable (offline — nothing can "resolve") or
    the scan fails; never raises."""
    if adapter is None or not cluster:
        return False
    try:
        for fp in adapter.get_footprints():
            fp_cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
            if cluster_prefix_match(fp_cluster, cluster):
                return True
    except Exception:  # noqa: BLE001 — best-effort gate, never fatal
        return False
    return False


def resolve_context_footprints(adapter, footprints, cluster: str, sheet,
                               sheet_names: Optional[dict]) -> list:
    """The live footprints of the remembered (Cluster, Sheet) instance.

    The CLUSTER step is the hard gate: only footprints whose Cluster field
    cluster_prefix_matches `cluster`. An empty result means the remembered
    context no longer exists on this board — the caller then leaves the
    UI/selection alone (a message, never a fatal). The SHEET step narrows that
    set through role_narrowing.narrow_candidates_by_sheet — the project's ONE
    (Sheet, Cluster) addressing cascade — and, exactly like that cascade
    everywhere, only when it genuinely reduces the set (a stale Sheet never
    hides the cluster's footprints; when the cluster sits on one instance only,
    Sheet is moot)."""
    if not cluster:
        return []
    try:
        members = [fp for fp in (footprints or [])
                   if cluster_prefix_match(
                       adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or "",
                       cluster)]
        if members and sheet:
            by_sheet = narrow_candidates_by_sheet(members, sheet, sheet_names or {})
            if by_sheet and len(by_sheet) < len(members):
                members = by_sheet
        return members
    except Exception:  # noqa: BLE001 — best-effort; a stale context is the norm
        return []

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

... and, since 2026-09-17 (stage 2 of the spoke work), the cell editor's "Refs"
tab keeps the last role table the user filled in under its OWN key — a table of
user input only, the board's columns being the snapshot's business:

    "cell_role_table": {
        "<abs path of the root config>": {
            "<cell name>": {"cluster": "FPGA_PWR_BANK",
                            "rows": [{"ref": "C74", "role": "C_FPGA_BULK",
                                      "cluster": "FPGA_PWR_BANK"}]}
        }
    }

A separate key on purpose: ANY context write without refs erases the identified
refs (the stage-1 rule), and that must not touch a table the user just typed.

... and, since 2026-09-17 (stage 1 of the spoke work), an entry may also carry the
IDENTIFIED refs of that instance:

            "<cell name>": {"cluster": "FPGA_PWR_BANK", "sheet": null,
                            "refs": {"C_FPGA_BULK": "C69", "C_FPGA_BYPASS": "C53"}}

Why the refs live here and not in the config: a spoke's cluster holds the same
Role many times, so (Cluster, Sheet) cannot name WHICH pair the user is editing —
the selection can, and what it yields is a role -> refdes map. That map is an
INTERFACE cache, checked against the board on every use (the live frame reader
refuses a ref whose Role changed or which left the board as "stale"); refdes are
never written to a cell, a spoke or the config. The rule that keeps it honest:
ANY write of a context WITHOUT refs (a manual Cluster/Sheet pick, a re-read that
brings a fresh cluster) ERASES them — see remember_cell_edit_context's docstring.

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

Live-resolution helpers live here too:
  - resolve_context_footprints(...) — the actual board footprints of the
    remembered (Cluster, Sheet) instance, gathered through the SAME
    role_narrowing cascade the whole project uses for (Sheet, Cluster)
    addressing (no second terminology), used by CellDock's "Select cluster on
    the board" button.
    NOT by the cell-anchor page's prefill any more: that one now judges the
    remembered cluster against the snapshot the page has already been fed, never
    against the adapter (2026-09-17, stage 1а of the spoke work — a board read on
    the UI thread that failed used to empty the Sheet/Cluster fields on reopen,
    and cluster_present_on_board, which swallowed every exception, was that read).
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

# The gui_state.json key holding the last table the user filled in on the cell
# editor's "Refs" tab (2026-09-17, stage 2 of the spoke work). Deliberately a
# SEPARATE key from the context above: a context write without refs ERASES the
# identified refs (stage 1 rule), and the user's typed table must survive that.
CELL_ROLE_TABLE_KEY = "cell_role_table"


def _state(key: str = CELL_EDIT_CONTEXT_KEY) -> dict:
    """The current stored map under `key` (or {} when absent/malformed) —
    never raises."""
    try:
        raw = settings.state.get(key, {})
    except Exception:  # noqa: BLE001 — a state read must never break a caller
        return {}
    return raw if isinstance(raw, dict) else {}


def remember_cell_edit_context(root_path, cell_name: str, cluster, sheet) -> None:
    """Record the last-used (Cluster, Sheet) of `cell_name` under the root
    config `root_path`. Best-effort and never raises (a state write must never
    break a Save / cell creation). An empty cluster or missing cell name/root
    writes nothing.

    This write ERASES any identified `refs` of that cell (2026-09-17): a manual
    Cluster/Sheet pick — or a read that brings a DIFFERENT cluster — says nothing
    about which pair of components is meant, and a leftover map from another
    instance is exactly the stale data the "stale identification" refusal exists
    for. The entry is rewritten whole, so nothing has to be cleaned up by hand."""
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


def remember_cell_instance(root_path, cell_name: str, identification) -> None:
    """Record an IDENTIFIED instance of `cell_name`: its (Cluster, Sheet) AND the
    role -> refdes map that pins the pair down (2026-09-17, design Р2: "if the
    cluster is identified, the refs must be written down automatically").

    `identification` is gui.cell_identification.Identification (duck-typed: a
    .cluster/.sheet/.role_to_ref shape). Best-effort and never raises — a state
    write must never break the button that produced it. An identification without
    a cluster, or with an empty map, writes no refs at all (the entry then behaves
    exactly like one written by remember_cell_edit_context)."""
    if not cell_name or root_path is None or identification is None:
        return
    cluster = getattr(identification, "cluster", None)
    if not cluster:
        return
    sheet = getattr(identification, "sheet", None)
    refs = {str(role): str(ref)
            for role, ref in (getattr(identification, "role_to_ref", None) or {}).items()
            if role and ref}
    try:
        state = _state()
        per_root = state.setdefault(str(root_path), {})
        entry = {
            "cluster": str(cluster),
            "sheet": str(sheet) if sheet else None,
        }
        if refs:
            entry["refs"] = refs
        per_root[cell_name] = entry
        settings.state.set(CELL_EDIT_CONTEXT_KEY, state)
    except Exception:  # noqa: BLE001 — state is a hint; never fatal
        logger.warning("Failed to remember the identified instance of %r "
                       "(cluster %r) — state write skipped", cell_name, cluster)


def remembered_cell_refs(root_path, cell_name: str) -> Optional[dict]:
    """The role -> refdes map remembered for `cell_name`, or None when there is
    none (no entry, no refs key, an empty/malformed map).

    Never raises — like every other reader here, "nothing remembered" is the
    everyday case (a cell that was only ever hand-picked, or a context written
    before this stage). A non-empty result is STILL only a hint: whether those
    refs are on the board with the expected Roles is decided by the live frame
    reader, which refuses a stale map instead of drawing someone else's pair."""
    if root_path is None or not cell_name:
        return None
    try:
        per_root = _state().get(str(root_path)) or {}
        if not isinstance(per_root, dict):
            return None
        entry = per_root.get(cell_name)
        if not isinstance(entry, dict):
            return None
        refs = entry.get("refs")
        if not isinstance(refs, dict) or not refs:
            return None
        cleaned = {str(role): str(ref) for role, ref in refs.items()
                   if role and ref}
        return cleaned or None
    except Exception:  # noqa: BLE001 — best-effort read, never fatal
        return None


def remember_role_table(root_path, cell_name: str, table) -> None:
    """Record the LAST table filled in on the cell editor's "Refs" tab, under
    `cell_name` / `root_path` (2026-09-17, stage 2; design Р2б).

    `table` is the model's own state shape — {"cluster": str, "rows": [{"ref",
    "role", "cluster"}]} (gui/role_table_model.table_to_state) — and only what
    the USER typed belongs in it: the board's own values are the snapshot's
    business and are deliberately NOT stored here (they rot; a remembered copy
    would be shown as if the user had typed it). An empty row list means "the
    user cleared the table", so the entry is REMOVED rather than stored empty —
    otherwise the rows would come back on the next open.

    Best-effort and never raises, like every other writer here: a state write
    must never break the button that produced it.

    Separate key, separate concern (Р2б): this is NOT a field of
    cell_edit_context, so the stage-1 rule "a context write without refs erases
    the identified refs" — a manual Cluster/Sheet pick on the Source tab, say —
    cannot erase the table the user is working with."""
    if not cell_name or root_path is None:
        return
    try:
        state = _state(CELL_ROLE_TABLE_KEY)
        per_root = state.setdefault(str(root_path), {})
        rows = []
        if isinstance(table, dict):
            for entry in (table.get("rows") or ()):
                if not isinstance(entry, dict):
                    continue
                ref = str(entry.get("ref") or "").strip()
                if not ref:
                    continue
                rows.append({"ref": ref,
                             "role": str(entry.get("role") or ""),
                             "cluster": str(entry.get("cluster") or "")})
        if rows:
            per_root[cell_name] = {
                "cluster": str((table or {}).get("cluster") or ""),
                "rows": rows,
            }
        else:
            per_root.pop(cell_name, None)
            if not per_root:
                state.pop(str(root_path), None)
        settings.state.set(CELL_ROLE_TABLE_KEY, state)
    except Exception:  # noqa: BLE001 — state is a hint; never fatal
        logger.warning("Failed to remember the role table of %r — state write "
                       "skipped", cell_name)


def remembered_role_table(root_path, cell_name: str) -> Optional[dict]:
    """The table last filled in on the "Refs" tab of `cell_name` under
    `root_path`, or None when there is none (nothing recorded, an emptied table,
    a malformed entry). Never raises.

    A non-empty result is STILL only the user's own input: the board's columns
    of every row come from the snapshot the page holds
    (role_table_model.rows_from_state), never from here."""
    if root_path is None or not cell_name:
        return None
    try:
        per_root = _state(CELL_ROLE_TABLE_KEY).get(str(root_path)) or {}
        if not isinstance(per_root, dict):
            return None
        entry = per_root.get(cell_name)
        if not isinstance(entry, dict):
            return None
        rows = entry.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        cleaned = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            ref = str(raw.get("ref") or "").strip()
            if not ref:
                continue
            cleaned.append({"ref": ref,
                            "role": str(raw.get("role") or ""),
                            "cluster": str(raw.get("cluster") or "")})
        if not cleaned:
            return None
        return {"cluster": str(entry.get("cluster") or ""), "rows": cleaned}
    except Exception:  # noqa: BLE001 — best-effort read, never fatal
        return None


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

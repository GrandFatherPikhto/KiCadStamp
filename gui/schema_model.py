# gui/schema_model.py
"""
load_schematic_components() — flattens the whole schematic hierarchy
(kicadstamp.schematic_discovery + kicadstamp.schematic_blocks) into one
row per REFDES, for the main GUI's Components tree in "Not yet applied"
mode (gui/docks/role_cluster_tree.py, reading gui.fieldstool_window.
MainWindow's own self._components) and for staging edits by ref. GUI-only
(fieldstool_cli.py never needs a per-ref flattened view) — that's why this
lives in gui/, not alongside the kicadstamp.schematic_* modules it reads.
A (symbol ...) block can carry several refdes (multi-instance sheet) or a
refdes can span several blocks (multi-unit symbol, see kicadstamp.
schematic_set_fields's module docstring) — this expands/collapses both
into "one row per ref" since that's the unit a human picks components by,
even though the underlying edit is block-level.

load_schematic_view() is the ONE hierarchy walk these two views are built
from, and it also hands back the list of .kicad_sch files it read —
hierarchy_signature()/signature_is_stale() turn that list into the cheap
freshness probe the fieldstool window runs on its existing triggers.
load_schematic_components()/load_schematic_instances() stay as their own
names for the existing callers, as one-line delegates to the view.
"""
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from kicadstamp.schematic_blocks import find_property_value_span
from kicadstamp.schematic_discovery import load_schematic_tree


@dataclass
class SchematicComponent:
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    file: str
    block_start: int  # identifies which SymbolBlock this ref's shown values came from
    divergent: bool    # True if this ref spans multiple blocks with disagreeing Role/Cluster
                       # (multi-unit symbol whose per-unit copies were never kept in sync —
                       # schema allows it, nothing enforces it, see schematic_set_fields.py)
    symbol_uuids: tuple = ()  # top-level (uuid ...) of the SymbolBlock(s) carrying this
                              # ref — the symbol's own identity. The board side exposes the
                              # same uuid as fp.sheet_path.path[-1] (recon confirmed:
                              # 279/279 on 3CH-AWG-TIA), which lets Pending changes verify a
                              # refdes means the SAME symbol on both sides instead of trusting
                              # the refdes string blindly (see gui/docks/pending.py).


def _components_from(file_texts: Dict[str, str],
                     all_blocks) -> List[SchematicComponent]:
    # ref -> list of (role, cluster, file, block_start, symbol_uuid), one entry per block containing it
    by_ref: Dict[str, list] = {}
    for block in all_blocks:
        if not block.refs:
            continue
        span_text = file_texts[block.file][block.start:block.end]
        role_span = find_property_value_span(span_text, "Role")
        cluster_span = find_property_value_span(span_text, "Cluster")
        role = span_text[role_span[0]:role_span[1]] if role_span else None
        cluster = span_text[cluster_span[0]:cluster_span[1]] if cluster_span else None
        for ref in block.refs:
            # KiCad's own convention: a reference starting with "#" (power
            # symbols/PWR_FLAG — #PWR01, #FLG01, ...) marks "excluded from
            # board" — no footprint ever exists for these, so they never
            # show up in the live PCB tree (kipy only enumerates real
            # footprints) and can't usefully carry a Role/Cluster tag.
            # iter_symbol_blocks() reads every (symbol ...) instance
            # unconditionally (it's shared with schematic_set_fields.py/
            # schematic_rename_fields.py, which must still be able to
            # target one directly if ever asked) — this GUI-only "one row
            # per ref" view is where the noise actually gets filtered
            # (found live 2026-08-03: 171 "#FLG*" rows in one real board's
            # schematic view, none selectable on the PCB).
            if ref.startswith("#"):
                continue
            by_ref.setdefault(ref, []).append((role, cluster, block.file, block.start, block.uuid))

    components = []
    for ref, entries in by_ref.items():
        role, cluster, file, block_start, _ = entries[0]
        divergent = any((r, c) != (role, cluster) for r, c, _, _, _ in entries[1:])
        symbol_uuids = tuple(sorted({u for _, _, _, _, u in entries if u}))
        components.append(SchematicComponent(
            ref, role, cluster, file, block_start, divergent, symbol_uuids))
    return components


def load_schematic_view(root_sheet: str) -> Tuple[
        List[SchematicComponent], Dict[tuple, "SchematicInstance"], List[str]]:
    """Read the whole sheet hierarchy ONCE and build all three things a caller
    needs: the flat per-ref view (row per REFDES), the full-path instance index
    (board fp.sheet_path.path -> schematic instance) and the list of .kicad_sch
    files that were read. load_schematic_components()/load_schematic_instances()
    below are one-line delegates to this — they used to each walk the hierarchy
    on their own (two full reads for one Rescan); now there is one walk per call."""
    files, file_texts, all_blocks = load_schematic_tree(root_sheet)
    return (_components_from(file_texts, all_blocks),
            _instances_from(file_texts, all_blocks),
            files)


def load_schematic_components(root_sheet: str) -> List[SchematicComponent]:
    """The flat per-ref view alone — a delegate to load_schematic_view(), kept
    as its own name for the existing callers (the main GUI's Components tree,
    which reads gui.fieldstool_window.MainWindow.components, and the tests)."""
    return load_schematic_view(root_sheet)[0]


@dataclass
class SchematicInstance:
    """One PLACED instance of a symbol — the per-instance (path ...) entry
    from (instances ...) resolved to the block's Role/Cluster. The unit the
    full-path diff (compute_pending_edits with path_index) matches against:
    a board footprint's sheet_path.path equals exactly one of these keys."""
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    file: str
    block_start: int


def _full_key(inst_path: tuple, top_uuid: str) -> tuple:
    """The key a board fp.sheet_path.path equals: the schematic (instances ...)
    path minus its root uuid, plus the block's top-level uuid. For a root-sheet
    symbol inst_path is (root,) -> key is just (top_uuid,). Empirically this
    joins 358/364 footprints on 3CH-AWG-TIA (the 6 misses are board-only)."""
    return inst_path[1:] + (top_uuid,) if len(inst_path) > 1 else (top_uuid,)


def _instances_from(file_texts: Dict[str, str],
                    all_blocks) -> Dict[tuple, SchematicInstance]:
    """Full-path index: board `fp.sheet_path.path` -> the schematic instance
    that footprint physically is, regardless of whether the two sides agree
    on the refdes (re-annotation desync). Key = _full_key(instances path, block
    uuid). This is what lets Pending changes diff a re-annotated board's
    footprints against the correct schematic symbol instead of silently
    dropping them as "board-only" or pairing them by a stale refdes."""
    index: Dict[tuple, SchematicInstance] = {}
    for block in all_blocks:
        if not block.instances or not block.uuid:
            continue
        span_text = file_texts[block.file][block.start:block.end]
        role_span = find_property_value_span(span_text, "Role")
        cluster_span = find_property_value_span(span_text, "Cluster")
        role = span_text[role_span[0]:role_span[1]] if role_span else None
        cluster = span_text[cluster_span[0]:cluster_span[1]] if cluster_span else None
        for inst_path, ref in block.instances:
            if ref.startswith("#"):
                continue
            key = _full_key(inst_path, block.uuid)
            index.setdefault(key, SchematicInstance(ref, role, cluster,
                                                    block.file, block.start))
    return index


def load_schematic_instances(root_sheet: str) -> Dict[tuple, SchematicInstance]:
    """The full-path instance index alone — a delegate to
    load_schematic_view() (see its docstring for what the index means)."""
    return load_schematic_view(root_sheet)[1]


def hierarchy_signature(files: Iterable[str]
                        ) -> Tuple[Tuple[str, Optional[int], Optional[int]], ...]:
    """The freshness signature of ONE read of the sheet hierarchy: for every
    .kicad_sch file that read covered, its (path, mtime_ns, size). A file that
    cannot be stat()ed (already gone) is recorded as (path, None, None) instead
    of raising — a missing sheet then reads as CHANGED (see signature_is_stale).

    This is the CHEAP probe's baseline: it is taken from the file list the last
    full read already produced (load_schematic_view), so the per-tick check
    never walks the hierarchy again — walking it READS EVERY SHEET'S TEXT, which
    is exactly the cost the auto-reread exists to avoid paying on a timer.

    Coarse-clock caveat: a rewrite that leaves both mtime_ns and size identical
    is invisible here (FAT's 2-second stamp, or a same-length in-place edit).
    That is a deliberate trade — no file hashing on the poll tick. The fallback
    for that case is the Rescan button, which always re-reads."""
    out = []
    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            out.append((path, None, None))
        else:
            out.append((path, st.st_mtime_ns, st.st_size))
    return tuple(out)


def signature_is_stale(
        signature: Tuple[Tuple[str, Optional[int], Optional[int]], ...]) -> bool:
    """Did any file behind `signature` change since it was taken? Re-stats the
    RECORDED files only (no re-walk — see hierarchy_signature) and compares the
    whole tuple: a different mtime_ns or size, a file that vanished, and a file
    that was missing at read time and has since appeared all read as "changed".

    A brand-new sheet added to the hierarchy always rides on its PARENT sheet
    being rewritten to reference it, so that parent's mtime/size change is what
    this sees; the file SET is compared after the re-read, not here."""
    paths = [path for path, _mtime, _size in signature]
    return hierarchy_signature(paths) != signature

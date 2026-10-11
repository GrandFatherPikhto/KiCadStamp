# gui/schematic_freshness.py
"""
SchematicFreshness — the poll-tick-cheap half of
plan_2026_10_11_fieldstool_stale_schematic.

fieldstool reads the sheet hierarchy once and then judges Pending/Apply/Sync
against that read. If the user saves in Eeschema afterwards, the window would
keep judging a read taken BEFORE the save (the live defect: 29 stale
"Refdes/symbol mismatch" rows and a dead Sync). This class owns the fix's
non-UI half: it remembers the .kicad_sch files the last read covered, with
their mtime_ns/size (gui.schema_model.hierarchy_signature), answers "did any of
them change?" with a cheap os.stat — never a re-walk, which READS EVERY SHEET'S
TEXT, exactly the cost the auto-reread exists to avoid on a timer — and re-reads
the hierarchy (gui.schema_model.load_schematic_view) only when one did.

Qt-free and widget-free on purpose: the caller adopts the returned view
(components, path index) and does its own combo/forget bookkeeping. A failed
re-read keeps the last good read, does NOT advance the signature (so the next
tick retries) and writes ONE WARN per distinct failing signature — the auto
path runs on the ~2s poll, so it must never open a window (that stays the
Rescan button's, which is an explicit click and always re-reads).
"""
import logging
from typing import List, Optional, Tuple

from kicadstamp.i18n import _

from .schema_model import (hierarchy_signature, load_schematic_view,
                           signature_is_stale)


class SchematicFreshness:
    """One window's record of its last schematic read (see the module
    docstring). It is not a cache of the parsed components — the caller keeps
    those; this keeps only the file signature and the WARN dedup."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._files: List[str] = []
        self._signature: Optional[tuple] = None
        self._error_signature: Optional[tuple] = None

    def read(self, root_sheet) -> Tuple[Optional[tuple], Optional[str]]:
        """Force a read of the hierarchy. Returns (view, None) on success where
        view = (components, path_index, files), or (None, error_text) on an
        OSError/ValueError. On success the recorded signature is replaced; on
        failure nothing is touched, so the caller keeps its last good read.

        The signature is taken AFTER the read (load_schematic_view, then
        hierarchy_signature), so a write that lands between the read and the
        stat is NOT seen — the stamp recorded is the one the new write left, and
        it is noticed only at the NEXT change. The Rescan button is the fallback
        for that window: it re-reads regardless of the signature."""
        try:
            components, path_index, files = load_schematic_view(str(root_sheet))
        except (OSError, ValueError) as e:
            return None, str(e)
        self._files = list(files)
        self._signature = hierarchy_signature(files)
        self._error_signature = None
        return (components, path_index, files), None

    def reread_if_stale(self, root_sheet) -> Tuple[Optional[tuple], Optional[str]]:
        """Read only when a file behind the last read changed (cheap stat).
        Returns (None, None) when nothing changed, (view, None) when it re-read
        successfully, or (None, error_text) on a failed re-read — in which case
        the last read and the signature both stay (the next tick retries) and
        ONE WARN per distinct failing signature is logged."""
        if self._signature is None or not signature_is_stale(self._signature):
            return None, None
        view, error = self.read(root_sheet)
        if error is None:
            return view, None
        stale = hierarchy_signature(self._files)
        if self._error_signature != stale:
            self._error_signature = stale
            self._logger.warning(
                _("The schematic changed on disk but could not be re-read — "
                  "keeping the previous read; it will be retried. If this "
                  "persists, use Rescan once the file is readable."))
        return None, error


def adopt_read(window, view) -> None:
    """Adopt a read (components, path_index, files) into `window`: store the
    view on it, refresh the pickable Role/Cluster combos (unioned with the live
    board's values this session — a value set on the board is not in the
    schematic until Apply, so a read alone would never surface it as pickable)
    and run the Т6/С5 "our note is redundant now" pass.

    Moved out of gui/fieldstool_window.py so the 800-line giant keeps only calls
    (rule 45); behaviour is byte-for-byte the old private method. It takes the
    window because this bookkeeping IS window state (the combos and the override
    store it forgets from)."""
    components, path_index, _files = view
    window._components = components
    window._path_index = path_index
    roles = {c.role for c in components if c.role}
    clusters = {c.cluster for c in components if c.cluster}
    roles |= {s.role for s in window._live_snapshot if s.role}
    clusters |= {s.cluster for s in window._live_snapshot if s.cluster}
    window._set_combo_items(window.role_combo, sorted(roles))
    window._set_combo_items(window.cluster_combo, sorted(clusters))
    window._forget_the_notes_the_sheet_carries()

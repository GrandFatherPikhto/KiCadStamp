# gui/fieldstool_window.py
"""
MainWindow — root-sheet picker, an edit panel, PendingChangesDock, and
Apply. Two phases with different KiCad requirements (see
docs/fieldstool.md):

1. Staging (KiCad open): always embedded as a dock inside the main GUI
   (gui/docks/fieldstool_dock.py), which injects its OWN BoardConnection
   (one kipy client, one REQ socket) here — this window never creates or
   polls a connection of its own. The main GUI's single 2s/400ms poll
   drives this window through the public set_connection_status()/
   set_live_selection()/set_live_snapshot() hooks below (Phase 5.1 — a
   second, independent timer on the same connection would interleave
   requests mid-flight on kipy's REQ socket). Because PCB/schematic
   selection cross-probe in KiCad, a selection made in Eeschema reaches
   set_live_selection() too.

   The main GUI's own Components tree (gui/docks/role_cluster_tree.py) can
   ALSO pick a target without any live selection at all — its "Not yet
   applied" mode reads this window's own parsed schematic components
   (self._components, refreshed via _rescan()) and calls
   _on_tree_leaf_picked()/_on_group_picked() below directly. This window
   used to have its own internal tree for that (ComponentTreeDock,
   fieldstool/gui/tree.py) — retired 2026-08-01 in favor of the merged
   tree. A standalone entry point (fieldstool_gui.py, with its own
   BoardConnection and polling timers) existed until 2026-08-02, when it
   was retired as pure duplication of the embedded tab.

   2026-08-03 redesign: Stage writes Role/Cluster straight to the LIVE
   BOARD over IPC (same mechanism RoleClusterTreeDock's Clear all/Delete
   selected already use) — there is no separate JSON staging queue anymore
   (see gui/docks/pending.py's module docstring for why the old
   PendingRegistry was retired: it could drift out of sync with the board,
   found live when Clear all wrote to the board but staged nothing, leaving
   Apply permanently disabled with no way to apply the erasure).
2. Apply (KiCad must be closed): diffs the schematic's last-known Role/
   Cluster (self._components) against the live board's last-known values
   (self._live_snapshot) via gui.docks.pending.compute_pending_edits(), and
   writes exactly that diff via
   kicadstamp.schematic_set_fields.plan_set_edits_for_root() +
   kicadstamp.schematic_editing.write_files() — the same offline pipeline
   the CLI's `set` subcommand uses. Gated by check_kicad_not_running() — if
   KiCad is running, this is an INSTRUCTION dialog, never automated
   (confirmed 2026-08-01: kipy has no app-level quit/save-all call to
   automate this safely, see handoff_2026_08_01_fieldstool.md).

This module used to be fieldstool/gui/main_window.py, in its own package
independent of gui/ — folded in 2026-08-02 (see docs/fieldstool.md)
because it had already become entirely gui/-coupled in practice (always
requires an injected gui.connection.BoardConnection, embedded exclusively
via gui/docks/fieldstool_dock.py). The underlying schematic-editing
library (parsing, splicing, safety guards) stays independent, in
kicadstamp/ — fieldstool_cli.py uses that directly, without any of this.
"""
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                              QFormLayout, QHBoxLayout, QLabel, QListWidget,
                              QMainWindow, QMessageBox, QPushButton, QVBoxLayout,
                              QWidget)

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import FieldsToolError, ValidationError
from kicadstamp.explore import Selected
from kicadstamp.i18n import _
from kicadstamp.schematic_editing import check_kicad_not_running, write_files
from kicadstamp.schematic_set_fields import (plan_ensure_fields_for_root,
                                             plan_set_edits_for_root)

from .docks._common import configure_searchable
from .docks.pending import PendingChangesDock, PendingEdit, compute_pending_edits, edits_to_fields_cfg
from .schema_model import (SchematicComponent, SchematicInstance,
                           load_schematic_components, load_schematic_instances)
from .settings import Settings
from .worker import start_long_op

# Own state file, separate from gui/gui_state.json (gui/settings.py's
# default singleton) — reuses the same Settings class (atomic write,
# point-wise get/set) rather than a second near-identical settings module.
FIELDSTOOL_SETTINGS_PATH = Path(__file__).resolve().parent / "fieldstool_gui_state.json"


class _FieldstoolSettings(Settings):
    @property
    def path(self) -> Path:
        # Resolved at call time (not construction time), same reasoning as
        # gui/settings.py's own module-level singleton — lets tests
        # monkeypatch FIELDSTOOL_SETTINGS_PATH after import.
        return self._path if self._path is not None else FIELDSTOOL_SETTINGS_PATH


settings = _FieldstoolSettings()


class MainWindow(QMainWindow):
    def __init__(self, connection, pending_dock: Optional[PendingChangesDock] = None):
        super().__init__()
        self.setWindowTitle(_("fieldstool"))
        self.resize(420, 700)

        # Always injected by the embedding main GUI (gui/docks/
        # fieldstool_dock.py) — the shared BoardConnection this window is
        # driven through (see module docstring). Never created here.
        self.connection = connection
        self._root_sheet: Optional[Path] = None
        self._current_targets: List[str] = []
        self._components: List[SchematicComponent] = []
        # full-path index (load_schematic_instances): board sheet_path.path ->
        # schematic instance. Built on Rescan alongside self._components and fed
        # to compute_pending_edits so the diff can resolve re-annotated boards
        # per-instance instead of trusting the refdes string (2026-08-08).
        self._path_index: Dict[tuple, SchematicInstance] = {}
        # Last live-board snapshot pushed by the main GUI's ~2s poll (see
        # set_live_snapshot below) — together with self._components, this is
        # the whole input to the schematic-vs-board diff (gui.docks.pending.
        # compute_pending_edits). Empty until the first poll tick.
        self._live_snapshot: List[Selected] = []
        self._pending_edits: List[PendingEdit] = []
        # Set by gui/docks/fieldstool_dock.py when the window is embedded
        # as the fieldstool tab (the only way it runs in production — the
        # standalone fieldstool_gui.py entry point was retired 2026-08-02;
        # see the module docstring). Tests construct MainWindow directly
        # and never assign this, so it must default to None and _rescan()
        # must guard on it (not assume it's already wired) —
        # _restore_last_root_sheet() below can synchronously trigger the
        # FIRST _rescan() during this very __init__, before FieldsToolDock
        # has had a chance to assign the real callback.
        self.on_components_changed: Optional[Callable[[], None]] = None
        # Set by gui/dock_hub.py to the main window's request_refresh() —
        # called right after Stage successfully writes to the live board, so
        # Pending changes' diff picks up the write immediately instead of
        # waiting for a manual Refresh click (the automatic poll tick never
        # refreshes on its own once already connected, see MainWindow._poll's
        # docstring). Same default-None-and-guard pattern as
        # on_components_changed, for the same reason (direct-construction
        # tests never assign it).
        self.on_board_written: Optional[Callable[[], None]] = None

        central = QWidget()
        layout = QVBoxLayout(central)

        root_row = QHBoxLayout()
        self.root_label = QLabel(_("No root sheet picked"))
        self.root_label.setWordWrap(True)
        root_row.addWidget(self.root_label, 1)
        pick_button = QPushButton(_("Pick root sheet..."))
        pick_button.clicked.connect(self._on_pick_root_sheet)
        root_row.addWidget(pick_button)
        rescan_button = QPushButton(_("Rescan"))
        rescan_button.clicked.connect(self._rescan)
        root_row.addWidget(rescan_button)
        layout.addLayout(root_row)

        status_row = QHBoxLayout()
        self.status_label = QLabel(_("Not connected"))
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)

        edit_box = QWidget()
        edit_layout = QVBoxLayout(edit_box)
        self.target_label = QLabel(_("Nothing selected"))
        self.target_label.setWordWrap(True)
        edit_layout.addWidget(self.target_label)
        self.pending_label = QLabel("")
        self.pending_label.setWordWrap(True)
        self.pending_label.setStyleSheet("color: #a60;")
        edit_layout.addWidget(self.pending_label)
        form = QFormLayout()
        self.role_combo = QComboBox()
        # CaseSensitive search-as-you-type (2026-08-04, Denis live: typed
        # "C_Out_Bulk", fieldstool silently turned it back into the
        # existing "C_OUT_BULK") — see configure_searchable()'s docstring;
        # every actual Role/Cluster comparison elsewhere is case-sensitive,
        # so Qt's default case-INsensitive combo completer was quietly
        # substituting a different value than the one actually typed.
        configure_searchable(self.role_combo)
        # Enter in either field stages immediately — same guards as clicking
        # Stage itself (2026-08-04, Denis: "долго Stage жать"). Deliberately
        # NOT on focus-out: losing focus also happens by clicking elsewhere
        # (e.g. a different component in the tree), which would stage an
        # unrelated or half-typed value onto the live board with no explicit
        # user action asking for a write.
        self.role_combo.lineEdit().returnPressed.connect(self._on_stage)
        form.addRow(_("Role:"), self.role_combo)
        self.cluster_combo = QComboBox()
        configure_searchable(self.cluster_combo)
        self.cluster_combo.lineEdit().returnPressed.connect(self._on_stage)
        form.addRow(_("Cluster:"), self.cluster_combo)
        edit_layout.addLayout(form)
        self.stage_button = QPushButton(_("Stage"))
        self.stage_button.setEnabled(False)
        self.stage_button.clicked.connect(self._on_stage)
        edit_layout.addWidget(self.stage_button)
        layout.addWidget(edit_box)

        # Only construct+embed our own PendingChangesDock when we are NOT
        # injected with the shared one (direct/test construction, see module
        # docstring) — the embedding DockHub instead builds ONE shared
        # instance (2026-09-05, plan components_fieldstool_master_detail:
        # hosted as the "Pending" page of the Components master-detail dock's
        # left QTabWidget) and passes it in here, so RoleClusterTreeDock's
        # live-board writes and this window's own Stage write into the same
        # diff view. A self-constructed PendingChangesDock is now a plain
        # QWidget (not a QDockWidget — a QWidget can only have one parent), so
        # it is stacked at the bottom of this window's central layout instead
        # of being added to a QMainWindow dock area.
        self.pending_dock = pending_dock if pending_dock is not None else PendingChangesDock(self)
        if pending_dock is None:
            layout.addWidget(self.pending_dock, 1)
        self.pending_dock.on_apply_clicked = self._on_apply
        self.pending_dock.on_ensure_fields_clicked = self._on_ensure_fields
        self.pending_dock.on_sync_clicked = self._on_sync_from_schematic

        self.setCentralWidget(central)

        self._restore_last_root_sheet()

    # ── Root sheet ───────────────────────────────────────────────────────

    def _restore_last_root_sheet(self) -> None:
        saved = settings.get("root_sheet")
        if saved and Path(saved).is_file():
            self._set_root_sheet(Path(saved))

    def _on_pick_root_sheet(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Pick the project's root .kicad_sch"), "", "KiCad Schematic (*.kicad_sch)")
        if path:
            self._set_root_sheet(Path(path))

    def _set_root_sheet(self, path: Path) -> None:
        self._root_sheet = path
        self.root_label.setText(str(path))
        settings.set("root_sheet", str(path))

        self._rescan()

    def set_project_root_sheet(self, path: Optional[Path]) -> None:
        """Public — wired to FieldsToolDock.set_root_path, itself wired to
        RootMetadataDock's root_changed (see gui/dock_hub.py). path is the
        current root config's root_sheet field, already resolved to an
        absolute path, or None if that file has no root_sheet set (an old
        profile that hasn't adopted the field yet, or no project open).

        A set path always takes over (this is now the authoritative source —
        found live 2026-08-07: root_sheet used to be a single global
        QSettings value with no per-project scoping, so switching projects
        silently kept the PREVIOUS project's root sheet and Pending changes
        matched nothing against a schematic nobody told it had changed).
        None is deliberately a no-op rather than clearing back to "no root
        sheet picked", so a profile that hasn't set root_sheet yet doesn't
        regress fieldstool's old manual-Pick-remembered-in-QSettings
        behavior — add root_sheet to that profile to opt in."""
        if path is not None and path != self._root_sheet:
            self._set_root_sheet(path)

    def _rescan(self) -> None:
        """Explicit action, not auto-polled — the schematic only changes
        when someone saves in Eeschema, not every 2 seconds (same
        "explicit refresh, no silent polling" discipline
        kicadstamp/gui/main_window.py documents for its own tree)."""
        if self._root_sheet is None:
            return
        try:
            components = load_schematic_components(str(self._root_sheet))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, _("Rescan failed"), str(e))
            return
        self._components = components
        try:
            self._path_index = load_schematic_instances(str(self._root_sheet))
        except (OSError, ValueError):
            # Same failure mode as above; the diff then falls back to the
            # refdes join + symbol-uuid guard, which is still safe.
            self._path_index = {}
        roles = {c.role for c in components if c.role}
        clusters = {c.cluster for c in components if c.cluster}
        # Union in whatever's currently on the live board too — a value set
        # there (Stage, Clear all, Cluster tagging, ...) won't be in the
        # schematic on disk until Apply, so a rescan alone would never
        # surface it as a pickable combo item this session.
        for s in self._live_snapshot:
            if s.role:
                roles.add(s.role)
            if s.cluster:
                clusters.add(s.cluster)
        self._set_combo_items(self.role_combo, sorted(roles))
        self._set_combo_items(self.cluster_combo, sorted(clusters))
        self._recompute_pending()

    def _recompute_pending(self) -> None:
        """Recomputes the schematic-vs-board diff and pushes it to
        pending_dock — cheap (no new IPC/file reads, both inputs are
        already-cached: self._components from the last Rescan,
        self._live_snapshot from the last poll tick), so this can safely
        run on every trigger (Rescan, or a fresh live snapshot) without
        the "expensive work belongs behind an explicit action" concern
        that applies to the reads themselves. Also the single place that
        notifies on_components_changed — the main GUI's Components tree in
        "Not yet applied" mode now filters by pending_refs (see below), so
        it needs telling on every trigger this fires from (a fresh poll
        tick/Stage write changes which refs are pending, not just Rescan/
        Apply), not just the two call sites that used to fire it directly."""
        self._pending_edits = compute_pending_edits(
            self._components, self._live_snapshot, self._path_index)
        self.pending_dock.set_edits(self._pending_edits)
        # Refreshes the pending indicator for whatever is CURRENTLY selected
        # on every trigger (a fresh poll tick right after Stage, not just a
        # re-click) — never touches the combos themselves here, since that
        # would clobber in-progress typing; only _prefill_combos_for_refs
        # (an explicit re-pick) is allowed to do that.
        self._update_pending_indicator(self._current_targets)
        if self.on_components_changed:
            self.on_components_changed()

    @property
    def components(self) -> List[SchematicComponent]:
        """Public read-only access to the latest parsed schematic components
        (self._components, refreshed by _rescan()). The main GUI's Components
        tree reads this when embedded (gui/docks/role_cluster_tree.py's
        "Not yet applied" mode) instead of reaching into the private
        attribute — a stable public interface for a value that is refreshed
        wholesale on every rescan and never mutated in place."""
        return self._components

    @property
    def pending_refs(self) -> set:
        """Refs that currently have at least one Role/Cluster discrepancy
        between the schematic and the live board (see _pending_edits) — what
        "Not yet applied" actually means. Added 2026-08-03: that tree mode
        used to list EVERY schematic component regardless of whether it had
        anything outstanding (its original job was just "pick a fieldstool
        target without a live board selection"), which read as a bug once
        Pending changes existed alongside it — Denis, live: components stayed
        listed there after a successful Apply even though nothing was left
        to apply. The tree now filters by this set instead of showing
        dock.components wholesale."""
        return {e.ref for e in self._pending_edits}

    @staticmethod
    def _set_combo_items(combo: QComboBox, items: List[str]) -> None:
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.setCurrentText(current)
        combo.blockSignals(False)

    @staticmethod
    def _add_combo_item_if_missing(combo: QComboBox, value: str) -> None:
        if combo.findText(value) < 0:
            combo.addItem(value)

    # ── Target selection (leaf click / group click / live PCB selection) ──

    def _set_targets(self, refs: List[str]) -> None:
        self._current_targets = list(refs)
        self.target_label.setText(
            _("{count} selected: {refs}").format(count=len(refs), refs=", ".join(sorted(refs)))
            if refs else _("Nothing selected"))
        self.stage_button.setEnabled(bool(refs))

    def _on_tree_leaf_picked(self, refs: List[str]) -> None:
        self._set_targets(refs)
        self._push_selection_to_board(refs)
        self._prefill_combos_for_refs(refs)

    def _on_group_picked(self, field: str, value: str, refs: List[str]) -> None:
        self._set_targets(refs)
        self._push_selection_to_board(refs)
        # _prefill_combos_for_refs already reproduces at least the grouped
        # field's value (every member of a Role/Cluster group shares it, by
        # construction — that's what "group" means here), and additionally
        # fills the OTHER field too if it happens to be uniform across the
        # group as well, so field/value aren't needed for the fill itself.
        self._prefill_combos_for_refs(refs)

    def _prefill_combos_for_refs(self, refs: List[str]) -> None:
        """Role/Cluster combos reflect the picked target(s)' EFFECTIVE
        current value — the live board's value when the live snapshot has
        seen this ref, else the parsed schematic's (self._components) —
        when it's uniform across all of them; cleared when it differs,
        rather than a "(mixed)" placeholder that could get accidentally
        staged as a literal value if Stage is clicked without editing it
        first. Called from every way a target gets picked (tree leaf/group
        click, live board-selection cross-probe) — previously only group
        clicks filled anything at all, and only the one field being grouped
        by.

        FIXED (2026-08-04, Denis live: "прописал роли... но когда кликаю
        эти диоды, ...роль... не видно"): used to read self._components
        unconditionally, i.e. the schematic's last-Rescan value — a target
        already Staged but not yet Applied has its NEW value only on the
        live board, so re-selecting it kept showing the OLD, pre-Stage
        value, forcing edits "blind". The live board already IS the
        accumulated pending state (see gui/docks/pending.py's module
        docstring) — reading it here first makes the combo reflect
        whatever was actually last staged, Applied or not."""
        by_ref = {c.ref: c for c in self._components}
        live_by_ref = {s.ref: s for s in self._live_snapshot}
        picked_refs = [ref for ref in refs if ref in by_ref]
        if not picked_refs:
            return

        def effective(ref: str, field: str) -> str:
            live = live_by_ref.get(ref)
            if live is not None:
                return getattr(live, field) or ""
            return getattr(by_ref[ref], field) or ""

        roles = {effective(ref, "role") for ref in picked_refs}
        clusters = {effective(ref, "cluster") for ref in picked_refs}
        self.role_combo.setCurrentText(next(iter(roles)) if len(roles) == 1 else "")
        self.cluster_combo.setCurrentText(next(iter(clusters)) if len(clusters) == 1 else "")
        self._update_pending_indicator(picked_refs)

    def _update_pending_indicator(self, refs: List[str]) -> None:
        """Small note under the target label naming which of the picked
        refs still differ from the schematic (pending_refs — the same
        Apply diff Pending changes shows) — the counterpart to the prefill
        fix above: the combo now shows the right value, but a value staged
        live and not yet Applied still isn't in the schematic, so it's
        worth flagging rather than looking identical to an already-durable
        one."""
        diverged = sorted(set(refs) & self.pending_refs)
        if diverged:
            self.pending_label.setText(
                _("Not yet applied to schematic: {refs}").format(refs=", ".join(diverged)))
        else:
            self.pending_label.setText("")

    def _push_selection_to_board(self, refs: List[str]) -> None:
        """Target picked (leaf/group click, from either the live-selection
        cross-probe below or — when embedded — the main GUI's Components
        tree in schematic mode) -> live board selection, so the picked
        component(s) are visibly highlighted on the real board too, not
        just in this window's target label. adapter.select_items() only
        sets GUI selection state, not board data (see its docstring in
        kicadstamp/kicad/adapter.py) — doesn't conflict with the shared
        connection otherwise only ever being read from here."""
        if not self.connection.is_connected or not refs:
            return
        # Phase 5.2 — a background long op (Extract/Redraw) holds the shared
        # socket; select_items() here would interleave into its in-flight
        # REQ transaction (this connection IS the shared one when embedded).
        if self.connection.long_op_active:
            return
        adapter = self.connection.board.adapter
        footprints = [fp for fp in (adapter.get_footprint(ref) for ref in refs) if fp is not None]
        if footprints:
            adapter.select_items(footprints)

    def _on_stage(self) -> None:
        """"Stage" now writes Role/Cluster straight to the live board over
        IPC (2026-08-03 redesign, see module docstring) instead of a JSON
        queue — the board itself IS the pending state; Apply's diff picks
        this up once the main GUI's next ~2s poll tick refreshes
        BoardConnection.snapshot (same short lag Clear all/Delete selected
        already have — not instant, but not worth a forced extra IPC
        round-trip here just to shave ~2s off a change you're about to
        Apply anyway)."""
        role = self.role_combo.currentText().strip()
        cluster = self.cluster_combo.currentText().strip()
        if not role and not cluster:
            QMessageBox.warning(self, _("Nothing to stage"),
                                _("Set Role and/or Cluster first."))
            return
        if not self._current_targets:
            return
        if not self.connection.is_connected:
            QMessageBox.warning(self, _("Not connected"), _("Connect to KiCad first."))
            return
        # A background long op (Extract/Redraw) or another poll tick holds
        # the shared socket; writing now would interleave into its
        # in-flight REQ transaction.
        if self.connection.long_op_active:
            return
        payload = {"refs": list(self._current_targets), "role": role, "cluster": cluster}
        self._active_stage_op = start_long_op(
            self.connection, (self.stage_button,), self._run_stage, self._finish_stage,
            self._on_stage_failed, payload)

    def _run_stage(self, payload: dict) -> dict:
        """Worker thread: board IPC only — never touches a widget.

        Skips a (footprint, field) pair the footprint doesn't have, instead
        of letting set_field_value's fatal ValidationError roll back the
        WHOLE batch over one stale/incomplete component (2026-08-04 handoff:
        handoff_2026_08_04_arch_review_handoff_and_cluster_bug.md, "Разрыв
        B" — FB3 missing Cluster used to block Role/Cluster from reaching
        the other 5 targets too) — same has_field guard RoleClusterTreeDock.
        _run_clear already uses for Clear all, just per-field instead of
        per-footprint since Stage may be setting only Role, only Cluster,
        or both."""
        adapter = self.connection.board.adapter
        footprints = [fp for fp in (adapter.get_footprint(ref) for ref in payload["refs"])
                      if fp is not None]
        result = {"error": None, "role": payload["role"], "cluster": payload["cluster"],
                  "skipped": []}
        if not footprints:
            return result
        updates = []
        skipped = []
        for fp in footprints:
            ref = fp.ref if fp.ref else "?"
            for field, value in (("Role", payload["role"]), ("Cluster", payload["cluster"])):
                if not value:
                    continue
                if adapter.has_field(fp, field):
                    updates.append((fp, field, value))
                else:
                    skipped.append(f"{ref} ({field})")
        result["skipped"] = skipped
        if updates:
            touched = len({id(u[0]) for u in updates})
            try:
                adapter.set_field_values_bulk(
                    updates, _("Set Role/Cluster on {count} component(s)").format(count=touched))
            except ValidationError as e:
                return {"error": str(e)}
        return result

    def _finish_stage(self, result: dict) -> None:
        """UI thread: reflect the worker's result into widgets."""
        if result["error"]:
            QMessageBox.critical(self, _("Could not set fields"), result["error"])
            return
        # Reflect a brand-new Role/Cluster value in the dropdown right away —
        # don't make the user hit Rescan just to reuse what they typed a moment ago.
        if result["role"]:
            self._add_combo_item_if_missing(self.role_combo, result["role"])
        if result["cluster"]:
            self._add_combo_item_if_missing(self.cluster_combo, result["cluster"])
        skipped = result.get("skipped") or []
        if skipped:
            QMessageBox.warning(
                self, _("Some fields were skipped"),
                _("These targets have no such field on their footprint yet — nothing was "
                  "written for them (use Ensure fields... below, or add the field by hand, "
                  "then Update PCB from Schematic):\n{refs}").format(refs="\n".join(skipped)))
        if self.on_board_written:
            self.on_board_written()

    def _on_stage_failed(self, message: str) -> None:
        QMessageBox.critical(self, _("Could not set fields"), message)

    # ── Sync from schematic (2026-08-27) ───────────────────────────────────
    #
    # "Sync from schematic..." writes each pending edit's SCHEMATIC value
    # (PendingEdit.old_value) back onto the LIVE board — the automated
    # equivalent of the module docstring's own recommended "revert the field's
    # value on the board itself (Ctrl+Z in KiCad)" workaround, for when that's
    # inconvenient (other unrelated board edits made since). Structurally a
    # mirror of the Stage flow (_on_stage/_run_stage/_finish_stage) — same
    # connection/long-op guards, same has_field skip-per-field discipline,
    # same on_board_written hook — so Pending changes' diff picks up the write
    # on the next poll tick without a forced extra round-trip. No new persisted
    # state: a live IPC write, immediately followed by the diff recomputing
    # itself against the now-matching board.

    def _on_sync_from_schematic(self) -> None:
        """Sync handler — writes the SCHEMATIC's current value back onto the
        live board for every non-mismatched pending edit. Mismatched edits
        (refdes/symbol mismatch) are never touched — same exclusion
        edits_to_fields_cfg() already applies for Apply, for the same reason
        (the refdes doesn't identify the same symbol on both sides)."""
        syncable = [e for e in self._pending_edits if not e.mismatched]
        if not syncable:
            return
        if not self.connection.is_connected:
            QMessageBox.warning(self, _("Not connected"), _("Connect to KiCad first."))
            return
        if self.connection.long_op_active:
            return
        if not self._confirm_sync(syncable):
            return
        payload = {"edits": [(e.ref, e.field, e.old_value) for e in syncable]}
        self._active_sync_op = start_long_op(
            self.connection, (self.pending_dock.sync_button,),
            self._run_sync_from_schematic, self._finish_sync_from_schematic,
            self._on_sync_from_schematic_failed, payload)

    def _confirm_sync(self, edits: List[PendingEdit]) -> bool:
        """Same height-capped QListWidget summary pattern as _confirm_apply
        (Apply), direction reversed: board's CURRENT value -> what's about to
        be written (the schematic's value)."""
        dialog = QDialog(self)
        dialog.setWindowTitle(_("Confirm sync from schematic"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            _("About to write {count} field(s) on the LIVE BOARD, reverting "
              "them to match the schematic:").format(count=len(edits))))
        summary_list = QListWidget()
        for e in edits:
            summary_list.addItem(f"{e.ref}.{e.field}: {e.new_value!r} -> {e.old_value!r}")
        summary_list.setMaximumHeight(300)
        layout.addWidget(summary_list)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _run_sync_from_schematic(self, payload: dict) -> dict:
        """Worker thread: board IPC only — never touches a widget. Same
        has_field skip-guard as _run_stage (a field that vanished from a
        footprint between the diff being computed and this running is skipped,
        not a fatal abort of the whole batch). Skips are split by CAUSE so the
        finish handler can explain each properly: "not_found" = the refdes is
        not on the live board right now (renamed/deleted since the last poll);
        "missing_field" = the footprint exists but has no such field (the far
        more common case — the field is in the schematic but was never created
        on the board footprint)."""
        adapter = self.connection.board.adapter
        result = {"error": None, "not_found": [], "missing_field": []}
        updates = []
        for ref, field, old_value in payload["edits"]:
            fp = adapter.get_footprint(ref)
            if fp is None:
                result["not_found"].append(f"{ref} ({field})")
                continue
            if adapter.has_field(fp, field):
                updates.append((fp, field, old_value))
            else:
                result["missing_field"].append(f"{ref} ({field})")
        if updates:
            touched = len({id(u[0]) for u in updates})
            try:
                adapter.set_field_values_bulk(
                    updates, _("Sync {count} field(s) from schematic").format(count=touched))
            except ValidationError as e:
                return {"error": str(e)}
        return result

    def _finish_sync_from_schematic(self, result: dict) -> None:
        if result["error"]:
            QMessageBox.critical(self, _("Could not sync fields"), result["error"])
            return
        # Two DIFFERENT, correctly-worded warnings — the field-missing case
        # (the overwhelmingly common one) reuses Stage's exact wording, so the
        # two flows agree on what to do (Ensure fields / add by hand + Update
        # PCB from Schematic), while a genuinely-missing ref is its own thing.
        missing_field = result.get("missing_field") or []
        if missing_field:
            QMessageBox.warning(
                self, _("Some fields were skipped"),
                _("These targets have no such field on their footprint yet — nothing "
                  "was written for them (use Ensure fields... below, or add the field "
                  "by hand, then Update PCB from Schematic):\n{refs}")
                .format(refs="\n".join(missing_field)))
        not_found = result.get("not_found") or []
        if not_found:
            QMessageBox.warning(
                self, _("Some targets were not found"),
                _("These refs are not currently on the live board — nothing was "
                  "written for them:\n{refs}").format(refs="\n".join(not_found)))
        # Same "Pending changes never sees a write until told" fix as Stage
        # (2026-08-03) — the automatic poll tick never refreshes on its own
        # once already connected.
        if self.on_board_written:
            self.on_board_written()

    def _on_sync_from_schematic_failed(self, message: str) -> None:
        QMessageBox.critical(self, _("Could not sync fields"), message)

    # ── Live connection (Stage writes Role/Cluster to the board; Apply writes
    #    the schematic — see module docstring) ───────────────────────────────

    def set_connection_status(self, error: Optional[str]) -> None:
        """Public — the single point that reflects the shared connection's
        state in this window's status label. Called from the main GUI's
        single 2s poll (through gui/docks/fieldstool_dock.py) so the label
        stays honest without this window running any connect()/refresh() of
        its own."""
        self.status_label.setText(
            _("Not connected: {error}").format(error=error) if error
            else _("Connected"))

    def set_live_selection(self, refs: List[str]) -> None:
        """Public — the single point that turns a live PCB selection into
        this window's target label/combos. Called from the main GUI's single
        400ms selection tick (through the dock). An empty selection is a
        no-op (keeps the last picked targets — the existing cross-probe
        behavior)."""
        if not refs:
            return
        self._set_targets(sorted(refs))
        self._prefill_combos_for_refs(sorted(refs))

    def set_live_snapshot(self, snapshot: List[Selected]) -> None:
        """Public — the single point that reflects the shared connection's
        live board snapshot here, so the schematic-vs-board diff (Apply)
        stays current without this window polling on its own. Called from
        the main GUI's ~2s poll (through gui/docks/fieldstool_dock.py), same
        pattern as set_connection_status()/set_live_selection()."""
        self._live_snapshot = snapshot
        self._recompute_pending()

    # ── Apply (KiCad must be closed — instruction, not automation) ─────────

    def _confirm_apply(self, report: List) -> bool:
        """Confirmation dialog for Apply — a plain QMessageBox with one line
        of text per changed ref used to grow into an enormous window on a
        real board (hundreds of pending edits), pushing its OK/Cancel
        buttons off-screen. The summary lives in a height-capped QListWidget
        instead — it gets its own scrollbar once the list outgrows that cap,
        so the dialog itself (and its buttons) stays a sane, fixed size
        regardless of how many refs are being applied."""
        dialog = QDialog(self)
        dialog.setWindowTitle(_("Confirm apply"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(_("About to write {count} change(s):").format(count=len(report))))

        summary_list = QListWidget()
        for r in report:
            summary_list.addItem(
                f"{','.join(r.refs)} .{r.field}: {r.old_value!r} -> {r.new_value!r}")
        summary_list.setMaximumHeight(300)
        layout.addWidget(summary_list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        return dialog.exec() == QDialog.DialogCode.Accepted

    def _on_apply(self) -> None:
        if self._root_sheet is None:
            QMessageBox.warning(self, _("No root sheet"), _("Pick a root sheet first."))
            return
        # Apply requires KiCad closed anyway (check below), so there is no
        # point holding on to a stale connection handle while it's gone —
        # drop it before even checking (disconnect() also closes the
        # underlying kipy socket instead of leaving that to the GC, see its
        # docstring); the main window's own poll (already backgrounded, see
        # gui/main_window.py) will reconnect on its own once KiCad becomes
        # available again, no extra "try to come back" logic needed here.
        self.connection.disconnect()
        try:
            check_kicad_not_running(force=False)
        except RuntimeError:
            QMessageBox.information(
                self, _("Close KiCad first"),
                _("KiCad appears to be running. Save your work and close KiCad, then click "
                  "Apply again — this tool never closes KiCad for you (see "
                  "docs/fieldstool.md for why)."))
            return

        # 2026-08-08: refdes/symbol identity mismatches (the same refdes means
        # DIFFERENT symbols on the two sides — different symbol UUIDs) are
        # shown in the Pending changes table but never applied: writing the
        # board value into the wrong schematic symbol is exactly the silent
        # corruption this guard exists to prevent. If everything pending is a
        # mismatch there is nothing safe to do at all.
        mismatched = [e for e in self._pending_edits if e.mismatched]
        applicable = [e for e in self._pending_edits if not e.mismatched]
        if mismatched and not applicable:
            QMessageBox.warning(
                self, _("Nothing to apply"),
                _("{count} ref(s) are refdes/symbol mismatches: the schematic and the "
                  "board disagree on what that refdes is (different symbol UUIDs). "
                  "Re-annotate the schematic / Update PCB from Schematic to re-sync, "
                  "then Apply again.").format(count=len(mismatched)))
            return
        if mismatched:
            QMessageBox.warning(
                self, _("Skipped mismatches"),
                _("{count} ref(s) skipped: the schematic and the board disagree on what "
                  "that refdes is (different symbol UUIDs). Only verified matches are "
                  "applied.").format(count=len(mismatched)))

        fields_cfg = edits_to_fields_cfg(self._pending_edits)
        try:
            edits_by_file, file_texts, report = plan_set_edits_for_root(
                str(self._root_sheet), fields_cfg)
        except FieldsToolError as e:
            QMessageBox.critical(self, _("Cannot apply"), str(e))
            return

        if not report:
            QMessageBox.information(self, _("Nothing to apply"),
                                    _("Every pending value already matches the schematic."))
            return

        if not self._confirm_apply(report):
            return

        written, failed = write_files(edits_by_file, file_texts)
        if failed:
            QMessageBox.critical(self, _("Some files failed"),
                                 _("Restored from .bak: {failed}").format(failed=failed))
        else:
            QMessageBox.information(
                self, _("Applied"),
                _("{count} file(s) written. Reopen KiCad to see the updated schematic — a "
                  "running KiCad process does not hot-reload an externally-modified file.")
                .format(count=len(written)))
            # Reloads self._components from the schematic just written and
            # recomputes the diff against the (unchanged) live snapshot —
            # since the schematic now matches what was just applied, the
            # diff comes out empty and Apply disables itself again.
            self._rescan()

    def _on_ensure_fields(self) -> None:
        """2026-08-04: Denis found FB3 had Role but no Cluster property at
        all in the schematic — a one-off gap, not caused by Clear all/Stage
        (those only ever touch the live board, never .kicad_sch, see
        kicadstamp.kicad.adapter.set_field_value's docstring). This sweeps
        the whole schematic tree once via plan_ensure_fields_for_root(),
        adding an empty Role/Cluster property to every component missing
        one — never touching a component that already has the field,
        whatever its current value. Same KiCad-closed/confirm/write/
        rescan shape as _on_apply above, since it's the same offline
        schematic-editing pipeline underneath; afterwards the user reopens
        KiCad and runs Update PCB from Schematic (F8) themselves — this
        never touches the board."""
        if self._root_sheet is None:
            QMessageBox.warning(self, _("No root sheet"), _("Pick a root sheet first."))
            return
        self.connection.disconnect()
        try:
            check_kicad_not_running(force=False)
        except RuntimeError:
            QMessageBox.information(
                self, _("Close KiCad first"),
                _("KiCad appears to be running. Save your work and close KiCad, then click "
                  "Ensure fields again — this tool never closes KiCad for you (see "
                  "docs/fieldstool.md for why)."))
            return

        try:
            edits_by_file, file_texts, report = plan_ensure_fields_for_root(
                str(self._root_sheet), [ROLE_FIELD_NAME, CLUSTER_FIELD_NAME])
        except FieldsToolError as e:
            QMessageBox.critical(self, _("Cannot ensure fields"), str(e))
            return

        if not report:
            QMessageBox.information(
                self, _("Nothing to add"),
                _("Every component already has {role!r} and {cluster!r}.")
                .format(role=ROLE_FIELD_NAME, cluster=CLUSTER_FIELD_NAME))
            return

        if not self._confirm_apply(report):
            return

        written, failed = write_files(edits_by_file, file_texts)
        if failed:
            QMessageBox.critical(self, _("Some files failed"),
                                 _("Restored from .bak: {failed}").format(failed=failed))
        else:
            QMessageBox.information(
                self, _("Fields added"),
                _("{count} file(s) written. Reopen KiCad and run Update PCB from Schematic "
                  "(F8) to sync the new fields down to the board.")
                .format(count=len(written)))
            self._rescan()

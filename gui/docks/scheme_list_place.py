# gui/docks/scheme_list_place.py
"""
SchemeListPlaceFormWidget — Config-side QView "Place Scheme List..." (P6,
plan_2026_09_05_scheme_list.md §6, design §6).

This is the *placement* side of the Scheme List feature (the Config side of
the same record the read-only SchemeListFormWidget + Reread in
gui/docks/scheme_list.py shows). It turns ONE recorded ``scheme_lists:``
snapshot into a NEW scheme_list-based Entity plus a placement node in an
EXISTING tree (design §6 — "указывает, не копирует": the Entity only carries
``scheme_list:``/``sheet:``, never a copy of the geometry; a placement node
then says WHERE the snapshot's anchor lands).

The page is deliberately a plain QWidget Config right-QView page (the same
shape as SchemeListFormWidget / NetTraceDock / Placer), built once by DockHub
and registered through ConfigTreeDock.add_right_page — NOT a modal dialog and
NOT a third tab of "Instantiate from Cell..." (Denis's anti-pattern §9.1, see
plan §6). The user picks:

  * the Scheme List to place (scheme_list_combo — from cfg.scheme_lists);
  * an optional target sheet (sheet_combo — blank / the record's source_sheet
    == mode "in place"; any other live sheet == twin target, design §5.2);
  * the EXISTING tree to append the node to (tree_combo — from cfg.trees;
    generated tree_instances trees are read-only and excluded) and the parent
    node inside it (parent_combo — DFS over the tree's nodes + a top-level
    sentinel; decision 1 — the node NEVER creates a new tree);
  * the node offset (x_spin/y_spin — TreeNode.xy semantics: offset relative
    to the chosen parent, or to the tree anchor for a top-level node;
    from_selection_check is an opt-in hint that fills them from the live
    board selection, design decision 4);
  * the node rotation (rotation_edit — a real QLineEdit like TreesDock's Node
    form; written ONTO the node at creation, decision 5);
  * the name of the NEW Entity (name_edit — non-empty and unique against
    cfg.entities).

"Place" then writes BOTH records through the Stage-1 core:
``upsert_entity(path, build_scheme_list_entity(...))`` +
``append_tree_child_node(path, tree_name, parent_ref, node_dict)`` — `path`
being the physical file that OWNS the chosen tree (resolved via
find_list_entry_file, config_writer stays core/Qt-free) — and emits
``saved`` for DockHub to refresh the Config tree + TreesDock.

The synchronous ``_do_place()`` test hook (mirror of SchemeListFormWidget's
``_do_reread``) runs the whole write path without any dialog, so Stage 5's
GUI tests can drive validation/placement headlessly.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

from kicadstamp.config import load_config
from kicadstamp.config_writer import append_tree_child_node, upsert_entity
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, set_combo_items, show_message)
from .rename import find_list_entry_file
from .tree_from_selection import (
    build_scheme_list_entity,
    selected_center_mm,
)

logger = logging.getLogger(__name__)

# Combo choices that make no sense as a live place target (they carry no
# reachable record / are regenerated on every load) are excluded up front so a
# wrong pick never fails deep inside link_trees.
_TOP_LEVEL = _("— top level (no parent) —")


def collect_parent_candidates(tree) -> List[tuple[Optional[str], str]]:
    """[(parent_ref | None, display)] for the parent_combo of one Tree: the
    top-level sentinel first (None — the node lands in ``tree.nodes``), then
    EVERY node of the tree, DFS, parent-before-child, indented by depth.

    A node is identified by its ``ref`` (link_trees guarantees a ref appears
    in at most one node of the whole config, so it is unambiguous here too);
    ``name`` is only a display decoration when it differs from the ref.
    Pure/Qt-free — callable from tests and from the combo rebuild."""
    out: List[tuple[Optional[str], str]] = [(None, _TOP_LEVEL)]

    def walk(nodes: list, depth: int) -> None:
        for n in nodes or []:
            label = "  " * depth + str(n.ref)
            if n.name and n.name != n.ref:
                label += f" ({n.name})"
            out.append((n.ref, label))
            walk(n.children, depth + 1)

    walk(tree.nodes if tree is not None else [], 0)
    return out


def placement_node_payload(entity_name: str, x_mm: float, y_mm: float,
                           rotation_deg: float) -> Dict[str, Any]:
    """The trees: node dict that PLACES the new scheme_list Entity — the raw
    dict-node shape config_writer.append_tree_child_node expects
    (ref/kind/xy/rotation, see Stage-1 tests' _placement_node). ``rotation``
    is written at creation (decision 5) — even a 0.0 is explicit here; the
    sexp serializer strips the default 0.0 on the round-trip. Pure/Qt-free."""
    node: Dict[str, Any] = {"ref": entity_name, "kind": "placement",
                            "xy": [x_mm, y_mm], "rotation": float(rotation_deg)}
    return node


class SchemeListPlaceFormWidget(QWidget):
    """Config right-QView "Place Scheme List..." page (P6, plan §6.1). See
    the module docstring for the flow and the fixed decisions (existing tree,
    never a new one; offset == TreeNode.xy; rotation set at creation; Entity
    carries only scheme_list/sheet)."""

    # Fired after a successful Place wrote the Entity + node — DockHub
    # refreshes the Config tree + TreesDock on it (Stage 3).
    saved = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__(main_window)
        self._main_window = main_window
        self._connection = connection if connection is not None else main_window.connection
        self._root_path: Optional[Path] = None
        self._cfg = None
        self._ctx = None
        # The opt-in "from selection" hint reads the live-board selection the
        # DockHub pushes here via set_board_selection (Stage 3 wiring).
        self._selection: list = []
        # Scheme list currently chosen (for the sheet-combo candidates) — the
        # combo's own source_sheet when a record is selected.
        self._selected_source_sheet: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        self.title_label = QLabel(_("Place Scheme List"))
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        form = QFormLayout()

        self.scheme_list_combo = QComboBox()
        configure_searchable(self.scheme_list_combo)
        self.scheme_list_combo.currentTextChanged.connect(
            lambda _t: self._on_scheme_list_changed())
        form.addRow(_("Scheme List:"), self.scheme_list_combo)

        self.sheet_combo = QComboBox()
        configure_searchable(self.sheet_combo)
        self.sheet_combo.setToolTip(
            _("Leave empty to place on the sheet the Scheme List was recorded "
              "from (in place); pick another sheet to place onto its twin."))
        form.addRow(_("Target sheet:"), self.sheet_combo)

        self.tree_combo = QComboBox()
        configure_searchable(self.tree_combo)
        self.tree_combo.currentTextChanged.connect(
            lambda _t: self._rebuild_parent_combo())
        form.addRow(_("Tree:"), self.tree_combo)

        self.parent_combo = QComboBox()
        self.parent_combo.setToolTip(_("Top level = relative to the tree anchor."))
        form.addRow(_("Parent node:"), self.parent_combo)

        root.addLayout(form)

        self.from_selection_check = QCheckBox(
            _("Take from selection (center of the selected group)"))
        self.from_selection_check.setChecked(False)  # opt-in, never auto-assume
        self.from_selection_check.toggled.connect(self._on_from_selection_toggled)
        root.addWidget(self.from_selection_check)

        pos_form = QFormLayout()
        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-10000.0, 10000.0)
        self.x_spin.setDecimals(3)
        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-10000.0, 10000.0)
        self.y_spin.setDecimals(3)
        pos_form.addRow(_("X offset (mm):"), self.x_spin)
        pos_form.addRow(_("Y offset (mm):"), self.y_spin)
        root.addLayout(pos_form)

        entity_form = QFormLayout()
        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText("0")
        entity_form.addRow(_("Rotation (deg):"), self.rotation_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("new Entity name (must be unique)"))
        entity_form.addRow(_("Entity name:"), self.name_edit)
        root.addLayout(entity_form)

        note = QLabel(
            _("Place adds a tree Entity that references the Scheme List by "
              "name — it never copies the recorded geometry and never "
              "touches the live board until you Redraw the tree node."))
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QHBoxLayout()
        self.place_button = QPushButton(_("Place"))
        self.place_button.clicked.connect(self._on_place_clicked)
        buttons.addWidget(self.place_button)
        buttons.addStretch(1)
        root.addLayout(buttons)
        root.addStretch(1)

    # ── Message helper ──────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(text, style, logger)

    # ── Root / cfg wiring ───────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """DockHub root_changed slot — store the new root, reload the cfg for
        the cfg-derived combos and refresh them."""
        self._root_path = path
        self.refresh()

    def preset_scheme_list(self, name: str) -> None:
        """Preselect a Scheme List record by name (the Config-tree context
        menu's "Place..." and the Tools delegate prefill the form with the
        record the user right-clicked / selected — Stage 3). No-op when the
        root/cfg is not loaded yet or the name is unknown (the caller shows
        the page first via set_root_path, so this is normally a live pick)."""
        if not name or self._cfg is None:
            return
        idx = self.scheme_list_combo.findText(name)
        if idx >= 0:
            self.scheme_list_combo.setCurrentIndex(idx)
            self._on_scheme_list_changed()

    def refresh(self) -> None:
        """Public — re-read the config at the current root (if any) and
        refresh the cfg-derived combos (scheme lists, trees, parent list),
        preserving current selections where they still exist. Called on
        root change and by DockHub after a graph change (Stage 3)."""
        self._cfg = None
        self._ctx = None
        if self._root_path is None:
            self._refresh_cfg_combos([])
            return
        try:
            self._cfg, self._ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            self._show_message(
                _("Failed to load the config: {error}").format(error=e),
                _ERROR_STYLE)
            self._refresh_cfg_combos([])
            return
        self._refresh_cfg_combos(list(self._cfg.scheme_lists or []))
        self._on_scheme_list_changed()

    def _refresh_cfg_combos(self, scheme_lists) -> None:
        """Repopulate scheme_list_combo + tree_combo from cfg (preserving the
        current selections where possible). The parent combo is rebuilt for
        the (possibly unchanged) tree selection."""
        scheme_names = [getattr(sl, "name", "") for sl in scheme_lists if getattr(sl, "name", "")]
        if scheme_names:
            set_combo_items(self.scheme_list_combo, scheme_names)
            # A stale selection from a previous root must not silently point at
            # a record that no longer exists — blank it (placeholder shows).
            if self.scheme_list_combo.currentText().strip() not in scheme_names:
                self.scheme_list_combo.clearEditText()
        else:
            self.scheme_list_combo.clear()
            self.scheme_list_combo.setPlaceholderText(
                _("no Scheme Lists recorded — use Tools → Scheme Lists → Record..."))

        tree_names = self._placeable_tree_names()
        if tree_names:
            set_combo_items(self.tree_combo, tree_names)
            if self.tree_combo.currentText().strip() not in tree_names:
                self.tree_combo.clearEditText()
        else:
            self.tree_combo.clear()
            self.tree_combo.setPlaceholderText(
                _("no editable trees in this config"))
        self._rebuild_parent_combo()

    def _placeable_tree_names(self) -> List[str]:
        """Names of cfg.trees a manual node may be appended to — every
        hand-written tree, minus the read-only materialized tree_instances
        (they are regenerated on every load, so a manual child would be
        silently lost; TreesDock marks the same set read-only)."""
        cfg = self._cfg
        if cfg is None:
            return []
        generated = {ti.name for ti in (cfg.tree_instances or [])
                     if getattr(ti, "name", None)}
        return [t.name for t in (cfg.trees or []) if t.name not in generated]

    def _selected_tree(self):
        """The cfg.trees Tree object whose name is currently in tree_combo, or
        None when there is no cfg / no selection."""
        cfg = self._cfg
        if cfg is None:
            return None
        name = self.tree_combo.currentText().strip()
        for t in (cfg.trees or []):
            if t.name == name:
                return t
        return None

    def _rebuild_parent_combo(self) -> None:
        """DFS of the chosen tree (top-level sentinel first) into
        parent_combo, preserving the current selection where it still exists."""
        current = self.parent_combo.currentData()
        self.parent_combo.blockSignals(True)
        self.parent_combo.clear()
        tree = self._selected_tree()
        for ref, label in collect_parent_candidates(tree):
            self.parent_combo.addItem(label, ref)
        if current is not None:
            idx = self.parent_combo.findData(current)
            if idx >= 0:
                self.parent_combo.setCurrentIndex(idx)
        elif self.parent_combo.count():
            self.parent_combo.setCurrentIndex(0)
        self.parent_combo.blockSignals(False)

    def _on_scheme_list_changed(self) -> None:
        """Record selection changed — refresh the target-sheet candidates
        from the record's source_sheet + the live snapshot's sheet segments."""
        self._selected_source_sheet = self._record_source_sheet(
            self.scheme_list_combo.currentText().strip())
        self._rebuild_sheet_combo()

    def _record_source_sheet(self, name: str) -> Optional[str]:
        """The source_sheet of the named scheme list in the loaded cfg (the
        sheet the snapshot was recorded from), or None when unknown."""
        cfg = self._cfg
        if cfg is None or not name:
            return None
        for sl in (cfg.scheme_lists or []):
            if getattr(sl, "name", "") == name:
                return getattr(sl, "source_sheet", None)
        return None

    def _live_sheets(self) -> List[str]:
        """Distinct sheet-instance segments from the current live snapshot,
        sorted — the same source TreesDock._live_sheets uses for its Sheet
        combo (Selected.sheet is a tuple of path segments). connection may be
        a fake without a snapshot attribute (tests) — getattr-guarded."""
        snapshot = getattr(self._connection, "snapshot", None) or []
        return sorted({seg for s in snapshot
                       for seg in (getattr(s, "sheet", None) or ()) if seg})

    def _rebuild_sheet_combo(self) -> None:
        """Target-sheet choices: blank (in place) first, then the record's own
        source_sheet and every live sheet instance (distinct). Picking a value
        equal to source_sheet still means in place (design §5.2 p2), so only a
        genuinely different value is written to the Entity."""
        current = self.sheet_combo.currentText()
        source = self._selected_source_sheet or ""
        candidates: List[str] = []
        for value in ([source] if source else []) + self._live_sheets():
            if value and value not in candidates:
                candidates.append(value)
        choices = [""] + candidates
        set_combo_items(self.sheet_combo, choices)
        # Blank (in place) is the default after a rebuild; a previously chosen
        # sheet survives only when it is still among the candidates.
        if current not in choices:
            self.sheet_combo.setCurrentIndex(0)

    # ── Board-selection hook (from-selection hint) ──────────────────────

    def set_board_selection(self, raw_items, selected_footprints) -> None:
        """Called by DockHub on every selection tick (Stage 3 wiring) — the
        opt-in "from selection" hint reads the current board selection."""
        self._selection = list(selected_footprints or [])

    def _live_adapter(self):
        board = getattr(self._connection, "board", None)
        return getattr(board, "adapter", None) if board is not None else None

    def _on_from_selection_toggled(self, checked: bool) -> None:
        """Opt-in hint (design decision 4): when checked, try to fill x/y from
        the live board (center of the current selection minus the chosen
        parent's live base). On any failure show a warning and fall back to
        manual entry (never a silent partial write)."""
        if not checked:
            return
        offset_mm = self._read_from_selection_offset()
        if offset_mm is None:
            QMessageBox.warning(
                self, _("Take from selection"),
                _("Cannot derive the node offset from the selection — enter "
                  "the X/Y offset manually."))
            self.from_selection_check.setChecked(False)
            return
        self.x_spin.setValue(offset_mm[0])
        self.y_spin.setValue(offset_mm[1])

    def _read_from_selection_offset(self) -> Optional[tuple[float, float]]:
        """(x_offset_mm, y_offset_mm) from the CURRENT board selection center
        minus the chosen parent's live base (tree anchor for top level), or
        None when the live prerequisites are missing (no adapter / no cfg /
        no selection / unresolvable base). Best-effort — a failure is a
        warning, never a crash."""
        adapter = self._live_adapter()
        cfg = self._cfg
        if adapter is None or cfg is None:
            return None
        center = selected_center_mm(self._selection)
        if center is None:
            return None
        tree = self._selected_tree()
        if tree is None:
            return None
        try:
            base = self._live_parent_base_mm(adapter, cfg, tree)
        except Exception:  # noqa: BLE001 — live read, best-effort
            return None
        if base is None:
            return None
        return (center[0] - base[0], center[1] - base[1])

    def _live_parent_base_mm(self, adapter, cfg, tree) -> Optional[tuple[float, float]]:
        """(x_mm, y_mm) live base of the chosen parent — the tree anchor for a
        top-level node, the parent node's own record otherwise (same
        parent-base semantics as TreesDock's "Read current position"; the
        offset is measured from THIS base). None when unresolvable."""
        from kicadstamp.tree_position import (
            _anchor_base_live_position,
            resolve_base_live_position,
        )
        from kicadstamp.utils.units import MM

        sheet_names = dict(getattr(self._ctx, "sheet_names", {}) or {})
        parent_ref = self.parent_combo.currentData()
        if parent_ref is None:
            pos, _rot = _anchor_base_live_position(adapter, cfg, tree, sheet_names)
            return (pos.x / MM, pos.y / MM)
        # Parent is an existing node: resolve its own record (same rules as a
        # real tree node — external refs resolve directly, records via the
        # kind dispatch of resolve_base_live_position).
        from .trees_dock import _resolve_probe_ref
        parent_node = self._find_node_by_ref(tree, parent_ref)
        if parent_node is None:
            return None
        record, _is_external = _resolve_probe_ref(cfg, parent_node.ref,
                                                  parent_node.kind)
        pos = resolve_base_live_position(adapter, cfg, parent_node.ref, record,
                                         {}, sheet_names)
        return (pos.x / MM, pos.y / MM)

    def _find_node_by_ref(self, tree, ref: str):
        """DFS the Tree for the node with `ref` (top-level or nested), or None
        — parent_combo selections must resolve to a real node of the tree."""
        def walk(nodes):
            for n in nodes or []:
                if n.ref == ref:
                    return n
                hit = walk(n.children)
                if hit is not None:
                    return hit
            return None
        return walk(tree.nodes if tree is not None else [])

    # ── The public action ───────────────────────────────────────────────

    def place(self) -> bool:
        """Public entry point (form button + DockHub delegate in Stage 3):
        validate the form, run the Place write, report the result and emit
        saved on success. Returns True when a Place actually happened."""
        problems = self.validate()
        if problems:
            QMessageBox.warning(self, self.windowTitle() or _("Place Scheme List"),
                                problems[0])
            return False
        result = self._do_place()
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return False
        self._show_message(
            _("Placed Scheme List {scheme!r} as Entity {entity!r} under tree "
              "{tree!r}.").format(scheme=result.get("scheme", "?"),
                                  entity=result.get("entity", "?"),
                                  tree=result.get("tree", "?")),
            _SUCCESS_STYLE)
        self.saved.emit()
        return True

    def _on_place_clicked(self) -> None:
        self.place()

    def validate(self) -> List[str]:
        """Localized problems blocking a Place — empty means the form is ready.
        Stage 2 validation set: a Scheme List is chosen; a tree is chosen; a
        parent is chosen (top level is a valid choice); the Entity name is
        non-empty and unique against cfg.entities. Scheme List / tree combos
        are editable+searchable, so a free-typed value must also name a real
        record (a nonexistent reference would be fatal at the next load)."""
        problems: List[str] = []
        cfg = self._cfg
        scheme = self.scheme_list_combo.currentText().strip()
        if not scheme:
            problems.append(_("Select a Scheme List to place."))
        elif cfg is not None and not any(
                getattr(sl, "name", "") == scheme for sl in (cfg.scheme_lists or [])):
            problems.append(_("Unknown Scheme List {name!r}.").format(name=scheme))
        tree_name = self.tree_combo.currentText().strip()
        if not tree_name:
            problems.append(_("Pick a tree to place into."))
        elif cfg is not None and tree_name not in self._placeable_tree_names():
            problems.append(_("Unknown tree {name!r}.").format(name=tree_name))
        # parent_combo always has a selection (top-level sentinel or a node).
        entity_name = self.name_edit.text().strip()
        if not entity_name:
            problems.append(_("Entity name is required."))
        else:
            if cfg is not None and any(e.name == entity_name for e in cfg.entities):
                problems.append(
                    _("An entity named {name!r} already exists.").format(name=entity_name))
        text = self.rotation_edit.text().strip()
        if text:
            try:
                float(text)
            except ValueError:
                problems.append(_("Rotation must be a number."))
        return problems

    # ── Synchronous write path (mirror of SchemeListFormWidget._do_reread) ──

    def _collect_payload(self) -> Optional[Dict[str, Any]]:
        """Snapshot the plain-data payload for the Place write, or None when a
        hard precondition (no root / no cfg) is missing (the message is
        logged — a dialog is the caller's job)."""
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None
        if self._cfg is None:
            self._show_message(_("Failed to load the config."), _ERROR_STYLE)
            return None
        scheme = self.scheme_list_combo.currentText().strip()
        tree_name = self.tree_combo.currentText().strip()
        parent_ref = self.parent_combo.currentData()  # None == top level
        entity_name = self.name_edit.text().strip()
        source = self._selected_source_sheet or ""
        sheet_text = self.sheet_combo.currentText().strip()
        sheet = None if (not sheet_text or sheet_text == source) else sheet_text
        rotation = float(self.rotation_edit.text().strip() or "0.0")
        return {
            "root": str(self._root_path),
            "scheme": scheme,
            "sheet": sheet,
            "tree": tree_name,
            "parent_ref": parent_ref,
            "entity": entity_name,
            "x_mm": self.x_spin.value(),
            "y_mm": self.y_spin.value(),
            "rotation": rotation,
        }

    def _do_place(self) -> Dict[str, Any]:
        """Synchronous Place (no dialogs, for tests): validate, then build
        the Entity dict + the placement node dict, resolve the file that OWNS
        the chosen tree, write both (upsert_entity + append_tree_child_node —
        the Stage-1 core) and return a result dict. {"error": ...} on any
        failure (including validation); {"ok": ...} on success. The caller
        (place()/tests) owns saved.emit()."""
        problems = self.validate()
        if problems:
            return {"error": problems[0]}
        payload = self._collect_payload()
        if payload is None:
            return {"error": _("Place failed — check the log.")}
        path = find_list_entry_file(Path(payload["root"]), "trees",
                                    {"name": payload["tree"]})
        if path is None:
            return {"error": _("Tree {name!r} not found in the config graph.")
                    .format(name=payload["tree"])}
        try:
            entity = build_scheme_list_entity(
                payload["entity"], payload["scheme"], payload["sheet"])
            upsert_entity(Path(path), entity)
            node = placement_node_payload(
                payload["entity"], payload["x_mm"], payload["y_mm"],
                payload["rotation"])
            append_tree_child_node(Path(path), payload["tree"],
                                   payload["parent_ref"], node)
        except (OSError, ValidationError) as e:
            return {"error": _("Place failed: {error}").format(error=e)}
        return {"ok": True,
                "entity": payload["entity"],
                "scheme": payload["scheme"],
                "sheet": payload["sheet"],
                "tree": payload["tree"],
                "parent_ref": payload["parent_ref"],
                "path": str(path)}

# gui/docks/_anchor_origin.py
"""AnchorOriginWidget — the anchor/origin-resolution block copy-pasted
near-identically across PointsDock, RuleDock, ThermalViaArrayDock and
PlacerDock (found live 2026-08-11 reviewing the codebase with Denis: same
origin_mode_combo + anchor_ref/role/sheet/pad/cluster_edit + point_edit
widgets, same _on_origin_mode_changed row-visibility toggle, same Ref-xor-
Role/Point-required validation, in four files). PointsDock's own module
docstring already flagged this as a deliberate deferral ("Deliberately NOT
sharing a widget... extracting a shared Origin widget once this shape is
validated is a natural follow-up") — this is that follow-up, done once the
"7 raz" repetition bar was actually crossed (4 near-identical copies).

Scope is UI + validation ONLY, not YAML-schema mapping — the four callers'
entry shapes genuinely differ, not just their widget subset:
  - Point has real shift_x_mm/shift_y_mm fields.
  - ClonePlacement (PlacerDock) has NO separate shift fields — it reuses
    `xy:` itself to carry the shift in Anchor/Point mode (see placer.py's
    _build_entry_dict). Same UI row, different YAML meaning.
  - Rule/ThermalViaArrayConfig have no shift concept on their own anchor at
    all (Rule's shift lives per-spoke; a thermal via array's origin is
    always the bare anchor).
Forcing one shared build() to also decide the OUTPUT key names would have
meant leaking each model's own YAML shape back into this widget, so build()
returns a small GENERIC dict (mode + whichever of x/y/ref/role/sheet/pad/
cluster/point/kind/shift_x/shift_y are relevant) and every caller keeps its
own few lines mapping that into its own entry dict — same division of
labour PointsDock's _build_entry already had, just with the widget-
construction and validation half factored out.

Modes offered and anchor sub-fields shown are both constructor-configurable
(not every consumer wants all four modes or all five anchor fields —
see MODES/ANCHOR_FIELDS below and each caller's own instantiation)."""
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from ._common import (configure_searchable, parse_float_field, set_combo_items)

logger = logging.getLogger(__name__)

# Every mode this widget knows how to offer, in the order callers may pick
# a subset+order from. Board origin's two kinds are fixed (only PointsDock
# uses this mode, see its own module docstring on 'drill' vs 'grid').
_MODE_LABELS = {
    # "parent" — an EMPTY mode (no field row at all): the "relative to the
    # parent's own base" default state of a node's own_anchor picker
    # (gui/docks/trees_dock.py _NodeDialog Position tab). Reuses the same
    # msgid as the old QRadioButton there so no new catalog string appears.
    "parent": _("Relative to parent"),
    "xy": _("Absolute XY"),
    "anchor": _("Anchor (ref/role)"),
    "point": _("Point"),
    "board_origin": _("Board origin"),
}
_BOARD_ORIGIN_KINDS = [
    ("drill", _("Drill/place (drill/position files, optional for Gerbers)")),
    ("grid", _("Grid (visual only — Place > Set Grid Origin)")),
]


class AnchorOriginWidget(QWidget):
    """One QWidget: the Origin combo plus whichever of its mode-specific
    rows (XY / Anchor / Point / Board origin / the empty "parent" state) and
    the optional Shift row the caller asked for. Visibility of each row
    follows the combo, same _on_mode_changed shape every one of the four
    originals had.

    anchor_fields is a subset of {"sheet", "pad", "cluster"} — Ref/Role
    themselves are always present whenever "anchor" is in modes UNLESS the
    caller opts out of Ref via show_ref=False (a role-only anchor picker,
    e.g. _NodeDialog's own_anchor). mode_labels optionally overrides a
    mode's combo label (keyed by mode name, merged over _MODE_LABELS) for
    consumers that need a consumer-specific wording on a shared mode."""

    modeChanged = pyqtSignal()
    # design §9.4 (plan_2026_09_04_trees_dock_master_detail.md §3c): ONE
    # "any field changed" signal the embedding _touched-tracking forms subscribe
    # to, instead of every form wiring every internal edit/combo by hand. Fires
    # on user edits AND on programmatic loads — the embedding form resets its
    # own _touched flag after a prefill.
    fieldChanged = pyqtSignal()

    def __init__(self, modes: Sequence[str], anchor_fields: Sequence[str] = (),
                shift: bool = False, polar: bool = False, show_ref: bool = True,
                mode_labels: Optional[Mapping[str, str]] = None,
                parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._modes: List[str] = list(modes)
        self._anchor_fields = set(anchor_fields)
        self._shift = shift
        self._polar = polar
        self._show_ref = show_ref
        # Consumer-specific wording wins over the shared _MODE_LABELS defaults.
        self._mode_labels: Dict[str, str] = {**_MODE_LABELS, **(mode_labels or {})}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        origin_form = QFormLayout()
        self.origin_mode_combo = QComboBox()
        self.origin_mode_combo.addItems([self._mode_labels[m] for m in self._modes])
        self.origin_mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        origin_form.addRow(_("Origin:"), self.origin_mode_combo)
        layout.addLayout(origin_form)

        self.x_edit: Optional[QLineEdit] = None
        self.y_edit: Optional[QLineEdit] = None
        self.radius_edit: Optional[QLineEdit] = None
        self.angle_edit: Optional[QLineEdit] = None
        self._polar_combo: Optional[QComboBox] = None
        self._polar_row: Optional[QWidget] = None
        self._radius_angle_box: Optional[QWidget] = None
        self._shift_row: Optional[QWidget] = None
        self.shift_x_edit: Optional[QLineEdit] = None
        self.shift_y_edit: Optional[QLineEdit] = None
        if "xy" in self._modes:
            self._xy_row = QWidget()
            xy_row = QHBoxLayout(self._xy_row)
            xy_row.setContentsMargins(0, 0, 0, 0)
            self.x_edit = QLineEdit()
            self.x_edit.setPlaceholderText(_("X mm"))
            self.y_edit = QLineEdit()
            self.y_edit.setPlaceholderText(_("Y mm"))
            xy_row.addWidget(QLabel(_("X:")))
            xy_row.addWidget(self.x_edit)
            xy_row.addWidget(QLabel(_("Y:")))
            xy_row.addWidget(self.y_edit)
            layout.addWidget(self._xy_row)

            # Optional Cartesian/Polar switch for the XY row. Only consumers
            # that opt in via polar=True (PlacerDock/ClonePlacement) get this;
            # the other docks' xy shape is unchanged. The ACTIVE coordinate pair
            # is shown (X/Y for Cartesian, Radius/Angle for Polar) and the
            # inactive one is HIDDEN, not just disabled (Denis 2026-09-05).
            if self._polar:
                self._polar_row = QWidget()
                polar_row = QHBoxLayout(self._polar_row)
                polar_row.setContentsMargins(0, 0, 0, 0)
                self._polar_combo = QComboBox()
                self._polar_combo.addItems([_("Cartesian"), _("Polar")])
                self._polar_combo.currentIndexChanged.connect(self._update_polar_mode)
                polar_row.addWidget(QLabel(_("Mode:")))
                polar_row.addWidget(self._polar_combo)
                # Radius/Angle live in their own sub-container so the Cartesian/
                # Polar switch can hide/show them while the Mode combo stays.
                self._radius_angle_box = QWidget()
                radius_angle = QHBoxLayout(self._radius_angle_box)
                radius_angle.setContentsMargins(0, 0, 0, 0)
                radius_angle.addWidget(QLabel(_("Radius:")))
                self.radius_edit = QLineEdit()
                self.radius_edit.setPlaceholderText(_("radius mm"))
                radius_angle.addWidget(self.radius_edit)
                radius_angle.addWidget(QLabel(_("Angle:")))
                self.angle_edit = QLineEdit()
                self.angle_edit.setPlaceholderText(_("angle deg"))
                radius_angle.addWidget(self.angle_edit)
                polar_row.addWidget(self._radius_angle_box)
                layout.addWidget(self._polar_row)
                self._update_polar_mode()

        self.anchor_ref_edit: Optional[QLineEdit] = None
        self.anchor_role_edit: Optional[QComboBox] = None
        self.anchor_sheet_edit: Optional[QComboBox] = None
        self.anchor_pad_edit: Optional[QLineEdit] = None
        self.anchor_cluster_edit: Optional[QComboBox] = None
        if "anchor" in self._modes:
            self._anchor_row = QWidget()
            anchor_form = QFormLayout(self._anchor_row)
            anchor_form.setContentsMargins(0, 0, 0, 0)
            if self._show_ref:
                self.anchor_ref_edit = QLineEdit()
                self.anchor_ref_edit.setPlaceholderText(
                    _("e.g. U3 (refdes — mostly avoided in this project)"))
                anchor_form.addRow(_("Ref:"), self.anchor_ref_edit)
            self.anchor_role_edit = QComboBox()
            configure_searchable(self.anchor_role_edit)
            anchor_form.addRow(_("Role:"), self.anchor_role_edit)
            if "sheet" in self._anchor_fields:
                # Searchable combo (2026-08-15, plan
                # plan_2026_08_15_sheet_combo_everywhere.md) — same
                # "populate, don't restrict" picker pattern as
                # anchor_role_edit/anchor_cluster_edit, but sourced from the
                # project's schematic files (set_known_sheets), NOT the live
                # board — Sheet names live in .kicad_sch, not on the board.
                self.anchor_sheet_edit = QComboBox()
                configure_searchable(self.anchor_sheet_edit)
                self.anchor_sheet_edit.lineEdit().setPlaceholderText(
                    _("sheet name (narrows an ambiguous Role, optional)"))
                anchor_form.addRow(_("Sheet:"), self.anchor_sheet_edit)
            if "pad" in self._anchor_fields:
                self.anchor_pad_edit = QLineEdit()
                self.anchor_pad_edit.setPlaceholderText(_("pad (optional)"))
                anchor_form.addRow(_("Pad:"), self.anchor_pad_edit)
            if "cluster" in self._anchor_fields:
                self.anchor_cluster_edit = QComboBox()
                configure_searchable(self.anchor_cluster_edit)
                anchor_form.addRow(_("Anchor cluster:"), self.anchor_cluster_edit)
            layout.addWidget(self._anchor_row)

        self.point_edit: Optional[QComboBox] = None
        if "point" in self._modes:
            self._point_row = QWidget()
            point_form = QFormLayout(self._point_row)
            point_form.setContentsMargins(0, 0, 0, 0)
            self.point_edit = QComboBox()
            configure_searchable(self.point_edit)
            point_form.addRow(_("Point:"), self.point_edit)
            layout.addWidget(self._point_row)

        self.board_origin_kind_combo: Optional[QComboBox] = None
        if "board_origin" in self._modes:
            self._board_origin_row = QWidget()
            board_origin_form = QFormLayout(self._board_origin_row)
            board_origin_form.setContentsMargins(0, 0, 0, 0)
            self.board_origin_kind_combo = QComboBox()
            for kind, label in _BOARD_ORIGIN_KINDS:
                self.board_origin_kind_combo.addItem(label, kind)
            board_origin_form.addRow(_("Kind:"), self.board_origin_kind_combo)
            layout.addWidget(self._board_origin_row)

        if self._shift:
            self._shift_row = QWidget()
            shift_row = QHBoxLayout(self._shift_row)
            shift_row.setContentsMargins(0, 0, 0, 0)
            self.shift_x_edit = QLineEdit()
            self.shift_x_edit.setPlaceholderText(_("shift X mm (0)"))
            self.shift_y_edit = QLineEdit()
            self.shift_y_edit.setPlaceholderText(_("shift Y mm (0)"))
            shift_row.addWidget(QLabel(_("Shift X:")))
            shift_row.addWidget(self.shift_x_edit)
            shift_row.addWidget(QLabel(_("Shift Y:")))
            shift_row.addWidget(self.shift_y_edit)
            layout.addWidget(self._shift_row)

        self._on_mode_changed()
        # design §9.4: after every internal field exists, connect them all to
        # fieldChanged — the shared "the user touched something" source.
        self._wire_field_changed()

    def _wire_field_changed(self) -> None:
        """Connect every internal QLineEdit/QComboBox to fieldChanged. Runs
        after the whole widget is built so no handler fires mid-construction;
        programmatic loads emit too, which is fine — the embedding form resets
        its _touched flag after it prefills."""
        for edit in self.findChildren(QLineEdit):
            edit.textChanged.connect(self._emit_field_changed)
        for combo in self.findChildren(QComboBox):
            combo.currentTextChanged.connect(self._emit_field_changed)

    def _emit_field_changed(self, *_args) -> None:
        self.fieldChanged.emit()

    # ── Mode visibility ──────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._modes[self.origin_mode_combo.currentIndex()]

    def _on_mode_changed(self) -> None:
        mode = self.mode
        polar = (self._polar and self._polar_combo is not None
                 and self._polar_combo.currentIndex() == 1)
        if "xy" in self._modes:
            # Cartesian absolute pair — X/Y shown only when xy AND Cartesian;
            # in Polar mode the same slot is taken by the radius/angle pair.
            self._xy_row.setVisible(mode == "xy" and not polar)
        if "anchor" in self._modes:
            self._anchor_row.setVisible(mode == "anchor")
        if "point" in self._modes:
            self._point_row.setVisible(mode == "point")
        if "board_origin" in self._modes:
            self._board_origin_row.setVisible(mode == "board_origin")
        if self._polar and self._polar_row is not None:
            # The Cartesian/Polar switch applies to the OFFSET — in xy mode it
            # toggles the literal x/y vs radius/angle; in anchor/point mode it
            # toggles the SHIFT vs a polar offset (ClonePlacement's anchor can
            # carry a polar offset — was silently dropped by load_placement
            # before, 2026-08-12 Group 2 fix). Only the ACTIVE coordinate pair
            # is shown; the inactive one is hidden, not just disabled.
            self._polar_row.setVisible(mode == "xy" or mode in ("anchor", "point"))
            if self._radius_angle_box is not None:
                self._radius_angle_box.setVisible(polar)
        if self._shift and self._shift_row is not None:
            # Cartesian offset at an anchor/point is the Shift row; Polar mode
            # hides it in favour of the radius/angle pair.
            self._shift_row.setVisible(mode != "xy" and not polar)
        self.modeChanged.emit()

    def _update_polar_mode(self) -> None:
        """Cartesian/Polar field-toggle (2026-09-05): show ONLY the active
        coordinate pair — X/Y (xy mode) or Shift X/Y (anchor/point) for
        Cartesian, Radius/Angle for Polar — and hide the inactive pair (no
        longer just disabling it)."""
        if not self._polar or self._polar_combo is None:
            return
        polar = self._polar_combo.currentIndex() == 1
        mode = self.mode
        if "xy" in self._modes and self._xy_row is not None:
            self._xy_row.setVisible(mode == "xy" and not polar)
        if self._radius_angle_box is not None:
            self._radius_angle_box.setVisible(polar)
        if self._shift and self._shift_row is not None:
            self._shift_row.setVisible(mode != "xy" and not polar)
        # Keep enabled state consistent for programmatic access (a hidden field
        # is not focusable anyway): only the shown pair stays enabled.
        if polar:
            for edit in (self.x_edit, self.y_edit,
                         self.shift_x_edit, self.shift_y_edit):
                if edit is not None:
                    edit.setEnabled(False)
            for edit in (self.radius_edit, self.angle_edit):
                if edit is not None:
                    edit.setEnabled(True)
        else:
            for edit in (self.radius_edit, self.angle_edit):
                if edit is not None:
                    edit.setEnabled(False)
            for edit in (self.x_edit, self.y_edit,
                         self.shift_x_edit, self.shift_y_edit):
                if edit is not None:
                    edit.setEnabled(True)

    # ── Populating combos from the live board / other files ───────────────

    def set_known_roles(self, roles: Sequence[str], clusters: Sequence[str]) -> None:
        """Same "populate from the live board" pattern every original
        refresh_known_roles used — called at the same ~2s poll cadence via
        DockHub.push_snapshot, now routed through the widget instead of each
        dock's own two set_combo_items calls."""
        if self.anchor_role_edit is not None:
            set_combo_items(self.anchor_role_edit, list(roles))
        if self.anchor_cluster_edit is not None:
            set_combo_items(self.anchor_cluster_edit, list(clusters))

    def set_point_names(self, names: Sequence[str]) -> None:
        if self.point_edit is not None:
            set_combo_items(self.point_edit, list(names))

    def set_known_sheets(self, sheets: Sequence[str]) -> None:
        """Same "populate, don't restrict" pattern as set_known_roles — sheet
        names come from the project's schematic files (RuntimeContext.
        sheet_names, built in config/loader.py via schematic_dir/
        schematic_files), refreshed on root-file change, not the ~2s board
        poll Cluster/Role ride (see collect_all_sheet_names,
        gui/docks/rename.py)."""
        if self.anchor_sheet_edit is not None:
            set_combo_items(self.anchor_sheet_edit, list(sheets))

    # ── Numeric parsing (moved in from each caller's own copy) ────────────

    @staticmethod
    def _parse_required_float(edit: QLineEdit, label: str) -> Tuple[Optional[float], Optional[str]]:
        ok, value = parse_float_field(edit)
        if not ok:
            return None, _("{label}: {text!r} is not a number.").format(label=label, text=edit.text().strip())
        if value is None:
            return None, _("{label} is required.").format(label=label)
        return value, None

    @staticmethod
    def _parse_optional_float(edit: QLineEdit, label: str, default: float) -> Tuple[Optional[float], Optional[str]]:
        ok, value = parse_float_field(edit)
        if not ok:
            return None, _("{label}: {text!r} is not a number.").format(label=label, text=edit.text().strip())
        return default if value is None else value, None

    # ── Build: validate the current form, return a generic field dict ─────

    def build(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """(fields, error) — error is None on success. fields only ever
        carries GENERIC keys (mode/x/y/ref/role/sheet/pad/cluster/point/
        kind/shift_x/shift_y), never a model's own YAML key names — see
        module docstring on why that mapping stays with each caller.
        Optional text fields (ref/role/sheet/pad/cluster) are present only
        if non-blank, same "if x:" omission the originals used."""
        mode = self.mode
        fields: Dict[str, Any] = {"mode": mode}

        if mode == "parent":
            # Empty mode — nothing to validate or collect; the caller treats
            # it as "relative to the parent's own base" (own_anchor=None).
            return fields, None

        if mode == "xy":
            if self._polar and self._polar_combo is not None and self._polar_combo.currentIndex() == 1:
                radius, err = self._parse_required_float(self.radius_edit, _("Radius"))
                if err:
                    return None, err
                angle, err = self._parse_required_float(self.angle_edit, _("Angle"))
                if err:
                    return None, err
                fields["radius"] = radius
                fields["angle"] = angle
                return fields, None
            x, err = self._parse_required_float(self.x_edit, _("X"))
            if err:
                return None, err
            y, err = self._parse_required_float(self.y_edit, _("Y"))
            if err:
                return None, err
            fields["x"] = x
            fields["y"] = y
            return fields, None

        if mode == "anchor":
            # show_ref=False (role-only consumers) never has a Ref field at
            # all — read text only when the widget exists (it is None then).
            ref = self.anchor_ref_edit.text().strip() if self.anchor_ref_edit is not None else ""
            role = self.anchor_role_edit.currentText().strip()
            if not ref and not role:
                if self.anchor_ref_edit is None:
                    # Role-only picker: no Ref to fall back on, Role is the
                    # sole required field (design §3.2 — one wording, no
                    # duplication between widget and _NodeDialog callers).
                    return None, _("Anchor: Role is required.")
                return None, _("Anchor: set Ref or Role.")
            if ref and role:
                return None, _("Anchor: Ref and Role are mutually exclusive — set one.")
            if ref:
                fields["ref"] = ref
            else:
                fields["role"] = role
                if self.anchor_sheet_edit is not None:
                    sheet = self.anchor_sheet_edit.currentText().strip()
                    if sheet:
                        fields["sheet"] = sheet
            if self.anchor_pad_edit is not None:
                pad = self.anchor_pad_edit.text().strip()
                if pad:
                    fields["pad"] = pad
            if self.anchor_cluster_edit is not None:
                cluster = self.anchor_cluster_edit.currentText().strip()
                if cluster:
                    fields["cluster"] = cluster
        elif mode == "point":
            point = self.point_edit.currentText().strip()
            if not point:
                return None, _("Point: name is required.")
            fields["point"] = point
        else:  # board_origin
            fields["kind"] = self.board_origin_kind_combo.currentData()

        if self._shift:
            if self._polar and self._polar_combo is not None and self._polar_combo.currentIndex() == 1:
                # Polar OFFSET (anchor/point mode) — radius/angle instead of
                # shift (2026-08-12, Group 2 fix).
                radius, err = self._parse_required_float(self.radius_edit, _("Radius"))
                if err:
                    return None, err
                angle, err = self._parse_required_float(self.angle_edit, _("Angle"))
                if err:
                    return None, err
                fields["radius"] = radius
                fields["angle"] = angle
            else:
                shift_x, err = self._parse_optional_float(self.shift_x_edit, _("Shift X"), default=0.0)
                if err:
                    return None, err
                shift_y, err = self._parse_optional_float(self.shift_y_edit, _("Shift Y"), default=0.0)
                if err:
                    return None, err
                fields["shift_x"] = shift_x
                fields["shift_y"] = shift_y

        return fields, None

    # ── Load: reverse of build(), populate the widgets from known values ──

    def load(self, *, mode: Optional[str] = None, ref: str = "", role: str = "", sheet: str = "",
             pad: str = "", cluster: str = "", point: str = "", x: Optional[float] = None,
             y: Optional[float] = None, kind: Optional[str] = None, shift_x: Optional[float] = None,
             shift_y: Optional[float] = None, polar: Optional[bool] = None,
             radius: Optional[float] = None, angle: Optional[float] = None) -> None:
        """Sets the mode combo + every present field; blank/default for
        anything not passed — used both for "load an already-saved entry"
        (caller extracts its own YAML keys, passes them through by generic
        name) and for new_xxx()'s "reset to blank" (called with no kwargs
        beyond mode)."""
        self.origin_mode_combo.setCurrentIndex(self._modes.index(mode) if mode in self._modes else 0)
        if self._polar_combo is not None:
            self._polar_combo.setCurrentIndex(1 if (polar or radius is not None) else 0)
        if self.radius_edit is not None:
            self.radius_edit.setText(str(radius) if radius is not None else "")
        if self.angle_edit is not None:
            self.angle_edit.setText(str(angle) if angle is not None else "")
        if self.x_edit is not None:
            self.x_edit.setText(str(x) if x is not None else "")
        if self.y_edit is not None:
            self.y_edit.setText(str(y) if y is not None else "")
        if self.anchor_ref_edit is not None:
            self.anchor_ref_edit.setText(ref)
        if self.anchor_role_edit is not None:
            self.anchor_role_edit.setCurrentText(role)
        if self.anchor_sheet_edit is not None:
            self.anchor_sheet_edit.setCurrentText(sheet)
        if self.anchor_pad_edit is not None:
            self.anchor_pad_edit.setText(pad)
        if self.anchor_cluster_edit is not None:
            self.anchor_cluster_edit.setCurrentText(cluster)
        if self.point_edit is not None:
            self.point_edit.setCurrentText(point)
        if self.board_origin_kind_combo is not None:
            idx = self.board_origin_kind_combo.findData(kind) if kind is not None else -1
            self.board_origin_kind_combo.setCurrentIndex(idx if idx >= 0 else 0)
        # "is not None", not truthy — PlacerDock always passes the real
        # xy[i] float (never None, may legitimately be 0.0 and must still
        # render as "0.0"); PointsDock instead passes None itself for an
        # absent/zero shift_x_mm (its own save path omits zero shifts), so
        # both callers' own blank-vs-zero conventions survive unchanged.
        if self.shift_x_edit is not None:
            self.shift_x_edit.setText(str(shift_x) if shift_x is not None else "")
        if self.shift_y_edit is not None:
            self.shift_y_edit.setText(str(shift_y) if shift_y is not None else "")
        self._on_mode_changed()

    def clear(self) -> None:
        self.load()

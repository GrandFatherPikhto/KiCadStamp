# gui/docks/points.py
"""
PointsDock — edits a named, reusable `points:` entry (kicadstamp/config/
points.py's Point dataclass): a base position (Absolute XY / Anchor ref-
role, narrowed by Sheet/Pad/Cluster / chain to another named Point / Board
origin) plus an optional flat mm shift. Requested live 2026-08-05 (Denis: "а
можем мы соорудить панельку с именованными поинтами?") after noticing how
much of PlacerDock's own Origin widget (gui/docks/placer.py) already
matches Point's shape field-for-field — Absolute XY/Anchor(ref/role)/Point
are exactly Point's mutually exclusive anchor "bases", and shift_x_mm/
shift_y_mm is exactly PlacerDock's own Anchor/Point-mode shift.

Board origin (added 2026-08-06, Denis: "точка 0,0 -- это левый верхний угол
листа, никак не origin") reads the board's own LIVE origin marker via kipy
(adapter.get_board_origin) instead of a config-file xy: literal — 'drill'
(Place > Drill/Place Origin, the auxiliary axis drill/position files are
always relative to, and Gerbers optionally via their own plot option) or
'grid' (Place > Set Grid Origin, visual-only, no exported file uses it).
Same "coordinate, no footprint" shape as Absolute XY (see Rule/
ThermalViaArrayConfig still refusing to anchor to it directly — they need a
pad on a live component, not a bare coordinate), but unlike Absolute XY it
is read live rather than guessed at as a literal number, and CAN carry a
shift on top (it isn't a config literal you could "just edit instead").

Deliberately NOT sharing a widget with PlacerDock/ThermalViaArrayDock yet —
first pass, built to be used and judged by hand before generalizing
anything (Denis: "надо руками пощупать"); PlacerDock/ThermalViaArrayDock's
own Anchor rows are untouched here, including their still-missing Sheet
field (see anchor_sheet below) — extracting a shared Origin widget once
this shape is validated is a natural follow-up, not done in this pass.

anchor_sheet (Denis, 2026-08-05: "да, нужен anchor_sheet в этой панели") is
a searchable combo autocompleted from the project's schematic files
(kicadstamp.sheet_names.build_sheet_name_map(), built inside config/
loader.py's load_config — see collect_all_sheet_names, gui/docks/rename.py)
— NOT a whitelist, same "populate, don't restrict" picker as anchor_role.
Closed 2026-08-15 (the earlier deferral was real: the map DOES need the
PROJECT's schematic_dir/schematic_files, a second file dependency distinct
from "which file do points: get written to" — this dock's own
set_target_file; wired now via set_root_path(), the same root_changed
trigger Point-chain autocomplete already uses — see
plan_2026_08_15_sheet_combo_everywhere.md).

Point-chain (`anchor_point`) IS autocompleted, though — from the currently
targeted file's own points: keys (self-contained, no cross-dock
dependency), closing the "points:-name autocomplete" gap docs/gui.md's
Placer section has flagged since 2026-08-03.

Save writes via merge_write(section="points") — points: is a DICT section
(keyed by name), unlike thermal_via_arrays:/clone_placements: (list
sections upsert_list_entry matches by an inline `name` field) — see
_common.py's merge_write docstring for the section= dict-merge shape this
uses, same as the retired Extract dock's cells:/extract_profiles: writes.

Resolve (Denis: "надо руками пощупать" — a way to check where a point
CURRENTLY computes to, before Save, without moving anything on the board —
Points have no physical effect of their own, unlike PlacerDock's Redraw)
calls kicadstamp.placement.services.point_resolver.resolve_point_chain(),
a standalone alternative to the full apply-time dependency-order pass
(dependency_order.py) that only walks this file's points: graph — so an
unrelated broken rule/clone_placement elsewhere in the same config file
can never block previewing an unrelated point (deliberately more lenient
than load_config()'s all-or-nothing validation). Resolved position shows
as text; if the point resolved through a live footprint (no shift
applied — see ResolvedPoint's own docstring), that footprint is also
selected on the live board via adapter.select_items(), the same highlight
mechanism the Components tree's own "click a node -> highlight on board"
already uses (kicadstamp/kicad/adapter.py's select_items). A bare xy point,
or one with a shift applied, has no footprint to highlight (the position
text says so) — which is exactly the gap the overlay circle closes.

Overlay circles (2026-09-11, plan plan_2026_09_11_points_markers.md, Ж).
A resolved point is ALSO drawn as a marker circle on the overlay user layer,
through gui/overlay_markers.py's OWNER — the key `point/<name>` was reserved
for this consumer by that owner's key shape (Е.2.1), so no new kind of key
is invented here. Resolve draws it on the WORKER that already resolves the
point (never IPC on the UI thread), and the owner's ensure_marker is
idempotent BY KEY: resolving the same point again MOVES its one circle
instead of stacking a second one (the "мы это так и не исправили"
complaint). The select_items highlight is untouched and now works TOGETHER
with the circle.

The single "Show all points" / "Hide all points" toggle button (Ж.2.2) does
the same for the WHOLE flat list: every point that resolves gets its circle,
a point that does NOT resolve is skipped with a Log line naming it (one bad
point never cancels the rest), and the second press calls the owner's
remove_namespace("point") — our keys only, shapes of other namespaces are
never touched. The toggle's state is READ FROM THE MAP (is there any
`point/...` key), never a GUI flag that could drift from reality. No live
board -> one Log line and the button does nothing; a circle is a
visualisation and never raises and never shows a modal (Е.2.6).

Cleanup (Ж.2.3): renaming a point through this dock drops the key of the
previous name, and a root switch (set_root_path) clears the whole point
namespace. A point deleted or renamed SOMEWHERE ELSE (the config tree)
leaves its circle behind as an orphan — deliberately accepted for this pass:
the owner's reconcile counts orphans on the next connect, and "Show all
points" re-places the circles. Circles are pure visualisation and are never
written to the config.

Read from board (2026-09-12, plan plan_2026_09_12_point_read_from_marker.md,
К): a circle is DRAGGABLE in KiCad (overlay_markers.owner.read_position is the
"where did the user drag it" call the cell-anchor editor already makes), so a
point's position can be set by hand instead of by numbers — the button reads
the circle's centre back and fills the FORM only (К.2.2: the read never
writes the config, Save stays explicit, like every other field of this dock).
Where the number goes is decided by what the point IS (К.1), never by the
mode the user happens to look at: a literal-xy point gets its xy REPLACED, an
anchored point (anchor_ref/anchor_role/anchor_point/anchor_origin) gets its
SHIFT — never both, because a shift on top of xy is fatal (Point's own
docstring: "just edit the literal coordinate instead"). The new shift is
recomputed from the BASE — base = resolved position − old shift, so
new_shift = dragged position − base — which is what keeps a repeated read
from making the point creep (the shift is board-absolute mm, not a delta).
No circle for this point -> one Log line telling the user to Resolve first,
never a silent draw: the user must see where the numbers came from (К.2.1).
The whole read runs on the same worker path as Resolve (К.2.3).

sheet_names is passed as {} for now (same cross-dock-dependency
deferral as anchor_sheet's own free-text field above) — anchor_sheet is
saved correctly into the YAML either way, it just won't narrow ambiguity
in THIS panel's own Resolve preview yet (a real `apply`/CLI run already
builds sheet_names properly from the project's schematic_dir).
"""
import logging
import math
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLineEdit,
                              QPushButton, QVBoxLayout, QWidget)

from kicadstamp.config import load_point
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.placement.services.point_resolver import resolve_point_chain
from kicadstamp.utils.units import MM

from .. import board_overlay, overlay_markers
from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE, combo_line_edits, display_path,
                      merge_write, own_line_edits, show_message)
from .rename import collect_all_sheet_names, collect_section_entries, find_dict_entry_file

logger = logging.getLogger(__name__)

# ── Overlay circles for the flat point list (Ж, 2026-09-11) ──────────────
# `point/<name>` was already reserved by the overlay owner's key shape
# (gui/overlay_markers.py, Е.2.1) — this dock is that consumer. The string is
# built here from the PUBLIC namespace constant: the owner exposes a key
# builder only for the cell-anchor consumer (cell_anchor_key) and treats every
# key as an opaque string otherwise.
_POINT_KEY_PREFIX = overlay_markers.NS_POINT + "/"


def _point_key(name: str) -> str:
    """The overlay key one named point's circle owns."""
    return _POINT_KEY_PREFIX + str(name)


def _nm_to_mm_exact(nm: int) -> float:
    """The board-mm float whose OWN nanometre round trip — `int(mm * MM)`, the
    conversion point_resolver.resolve_point performs — returns exactly `nm`.

    A plain `nm / MM` cannot promise that: for about 1% of nanometre counts the
    product lands a hair BELOW the integer and the resolver's truncating
    `int()` then returns one nanometre less (measured: ~23k of 2M samples in
    [-2m, +2m] mm). A dragged circle carries a whole nanometre count, and К.5.3
    is exactly "read, save, Resolve again, land on the same nanometre", so the
    value written to the form must survive that round trip by construction.
    Nudging by at most a couple of ULPs (≈1e-7 nm) never moves the value to a
    different nanometre."""
    value = nm / MM
    for _ in range(4):
        if int(value * MM) == nm:
            return value
        value = math.nextafter(value, math.inf)
    return value


def _ensure_point_marker(adapter, name: str, x_mm: float, y_mm: float) -> Optional[str]:
    """WORKER-side: make the overlay owner give `name` exactly ONE circle at
    (x_mm, y_mm) — idempotent by key, so a repeat call MOVES that circle
    (Е.2.2). Never raises: a circle is a visualisation and must not fail the
    operation that asked for it (Е.2.6), so a disabled overlay layer or a
    dead board read degrades to a Log line."""
    try:
        return overlay_markers.owner.ensure_marker(
            adapter, _point_key(name), x_mm, y_mm)
    except Exception:  # noqa: BLE001 — drawing must never break the caller
        logger.warning(
            _("Point {name!r}: marker not drawn — the overlay layer {layer!r} "
              "is not enabled on this board, or the board read failed.")
            .format(name=name, layer=board_overlay.overlay_layer_name()))
        return None


class PointsDock(QWidget):
    """Edits a named `points:` entry — hosted since 2026-09-01 (plan
    plan_2026_09_01_points_dialog.md) in the standalone non-modal PointsDialog
    (gui/docks/points_dialog.py), same "plain QWidget, not its own
    QDockWidget" shape as ThermalViaArrayDock, see its module docstring. The
    single live instance is owned by DockHub."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Points category (see gui/dock_hub.py), same as Placer/ThermalVia/Extract.
    saved = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__(main_window)
        self._main_window = main_window
        self._connection = connection if connection is not None else main_window.connection
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        # The overlay map is owned by gui/overlay_markers (the map itself is
        # the shared gui_state.json entry); this dock only asks it for KEY
        # presence and for its idempotent ensure_*/forget_* operations.
        self._overlay = overlay_markers.owner
        # The name currently loaded in the form — lets Save detect a RENAME
        # and drop the previous name's circle (Ж.2.3).
        self._loaded_name: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        name_form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("name (referenced by anchor_point elsewhere)"))
        name_form.addRow(_("Name:"), self.name_edit)
        layout.addLayout(name_form)

        comment_form = QFormLayout()
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(_("optional free-form note"))
        comment_form.addRow(_("Comment:"), self.comment_edit)
        layout.addLayout(comment_form)

        self.origin_widget = AnchorOriginWidget(
            modes=["xy", "anchor", "point", "board_origin"],
            anchor_fields=["sheet", "pad", "cluster"], shift=True)
        layout.addWidget(self.origin_widget)
        # Aliases onto the shared widget's own sub-widgets — kept so
        # existing tests/call sites that poke fields directly keep working.
        self.origin_mode_combo = self.origin_widget.origin_mode_combo
        self.x_edit = self.origin_widget.x_edit
        self.y_edit = self.origin_widget.y_edit
        self.anchor_ref_edit = self.origin_widget.anchor_ref_edit
        self.anchor_role_edit = self.origin_widget.anchor_role_edit
        self.anchor_sheet_edit = self.origin_widget.anchor_sheet_edit
        self.anchor_pad_edit = self.origin_widget.anchor_pad_edit
        self.anchor_cluster_edit = self.origin_widget.anchor_cluster_edit
        self.point_edit = self.origin_widget.point_edit
        self.board_origin_kind_combo = self.origin_widget.board_origin_kind_combo
        self.shift_x_edit = self.origin_widget.shift_x_edit
        self.shift_y_edit = self.origin_widget.shift_y_edit

        button_row = QHBoxLayout()
        self.resolve_button = QPushButton(_("Resolve"))
        self.resolve_button.clicked.connect(self._on_resolve)
        button_row.addWidget(self.resolve_button)
        # К: "the circle is draggable" — read its centre back into the form.
        self.read_position_button = QPushButton(_("Read from board"))
        self.read_position_button.setToolTip(
            _("Fill the position from this point's marker circle — drag the "
              "circle in KiCad first (Resolve places it)."))
        self.read_position_button.clicked.connect(self._on_read_position)
        button_row.addWidget(self.read_position_button)
        # Ж.2.2: ONE toggle button for the whole list (space in the dock is
        # expensive). Its label follows the map — see _refresh_show_all_button.
        self.show_all_button = QPushButton(_("Show all points"))
        self.show_all_button.clicked.connect(self._on_show_all_points)
        button_row.addWidget(self.show_all_button)
        layout.addLayout(button_row)

        # 2026-09-01 (plan project_save_model): no per-dock Save button — a
        # field's commit point (blur/Enter for a line edit, a combo pick)
        # auto-stages the current Point into the working set; File > Save
        # commits it to disk. _loading guards programmatic form population.
        self._loading = False
        for w in own_line_edits(self):
            w.editingFinished.connect(self._autostage)
        # Load-bearing: currentIndexChanged does NOT fire for free-typed text,
        # so this is the only signal that reaches _autostage for a hand-typed
        # role/cluster. editingFinished only — see combo_line_edits.
        for w in combo_line_edits(self):
            w.editingFinished.connect(self._autostage)
        for w in self.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._autostage)

        # The toggle's starting label follows whatever the map already owns —
        # the map lives in gui_state.json and survives a GUI restart.
        self._refresh_show_all_button()

        layout.addStretch(1)

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — new points: entries are
        always written to the project root file (2026-08-21, plan
        flatten_and_single_file_gui), so the write target IS the root. The
        point-chain autocomplete now reads the WHOLE include graph (a point
        can live in any included file), same graph-wide scope as every other
        dock's point autocomplete."""
        if path != self._root_path:
            # Ж.2.3: an ACTUAL root switch invalidates every circle this dock
            # drew (the points belonged to the previous project). A repeat
            # call with the SAME path (DockHub broadcasts root_changed on
            # graph refreshes) must NOT wipe circles the user is looking at.
            self._clear_point_markers()
        self._root_path = path
        self._path = path
        self._refresh_sheet_names()
        self._refresh_point_names()
        self._refresh_show_all_button()

    def refresh_known_roles(self, snapshot) -> None:
        """Same "populate from the live board" pattern as PlacerDock's own
        refresh_known_roles — called by DockHub.push_snapshot at the same
        ~2s poll cadence."""
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        self.origin_widget.set_known_roles(roles, clusters)

    def _refresh_point_names(self) -> None:
        """Autocomplete for the Point-chain field, from the WHOLE include
        graph's points: keys (a point can live in any included file — see
        collect_section_entries)."""
        names = []
        if self._root_path is not None:
            names = sorted(collect_section_entries(self._root_path, "points").keys())
        self.origin_widget.set_point_names(names)

    def _refresh_sheet_names(self) -> None:
        """Sheet-name autocomplete for the point's own anchor Sheet field —
        from the project's schematic files (RuntimeContext.sheet_names),
        refreshed on root-file change (set_root_path), the same trigger the
        Point-chain autocomplete uses (see collect_all_sheet_names,
        gui/docks/rename.py)."""
        names = collect_all_sheet_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_known_sheets(names)

    # ── Message helper ────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

    # ── Building the Point entry dict (shared by Resolve/Save) ─────────────

    def _build_entry(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Returns (name, data) — data matches _load_point()'s own shape
        (no 'name' key inside, unlike thermal_via_arrays/clone_placements'
        list entries — points: is keyed by name, see module docstring).
        None after showing the error."""
        name = self.name_edit.text().strip()
        if not name:
            self._show_message(_("Name is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {}
        origin_fields, err = self.origin_widget.build()
        if err:
            self._show_message(err, _ERROR_STYLE)
            return None
        mode = origin_fields["mode"]
        if mode == "xy":
            entry["xy"] = [origin_fields["x"], origin_fields["y"]]
        elif mode == "anchor":
            if "ref" in origin_fields:
                entry["anchor_ref"] = origin_fields["ref"]
            else:
                entry["anchor_role"] = origin_fields["role"]
                if "sheet" in origin_fields:
                    entry["anchor_sheet"] = origin_fields["sheet"]
            if "cluster" in origin_fields:
                entry["anchor_cluster"] = origin_fields["cluster"]
            if "pad" in origin_fields:
                entry["anchor_pad"] = origin_fields["pad"]
        elif mode == "point":
            entry["anchor_point"] = origin_fields["point"]
        else:  # board_origin
            entry["anchor_origin"] = origin_fields["kind"]

        if mode != "xy":
            if origin_fields["shift_x"]:
                entry["shift_x_mm"] = origin_fields["shift_x"]
            if origin_fields["shift_y"]:
                entry["shift_y_mm"] = origin_fields["shift_y"]

        comment = self.comment_edit.text().strip()
        if comment:
            entry["comment"] = comment

        return name, entry

    # ── Resolve ───────────────────────────────────────────────────────────

    def _on_resolve(self) -> None:
        self._show_message("")
        payload = self._collect_resolve_inputs()
        if payload is None:
            return
        self._start_resolve_op(payload)

    def _collect_resolve_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: build+validate the current form's Point, load every
        OTHER point already saved in this file (silently skipping any that
        fail to load — see module docstring on why an unrelated broken
        point must not block this preview), and hand the worker a plain-data
        payload (no widget references)."""
        built = self._build_entry()
        if built is None:
            return None
        name, entry = built

        try:
            point = load_point(name, entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None

        board = self._connection.board
        if board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return None

        return {"name": name, "points": self._all_points_with(name, point),
                "board": board}

    def _all_points_with(self, name: str, point: Any) -> Dict[str, Any]:
        """The whole include graph's points, with `name` REPLACED by the form's
        own just-validated Point — the dict both Resolve and the marker read
        hand to resolve_point_chain. An unrelated OTHER entry that fails to
        load is silently skipped (see the module docstring), and the
        replace-by-name is the same discipline PlacerDock/ThermalViaArrayDock's
        Redraw uses."""
        points: Dict[str, Any] = {}
        for other_name, other_data in collect_section_entries(self._root_path, "points").items():
            if other_name == name:
                continue
            try:
                points[other_name] = load_point(other_name, other_data or {})
            except ValidationError:
                continue  # unrelated broken entry — must not block this preview
        points[name] = point
        return points

    def _run_resolve(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: board IPC only, never touches a widget."""
        adapter = payload["board"].adapter
        try:
            resolved = resolve_point_chain(adapter, payload["points"], payload["name"], sheet_names={})
        except (ValidationError, ApiError) as e:
            return {"error": _("Resolve failed: {error}").format(error=e)}
        if resolved.footprint is not None:
            adapter.select_items([resolved.footprint])
        x_mm = resolved.position.x / MM
        y_mm = resolved.position.y / MM
        # Ж.2.1: Resolve also puts the circle — same worker as the IPC above,
        # so nothing is drawn from the UI thread. The owner MOVES the existing
        # circle of this key on a repeat Resolve, it never stacks a second.
        _ensure_point_marker(adapter, payload["name"], x_mm, y_mm)
        return {
            "x_mm": x_mm,
            "y_mm": y_mm,
            "has_footprint": resolved.footprint is not None,
        }

    def _finish_resolve(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        suffix = "" if result["has_footprint"] else _(" (no footprint to highlight)")
        self._show_message(
            _("X={x:.3f}mm Y={y:.3f}mm{suffix}").format(
                x=result["x_mm"], y=result["y_mm"], suffix=suffix),
            _SUCCESS_STYLE)
        # Resolve put a circle — the toggle's label follows the map (Ж.2.2).
        self._refresh_show_all_button()

    def _start_resolve_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection, (self.resolve_button,),
            self._run_resolve, self._finish_resolve, self._on_resolve_failed, payload)

    def _on_resolve_failed(self, message: str) -> None:
        self._show_message(_("Resolve failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_resolve(self) -> None:
        """Synchronous composition of collect + run + finish — for tests and
        any caller that must not return until Resolve is complete."""
        payload = self._collect_resolve_inputs()
        if payload is None:
            return
        result = self._run_resolve(payload)
        self._finish_resolve(result)

    # ── Read the marker position back (К, 2026-09-12) ─────────────────────

    def _on_read_position(self) -> None:
        """UI thread (button "Read from board"): the point's circle is
        DRAGGABLE in KiCad, so the user sets the position by hand and this
        reads the circle's centre back into the FORM. The write to the config
        stays the explicit, existing Save (К.2.2) — a read is not a save."""
        self._show_message("")
        payload = self._collect_read_inputs()
        if payload is None:
            return
        self._active_op = start_long_op(
            self._connection,
            (self.resolve_button, self.read_position_button),
            self._run_read_position, self._finish_read_position,
            self._on_read_failed, payload)

    def _collect_read_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: reading is a board IPC (К.2.3), so it needs the same
        preconditions Resolve has — plus a circle that really exists. Without
        one there is nothing to read, and it is deliberately NOT drawn here:
        the user must see where the numbers came from (К.2.1)."""
        name = self.name_edit.text().strip()
        if not name:
            self._show_message(_("Name is required."), _ERROR_STYLE)
            return None
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None
        board = self._connection.board
        if board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return None
        key = _point_key(name)
        if not self._overlay.has_key(key):
            self._show_message(
                _("Point {name!r}: no marker on the board — press Resolve "
                  "first (nothing was read).").format(name=name),
                _WARN_STYLE)
            return None
        built = self._build_entry()
        if built is None:
            return None
        try:
            point = load_point(name, built[1])
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return {"name": name, "key": key, "point": point,
                "points": self._all_points_with(name, point), "board": board}

    def _run_read_position(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: board IPC only, never touches a widget. The circle
        gives an ABSOLUTE board position; WHERE that number belongs is decided
        by what the point IS (К.1): a literal-xy point gets its xy REPLACED, an
        anchored point gets its SHIFT — never both (a shift on top of xy is
        fatal, see Point's own docstring).

        The new shift is recomputed from the BASE, not from the old shift:
        base = resolved position − old shift, new_shift = dragged − base. That
        is what keeps a repeated read from making the point creep (the shift is
        board-absolute mm, not a delta on top of the previous shift)."""
        adapter = payload["board"].adapter
        name = payload["name"]
        try:
            resolved = resolve_point_chain(adapter, payload["points"], name,
                                          sheet_names={})
        except (ValidationError, ApiError) as e:
            return {"error": _("Read position failed: {error}").format(error=e)}
        marker = overlay_markers.owner.read_position(adapter, payload["key"])
        if marker is None:
            return {"name": name, "gone": True}
        # The drag is a whole nanometre count (the board stores nm), and it must
        # stay one all the way into the config so that a re-Resolve lands on it
        # EXACTLY (К.5.3) — hence _nm_to_mm_exact below, not a bare mm float.
        marker_x_nm = int(round(marker[0] * MM))
        marker_y_nm = int(round(marker[1] * MM))
        point = payload["point"]
        if point.xy is not None:
            # A literal point has no shift to write (both at once is fatal) —
            # the marker's absolute position IS the new literal, and the old
            # literal is exactly where the point resolved before the drag.
            return {
                "name": name,
                "mode": "xy",
                "x_mm": _nm_to_mm_exact(marker_x_nm),
                "y_mm": _nm_to_mm_exact(marker_y_nm),
                "old_x_mm": resolved.position.x / MM,
                "old_y_mm": resolved.position.y / MM,
            }
        base_x_nm = resolved.position.x - int(point.shift_x_mm * MM)
        base_y_nm = resolved.position.y - int(point.shift_y_mm * MM)
        return {
            "name": name,
            "mode": "shift",
            "shift_x_mm": _nm_to_mm_exact(marker_x_nm - base_x_nm),
            "shift_y_mm": _nm_to_mm_exact(marker_y_nm - base_y_nm),
            "old_shift_x_mm": point.shift_x_mm,
            "old_shift_y_mm": point.shift_y_mm,
            "x_mm": marker_x_nm / MM,
            "y_mm": marker_y_nm / MM,
        }

    def _finish_read_position(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        name = result["name"]
        if result.get("gone"):
            # The circle was deleted in KiCad (or swept): nothing to read, the
            # fields stay as they are — the stale key is left for Resolve to
            # replace (ensure_marker drops a dead uuid by itself).
            self._show_message(
                _("Point {name!r}: the marker is gone from the board (deleted "
                  "in KiCad or swept) — press Resolve to place a new one.")
                .format(name=name), _WARN_STYLE)
            return
        # Programmatic population must not look like a user edit to the
        # auto-stage path (same _loading guard new_point/load_entry use).
        self._loading = True
        try:
            if result["mode"] == "xy":
                self.x_edit.setText(str(result["x_mm"]))
                self.y_edit.setText(str(result["y_mm"]))
            else:
                self.shift_x_edit.setText(str(result["shift_x_mm"]))
                self.shift_y_edit.setText(str(result["shift_y_mm"]))
        finally:
            self._loading = False
        if result["mode"] == "xy":
            self._show_message(
                _("Read from the board: {name!r} xy = X={x:.3f}mm Y={y:.3f}mm "
                  "(was X={old_x:.3f}mm Y={old_y:.3f}mm).").format(
                    name=name, x=result["x_mm"], y=result["y_mm"],
                    old_x=result["old_x_mm"], old_y=result["old_y_mm"]),
                _SUCCESS_STYLE)
            return
        self._show_message(
            _("Read from the board: {name!r} shift = X={shift_x:+.3f}mm "
              "Y={shift_y:+.3f}mm (was X={old_x:+.3f}mm Y={old_y:+.3f}mm); the "
              "point resolves to X={x:.3f}mm Y={y:.3f}mm.").format(
                name=name, shift_x=result["shift_x_mm"],
                shift_y=result["shift_y_mm"],
                old_x=result["old_shift_x_mm"], old_y=result["old_shift_y_mm"],
                x=result["x_mm"], y=result["y_mm"]),
            _SUCCESS_STYLE)

    def _on_read_failed(self, message: str) -> None:
        self._show_message(
            _("Read position failed: {error}").format(error=message),
            _ERROR_STYLE)

    def _do_read_position(self) -> None:
        """Synchronous composition of collect + run + finish — for tests and
        any caller that must not return until the read is complete."""
        payload = self._collect_read_inputs()
        if payload is None:
            return
        self._finish_read_position(self._run_read_position(payload))

    # ── Show / hide every point circle (Ж.2.2) ────────────────────────────

    def _has_point_markers(self) -> bool:
        """True when the owner holds ANY key of the point namespace — the
        toggle's state, read FROM THE MAP. A separate GUI flag is deliberately
        not kept: it would drift from what is really on the board."""
        return any(k.startswith(_POINT_KEY_PREFIX) for k in self._overlay.keys())

    def _refresh_show_all_button(self) -> None:
        """The label follows the fact (Ж.2.2): with no `point/...` key the
        button offers to draw the circles, with at least one it offers to
        clear the whole namespace."""
        self.show_all_button.setText(
            _("Hide all points") if self._has_point_markers()
            else _("Show all points"))

    def _on_show_all_points(self) -> None:
        """UI thread (button): the ONE toggle — hide when the map already owns
        point keys, draw for every resolvable point otherwise. Both halves run
        on a worker with the same button lock as Resolve (the list can be long
        and every point is a board read)."""
        if self._has_point_markers():
            payload = self._collect_hide_all_inputs()
            if payload is None:
                return
            self._active_op = start_long_op(
                self._connection, (self.resolve_button, self.show_all_button),
                self._run_hide_all_points, self._finish_hide_all_points,
                self._on_show_all_failed, payload)
            return
        payload = self._collect_show_all_inputs()
        if payload is None:
            return
        self._active_op = start_long_op(
            self._connection, (self.resolve_button, self.show_all_button),
            self._run_show_all_points, self._finish_show_all_points,
            self._on_show_all_failed, payload)

    def _collect_show_all_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: load every point of the flat list (the WHOLE include
        graph, same scope as the point-name autocomplete) and hand the worker
        plain data. An entry that does not even load is REPORTED, never
        silently dropped, and never blocks the others (Ж.2.2)."""
        if self._root_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None
        board = self._connection.board
        if board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return None
        points: Dict[str, Any] = {}
        names = []
        failed = []
        for name, data in collect_section_entries(self._root_path, "points").items():
            try:
                points[name] = load_point(name, data or {})
            except ValidationError as e:
                failed.append((name, str(e)))
                continue
            names.append(name)
        return {"points": points, "names": names, "failed": failed,
                "board": board}

    def _run_show_all_points(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: resolve EVERY point and give each resolved one its
        circle. One bad point is NOT fatal (Ж.2.2) — it is skipped and
        reported, and the rest of the list still gets its circles."""
        adapter = payload["board"].adapter
        drawn = []
        failed = list(payload.get("failed") or [])
        for name in payload["names"]:
            try:
                resolved = resolve_point_chain(adapter, payload["points"], name,
                                               sheet_names={})
            except (ValidationError, ApiError) as e:
                failed.append((name, str(e)))
                continue
            _ensure_point_marker(adapter, name,
                                 resolved.position.x / MM,
                                 resolved.position.y / MM)
            drawn.append(name)
        return {"drawn": drawn, "failed": failed}

    def _finish_show_all_points(self, result: Dict[str, Any]) -> None:
        for name, error in result.get("failed") or []:
            self._show_message(
                _("Point {name!r} did not resolve: {error}").format(
                    name=name, error=error),
                _ERROR_STYLE)
        if not result.get("drawn") and not result.get("failed"):
            self._show_message(_("No points to show."), _WARN_STYLE)
        self._refresh_show_all_button()

    def _collect_hide_all_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: clearing the namespace is board-side IPC too, so it
        needs a live board — without one the button does nothing but say so
        (Ж.2.2), never a modal."""
        board = self._connection.board
        if board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return None
        return {"board": board}

    def _run_hide_all_points(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: drop the WHOLE point namespace — OUR keys only;
        shapes of other namespaces are never touched (Е.2.1)."""
        self._overlay.remove_namespace(payload["board"].adapter,
                                       overlay_markers.NS_POINT)
        return {}

    def _finish_hide_all_points(self, _result: Dict[str, Any]) -> None:
        self._refresh_show_all_button()

    def _on_show_all_failed(self, message: str) -> None:
        self._show_message(
            _("Show all points failed: {error}").format(error=message),
            _ERROR_STYLE)

    def _do_show_all_points(self) -> None:
        """Synchronous composition of collect + run + finish — for tests and
        any caller that must not return until the toggle is complete."""
        if self._has_point_markers():
            payload = self._collect_hide_all_inputs()
            if payload is None:
                return
            self._finish_hide_all_points(self._run_hide_all_points(payload))
            return
        payload = self._collect_show_all_inputs()
        if payload is None:
            return
        self._finish_show_all_points(self._run_show_all_points(payload))

    # ── Cleanup of point circles (Ж.2.3) ──────────────────────────────────

    def _live_adapter(self):
        """The live board's adapter, or None when there is no board."""
        board = getattr(self._connection, "board", None)
        return getattr(board, "adapter", None) if board is not None else None

    def _forget_point_marker(self, name: str) -> None:
        """Drop ONE name's circle: state on the UI thread, the board-side
        removal on a worker — the same split as cell_anchor_view.cleanup()
        (never a synchronous adapter call on the UI thread)."""
        if getattr(self._connection, "long_op_active", False):
            return  # never interleave two IPC ops on the shared kipy REQ socket
        uuid = self._overlay.forget_key(_point_key(name))
        self._refresh_show_all_button()
        adapter = self._live_adapter()
        if not uuid or adapter is None:
            return
        self._active_op = start_long_op(
            self._connection, [], board_overlay.remove_overlay,
            lambda _ok: None, lambda _message: None, adapter, [uuid])

    def _clear_point_markers(self) -> None:
        """Drop EVERY circle this dock owns (an actual root switch). State
        FIRST, so a missing board never leaves stale tracking behind; the
        shapes themselves go on a worker."""
        if getattr(self._connection, "long_op_active", False):
            return
        uuids = self._overlay.forget_scope(overlay_markers.NS_POINT)
        self._refresh_show_all_button()
        adapter = self._live_adapter()
        if not uuids or adapter is None:
            return
        self._active_op = start_long_op(
            self._connection, [], board_overlay.remove_overlay,
            lambda _ok: None, lambda _message: None, adapter, uuids)

    # ── Save (auto-stage) ─────────────────────────────────────────────────

    def _autostage(self) -> None:
        """Field commit point -> stage the current Point into the working set
        (2026-09-01, plan project_save_model). Skips programmatic population
        (_loading) and incomplete points (no name yet); _on_save validates
        and reports — an invalid point is never staged. Wrapped in try/except:
        an unhandled exception in a PyQt6 signal slot aborts the whole
        process, so a staging bug must degrade to a log line, never a crash."""
        try:
            if self._loading or self._path is None:
                return
            if not self.name_edit.text().strip():
                return
            self._on_save()
        except Exception:
            logger.exception("points auto-stage failed")

    def _on_save(self) -> None:
        built = self._build_entry()
        if built is None:
            return
        name, entry = built
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return

        try:
            load_point(name, entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            overwritten = merge_write(self._path, {"points": {name: entry}}, section="points")
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=name, path=display_path(self._path)),
            _SUCCESS_STYLE)
        self._refresh_point_names()
        # Ж.2.3: a Save under a DIFFERENT name is a rename — the point the old
        # name labelled no longer exists, so its circle must go with it (this
        # dock's own delete path does not exist; deletes live in the config
        # tree and leave an orphan by design, see the module docstring).
        if self._loaded_name is not None and self._loaded_name != name:
            self._forget_point_marker(self._loaded_name)
        self._loaded_name = name
        self.saved.emit()

    # ── Starting a brand new entry (ConfigTreeDock's Add point...) ──────────

    def new_point(self, path: Path) -> None:
        """Resets the form to its initial (blank) state — ConfigTreeDock's
        "Add point..." context-menu action opens this form empty, same
        reasoning as PlacerDock.new_placement()/ThermalViaArrayDock.
        new_thermal_via(). The entry is written to the project root file
        (2026-08-21), so the passed path is ignored."""
        self._loading = True
        try:
            self._path = self._root_path
            self._loaded_name = None
            self.name_edit.setText("")
            self.origin_widget.clear()
        finally:
            self._loading = False
        self._show_message("")

    # ── Loading an already-saved entry back into the form ───────────────────

    def load_entry(self, name: str) -> None:
        """Reverse of _build_entry() — called by ConfigTreeDock's Points
        category (via points_edit_requested, a DOUBLE click on a points:
        leaf, since 2026-09-01 — see plan plan_2026_09_01_points_dialog.md)
        when an already-saved entry is opened. points: is a DICT section
        (see module docstring), so the signal only carries the name — the
        actual data is re-read fresh from the WHOLE include graph here (a
        point can live in any included file). The WRITE target is set back
        to the file the entry actually lives in, so a Save updates that
        file instead of duplicating the point into the root (2026-08-21
        review fix)."""
        self._show_message("")
        source = find_dict_entry_file(self._root_path, "points", name)
        if source is not None:
            self._path = source
        # Remember which name is in the form, so a later Save under another
        # name is recognised as a RENAME (Ж.2.3).
        self._loaded_name = name
        entry = {}
        if self._root_path is not None:
            entry = collect_section_entries(self._root_path, "points").get(name) or {}
        self._loading = True
        try:
            self.name_edit.setText(name)
            self.comment_edit.setText(str(entry.get("comment") or ""))

            shift_x = entry.get("shift_x_mm") or None
            shift_y = entry.get("shift_y_mm") or None
            if "anchor_point" in entry:
                self.origin_widget.load(mode="point", point=str(entry["anchor_point"]),
                                        shift_x=shift_x, shift_y=shift_y)
            elif "anchor_origin" in entry:
                self.origin_widget.load(mode="board_origin", kind=entry["anchor_origin"],
                                        shift_x=shift_x, shift_y=shift_y)
            elif "xy" in entry:
                xy = entry["xy"] or [0, 0]
                self.origin_widget.load(mode="xy", x=xy[0], y=xy[1],
                                        shift_x=shift_x, shift_y=shift_y)
            else:
                self.origin_widget.load(
                    mode="anchor", ref=str(entry.get("anchor_ref", "")),
                    role=str(entry.get("anchor_role", "")),
                    sheet=str(entry.get("anchor_sheet", "")),
                    pad=str(entry.get("anchor_pad", "")),
                    cluster=str(entry.get("anchor_cluster", "")),
                    shift_x=shift_x, shift_y=shift_y)
        finally:
            self._loading = False

# gui/docks/net_trace.py
"""
NetTraceDock — GUI for the `net_traces:` section (plan
techdocs/handoff/deepseek/plan_2026_08_21_net_trace_dock.md): pick a net BY
NAME over the WHOLE live board (no mouse selection needed — this is the
long-standing GUI gap the review's finding 5 was about), an anchor block, and
Extract / Save / Redraw.

Unlike the selection-based ExtractDock ("Origin: Via net" combo), the net
picker here is sourced from ALL tracks/vias of the live board
(adapter.get_tracks() + get_vias()), not the current selection — the two
widgets look similar but are deliberately different mechanisms (the plan's
§1.1 regression guard).

Reuses (never rewrites):
  - core: kicadstamp.net_trace_extract.extract_net_trace()/write_net_trace()
  - anchor block: AnchorOriginWidget(modes=["anchor"], anchor_fields=
    ["sheet","pad","cluster"]) — NetTrace is anchor-role-only, no ref/xy/point
    alternative
  - persist: the _persist_rule-style collect -> load_net_trace validation ->
    upsert_list_entry -> Log flow (rules.py)
  - redraw: ApplyPipeline with only=[net], the same mechanism RuleDock uses

Geometry (tracks:/vias:) is NEVER edited by the form — machine-written data,
hand-edited only by re-running Extract (same reason cells: are not hand-edited).
"""
from dataclasses import replace
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config, RuntimeContext, load_config, load_net_trace
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.net_trace_extract import (extract_net_trace, net_trace_to_dict,
                                          read_net_trace_flags, write_net_trace)

from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, read_data,
                      set_combo_items, show_message, upsert_list_entry)
from .rename import collect_all_sheet_names, find_list_entry_file

logger = logging.getLogger(__name__)


def _net_trace_identity(entry: Dict[str, Any]) -> Any:
    """upsert_list_entry's key_fn — mirrors net_trace_effective_name() at the
    raw-dict level: the net itself is the record's --only identity (one record
    per net, enforced at load)."""
    return entry.get("net")


class NetTraceDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — the same
    "plain QWidget, not its own QDockWidget" shape as every other entity dock."""

    # Fired after a successful Extract/Save that changed what the Config tree
    # shows — ConfigTreeDock listens to refresh (see gui/dock_hub.py).
    saved = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__(main_window)
        self._main_window = main_window
        # Injected BoardConnection — falls back to the owning window's when
        # not passed explicitly (keeps direct-construction callers working).
        self._connection = connection if connection is not None else main_window.connection
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run.
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None
        self._root_path: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Net picker — over the WHOLE live board's copper (tracks+vias), NOT
        # the mouse selection (see module docstring).
        form = QFormLayout()
        self.net_edit = QComboBox()
        configure_searchable(self.net_edit)
        self.net_edit.setPlaceholderText(_("net name (from the whole board's copper)"))
        form.addRow(_("Net:"), self.net_edit)
        layout.addLayout(form)

        # Anchor block — NetTrace is anchor-role-only: no ref/xy/point mode.
        self.anchor_widget = AnchorOriginWidget(
            modes=["anchor"], anchor_fields=["sheet", "pad", "cluster"])
        layout.addWidget(self.anchor_widget)

        checks_row = QHBoxLayout()
        self.retired_checkbox = QCheckBox(_("Retired"))
        self.skip_checkbox = QCheckBox(_("Skip"))
        checks_row.addWidget(self.retired_checkbox)
        checks_row.addWidget(self.skip_checkbox)
        layout.addLayout(checks_row)

        buttons = QHBoxLayout()
        self.extract_button = QPushButton(_("Extract"))
        self.extract_button.clicked.connect(self._on_extract)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        self.redraw_button = QPushButton(_("Redraw"))
        self.redraw_button.clicked.connect(self._on_redraw)
        buttons.addWidget(self.extract_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.redraw_button)
        layout.addLayout(buttons)

        # Geometry note — tracks:/vias: are machine-written, not hand-edited.
        self.geometry_note = QLabel(
            _("tracks:/vias: are written by Extract — edit them by re-extracting, "
              "not by hand."))
        self.geometry_note.setWordWrap(True)
        layout.addWidget(self.geometry_note)

        layout.addStretch(1)

    # ── Target file / root ──────────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — new net_traces: records
        are always written to the project root file (2026-08-21, plan
        flatten_and_single_file_gui), so the dock's write target IS the root."""
        self._root_path = path
        self._path = path
        self._refresh_sheet_names()

    def _refresh_sheet_names(self) -> None:
        names = collect_all_sheet_names(self._root_path) if self._root_path is not None else []
        self.anchor_widget.set_known_sheets(names)

    # ── Live-board population (DockHub.push_snapshot) ───────────────────────

    def refresh_known_roles(self, snapshot) -> None:
        """Role/Cluster combos of the anchor block — from the live-board
        snapshot, same pattern as RuleDock.refresh_known_roles."""
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        self.anchor_widget.set_known_roles(roles, clusters)

    def refresh_known_nets(self, board) -> None:
        """Net picker from the WHOLE board's copper (tracks + vias) — NOT the
        selection, and NOT get_all_nets() (which would include pad-only nets
        with no copper to capture). Same collection pattern as
        extract.py::_update_origin_choices, minus the selection restriction."""
        if board is None:
            set_combo_items(self.net_edit, [])
            return
        adapter = board.adapter
        nets = {t.net.name for t in adapter.get_tracks() if t.net and t.net.name}
        nets |= {v.net.name for v in adapter.get_vias() if v.net and v.net.name}
        set_combo_items(self.net_edit, sorted(nets))

    # ── Message helper ──────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(text, style, logger)

    # ── Form <-> entry mapping ──────────────────────────────────────────────

    def _collect_anchor_fields(self) -> Optional[Dict[str, str]]:
        """Build()+map the anchor block into NetTrace's field names. NetTrace
        is anchor-ROLE-only (no anchor_ref/anchor_point) — a filled Ref field
        is a config error for this section, not something to silently drop."""
        origin_fields, err = self.anchor_widget.build()
        if err:
            self._show_message(err, _ERROR_STYLE)
            return None
        if "ref" in origin_fields:
            self._show_message(_("net_traces anchors by Role only — "
                                 "anchor_ref is not a net_traces field."), _ERROR_STYLE)
            return None
        fields: Dict[str, str] = {"role": origin_fields["role"]}
        for generic, yaml_key in (("sheet", "sheet"), ("pad", "pad"), ("cluster", "cluster")):
            if generic in origin_fields:
                fields[yaml_key] = origin_fields[generic]
        return fields

    def _build_entry_dict(self) -> Optional[Dict[str, Any]]:
        """Build the record's CONTROLLABLE fields (net/anchor/retired/skip) —
        NEVER tracks:/vias: (machine-written geometry, see module docstring).
        Used by Save and (as a base) by Redraw/Extract."""
        net = self.net_edit.currentText().strip()
        if not net:
            self._show_message(_("Net is required."), _ERROR_STYLE)
            return None
        anchor = self._collect_anchor_fields()
        if anchor is None:
            return None
        entry: Dict[str, Any] = {"net": net, "anchor_role": anchor["role"]}
        if "sheet" in anchor:
            entry["anchor_sheet"] = anchor["sheet"]
        if "pad" in anchor:
            entry["anchor_pad"] = anchor["pad"]
        if "cluster" in anchor:
            entry["anchor_cluster"] = anchor["cluster"]
        if self.retired_checkbox.isChecked():
            entry["retired"] = True
        if self.skip_checkbox.isChecked():
            entry["skip"] = True
        return entry

    def load_entry(self, entry: Dict[str, Any], file_path: Optional[Path] = None) -> None:
        """Reverse of _build_entry_dict — called by ConfigTreeDock's net_traces
        leaf click (net_trace_picked): fills the form from the saved record.
        tracks:/vias: are deliberately not shown (not hand-edited). The WRITE
        target is set back to the file the record actually lives in, so a Save
        updates that file instead of adding a root duplicate (2026-08-21
        review fix)."""
        self._show_message("")
        if file_path is None:
            file_path = find_list_entry_file(self._root_path, "net_traces", entry)
        if file_path is not None:
            self._path = file_path
        self.net_edit.setCurrentText(str(entry.get("net", "")))
        self.anchor_widget.load(
            mode="anchor", role=str(entry.get("anchor_role", "")),
            sheet=str(entry.get("anchor_sheet", "")),
            pad=str(entry.get("anchor_pad", "")),
            cluster=str(entry.get("anchor_cluster", "")))
        self.retired_checkbox.setChecked(bool(entry.get("retired", False)))
        self.skip_checkbox.setChecked(bool(entry.get("skip", False)))

    # ── Extract ────────────────────────────────────────────────────────────

    def _on_extract(self) -> None:
        """Extract button: capture the selected net's copper from the LIVE
        board (whole-board search, no selection) and write it under
        net_traces: — via the worker thread (board IPC happens there)."""
        self._show_message("")
        board = self._connection.board
        if board is None:
            self._show_message(_("Connect to KiCad first."), _ERROR_STYLE)
            return
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        net = self.net_edit.currentText().strip()
        if not net:
            self._show_message(_("Net is required."), _ERROR_STYLE)
            return
        anchor = self._collect_anchor_fields()
        if anchor is None:
            return
        payload = {
            "board": board, "path": self._path,
            "net": net, "anchor_role": anchor["role"],
            "anchor_sheet": anchor.get("sheet"), "anchor_cluster": anchor.get("cluster"),
            "anchor_pad": anchor.get("pad"),
        }
        self._active_op = start_long_op(
            self._connection,
            (self.extract_button, self.save_button, self.redraw_button),
            self._run_extract, self._finish_extract, self._on_op_failed, payload)

    def _run_extract(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: board IPC + file write only — never touches a widget."""
        try:
            # A re-extract refreshes GEOMETRY but must not silently clear the
            # hand-set retired:/skip: of an already-saved record (review fix
            # 2026-08-21) — carry the existing flags into the new extraction.
            existing_retired, existing_skip = read_net_trace_flags(
                str(payload["path"]), payload["net"])
            nt = extract_net_trace(
                payload["board"].adapter,
                net=payload["net"], anchor_role=payload["anchor_role"],
                anchor_sheet=payload["anchor_sheet"],
                anchor_cluster=payload["anchor_cluster"],
                anchor_pad=payload["anchor_pad"], sheet_names={},
                retired=existing_retired, skip=existing_skip)
            write_net_trace(str(payload["path"]), nt)
        except (PlacerError, ValidationError, OSError, yaml.YAMLError) as e:
            return {"error": _("Extract failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("net_trace extract failed")
            return {"error": _("Extract failed: {error}").format(error=e)}
        return {"net": nt.net, "tracks": len(nt.tracks), "vias": len(nt.vias),
                "path": str(payload["path"])}

    def _finish_extract(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        self._show_message(
            _("Wrote net trace {net!r} — {tracks} tracks, {vias} vias -> {path}")
            .format(net=result["net"], tracks=result["tracks"],
                    vias=result["vias"], path=display_path(Path(result["path"]))),
            _SUCCESS_STYLE)
        self.saved.emit()

    def _on_op_failed(self, message: str) -> None:
        self._show_message(_("Operation failed: {error}").format(error=message), _ERROR_STYLE)

    # ── Save ───────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        """Save the form's controllable fields (net/anchor/retired/skip) into
        the target file, replacing the same-net record in place. Geometry is
        NOT touched (see module docstring). Validates via load_net_trace first,
        never writes silently."""
        self._show_message("")
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        entry = self._build_entry_dict()
        if entry is None:
            return
        try:
            load_net_trace(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        # Save edits ONLY the controllable fields — the machine-written
        # geometry (tracks:/vias:) of an already-saved record must survive.
        # upsert_list_entry replaces the whole entry by identity (net), so
        # carry the existing record's tracks/vias across, or a Save would
        # silently erase them (found by the dock's own test, 2026-08-21).
        try:
            existing = read_data(self._path)
        except OSError:
            existing = {}
        for saved in existing.get("net_traces") or []:
            if isinstance(saved, dict) and saved.get("net") == entry["net"]:
                if "tracks" in saved:
                    entry["tracks"] = saved["tracks"]
                if "vias" in saved:
                    entry["vias"] = saved["vias"]
                break
        try:
            overwritten = upsert_list_entry(self._path, "net_traces", entry,
                                            key_fn=_net_trace_identity)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return
        self._show_message(
            _("{action} net trace {net!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                net=entry["net"], path=display_path(self._path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Redraw ─────────────────────────────────────────────────────────────

    def _on_redraw(self) -> None:
        self._show_message("")
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        self._active_op = start_long_op(
            self._connection,
            (self.extract_button, self.save_button, self.redraw_button),
            self._run_redraw, self._finish_redraw, self._on_op_failed, payload)

    def _collect_redraw_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: build the form's net/anchor, then load the saved record's
        GEOMETRY (tracks:/vias:) from the project root and merge the two — a
        Redraw re-places the actual captured copper, following the form's
        (possibly edited) anchor."""
        entry = self._build_entry_dict()
        if entry is None:
            return None
        if self._path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return None
        try:
            load_net_trace(entry)  # validate the form's controllable fields
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        # Load from the project ROOT, not self._path (the file this record is
        # saved into) — same reasoning as RuleDock._collect_redraw_inputs.
        config_path = self._root_path if self._root_path is not None else self._path
        try:
            if config_path.exists():
                cfg, ctx = load_config(str(config_path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load file: {error}").format(error=e), _ERROR_STYLE)
            return None

        for saved in cfg.net_traces:
            if saved.net == entry["net"]:
                saved_dict = net_trace_to_dict(saved)
                entry["tracks"] = saved_dict.get("tracks", [])
                entry["vias"] = saved_dict.get("vias", [])
                break

        nt = load_net_trace(entry)  # full NetTrace incl. carried geometry
        # load_config() returns the graph cache's SHARED Config (no defensive
        # deepcopy) — the replace-by-net below must not mutate the cached one.
        cfg = replace(cfg)
        cfg.net_traces = [n for n in cfg.net_traces if n.net != entry["net"]]
        cfg.net_traces.append(nt)
        return {"path": config_path, "cfg": cfg, "ctx": ctx, "name": entry["net"]}

    def _run_redraw(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: ApplyPipeline --only=<net> — never touches a widget."""
        pipeline = ApplyPipeline(config_path=str(payload["path"]),
                                 preloaded_cfg=payload["cfg"], preloaded_ctx=payload["ctx"],
                                 only=[payload["name"]], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            return {"error": _("Placement failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("net_trace redraw failed")
            return {"error": _("Placement failed: {error}").format(error=e)}
        return {"name": payload["name"]}

    def _finish_redraw(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        self._show_message(_("Placed {name!r}.").format(name=result["name"]), _SUCCESS_STYLE)

    # ── Test hook ──────────────────────────────────────────────────────────

    def _do_extract(self) -> Dict[str, Any]:
        """Synchronous Extract — for tests (mirrors RuleDock._do_redraw_*)."""
        board = self._connection.board
        if board is None or self._path is None:
            return {}
        net = self.net_edit.currentText().strip()
        anchor = self._collect_anchor_fields()
        if not net or anchor is None:
            return {}
        payload = {"board": board, "path": self._path, "net": net,
                   "anchor_role": anchor["role"], "anchor_sheet": anchor.get("sheet"),
                   "anchor_cluster": anchor.get("cluster"), "anchor_pad": anchor.get("pad")}
        result = self._run_extract(payload)
        self._finish_extract(result)
        return result

    def _do_redraw(self) -> Dict[str, Any]:
        """Synchronous Redraw — for tests."""
        payload = self._collect_redraw_inputs()
        if payload is None:
            return {}
        result = self._run_redraw(payload)
        self._finish_redraw(result)
        return result

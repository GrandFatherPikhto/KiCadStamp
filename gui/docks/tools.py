# gui/docks/tools.py
"""ToolsDock — the Entity/Placement split's "Tools" page (phase 5.2, stage 3):
Nets / Net overrides / Refs are the Entity's ELECTRICAL fields, and with
PlacerDock trimmed to Source (Entity) + Origin they now live HERE instead of
Placer's tabs.

The dock is Entity-targeted like PlacerDock's Entity source mode: pick an
Entity (graph-wide), edit its nets:/net_overrides:/refs: dicts, Save writes
the record back to the file it actually lives in (so an Entity in an
included file is updated in place, never duplicated into the root). Placer's
own Entity Save merges (see placer._do_save_entity) so the two docks never
clobber each other's fields.
"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from kicadstamp.config import entity_effective_name, load_config, load_entity
from kicadstamp.config_writer import read_data, upsert_entity
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ..ui_utils import busy
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      KeyValueTableEditor, set_combo_items)
from .rename import find_list_entry_file

logger = logging.getLogger(__name__)


class ToolsDock(QWidget):
    """Edits an Entity's electrical fields (nets/net_overrides/refs) — the
    part PlacerDock dropped when it was trimmed to Source+Origin. Pick an
    Entity, edit the three dicts, Save (validate via load_entity, upsert
    into the entity's own file, merge-safe against Placer's own Entity save)."""

    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__()
        self.setObjectName("tools_dock")
        self._main_window = main_window
        self._root_path: Optional[Path] = None
        # Raw entities: dict of the currently picked Entity + the file it
        # lives in — kept whole so Save MERGES the edited dicts over it and
        # never drops the other fields (cluster/sheet/comment/...).
        self._entity_data: Dict[str, Any] = {}
        self._entity_file: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        target_form = QFormLayout()
        self.target_combo = QComboBox()
        self.target_combo.setPlaceholderText(_("pick an Entity"))
        self.target_combo.currentTextChanged.connect(self._on_target_picked)
        target_form.addRow(_("Entity:"), self.target_combo)
        layout.addLayout(target_form)

        self.nets_table = KeyValueTableEditor(
            _("Role"), _("Net"), key_placeholder=_("role, e.g. C_IN"),
            value_placeholder=_("net, e.g. +3V3"))
        layout.addWidget(QLabel(_("Nets (role -> net):")))
        layout.addWidget(self.nets_table)

        self.net_overrides_table = KeyValueTableEditor(
            _("Net"), _("Override"), key_placeholder=_("net, e.g. +5V"),
            value_placeholder=_("override, e.g. +5V_DIRTY"))
        layout.addWidget(QLabel(_("Net overrides (net -> override):")))
        layout.addWidget(self.net_overrides_table)

        self.refs_table = KeyValueTableEditor(
            _("Role"), _("Ref"), key_placeholder=_("role, e.g. C_IN"),
            value_placeholder=_("ref, e.g. C12"))
        layout.addWidget(QLabel(_("Refs (role -> ref):")))
        layout.addWidget(self.refs_table)

        button_row = QVBoxLayout()
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

    # ── Root wiring / target choices ──────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — the Entity picker is
        sourced from the WHOLE include graph (an Entity may live in any
        included file), same convention as PlacerDock's Entity source."""
        self._root_path = path
        self._entity_data = {}
        self._entity_file = None
        self._refresh_target_choices()

    def _refresh_target_choices(self) -> None:
        names: list = []
        if self._root_path is not None:
            try:
                cfg, _ctx = load_config(str(self._root_path))
            except (ValidationError, OSError):
                cfg = None
            if cfg is not None:
                names = [entity_effective_name(e) for e in cfg.entities]
        set_combo_items(self.target_combo, sorted(names))

    # ── Pick -> load ──────────────────────────────────────────────────────

    def _on_target_picked(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        found = self._load_entity_dict(name)
        if found is None:
            return
        self._entity_data, self._entity_file = found
        self.nets_table.load_dict(self._entity_data.get("nets"))
        self.net_overrides_table.load_dict(self._entity_data.get("net_overrides"))
        self.refs_table.load_dict(self._entity_data.get("refs"))
        self._show_message("")

    def _load_entity_dict(self, name: str) -> Optional[Tuple[Dict[str, Any], Path]]:
        """(raw entities: dict, file) for `name`, from whichever physical
        file holds it (graph-wide) — or None. The RAW dict is kept so Save
        can merge over it instead of replacing the whole record."""
        if self._root_path is None:
            return None
        try:
            file_path = find_list_entry_file(self._root_path, "entities", {"name": name})
        except (ValidationError, OSError):
            return None
        if file_path is None:
            return None
        try:
            data = read_data(file_path)
        except (ValidationError, OSError):
            return None
        for entry in data.get("entities") or []:
            if isinstance(entry, dict) and entry.get("name") == name:
                return entry, file_path
        return None

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        with busy((self.save_button,)):
            self._do_save()

    def _do_save(self) -> None:
        if not self._entity_data or self._entity_file is None:
            self._show_message(_("Pick an Entity first."), _ERROR_STYLE)
            return
        entry: Dict[str, Any] = dict(self._entity_data)
        entry["nets"] = self.nets_table.to_dict()
        entry["net_overrides"] = self.net_overrides_table.to_dict()
        entry["refs"] = self.refs_table.to_dict()
        try:
            load_entity(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return
        try:
            upsert_entity(self._entity_file, entry)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return
        self._show_message(
            _("Wrote entity {name!r} in {path}").format(
                name=entry.get("name"), path=self._entity_file),
            _SUCCESS_STYLE)
        self.saved.emit()

    def _show_message(self, text: str, style: str = "") -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(style)
        if text:
            logger.info("%s", text)

# gui/docks/chains_nav.py
"""
ChainsNavDock — the Config right-QView page shown when a chains: ANCHOR or
CHAIN node is single-clicked (2026-09-05, design config_qview_chain_entity_
pages §4 / §8.2). The right panel mirrors the tree's chains hierarchy as a
clickable drill list:

  - click an ANCHOR in the tree  -> show_anchor(...) : clickable list of CHAINS
  - click a CHAIN row            -> drill to show_chain(...) : clickable list of
                                    PADS + reveal_chain(chain) (syncs the tree
                                    selection onto that chain node)
  - click a PAD row              -> open_spoke(chain, index) (the spoke editor,
                                    ChainDock pad page)
  - "back" pops the drill one level up.

The widget holds no graph access of its own: DockHub feeds it the raw chain
dicts (from the Config tree) and reacts to its signals.
"""
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from .rename import entry_effective_name

_K_CHAIN = "chain"
_K_PAD = "pad"


class ChainsNavDock(QWidget):
    """Clickable chains drill (anchor -> chains -> pads) in the Config QView."""

    # A pad row activated — (chain_dict, pad_index): open the spoke editor.
    open_spoke = pyqtSignal(object, int)
    # A chain row activated (drill down / tree sync) — the chain dict.
    reveal_chain = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.setObjectName("chains_nav_dock")
        self._main_window = main_window
        # Drill stack of ("anchor", anchor_key, [chains]) / ("chain", chain_dict)
        self._contexts: List[Tuple[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.back_button = QPushButton(_("Back"))
        self.back_button.clicked.connect(self._go_back)
        top.addWidget(self.back_button)
        self.header_label = QLabel("—")
        top.addWidget(self.header_label, 1)
        layout.addLayout(top)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget, 1)

        self._render()
        self._update_back()

    # ── Population (called by DockHub on a tree single click) ──────────────

    def show_anchor(self, anchor_key: str, chains: List[Dict[str, Any]]) -> None:
        """Root view: the anchor's chains (clickable). `anchor_key` is the
        anchor's display identity (ref/role/point)."""
        self._contexts = [("anchor", anchor_key, list(chains))]
        self._render()
        self._update_back()

    def show_chain(self, chain: Dict[str, Any]) -> None:
        """A chain view: its pads (clickable). Pushes onto the drill stack when
        reached from an anchor view; used directly for a tree chain single
        click (one-level view)."""
        if not self._contexts or self._contexts[-1][0] != "chain":
            self._contexts.append(("chain", chain))
        else:
            self._contexts[-1] = ("chain", chain)
        self._render()
        self._update_back()

    # ── Rendering ──────────────────────────────────────────────────────────

    def _render(self) -> None:
        self.list_widget.clear()
        if not self._contexts:
            self.header_label.setText(_("Chains"))
            return
        kind = self._contexts[-1][0]
        if kind == "anchor":
            _k, anchor_key, chains = self._contexts[-1]
            self.header_label.setText(_("Anchor: {key} — chains").format(key=anchor_key))
            for chain in sorted(chains, key=lambda c: entry_effective_name("chains", c)):
                label = entry_effective_name("chains", chain)
                pads = len(chain.get("spokes") or [])
                item = QListWidgetItem(_("{name} ({pads} spokes)").format(name=label, pads=pads))
                item.setData(Qt.ItemDataRole.UserRole, (_K_CHAIN, chain))
                self.list_widget.addItem(item)
        else:
            _k, chain = self._contexts[-1]
            self.header_label.setText(
                _("Chain: {name} — pads").format(name=entry_effective_name("chains", chain)))
            for idx, spoke in enumerate(chain.get("spokes") or []):
                if not isinstance(spoke, dict):
                    continue
                pad = str(spoke.get("pad", "?"))
                cell = str(spoke.get("cell", ""))
                label = f"{pad}  —  {cell}" if cell else pad
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, (_K_PAD, chain, idx))
                self.list_widget.addItem(item)

    def _update_back(self) -> None:
        self.back_button.setEnabled(len(self._contexts) > 1)

    def _go_back(self) -> None:
        if len(self._contexts) > 1:
            self._contexts.pop()
            self._render()
            self._update_back()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind = data[0]
        if kind == _K_CHAIN:
            chain = data[1]
            self.reveal_chain.emit(chain)
            self.show_chain(chain)
        elif kind == _K_PAD:
            _k, chain, idx = data
            self.open_spoke.emit(chain, idx)

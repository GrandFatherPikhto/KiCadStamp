# tests/gui/test_chains_nav.py
"""Tests for the Config QView chains-navigation drill (ChainsNavDock,
2026-09-05, design config_qview_chain_entity_pages §4/§8.2): anchor single
click -> clickable chains; a chain row drills to its pads + reveals the chain
(tree sync); a pad row opens the spoke editor; Back pops the drill."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.docks.chains_nav import ChainsNavDock


def _nav(main_window):
    return ChainsNavDock(main_window)


def _chain(net, spokes):
    return {"net": net, "anchor_role": "FPGA", "spokes": spokes}


def test_show_anchor_lists_chains(main_window):
    nav = _nav(main_window)
    c1 = _chain("+3V3", [{"pad": "1", "cell": "a"}, {"pad": "2", "cell": "b"}])
    c2 = _chain("+1V2", [])
    nav.show_anchor("FPGA", [c2, c1])

    assert nav.list_widget.count() == 2
    texts = [nav.list_widget.item(i).text() for i in range(2)]
    # Chains sorted by effective name inside the nav render.
    assert "+1V2" in texts[0] and "+3V3" in texts[1]
    assert nav.header_label.text().startswith("Anchor: FPGA")
    assert nav.back_button.isEnabled() is False  # root view


def test_chain_row_drills_to_pads_and_reveals(main_window):
    nav = _nav(main_window)
    c1 = _chain("+3V3", [{"pad": "1", "cell": "a"}, {"pad": "2", "cell": "b"}])
    nav.show_anchor("FPGA", [c1])

    revealed = []
    nav.reveal_chain.connect(revealed.append)
    # Find and click the "+3V3" chain row.
    row = None
    for i in range(nav.list_widget.count()):
        if "+3V3" in nav.list_widget.item(i).text():
            row = i
    nav._on_item_clicked(nav.list_widget.item(row))

    assert revealed == [c1]
    assert nav.list_widget.count() == 2  # pads now
    assert "1" in nav.list_widget.item(0).text()
    assert nav.back_button.isEnabled() is True


def test_pad_row_opens_spoke(main_window):
    nav = _nav(main_window)
    c1 = _chain("+3V3", [{"pad": "1", "cell": "a"}, {"pad": "2", "cell": "b"}])
    nav.show_chain(c1)

    opened = []
    nav.open_spoke.connect(lambda chain, idx: opened.append((chain, idx)))
    nav._on_item_clicked(nav.list_widget.item(1))
    assert opened == [(c1, 1)]


def test_back_pops_to_anchor_level(main_window):
    nav = _nav(main_window)
    c1 = _chain("+3V3", [{"pad": "1", "cell": "a"}])
    nav.show_anchor("FPGA", [c1])
    nav.show_chain(c1)
    assert nav.header_label.text().startswith("Chain:")

    nav.back_button.click()
    assert nav.header_label.text().startswith("Anchor: FPGA")
    assert nav.back_button.isEnabled() is False

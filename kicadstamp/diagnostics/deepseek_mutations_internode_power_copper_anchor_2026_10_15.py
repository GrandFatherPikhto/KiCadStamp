# kicadstamp/diagnostics/deepseek_mutations_internode_power_copper_anchor_2026_10_15.py
"""Acceptance mutations for plan_2026_10_15_internode_power_copper_anchor (the
anchor's SECOND tier: a piece between clusters that SHARE one role is captured,
and every element that cannot name its own footprint takes the ANCHOR's pair),
Demon, 2026-10-15.

Grown from the Demon's own rig of 05.10 (rule 38): the same machinery —
basename-resolved T under tests/, `_drop_pyc` for the mutated file, the
`original.count(old) != 1` refusal, "ПРОМАХ" when nothing red came back, a
row-name filter that matches no row, and a control that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/net/test_internode_capture_modules.py:
the Т5-1 cell (the FACT — power copper between clusters that share C_IN_BULK),
Т5-1b (the second read reports "unchanged"), Т5-2 (the THIRD component: an
element whose own pair lands OUTSIDE the piece), the rewritten Т4-2 A2 cell, and —
untouched on purpose — the Т3-3 and Т4-2 A6 cells that pin tier 1.

  * G1 tier 2 removed                     -> Т5-1 red (the fact is skipped again,
                                             the report says "no pad of the piece
                                             anchors the record")
  * G2 tier 1 never checked (always tier 2)-> Т3-3 and A6 red (the anchor becomes
                                             pads[0], the FPGA side of the bridge)
  * G3 tier 2 writes the element's OWN pair when the narrowing found exactly ONE
       component (even a FOREIGN one)    -> Т5-2 red (B1's pad is on another net:
                                             the record's elements split across
                                             two nets) and Т5-1 red (a foreign
                                             C_IN_BULK pair is written)
  * K  a cosmetic comment                 -> MUST survive

Run: .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_internode_power_copper_anchor_2026_10_15.py
An optional row-name prefix filter takes the rest of argv.
"""
from kicadstamp.diagnostics import deepseek_mutations_tree_reread_modules_2026_10_05 as rig

NODES = rig.NODES
G = rig.GUARDS

ROWS = [
    ("G1 tier 2 removed", NODES,
     "    if candidates:\n"
     "        # Tier 2: only where tier 1 had nothing. The anchor's own role resolves\n"
     "        # (checked above); the items that cannot name their own footprint take\n"
     "        # its pair — the piece is one net.\n"
     "        return candidates[0][0], None\n",
     "    if candidates:\n        pass  # MUTATION\n",
     "die", G, ()),
    ("G2 tier 1 never checked", NODES,
     "    for pad, sheet in candidates:\n"
     "        # Tier 1: every item names its OWN pad — the 05.10 rule.\n"
     "        if _items_resolve(unit, components, pad, resolver, sheet,\n"
     "                          components[pad.ref].cluster):",
     "    for pad, sheet in candidates:\n"
     "        # Tier 1: every item names its OWN pad — the 05.10 rule.\n"
     "        if True:  # MUTATION",
     "die", G, ()),
    ("G3 one candidate is enough for the item", NODES,
     "    if own is not None and (resolver is None\n"
     "                            or resolver.resolves_to(own[0], sheet, cluster, own[2])):",
     "    if own is not None and (resolver is None\n"
     "                            or len(resolver.narrowed(own[0], sheet, cluster)) == 1):  # MUTATION",
     "die", G, ()),
    ("K cosmetic comment (control)", NODES,
     "    candidates: list[tuple[PadRef, str | None]] = []",
     "    candidates: list[tuple[PadRef, str | None]] = []  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()

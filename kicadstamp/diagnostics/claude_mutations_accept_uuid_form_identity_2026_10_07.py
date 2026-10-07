# kicadstamp/diagnostics/claude_mutations_accept_uuid_form_identity_2026_10_07.py
"""Claude's own acceptance rows for f1d37207 (plan_2026_10_05_uuid_tails part 0:
a form Redraw keeps the record's identity), 2026-10-07.

Reuses the machinery of deepseek_mutations_uuid_form_identity_2026_10_07.py (its
base rig, the guards G). The Demon's M4 covers only the thermal site; these rows
cut the rule out of the four other sites and the Save half:

  * E1 net_trace Redraw skips identify
  * E2 clone_placement Redraw skips identify
  * E3 coordinate_placement Redraw skips identify
  * E4 chain Redraw skips identify
  * E5 thermal Save drops the preview's uuid
  * K2 cosmetic comment -> MUST survive

    KICADSTAMP_ACCEPT_ROOT=<tree> .venv/bin/python \
        kicadstamp/diagnostics/claude_mutations_accept_uuid_form_identity_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_uuid_form_identity_2026_10_07 as ds

G = ds.G
_SKIP = "(lambda e, *a, **k: e)("  # identify() replaced by the identity function

ROWS = [
    ("E1 net_trace Redraw skips identify", "gui/docks/net_trace.py",
     'entry = identify(entry, "net_traces", cfg=cfg,',
     'entry = ' + _SKIP + 'entry, "net_traces", cfg=cfg,',
     "die", G, ()),
    ("E2 clone Redraw skips identify", "gui/docks/placer.py",
     'entry = identify(entry, "clone_placements", cfg=cfg,',
     'entry = ' + _SKIP + 'entry, "clone_placements", cfg=cfg,',
     "die", G, ()),
    ("E3 coordinate Redraw skips identify", "gui/docks/placer.py",
     'entry = identify(entry, "coordinate_placements", cfg=cfg,',
     'entry = ' + _SKIP + 'entry, "coordinate_placements", cfg=cfg,',
     "die", G, ()),
    ("E4 chain Redraw skips identify", "gui/docks/chain.py",
     'raw = identify(raw, "chains", cfg=cfg,',
     'raw = ' + _SKIP + 'raw, "chains", cfg=cfg,',
     "die", G, ()),
    ("E5 thermal Save drops the preview uuid", "gui/docks/thermal_via.py",
     "        identity_uuid = self._draft_uuid or self._loaded_uuid\n"
     "        if identity_uuid:\n            entry[\"uuid\"] = identity_uuid\n"
     "        if self._path is None:",
     "        identity_uuid = self._draft_uuid or self._loaded_uuid\n"
     "        if False:  # MUTATION\n            entry[\"uuid\"] = identity_uuid\n"
     "        if self._path is None:",
     "die", G, ()),
    ("K2 cosmetic comment (control)", "kicadstamp/config/form_identity.py",
     "    entry = copy.deepcopy(entry)\n",
     "    entry = copy.deepcopy(entry)  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()

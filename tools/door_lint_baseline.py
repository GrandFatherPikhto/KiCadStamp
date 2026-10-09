"""tools/door_lint.py ratchet: suspect board reads per file — may only
shrink. Rewritten by `python tools/door_lint.py --update-baseline`."""

SUSPECTS = {
    'gui/dock_hub.py': 3,
    'gui/docks/cell_anchor_view.py': 2,
    'gui/docks/cell_refs_tab.py': 1,
    'gui/docks/configurator.py': 1,
    'gui/docks/imprint.py': 2,
    'gui/docks/imprint_place.py': 1,
    'gui/docks/net_trace.py': 3,
    'gui/docks/placer.py': 1,
    'gui/docks/points.py': 5,
    'gui/docks/role_cluster_tree.py': 3,
    'gui/docks/trees_dock.py': 3,
    'gui/fieldstool_window.py': 1,
    'gui/worker.py': 1,
}

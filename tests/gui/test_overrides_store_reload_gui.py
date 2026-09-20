# tests/gui/test_overrides_store_reload_gui.py
"""С5, the behavioural half (plan_2026_09_19_poll_adapter_store_reload): the
WHOLE wired chain re-reads the override store when the write event fires.

Why a second file for the same rule: tests/test_overrides_store_reload.py proves
the event's own behaviour (С1–С4) and derives the INVENTORY of holders from the
source, but it deliberately builds no Qt dock. Two of the four holders are fed
TRANSITIVELY — Pending changes gets its copy from the fieldstool window, the
Refs tab from the cell editor — and that chain only exists in a real DockHub:
drop the push-in line and the non-GUI guards stay green.

So this test builds the real composition (bare QMainWindow + real BoardConnection
+ real DockHub, the same shape tests/gui/test_phase3_wiring.py uses), points it
at a throwaway project, and then walks EVERY holder by hand. It is the guard
М5 ("return one holder to the forgotten list") must turn red.

No live KiCad is involved and none must be: the poll adapter's layer is a real
FieldOverrideAdapter over an EMPTY stub, and nothing here calls it — which is
itself the point (door §2 of the plan: the reload is a FILE operation).
"""
import logging
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QMainWindow

from gui.connection import BoardConnection
from gui.dock_hub import DockHub
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import SOURCE_CELL_TABLE, load_field_overrides
from kicadstamp.utils.paths import overrides_path_for_config

UUID_R1 = "uuid-R1"


def _teardown_hub(hub):
    """Same leaks as test_phase3_wiring._teardown_hub: a DockHub embeds a real
    fieldstool window and a LogDock (root-logger handler), plus the log_file:
    FileHandler — which also holds an open handle into a tmp_path this test's
    teardown may have to delete (matters on Windows)."""
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


def test_c5_the_write_event_refreshes_every_holder_in_the_wired_gui(qapp, tmp_path):
    """Bind the store EMPTY (the project opens), record into the file through a
    FOREIGN store object (the shape the Refs table writes through), fire the one
    event — and then ask EVERY holder in the real hub what it is serving.

    The list below is the inventory of Т3, and it is the assertion that matters:
    `stale` collects the holders that still serve the pre-write store, so a
    missing stop — the third line (М1), or the push-in into a transitive holder
    (М5) — names itself instead of dying somewhere generic."""
    window = QMainWindow()
    connection = BoardConnection(timeout_ms=10)
    hub = DockHub(window, connection=connection, verbose=False)
    try:
        adapter = FieldOverrideAdapter(SimpleNamespace())
        connection.board = SimpleNamespace(adapter=adapter)

        root = tmp_path / "root.sexp"
        root.write_text(dict_to_sexp({}), encoding="utf-8")
        # The project opens: the window takes its own copy, the cell editor
        # takes one and hands it to its Refs tab, and the poll adapter is
        # REBOUND to the same (still empty) store.
        hub.root_metadata_dock.set_root_file(root)
        assert adapter.store is not None, "the poll adapter was never bound"

        other = load_field_overrides(overrides_path_for_config(str(root)))
        other.set(UUID_R1, "R1", ROLE_FIELD_NAME, "R_WRITTEN", SOURCE_CELL_TABLE)
        other.save()
        version_before = connection.snapshot_version

        hub._on_overrides_written()

        holders = {
            "fieldstool window": hub.fieldstool_dock.window.overrides,
            "Pending changes": hub.pending_dock._overrides,
            "cell editor (anchor page)": hub.cell_anchor_view._overrides,
            "cell editor's Refs table": hub.cell_anchor_view._refs_tab._overrides,
            # 2026-09-20 (Д2): the imprint page's Roles tab records into the same
            # store, so it must hear about a write made in ANY other pane.
            "imprint page's Roles tab": hub.imprint_dock.refs_tab._overrides,
            "poll adapter's bound store": adapter.store,
            "Components tree (through the window)": hub.tree_dock._overrides(),
        }
        stale = [name for name, store in holders.items()
                 if store is None or store.get(UUID_R1, ROLE_FIELD_NAME) != "R_WRITTEN"]
        assert stale == [], (
            "these holders still serve the store loaded at project open: "
            "{stale} — the GUI contradicts itself and С25 breaks for whatever "
            "reads them".format(stale=stale))

        # ... and none of it cost the board a thing (door §2): no snapshot was
        # rebuilt, no long op was started, the socket was never touched.
        assert connection.snapshot_version == version_before
        assert connection.long_op_active is False
    finally:
        _teardown_hub(hub)

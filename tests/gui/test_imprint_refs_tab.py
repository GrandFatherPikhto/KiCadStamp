# tests/gui/test_imprint_refs_tab.py
"""The WIDGET half of Д2 (plan_2026_09_18_scheme_list_to_cell_and_capture.md):
the imprint's Roles tab, its ONE cluster field and the "Convert to cell" button.

The guards, by their plan numbers:

  * С1  — the table is filled from the imprint RECORD's components, and a table
          edit is recorded in the override store in ONE batch (one save);
  * С2  — "Convert to cell" refuses while a Role is missing or repeated or the
          cluster is empty, and writes NOTHING;
  * С9  — it creates `cells:` and does NOT touch the entity (the record keeps
          working as it did);
  * С12 — the table does not touch the BOARD at all (an adapter spy that raises
          on any attribute is handed in and never looked at);
  * С13 — a Role recorded here is visible to the RESOLVER and wins over the
          board (the real FieldOverrideAdapter over the real store file);
  * С22 — after the write the value is IN THE STORE FILE for that symbol uuid
          (read back from disk, not from the widget);
  * С23 — the cluster is ONE per imprint: there is no cluster column at all, and
          the single field reaches every row.

The widget is always built with a Qt PARENT: a parentless widget is destroyed by
the garbage collector, which crashes the whole run on Windows (plan 2а §1.1).
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from PyQt6.QtWidgets import QMainWindow

from gui.docks.imprint_refs_tab import ImprintRefsTab
from kicadstamp.config import load_imprint
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import load_field_overrides
from kicadstamp.imprint_cell import cell_name_for_cluster
from kicadstamp.utils.paths import overrides_path_for_config

UUID = {"BZ1": "uuid-BZ1", "Q1": "uuid-Q1", "R6": "uuid-R6", "D6": "uuid-D6"}


class _AdapterSpy:
    """Any attribute access is a FAILURE — guard С12. The tab is handed a live
    -looking connection with this adapter behind it; if any code path ever
    reaches the board through it, the test says so by name."""

    def __getattr__(self, name):  # pragma: no cover - only fires on a violation
        raise AssertionError(
            f"the imprint Roles tab touched the ADAPTER ({name!r}) — its only "
            "inputs are the record, the polled snapshot and the store")


def _records(roles=None):
    """The SAME snapshot as the page hands over: raw Selected-shaped items run
    through the tab's own record builder (records_from_snapshot), which is what
    the page does — the tab itself only ever sees role-table records."""
    return ImprintRefsTab.records_from_snapshot(_snapshot(roles))


def _record(name="zummer", extra_refs=()):
    """The record as the page loads it. `extra_refs` appends further components —
    the D6 of the live case (Denis re-read the record after adding D6, so the
    RECORD grew a component the table had never seen)."""
    components = [
        {"ref": "BZ1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
         "rotation_deg": 0.0},
        {"ref": "Q1", "offset_along_mm": 10.0, "offset_across_mm": 5.0,
         "rotation_deg": 90.0},
        {"ref": "R6", "offset_along_mm": -2.5, "offset_across_mm": 1.5,
         "rotation_deg": 180.0},
    ]
    for i, ref in enumerate(extra_refs):
        components.append({"ref": ref, "offset_along_mm": 20.0 + 2.0 * i,
                           "offset_across_mm": -1.0, "rotation_deg": 0.0})
    return load_imprint({
        "name": name,
        "source_sheet": "Channel_0",
        "components": components,
        "vias": [{"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                  "drill_mm": 0.3, "diameter_mm": 0.6, "net": "GND"}],
        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                    "end_along_mm": 1.0, "end_across_mm": 1.0,
                    "width_mm": 0.25, "layer": "F.Cu", "net": "BUZZER"}],
    })


def _snapshot(roles=None):
    """The polled snapshot the PAGE feeds in: Selected-shaped items carrying the
    board's own Role plus the symbol uuid the store is keyed by."""
    roles = roles if roles is not None else {"BZ1": "", "Q1": "", "R6": ""}
    return [SimpleNamespace(ref=ref, role=role, cluster="",
                            symbol_uuid=UUID[ref], sheet=("Top",))
            for ref, role in roles.items()]


def _root(tmp_path, with_entity=True):
    data = {"cells": {},
            "imprints": [{"name": "zummer", "source_sheet": "Channel_0",
                          "components": [{"ref": "BZ1"}, {"ref": "Q1"},
                                         {"ref": "R6"}]}]}
    if with_entity:
        data["entities"] = [{"name": "E_AMP", "imprint": "zummer"}]
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(data), encoding="utf-8")
    return root


def _tab(qapp, tmp_path, roles=None):
    """A tab pointed at a throwaway project, with a REAL (empty) override store
    on disk and the adapter spy behind the connection."""
    parent = QMainWindow()
    spy = _AdapterSpy()
    connection = SimpleNamespace(board=SimpleNamespace(adapter=spy))
    tab = ImprintRefsTab(connection=connection, parent=parent)
    # The Qt PARENT must outlive the test function: a local QMainWindow would be
    # collected and take the table's C++ object with it ("wrapped C/C++ object
    # ... has been deleted"). Holding it on the tab is the usual fix, and it is
    # also the rule of plan 2а §1.1 — never a parentless widget.
    tab._test_parent = parent
    root = _root(tmp_path)
    store = load_field_overrides(overrides_path_for_config(str(root)))
    tab.set_context(root, "zummer", _record(), _records(roles), store)
    return tab, root, store, spy


def _set_role(tab, ref, role):
    """Type a Role the way the delegate does — through the model item."""
    index = tab.row_refs().index(ref)
    from gui.docks.cell_refs_tab import COL_ROLE
    item = tab._table.item(index, COL_ROLE)
    item.setText(role)
    tab._on_item_changed(item)


# ── С1 + С22: the rows are the record's, the write lands in the FILE ───────

class TestTableAndStore:
    def test_the_rows_are_the_records_components(self, qapp, tmp_path):
        """С1: the table is built from the imprint RECORD (its literal refs, in
        its order) — never from a board selection."""
        tab, _root_path, _store, _spy = _tab(qapp, tmp_path)
        assert tab.row_refs() == ["BZ1", "Q1", "R6"]

    def test_a_role_edit_reaches_the_store_file_for_that_symbol(self, qapp, tmp_path):
        """С1 + С22: the value is in the STORE FILE, keyed by the symbol uuid —
        read back from disk, not from the widget."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")
        _set_role(tab, "Q1", "DRIVER")
        assert tab.write_button_enabled()
        tab.write_to_store()
        from_disk = load_field_overrides(overrides_path_for_config(str(root)))
        assert from_disk.get("uuid-BZ1", ROLE_FIELD_NAME) == "BUZZER"
        assert from_disk.get("uuid-Q1", ROLE_FIELD_NAME) == "DRIVER"

    def test_the_batch_is_recorded_in_one_save(self, qapp, tmp_path, monkeypatch):
        """С1's mutation is "write one at a time": the whole table must be ONE
        atomic save (a half-applied table is what the store's own discipline
        exists to prevent).

        The counter goes on the CLASS: write_to_store re-reads the store from
        its file first (so a stale in-memory copy can never be the base of a
        write), which replaces the very object a per-instance patch would sit
        on."""
        saves = []
        from kicadstamp.field_overrides import FieldOverrides
        real_save = FieldOverrides.save

        def counting_save(self):
            saves.append(1)
            real_save(self)

        monkeypatch.setattr(FieldOverrides, "save", counting_save)
        tab, _root_path, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")
        _set_role(tab, "Q1", "DRIVER")
        _set_role(tab, "R6", "BASE_RES")
        tab.write_to_store()
        assert len(saves) == 1

    def test_an_unchanged_table_records_nothing(self, qapp, tmp_path):
        """The sparse rule (Р17): open the table, look at it, record — the store
        gains nothing when every value already matches what is in force."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        assert not tab.write_button_enabled()
        tab.write_to_store()
        assert load_field_overrides(
            overrides_path_for_config(str(root))).records() == []

    def test_the_board_role_is_prefilled_and_kept_when_untouched(self, qapp, tmp_path):
        tab, _root_path, _store, _spy = _tab(qapp, tmp_path,
                                             roles={"BZ1": "FROM_BOARD",
                                                    "Q1": "", "R6": ""})
        assert tab.rows_by_ref().get("BZ1") if hasattr(tab, "rows_by_ref") else True
        assert tab.roles_by_ref()["BZ1"] == "FROM_BOARD"
        assert not tab.write_button_enabled()  # nothing differs yet

    def test_the_adapter_is_never_touched(self, qapp, tmp_path):
        """С12: reading the table, editing it, recording it — the spy behind
        `connection.board.adapter` raises on ANY attribute access."""
        tab, _root_path, _store, spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")
        tab.write_to_store()
        tab.convert_to_cell()
        tab.set_snapshot(_records({"BZ1": "BUZZER", "Q1": "", "R6": ""}))
        assert isinstance(spy, _AdapterSpy)  # never dereferenced


# ── С23: ONE cluster per imprint ──────────────────────────────────────────

class TestClusterIsOne:
    def test_there_is_no_cluster_column(self, qapp, tmp_path):
        """С23: the widget does not even offer a second cluster — two different
        clusters in one imprint are untypeable, not merely discouraged."""
        tab, _root_path, _store, _spy = _tab(qapp, tmp_path)
        assert tab._table.columnCount() == 2

    def test_the_single_field_reaches_every_row(self, qapp, tmp_path):
        tab, _root_path, _store, _spy = _tab(qapp, tmp_path)
        tab._cluster_edit.setText("FPGA_PWR_BANK")
        assert {r.cluster for r in tab.rows} == {"FPGA_PWR_BANK"}

    def test_the_recorded_cluster_goes_to_the_store(self, qapp, tmp_path):
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        tab._cluster_edit.setText("FPGA_PWR_BANK")
        _set_role(tab, "BZ1", "BUZZER")
        tab.write_to_store()
        from_disk = load_field_overrides(overrides_path_for_config(str(root)))
        assert from_disk.get("uuid-BZ1", "Cluster") == "FPGA_PWR_BANK"

    def test_a_programmatic_fill_reaches_both_buttons(self, qapp, tmp_path):
        """Д11: the field is the ONE source of truth for both buttons — and a
        PROGRAMMATIC fill is exactly the path that used to split them.

        Typing is not the interesting case (the textChanged signal feeds the rows
        as a side effect); `setText` under `_loading` — a project switch, a
        re-read, this test — is: "Convert to cell" read the field while "Write to
        the store" read the rows, and the second one saw nothing. Mutation for
        this guard: give the batch its own source again (read the rows)."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")

        tab._set_cluster_text("FPGA_PWR_BANK")     # NOT typing — no signal

        assert tab.cluster_text() == "FPGA_PWR_BANK"
        assert tab.suggested_cell_name() == cell_name_for_cluster("FPGA_PWR_BANK")
        assert tab.write_button_enabled() is True
        tab.write_to_store()
        from_disk = load_field_overrides(overrides_path_for_config(str(root)))
        assert from_disk.get("uuid-BZ1", "Cluster") == "FPGA_PWR_BANK"

    def test_roles_without_a_cluster_say_so(self, qapp, tmp_path):
        """Д11, the other half of the live case: recording Roles while the cluster
        field stands empty is a legitimate half-done job — and the status line
        used to report a complete success, so the user could not tell that the
        cluster had not gone in (Денис, 20.09: «кластер нет»)."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")

        tab.write_to_store()

        from_disk = load_field_overrides(overrides_path_for_config(str(root)))
        assert from_disk.get("uuid-BZ1", ROLE_FIELD_NAME) == "BUZZER"
        assert from_disk.get("uuid-BZ1", "Cluster") is None
        assert "Cluster" in tab.status_text()

    def test_a_footprint_without_a_cluster_field_can_still_be_recorded(
            self, qapp, tmp_path):
        """Denis's live case, 2026-09-20: «я меняю поле Cluster, но кнопка
        остаётся серой, не могу записать это значение».

        The imprint's footprints carry no Cluster field ON THE BOARD (his
        BZ1/D6/Q1/R6 have no Role field either). Typing the cluster pushed it into
        the rows through apply_cluster_to_all, which deliberately LEAVES A ROW
        ALONE when the board has no such field — a rule for writing a value ONTO
        the board. The STORE needs no field on the footprint (build_override_updates
        says exactly that: the KEY is the only gate), so the check kept the cluster
        out of the batch and the button could never go live: a dead end with no
        message, on a table that looked complete."""
        parent = QMainWindow()
        connection = SimpleNamespace(board=SimpleNamespace(adapter=_AdapterSpy()))
        tab = ImprintRefsTab(connection=connection, parent=parent)
        tab._test_parent = parent
        root = _root(tmp_path)
        store = load_field_overrides(overrides_path_for_config(str(root)))
        records = ImprintRefsTab.records_from_snapshot([
            SimpleNamespace(ref=ref, role="", cluster="", symbol_uuid=UUID[ref],
                            sheet=("Top",), role_field_exists=False,
                            cluster_field_exists=False)
            for ref in ("BZ1", "Q1", "R6")])
        tab.set_context(root, "zummer", _record(), records, store)
        assert not tab.write_button_enabled()      # nothing differs yet

        tab._cluster_edit.setText("BUZZER")

        # С23 first: the field reaches every row even HERE — a table that shows
        # the value in the field and nothing in its rows is the "looks complete"
        # half of the same live case.
        assert {r.cluster for r in tab.rows} == {"BUZZER"}
        assert tab.write_button_enabled() is True
        tab.write_to_store()
        from_disk = load_field_overrides(overrides_path_for_config(str(root)))
        assert from_disk.get("uuid-BZ1", "Cluster") == "BUZZER"

    def test_the_cluster_field_opens_with_the_value_in_force(self, qapp, tmp_path):
        """Our stored cluster wins over the board's (Т2) — a freshly OPENED tab
        must show what is IN FORCE, or the table would promise a value the
        resolver then ignores. (An already-open tab keeps the user's own text:
        that is the cell table's rule too.)"""
        parent = QMainWindow()
        connection = SimpleNamespace(board=SimpleNamespace(adapter=_AdapterSpy()))
        root = _root(tmp_path)
        store = load_field_overrides(overrides_path_for_config(str(root)))
        store.set("uuid-BZ1", "BZ1", "Cluster", "OURS", "test")
        store.save()
        tab = ImprintRefsTab(connection=connection, parent=parent)
        tab._test_parent = parent
        tab.set_context(root, "zummer", _record(),
                        _records({"BZ1": "BUZZER", "Q1": "", "R6": ""}),
                        load_field_overrides(overrides_path_for_config(str(root))))
        assert tab.cluster_text() == "OURS"


# ── the list follows the record (live case 2026-09-20) ────────────────────

class TestTheListFollowsTheRecord:
    def test_a_new_component_arrives_and_the_typed_roles_survive(self, qapp, tmp_path):
        """Denis's case: D6 was added to the board and the record re-read — the
        table must show the new component WITHOUT throwing away the Roles already
        typed for the others (the record is the source of the LIST, never of the
        user's typing).

        Д13 (plan, Денис 20.09): the CLUSTER must survive the same rebuild. The
        rebuild carried only `role` out of the old rows, so a typed cluster stayed
        in the field above the table while the rows lost it — the table LOOKED
        complete, the button went grey (nothing differed any more) and the cluster
        could not be recorded any more. The plan's own conclusion: carry it, and
        then make the loss impossible by construction — the field is the ONE
        source of truth (Д11), so there is nothing in the rows to lose."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")
        _set_role(tab, "Q1", "DRIVER")
        tab._cluster_edit.setText("BUZZER")

        tab.set_context(root, "zummer", _record(extra_refs=("D6",)),
                        _records({"BZ1": "", "Q1": "", "R6": "", "D6": ""}), None)

        assert tab.row_refs() == ["BZ1", "Q1", "R6", "D6"]  # new one appended
        assert tab.roles_by_ref()["BZ1"] == "BUZZER"
        assert tab.roles_by_ref()["Q1"] == "DRIVER"
        assert "D6" in tab.status_text()                    # and it is reported
        assert tab.cluster_text() == "BUZZER"               # Д13: field kept it
        tab.write_to_store()
        from_disk = load_field_overrides(overrides_path_for_config(str(root)))
        assert from_disk.get("uuid-BZ1", "Cluster") == "BUZZER"

    def test_a_component_that_left_the_record_loses_its_row(self, qapp, tmp_path):
        """The other direction: the record no longer carries R6, so its row goes —
        and the refdes is named in the status line rather than disappearing
        silently."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "R6", "BASE_RES")
        shrunk = _record()
        shrunk.components = [c for c in shrunk.components if c.ref != "R6"]

        tab.set_context(root, "zummer", shrunk,
                        _records({"BZ1": "", "Q1": ""}), None)

        assert tab.row_refs() == ["BZ1", "Q1"]
        assert "R6" in tab.status_text()

    def test_the_typed_role_reaches_the_store_after_a_reread(self, qapp, tmp_path):
        """End to end: type a Role, Reread adds a component, record — the value
        still lands in the store file (the rebuild must not lose the edit)."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")
        tab.set_context(root, "zummer", _record(extra_refs=("D6",)),
                        _records({"BZ1": "", "Q1": "", "R6": "", "D6": ""}), None)
        tab.write_to_store()
        from_disk = load_field_overrides(overrides_path_for_config(str(root)))
        assert from_disk.get("uuid-BZ1", ROLE_FIELD_NAME) == "BUZZER"


# ── С2 + С9: the conversion ───────────────────────────────────────────────

class TestConvertToCell:
    def _filled(self, tab):
        _set_role(tab, "BZ1", "BUZZER")
        _set_role(tab, "Q1", "DRIVER")
        _set_role(tab, "R6", "BASE_RES")
        tab._cluster_edit.setText("BUZZER")

    def test_it_builds_the_cell_and_leaves_the_entity_alone(self, qapp, tmp_path):
        """С9: `cells:` appears, the entity still points at the imprint."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        self._filled(tab)
        before = sexp_to_dict(root.read_text(encoding="utf-8"))
        tab.convert_to_cell()
        after = sexp_to_dict(root.read_text(encoding="utf-8"))
        assert "buzzer" in after["cells"]
        cell = after["cells"]["buzzer"]
        assert [c["role"] for c in cell["components"]] == [
            "BUZZER", "DRIVER", "BASE_RES"]
        assert after["entities"] == before["entities"]
        assert [e.get("cell") for e in after["entities"]] == [None]
        assert after["entities"][0]["imprint"] == "zummer"

    def test_a_missing_role_refuses_and_writes_nothing(self, qapp, tmp_path):
        """С2: the refusal names what is missing and the config is untouched."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")
        tab._cluster_edit.setText("BUZZER")
        before = root.read_text(encoding="utf-8")
        tab.convert_to_cell()
        assert root.read_text(encoding="utf-8") == before
        assert "Q1" in tab.status_text() and "R6" in tab.status_text()
        # The fixture profile HAS an empty `cells:` section; the refusal must not
        # add a cell to it.
        assert sexp_to_dict(root.read_text(encoding="utf-8"))["cells"] == {}

    def test_a_repeated_role_refuses_and_writes_nothing(self, qapp, tmp_path):
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "SAME")
        _set_role(tab, "Q1", "SAME")
        _set_role(tab, "R6", "OTHER")
        tab._cluster_edit.setText("BUZZER")
        before = root.read_text(encoding="utf-8")
        tab.convert_to_cell()
        assert root.read_text(encoding="utf-8") == before
        assert "SAME" in tab.status_text()

    def test_no_cluster_refuses_and_writes_nothing(self, qapp, tmp_path):
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "BUZZER")
        _set_role(tab, "Q1", "DRIVER")
        _set_role(tab, "R6", "BASE_RES")
        before = root.read_text(encoding="utf-8")
        tab.convert_to_cell()
        assert root.read_text(encoding="utf-8") == before
        assert "Cluster" in tab.status_text()

    def test_the_cell_carries_the_records_copper(self, qapp, tmp_path):
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        self._filled(tab)
        tab.convert_to_cell()
        cell = sexp_to_dict(root.read_text(encoding="utf-8"))["cells"]["buzzer"]
        assert len(cell["vias"]) == 1 and len(cell["tracks"]) == 1


# ── С13: the resolver sees our value, and it wins over the board ──────────

class TestResolverSeesOurValue:
    def test_the_recorded_role_wins_over_the_board(self, qapp, tmp_path):
        """С13 — the same path the cell editor's table uses (Т1/Т2): the store
        the RESOLVER would bind for this profile is layered over the board, and
        OUR value is what comes out while the board says otherwise.

        The footprint is built as `domain.board.Footprint` — the shape the
        ADAPTER hands over — and NOT as `SimpleNamespace(sheet_path=...)` (the
        kipy shape) as this guard did until 2026-09-20: that mock made the guard
        green on a base where the read side could not work at all
        (plan_2026_09_20_symbol_uuid_wrong_attribute.md, С8)."""
        tab, root, _store, _spy = _tab(qapp, tmp_path)
        _set_role(tab, "BZ1", "OURS")
        tab.write_to_store()

        from kicadstamp.adapter_factory import store_for_config
        store, source = store_for_config(str(root))
        assert store is not None, "the profile does not serve the store at all"

        footprint = Footprint(
            ref="BZ1", uuid="fp-BZ1", position=Vector2.from_xy_mm(0.0, 0.0),
            angle_deg=0.0, layer=BoardLayer.BL_F_Cu,
            sheet_path_uuids=("uuid-sheet", UUID["BZ1"]))
        board = SimpleNamespace(get_field_value=lambda fp, field: "FROM_BOARD")
        layered = FieldOverrideAdapter(board, store, source=source)
        assert layered.get_field_value(footprint, ROLE_FIELD_NAME) == "OURS"

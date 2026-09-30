# tests/test_imprint_cell.py
"""The PURE half of "Convert to cell" (Д2 of
plan_2026_09_18_scheme_list_to_cell_and_capture.md).

kicadstamp/imprint_cell.py holds every decision of the conversion — what blocks
it, what the cells: entry looks like, what the cell is called — so it can be
pinned here without a QApplication, without a project and without a board. The
widget half (the table, the store write, the refusal that writes nothing) is
tests/gui/test_imprint_refs_tab.py.

The refusals are the interesting part: a cell whose roles are missing or
repeated CANNOT be loaded (config/entries.py::_load_cell fatals on a duplicate
role), so the button must refuse BEFORE building anything, and it must say every
reason at once.
"""
import pytest

from kicadstamp.config import load_cell, load_imprint
from kicadstamp.imprint_cell import (
    PROBLEM_DUPLICATE_ROLE,
    PROBLEM_NO_CLUSTER,
    PROBLEM_NO_ROLE,
    cell_name_for_cluster,
    effective_roles,
    imprint_cell_entry,
    imprint_cell_payload,
    imprint_cell_problems,
    imprint_to_cell_plan,
)


def _record_dict(name="zummer", pivot=None):
    d = {
        "name": name,
        "source_sheet": "Channel_0",
        "components": [
            {"ref": "BZ1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
            {"ref": "Q1", "offset_along_mm": 10.0, "offset_across_mm": 5.0,
             "rotation_deg": 90.0},
            {"ref": "R6", "offset_along_mm": -2.5, "offset_across_mm": 1.5,
             "rotation_deg": 180.0},
        ],
        "vias": [{"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                  "drill_mm": 0.3, "diameter_mm": 0.6, "net": "GND"}],
        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                    "end_along_mm": 1.0, "end_across_mm": 1.0,
                    "width_mm": 0.25, "layer": "B.Cu", "net": "BUZZER"}],
    }
    if pivot is not None:
        d["pivot"] = list(pivot)
    return d


def _record(**kwargs):
    return load_imprint(_record_dict(**kwargs))


ROLES = {"BZ1": "BUZZER", "Q1": "DRIVER", "R6": "BASE_RES"}


# ── the name ───────────────────────────────────────────────────────────────

class TestCellName:
    def test_the_cluster_slugs_the_same_way_the_gui_does(self):
        """The cell an imprint becomes is named after its cluster, with the slug
        the extract path already uses (DAC_BUF -> dac_buf). Pinned against the
        GUI helper, because the two must never drift into two conventions."""
        from gui.docks.tree_from_selection import cluster_cell_name

        for cluster in ("DAC_BUF", "FPGA_PWR_BANK", "zummer", " A B ", ""):
            assert cell_name_for_cluster(cluster) == cluster_cell_name(cluster)


# ── what blocks the conversion ─────────────────────────────────────────────

class TestProblems:
    def test_a_filled_imprint_has_no_problems(self):
        assert imprint_cell_problems(["BZ1", "Q1", "R6"], ROLES, "BUZZER") == []

    def test_a_missing_role_is_named(self):
        problems = imprint_cell_problems(["BZ1", "Q1", "R6"],
                                         {"BZ1": "BUZZER", "Q1": "DRIVER"},
                                         "BUZZER")
        assert len(problems) == 1
        assert "R6" in problems[0]
        assert PROBLEM_NO_ROLE.replace("_", " ") not in problems[0]  # a sentence

    def test_a_repeated_role_is_named_with_its_refs(self):
        problems = imprint_cell_problems(
            ["BZ1", "Q1", "R6"], {"BZ1": "X", "Q1": "X", "R6": "Y"}, "BUZZER")
        assert len(problems) == 1
        assert "X" in problems[0] and "BZ1" in problems[0] and "Q1" in problems[0]

    def test_no_cluster_is_a_problem(self):
        problems = imprint_cell_problems(["BZ1"], {"BZ1": "BUZZER"}, "   ")
        assert len(problems) == 1
        assert "Cluster" in problems[0]

    def test_every_reason_comes_at_once(self):
        """One press must tell the user EVERYTHING that is wrong (the plan's
        "отказ строкой с перечнем"), not one reason per press: a missing role,
        a repeated one and no cluster come back together."""
        problems = imprint_cell_problems(
            ["BZ1", "Q1", "R6"], {"BZ1": "X", "Q1": "X"}, "")
        assert len(problems) == 3
        joined = " ".join(problems)
        assert "R6" in joined and "X" in joined and "Cluster" in joined

    def test_problem_keys_exist_for_the_widget(self):
        """The keys are the model's own vocabulary (the widget/tests may branch
        on them; the sentences stay in the catalogues)."""
        assert {PROBLEM_NO_ROLE, PROBLEM_DUPLICATE_ROLE, PROBLEM_NO_CLUSTER}

    def test_empty_roles_do_not_count_as_a_role(self):
        assert effective_roles(["BZ1", "Q1"], {"BZ1": " ", "Q1": "X"}) == {"Q1": "X"}


# ── the entry ──────────────────────────────────────────────────────────────

class TestCellEntry:
    def test_the_entry_is_a_loadable_cell(self):
        entry = imprint_cell_entry(_record(), ROLES, "BUZZER")
        cell = load_cell("zummer", entry)  # the real loader is the judge
        assert [c.role for c in cell.components] == ["BUZZER", "DRIVER", "BASE_RES"]
        assert [c.offset_along_mm for c in cell.components] == [0.0, 10.0, -2.5]
        assert [c.angle_deg for c in cell.components] == [0.0, 90.0, 180.0]
        assert len(cell.vias) == 1 and cell.vias[0].net == "GND"
        assert len(cell.tracks) == 1 and cell.tracks[0].layer == "B.Cu"
        assert cell.tracks[0].net == "BUZZER"

    def test_the_pivot_becomes_the_mount_point(self):
        entry = imprint_cell_entry(_record(pivot=(1.5, -2.5)), ROLES, "BUZZER")
        assert entry["anchor_xy"] == [1.5, -2.5]

    def test_a_default_pivot_writes_no_anchor(self):
        assert "anchor_xy" not in imprint_cell_entry(_record(), ROLES, "BUZZER")

    def test_the_cell_layer_is_left_to_the_default(self):
        """The side of the copper is a decision about the PLACEMENT (mirror) —
        the conversion builds a template and does not pick a board side."""
        assert "layer" not in imprint_cell_entry(_record(), ROLES, "BUZZER")

    def test_a_record_without_copper_still_converts(self):
        d = _record_dict()
        d.pop("vias")
        d.pop("tracks")
        entry = imprint_cell_entry(load_imprint(d), ROLES, "BUZZER")
        assert "vias" not in entry and "tracks" not in entry
        load_cell("zummer", entry)


# ── the payload / plan ─────────────────────────────────────────────────────

class TestPlan:
    def test_the_plan_names_the_cell_and_carries_it(self):
        plan = imprint_to_cell_plan(_record(), ROLES, "BUZZER")
        assert plan["problems"] == []
        assert plan["name"] == "buzzer"
        assert list(plan["cells"]) == ["buzzer"]
        assert plan["summary"] == {"components": 3, "vias": 1, "tracks": 1}

    def test_a_refused_plan_carries_nothing_to_write(self):
        plan = imprint_to_cell_plan(_record(), {"BZ1": "BUZZER"}, "BUZZER")
        assert plan["problems"] and plan["cells"] == {} and plan["name"] is None

    def test_the_payload_raises_without_the_check(self):
        """The caller checks the problems first; the payload is the
        "you forgot the check" guard, so it must not build a half-entry."""
        with pytest.raises(ValueError):
            imprint_cell_payload(_record(), {}, "BUZZER")

    def test_an_explicit_name_wins(self):
        plan = imprint_to_cell_plan(_record(), ROLES, "BUZZER", name="drv_stage")
        assert plan["name"] == "drv_stage"

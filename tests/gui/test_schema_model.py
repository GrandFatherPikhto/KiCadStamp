# tests/gui/test_schema_model.py
from gui.schema_model import load_schematic_components, load_schematic_instances
from tests.fakes.schematic_text import sch_file, symbol_block


def test_one_row_per_ref_simple(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="R_A", cluster="Cl_A")), encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert len(comps) == 1
    assert comps[0].ref == "R1" and comps[0].role == "R_A" and comps[0].cluster == "Cl_A"
    assert comps[0].divergent is False


def test_multi_instance_block_expands_to_one_row_per_ref(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R41", "R50", "R59"], role="R_A")), encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert {c.ref for c in comps} == {"R41", "R50", "R59"}
    assert all(c.role == "R_A" for c in comps)


def test_multi_unit_ref_collapses_to_one_row(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["U1"], role="OA_A"), symbol_block(["U1"], role="OA_A")),
                     encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert len(comps) == 1
    assert comps[0].ref == "U1" and comps[0].divergent is False


def test_multi_unit_divergent_values_are_flagged(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["U1"], role="OA_A"), symbol_block(["U1"], role="OA_B")),
                     encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert len(comps) == 1
    assert comps[0].divergent is True


def test_component_with_no_role_or_cluster(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["C1"])), encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert comps[0].role is None and comps[0].cluster is None


def test_power_symbols_are_excluded(tmp_path):
    """Refs starting with "#" (power symbols/PWR_FLAG, e.g. #PWR01, #FLG01)
    are KiCad's own "excluded from board" convention — no footprint ever
    exists for them, so they'd otherwise show up as pure noise (found live
    2026-08-03: 171 unfilterable "#FLG*" rows on a real board)."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(
        symbol_block(["R1"], role="R_A"),
        symbol_block(["#FLG01"]),
        symbol_block(["#PWR01"]),
    ), encoding="utf-8")

    comps = load_schematic_components(str(root))

    assert {c.ref for c in comps} == {"R1"}


def test_components_across_multiple_sheets(tmp_path):
    child = tmp_path / "child.kicad_sch"
    child.write_text(sch_file(symbol_block(["C1"], role="C_A")), encoding="utf-8")
    root = tmp_path / "root.kicad_sch"
    from tests.fakes.schematic_text import sheet_block
    root.write_text(sch_file(symbol_block(["R1"], role="R_A"), sheet_block("child.kicad_sch")),
                     encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert {c.ref for c in comps} == {"R1", "C1"}


def test_symbol_uuids_collected_from_blocks(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(
        symbol_block(["R1"], role="R_A", symbol_uuid="AAA-111"),
        symbol_block(["U1"], role="OA", symbol_uuid="BBB-222"),
    ), encoding="utf-8")

    comps = load_schematic_components(str(root))
    by_ref = {c.ref: c for c in comps}

    assert by_ref["R1"].symbol_uuids == ("AAA-111",)
    assert by_ref["U1"].symbol_uuids == ("BBB-222",)


def test_multi_unit_ref_union_of_symbol_uuids(tmp_path):
    """A ref spanning several blocks (multi-unit symbol) carries the union of
    the blocks' top-level uuids — the identity guard needs all of them."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(
        symbol_block(["U1"], role="OA_A", symbol_uuid="AAA-1"),
        symbol_block(["U1"], role="OA_B", symbol_uuid="BBB-2"),
    ), encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert comps[0].symbol_uuids == ("AAA-1", "BBB-2")


def test_multi_instance_block_shared_uuid(tmp_path):
    """A multi-instance sheet carries several refdes in ONE block — all of
    them share that block's top-level uuid (the master symbol's own id)."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(
        symbol_block(["R41", "R50", "R59"], role="R_A", symbol_uuid="MASTER-UUID"),
    ), encoding="utf-8")

    comps = load_schematic_components(str(root))
    by_ref = {c.ref: c for c in comps}

    assert by_ref["R41"].symbol_uuids == ("MASTER-UUID",)
    assert by_ref["R59"].symbol_uuids == ("MASTER-UUID",)


# ── load_schematic_instances: the full-path (per-instance) index ────────────

def test_load_schematic_instances_full_path_index(tmp_path):
    """Key = (instances path minus root) + block top-level uuid — the same
    shape a board footprint's sheet_path.path has. The instances path's LAST
    element is the SHEET uuid (real format); the symbol's own uuid is appended
    by _full_key. Each instance maps to its own refdes with the block's Role."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(
        ["C140", "C161"], role="C_OUT_BULK", symbol_uuid="SYM-1",
        instance_paths=[("C140", "/root-uuid/inst-A/SHEET-1"),
                        ("C161", "/root-uuid/inst-B/SHEET-1")])),
        encoding="utf-8")

    index = load_schematic_instances(str(root))

    assert set(index) == {("inst-A", "SHEET-1", "SYM-1"),
                          ("inst-B", "SHEET-1", "SYM-1")}
    assert index[("inst-A", "SHEET-1", "SYM-1")].ref == "C140"
    assert index[("inst-A", "SHEET-1", "SYM-1")].role == "C_OUT_BULK"
    assert index[("inst-B", "SHEET-1", "SYM-1")].ref == "C161"


def test_load_schematic_instances_root_sheet_key_is_symbol_uuid(tmp_path):
    """A root-sheet symbol's instances path is just the root uuid; the full
    key collapses to just the block's top-level uuid (the board path for it)."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(
        ["H3"], symbol_uuid="H3-UUID",
        instance_paths=[("H3", "/root-uuid")])),
        encoding="utf-8")

    index = load_schematic_instances(str(root))

    assert set(index) == {("H3-UUID",)}
    assert index[("H3-UUID",)].ref == "H3"


def test_load_schematic_instances_power_symbols_excluded(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(
        ["#PWR01"], symbol_uuid="PWR-1",
        instance_paths=[("#PWR01", "/root-uuid")])),
        encoding="utf-8")

    assert load_schematic_instances(str(root)) == {}

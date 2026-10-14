# tests/gui/test_reead.py
"""Pure-logic tests for the FULLY-SELECTED cluster detection core
(gui/docks/reead.py, no Qt). Phase F (2026-09-01): "Re-read selected..." was
merged into "Extract tree..." (the Extract dock was removed) — this module is
now the shared cluster-detection core BOTH features used, and it stays covered
independently of the GUI (see plan_2026_09_01_extract_tree_rework_phaseF_
cells_dock_audit.md §6.6: "core-пути ... reead детект ... покрыты тестами
независимо от GUI").

The core scenario is Denis's real board: PIF_AVDD placed THREE times on
Channel_0/1/2 (entities CH0/CH1/CH2_PIF_AVDD, all with cell dac_pif_avdd).
The profile stores neither cluster nor sheet, so the SELECTION (cluster tag +
sheet) is what picks the instance — this module finds which clusters are FULLY
selected and maps each to its entity -> cell -> extract_profiles recipe.
"""
from gui.docks.reead import (
    REASON_SHEET_UNRESOLVED,
    fully_selected_clusters,
    match_entity,
)
from kicadstamp.config.models import Entity
from kicadstamp.explore import Selected
from kicadstamp.sheet_names import ROOT_SHEET


def _sel(ref, cluster, sheet):
    """A Selected footprint whose sheet chain is a single segment (the test
    convenience; the matching convention is 'entity.sheet in fp.sheet', the
    same one Board.select(sheet=) uses)."""
    return Selected(ref=ref, role=None, cluster=cluster, sheet=[sheet], nets={}, fp=object())


def _entity(name, cell, cluster, sheet):
    return Entity(name=name, cell=cell, cluster=cluster, sheet=sheet)


def _pif_entities():
    return [
        _entity("CH0_PIF_AVDD", "dac_pif_avdd", "PIF_AVDD", "Channel_0"),
        _entity("CH1_PIF_AVDD", "dac_pif_avdd", "PIF_AVDD", "Channel_1"),
        _entity("CH2_PIF_AVDD", "dac_pif_avdd", "PIF_AVDD", "Channel_2"),
    ]


def test_fully_selected_cluster_maps_to_entity_cell_and_profile():
    """The PIF_AVDD case: a FULL selection of Channel_1's cluster maps to ITS
    entity (by sheet), the entity's cell, and the extract_profiles key for it —
    even though Channel_0/2 share the same cluster tag and cell."""
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1"), _sel("C1", "PIF_AVDD", "Channel_1"),
        _sel("R2", "PIF_AVDD", "Channel_0"), _sel("C2", "PIF_AVDD", "Channel_0"),
    ]
    selected = [_sel("R1", "PIF_AVDD", "Channel_1"), _sel("C1", "PIF_AVDD", "Channel_1")]
    clusters = fully_selected_clusters(selected, snapshot, _pif_entities(), ["dac_pif_avdd"])

    assert len(clusters) == 1
    c = clusters[0]
    assert c.cluster == "PIF_AVDD"
    assert c.sheet == "Channel_1"
    assert c.entity_name == "CH1_PIF_AVDD"
    assert c.cell == "dac_pif_avdd"
    assert c.profile_key == "dac_pif_avdd"
    assert c.refs == ["R1", "C1"]


def test_partial_cluster_selection_is_not_fully_selected():
    """Only ONE of the cluster's two components is selected -> not "fully" ->
    no row (a re-read must capture the WHOLE cluster)."""
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_1"), _sel("C1", "PIF_AVDD", "Channel_1"),
    ]
    selected = [_sel("R1", "PIF_AVDD", "Channel_1")]
    clusters = fully_selected_clusters(selected, snapshot, _pif_entities(), ["dac_pif_avdd"])
    assert clusters == []


def test_two_full_channels_both_listed_separately():
    """Two channels fully selected -> two SEPARATE rows (one per sheet
    instance), never merged into one PIF_AVDD row."""
    snapshot = [
        _sel("R1", "PIF_AVDD", "Channel_0"), _sel("C1", "PIF_AVDD", "Channel_0"),
        _sel("R2", "PIF_AVDD", "Channel_1"), _sel("C2", "PIF_AVDD", "Channel_1"),
    ]
    selected = snapshot
    clusters = fully_selected_clusters(selected, snapshot, _pif_entities(), ["dac_pif_avdd"])
    assert [(c.sheet, c.entity_name) for c in clusters] == [
        ("Channel_0", "CH0_PIF_AVDD"), ("Channel_1", "CH1_PIF_AVDD")]


def test_cell_falls_back_to_cluster_slug_when_no_entity():
    """A cluster with no matching Entity falls back to the slugified Cluster as
    the cell name and gets no profile key — the re-read still works (auto)."""
    snapshot = [_sel("R1", "MY_NEW", "Root"), _sel("C1", "MY_NEW", "Root")]
    selected = snapshot
    clusters = fully_selected_clusters(selected, snapshot, [], [])
    assert len(clusters) == 1
    c = clusters[0]
    assert c.cluster == "MY_NEW"
    assert c.sheet == "Root"
    assert c.entity_name is None
    assert c.cell == "my_new"
    assert c.profile_key is None


class _FakeFP:
    """Raw footprint exposing sheet_path_uuids for resolve_sheet_path_names."""

    def __init__(self, uuids):
        self.sheet_path_uuids = uuids


def test_sheet_names_resolve_gui_stale_chains():
    """Live bug 2026-08-31: the GUI's BoardConnection connects WITHOUT
    schematic_dir, so its snapshot's Selected.sheet chains are all None and the
    three PIF_AVDD instances can't be told apart by sheet. The fix: pass the
    config's RuntimeContext sheet_names, re-resolve each footprint's sheet from
    its raw fp, and the Channel_0 instance is found again."""
    sheet_names = {
        "root-uuid": "root", "ch0-uuid": "Channel_0", "dac-uuid": "DAC", "c0-uuid": "comp0",
        "ch1-uuid": "Channel_1", "c1-uuid": "comp1",
    }

    def _sel_gui(ref, cluster, uuids):
        # fp carries the sheet path; sheet=[None] mimics the GUI snapshot.
        return Selected(ref=ref, role=None, cluster=cluster, sheet=[None], nets={},
                        fp=_FakeFP(uuids))

    snapshot = [
        _sel_gui("R1", "PIF_AVDD", ["root-uuid", "ch0-uuid", "dac-uuid", "c0-uuid"]),
        _sel_gui("C1", "PIF_AVDD", ["root-uuid", "ch0-uuid", "dac-uuid", "c0-uuid"]),
        _sel_gui("R2", "PIF_AVDD", ["root-uuid", "ch1-uuid", "dac-uuid", "c1-uuid"]),
        _sel_gui("C2", "PIF_AVDD", ["root-uuid", "ch1-uuid", "dac-uuid", "c1-uuid"]),
    ]
    selected = snapshot[:2]  # Channel_0 only, fully
    clusters = fully_selected_clusters(selected, snapshot, _pif_entities(),
                                       ["dac_pif_avdd"], sheet_names=sheet_names)

    assert len(clusters) == 1
    assert clusters[0].sheet == "Channel_0"
    assert clusters[0].entity_name == "CH0_PIF_AVDD"
    assert clusters[0].cell == "dac_pif_avdd"


# ── ROOT-sheet clusters (plan_2026_10_13_root_sheet_clusters, Ч1) ───────────

def _gui_root_sel(ref, cluster):
    """A Selected standing on the ROOT sheet as the GUI snapshot hands it over:
    the chain is all-None and the raw fp carries ONE hop — the symbol's own uuid
    — so the re-resolution (resolve_sheet_path_names) yields an EMPTY chain."""
    return Selected(ref=ref, role=None, cluster=cluster, sheet=[None], nets={},
                    fp=_FakeFP((f"{ref}-own",)))


def _sel_root(ref, cluster):
    """A Selected whose ALREADY-RESOLVED chain is empty (the CLI/caller shape:
    Board.select builds it with resolve_sheet_path_names, which returns [] for a
    root symbol whatever the dictionary holds)."""
    return Selected(ref=ref, role=None, cluster=cluster, sheet=[], nets={},
                    fp=object())


def test_denis_fact_four_mounting_holes_on_the_root_are_extracted():
    """Denis 13.10, live board: H1–H4 (Cluster MOUNTING_HOLES) stand on the ROOT
    sheet and "Extract cluster" refused them with "its sheet did not resolve".

    A root symbol has NOTHING above it to resolve — its chain is EMPTY — so the
    cluster must be extracted, with the ROOT_SHEET identity and no rejection at
    all. `schematic_dir` is set (the sheets map exists) exactly as on the board
    where Denis hit this."""
    refs = ["H1", "H2", "H3", "H4"]
    snapshot = [_gui_root_sel(ref, "MOUNTING_HOLES") for ref in refs]
    rejections: list = []
    clusters = fully_selected_clusters(snapshot, snapshot, [], [],
                                       sheet_names={"ch0": "Channel_0"},
                                       rejections=rejections)

    assert rejections == []
    assert len(clusters) == 1
    c = clusters[0]
    assert c.cluster == "MOUNTING_HOLES"
    assert c.sheet == ROOT_SHEET
    assert c.entity_name is None            # no entities entry — auto-derived
    assert c.cell == "mounting_holes"       # the Cluster slug
    assert sorted(c.refs) == refs


def test_a_root_cluster_needs_no_sheet_map_at_all():
    """The chain of a root symbol has no entry to look up, so an EMPTY sheet map
    (no schematic_dir) is no obstacle. The old gate answered
    REASON_SHEET_MAP_EMPTY for the root group too; it must not any more."""
    snapshot = [_sel_root("H1", "MOUNTING_HOLES"), _sel_root("H2", "MOUNTING_HOLES")]
    rejections: list = []
    clusters = fully_selected_clusters(snapshot, snapshot, [], [],
                                       sheet_names={}, rejections=rejections)

    assert [c.sheet for c in clusters] == [ROOT_SHEET]
    assert [r.reason for r in rejections] == []


def test_the_same_tag_on_the_root_and_in_a_sub_sheet_is_two_instances():
    """One Cluster tag standing BOTH on the root sheet and on a sub-sheet must
    never be one instance: two groups, and neither holds the other's refs.
    The membership rule asks the chain — an EMPTY chain is the root, a chain
    naming MCU is MCU, and the two are disjoint."""
    snapshot = [
        _sel_root("H1", "MOUNTING_HOLES"), _sel_root("H2", "MOUNTING_HOLES"),
        _sel("H3", "MOUNTING_HOLES", "MCU"), _sel("H4", "MOUNTING_HOLES", "MCU"),
    ]
    rejections: list = []
    clusters = fully_selected_clusters(snapshot, snapshot, [], [],
                                       rejections=rejections)

    assert rejections == []
    assert {c.sheet: sorted(c.refs) for c in clusters} == {
        ROOT_SHEET: ["H1", "H2"], "MCU": ["H3", "H4"]}


def test_a_genuinely_unresolved_uuid_keeps_the_old_reason():
    """Denis's rule: "не разрешилось" is ONLY when a uuid IS in the chain and the
    dictionary cannot name it. Such a group keeps REASON_SHEET_UNRESOLVED — the
    root marker must not swallow it (that would be the mirror-image bug)."""
    fp = _FakeFP(("unknown-uuid", "own-uuid"))
    sel = Selected(ref="C1", role=None, cluster="PIF_AVDD", sheet=[None],
                   nets={}, fp=fp)
    rejections: list = []
    clusters = fully_selected_clusters([sel], [sel], [], [],
                                       sheet_names={"ch0": "Channel_0"},
                                       rejections=rejections)

    assert clusters == []
    assert [r.reason for r in rejections] == [REASON_SHEET_UNRESOLVED]


def test_an_entity_on_the_root_matches_only_the_root_footprint():
    """match_entity through the ONE membership rule: an Entity carrying
    ROOT_SHEET names the root footprint (EMPTY chain) and NOT a sub-sheet one,
    and a sub-sheet Entity names only its own channel — the two can never be
    each other's instance, which is what makes the same tag on both safe."""
    entities = [
        _entity("MH_ROOT", "mounting_holes", "MOUNTING_HOLES", ROOT_SHEET),
        _entity("MH_MCU", "mounting_holes", "MOUNTING_HOLES", "MCU"),
    ]
    assert match_entity(entities, "MOUNTING_HOLES", []) is entities[0]
    assert match_entity(entities, "MOUNTING_HOLES", ["MCU"]) is entities[1]
    assert match_entity(entities, "OTHER", []) is None

# tests/gui/test_extract_selection_diagnostics.py
"""Extract-selection diagnostics (plan_2026_09_11_extract_selection_diagnostics):
the four gates that used to collapse into ONE misleading "select ALL components"
message now name the real cause.

- engine: gui/docks/reead.py::fully_selected_clusters fills a structured
  `rejections` out-list (one ClusterRejection per dropped group);
- presentation: gui/docks/extract_diagnostics.py turns them into text.

Denis's live case (V.0) is the fixture of test #2: a flawless 5-component
selection of PIF_DVDD, but the config has no schematic_dir, so every sheet
chain is None and the cluster can never be recognised.
"""
import logging
from types import SimpleNamespace

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.explore import Selected

import gui.dock_hub as dock_hub_mod
from gui.docks.extract_diagnostics import (
    format_cluster_rejections,
    rejections_log_detail,
)
from gui.docks.reead import (
    REASON_NO_CLUSTER_TAG,
    REASON_PAIR_NOT_IN_SNAPSHOT,
    REASON_PARTIAL,
    REASON_SHEET_MAP_EMPTY,
    REASON_SHEET_UNRESOLVED,
    fully_selected_clusters,
)


def _sel(ref, cluster, sheet):
    return Selected(ref=ref, role=None, cluster=cluster, sheet=[sheet], nets={},
                    fp=object())


def _sel_chain(ref, cluster, chain):
    return Selected(ref=ref, role=None, cluster=cluster, sheet=list(chain),
                    nets={}, fp=object())


def _detect(selected, snapshot, sheet_names=None, entities=(), profiles=()):
    rejections: list = []
    clusters = fully_selected_clusters(
        selected, snapshot, entities, profiles,
        sheet_names=sheet_names, rejections=rejections)
    return clusters, rejections, format_cluster_rejections(rejections)


# ── (0) no Cluster tag ────────────────────────────────────────────────────

def test_gate0_no_cluster_tag_names_the_untagged_footprints():
    selected = [_sel("R1", None, "Channel_0"), _sel("C1", None, "Channel_0")]
    clusters, rejections, message = _detect(selected, selected)

    assert clusters == []
    assert [r.reason for r in rejections] == [REASON_NO_CLUSTER_TAG]
    assert rejections[0].refs == ["R1", "C1"]
    assert "without a Cluster tag" in message
    assert "R1" in message and "C1" in message


# ── (1a) empty sheet map: the Denis regression ────────────────────────────

def test_gate1a_empty_schematic_dir_is_named():
    """Denis's V.0 numbers: a flawless PIF_DVDD selection of 5, but no
    schematic_dir in the config -> every chain is [None, None]."""
    refs = ["C131", "C132", "C133", "C134", "FB15"]
    roles = ["C_IN_BULK", "C_IN_BYPASS", "C_OUT_BULK", "C_OUT_BYPASS", "FB_PI_FLT"]
    selected = [_sel_chain(ref, "PIF_DVDD", [None, None]) for ref in refs]
    clusters, rejections, message = _detect(selected, selected, sheet_names={})

    assert clusters == []
    assert [r.reason for r in rejections] == [REASON_SHEET_MAP_EMPTY]
    assert rejections[0].cluster == "PIF_DVDD"
    assert "schematic_dir" in message
    assert "cannot be told apart" in message


# ── (1b) sheet unresolved although schematic_dir IS set ───────────────────

def test_gate1b_sheet_unresolved_with_a_non_empty_map_is_worded_differently():
    selected = [_sel("R1", "PIF_AVDD", None)]
    clusters, rejections, message = _detect(
        selected, selected, sheet_names={"some-uuid": "Channel_0"})

    assert clusters == []
    assert rejections[0].reason == REASON_SHEET_UNRESOLVED
    assert "did not resolve" in message
    assert "schematic_dir is set" in message
    # NOT the "no schematic_dir" wording.
    assert "has no schematic_dir" not in message


# ── (2) the (Cluster, sheet) pair is unknown to the snapshot ───────────────

def test_gate2_pair_not_in_snapshot_names_cluster_and_sheet():
    selected = [_sel("R1", "PIF_AVDD", "Channel_1"),
                _sel("C1", "PIF_AVDD", "Channel_1")]
    snapshot = [_sel("R9", "PIF_AVDD", "Channel_9")]   # another instance only
    clusters, rejections, message = _detect(selected, snapshot)

    assert clusters == []
    assert rejections[0].reason == REASON_PAIR_NOT_IN_SNAPSHOT
    assert "PIF_AVDD" in message and "Channel_1" in message
    assert "not in the board snapshot" in message


# ── (3) partial selection names cluster, sheet and the missing refs ───────

def test_gate3_partial_names_missing_ref():
    snapshot = [_sel(ref, "PIF_DVDD", "Channel_0")
                for ref in ["C131", "C132", "C133", "C134", "FB15"]]
    selected = snapshot[:4]   # FB15 missing
    clusters, rejections, message = _detect(selected, snapshot)

    assert clusters == []
    rej = rejections[0]
    assert rej.reason == REASON_PARTIAL
    assert (rej.selected, rej.total, rej.missing) == (4, 5, ["FB15"])
    assert "PIF_DVDD" in message and "Channel_0" in message
    assert "selected 4 of 5" in message
    assert "FB15" in message


# ── several causes at once -> all shown ───────────────────────────────────

def test_multiple_reasons_are_all_reported():
    untagged = [_sel("X1", None, "S")]
    partial = [_sel("R1", "PIF_DVDD", "Channel_0")]
    partial_snapshot = [_sel("R1", "PIF_DVDD", "Channel_0"),
                        _sel("R2", "PIF_DVDD", "Channel_0")]
    pair = [_sel("Q1", "OTHER", "Channel_3")]

    selected = untagged + partial + pair
    snapshot = partial_snapshot
    clusters, rejections, message = _detect(selected, snapshot)

    assert clusters == []
    reasons = {r.reason for r in rejections}
    assert reasons == {REASON_NO_CLUSTER_TAG, REASON_PARTIAL,
                       REASON_PAIR_NOT_IN_SNAPSHOT}
    # Every cause has a line — never just the first.
    assert message.count("\n") == 2
    assert "without a Cluster tag" in message
    assert "selected 1 of 2" in message
    assert "not in the board snapshot" in message


# ── the long list is truncated on screen, full in the log ─────────────────

def test_long_missing_ref_list_is_truncated_on_screen_but_full_in_log():
    refs = [f"R{i}" for i in range(12)]
    snapshot = [_sel(ref, "BIG", "S") for ref in refs]
    selected = snapshot[:2]   # 10 missing
    clusters, rejections, message = _detect(selected, snapshot)

    assert clusters == []
    assert "(+2 more)" in message
    # Truncated on screen (the missing list is sorted, so the first 8 are
    # R0,R1,R10,R11,R2,R3,R4,R5) — a late ref is absent from the message...
    assert "R9" not in message
    # ...but the Log detail has every MISSING ref (the selected R0/R1 are not
    # in the "missing" list, of course).
    detail = rejections_log_detail(rejections)
    assert all(ref in detail for ref in refs[2:])


# ── success unchanged ─────────────────────────────────────────────────────

def test_success_is_unchanged_and_reports_no_rejection():
    snapshot = [_sel("R1", "PIF_AVDD", "Channel_1"),
                _sel("C1", "PIF_AVDD", "Channel_1")]
    clusters, rejections, message = _detect(snapshot, snapshot)

    assert len(clusters) == 1
    assert clusters[0].refs == ["R1", "C1"]
    assert rejections == []
    assert message == ""


# ── V.3: one informational Log line for a config without schematic_dir ────

def test_v3_logs_once_when_a_board_is_live_and_schematic_dir_is_absent(
        real_main_window, tmp_path, monkeypatch):
    """A root config without schematic_dir silently disables sheet narrowing —
    one Log line (never a modal), once per root, only with a live board."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({}), encoding="utf-8")
    real_main_window.root_metadata_dock.set_root_file(root)

    infos: list = []
    monkeypatch.setattr(dock_hub_mod.logging, "info",
                        lambda msg, *a, **k: infos.append(msg % a if a else msg))

    hub = real_main_window._dock_hub
    # No live board yet: the root load above must NOT have warned.
    assert infos == []

    real_main_window.connection = SimpleNamespace(
        board=object(), snapshot=[], long_op_active=False)
    hub.set_board_selection([], [])
    assert sum("sheet-based narrowing" in m for m in infos) == 1

    # A second tick is a no-op for the SAME root.
    hub.set_board_selection([], [])
    assert sum("sheet-based narrowing" in m for m in infos) == 1


def test_v3_does_not_warn_when_schematic_dir_resolves_sheets(
        real_main_window, tmp_path, monkeypatch):
    """A config WITH schematic_dir (real sheet files) is left alone."""
    sch = tmp_path / "sch"
    sch.mkdir()
    (sch / "root.kicad_sch").write_text(
        '(kicad_sch\n'
        '  (sheet\n'
        '    (uuid "11111111-1111-1111-1111-111111111111")\n'
        '    (property "Sheetname" "Channel_0"))\n'
        ')\n',
        encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"schematic_dir": "sch"}), encoding="utf-8")
    real_main_window.root_metadata_dock.set_root_file(root)

    infos: list = []
    monkeypatch.setattr(dock_hub_mod.logging, "info",
                        lambda msg, *a, **k: infos.append(msg % a if a else msg))

    real_main_window.connection = SimpleNamespace(
        board=object(), snapshot=[], long_op_active=False)
    real_main_window._dock_hub.set_board_selection([], [])
    assert infos == []

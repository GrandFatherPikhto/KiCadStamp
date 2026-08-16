#!/usr/bin/env python3
"""Тесты на template_extraction.py и adapter.get_selected_items()."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import FootprintInstance, Via, Track, Group, BoardLayer

from kicadstamp.template_extraction import extract_template_from_selection, render_uncertain_comments
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.exceptions import ValidationError

MM = 1_000_000


def _make_fp(ref, x_mm, y_mm, angle_deg, role, pad_nets=None):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp.orientation = Angle.from_degrees(angle_deg)
    fp._role = role
    fp._pad_nets = pad_nets or []  # см. _make_adapter_with_pads
    return fp


def _make_adapter(footprints, vias=()):
    """Общий adapter-мок: get_field_value читает fp._role, get_footprint_pads
    строит по fp._pad_nets (список имён цепей -> список фейковых Pad)."""
    adapter = MagicMock()
    adapter.get_selected_items.return_value = list(footprints) + list(vias)
    adapter.get_field_value.side_effect = lambda fp, name: fp._role

    def _pads(fp):
        pads = []
        for i, net_name in enumerate(fp._pad_nets):
            pad = MagicMock()
            pad.net.name = net_name
            pad.number = str(i + 1)
            pads.append(pad)
        return pads

    adapter.get_footprint_pads.side_effect = _pads
    return adapter


def _make_via(x_mm, y_mm, net_name, drill_mm=0.3, diameter_mm=0.6):
    v = MagicMock(spec=Via)
    v.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    v.net.name = net_name
    v.drill_diameter = int(drill_mm * MM)
    v.diameter = int(diameter_mm * MM)
    return v


class TestExtractTemplateFromSelection:
    def test_crystal_with_three_roles_and_via(self):
        xtal = _make_fp("Y2", 10.0, 10.0, 0.0, "XTAL")
        cap1 = _make_fp("C15", 8.0, 12.0, 90.0, "LOAD_CAP_1")
        cap2 = _make_fp("C16", 12.0, 12.0, 270.0, "LOAD_CAP_2")
        via1 = _make_via(10.0, 8.0, "GND")

        adapter = MagicMock()
        adapter.get_selected_items.return_value = [xtal, cap1, cap2, via1]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role

        result = extract_template_from_selection(adapter, "crystal_8mhz")
        tpl = result["crystal_8mhz"]
        assert len(tpl["components"]) == 3
        assert len(tpl["vias"]) == 1

        xtal_c = next(c for c in tpl["components"] if c["role"] == "XTAL")
        assert xtal_c["offset_along_mm"] == 2.0
        assert xtal_c["offset_across_mm"] == -2.0
        assert xtal_c["angle_deg"] == 0.0

        assert tpl["vias"][0]["offset_along_mm"] == 2.0
        assert tpl["vias"][0]["offset_across_mm"] == -4.0
        assert tpl["vias"][0]["net"] == "GND"

    def test_empty_selection_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = []
        with pytest.raises(ValidationError):
            extract_template_from_selection(adapter, "t")

    def test_missing_role_raises_with_ref(self):
        fp = _make_fp("C5", 0, 0, 0, None)
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [fp]
        adapter.get_field_value.return_value = None
        with pytest.raises(ValidationError, match="C5"):
            extract_template_from_selection(adapter, "t")

    def test_duplicate_role_raises_with_both_refs(self):
        fp1 = _make_fp("C5", 0, 0, 0, "HEAVY")
        fp2 = _make_fp("C6", 1, 1, 0, "HEAVY")
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [fp1, fp2]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        with pytest.raises(ValidationError, match="HEAVY"):
            extract_template_from_selection(adapter, "t")

    def test_non_footprint_non_via_items_ignored_not_fatal(self):
        """Что-то ещё в выделении (например, зона) -- игнорируется, не фатально,
        если рядом есть хотя бы один валидный футпринт/via."""
        fp = _make_fp("C5", 0, 0, 0, "SOLO")
        stray_item = MagicMock()  # не Footprint и не Via
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [fp, stray_item]
        adapter.get_field_value.side_effect = lambda f, name: getattr(f, "_role", None)

        result = extract_template_from_selection(adapter, "t")
        assert len(result["t"]["components"]) == 1


class TestExplicitItemsParameter:
    """items= (added for scripted extract, see kicadstamp.explore.Board.select_items)
    — None (default) keeps using live GUI selection unchanged; an explicit
    list bypasses adapter.get_selected_items() entirely, same 'explicit flag,
    not implicit inference' principle as ClonePlacement.by_selection."""

    def test_explicit_items_bypasses_live_selection(self):
        cap_wrong = _make_fp("WRONG", 0, 0, 0, "C_IN_BULK")
        cap_right = _make_fp("RIGHT", 1, 1, 0, "C_IN_BULK")
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [cap_wrong]
        adapter.get_field_value.side_effect = lambda f, name: getattr(f, "_role", None)

        result = extract_template_from_selection(adapter, "t", items=[cap_right])

        adapter.get_selected_items.assert_not_called()
        assert len(result["t"]["components"]) == 1

    def test_items_none_falls_back_to_live_selection(self):
        cap = _make_fp("C1", 0, 0, 0, "C_IN_BULK")
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [cap]
        adapter.get_field_value.side_effect = lambda f, name: getattr(f, "_role", None)

        result = extract_template_from_selection(adapter, "t")

        adapter.get_selected_items.assert_called_once()
        assert len(result["t"]["components"]) == 1

    def test_explicit_empty_items_list_is_not_treated_as_none(self):
        """An explicit [] must raise the same 'nothing selected' fatal as an
        empty live selection would — NOT silently fall back to
        get_selected_items() (that would defeat the point of items= being
        explicit)."""
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [_make_fp("SHOULD_NOT_BE_USED", 0, 0, 0, "X")]

        with pytest.raises(ValidationError):
            extract_template_from_selection(adapter, "t", items=[])

        adapter.get_selected_items.assert_not_called()


class TestGetSelectedItems:
    def test_group_expanded_via_proto_items(self):
        adapter = KiCadBoardAdapter.__new__(KiCadBoardAdapter)

        member_uuid = MagicMock()
        member_uuid.value = "fp-uuid-1"
        group = MagicMock(spec=Group)
        group.proto.items = [member_uuid]
        group.items = []  # с сервера всегда пусто -- не должно использоваться

        fp_in_group = MagicMock(spec=FootprintInstance)
        fp_in_group.id.value = "fp-uuid-1"
        fp_direct = MagicMock(spec=FootprintInstance)
        fp_direct.id.value = "fp-uuid-2"
        via_direct = MagicMock(spec=Via)
        via_direct.id.value = "via-uuid-1"

        board = MagicMock()
        board.get_selection.return_value = [group, fp_direct, via_direct]
        board.get_footprints.return_value = [fp_in_group, fp_direct]
        board.get_vias.return_value = [via_direct]
        adapter._board = board
        adapter._footprints_cache = None
        adapter.ignore_selection = False

        items = adapter.get_selected_items()
        assert len(items) == 3
        assert fp_in_group in items
        assert fp_direct in items
        assert via_direct in items


class TestNetTemplateAutoDetect:
    """net_template по одной совпавшей цепи из net_template_map — без
    net_template_role, старое поведение (регрессия)."""

    def test_single_matching_net_sets_net_template(self):
        cap = _make_fp("C1", 0, 0, 0, "C_IN_BULK", pad_nets=["+5V_DIRTY", "GND"])
        adapter = _make_adapter([cap])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}"},
        )
        comp = result["t"]["components"][0]
        assert comp["net_template"] == "{PWR_IN}"
        # 2026-08-16 (net_template_same_as_role): a lemma-2-safe role (exactly
        # one non-rule net) needs NO disambiguation — it must write NEITHER
        # net_template_pad NOR net_template_same_as_role. This is the
        # C_ADJ_BULK/R_FB_BOT bug fix (an unneeded pad number used to be
        # recorded here, which then silently broke on another instance).
        assert "net_template_pad" not in comp
        assert "net_template_same_as_role" not in comp

    def test_two_matching_nets_leaves_net_template_unset_with_warning(self, caplog):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY", "PWR_OUT": "+5V"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}", "+5V": "{PWR_OUT}"},
        )
        comp = result["t"]["components"][0]
        assert "net_template" not in comp
        # A genuinely ambiguous net_template (2+ matching nets) is NOT resolved
        # by a pad number — no net_template_pad may appear there (2026-08-16).
        assert "net_template_pad" not in comp
        # Message text is translated (see kicadstamp/i18n.py) — match either
        # locale the project ships (en/ru), not just the raw English msgid.
        assert "nets from --net-template" in caplog.text or "цепей из --net-template" in caplog.text

    def test_two_matching_nets_appends_annotation_when_requested(self):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])
        annotations = []

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY", "PWR_OUT": "+5V"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}", "+5V": "{PWR_OUT}"},
            annotations=annotations,
        )
        comp = result["t"]["components"][0]
        assert "net_template" not in comp
        assert len(annotations) == 1
        role, field, hint = annotations[0]
        assert role == "PI_FILTER_FB"
        assert field == "net_template"
        assert "+5V" in hint and "+5V_DIRTY" in hint

    def test_single_matching_net_leaves_annotations_empty(self):
        cap = _make_fp("C1", 0, 0, 0, "C_IN_BULK", pad_nets=["+5V_DIRTY", "GND"])
        adapter = _make_adapter([cap])
        annotations = []

        extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}"},
            annotations=annotations,
        )
        assert annotations == []


class TestNetTemplateRole:
    """net_template_role — явное указание, какую из нескольких цепей на
    падах компонента считать net_template этой роли (см. диалог про
    PI_FILTER_FB/дроссели на стыке двух рельсов)."""

    def test_resolves_ambiguous_component_explicitly(self):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY", "PWR_OUT": "+5V"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}", "+5V": "{PWR_OUT}"},
            net_template_role={"PI_FILTER_FB": "+5V_DIRTY"},
        )
        comp = result["t"]["components"][0]
        assert comp["net_template"] == "{PWR_IN}"
        # 2026-08-16 (net_template_pad): the explicit --net-template-role path
        # also records which pad carried the literal (+5V_DIRTY is pad 1 here).
        assert comp["net_template_pad"] == "1"

    def test_fatal_if_requested_net_not_on_pads(self):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        with pytest.raises(ValidationError):
            extract_template_from_selection(
                adapter, "t", params={"PWR_IN": "+5V_DIRTY"},
                net_template_map={"+5V_DIRTY": "{PWR_IN}"},
                net_template_role={"PI_FILTER_FB": "+3V3"},  # такой цепи на падах нет
            )

    def test_fatal_if_literal_missing_from_net_template_map(self):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        with pytest.raises(ValidationError):
            extract_template_from_selection(
                adapter, "t", params={},
                net_template_map={},  # +5V_DIRTY нигде не зарегистрирован
                net_template_role={"PI_FILTER_FB": "+5V_DIRTY"},
            )

    def test_role_not_in_net_template_role_uses_auto_detect(self):
        """Компонент, для которого net_template_role не задан, продолжает
        резолвиться старым (авто) путём — новая опция не ломает соседей."""
        cap = _make_fp("C1", 0, 0, 0, "C_IN_BULK", pad_nets=["+5V_DIRTY", "GND"])
        fb = _make_fp("FB1", 5, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([cap, fb])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY", "PWR_OUT": "+5V"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}", "+5V": "{PWR_OUT}"},
            net_template_role={"PI_FILTER_FB": "+5V_DIRTY"},
        )
        by_role = {c["role"]: c for c in result["t"]["components"]}
        # C_IN_BULK: lemma-2-safe (auto-detect) -> records only net_template,
        # NEITHER pad NOR same-as-role (2026-08-16 fix).
        assert by_role["C_IN_BULK"]["net_template"] == "{PWR_IN}"
        assert "net_template_pad" not in by_role["C_IN_BULK"]
        assert "net_template_same_as_role" not in by_role["C_IN_BULK"]
        # PI_FILTER_FB: explicit --net-template-role, and C_IN_BULK was already
        # classified as lemma-2-safe on the SAME net THIS pass -> reference it
        # (net_template_same_as_role) instead of a pad number (2026-08-16).
        assert by_role["PI_FILTER_FB"]["net_template"] == "{PWR_IN}"
        assert by_role["PI_FILTER_FB"]["net_template_same_as_role"] == "C_IN_BULK"
        assert "net_template_pad" not in by_role["PI_FILTER_FB"]


class TestRuleNets:
    """rule_nets (2026-08-05, Denis: "давай сделаем явный чекбокс [для null].
    Это правильная фича. Она замыкает использование rules") — a via/track
    net in rule_nets is written as null instead of its literal, so a
    ManualSpoke-placed cell using it inherits the enclosing Rule's own net
    at apply time (spoke_layout.py's `via.net or rule_net`)."""

    def test_via_net_in_rule_nets_is_written_as_null(self):
        via_pwr = _make_via(0, 0, "+3V3_VCCIO")
        via_gnd = _make_via(1, 0, "GND")
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [via_pwr, via_gnd]

        result = extract_template_from_selection(
            adapter, "t", rule_nets={"+3V3_VCCIO"})

        vias_by_net = {v["net"] for v in result["t"]["vias"]}
        assert None in vias_by_net  # the PWR via
        assert "GND" in vias_by_net  # GND untouched — not in rule_nets

    def test_net_in_both_rule_nets_and_net_template_map_is_fatal(self):
        via = _make_via(0, 0, "+5V_DIRTY")
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [via]

        with pytest.raises(ValidationError):
            extract_template_from_selection(
                adapter, "t", params={"PWR_IN": "+5V_DIRTY"},
                net_template_map={"+5V_DIRTY": "{PWR_IN}"},
                rule_nets={"+5V_DIRTY"})

    def test_rule_nets_does_not_affect_unrelated_nets(self):
        via_pwr = _make_via(0, 0, "+3V3_VCCIO")
        via_other = _make_via(1, 0, "+1V2_VCCINT")
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [via_pwr, via_other]

        result = extract_template_from_selection(
            adapter, "t", rule_nets={"+3V3_VCCIO"})

        nets = {v["net"] for v in result["t"]["vias"]}
        assert nets == {None, "+1V2_VCCINT"}


class TestRenderUncertainComments:
    """render_uncertain_comments — текстовая пост-обработка yaml.dump()
    вывода cmd_extract: закомментированная строка-подсказка после блока
    компонента с нужной ролью, см. handoff про auto-guess extract."""

    @staticmethod
    def _dump(data):
        import yaml
        return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)

    def test_inserts_comment_after_matching_role_block(self):
        data = {"my_tpl": {"components": [
            {"role": "A", "offset_along_mm": 1.0, "angle_deg": 0.0},
            {"role": "B", "offset_along_mm": 2.0, "angle_deg": 90.0},
        ], "vias": [], "tracks": [], "layer": "F.Cu"}}
        text = self._dump(data)
        annotations = [("B", "net_template", "could not determine automatically")]

        out = render_uncertain_comments(text, "my_tpl", annotations)

        lines = out.splitlines()
        b_idx = next(i for i, l in enumerate(lines) if l.strip() == "- role: B")
        # comment must be inside B's block (before the next top-level dedent)
        # and must not appear anywhere before B's block (i.e. not attached to A).
        a_idx = next(i for i, l in enumerate(lines) if l.strip() == "- role: A")
        assert not any("# net_template" in l for l in lines[a_idx:b_idx])
        comment_idx = next(i for i, l in enumerate(lines) if "# net_template" in l)
        assert comment_idx > b_idx
        assert "could not determine automatically" in lines[comment_idx]
        # must land before the next sibling key (vias:), i.e. still part of B's block
        vias_idx = next(i for i, l in enumerate(lines) if l.strip() == "vias: []")
        assert comment_idx < vias_idx

    def test_same_role_in_different_template_is_not_touched(self):
        data = {
            "tpl_one": {"components": [{"role": "X", "offset_along_mm": 0.0, "angle_deg": 0.0}],
                        "vias": [], "tracks": [], "layer": "F.Cu"},
            "tpl_two": {"components": [{"role": "X", "offset_along_mm": 0.0, "angle_deg": 0.0}],
                        "vias": [], "tracks": [], "layer": "F.Cu"},
        }
        text = self._dump(data)
        annotations = [("X", "net_template", "hint for tpl_two only")]

        out = render_uncertain_comments(text, "tpl_two", annotations)

        lines = out.splitlines()
        one_start = next(i for i, l in enumerate(lines) if l.strip() == "tpl_one:")
        two_start = next(i for i, l in enumerate(lines) if l.strip() == "tpl_two:")
        comment_idx = next(i for i, l in enumerate(lines) if "# net_template" in l)
        assert two_start < comment_idx
        assert not (one_start < comment_idx < two_start)

    def test_no_annotations_returns_text_unchanged(self):
        text = self._dump({"t": {"components": [], "vias": [], "tracks": [], "layer": "F.Cu"}})
        assert render_uncertain_comments(text, "t", []) == text

    def test_unknown_role_is_silently_skipped(self):
        data = {"t": {"components": [{"role": "A", "offset_along_mm": 0.0, "angle_deg": 0.0}],
                       "vias": [], "tracks": [], "layer": "F.Cu"}}
        text = self._dump(data)
        out = render_uncertain_comments(text, "t", [("NOT_PRESENT", "net_template", "hint")])
        assert out == text


class TestNetFromRoleAutoSuggest:
    """net_from_role / net_from_role_pad auto-suggestion on extract (plan step 4):
    a via/track whose net maps unambiguously to one selected role is written as
    net_from_role (optionally with net_from_role_pad) instead of a literal or a
    parametrised net — the cell then resolves that net live on ANY cluster it is
    applied to. Fallback / ambiguity keeps the existing literal/parametrize path."""

    def test_via_unambiguous_role_writes_net_from_role(self):
        cap = _make_fp("C1", 0.0, 0.0, 0.0, "C_OUT_BULK", pad_nets=["+3V3", "GND"])
        via = _make_via(0.0, -2.0, "+3V3")
        adapter = _make_adapter([cap], [via])

        result = extract_template_from_selection(adapter, "t", rule_nets={"GND"})
        v = result["t"]["vias"][0]
        assert v["net"] is None          # no literal — resolved from the role
        assert v["net_from_role"] == "C_OUT_BULK"
        assert "net_from_role_pad" not in v  # lemma 2, no pad needed

    def test_via_on_rule_net_keeps_net_null(self):
        cap = _make_fp("C1", 0.0, 0.0, 0.0, "C_OUT_BULK", pad_nets=["+3V3", "GND"])
        via = _make_via(0.0, -2.0, "GND")
        adapter = _make_adapter([cap], [via])

        result = extract_template_from_selection(adapter, "t", rule_nets={"GND"})
        v = result["t"]["vias"][0]
        assert v["net"] is None
        assert "net_from_role" not in v

    def test_via_no_matching_role_keeps_literal(self):
        cap = _make_fp("C1", 0.0, 0.0, 0.0, "C_OUT_BULK", pad_nets=["+3V3", "GND"])
        via = _make_via(0.0, -2.0, "+1V8")  # no selected role carries +1V8
        adapter = _make_adapter([cap], [via])

        result = extract_template_from_selection(adapter, "t", rule_nets={"GND"})
        v = result["t"]["vias"][0]
        assert v["net"] == "+1V8"
        assert "net_from_role" not in v

    def test_via_multi_net_role_writes_net_from_role_with_pad(self):
        # LDO: VIN/VOUT/GND — a multi-net role; +3V3 alone cannot identify a
        # single pad-less role, so net_from_role_pad is written explicitly.
        ldo = _make_fp("U1", 0.0, 0.0, 0.0, "LDO", pad_nets=["+5V", "+3V3", "GND"])
        via = _make_via(0.0, -2.0, "+3V3")
        adapter = _make_adapter([ldo], [via])

        result = extract_template_from_selection(adapter, "t", rule_nets={"GND"})
        v = result["t"]["vias"][0]
        assert v["net"] is None
        assert v["net_from_role"] == "LDO"
        assert v["net_from_role_pad"] == "2"  # the pad carrying +3V3

    def test_geometry_tiebreak_chooses_nearest_role(self):
        # Two caps share +5V (common bus); the via sits next to C1 — the
        # geometric tiebreak must attribute it to C1, deterministically.
        c1 = _make_fp("C1", 0.0, 0.0, 0.0, "C1", pad_nets=["+5V", "GND"])
        c2 = _make_fp("C2", 10.0, 0.0, 0.0, "C2", pad_nets=["+5V", "GND"])
        via = _make_via(1.0, 0.0, "+5V")
        adapter = _make_adapter([c1, c2], [via])

        result = extract_template_from_selection(adapter, "t", rule_nets={"GND"})
        v = result["t"]["vias"][0]
        assert v["net"] is None
        assert v["net_from_role"] == "C1"

    def test_net_from_role_takes_priority_over_net_template_map(self):
        cap = _make_fp("C1", 0.0, 0.0, 0.0, "C_OUT_BULK", pad_nets=["+3V3", "GND"])
        via = _make_via(0.0, -2.0, "+3V3")
        adapter = _make_adapter([cap], [via])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_OUT": "+3V3"},
            net_template_map={"+3V3": "{PWR_OUT}"}, rule_nets={"GND"})
        v = result["t"]["vias"][0]
        assert v["net"] is None
        assert v["net_from_role"] == "C_OUT_BULK"  # not "{PWR_OUT}"

    def test_track_unambiguous_role_writes_net_from_role(self):
        # Track kept because both ends land inside a selected via's box; its
        # net maps to the single selected role.
        cap = _make_fp("C1", 0.0, 0.0, 0.0, "C_OUT_BULK", pad_nets=["+3V3", "GND"])
        via_start = _make_via(0.0, 0.0, "+3V3")
        via_end = _make_via(0.0, -2.0, "+3V3")
        t = MagicMock(spec=Track)
        t.start = Vector2.from_xy(0, 0)
        t.end = Vector2.from_xy(0, int(-2.0 * MM))
        t.net = MagicMock()
        t.net.name = "+3V3"
        t.width = int(0.65 * MM)
        t.layer = BoardLayer.BL_F_Cu
        adapter = _make_adapter([cap], [via_start, via_end])
        adapter.get_selected_items.return_value = [cap, via_start, via_end, t]

        def _bboxes(items):
            out = []
            for it in items:
                pos = getattr(it, "position", None)
                if isinstance(pos, Vector2):
                    box = MagicMock()
                    box.pos = Vector2.from_xy(pos.x - int(0.2 * MM), pos.y - int(0.2 * MM))
                    box.size = Vector2.from_xy(int(0.4 * MM), int(0.4 * MM))
                    out.append(box)
                else:
                    out.append(None)
            return out

        adapter.get_bounding_boxes.side_effect = _bboxes

        result = extract_template_from_selection(adapter, "t", rule_nets={"GND"})
        tr = result["t"]["tracks"][0]
        assert tr["net"] is None
        assert tr["net_from_role"] == "C_OUT_BULK"


def _make_fp_with_pads(ref, x_mm, y_mm, angle_deg, role, pads):
    """Like _make_fp, but with REAL pad geometry (position + size) so the
    connected-components closure sees actual pad boxes to anchor at.
    pads: list of (net_name, px_mm, py_mm, size_mm)."""
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp.orientation = Angle.from_degrees(angle_deg)
    fp._role = role
    fp._pads = []
    for i, (net, px, py, size) in enumerate(pads):
        pad = MagicMock()
        pad.net.name = net
        pad.number = str(i + 1)
        pad.position = Vector2.from_xy(int(px * MM), int(py * MM))
        pad._box_size = int(size * MM)
        fp._pads.append(pad)
    return fp


def _make_track(x1_mm, y1_mm, x2_mm, y2_mm, net_name):
    t = MagicMock(spec=Track)
    t.start = Vector2.from_xy(int(x1_mm * MM), int(y1_mm * MM))
    t.end = Vector2.from_xy(int(x2_mm * MM), int(y2_mm * MM))
    t.net = MagicMock()
    t.net.name = net_name
    t.width = int(0.65 * MM)
    t.layer = BoardLayer.BL_F_Cu
    return t


def _make_closure_adapter(footprints, vias=(), tracks=()):
    """Adapter whose get_bounding_boxes returns REAL boxes for pads/vias —
    this is what makes the closure path (not the fallback) run: footprints
    are routed through _make_fp_with_pads (real pad positions/sizes)."""
    adapter = MagicMock()
    adapter.get_selected_items.return_value = list(footprints) + list(vias) + list(tracks)
    adapter.get_field_value.side_effect = lambda fp, name: fp._role

    def _pads(fp):
        return list(getattr(fp, "_pads", []))

    adapter.get_footprint_pads.side_effect = _pads

    def _bboxes(items):
        out = []
        for it in items:
            pos = getattr(it, "position", None)
            if isinstance(pos, Vector2):
                size = getattr(it, "_box_size", int(0.4 * MM))
                half = size // 2
                box = MagicMock()
                box.pos = Vector2.from_xy(pos.x - half, pos.y - half)
                box.size = Vector2.from_xy(size, size)
                out.append(box)
            else:
                out.append(None)
        return out

    adapter.get_bounding_boxes.side_effect = _bboxes
    return adapter


class TestTrackViaClusterClosure:
    """The Cluster filter must propagate to via/track connectivity, not just
    footprints (plan_2026_08_16_extract_cluster_closure.md): a track/via is
    kept ONLY if its connected component reaches a REAL anchor — a pad of a
    KEPT footprint. A track-to-track island that only ever touches excluded
    material is dropped as a WHOLE component (closes the old "two tracks
    mutually validate each other at a shared endpoint on an EXCLUDED pad"
    loophole), and vias — previously passed through completely unfiltered —
    go through the same closure.

    These tests exercise the closure path: footprints are built with REAL
    pad boxes (_make_fp_with_pads / _make_closure_adapter). The both-ends-
    match regression (no real pad boxes) stays covered by the pre-existing
    tests above through the fallback branch.
    """

    def test_track_between_two_kept_pads_kept(self):
        """Regression: a genuine both-ends-matched track between two KEPT
        footprints' pads survives unchanged."""
        fp1 = _make_fp_with_pads("U1", 0, 0, 0, "A", [("GND", 0, 0, 0.6)])
        fp2 = _make_fp_with_pads("U2", 10, 0, 0, "B", [("GND", 10, 0, 0.6)])
        t = _make_track(0, 0, 10, 0, "GND")
        adapter = _make_closure_adapter([fp1, fp2], [], [t])

        result = extract_template_from_selection(adapter, "t")

        # Kept (closure-wise); the net is auto-suggested from role A (GND is
        # A's only pad net) — net_from_role, not the closure, owns the net.
        assert len(result["t"]["tracks"]) == 1

    def test_two_tracks_share_coincident_endpoint_not_anchored_both_dropped(self, caplog):
        """The exact dac_buf repro, minimized: two tracks share a coincident
        endpoint that is NOT a pad of any KEPT footprint and NOT a via. The
        old non-transitive check let each track "rescue" the other at that
        shared point; the closure drops the whole unanchored island."""
        u6 = _make_fp_with_pads("U6", 0, 0, 0, "OP_AMP", [("GND", 0, 0, 0.6)])
        t1 = _make_track(5, 5, 10, 10, "/Channel_0/OpAmp/PROT_OUT_P")
        t2 = _make_track(10, 10, 15, 15, "/Channel_0/OpAmp/PROT_OUT_P")
        adapter = _make_closure_adapter([u6], [], [t1, t2])

        with caplog.at_level("WARNING"):
            result = extract_template_from_selection(adapter, "t")

        assert result["t"]["tracks"] == []
        assert "/Channel_0/OpAmp/PROT_OUT_P" in caplog.text

    def test_legit_multihop_chain_pad_track_via_track_pad_survives(self):
        """A legitimate 3+ hop chain (pad -> track -> via -> track -> pad, all
        real anchors present) survives fully — the closure must not over-prune
        genuine multi-hop routing."""
        fp1 = _make_fp_with_pads("U1", 0, 0, 0, "A", [("GND", 0, 0, 0.6)])
        fp2 = _make_fp_with_pads("U2", 10, 0, 0, "B", [("GND", 10, 0, 0.6)])
        via = _make_via(5, 0, "GND")
        t1 = _make_track(0, 0, 5, 0, "GND")
        t2 = _make_track(5, 0, 10, 0, "GND")
        adapter = _make_closure_adapter([fp1, fp2], [via], [t1, t2])

        result = extract_template_from_selection(adapter, "t")

        assert len(result["t"]["tracks"]) == 2
        assert len(result["t"]["vias"]) == 1

    def test_via_directly_on_kept_pad_kept(self):
        """A via with no connecting track, sitting directly on a KEPT pad —
        kept (single-hop anchor case, no regression for the common case)."""
        fp = _make_fp_with_pads("U1", 0, 0, 0, "A", [("GND", 0, 0, 0.6)])
        via = _make_via(0, 0, "GND")
        adapter = _make_closure_adapter([fp], [via], [])

        result = extract_template_from_selection(adapter, "t")

        assert len(result["t"]["vias"]) == 1

    def test_isolated_via_not_on_any_pad_dropped(self, caplog):
        """A via with no connecting track, NOT on any kept pad, isolated —
        dropped (the vias-were-completely-unfiltered-before gap, closed)."""
        fp = _make_fp_with_pads("U1", 0, 0, 0, "A", [("GND", 0, 0, 0.6)])
        via = _make_via(50, 50, "GND")
        adapter = _make_closure_adapter([fp], [via], [])

        with caplog.at_level("WARNING"):
            result = extract_template_from_selection(adapter, "t")

        assert result["t"]["vias"] == []
        assert "not connected to any kept footprint's pad" in caplog.text

    def test_end_to_end_live_repro_tracks_vias_of_excluded_footprints_absent(self, caplog):
        """End-to-end mirror of the live dac_buf repro: footprints limited to
        the KEPT cluster (as if "Keep only one Cluster" already applied), but
        items still contain tracks/vias whose true endpoints belong to
        EXCLUDED footprints not in footprints at all — they must be absent
        from tracks:/vias: with a warning logged, not a literal net silently
        written."""
        u6 = _make_fp_with_pads("U6", 0, 0, 0, "OP_AMP", [("GND", 0, 0, 0.6)])
        # D13's pad position — an EXCLUDED footprint (PES_D cluster) that is
        # NOT in footprints at all; the protection track's true endpoint sits
        # here.
        t_prot = _make_track(5, 5, 10, 10, "/Channel_0/OpAmp/PROT_OUT_P")
        v_prot = _make_via(7, 7, "/Channel_0/OpAmp/PA_EN_PROT")
        # Legitimate power-rail track genuinely connecting to U6's kept pad.
        t_legit = _make_track(0, 0, 10, 0, "+3V3")
        adapter = _make_closure_adapter([u6], [v_prot], [t_prot, t_legit])

        with caplog.at_level("WARNING"):
            result = extract_template_from_selection(adapter, "t")

        nets = [tr["net"] for tr in result["t"]["tracks"]]
        assert nets == ["+3V3"]
        assert result["t"]["vias"] == []
        assert "/Channel_0/OpAmp/PROT_OUT_P" in caplog.text
        assert "/Channel_0/OpAmp/PA_EN_PROT" in caplog.text

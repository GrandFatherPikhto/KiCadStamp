# tests/geometry/test_thermal_grid_duplicate_pad.py
"""A thermal via array lands on the LARGEST copper pad when the number repeats.

plan_2026_10_27_thermal_via_duplicate_pad_number (Denis, 27.10). Live case on v103:
`p3v3_thermal_via_pad` (`anchor_ref "U2"`, `pad "2"`), and U2 is
`Package_TO_SMT_SMD:TO-252-3_TabPin2` — «2» names BOTH the little pin (2.2×1.2 mm,
2.64 mm²) and the tab (6.4×5.8 mm, 37.12 mm²), the pin FIRST in the file. The
planner asked `adapter.get_pad_by_number`, which answers the FIRST pad, so all 16
points of the 4×4 grid sat inside the pin and none inside the tab (measured, plan
§7.3).

The rule: `geometry/thermal_grid.pick_thermal_pad(pads, number)` answers
``(pad, count of pads with that number)`` — the LARGEST pad by copper area (width ×
height), the FIRST on a tie. A pad with no copper at all (`pad_has_copper`: a
paste-only opening, `copper_layers == ()`) and a pad without a size are NOT
candidates; if that leaves nothing but pads with the number DO exist, the FIRST pad
is returned — the historical answer, so a lone pad without a size fails exactly
where it always did (`get_pad_size` → GeometryError) instead of «has no pad».

Cells 1–4 and 6 drive the PLANNER, not only the pure function: the mutation «the
planner calls `get_pad_by_number` again» must redden them, and the adapter double
answers `get_footprint_pads` only.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kicadstamp.config import Config, ThermalViaArrayConfig
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ComponentNotFoundError, GeometryError
from kicadstamp.geometry.thermal_grid import (compute_thermal_via_grid,
                                               pick_thermal_pad)
from kicadstamp.placement.commands import PlacedComponentInfo
from kicadstamp.placement.services.via_planner import ViaPlanner
from tests.fakes.format3 import det_uuid

MM = 1_000_000


def _pad(number="2", w_mm=2.2, h_mm=1.2, x_mm=0.0, y_mm=0.0, net="GND",
         copper=True, size=True):
    """A pad double: only what the planner and the grid read.

    ``copper=False`` builds a PASTE-only pad (`copper_layers == ()`); ``size=False``
    a pad whose padstack reported no copper layer, i.e. no size at all."""
    return SimpleNamespace(
        number=number, net_name=net,
        position=Vector2.from_xy(int(x_mm * MM), int(y_mm * MM)),
        size=(Vector2.from_xy(int(w_mm * MM), int(h_mm * MM)) if size else None),
        angle_rad=0.0, shape="rect", offset=None, trapezoid_delta=None,
        copper_layers=((BoardLayer.BL_F_Cu,) if copper else ()))


def _fp(pads, ref="U2"):
    return SimpleNamespace(ref=ref, uuid=ref.lower(), _pads=list(pads))


def _adapter(*footprints):
    """The planner's adapter: pads and footprints answer for the given doubles,
    `get_pad_by_number` does NOT (that is what mutation M1 puts back), and every
    pad handed to `get_bounding_boxes` is recorded — cell 10 reads that record."""
    from unittest.mock import MagicMock

    by_ref = {footprint.ref: footprint for footprint in footprints}
    adapter = MagicMock()
    adapter.get_footprint_pads.side_effect = lambda footprint: list(footprint._pads)
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_bounding_boxes.return_value = []
    return adapter


def _cfg(*, pad="2", rows=1, cols=1, margin_mm=0.0, anchor_ref="U2"):
    tva = ThermalViaArrayConfig(
        name="u2_thermal", uuid=det_uuid("thermal_via_arrays:u2_thermal"),
        pad=pad, anchor_ref=anchor_ref, net="GND", rows=rows, cols=cols,
        margin_mm=margin_mm, pattern="grid", drill_mm=0.3, diameter_mm=0.6)
    return Config(layer="B.Cu", cells={}, thermal_via_arrays=[tva],
                  clone_placements=[], skip_existing_components=False,
                  via_keepout_clearance_mm=0.2, via_search_step_mm=0.1,
                  via_search_max_radius_mm=5.0, via_search_n_directions=8)


def _thermal(vias):
    return [v for v in vias if v.registry_key is not None]


def _plan_and_adapter(pads, *, pad="2", rows=1, cols=1, margin_mm=0.0,
                      ref="U2", neighbours=(), planned=()):
    """(planned thermal vias, the adapter double) — the adapter comes back so a
    cell can ask WHAT was measured by KiCad's boxes, not only where vias landed."""
    adapter = _adapter(_fp(pads, ref=ref), *neighbours)
    planner = ViaPlanner(adapter, _cfg(pad=pad, rows=rows, cols=cols,
                                       margin_mm=margin_mm, anchor_ref=ref))
    return (_thermal(planner.plan_vias(planned_components=list(planned),
                                       planned_vias=[])),
            adapter)


def _plan(pads, **kwargs):
    return _plan_and_adapter(pads, **kwargs)[0]


def _positions(vias):
    return [(via.position.x, via.position.y) for via in vias]


def _inside(pad, point_mm) -> bool:
    """Is `point_mm` inside the pad's own rectangle (its centre ± size/2)?"""
    size = pad.size
    cx, cy = pad.position.x / MM, pad.position.y / MM
    half_w, half_h = size.x / MM / 2, size.y / MM / 2
    return (abs(point_mm[0] - cx) <= half_w + 1e-9
            and abs(point_mm[1] - cy) <= half_h + 1e-9)


# ── cell 1: the live shape — the little pin FIRST, the tab second ───────────

def test_the_grid_lands_on_the_tab_when_the_pin_is_first():
    """Cell 1 (v103 U2): pin 2.2×1.2 first, tab 6.4×5.8 second. The grid must sit
    in the TAB — inside the tab, outside the pin. Mutation M2 («the smallest») and
    M1 («the planner calls get_pad_by_number again») die here."""
    pin = _pad("2", 2.2, 1.2, x_mm=-5.0)
    tab = _pad("2", 6.4, 5.8, x_mm=1.0)

    vias = _plan([pin, tab])

    assert len(vias) == 1
    point = (vias[0].position.x / MM, vias[0].position.y / MM)
    assert _inside(tab, point), f"the grid must sit in the tab, got {point}"
    assert not _inside(pin, point), f"the grid must not sit in the pin, got {point}"


# ── cell 2: the order of the file decides nothing ──────────────────────────

def test_the_tab_wins_even_when_it_is_listed_first():
    """Cell 2: the same two pads in the OPPOSITE order — the largest is still the
    answer, so the order of the file never decides. Mutation M2 dies here too."""
    tab = _pad("2", 6.4, 5.8, x_mm=1.0)
    pin = _pad("2", 2.2, 1.2, x_mm=-5.0)

    vias = _plan([tab, pin])

    assert len(vias) == 1
    point = (vias[0].position.x / MM, vias[0].position.y / MM)
    assert _inside(tab, point)


# ── cell 3: a unique number behaves byte for byte as before ────────────────

def test_a_unique_number_is_untouched():
    """Cell 3: one pad with the number — the same pad `get_pad_by_number` answers,
    the same single grid point, no Log line. Guards «the rule only changes the
    repeated case»."""
    only = _pad("33", 3.25, 3.25, x_mm=2.0, y_mm=-1.0)

    picked, count = pick_thermal_pad([only], "33")
    vias = _plan([only], pad="33")

    assert picked is only and count == 1
    assert len(vias) == 1
    point = (vias[0].position.x / MM, vias[0].position.y / MM)
    assert _inside(only, point)


# ── cell 4: equal areas — the FIRST, stably ────────────────────────────────

def test_equal_pads_keep_the_first():
    """Cell 4: two pads of the same size — the FIRST one (stable, never «the last
    one wins» by accident). Mutation M3 (`>=` instead of `>`) dies here."""
    first = _pad("1", 2.0, 2.0, x_mm=-4.0)
    second = _pad("1", 2.0, 2.0, x_mm=4.0)

    picked, count = pick_thermal_pad([first, second], "1")
    vias = _plan([first, second], pad="1")

    assert picked is first and count == 2
    assert len(vias) == 1
    point = (vias[0].position.x / MM, vias[0].position.y / MM)
    assert _inside(first, point)
    assert not _inside(second, point)


# ── cell 5: no such number — the OLD error and text ────────────────────────

def test_a_missing_number_keeps_the_old_error_and_text():
    """Cell 5: no pad carries the number → the historical `ComponentNotFoundError`
    with the historical text (nothing about «largest» leaks into the message)."""
    with pytest.raises(ComponentNotFoundError) as exc:
        _plan([_pad("1"), _pad("3")], pad="2")

    assert "has no pad 2" in str(exc.value)


# ── cell 6: the Log line ───────────────────────────────────────────────────

def test_a_repeated_number_is_said_once_in_the_log(caplog):
    """Cell 6: with TWO pads there is ONE INFO line naming ref, number and count;
    with one pad there is no line at all. Mutation M4 («the line on any count»)
    dies on the second half."""
    with caplog.at_level("INFO"):
        _plan([_pad("2", 2.2, 1.2, x_mm=-5.0), _pad("2", 6.4, 5.8, x_mm=1.0)])
    lines = [r.getMessage() for r in caplog.records if "pads numbered" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "U2" in lines[0] and "2" in lines[0] and "2 pads" in lines[0]
    assert "6.40" in lines[0] and "5.80" in lines[0]      # the chosen one's size

    caplog.clear()
    with caplog.at_level("INFO"):
        _plan([_pad("33", 3.25, 3.25)], pad="33")
    assert not [r for r in caplog.records if "pads numbered" in r.getMessage()]


# ── cell 7: a lone pad without a size keeps ITS OWN error ──────────────────

def test_a_single_pad_without_a_size_keeps_the_geometry_error():
    """Cell 7 (§7.7 of the plan): the only pad «2» has no size — the answer is the
    historical `GeometryError` from `get_pad_size`, NOT «has no pad 2». A rule that
    dropped sizeless pads from the candidates would report a missing pad."""
    with pytest.raises(GeometryError) as exc:
        _plan([_pad("2", size=False)])

    assert "pad has no copper layers in its padstack" in str(exc.value)
    assert "has no pad" not in str(exc.value)


# ── cell 8: TWO pads, neither with a size — the same wrapped error ─────────

def test_two_pads_without_a_size_keep_the_wrapped_geometry_error():
    """Cell §7.7(б) of the plan, found while writing the code — the plan's own
    numbers 8/8б/9/10 belong to the keepout cells below, so this one is cited by
    its section (rule 37): TWO pads carry the number and NEITHER has a size — the
    pick falls back to the first pad (no candidate), and the Log line is about to
    fire. Its `get_pad_size` must fail INSIDE the same «Thermal pad: …» wrapper
    the grid gets, never as a bare GeometryError: the measurement the Log line
    takes is part of the same answer."""
    with pytest.raises(GeometryError) as exc:
        _plan([_pad("2", size=False, x_mm=-4.0), _pad("2", size=False, x_mm=4.0)])

    assert "Thermal pad:" in str(exc.value)
    assert "pad has no copper layers in its padstack" in str(exc.value)


# ── the predicate that keeps a paste-only pad out of the candidates ────────

def test_a_paste_only_pad_is_never_a_candidate():
    """The tab's paste windows (``copper_layers == ()``, 3.05×2.75 each on v103)
    are BIGGER than the pin but carry no copper at all: the largest COPPER pad
    wins. Without the predicate the paste window would be picked."""
    window = _pad("", 3.05, 2.75, copper=False)
    pin = _pad("", 2.2, 1.2)

    picked, count = pick_thermal_pad([window, pin], "")

    assert picked is pin and count == 2


# ── cell 8: the tab's own paste windows must not push the grid off the tab ─

def test_the_tab_paste_windows_do_not_push_the_grid_off_the_tab():
    """Cell 8 (§7.8): the live U1 shape of HiPiMS-Controller-v099 — pin «2»
    2.2×1.2 first, tab «2» 6.4×5.8 second, and four PASTE-only windows of the tab
    (`""`, 3.05×2.75, `copper_layers == ()`) lying on it. A pad that carries no
    copper is not an obstacle, so the grid stays exactly where the pad laid it:
    the ideal 4×4 points, none moved off the tab. Measured live 29.10 before the
    fix: 0 of 16 on the tab; mutation M5 (keepout without the copper check) dies
    here."""
    pin = _pad("2", 2.2, 1.2, x_mm=-5.0)
    tab = _pad("2", 6.4, 5.8, x_mm=1.0)
    windows = [_pad("", 3.05, 2.75, x_mm=1.0 + dx, y_mm=dy, copper=False)
               for dx, dy in ((-1.0, -0.9), (1.0, -0.9), (-1.0, 0.9), (1.0, 0.9))]

    vias = _plan([pin, tab, *windows], rows=4, cols=4, margin_mm=0.5)

    ideal = compute_thermal_via_grid(tab, rows=4, cols=4, margin_mm=0.5)
    assert _positions(vias) == [(p.x, p.y) for p in ideal], (
        "the grid must stay exactly where the pad laid it")
    assert all(_inside(tab, (x / MM, y / MM)) for x, y in _positions(vias))


# ── cell 8б: the same for a NEIGHBOUR's window (the second keepout loop) ────

def test_a_neighbours_paste_window_is_not_an_obstacle_either():
    """Cell 8б (§7.8): the window belongs to ANOTHER footprint this run plans,
    i.e. the second loop of `_build_keepout`. The same skip must hold there —
    mutation M7 (the check only in the `target_fp` loop) dies here."""
    pin = _pad("2", 2.2, 1.2, x_mm=-5.0)
    tab = _pad("2", 6.4, 5.8, x_mm=1.0)
    window = _pad("", 3.05, 2.75, x_mm=1.0, copper=False)
    neighbour = _fp([window], ref="U9")
    planned = [PlacedComponentInfo(ref="U9", dest=Vector2.from_xy(0, 0),
                                   angle_deg=0.0)]

    vias, _adapter_double = _plan_and_adapter(
        [pin, tab], rows=4, cols=4, margin_mm=0.5,
        neighbours=(neighbour,), planned=planned)

    ideal = compute_thermal_via_grid(tab, rows=4, cols=4, margin_mm=0.5)
    assert _positions(vias) == [(p.x, p.y) for p in ideal]


# ── cell 9: the same shape WITH copper stays an obstacle ───────────────────

def test_a_copper_pad_with_an_empty_number_is_still_an_obstacle():
    """Cell 9 (§7.8): the same window shape, but carrying copper (a mounting pad
    with an empty number) is an obstacle as before — the points under it move.
    This is what tells «by copper» from «by the empty number»: mutation M6 skips
    by `number == ""` and dies here."""
    pin = _pad("2", 2.2, 1.2, x_mm=-5.0)
    tab = _pad("2", 6.4, 5.8, x_mm=1.0)
    mounting = _pad("", 3.05, 2.75, x_mm=1.0)

    vias = _plan([pin, tab, mounting], rows=4, cols=4, margin_mm=0.5)

    ideal = compute_thermal_via_grid(tab, rows=4, cols=4, margin_mm=0.5)
    assert _positions(vias) != [(p.x, p.y) for p in ideal], \
        "a COPPER pad with an empty number must stay an obstacle"
    under = [(x / MM, y / MM) for x, y in _positions(vias)
             if _inside(mounting, (x / MM, y / MM))]
    assert not under, f"no via may sit on the copper pad, got {under}"


# ── cell 10: no copper AND no size — KiCad is never asked for its box ──────

def test_a_pad_without_copper_and_without_a_size_is_not_measured_by_kicad():
    """Cell 10 (§7.8): a pad with no copper AND no size has no own area either, so
    the old code would hand it to `get_bounding_boxes` — and its box would come
    back as an obstacle. The skip runs BEFORE `pad_area_of`, so the adapter never
    sees it. Mutation M8 (the check after `pad_area_of`) dies here."""
    pin = _pad("2", 2.2, 1.2, x_mm=-5.0)
    tab = _pad("2", 6.4, 5.8, x_mm=1.0)
    window = _pad("", copper=False, size=False)

    vias, adapter = _plan_and_adapter([pin, tab, window], rows=4, cols=4,
                                      margin_mm=0.5)

    measured = [pad for call in adapter.get_bounding_boxes.call_args_list
                for pad in (call.args[0] if call.args else [])]
    assert measured == [], (
        f"a pad with no copper must not reach KiCad's box, got {measured}")
    assert len(vias) == 16

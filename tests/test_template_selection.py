# tests/test_template_selection.py
"""_find_origin's role-origin narrowing (2026-08-31, handoff
extract_dock_name_and_origin_cluster, Задание 2): "By component role" origin
previously took the FIRST selected component with the matching role, silently
— wrong when the same role lives in several Clusters/Channels. Now the same
sheet -> Cluster cascade the role-anchor resolver uses narrows the candidates,
and several survivors are FATAL (ambiguous) instead of silently picking one.

Regression: a single same-role candidate (no Cluster/Sheet set) behaves
exactly as before."""
import pytest

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.template_selection import _find_origin


def _fp(ref, role, cluster=None, x_mm=0.0, y_mm=0.0, sheet_uuids=()):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy(int(x_mm * 1_000_000),
                                            int(y_mm * 1_000_000)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    fp.sheet_path_uuids = tuple(sheet_uuids)
    return fp


class _FakePad:
    def __init__(self, number, x_mm, y_mm):
        self.number = number
        self.position = Vector2.from_xy(int(x_mm * 1_000_000),
                                        int(y_mm * 1_000_000))


class _Adapter:
    """Role/Cluster fields come from fp._role/_cluster; get_pad_by_number from
    an explicit per-ref list (so pad-refinement is testable without KiCad)."""

    def __init__(self, pads_by_ref=None):
        self.pads_by_ref = pads_by_ref or {}

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return getattr(fp, "_role", None)
        if name == CLUSTER_FIELD_NAME:
            return getattr(fp, "_cluster", None)
        return None

    def get_pad_by_number(self, fp, number):
        for pad in self.pads_by_ref.get(fp.ref, []):
            if pad.number == number:
                return pad
        return None


def _call(footprints, role, adapter, **kwargs):
    return _find_origin(footprints, [],
                        origin_via_net=None,
                        origin_component_role=role,
                        origin_component_pad=kwargs.pop("pad", None),
                        adapter=adapter,
                        origin_component_cluster=kwargs.pop("cluster", None),
                        origin_component_sheet=kwargs.pop("sheet", None),
                        sheet_names=kwargs.pop("sheet_names", None))


def test_role_origin_single_candidate_regression():
    """A single same-role candidate, no Cluster/Sheet — behaves exactly as
    before (component centre; the extraction bbox default unchanged)."""
    fp = _fp("U1", "FPGA", cluster="CH0", x_mm=10.0, y_mm=20.0)
    origin = _call([fp], "FPGA", _Adapter())
    assert origin == fp.position


def test_role_origin_returns_bbox_when_no_role_given():
    """No origin kwargs at all -> bbox (lower-left corner), unchanged."""
    fp = _fp("C1", "C_IN", x_mm=10.0, y_mm=5.0)
    origin = _find_origin([fp], [], None, None, None, _Adapter())
    assert origin == fp.position  # single fp bbox = its position


def test_role_origin_multiple_candidates_ambiguous_without_narrowing():
    """The live bug 2026-08-31: the same role in two different Clusters in the
    selection used to silently pick the first. Now: FATAL (ambiguous) with a
    hint to set Cluster/Sheet."""
    fp0 = _fp("U1", "FPGA", cluster="CH0", x_mm=1.0, y_mm=2.0)
    fp1 = _fp("U2", "FPGA", cluster="CH1", x_mm=3.0, y_mm=4.0)
    with pytest.raises(ValidationError, match="ambiguous"):
        _call([fp0, fp1], "FPGA", _Adapter())


def test_role_origin_cluster_narrowing_resolves():
    """origin_component_cluster narrows the same-role candidates to the right
    Cluster (segment-prefix match, as the role-anchor resolver uses)."""
    fp0 = _fp("U1", "FPGA", cluster="CH0", x_mm=1.0, y_mm=2.0)
    fp1 = _fp("U2", "FPGA", cluster="CH1", x_mm=3.0, y_mm=4.0)
    origin = _call([fp0, fp1], "FPGA", _Adapter(), cluster="CH1")
    assert origin == fp1.position


def test_role_origin_cluster_prefix_match():
    """Cluster narrowing is a segment-prefix match ('PWR/DAC0' is matched by
    wanted 'PWR'), not an exact equality."""
    fp0 = _fp("U1", "FPGA", cluster="PWR/DAC0", x_mm=1.0, y_mm=2.0)
    fp1 = _fp("U2", "FPGA", cluster="ANALOG/ADC", x_mm=3.0, y_mm=4.0)
    origin = _call([fp0, fp1], "FPGA", _Adapter(), cluster="PWR")
    assert origin == fp0.position


def test_role_origin_cluster_matching_nothing_stays_ambiguous():
    """No-guessing: a Cluster that matches NONE of the candidates does not
    silently pick — it stays ambiguous (same as the role-anchor resolver)."""
    fp0 = _fp("U1", "FPGA", cluster="CH0", x_mm=1.0, y_mm=2.0)
    fp1 = _fp("U2", "FPGA", cluster="CH1", x_mm=3.0, y_mm=4.0)
    with pytest.raises(ValidationError, match="ambiguous"):
        _call([fp0, fp1], "FPGA", _Adapter(), cluster="NOPE")


def test_role_origin_sheet_narrowing_resolves():
    """origin_component_sheet narrows via the resolved sheet-instance path
    (needs sheet_names, the uuid -> human-readable map)."""
    # sheet_path_uuids: the LAST element is the component's own uuid (dropped),
    # the rest are its ancestor sheets.
    fp0 = _fp("U1", "FPGA", x_mm=1.0, y_mm=2.0,
              sheet_uuids=("sheet-uuid-0", "fp-uuid-0"))
    fp1 = _fp("U2", "FPGA", x_mm=3.0, y_mm=4.0,
              sheet_uuids=("sheet-uuid-1", "fp-uuid-1"))
    sheet_names = {"sheet-uuid-0": "Channel_0", "sheet-uuid-1": "Channel_1"}
    origin = _call([fp0, fp1], "FPGA", _Adapter(),
                   sheet="Channel_1", sheet_names=sheet_names)
    assert origin == fp1.position


def test_role_origin_cluster_plus_pad():
    """Cluster narrowing + pad refinement compose: origin is the specific pad
    of the narrowed component."""
    fp0 = _fp("U1", "FPGA", cluster="CH0", x_mm=1.0, y_mm=2.0)
    fp1 = _fp("U2", "FPGA", cluster="CH1", x_mm=3.0, y_mm=4.0)
    adapter = _Adapter(pads_by_ref={
        "U2": [_FakePad("3", 30.0, 40.0), _FakePad("1", 31.0, 41.0)],
    })
    origin = _call([fp0, fp1], "FPGA", adapter, pad="3", cluster="CH1")
    assert origin == Vector2.from_xy(30_000_000, 40_000_000)


def test_role_origin_not_found_fatal_still_works():
    """Role not in the selection at all -> the original 'not found in
    selection' fatal, unchanged."""
    fp = _fp("U1", "FPGA", cluster="CH0")
    with pytest.raises(ValidationError, match="not found in selection"):
        _call([fp], "ADC", _Adapter())

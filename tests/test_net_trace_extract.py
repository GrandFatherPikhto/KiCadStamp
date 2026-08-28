# tests/test_net_trace_extract.py
"""
Mock-adapter tests for the `extract-net` path (net_trace_extract.py) — plan
2026_08_21_net_traces.md §2.3:
  - happy path (net found, anchor unambiguous) -> local offsets from the anchor
    point, explicit net/layer;
  - fatal on a net with no copper at all;
  - fatal on an absent anchor_role;
  - fatal on an ambiguous anchor_role (two candidates, no narrowing);
  - anchor_pad not set -> footprint centre is the anchor point;
  - anchor_pad set but missing on the footprint -> fatal;
  - s-expr upsert: same-net write replaces, distinct-net write appends, other
    top-level keys are preserved.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.domain.geometry import Vector2
from kicadstamp.domain.geometry import BoardLayer

from kicadstamp.config import NetTrace
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.net_trace_extract import (extract_net_trace, write_net_trace,
                                          net_trace_to_dict, read_net_trace_flags)
from kicadstamp.utils.units import MM


def _make_fp(ref, role, x_mm, y_mm):
    fp = MagicMock()
    fp.ref = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp._role = role
    return fp


def _make_pad(x_mm, y_mm):
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    return pad


def _make_track(sx, sy, ex, ey, net, width=0.25, layer=BoardLayer.BL_F_Cu):
    t = MagicMock()
    t.net_name = net
    t.start = Vector2.from_xy(int(sx * MM), int(sy * MM))
    t.end = Vector2.from_xy(int(ex * MM), int(ey * MM))
    t.width_mm = width
    t.layer = layer
    return t


def _make_via(x, y, net, drill=0.3, dia=0.6):
    v = MagicMock()
    v.net_name = net
    v.position = Vector2.from_xy(int(x * MM), int(y * MM))
    v.drill_mm = drill
    v.diameter_mm = dia
    return v


def _adapter(fps, tracks, vias, pad_by_number=None):
    """Shared MagicMock adapter: role lookup from fp._role, tracks/vias as
    given, get_pad_by_number optionally routed through pad_by_number (a
    dict {number: Pad})."""
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_selected_items.return_value = []
    adapter.get_tracks.return_value = tracks
    adapter.get_vias.return_value = vias
    if pad_by_number is not None:
        adapter.get_pad_by_number.side_effect = lambda fp, num: pad_by_number.get(num)
    return adapter


# ── happy paths ───────────────────────────────────────────────────────────────


def test_extract_happy_path_with_pad_anchor():
    fpga = _make_fp("U1", "FPGA", 50, 50)
    pad42 = _make_pad(52, 52)
    adapter = _adapter(
        [fpga],
        [_make_track(53, 54, 55, 56, "DAC_DB0", 0.2),   # rel pad42(52,52): (1,2)->(3,4)
         _make_track(100, 100, 110, 110, "OTHER_NET")],  # wrong net, ignored
        [_make_via(57, 58, "DAC_DB0"),                    # rel pad42: (5,6)
         _make_via(1, 1, "GND")],
        pad_by_number={"42": pad42},
    )

    nt = extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA", anchor_pad="42")

    assert isinstance(nt, NetTrace)
    assert nt.net == "DAC_DB0"
    assert nt.anchor_role == "FPGA"
    assert nt.anchor_pad == "42"
    assert len(nt.tracks) == 1
    assert len(nt.vias) == 1

    t = nt.tracks[0]
    assert t.start_along_mm == 1.0 and t.start_across_mm == 2.0
    assert t.end_along_mm == 3.0 and t.end_across_mm == 4.0
    assert t.width_mm == 0.2
    assert t.net == "DAC_DB0"
    assert t.layer == "F.Cu"

    v = nt.vias[0]
    assert v.offset_along_mm == 5.0 and v.offset_across_mm == 6.0
    assert v.net == "DAC_DB0"
    assert v.drill_mm == 0.3 and v.diameter_mm == 0.6


def test_extract_no_anchor_pad_uses_footprint_center():
    fpga = _make_fp("U1", "FPGA", 50, 50)
    # Track starts exactly at the footprint centre -> local (0,0).
    adapter = _adapter(
        [fpga],
        [_make_track(50, 50, 55, 55, "DAC_DB0")],
        [_make_via(50, 50, "DAC_DB0")],
    )

    nt = extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA")

    assert nt.anchor_pad is None
    assert nt.tracks[0].start_along_mm == 0.0 and nt.tracks[0].start_across_mm == 0.0
    assert nt.tracks[0].end_along_mm == 5.0 and nt.tracks[0].end_across_mm == 5.0
    assert nt.vias[0].offset_along_mm == 0.0 and nt.vias[0].offset_across_mm == 0.0


# ── fatals ────────────────────────────────────────────────────────────────────


def test_extract_fatal_when_net_has_no_copper():
    fpga = _make_fp("U1", "FPGA", 50, 50)
    adapter = _adapter(
        [fpga],
        [_make_track(100, 100, 110, 110, "OTHER_NET")],
        [_make_via(1, 1, "GND")],
    )
    with pytest.raises(ValidationError, match="has no copper"):
        extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA")


def test_extract_fatal_when_anchor_role_missing():
    adapter = _adapter([], [], [])  # no footprints at all
    with pytest.raises(ValidationError, match="anchor_role .* not found"):
        extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA")


def test_extract_fatal_when_anchor_role_ambiguous():
    fpga1 = _make_fp("U1", "FPGA", 50, 50)
    fpga2 = _make_fp("U2", "FPGA", 10, 10)  # same Role, no sheet/cluster narrowing
    adapter = _adapter(
        [fpga1, fpga2],
        [_make_track(53, 54, 55, 56, "DAC_DB0")],
        [],
    )
    with pytest.raises(ValidationError, match="ambiguous"):
        extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA")


def test_extract_fatal_when_anchor_pad_missing_on_footprint():
    fpga = _make_fp("U1", "FPGA", 50, 50)
    adapter = _adapter(
        [fpga],
        [_make_track(53, 54, 55, 56, "DAC_DB0")],
        [],
        pad_by_number={},  # pad "42" absent
    )
    with pytest.raises(ValidationError, match="anchor pad .* not found"):
        extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA", anchor_pad="42")


# ── s-expr upsert ─────────────────────────────────────────────────────────────


def test_write_net_trace_replaces_same_net_and_preserves_other_keys(tmp_path):
    out = tmp_path / "trace.sexp"
    # Pre-existing content with another top-level key.
    out.write_text(dict_to_sexp({"rules": [{"net": "GND"}]}), encoding="utf-8")

    nt = NetTrace(net="DAC_DB0", anchor_role="FPGA",
                  tracks=[], vias=[])
    write_net_trace(str(out), nt)
    write_net_trace(str(out), NetTrace(
        net="DAC_DB0", anchor_role="FPGA", anchor_pad="42", tracks=[], vias=[]))

    data = sexp_to_dict(out.read_text(encoding="utf-8"))
    assert "rules" in data  # other top-level key preserved
    assert len(data["net_traces"]) == 1  # same net replaced, not duplicated
    assert data["net_traces"][0]["net"] == "DAC_DB0"
    assert data["net_traces"][0]["anchor_pad"] == "42"


def test_write_net_trace_appends_distinct_nets(tmp_path):
    out = tmp_path / "trace.sexp"
    write_net_trace(str(out), NetTrace(net="DAC_DB0", anchor_role="FPGA",
                                       tracks=[], vias=[]))
    write_net_trace(str(out), NetTrace(net="DAC_DB1", anchor_role="FPGA",
                                       tracks=[], vias=[]))
    data = sexp_to_dict(out.read_text(encoding="utf-8"))
    assert [e["net"] for e in data["net_traces"]] == ["DAC_DB0", "DAC_DB1"]


def test_net_trace_to_dict_omits_default_false_fields():
    nt = NetTrace(net="DAC_DB0", anchor_role="FPGA", anchor_pad="42",
                  tracks=[], vias=[])
    d = net_trace_to_dict(nt)
    assert d["net"] == "DAC_DB0"
    assert d["anchor_role"] == "FPGA"
    assert d["anchor_pad"] == "42"
    assert "anchor_sheet" not in d
    assert "retired" not in d
    assert "skip" not in d


# ── config round-trip (loader) ────────────────────────────────────────────────


def test_load_config_net_traces_roundtrip(tmp_path):
    from kicadstamp.config import load_config
    cfg_path = tmp_path / "board.sexp"
    # layer "B.Cu" is deliberately NON-default — the s-expr writer omits fields
    # equal to their dataclass default, and a net_traces track requires an
    # explicit layer at load time (an omitted default "F.Cu" would be a load
    # fatal "has no layer").
    cfg_path.write_text(dict_to_sexp({
        "net_traces": [{
            "net": "DAC_DB0", "anchor_role": "FPGA", "anchor_pad": "42",
            "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                        "end_along_mm": 3.0, "end_across_mm": 4.0,
                        "width_mm": 0.2, "net": "DAC_DB0", "layer": "B.Cu"}],
            "vias": [{"offset_along_mm": 5.0, "offset_across_mm": 6.0,
                      "net": "DAC_DB0", "drill_mm": 0.3, "diameter_mm": 0.6}],
        }],
    }), encoding="utf-8")
    cfg, _ctx = load_config(str(cfg_path))
    assert len(cfg.net_traces) == 1
    nt = cfg.net_traces[0]
    assert nt.net == "DAC_DB0"
    assert nt.anchor_role == "FPGA"
    assert nt.anchor_pad == "42"
    assert len(nt.tracks) == 1 and len(nt.vias) == 1
    assert nt.tracks[0].net == "DAC_DB0" and nt.tracks[0].layer == "B.Cu"


def test_load_config_net_traces_missing_anchor_role_fatal(tmp_path):
    from kicadstamp.config import load_config
    cfg_path = tmp_path / "board.sexp"
    cfg_path.write_text(dict_to_sexp({"net_traces": [{"net": "DAC_DB0"}]}),
                        encoding="utf-8")
    with pytest.raises(ValidationError, match="without anchor_role"):
        load_config(str(cfg_path))


def test_load_config_net_traces_duplicate_net_fatal(tmp_path):
    from kicadstamp.config import load_config
    cfg_path = tmp_path / "board.sexp"
    cfg_path.write_text(dict_to_sexp({
        "net_traces": [
            {"net": "DAC_DB0", "anchor_role": "FPGA"},
            {"net": "DAC_DB0", "anchor_role": "FPGA"},
        ],
    }), encoding="utf-8")
    with pytest.raises(ValidationError, match="unique net"):
        load_config(str(cfg_path))


def test_load_config_net_traces_track_without_layer_fatal(tmp_path):
    """Review fix 2026-08-21: a net_traces track with layer: null (or no
    layer) must be a LOAD-TIME fatal — unlike cells:, there is no cell to
    inherit a layer from, and a silent F.Cu default would route copper onto
    the wrong side."""
    from kicadstamp.config import load_config
    cfg_path = tmp_path / "board.sexp"
    # A track dict without a layer key -> the s-expr has no layer node, and
    # load_config must reject it at load time.
    cfg_path.write_text(dict_to_sexp({
        "net_traces": [{
            "net": "DAC_DB0", "anchor_role": "FPGA",
            "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                        "end_along_mm": 3.0, "end_across_mm": 4.0,
                        "net": "DAC_DB0"}],
        }],
    }), encoding="utf-8")
    with pytest.raises(ValidationError, match="has no layer"):
        load_config(str(cfg_path))


# ── re-extract preserves hand-set retired/skip (review fix 2026-08-21) ────────


def test_extract_carries_retired_and_skip_params():
    fpga = _make_fp("U1", "FPGA", 50, 50)
    adapter = _adapter(
        [fpga],
        [_make_track(50, 50, 55, 55, "DAC_DB0")],
        [],
    )
    nt = extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA",
                           retired=True, skip=True)
    assert nt.retired is True
    assert nt.skip is True


def test_read_net_trace_flags(tmp_path):
    out = tmp_path / "trace.sexp"
    assert read_net_trace_flags(str(out), "DAC_DB0") == (False, False)  # no file
    out.write_text(dict_to_sexp({
        "net_traces": [
            {"net": "DAC_DB0", "anchor_role": "FPGA", "retired": True},
            {"net": "DAC_DB1", "anchor_role": "FPGA", "skip": True},
        ],
    }), encoding="utf-8")
    assert read_net_trace_flags(str(out), "DAC_DB0") == (True, False)
    assert read_net_trace_flags(str(out), "DAC_DB1") == (False, True)
    assert read_net_trace_flags(str(out), "DAC_DB9") == (False, False)


def test_reextract_keeps_retired_flag_through_write(tmp_path):
    """End-to-end: a record marked retired: true survives a re-extract+write —
    geometry is refreshed, the hand-set flag is NOT cleared."""
    fpga = _make_fp("U1", "FPGA", 50, 50)
    adapter = _adapter(
        [fpga],
        [_make_track(50, 50, 55, 55, "DAC_DB0")],
        [],
    )
    out = tmp_path / "trace.sexp"
    write_net_trace(str(out), NetTrace(net="DAC_DB0", anchor_role="FPGA", retired=True))

    existing_retired, _skip = read_net_trace_flags(str(out), "DAC_DB0")
    nt = extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA",
                           retired=existing_retired)
    write_net_trace(str(out), nt)

    data = sexp_to_dict(out.read_text(encoding="utf-8"))
    entry = data["net_traces"][0]
    assert entry.get("retired") is True  # survived the re-extract
    assert len(entry["tracks"]) == 1  # geometry refreshed


def test_write_net_trace_yaml_output_is_fatal(tmp_path):
    """2026-08-28, core_yaml_removal: a .yaml output path is a fatal
    ValidationError pointing at sexp_config_convert.py, not a silent YAML
    write."""
    out = tmp_path / "trace.yaml"
    with pytest.raises(ValidationError, match="sexp_config_convert.py"):
        write_net_trace(str(out), NetTrace(net="DAC_DB0", anchor_role="FPGA"))
    assert not out.exists()


# ── s-expr output (2026-08-28, sexp_output_writers_fix) ──────────────────────
#
# write_net_trace/read_net_trace_flags used to recognize ONLY ".json" and
# silently YAML-write everything else — including a ".sexp" output path, which
# then failed to parse as s-expr on the next load_config(). Both now dispatch
# three ways by file suffix (.json/.sexp/YAML), symmetric to config_writer's
# _read_data/_write_data and config/includes.py's _load_config_file.


def test_write_net_trace_sexp_roundtrips(tmp_path):
    """--output foo.sexp writes REAL s-expr text: sexp_to_dict reads back the
    same net_traces record the YAML path produces (compared against the
    default-stripped canonical form — dict_to_sexp omits fields equal to their
    dataclass default, the same contract test_sexp_config_roundtrip asserts)."""
    from kicadstamp.config import TemplateTrack, TemplateVia
    from kicadstamp.config.sexp_format import _strip_defaults, sexp_to_dict

    out = tmp_path / "trace.sexp"
    nt = NetTrace(
        net="DAC_DB0", anchor_role="FPGA", anchor_pad="42",
        tracks=[TemplateTrack(
            start_along_mm=1.0, start_across_mm=2.0,
            end_along_mm=3.0, end_across_mm=4.0,
            width_mm=0.2, net="DAC_DB0", layer="F.Cu")],
        vias=[TemplateVia(
            offset_along_mm=5.0, offset_across_mm=6.0,
            net="DAC_DB0", drill_mm=0.3, diameter_mm=0.6)],
        retired=True,
    )
    write_net_trace(str(out), nt)

    text = out.read_text(encoding="utf-8")
    assert text.lstrip().startswith("(kicadstamp-config")  # s-expr, not YAML
    data = sexp_to_dict(text)
    # _strip_defaults must see the record wrapped under its `net_traces:`
    # section (it operates on a CONFIG dict, not a bare record — that is what
    # applies the NetTrace/TemplateVia/TemplateTrack default-stripping the
    # s-expr writer also performs).
    expected = _strip_defaults({"net_traces": [net_trace_to_dict(nt)]})["net_traces"][0]
    assert data["net_traces"][0] == expected


def test_write_net_trace_sexp_upsert_replaces_same_net(tmp_path):
    """The .sexp path keeps the same upsert semantics as YAML: a same-net write
    REPLACES in place (no duplicate record), a distinct-net write appends, and
    the file stays a parseable s-expr."""
    from kicadstamp.config.sexp_format import sexp_to_dict

    out = tmp_path / "trace.sexp"
    write_net_trace(str(out), NetTrace(net="DAC_DB0", anchor_role="FPGA"))
    write_net_trace(str(out), NetTrace(
        net="DAC_DB0", anchor_role="FPGA", anchor_pad="42"))
    write_net_trace(str(out), NetTrace(net="DAC_DB1", anchor_role="FPGA"))

    data = sexp_to_dict(out.read_text(encoding="utf-8"))
    assert [e["net"] for e in data["net_traces"]] == ["DAC_DB0", "DAC_DB1"]
    assert data["net_traces"][0]["anchor_pad"] == "42"


def test_read_net_trace_flags_sexp(tmp_path):
    """read_net_trace_flags parses an existing .sexp trace file (symmetric to
    the YAML test above) and recovers the hand-set retired:/skip: flags."""
    from kicadstamp.config.sexp_format import dict_to_sexp

    out = tmp_path / "trace.sexp"
    out.write_text(dict_to_sexp({
        "net_traces": [
            {"net": "DAC_DB0", "anchor_role": "FPGA", "retired": True},
            {"net": "DAC_DB1", "anchor_role": "FPGA", "skip": True},
        ],
    }), encoding="utf-8")

    assert read_net_trace_flags(str(out), "DAC_DB0") == (True, False)
    assert read_net_trace_flags(str(out), "DAC_DB1") == (False, True)
    assert read_net_trace_flags(str(out), "DAC_DB9") == (False, False)


def test_reextract_keeps_retired_flag_through_sexp(tmp_path):
    """End-to-end over .sexp (the .sexp analog of the YAML test above): a
    record marked retired: true survives a re-extract+write — geometry is
    refreshed, the hand-set flag is NOT cleared."""
    from kicadstamp.config.sexp_format import sexp_to_dict

    fpga = _make_fp("U1", "FPGA", 50, 50)
    adapter = _adapter(
        [fpga],
        [_make_track(50, 50, 55, 55, "DAC_DB0")],
        [],
    )
    out = tmp_path / "trace.sexp"
    write_net_trace(str(out), NetTrace(net="DAC_DB0", anchor_role="FPGA", retired=True))

    existing_retired, _skip = read_net_trace_flags(str(out), "DAC_DB0")
    nt = extract_net_trace(adapter, net="DAC_DB0", anchor_role="FPGA",
                           retired=existing_retired)
    write_net_trace(str(out), nt)

    data = sexp_to_dict(out.read_text(encoding="utf-8"))
    entry = data["net_traces"][0]
    assert entry.get("retired") is True  # survived the re-extract
    assert len(entry["tracks"]) == 1  # geometry refreshed

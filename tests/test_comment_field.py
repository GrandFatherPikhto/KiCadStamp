# tests/test_comment_field.py
"""comment — optional free-form note field on all 7 top-level config
entities (Cell/Rule/ClonePlacement/CoordinatePlacement/
ThermalViaArrayConfig/NetTrace/Point, handoff_2026_08_27_entity_comment_field.md).
A plain schema field (NOT a syntactic comment): survives the YAML/s-expr
dict round-trip and shows up in the GUI. Here: read through load_config(),
absent -> None, like every other optional field."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import load_config

FULL_YAML = """\
layer: B.Cu
cells:
  c1:
    layer: B.Cu
    comment: cell note
    components: []
points:
  p1:
    anchor_role: LDO1
    comment: point note
rules:
  - net: +3V3
    anchor_role: FPGA
    comment: rule note
    spokes: []
clone_placements:
  - cluster: CH0
    cell: c1
    xy: [0.0, 0.0]
    comment: clone note
coordinate_placements:
  - cluster: CH0
    role: R_FILT
    x_mm: 1.0
    y_mm: 2.0
    rotation_deg: 0.0
    comment: coord note
thermal_via_arrays:
  - name: tva1
    anchor_role: AD9707
    comment: tva note
net_traces:
  - net: +3V3
    anchor_role: FPGA
    comment: trace note
"""

# Same config WITHOUT any comment: — every .comment must be None.
NO_COMMENT_YAML = FULL_YAML.replace("\n    comment: cell note", "") \
    .replace("\n    comment: point note", "") \
    .replace("\n    comment: rule note", "") \
    .replace("\n    comment: clone note", "") \
    .replace("\n    comment: coord note", "") \
    .replace("\n    comment: tva note", "") \
    .replace("\n    comment: trace note", "")


def _load(text: str, tmp_path, name="cfg.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    cfg, _ = load_config(str(p))
    return cfg


def test_comment_read_for_all_7_entities(tmp_path):
    cfg = _load(FULL_YAML, tmp_path)
    assert cfg.cells["c1"].comment == "cell note"
    assert cfg.points["p1"].comment == "point note"
    assert cfg.rules[0].comment == "rule note"
    assert cfg.clone_placements[0].comment == "clone note"
    assert cfg.coordinate_placements[0].comment == "coord note"
    assert cfg.thermal_via_arrays[0].comment == "tva note"
    assert cfg.net_traces[0].comment == "trace note"


def test_absent_comment_defaults_to_none(tmp_path):
    cfg = _load(NO_COMMENT_YAML, tmp_path)
    assert cfg.cells["c1"].comment is None
    assert cfg.points["p1"].comment is None
    assert cfg.rules[0].comment is None
    assert cfg.clone_placements[0].comment is None
    assert cfg.coordinate_placements[0].comment is None
    assert cfg.thermal_via_arrays[0].comment is None
    assert cfg.net_traces[0].comment is None

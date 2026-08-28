# generate_config.py
from dataclasses import dataclass
from typing import Dict

from kicadstamp.config.sexp_format import dict_to_sexp

@dataclass
class CloneParams:
    name: str
    anchor_ref: str
    anchor_pad: str
    origin_x: float
    origin_y: float
    rotation_deg: float
    params: Dict[str, str]
    nets: Dict[str, str]

# Шаблон вынесен в отдельную переменную (можно импортировать из другого файла)
TEMPLATE = {
    "layer": "B.Cu",
    "vias": [...],  # полный шаблон
    "components": [...],
    "tracks": [...]
}

FILTERS = [
    CloneParams(
        name="fpga_filter_1v2_vccint",
        anchor_ref="IC1",
        anchor_pad="102",
        origin_x=7.0,
        origin_y=0.0,
        rotation_deg=90.0,
        params={"PWR_IN": "+1V2", "PWR_OUT": "+1V2_VCCINT"},
        nets={
            "PI_FILTER_C1": "{PWR_IN}",
            "PI_FILTER_C2": "{PWR_IN}",
            "PI_FILTER_FB": "{PWR_OUT}",
            "PI_FILTER_C3": "{PWR_OUT}",
            "PI_FILTER_C4": "{PWR_OUT}",
        }
    ),
    CloneParams(
        name="fpga_filter_3v3_vccio",
        anchor_ref="IC1",
        anchor_pad="26",
        origin_x=-7.0,
        origin_y=0.0,
        rotation_deg=90.0,
        params={"PWR_IN": "+3V3", "PWR_OUT": "+3V3_VCCIO"},
        nets={
            "PI_FILTER_C1": "{PWR_IN}",
            "PI_FILTER_C2": "{PWR_IN}",
            "PI_FILTER_FB": "{PWR_OUT}",
            "PI_FILTER_C3": "{PWR_OUT}",
            "PI_FILTER_C4": "{PWR_OUT}",
        }
    ),
    # ... можно добавить в цикле
]

config = {
    "templates": {"pi_filter_4": TEMPLATE},
    "clone_placements": [
        {
            "name": f.name,
            "template": "pi_filter_4",
            "anchor_ref": f.anchor_ref,
            "anchor_pad": f.anchor_pad,
            "origin_x_mm": f.origin_x,
            "origin_y_mm": f.origin_y,
            "rotation_deg": f.rotation_deg,
            "params": f.params,
            "nets": f.nets,
            "enabled": True,
        }
        for f in FILTERS
    ]
}

with open("generated_config.sexp", "w", encoding="utf-8") as f:
    f.write(dict_to_sexp(config))
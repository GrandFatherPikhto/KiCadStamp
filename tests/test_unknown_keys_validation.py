#!/usr/bin/env python3
"""check_unknown_keys coverage for the two loading paths that were missing it
until now: ManualSpoke (spokes: inside a rule) and thermal_via_array:. Same
bug class as origin-by-via-net (dash typo) hit live on boards/3ch-awg-tia —
a typo'd/wrong field name in these two blocks was previously silently
ignored, no error at all. See _MANUAL_SPOKE_KNOWN_KEYS/
_THERMAL_VIA_ARRAY_KNOWN_KEYS in config/loader.py.

2026-08-28, core_yaml_removal: the typo-fatals are hand-written s-expr (a
typo'd record key now dies in the s-expr parser's "unknown key in a record"
fatal, which still names the key). The close-match SUGGESTION survives only
on the .json path — there the raw dict reaches the loader's check_unknown_keys
(difflib suggestion), whereas s-expr rejects the key before the loader runs."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    if name.endswith(".json"):
        p.write_text(json.dumps(data), encoding="utf-8")
    else:
        p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


class TestManualSpokeUnknownKeys:
    def test_typo_field_in_spoke_is_fatal(self, tmp_path):
        """Hand-written s-expr: dict_to_sexp would itself fatal on the typo'd
        key at SERIALIZE time. In s-expr the rejection is the parser's
        "unknown key in a record" — it still names the typo'd key."""
        config_file = tmp_path / "test.sexp"
        config_file.write_text(
            "(kicadstamp-config\n"
            '  (layer "B.Cu")\n'
            "  (cells)\n"
            "  (rules\n"
            '    (rule (net "+3V3_VCCIO") (anchor_role "FPGA")\n'
            "      (spokes\n"
            '        (spoke (pad "17") (cell "t") (retierd false))))))\n',
            encoding="utf-8")
        with pytest.raises(ValidationError, match="retierd"):
            load_config(str(config_file))

    def test_suggests_close_match(self, tmp_path):
        """The loader's check_unknown_keys close-match SUGGESTION is only
        reachable on the .json path (raw dict -> loader); s-expr rejects the
        key before the loader runs, so keep this coverage on JSON."""
        config_file = _write(tmp_path, "test.json", {
            "layer": "B.Cu",
            "cells": {},
            "rules": [{
                "net": "+3V3_VCCIO", "anchor_role": "FPGA",
                "spokes": [{"pad": "17", "cell": "t", "retierd": False}],
            }],
        })
        with pytest.raises(ValidationError, match="retired"):
            load_config(str(config_file))

    def test_all_known_spoke_fields_load_fine(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", {
            "layer": "B.Cu",
            "cells": {},
            "rules": [{
                "net": "+3V3_VCCIO", "anchor_role": "FPGA",
                "spokes": [{
                    "pad": "17", "cell": "t",
                    "shift_x_mm": 1.0, "shift_y_mm": -1.0,
                    "rotation_deg": 90.0, "retired": False,
                    "cluster": "Channel_0", "skip": True,
                }],
            }],
        })
        cfg, _ = load_config(str(config_file))
        spoke = cfg.rules[0].spokes[0]
        assert spoke.cluster == "Channel_0"
        assert spoke.retired is False
        assert spoke.skip is True


class TestThermalViaArrayUnknownKeys:
    def test_typo_field_is_fatal(self, tmp_path):
        """Hand-written s-expr for the same reason as the spoke typo test."""
        config_file = tmp_path / "test.sexp"
        config_file.write_text(
            "(kicadstamp-config\n"
            '  (layer "B.Cu")\n'
            "  (cells)\n"
            "  (thermal_via_arrays\n"
            '    (thermal_via_array (retired false) (anchor_role "FPGA")'
            ' (pad "145") (name "fpga_thermal") (rowss 4))))\n',
            encoding="utf-8")
        with pytest.raises(ValidationError, match="rowss"):
            load_config(str(config_file))

    def test_all_known_fields_load_fine(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", {
            "layer": "B.Cu",
            "cells": {},
            "thermal_via_arrays": [{
                "retired": False, "anchor_role": "FPGA",
                "anchor_sheet": "Channel_0", "anchor_cluster": "FPGA_BANK",
                "pad": "145", "net": "GND",
                "rows": 4, "cols": 4, "margin_mm": 0.5, "pattern": "grid",
                "drill_mm": 0.3, "diameter_mm": 0.5,
                "name": "fpga_thermal", "skip": True,
            }],
        })
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_arrays[0].retired is False
        assert cfg.thermal_via_arrays[0].skip is True

    def test_absent_thermal_via_arrays_is_fine(self, tmp_path):
        config_file = _write(tmp_path, "test.sexp", {
            "layer": "B.Cu",
            "cells": {},
        })
        cfg, _ = load_config(str(config_file))
        # An absent section is simply an empty list (2026-08-02: generalized
        # to thermal_via_arrays:, a real list — no more special sentinel,
        # see test_naming.py for the full story).
        assert cfg.thermal_via_arrays == []

# tests/config/test_format3_reference_hint.py
"""Part 1 of plan_2026_10_05_uuid_tails (Д1/2): the format-3 refusal of a
dangling reference shows the reference's NAME HINT and, when a record of the
target section has a close name, the "did you mean ...?" suggestion.

ONE rule for EVERY reference kind (config/name_hint.close_name_hint) — never a
points-only special case — and NEVER a repair by name: the uuid is the identity
(§0). A reference with no name hint refuses exactly as before.
"""
import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def _write(tmp_path, name, data, fmt=2):
    p = tmp_path / name
    p.write_text(dict_to_sexp(data, format_number=fmt), encoding="utf-8")
    return p


def _cells_and_clone(cell_ref, **anchor):
    """A minimal graph: one cell, one clone_placement naming it (possibly with a
    typo) plus an optional anchor_point reference."""
    placement = {"cluster": "cp1", "cell": cell_ref, "xy": [1.0, 2.0]}
    placement.update(anchor)
    return {
        "cells": {"pi_filter": {"components": [{"role": "R"}]}},
        "clone_placements": [placement],
    }


def test_a_cell_reference_typo_gets_the_same_hint(tmp_path):
    """The suggestion is NOT a points-only special case: a `cell:` typo (target
    section `cells:`) gets the very same hint — the rule lives in ONE place."""
    f = _write(tmp_path, "root.sexp", _cells_and_clone("pi_filte"))
    with pytest.raises(ValidationError) as e:
        load_config(str(f))
    text = str(e.value)
    assert "did you mean 'pi_filter'" in text
    assert "pi_filte" in text


def test_a_hint_far_from_every_name_is_shown_without_a_suggestion(tmp_path):
    """A name that resembles nothing: the refusal still NAMES it (the user sees
    what they typed), but offers no wrong suggestion."""
    f = _write(tmp_path, "root.sexp", {
        "points": {"fpga_center": {"anchor_role": "FPGA"}},
        "cells": {"pi_filter": {"components": [{"role": "R"}]}},
        "clone_placements": [{"cluster": "cp1", "cell": "pi_filter",
                              "xy": [1.0, 2.0],
                              "anchor_point": "zzz_nothing_like_it"}],
    })
    with pytest.raises(ValidationError) as e:
        load_config(str(f))
    text = str(e.value)
    assert "zzz_nothing_like_it" in text
    assert "name hint" in text
    assert "did you mean" not in text


def test_a_reference_without_a_name_hint_reads_as_before(tmp_path):
    """A dangling uuid with an EMPTY name beside it adds neither a hint nor a
    suggestion — the previous refusal, unchanged."""
    f = _write(tmp_path, "root.sexp", {
        "cells": {"pi_filter": {"components": [{"role": "R"}], "uuid": "U-C1"}},
        "clone_placements": [{"name": "cp1", "uuid": "U-CP", "cluster": "cp1",
                              "cell": "pi_filter", "cell_uuid": "U-C1",
                              "xy": [1.0, 2.0],
                              "anchor_point": "", "anchor_point_uuid": "U-GONE"}],
    }, fmt=3)
    with pytest.raises(ValidationError) as e:
        load_config(str(f))
    text = str(e.value)
    assert "U-GONE" in text
    assert "name hint" not in text
    assert "did you mean" not in text


def test_a_valid_name_never_repairs_a_dangling_uuid(tmp_path):
    """The name is a HINT, the uuid is the identity (§0): a reference whose name
    is exactly right but whose uuid names no record is STILL refused — no
    "found by name, substituted" repair."""
    f = _write(tmp_path, "root.sexp", {
        "points": {"fpga_center": {"anchor_role": "FPGA", "uuid": "U-PT"}},
        "cells": {"pi_filter": {"components": [{"role": "R"}], "uuid": "U-C1"}},
        "clone_placements": [{"name": "cp1", "uuid": "U-CP", "cluster": "cp1",
                              "cell": "pi_filter", "cell_uuid": "U-C1",
                              "xy": [1.0, 2.0],
                              "anchor_point": "fpga_center",
                              "anchor_point_uuid": "U-GONE"}],
    }, fmt=3)
    with pytest.raises(ValidationError) as e:
        load_config(str(f))
    assert "U-GONE" in str(e.value)

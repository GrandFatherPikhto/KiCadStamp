# tests/test_imprint_rename.py
"""Guards for the 2026-09-20 rename `Scheme List` -> `Imprint` (D7 of
plan_2026_09_18_scheme_list_to_cell_and_capture.md).

The rename changed the config SURFACE (section key, entity key, record tags,
module/class names, UI strings) but must never break a profile that is already
on disk:

  * С14 — an old profile still carrying `scheme_lists:` / `scheme_list:` loads
    and produces EXACTLY the same redraw plan as the same profile rewritten
    with the canonical keys;
  * С15 — a file (or a record) carrying BOTH spellings at once is a fatal, not
    a silent merge;
  * С16 — no legacy spelling is left in the shipped sources (ast/text scan),
    except the one shim module that owns the compatibility rule.

С18 (both catalogues complete after the rename) is the pre-existing
`tests/test_i18n.py::TestCatalogCompleteness`.

Headless: no Qt, no live board — the redraw plan is computed against a stub
adapter, exactly like the other imprint_apply tests.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.imprint_apply import plan_imprint

ROOT = Path(__file__).resolve().parent.parent


# ── a profile that exists in two spellings ──────────────────────────────────

def _record_dict(name="amp"):
    """A minimal valid Imprint record with copper of every kind, so the plan
    comparison exercises components, vias AND tracks."""
    return {
        "name": name,
        "source_sheet": "Channel_0",
        "pivot": [1.5, -2.5],
        "components": [
            {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
            {"ref": "C1", "offset_along_mm": 10.0, "offset_across_mm": 5.0,
             "rotation_deg": 90.0},
        ],
        "vias": [{"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                  "drill_mm": 0.3, "diameter_mm": 0.6, "net": "GND"}],
        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                    "end_along_mm": 1.0, "end_across_mm": 1.0,
                    "width_mm": 0.25, "layer": "F.Cu", "net": "+3V3"}],
    }


def _profile(record_key, entity_key):
    """The SAME profile in two spellings: legacy keys vs canonical ones."""
    return {
        "cells": {},
        record_key: [_record_dict()],
        "entities": [{"name": "E_AMP", entity_key: "amp"}],
    }


# The SAME profile as raw s-expr TEXT in both spellings. Written as text, not
# through dict_to_sexp: the writer is canonical-ONLY by design (writing a
# legacy key is a typo fatal), while reading must accept both — that asymmetry
# is exactly what these guards pin.
_LEGACY_SEXP = '''(kicadstamp-config
  (cells)
  (scheme_lists
    (scheme_list
      (name "amp")
      (source_sheet "Channel_0")
      (pivot 1.5 -2.5)
      (components
        (scheme_list_component (ref "R1"))
        (scheme_list_component (ref "C1") (offset_along_mm 10.0)
          (offset_across_mm 5.0) (rotation_deg 90.0))
      )
      (vias
        (scheme_list_via (offset_along_mm 1.0) (offset_across_mm 2.0)
          (drill_mm 0.3) (diameter_mm 0.6) (net "GND"))
      )
      (tracks
        (scheme_list_track (end_along_mm 1.0) (end_across_mm 1.0)
          (width_mm 0.25) (layer "F.Cu") (net "+3V3"))
      )
    )
  )
  (entities
    (entity
      (name "E_AMP")
      (scheme_list "amp")
    )
  )
)
'''

_CANONICAL_SEXP = '''(kicadstamp-config
  (cells)
  (imprints
    (imprint
      (name "amp")
      (source_sheet "Channel_0")
      (pivot 1.5 -2.5)
      (components
        (imprint_component (ref "R1"))
        (imprint_component (ref "C1") (offset_along_mm 10.0)
          (offset_across_mm 5.0) (rotation_deg 90.0))
      )
      (vias
        (imprint_via (offset_along_mm 1.0) (offset_across_mm 2.0)
          (drill_mm 0.3) (diameter_mm 0.6) (net "GND"))
      )
      (tracks
        (imprint_track (end_along_mm 1.0) (end_across_mm 1.0)
          (width_mm 0.25) (layer "F.Cu") (net "+3V3"))
      )
    )
  )
  (entities
    (entity
      (name "E_AMP")
      (imprint "amp")
    )
  )
)
'''


class _StubAdapter:
    """The only two things plan_imprint needs off the adapter in `in place`
    mode: the footprints (for ref -> layer resolution)."""

    def __init__(self, refs=("R1", "C1")):
        self._fps = [SimpleNamespace(ref=r, layer=BoardLayer.BL_F_Cu)
                     for r in refs]

    def get_footprints(self):
        return list(self._fps)


def _plan_signature(plan):
    """A comparable, identity-free signature of an ImprintApplyPlan: the
    commands' DATA. Comparing the dataclasses directly would compare Angle /
    Vector2 instances by identity."""
    return (
        plan.entity_name,
        plan.mode,
        dict(plan.ref_map),
        [(m.ref, m.position.x, m.position.y, m.angle.degrees, m.layer)
         for m in plan.moves],
        [(v.position.x, v.position.y, v.drill_mm, v.diameter_mm, v.net_name,
          v.registry_key) for v in plan.vias],
        [(t.start.x, t.start.y, t.end.x, t.end.y, t.width_mm, t.net_name,
          t.layer, t.registry_key) for t in plan.tracks],
    )


def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


# ── С14: the legacy profile is the SAME profile ─────────────────────────────

class TestLegacyProfileLoadsIdentically:
    def _load_pair(self, tmp_path):
        legacy_path = tmp_path / "legacy.sexp"
        canonical_path = tmp_path / "canonical.sexp"
        legacy_path.write_text(_LEGACY_SEXP, encoding="utf-8")
        canonical_path.write_text(_CANONICAL_SEXP, encoding="utf-8")
        cfg_legacy, _ = load_config(str(legacy_path))
        cfg_canonical, _ = load_config(str(canonical_path))
        return cfg_legacy, cfg_canonical

    def test_legacy_section_and_entity_keys_load(self, tmp_path):
        cfg_legacy, _ = self._load_pair(tmp_path)
        assert [r.name for r in cfg_legacy.imprints] == ["amp"]
        entity = next(e for e in cfg_legacy.entities if e.name == "E_AMP")
        assert entity.imprint == "amp"
        assert entity.cell is None

    def test_legacy_profile_gives_the_same_redraw_plan(self, tmp_path):
        cfg_legacy, cfg_canonical = self._load_pair(tmp_path)
        adapter = _StubAdapter()
        signatures = []
        for cfg in (cfg_legacy, cfg_canonical):
            entity = next(e for e in cfg.entities if e.name == "E_AMP")
            record = cfg.imprints[0]
            plan = plan_imprint(entity, record, adapter,
                                Vector2.from_xy(1_000_000, 2_000_000), 90.0)
            signatures.append(_plan_signature(plan))
        # Zero differences, and not vacuously: the plan really carries copper.
        assert signatures[0] == signatures[1]
        assert signatures[0][3] and signatures[0][4] and signatures[0][5]

    def test_legacy_json_profile_loads_too(self, tmp_path):
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(_profile("scheme_lists", "scheme_list")),
                        encoding="utf-8")
        cfg, _ = load_config(str(path))
        assert [r.name for r in cfg.imprints] == ["amp"]
        assert cfg.entities[0].imprint == "amp"

    def test_legacy_record_tags_still_parse(self, tmp_path):
        """The record tags inside the section (`(scheme_list ...)`,
        `(scheme_list_component ...)`, ...) are WRITTEN under their new names
        but must keep PARSING under the old ones — pinned by the legacy text
        loading at all."""
        cfg_legacy, cfg_canonical = self._load_pair(tmp_path)
        legacy_rec = cfg_legacy.imprints[0]
        canonical_rec = cfg_canonical.imprints[0]
        assert legacy_rec == canonical_rec
        assert [c.ref for c in legacy_rec.components] == ["R1", "C1"]
        assert legacy_rec.vias and legacy_rec.tracks

    def test_writer_emits_only_the_canonical_spelling(self):
        text = dict_to_sexp(_profile("imprints", "imprint"))
        assert "(imprints" in text and "(imprint " in text
        assert "scheme_list" not in text


# ── С15: both spellings in one file/record is fatal, never a merge ──────────

class TestBothSpellingsAreFatal:
    def test_both_section_keys_in_one_json_file(self, tmp_path):
        """The SAME record name on both spellings on purpose: with the alias
        fatal switched off nothing else could fail either (the entity resolves,
        the record loads), so this test can only pass for the RIGHT reason.

        Found by mutation М15 (2026-09-20): the first version let a legacy record
        named differently REPLACE the canonical one, and the test stayed green on
        the loader's unrelated "entity references a missing imprints entry"."""
        data = _profile("imprints", "imprint")
        data["scheme_lists"] = [_record_dict("amp")]
        path = tmp_path / "both.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValidationError, match="scheme_lists"):
            load_config(str(path))

    def test_both_entity_keys_in_one_json_record(self, tmp_path):
        data = _profile("imprints", "imprint")
        data["entities"][0]["scheme_list"] = "amp"
        path = tmp_path / "both_entity.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValidationError, match="imprint"):
            load_config(str(path))

    def test_both_entity_keys_in_one_sexp_record(self, tmp_path):
        """The s-expr path has its own guard: the alias mapping must not let
        the second spelling win silently (config/sexp_format.py::_parse_record).
        Both orders are exercised, legacy-first and canonical-first."""
        legacy_first = _LEGACY_SEXP.replace(
            '(scheme_list "amp")', '(scheme_list "amp") (imprint "amp")')
        canonical_first = _CANONICAL_SEXP.replace(
            '(imprint "amp")', '(imprint "amp") (scheme_list "amp")')
        for i, text in enumerate((legacy_first, canonical_first)):
            path = tmp_path / f"both_entity_{i}.sexp"
            path.write_text(text, encoding="utf-8")
            with pytest.raises(ValidationError):
                load_config(str(path))


# ── С16: nothing of the old name is left in the shipped sources ─────────────

_SHIP_DIRS = ("kicadstamp", "gui", "mcp_server")
# The compatibility CHAIN, and nothing else, is allowed to know the legacy
# spelling (the plan's "except config/aliases.py and the places that parse the
# old spelling"): the module that owns the rule, the two that APPLY it (the
# raw-dict entity loader and the s-expr record parser), and the GUI page that
# reads a legacy entity key through the shared helper.
_WHITELIST = {
    Path("kicadstamp/config/aliases.py"),
    Path("kicadstamp/config/entries.py"),
    Path("kicadstamp/config/sexp_format.py"),
    Path("gui/docks/entity_page.py"),
}
# Legacy storage file names: a profile's `include:` points at them BY NAME, so
# they must survive the rename untouched (see gui/docks/imprint.py).
_STORAGE_NAMES = ("scheme_lists.sexp", "scheme_lists.json")
_LEGACY_RE = re.compile(r"scheme[_ -]?lists?", re.IGNORECASE)
# References to HISTORICAL handoff documents (`plan_2026_09_05_scheme_list.md`)
# are not the legacy NAME of the feature: techdocs/ is an archive and is never
# renamed, so a docstring keeping its citation is correct, not a leftover.
_WORD_RE = re.compile(r"[A-Za-z0-9_.\-/]+")


def _document_reference_spans(text: str):
    return [(m.start(), m.end()) for m in _WORD_RE.finditer(text)
            if m.group(0).endswith(".md")]


def _inside(spans, pos: int) -> bool:
    return any(start <= pos < end for start, end in spans)


def _ship_sources():
    for base in _SHIP_DIRS:
        for path in sorted((ROOT / base).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


class TestNoLegacyNameInShipSources:
    def test_only_the_shim_module_knows_the_old_name(self):
        offenders = []
        for path in _ship_sources():
            rel = path.relative_to(ROOT)
            if rel in _WHITELIST:
                continue
            text = path.read_text(encoding="utf-8")
            for name in _STORAGE_NAMES:
                text = text.replace(name, "")
            spans = _document_reference_spans(text)
            for match in _LEGACY_RE.finditer(text):
                if _inside(spans, match.start()):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{rel}:{line}: {match.group(0)!r}")
        assert offenders == [], (
            "the 2026-09-20 rename left the legacy name in shipped sources "
            "(only config/aliases.py may know it, plus the legacy storage file "
            "names): " + repr(offenders[:10]))

    def test_the_whitelist_cannot_grow_silently(self):
        """Guards the whitelist itself: a new file may only join it by editing
        this assertion, i.e. deliberately."""
        assert {str(p) for p in _WHITELIST} == {
            "kicadstamp/config/aliases.py",
            "kicadstamp/config/entries.py",
            "kicadstamp/config/sexp_format.py",
            "gui/docks/entity_page.py",
        }

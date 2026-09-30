# tests/test_sheet_names.py
"""LazySheetNameMap's contract: always truthy without materialising, real
content only on access. Added after a live bug (2026-08-25): __bool__ was
missing, so Python fell back to __len__() for truthiness — meaning the
"sheet_names or {}" fallback used throughout the placement code
(dependency_order, planner, clone_role_resolver, position calculators,
validation, ...) forced the sexpdata parse immediately, defeating the whole
point of laziness."""
import kicadstamp.sheet_names as sheet_names_module
from kicadstamp.sheet_names import LazySheetNameMap


def _make_map(monkeypatch, data: dict[str, str]) -> tuple[LazySheetNameMap, list[int]]:
    calls: list[int] = []

    def fake_build(config_path, schematic_dir, schematic_files):
        calls.append(1)
        return dict(data)

    monkeypatch.setattr(sheet_names_module, "build_sheet_name_map", fake_build)
    return LazySheetNameMap("config.yaml", None, []), calls


def test_bool_is_true_without_materialising(monkeypatch):
    m, calls = _make_map(monkeypatch, {"u1": "Sheet1"})
    assert bool(m) is True
    assert calls == []


def test_or_fallback_does_not_materialise(monkeypatch):
    m, calls = _make_map(monkeypatch, {"u1": "Sheet1"})
    result = m or {}
    assert result is m
    assert calls == []


def test_empty_map_is_still_truthy(monkeypatch):
    """Even a genuinely empty map must not report falsy — truthiness is
    about "don't force a parse", not about the map's real content."""
    m, calls = _make_map(monkeypatch, {})
    assert bool(m) is True
    assert calls == []


def test_getitem_materialises_and_caches(monkeypatch):
    m, calls = _make_map(monkeypatch, {"u1": "Sheet1"})
    assert m["u1"] == "Sheet1"
    assert calls == [1]
    assert m["u1"] == "Sheet1"
    assert calls == [1]  # second access — no re-parse


def test_len_and_iteration_materialise(monkeypatch):
    m, calls = _make_map(monkeypatch, {"u1": "Sheet1", "u2": "Sheet2"})
    assert len(m) == 2
    assert calls == [1]
    assert sorted(m) == ["u1", "u2"]
    assert calls == [1]  # still cached


def test_eq_against_plain_dict(monkeypatch):
    m, calls = _make_map(monkeypatch, {"u1": "Sheet1"})
    assert m == {"u1": "Sheet1"}
    assert calls == [1]
    assert m != {"u1": "Other"}


def test_dict_conversion(monkeypatch):
    m, calls = _make_map(monkeypatch, {"u1": "Sheet1", "u2": "Sheet2"})
    assert dict(m) == {"u1": "Sheet1", "u2": "Sheet2"}
    assert calls == [1]

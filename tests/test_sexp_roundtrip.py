# tests/test_sexp_roundtrip.py
"""Round-trip fidelity tests for the s-expr write path added to
kicadstamp/cloner/sexp.py (sym/dumps/save_file) — the generic write
capability, no tree grammar, no GUI, no YAML integration.

The comparison helper _eq() is deliberately type-strict: Python treats
1 == 1.0 as True, so a naive equality check would silently pass a test
that corrupted an int into a float (or a str into a Symbol). Checking
type(a) is type(b) FIRST is what makes the mixed-type tests non-
tautological — this is exactly why sym()/sval() exist.
"""
import sexpdata
from pathlib import Path

from kicadstamp.cloner.sexp import dumps, load_file, save_file, sym


def _eq(a, b) -> bool:
    """Type-strict structural comparison: type identity is checked BEFORE
    any value comparison, so int/float and Symbol/str mix-ups are caught."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return a == b


def _roundtrip(obj):
    """dumps() then loads() must come back structurally identical
    (type-strict)."""
    return sexpdata.loads(dumps(obj))


# ── basic leaf round-trips ────────────────────────────────────────────────

def test_roundtrip_scalars_preserve_types():
    """int, float, str and Symbol must each survive dumps->loads with their
    exact Python type (1 must NOT become 1.0, a Symbol must stay a Symbol)."""
    for obj in (1, 1.5, "hello", sym("hello")):
        back = _roundtrip(obj)
        assert type(back) is type(obj)
        assert back == obj


def test_roundtrip_int_is_not_turned_into_float():
    """The classic Python gotcha: 1 == 1.0 — a naive equality assert would
    pass even if dumps corrupted the int. The type-strict _eq must flag it."""
    assert _eq(_roundtrip(1), 1)
    assert not _eq(_roundtrip(1), 1.0)


def test_sym_and_str_are_distinguished():
    """sym("x") and "x" differ only by type — exactly the distinction
    sym()/sval() exist to preserve, and dumps must keep it."""
    back = _roundtrip([sym("tree"), sym("name"), "plain"])
    assert type(back[0]) is sexpdata.Symbol
    assert type(back[1]) is sexpdata.Symbol
    assert type(back[2]) is str


# ── nested / structural round-trips ───────────────────────────────────────

def test_roundtrip_nested_tree():
    """A realistic nested (key ...) tree round-trips identically, including
    Symbol-vs-str distinction in the same node."""
    obj = [sym("kicadstamp-trees"),
           [sym("version"), 1],
           [sym("tree"),
            [sym("name"), "power_tree"],
            [sym("node"),
             [sym("ref"), "AMS1117_REG"],
             [sym("kind"), sym("clone")],
             [sym("xy"), 5.0, 2.0],
             [sym("node"), [sym("ref"), "C_OUT"], [sym("xy"), 1.0, 0]]]]]
    back = _roundtrip(obj)
    assert _eq(back, obj)


def test_roundtrip_empty_structures():
    """Empty list and a deeply-nested-but-empty structure must survive."""
    assert _eq(_roundtrip([]), [])
    assert _eq(_roundtrip([[[]]]), [[[]]])


def test_roundtrip_deep_nesting():
    """A moderately deep chain keeps its shape (no truncation/stack issue in
    this range)."""
    obj = []
    for _ in range(50):
        obj = [sym("wrap"), obj]
    back = _roundtrip(obj)
    assert _eq(back, obj)


def test_roundtrip_mixed_types():
    """A mixed scalar/list tree round-trips with every type intact —
    guarded by the type-strict _eq."""
    obj = [sym("mixed"), 1, 2.5, "text", [sym("x"), 0], 3]
    back = _roundtrip(obj)
    assert _eq(back, obj)


def test_roundtrip_unicode_and_special_characters():
    """Unicode and whitespace/punctuation inside strings must survive the
    text round-trip (utf-8 on the write side)."""
    obj = [sym("name"), "Полоса питания", "C_OUT/1", "a b\tc", "quotes \" and '"]
    back = _roundtrip(obj)
    assert _eq(back, obj)


# ── save_file / load_file round-trip ──────────────────────────────────────

def test_save_file_then_load_file_roundtrips(tmp_path):
    """save_file() writes what load_file() later reads back — the file-level
    analog of the in-memory dumps/loads round-trip."""
    path = str(tmp_path / "out.sexp")
    obj = [sym("kicadstamp-trees"),
           [sym("version"), 1],
           [sym("tree"), [sym("name"), "misc"],
            [sym("node"), [sym("ref"), "R_DEBUG"], [sym("xy"), 100.0, 50.0]]]]

    save_file(path, obj)
    assert Path(path).exists()
    back = load_file(path)
    assert _eq(back, obj)


def test_save_file_writes_utf8(tmp_path):
    """save_file() must write utf-8 — unicode text survives a read-back."""
    path = str(tmp_path / "unicode.sexp")
    save_file(path, [sym("name"), "Блок питания 3.3В"])
    text = Path(path).read_text(encoding="utf-8")
    assert "Блок питания 3.3В" in text
    back = load_file(path)
    assert _eq(back, [sym("name"), "Блок питания 3.3В"])

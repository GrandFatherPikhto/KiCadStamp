#!/usr/bin/env python3

from kicadstamp.schematic_blocks import (escape_sexp_string, find_balanced_span,
                                         find_insertion_point, find_property_value_span,
                                         find_symbol_at, iter_symbol_blocks, unescape_sexp_string)
from tests.fieldstool_fixtures import sch_file, symbol_block


def test_find_balanced_span_simple():
    text = "(a (b) (c (d)))"
    assert find_balanced_span(text, 0) == len(text)


def test_find_balanced_span_ignores_parens_in_strings():
    text = '(a "(not a paren)" (b))'
    end = find_balanced_span(text, 0)
    assert text[:end] == text


def test_iter_symbol_blocks_single_ref():
    text = sch_file(symbol_block(["R1"], role="R_A", cluster="Cl_A"))
    blocks = iter_symbol_blocks("f.kicad_sch", text)
    assert len(blocks) == 1
    assert blocks[0].refs == {"R1"}


def test_iter_symbol_blocks_multi_instance_shares_one_block():
    """Multi-instance sheet: several refdes in ONE (symbol ...) block."""
    text = sch_file(symbol_block(["R41", "R50", "R59"], role="R_A"))
    blocks = iter_symbol_blocks("f.kicad_sch", text)
    assert len(blocks) == 1
    assert blocks[0].refs == {"R41", "R50", "R59"}


def test_iter_symbol_blocks_multi_unit_is_two_separate_blocks():
    """Multi-unit symbol: one refdes across SEVERAL separate blocks."""
    text = sch_file(symbol_block(["U1"], role="OA_A"), symbol_block(["U1"], role="OA_B"))
    blocks = iter_symbol_blocks("f.kicad_sch", text)
    assert len(blocks) == 2
    assert all(b.refs == {"U1"} for b in blocks)
    assert blocks[0].id != blocks[1].id


def test_find_property_value_span_reads_value():
    text = sch_file(symbol_block(["R1"], role="R_A"))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    span_text = text[block.start:block.end]
    span = find_property_value_span(span_text, "Role")
    assert span is not None
    vs, ve = span
    assert span_text[vs:ve] == "R_A"


def test_find_property_value_span_missing_field_is_none():
    text = sch_file(symbol_block(["R1"]))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    span_text = text[block.start:block.end]
    assert find_property_value_span(span_text, "Role") is None


def test_find_symbol_at():
    text = sch_file(symbol_block(["R1"], x=12.5, y=34.75))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    span_text = text[block.start:block.end]
    x, y = find_symbol_at(span_text)
    assert x == "12.5" and y == "34.75"


def test_find_insertion_point_before_pin():
    text = sch_file(symbol_block(["R1"]))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    span_text = text[block.start:block.end]
    idx, prefix = find_insertion_point(span_text)
    assert span_text[idx:].lstrip().startswith('(pin ')


def test_escape_sexp_string_roundtrip_chars():
    assert escape_sexp_string('a"b\\c') == 'a\\"b\\\\c'


def test_unescape_sexp_string_is_inverse_of_escape():
    # escape_sexp_string('a"b\\c') == 'a\\"b\\\\c' — unescape must undo
    # quote-escape first (\\" -> "), then backslash-escape (\\\\ -> \).
    assert unescape_sexp_string('a\\"b\\\\c') == 'a"b\\c'


def test_unescape_sexp_string_roundtrip():
    for raw in ['plain', 'with "quote"', 'with \\backslash\\', 'both \\" \\\\', 'unicode é']:
        assert unescape_sexp_string(escape_sexp_string(raw)) == raw


def test_iter_symbol_blocks_extracts_top_level_uuid():
    text = sch_file(symbol_block(["R1"], role="R_A", symbol_uuid="AAA-111-BBB"))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    assert block.uuid == "AAA-111-BBB"


def test_iter_symbol_blocks_uuid_is_not_a_pin_uuid():
    """The block's top-level (uuid ...) is the symbol's own id; the (pin ...)
    uuid must NOT be mistaken for it (pins carry their own (uuid ...))."""
    text = sch_file(symbol_block(["R1"], symbol_uuid="SYMBOL-UUID"))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    assert block.uuid == "SYMBOL-UUID"


def test_iter_symbol_blocks_no_uuid_is_none():
    text = sch_file(symbol_block(["R1"]))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    assert block.uuid is None


def test_iter_symbol_blocks_parses_instance_paths():
    text = sch_file(symbol_block(
        ["R1"], role="R_A", symbol_uuid="SYM-UUID",
        instance_paths=[("R1", "/root-uuid/inst-A/SYM-UUID")]))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    assert block.instances == ((("root-uuid", "inst-A", "SYM-UUID"), "R1"),)


def test_iter_symbol_blocks_multiple_instance_paths():
    text = sch_file(symbol_block(
        ["C140", "C161"], role="R_A", symbol_uuid="SYM-1",
        instance_paths=[("C140", "/root/inst-A/SYM-1"),
                        ("C161", "/root/inst-B/SYM-1")]))
    block = iter_symbol_blocks("f.kicad_sch", text)[0]
    assert set(block.refs) == {"C140", "C161"}
    assert block.instances == ((("root", "inst-A", "SYM-1"), "C140"),
                               (("root", "inst-B", "SYM-1"), "C161"))

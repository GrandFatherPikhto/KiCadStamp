# kicadstamp/config/sexp_format.py
"""
config/sexp_format.py — bijective dict <-> s-expr converter for the config.

The whole config pipeline (config/loader.py, includes.py, entries.py, the GUI
docks) works on plain dicts — what yaml.safe_load returns today. This module
serializes/parses that SAME dict into the s-expr grammar from
design_2026_08_27_sexp_config_grammar.md (the "parallel .sexp config format",
chosen 2026-08-27: strings always in double quotes KiCad-style, tags/numbers
bare, true/false as atoms, None/default field values omitted, dict-section
record names in the first position, nested dicts as key-value pairs).

Bijectivity is the hard requirement (round-trip must not corrupt types or
structure — see the type-strict tests in test_sexp_config_roundtrip.py).
Two tools keep the dict <-> s-expr mapping in sync with the schema:

- Schema-aware serialization: field types come from the dataclasses in
  config/models.py (get_type_hints by key path), the section kinds from
  _LIST_SECTIONS/_DICT_SECTIONS in config/includes.py. The converter NEVER
  hand-lists fields — it introspects, so a schema change is picked up
  automatically.
- Type-driven fallback for the free-form sections (extract_profiles,
  clone_profiles, sheet_templates): their entries are dict[str, Any] with no
  dataclass. There a value's own Python type decides its s-expr form
  (str -> quoted string, number/bool -> bare atom, dict -> key-value pairs,
  list -> child nodes).
"""
import dataclasses
import types
from dataclasses import is_dataclass
from typing import Any, get_args, get_origin, get_type_hints

import sexpdata

from ..cloner.sexp import sym, sval
from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from ..trees import (
    anchor_from_dict,
    anchor_to_dict,
    anchor_to_sexp,
    parse_anchor,
    raw_tree_from_sexp,
    tree_from_dict,
    tree_from_sexp,
    tree_to_dict,
    tree_to_sexp,
)
from .aliases import _ENTITY_KEY_ALIASES, _SECTION_ALIASES
from .format_version import (
    VERSION_KEY,
    check_version_value,
    current_format,
    refuse_newer,
    upgrade_data,
)
from .includes import _DICT_SECTIONS, _LIST_SECTIONS
from .models import (
    Cell,
    CellPlacement,
    ClonePlacement,
    Config,
    CoordinatePlacement,
    Entity,
    ManualSpoke,
    NetTrace,
    Chain,
    ImprintBoundaryNet,
    ImprintComponentRecord,
    ImprintConfig,
    ImprintScopePreset,
    ImprintTrackRecord,
    ImprintViaRecord,
    TemplateComponentSlot,
    TemplateTrack,
    TemplateVia,
    ThermalViaArrayConfig,
    TreeInstance,
)
from .points import Point

TOP_TAG = "kicadstamp-config"

TRUE_SYM = sexpdata.Symbol("true")
FALSE_SYM = sexpdata.Symbol("false")

# ── record class -> singular tag (FORK-C, design grammar §3.2) ─────────────
_TAG_BY_CLASS = {
    ManualSpoke: "spoke",
    TemplateVia: "via",
    TemplateComponentSlot: "component",
    TemplateTrack: "track",
    CellPlacement: "clone_placement",
    Cell: "cell",
    Chain: "chain",
    ClonePlacement: "clone_placement",
    Entity: "entity",
    CoordinatePlacement: "coordinate_placement",
    ThermalViaArrayConfig: "thermal_via_array",
    NetTrace: "net_trace",
    Point: "point",
    TreeInstance: "tree_instance",
    ImprintConfig: "imprint",
    ImprintComponentRecord: "imprint_component",
    ImprintScopePreset: "imprint_scope_preset",
    ImprintViaRecord: "imprint_via",
    ImprintTrackRecord: "imprint_track",
    ImprintBoundaryNet: "imprint_boundary_net",
}
_TAG_TO_CLASS = {v: k for k, v in _TAG_BY_CLASS.items()}

# list sections (concatenated) and their record class — mirrors _LIST_SECTIONS.
_LIST_SECTION_CLASS = {
    "chains": Chain,
    "clone_placements": ClonePlacement,
    "thermal_via_arrays": ThermalViaArrayConfig,
    "coordinate_placements": CoordinatePlacement,
    "net_traces": NetTrace,
    "entities": Entity,
    # tree_instances: — a generic list section (plain {template,name,sheet}
    # records, unlike trees: which is _SPECIAL_SECTIONS — see below), handled
    # by the same schema-aware machinery as net_traces/entities.
    "tree_instances": TreeInstance,
    # imprints: — recorded live-board snapshots (design_2026_09_05_scheme_
    # list.md): plain dataclass records with nested components/vias/tracks/
    # boundary_nets lists, handled by the same schema-aware machinery.
    "imprints": ImprintConfig,
}

# dict sections with a real dataclass (record name in the first position).
_DICT_SECTION_CLASS = {
    "cells": Cell,
    "points": Point,
}

# dict sections with NO dataclass -> free-form, type-driven fallback.
_FREE_DICT_SECTIONS = ("extract_profiles", "clone_profiles", "sheet_templates")

# sheet_templates: the only free-form section whose entries have a known
# internal shape (config/sheet_templates.py: _TEMPLATE_KEYS) — typed so a
# one-element `sheets: [Channel_0]` list round-trips correctly. Everything
# else in a sheet_template entry stays type-driven.
_SHEET_TEMPLATE_FIELD_TYPE = {
    "sheets": ("list_str", None),
    "clone_placements": ("list_record", ClonePlacement),
    "coordinate_placements": ("list_record", CoordinatePlacement),
}

# Other free-form dict sections (config/includes.py's _FREE_DICT_SECTIONS)
# whose entries have a KNOWN list-field shape — same parse-side type-hint
# need as sheet_templates' sheets: a one-element `rule_nets: ["+3V3"]` list
# would otherwise round-trip as a bare STRING (_parse_free_field collapses a
# single atom) and silently break single-rule-net profiles. config/
# extract_writer.py writes rule_nets as a sorted list. Mirrored on the
# serialize side in _dict_section_to_sexp.
_FREE_DICT_FIELD_TYPE = {
    "extract_profiles": {
        "rule_nets": ("list_str", None),
    },
}

# trees: — a list section (config/includes.py's _LIST_SECTIONS) whose nodes
# are NOT generic dataclass records: TreeNode is self-referencing, so the
# generic schema-aware machinery is deliberately NOT extended for it. Instead
# the (trees ...) node is delegated wholesale to trees.py's own grammar
# (tree_to_sexp / tree_from_sexp), see design_2026_08_27_trees_in_config_file.md
# FORK-2 Variant B. Config.trees: list[Tree] IS a dataclass field (so the
# loader/validation pipeline sees it), but the converter handles it as a
# special section, not via _field_type/_LIST_SECTION_CLASS.
_SPECIAL_SECTIONS = ("trees",)

# The section sets are authoritative in config/includes.py — assert the
# converter's class maps cover exactly them, so a section added there is
# never silently skipped here (the converter would fatal on it instead).
assert set(_LIST_SECTION_CLASS) | set(_SPECIAL_SECTIONS) == set(_LIST_SECTIONS)
assert set(_DICT_SECTION_CLASS) | set(_FREE_DICT_SECTIONS) == set(_DICT_SECTIONS)


# ── type introspection (FORK-A: the dataclasses are the single schema) ─────

_HINTS_CACHE: dict = {}


def _hints(cls) -> dict:
    if cls not in _HINTS_CACHE:
        _HINTS_CACHE[cls] = get_type_hints(cls)
    return _HINTS_CACHE[cls]


def _norm(tp) -> tuple:
    """Classify a field type annotation into a descriptor tuple:
    ('str',) / ('int',) / ('float',) / ('bool',) / ('tuple2',) /
    ('list_str',) / ('list_record', cls) / ('list_any',) /
    ('dict_pairs', value_kind) / ('record', cls) / ('any',)."""
    origin = get_origin(tp)
    args = get_args(tp)

    if origin is None:
        if tp is str:
            return ("str",)
        if tp is int:
            return ("int",)
        if tp is float:
            return ("float",)
        if tp is bool:
            return ("bool",)
        if is_dataclass(tp):
            return ("record", tp)
        return ("any",)

    if origin is types.UnionType:  # `str | None` (py3.10+)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _norm(non_none[0])
        return ("any",)

    if origin is list:
        elem = args[0] if args else Any
        if elem is str:
            return ("list_str",)
        # list[list[str]] — nested string lists (e.g.
        # ImprintConfig.scope_sheet_paths: each inner list is ONE full sheet
        # path). _norm's generic list_any cannot round-trip a list-of-lists
        # (its all-scalar/all-mapping rule fatals on it), so it gets its own
        # kind with an unambiguous (path "...")-list representation.
        if get_origin(elem) is list:
            inner = get_args(elem) or (Any,)
            if inner and inner[0] is str:
                return ("list_list_str",)
        if is_dataclass(elem):
            return ("list_record", elem)
        return ("list_any",)

    if origin is dict:
        _, val = args if args else (str, Any)
        if val is str:
            return ("dict_pairs", "str")
        if val is Any:
            return ("dict_pairs", "any")
        if val is int:
            return ("dict_pairs", "int")
        if val is float:
            return ("dict_pairs", "float")
        if val is bool:
            return ("dict_pairs", "bool")
        return ("dict_pairs", "any")

    if origin is tuple:
        if len(args) == 2:
            return ("tuple2",)
        return ("any",)

    return ("any",)


def _field_type(dc, field_name: str) -> tuple:
    h = _hints(dc)
    if field_name not in h:
        return ("any",)
    return _norm(h[field_name])


# ── per-field default-value rule (design grammar §3.1) ─────────────────────

def _field_default(dc, field_name):
    for f in dataclasses.fields(dc):
        if f.name == field_name:
            if f.default is not dataclasses.MISSING:
                return f.default
            if f.default_factory is not dataclasses.MISSING:
                return f.default_factory()
            return dataclasses.MISSING
    return dataclasses.MISSING


def _is_default_value(dc, field_name, value) -> bool:
    """True iff `value` equals THIS field's own dataclass default (the
    per-field rule, NOT "all falsy values are dropped"): a required field
    (no default) is never a default; None/False/0 equal a default only when
    the field's default really is that value. place_components=False is NOT
    a default (Config.place_components defaults True) and must be written."""
    default = _field_default(dc, field_name)
    if default is dataclasses.MISSING:
        return False
    return value == default


# ── serialization (dict -> s-expr tree) ────────────────────────────────────

def _is_atom(x) -> bool:
    return not isinstance(x, list)


def _atom_str(x) -> str:
    return sexpdata.dumps(x)


def _dumps(obj, level: int = 0) -> str:
    """Multi-line s-expr text with 2-space indentation. Free-form line breaks
    (the grammar does not fix them) — this just makes the output readable for
    humans/AI; parsing is whitespace-insensitive."""
    pad = "  " * level
    if isinstance(obj, list):
        if not obj:
            return pad + "()"
        if all(_is_atom(x) for x in obj):
            return pad + "(" + " ".join(_atom_str(x) for x in obj) + ")"
        lines = [pad + "(" + _atom_str(obj[0])]
        for child in obj[1:]:
            lines.append(_dumps(child, level + 1))
        lines.append(pad + ")")
        return "\n".join(lines)
    return pad + _atom_str(obj)


def _bool_to_atom(value: bool):
    return TRUE_SYM if value else FALSE_SYM


def _any_atom(value):
    """Type-driven scalar -> atom for free-form values."""
    if isinstance(value, bool):
        return _bool_to_atom(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value
    raise ValidationError(format_fatal_error(
        _("s-expr: unsupported value {value!r} in a free-form field").format(value=value),
        [_("only strings, numbers, booleans, mappings and lists are supported "
           "in the free-form sections (extract_profiles/clone_profiles/sheet_templates)")]
    ))


def _typed_value_to_atom(value, kind: str):
    if kind == "str":
        return value
    if kind == "int":
        return value
    if kind == "float":
        return value
    if kind == "bool":
        return _bool_to_atom(value)
    # kind == "any"
    return _any_atom(value)


def _pair_to_sexp(key, value, value_kind: str):
    """One key-value pair. A scalar value -> one quoted-key atom; a NESTED
    dict value (free-form sections, e.g. fieldstool's fields: {R1: {Role: X}})
    -> quoted key + recursive child pairs, so (fields ("R1" (Role "X")))
    round-trips back to {"R1": {"Role": "X"}} unambiguously; a LIST value
    (free-form, e.g. the cloner snapshot's foreign_in_bbox.segment_nets) ->
    BARE key + atoms, so (segment_nets "GND" "B.Cu") round-trips back to
    ["GND", "B.Cu"] — the bare key keeps it distinct from a quoted-key pair,
    which must be ("key" value) with a single value (a quoted-key node with
    extra atoms stays malformed/fatal, see _parse_pairs_field)."""
    if isinstance(value, dict):
        return [key, *[_pair_to_sexp(k, v, "any") for k, v in value.items()]]
    if isinstance(value, list):
        return [sym(key), *[_typed_value_to_atom(x, "any") for x in value]]
    return [key, _typed_value_to_atom(value, value_kind)]


def _field_to_sexp(name: str, value, desc: tuple):
    kind = desc[0]

    if kind in ("str", "int", "float"):
        return [sym(name), _typed_value_to_atom(value, kind)]

    if kind == "bool":
        return [sym(name), _bool_to_atom(value)]

    if kind == "tuple2":  # (xy 0.0 0.0) — two bare numbers under one tag
        return [sym(name), value[0], value[1]]

    if kind == "list_str":  # (schematic_files "a" "b") — bare strings
        return [sym(name), *[_typed_value_to_atom(x, "str") for x in value]]

    if kind == "list_record":
        sub = desc[1]
        return [sym(name), *[_record_to_sexp(sub, item) for item in value]]

    if kind == "list_list_str":
        # (scope_sheet_paths ("Top" "Channel_0") ("Top")) — one quoted-string
        # LIST node per inner path, so a single-segment path ("Top") survives
        # the round-trip as ["Top"], never collapsing into a bare string.
        return [sym(name), *[list(path) for path in value]]

    if kind == "list_any":  # free-form list — type-driven children
        return _free_list_field_to_sexp(name, value)

    if kind == "dict_pairs":
        value_kind = desc[1]
        return [sym(name), *[_pair_to_sexp(k, v, value_kind) for k, v in value.items()]]

    # kind == "any" — free-form field, decide by the value's type
    return _free_field_to_sexp(name, value)


def _record_to_sexp(dc, data: dict):
    """One schema record, e.g. (rule (net "x") (spokes ...)). Unknown keys are
    fatal (mirrors check_unknown_keys on the YAML side). Default-valued fields
    are omitted (per-field rule, design grammar §3.1).

    One field is delegated to ANOTHER module's grammar instead of the generic
    free-form machinery (see _CUSTOM_FIELD_WRITERS): a tree_instances
    declaration's `anchor` is a TREE anchor, so it must be written as
    (anchor (point "p")) — bare tags — not as the free-form type-driven shape
    (anchor ("point" "p")) which the generic "any" path would produce. The
    delegation is the reason a second anchor grammar never exists."""
    known = {f.name for f in dataclasses.fields(dc)}
    node = [sym(_TAG_BY_CLASS[dc])]
    for key, value in data.items():
        if key not in known:
            raise ValidationError(format_fatal_error(
                _("s-expr: unknown key {key!r} in {tag}").format(key=key, tag=_TAG_BY_CLASS[dc]),
                [_("the s-expr config uses the same per-record known-key rule as YAML "
                   "(check_unknown_keys) — a key outside the record dataclass is a typo")]
            ))
        if _is_default_value(dc, key, value):
            continue
        writer = _CUSTOM_FIELD_WRITERS.get((dc, key))
        if writer is not None:
            node.append(writer(value, data))
            continue
        node.append(_field_to_sexp(key, value, _field_type(dc, key)))
    return node


def _write_tree_instance_anchor(value, record: dict):
    """(anchor ...) for a tree_instances declaration — the TREE anchor grammar
    (trees.anchor_to_sexp), the declaration's dict going through the SAME
    validating dict->TreeAnchor bridge a tree uses (2026-09-12,
    plan_2026_09_12_tree_instance_own_place §И.2)."""
    return anchor_to_sexp(anchor_from_dict(value, record.get("name") or "?"))


def _parse_tree_instance_anchor(field_node, path: str) -> dict:
    """Parse-side mirror of _write_tree_instance_anchor: the (anchor ...) node
    is read by the TREE anchor grammar (trees.parse_anchor, validating) and
    stored in the canonical dict shape (trees.anchor_to_dict)."""
    return anchor_to_dict(parse_anchor(field_node))


# (record class, field name) -> serializer/parser owned by ANOTHER grammar.
# A field listed on one side must be listed on the other too, with the same
# key, or the round-trip is asymmetric. Currently ONE entry: a tree_instances
# declaration's `anchor` speaks the TREE anchor grammar (§И.2).
_CUSTOM_FIELD_WRITERS = {
    (TreeInstance, "anchor"): _write_tree_instance_anchor,
}
_CUSTOM_FIELD_PARSERS = {
    (TreeInstance, "anchor"): _parse_tree_instance_anchor,
}


def _free_field_to_sexp(name: str, value):
    """Type-driven field in a free-form record: str/bool/number -> one atom,
    dict -> key-value pairs, list -> child nodes (see _free_list_field_to_sexp)."""
    if isinstance(value, dict):
        return [sym(name), *[_pair_to_sexp(k, v, "any") for k, v in value.items()]]
    if isinstance(value, list):
        return _free_list_field_to_sexp(name, value)
    return [sym(name), _any_atom(value)]


def _free_list_field_to_sexp(name: str, value):
    """Free-form list under a tag. All-scalar lists -> bare atoms (list_str
    style); all-dict lists -> one child node per entry with a singular tag
    derived from the field name (components->component, clone_placements->
    clone_placement). Mixed content is not representable -> fatal."""
    if all(isinstance(x, dict) for x in value):
        tag = _singular(name)
        return [sym(name), *[_free_record_to_sexp_inner(tag, x) for x in value]]
    if all(_is_scalar(x) for x in value):
        return [sym(name), *[_any_atom(x) for x in value]]
    raise ValidationError(format_fatal_error(
        _("s-expr: unsupported mixed list {name!r} in a free-form field").format(name=name),
        [_("a free-form list must be either all scalars or all mappings, "
           "so it can round-trip unambiguously")]
    ))


def _is_scalar(x) -> bool:
    return isinstance(x, (str, bool, int, float))


def _singular(name: str) -> str:
    return name[:-1] if name.endswith("s") else name


def _free_record_to_sexp_inner(tag: str, data: dict):
    """A free-form record node WITHOUT a leading name (used for list items)."""
    node = [sym(tag)]
    for key, value in data.items():
        node.append(_free_field_to_sexp(key, value))
    return node


def _dict_section_to_sexp(section: str, dc, data: dict):
    """cells/points/extract_profiles/... — record name in the FIRST position
    (KiCad (footprint "R_..." ...) pattern)."""
    node = [sym(section)]
    for name, entry in data.items():
        if dc is not None:  # schema record: cells/points
            rec = _record_to_sexp(dc, entry)
            rec.insert(1, name)
        else:  # free-form record
            tag = _singular(section)
            rec = [sym(tag), name]
            for key, value in entry.items():
                ftype = None
                if section == "sheet_templates":
                    ftype = _SHEET_TEMPLATE_FIELD_TYPE.get(key)
                else:
                    ftype = _FREE_DICT_FIELD_TYPE.get(section, {}).get(key)
                if ftype is not None:
                    rec.append(_field_to_sexp(key, value, ftype))
                else:
                    rec.append(_free_field_to_sexp(key, value))
        node.append(rec)
    return node


def _include_to_sexp(entries):
    """include: — list of path strings and/or {path, enabled} dicts."""
    if isinstance(entries, str):
        entries = [entries]
    node = [sym("include")]
    for entry in entries:
        if isinstance(entry, str):
            node.append(entry)
        else:  # dict {path, enabled}
            node.append([sym("path"), entry.get("path", "")])
            node.append([sym("enabled"), _bool_to_atom(bool(entry.get("enabled", True)))])
    return node


def _trees_to_sexp(value) -> list:
    """dict -> s-expr: (trees (tree ...) (tree ...)). value is the config-dict
    shape (list of plain dicts, each tree_to_dict's output); each dict is
    delegated to trees.py's own grammar via tree_from_dict -> tree_to_sexp
    (FORK-2 Variant B — the generic machinery never sees TreeNode)."""
    return [sym("trees"), *[tree_to_sexp(tree_from_dict(d)) for d in value]]


def _trees_from_sexp(node) -> list:
    """s-expr -> dict: children of (trees ...) are (tree ...) nodes, each
    parsed by trees.py's grammar and converted to the config-dict shape via
    tree_to_dict. seen_names/seen_refs are shared across ALL trees in the
    section, so uniqueness holds for the whole config (same invariant the
    loader enforces via _load_tree's shared seen_refs)."""
    seen_names: set[str] = set()
    seen_refs: set[str] = set()
    out = []
    for tree_node in node[1:]:
        out.append(tree_to_dict(tree_from_sexp(tree_node, seen_names, seen_refs, "<trees>")))
    return out


def _trees_raw_from_sexp(node) -> list:
    """s-expr -> dict WITHOUT grammar validation (trees.raw_tree_from_sexp) —
    ONLY for the one-way old-grammar converter, which must be able to read the
    removed own_anchor grammar (see sexp_to_dict's raw_trees parameter)."""
    return [raw_tree_from_sexp(tree_node) for tree_node in node[1:]]


def _root_child_to_sexp(key: str, value):
    if key in _LIST_SECTION_CLASS:
        dc = _LIST_SECTION_CLASS[key]
        return [sym(key), *[_record_to_sexp(dc, item) for item in value]]
    if key in _DICT_SECTION_CLASS:
        return _dict_section_to_sexp(key, _DICT_SECTION_CLASS[key], value)
    if key in _FREE_DICT_SECTIONS:
        return _dict_section_to_sexp(key, None, value)
    if key == "include":
        return _include_to_sexp(value)
    if key in _SPECIAL_SECTIONS:
        return _trees_to_sexp(value)
    if key in _hints(Config):  # root scalar Config field (layer, ...)
        if _is_default_value(Config, key, value):
            # a default-valued root scalar is omitted entirely
            return None
        return _field_to_sexp(key, value, _field_type(Config, key))
    # free-form root scalar (e.g. output: next to extract_profiles:)
    return _free_field_to_sexp(key, value)


def dict_to_sexp(data: dict, format_number: int | None = None) -> str:
    """Serialize a config dict (what yaml.safe_load returns) into s-expr text,
    wrapped in (kicadstamp-config ...).

    `format_number` — the FORMAT number written as the root's FIRST child
    (config/format_version.py). None (the default) means CURRENT_FORMAT,
    resolved at CALL time so a substituted current format is honoured; the RAW
    path passes the number it READ, never the current one, or format-1 content
    would be stamped as current.

    So EVERY writer stamps a number, and the writer does it itself: a new file
    is born in the current format and none of the ~twenty write sites can forget
    (the whole reason this lives here rather than there) — the same reasoning, on
    the write side, as the reader lifting by default on the read side (Т2).

    The number is NEVER taken from `data`: a `version` key inside the dict is
    dropped, so it can neither be duplicated nor win over the parameter."""
    if not isinstance(data, dict):
        raise ValidationError(format_fatal_error(
            _("s-expr: top level must be a mapping, got {type}").format(type=type(data).__name__),
            [_("(kicadstamp-config ...) must wrap a config mapping")]
        ))
    if format_number is None:
        # current_format(), not the imported constant: the number must be the
        # CURRENT one at write time, never whatever was current when this module
        # happened to be imported first (see format_version.current_format).
        format_number = current_format()
    root = [sym(TOP_TAG)]
    root.append([sym(VERSION_KEY), format_number])
    for key, value in data.items():
        if key == VERSION_KEY:
            continue
        child = _root_child_to_sexp(key, value)
        if child is not None:
            root.append(child)
    return _dumps(root, 0) + "\n"


# ── deserialization (s-expr -> dict) ───────────────────────────────────────

def _fatal(title: str, hints) -> "ValidationError":
    return ValidationError(format_fatal_error(_(title), hints))


def _atom_to_value(atom, kind: str, path: str):
    """Map one parsed atom to a Python value by the expected kind. Every
    mismatch is a distinct fatal (design grammar §4/§5): unquoted string in a
    string field, true/false where a number is expected, and vice versa.

    NOTE: in sexpdata a bare token parses to sexpdata.Symbol, and Symbol
    subclasses str (Symbol -> String -> str) — so the Symbol check MUST come
    first, and quoted strings are only ever the exact `str` type (type(...) is
    str), never a Symbol. Getting this order wrong silently treats a bare
    token as a quoted string."""
    if isinstance(atom, sexpdata.Symbol):
        if kind == "bool":
            if atom == TRUE_SYM:
                return True
            if atom == FALSE_SYM:
                return False
            raise _fatal(
                "s-expr: expected true/false, got a bare atom",
                [_("in {path}: got {value!r}; a boolean field accepts only the "
                   "bare atoms true or false").format(path=path, value=sval(atom))])
        if kind in ("str",):
            raise _fatal(
                "s-expr: expected a quoted string, got a bare atom",
                [_("in {path}: got {value!r} — a string field must be written in "
                   "double quotes (like \"{value}\"); the bare atom means a "
                   "forgotten quote").format(path=path, value=sval(atom))])
        if kind in ("int", "float"):
            raise _fatal(
                "s-expr: expected a number, got a bare atom",
                [_("in {path}: got {value!r}; numbers must be written bare, "
                   "without quotes").format(path=path, value=sval(atom))])
        # kind == "any" in a pair/root free context: bare true/false -> bool
        if atom == TRUE_SYM:
            return True
        if atom == FALSE_SYM:
            return False
        raise _fatal(
            "s-expr: expected a quoted string, got a bare atom",
            [_("in {path}: got {value!r} — a string value must be written in "
               "double quotes").format(path=path, value=sval(atom))])

    if type(atom) is str:  # quoted string (exactly str, never a Symbol)
        if kind == "str":
            return atom
        if kind in ("int", "float", "tuple2"):
            raise _fatal(
                "s-expr: expected a number, got a quoted string",
                [_("in {path}: a numeric field must hold a bare number, not a "
                   "quoted string").format(path=path)])
        if kind == "bool":
            raise _fatal(
                "s-expr: expected true/false, got a quoted string",
                [_("in {path}: a boolean field must hold the bare atom "
                   "true or false").format(path=path)])
        return atom

    if isinstance(atom, bool):
        if kind == "bool":
            return atom
        if kind == "str":
            raise _fatal(
                "s-expr: expected a quoted string, got true/false",
                [_("in {path}: true/false cannot stand for a string — use "
                   "quotes").format(path=path)])
        raise _fatal(
            "s-expr: expected a number, got true/false",
            [_("in {path}: true/false cannot stand for a number").format(path=path)])

    # int / float
    if kind in ("int", "float"):
        return atom
    if kind == "str":
        raise _fatal(
            "s-expr: expected a quoted string, got a number",
            [_("in {path}: a string field must be written in double quotes, "
               "got a bare number").format(path=path)])
    if kind == "bool":
        raise _fatal(
            "s-expr: expected true/false, got a number",
            [_("in {path}: a boolean field accepts only the bare atoms "
               "true or false").format(path=path)])
    return atom


def _parse_scalar_field(node, desc: tuple, path: str):
    """(key <one atom>) for str/int/float/bool fields."""
    if len(node) != 2:
        raise _fatal(
            "s-expr: expected exactly one value",
            [_("in {path}: got {n} child atom(s); a scalar field holds exactly "
               "one value").format(path=path, n=len(node) - 1)])
    return _atom_to_value(node[1], desc[0], path)


def _parse_tuple2(node, path: str):
    """(key x y) — exactly two bare numbers."""
    if len(node) != 3:
        raise _fatal(
            "s-expr: expected exactly 2 numbers in a coordinate pair",
            [_("in {path}: got {n} atom(s); a pair like (xy 0.0 0.0) needs "
               "exactly two numbers").format(path=path, n=len(node) - 1)])
    x = _atom_to_value(node[1], "float", path)
    y = _atom_to_value(node[2], "float", path)
    return [x, y]


def _parse_list_str(node, path: str):
    out = []
    for atom in node[1:]:
        if type(atom) is not str:  # exactly str, never a Symbol subclass
            raise _fatal(
                "s-expr: expected a quoted string in a string list",
                [_("in {path}: every element of a string list must be in "
                   "double quotes").format(path=path)])
        out.append(atom)
    return out


def _parse_list_list_str(node, path: str):
    """list[list[str]] — (key ("a" "b") ("c")): each child is one quoted-string
    list (one inner path); a child whose nodes are not all quoted strings is a
    fatal. Empty field (no children) -> [] (a legitimately-empty list)."""
    out = []
    for child in node[1:]:
        if not isinstance(child, list):
            raise _fatal(
                "s-expr: expected a quoted-string list in a nested string list",
                [_("in {path}: got {value!r}; a list[list[str]] field must be "
                   "a list of quoted-string lists").format(path=path, value=child)])
        row = []
        for atom in child:
            if type(atom) is not str:  # exactly str, never a Symbol subclass
                raise _fatal(
                    "s-expr: expected a quoted string in a nested string list",
                    [_("in {path}: every path segment must be a quoted string").format(path=path)])
            row.append(atom)
        out.append(row)
    return out


def _parse_record(dc, node, path: str) -> dict:
    known = {f.name for f in dataclasses.fields(dc)}
    out: dict = {}
    # Canonical keys that arrived under a LEGACY spelling (2026-09-20 Imprint
    # rename) — a second spelling of any of them on the same record is the
    # "both keys in one record" fatal (see config/aliases.py).
    _aliased_keys: set = set()
    for field_node in node[1:]:
        if not isinstance(field_node, list) or not field_node:
            raise _fatal(
                "s-expr: expected a (key ...) node in a record",
                [_("in {path}: got {value!r}; every field of a record must be "
                   "a (name ...) node").format(path=path, value=field_node)])
        raw_key = sval(field_node[0])
        # Legacy per-record key (2026-09-20 Imprint rename): an Entity written
        # before the rename carries `scheme_list:` and must keep parsing as
        # `imprint:` — the same alias config/entries.py::_load_entity applies
        # to the raw-dict (JSON/YAML) path. Mapping BEFORE the known-key check
        # means the legacy spelling is accepted, the canonical one is written
        # back by the serializer, and an unknown key still fatals.
        key = _ENTITY_KEY_ALIASES.get(raw_key, raw_key)
        if raw_key != key:
            if key in out or key in _aliased_keys:
                # Both spellings in ONE record — the s-expr counterpart of the
                # raw-dict fatal in config/aliases.py. Without this check the
                # alias mapping would silently let the last one win.
                raise _fatal(
                    "s-expr: both the legacy and the current spelling of a "
                    "renamed record key",
                    [_("in {path}: {legacy!r} and {canonical!r} on the same "
                       "record — keep one").format(
                           path=path, legacy=raw_key, canonical=key)])
            _aliased_keys.add(key)
        elif key in _aliased_keys:
            # The canonical spelling after its legacy twin: same fatal.
            raise _fatal(
                "s-expr: both the legacy and the current spelling of a renamed "
                "record key",
                [_("in {path}: the legacy spelling of {canonical!r} was already "
                   "given on this record — keep one").format(
                       path=path, canonical=key)])
        if key not in known:
            raise _fatal(
                "s-expr: unknown key in a record",
                [_("in {path}: key {key!r} is not a field of the {tag} record "
                   "(same known-key rule as YAML's check_unknown_keys)")
                 .format(path=path, key=key, tag=_TAG_BY_CLASS[dc])])
        parser = _CUSTOM_FIELD_PARSERS.get((dc, key))
        if parser is not None:
            out[key] = parser(field_node, f"{path}.{key}")
            continue
        out[key] = _parse_field(field_node, _field_type(dc, key), f"{path}.{key}")
    return out


def _parse_field(node, desc: tuple, path: str):
    kind = desc[0]
    if kind in ("str", "int", "float", "bool"):
        return _parse_scalar_field(node, desc, path)
    if kind == "tuple2":
        return _parse_tuple2(node, path)
    if kind == "list_str":
        return _parse_list_str(node, path)
    if kind == "list_record":
        sub = desc[1]
        return [_parse_record(sub, item, f"{path}[]") for item in node[1:]]
    if kind == "list_list_str":
        return _parse_list_list_str(node, path)
    if kind == "list_any":
        return _parse_free_list_field(node, path)
    if kind == "dict_pairs":
        value_kind = desc[1]
        return _parse_pairs_field(node, value_kind, path)
    # kind == "any" — free-form
    return _parse_free_field(node, path)


def _parse_pairs_field(node, value_kind: str, path: str) -> dict:
    out: dict = {}
    for pair in node[1:]:
        if not isinstance(pair, list) or not pair:
            raise _fatal(
                "s-expr: expected a key-value pair",
                [_("in {path}: got {value!r}; a mapping field is a list of "
                   "(\"key\" value) pairs").format(path=path, value=pair)])
        key = pair[0]
        if type(key) is not str:  # bare-Symbol key -> free-form sub-field
            # (a LIST value inside a free-form dict, e.g. the cloner snapshot's
            # foreign_in_bbox.segment_nets — the parse-side counterpart of
            # _pair_to_sexp's bare-key list branch). Parsed like any free-form
            # field: multiple atoms -> list, single atom -> scalar, none -> [].
            out[sval(key)] = _parse_free_field(pair, f"{path}.{sval(key)}")
            continue
        rest = pair[1:]
        if len(rest) == 1 and not isinstance(rest[0], list):
            out[key] = _atom_to_value(rest[0], value_kind, f"{path}.{key}")
        else:
            # nested dict value (free-form, e.g. fields: {R1: {Role: X}}) —
            # the remaining children are themselves pairs; a quoted-key pair
            # with extra bare atoms falls through here and fatal's on the
            # non-pair child (test_fatal_wrong_pair_shape).
            out[key] = _parse_pairs_field([sym("_"), *rest], "any", f"{path}.{key}")
    return out


def _parse_free_list_field(node, path: str):
    """Free-form list under a tag: bare atoms (list_str style) or child
    record nodes (list-of-dicts)."""
    if all(not isinstance(x, list) for x in node[1:]):
        return [_atom_to_value(x, "any", path) for x in node[1:]]
    out = []
    for item in node[1:]:
        if not isinstance(item, list) or not item:
            raise _fatal(
                "s-expr: expected a (name ...) node in a list",
                [_("in {path}: got {value!r}; a list of mappings must contain "
                   "only (name ...) nodes").format(path=path, value=item)])
        out.append(_parse_free_record(item, path))
    return out


def _parse_free_field(node, path: str) -> Any:
    """Free-form field: scalar (one atom), key-value pairs (dict) or child
    record nodes (list of dicts), decided by the node's content."""
    children = node[1:]
    if not children:
        return []
    if all(not isinstance(x, list) for x in children):
        if len(children) == 1:
            return _atom_to_value(children[0], "any", path)
        return [_atom_to_value(x, "any", path) for x in children]
    # Every child is a node. Quoted-key pairs (or a MIX of quoted-key pairs
    # and bare-key free-form sub-fields — a free-form dict that also carries
    # list values, e.g. the cloner snapshot's foreign_in_bbox) -> a dict via
    # _parse_pairs_field; all-bare-key record nodes -> a list of dicts (e.g.
    # footprints: (footprint ...) (footprint ...)).
    if all(isinstance(x, list) and x for x in children):
        if any(type(x[0]) is str for x in children):
            return _parse_pairs_field(node, "any", path)
        return [_parse_free_record(x, path) for x in children]


def _parse_free_record(node, path: str) -> dict:
    out: dict = {}
    for field_node in node[1:]:
        if not isinstance(field_node, list) or not field_node:
            raise _fatal(
                "s-expr: expected a (key ...) node in a record",
                [_("in {path}: got {value!r}; every field of a record must be "
                   "a (name ...) node").format(path=path, value=field_node)])
        key = sval(field_node[0])
        out[key] = _parse_free_field(field_node, f"{path}.{key}")
    return out


def _parse_free_record_typed(node, path: str, ftype_map: dict) -> dict:
    """A free-form record whose KNOWN list/dict fields are parsed by their
    type hint instead of _parse_free_field's content-driven guess — the
    parse-side counterpart of _dict_section_to_sexp's typed serialization.
    Needed because a one-element (rule_nets "+3V3") list would otherwise
    parse as a bare STRING and silently break single-rule-net profiles on
    round-trip (same class as sheet_templates' sheets)."""
    out: dict = {}
    for field_node in node[1:]:
        if not isinstance(field_node, list) or not field_node:
            raise _fatal(
                "s-expr: expected a (key ...) node in a record",
                [_("in {path}: got {value!r}; every field of a record must be "
                   "a (name ...) node").format(path=path, value=field_node)])
        key = sval(field_node[0])
        ftype = ftype_map.get(key)
        if ftype is not None:
            out[key] = _parse_field(field_node, ftype, f"{path}.{key}")
        else:
            out[key] = _parse_free_field(field_node, f"{path}.{key}")
    return out


def _parse_sheet_template_record(node, path: str) -> dict:
    """sheet_templates entry — sheets/clone_placements/coordinate_placements
    have a KNOWN shape (mirror of _SHEET_TEMPLATE_FIELD_TYPE), everything else
    stays free-form. Needed because a one-element (sheets "Channel_0") list
    would otherwise parse as a bare STRING in _parse_free_field and silently
    break single-sheet templates on round-trip — the serializer types the same
    fields (see _dict_section_to_sexp); this is its parse-side counterpart."""
    return _parse_free_record_typed(node, path, _SHEET_TEMPLATE_FIELD_TYPE)


def _parse_dict_section(node, section: str, path: str) -> dict:
    dc = _DICT_SECTION_CLASS.get(section)
    out: dict = {}
    for rec_node in node[1:]:
        if not isinstance(rec_node, list) or len(rec_node) < 2:
            raise _fatal(
                "s-expr: expected a record node with a quoted name",
                [_("in {path}: got {value!r}; a dict-section record must be "
                   "({tag} \"name\" ...) with the name in the first position")
                 .format(path=path, value=rec_node,
                         tag=_singular(section) if dc is None else _TAG_BY_CLASS[dc])])
        name = rec_node[1]
        if type(name) is not str:  # exactly str, never a Symbol
            raise _fatal(
                "s-expr: expected a quoted record name in the first position",
                [_("in {path}: got {value!r}; the record name of a dict section "
                   "must be a quoted string (like \"dac_buf\")").format(path=path, value=name)])
        if name in out:
            raise _fatal(
                "s-expr: duplicate name in a dict section",
                [_("in {path}: record name {name!r} appears twice — a dict "
                   "section key must be unique").format(path=path, name=name)])
        if dc is not None:
            # body = [tag, *fields] — _parse_record iterates node[1:] = fields
            out[name] = _parse_record(dc, [rec_node[0], *rec_node[2:]], f"{path}.{name}")
        elif section == "sheet_templates":
            out[name] = _parse_sheet_template_record(
                [sym(_singular(section)), *rec_node[2:]], f"{path}.{name}")
        elif section in _FREE_DICT_FIELD_TYPE:
            out[name] = _parse_free_record_typed(
                [sym(_singular(section)), *rec_node[2:]], f"{path}.{name}",
                _FREE_DICT_FIELD_TYPE[section])
        else:
            out[name] = _parse_free_record([sym(_singular(section)), *rec_node[2:]], f"{path}.{name}")
    return out


def _parse_include(node, path: str) -> list:
    entries: list = []
    i = 1
    while i < len(node):
        item = node[i]
        if type(item) is str:  # exactly str, never a Symbol
            entries.append(item)
            i += 1
        elif isinstance(item, list) and sval(item[0]) == "path":
            if len(item) != 2 or type(item[1]) is not str:
                raise _fatal(
                    "s-expr: expected a quoted path in include",
                    [_("in {path}: (path \"sub.sexp\") needs exactly one "
                       "quoted string").format(path=path)])
            enabled = True
            if i + 1 < len(node) and isinstance(node[i + 1], list) and sval(node[i + 1][0]) == "enabled":
                enabled = _atom_to_value(node[i + 1][1], "bool", f"{path}.enabled")
                i += 1
            entries.append({"path": item[1], "enabled": enabled})
            i += 1
        else:
            raise _fatal(
                "s-expr: unexpected node in include",
                [_("in {path}: got {value!r}; include accepts quoted path "
                   "strings and (path ...) (enabled ...) nodes")
                 .format(path=path, value=item)])
    return entries


def _root_version(child, path: str, seen: list[int]) -> int:
    """Root `(version N)` -> the format number, shape-checked.

    Lives in the parse layer on purpose (see sexp_to_dict's docstring): taken
    out where the file is parsed, the number can never reach a caller's dict as
    a free-form root field, and it is gone before the include merge — a
    `(version N)` inside an INCLUDED file used to be a fatal there."""
    if seen:
        raise _fatal(
            "s-expr: more than one root format number",
            [_("in {path}: (version N) appears more than once at the top level — keep exactly one")
             .format(path=path)])
    if len(child) != 2:
        raise _fatal(
            "s-expr: the root format number takes exactly one value",
            [_("in {path}: got {n} atom(s); write (version 2)").format(
                path=path, n=len(child) - 1)])
    return check_version_value(child[1], path, display=repr(sval(child[1])))


def sexp_to_dict(text: str, apply_aliases: bool = True,
                 raw_trees: bool = False, path: str = "<config>",
                 version_out: list[int] | None = None,
                 upgrade: bool = True) -> dict:
    """Parse s-expr config text back into the dict that yaml.safe_load would
    have produced for the equivalent YAML. The top-level node MUST be
    (kicadstamp-config ...).

    apply_aliases=True (default) maps a legacy `(rules ...)` section key to
    `chains` at parse time (2026-09-01 Rule -> Chain rename) — every normal
    reader wants this. The converter tools/convert_rules_to_chains.py passes
    apply_aliases=False so it can still SEE a legacy `rules` key on disk and
    rewrite the file to the canonical `(chains ...)` form.

    raw_trees=True skips trees.py's grammar validation for the trees: section
    (trees.raw_tree_from_sexp): the one-way own_anchor -> mount converter
    (kicadstamp/tree_mount_convert.py) must be able to READ a config written
    in the removed grammar. Every normal reader keeps the default False.

    A root-level `(version N)` — the file's FORMAT number, config/format_
    version.py — is TAKEN OUT here, in the parse layer, and never appears in the
    returned dict; `version_out` (an optional list) receives the number that was
    ON DISK (1 when the file carries none). Doing it here, and not one layer up,
    is what keeps a `(version N)` inside an INCLUDED file from reaching
    `_resolve`'s "unsupported top-level key" fatal, and what keeps every
    existing caller's dict shape unchanged. The reader is liberal about WHERE the
    node sits (a hand edit may move it); the writer always puts it first. A file
    written in a format NEWER than CURRENT_FORMAT is refused right here, for all
    callers at once (refuse_newer) — `path` only names the file in those
    messages.

    upgrade=True (the default) also LIFTS the content to CURRENT_FORMAT before
    returning, so a caller that never asks for the number still gets current
    content and cannot take an older format for the current one (decision A,
    Т2 — see format_version.py's module docstring on why discipline was
    rejected). upgrade=False is for a caller whose job is the file's OWN bytes
    (a format converter): it gets the content as written, and it is then its own
    duty to put the number it read back into the file it rewrites. `path` doubles
    as the step context: a future step needing the profile path refuses
    "<config>" instead of inventing a seed."""
    try:
        root = sexpdata.loads(text)
    except Exception as e:  # sexpdata raises on unbalanced parens etc.
        raise _fatal(
            "s-expr: parse error",
            [_("could not parse the file as s-expr: {error}").format(error=e)]) from e

    if not isinstance(root, list) or not root or sval(root[0]) != TOP_TAG:
        raise _fatal(
            "s-expr: invalid top-level node",
            [_("expected (kicadstamp-config ...) as the outermost form, got "
               "{value!r}").format(value=root)])

    out: dict = {}
    version_seen: list[int] = []
    # The FILE's own label, kept apart from the loop's per-field `path`: the loop
    # REBINDS `path` to "<section>" as it walks the root, and the number belongs
    # to the FILE — a message about a malformed number, a file newer than this
    # build, or a step refusing for a missing path must name the file, not
    # whichever section happened to be parsed last.
    file_path = path
    for child in root[1:]:
        if not isinstance(child, list) or not child:
            raise _fatal(
                "s-expr: expected a (key ...) node at the top level",
                [_("got {value!r}").format(value=child)])
        key = sval(child[0])
        # Root `(version N)` — the file's format number, taken out HERE (see the
        # docstring): it must never survive into the dict, and it must be gone
        # before the include merge (a non-root file carrying it used to be a
        # fatal in includes.py::_resolve).
        if key == VERSION_KEY:
            version_seen.append(_root_version(child, file_path, version_seen))
            continue
        # Legacy section-key aliases (2026-09-01 Rule -> Chain, 2026-09-20
        # Scheme List -> Imprint): an old profile written with `(rules ...)` or
        # `(scheme_lists ...)` must parse as the canonical record class, not as
        # an unknown free field. Same map as config/aliases.py's
        # normalize_section_aliases, applied at parse time so the emitted dict
        # already carries the canonical `chains:` / `imprints:` key.
        # apply_aliases=False (the migration converter) keeps the raw key.
        if apply_aliases:
            key = _SECTION_ALIASES.get(key, key)
        path = f"<{key}>"
        if key in _LIST_SECTION_CLASS:
            dc = _LIST_SECTION_CLASS[key]
            out[key] = [_parse_record(dc, item, f"{path}[]") for item in child[1:]]
        elif key in _DICT_SECTION_CLASS or key in _FREE_DICT_SECTIONS:
            out[key] = _parse_dict_section(child, key, path)
        elif key == "include":
            out[key] = _parse_include(child, path)
        elif key in _SPECIAL_SECTIONS:
            out[key] = (_trees_raw_from_sexp(child) if raw_trees
                        else _trees_from_sexp(child))
        elif key in _hints(Config):
            out[key] = _parse_field(child, _field_type(Config, key), path)
        else:
            out[key] = _parse_free_field(child, path)

    # The number, once, after the whole root is read: the node may sit anywhere
    # (see the docstring), so the lift cannot happen mid-loop.
    version = version_seen[0] if version_seen else 1
    refuse_newer(version, file_path)
    if version_out is not None:
        version_out.append(version)
    if not upgrade:
        return out
    return upgrade_data(out, version, file_path, at_parse_time=True)


# ── default-stripping helper (kept for the YAML-equivalence tests) ─────────

def _strip_defaults(data: dict) -> dict:
    """Return a copy of `data` with every field that equals ITS dataclass
    default removed, recursively — i.e. the exact dict that dict_to_sexp +
    sexp_to_dict is guaranteed to reproduce (design grammar §3.1). Used by the
    YAML-equivalence test: a real profile may explicitly write default-valued
    keys (retired: false, skip: false), which the s-expr format legitimately
    omits."""
    if not isinstance(data, dict):
        return data

    def strip_record(dc, rec: dict) -> dict:
        out = {}
        for key, value in rec.items():
            if key in _hints(dc) and _is_default_value(dc, key, value):
                continue
            out[key] = strip_value(_field_type(dc, key), value)
        return out

    def strip_value(desc, value):
        kind = desc[0]
        if kind == "list_record":
            sub = desc[1]
            return [strip_record(sub, item) for item in value]
        if kind == "dict_pairs":
            return dict(value)
        if kind == "list_str":
            return list(value)
        if kind == "list_list_str":
            return [list(path) for path in value]
        return value

    out = {}
    for key, value in data.items():
        if key in _LIST_SECTION_CLASS:
            dc = _LIST_SECTION_CLASS[key]
            out[key] = [strip_record(dc, item) for item in value]
        elif key in _DICT_SECTION_CLASS:
            dc = _DICT_SECTION_CLASS[key]
            out[key] = {n: strip_record(dc, entry) for n, entry in value.items()}
        elif key in _FREE_DICT_SECTIONS:
            out[key] = dict(value)
        elif key in _SPECIAL_SECTIONS:
            # trees: — normalizing round-trip via the dict bridge, so a
            # default-valued node field (kind None/rotation 0.0/...) is
            # stripped exactly as the s-expr writer omits it. MUST be before
            # _hints(Config) — Config.trees would otherwise route Tree into
            # the generic strip_record and produce a half-dataclass result.
            out[key] = [tree_to_dict(tree_from_dict(d)) for d in value]
        elif key in _hints(Config):
            if _is_default_value(Config, key, value):
                continue
            out[key] = strip_value(_field_type(Config, key), value)
        else:
            out[key] = value
    return out

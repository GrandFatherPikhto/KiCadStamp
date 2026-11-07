# kicadstamp/config/tree_instance_sexp.py
"""The s-expr FORM of a declaration's `root` (Н6, 07.11; acceptance 8.2).

    (root (ref "driver_sw_a" (uuid "76af6a93-…")))

Deliberately the SAME reference node every record already uses — a name beside
the target's uuid — so the two helpers are the parser and the writer of
`sexp_format`, not a second grammar. `sexp_format` registers these two in its
`_CUSTOM_FIELD_WRITERS` / `_CUSTOM_FIELD_PARSERS` tables (the same mechanism the
declaration's `anchor` uses): the s-expr reader takes a record's keys from
`models.TreeInstance`, so without the model field and these two entries a `root`
written by hand loaded as "unknown key in a record" (the live 8.2 blocker).

The imports are lazy: `sexp_format` imports THIS module at table-definition time,
so a module-level import back would close a cycle.
"""
from __future__ import annotations

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

__all__ = ["parse_root", "write_root"]


def write_root(value, _record: dict):
    """A declaration's `root` dict -> `(root (ref "x" [(uuid "…")]))`."""
    from .sexp_format import _ref_field_to_sexp, sym

    value = value if isinstance(value, dict) else {}
    return [sym("root"),
            _ref_field_to_sexp("ref", value.get("ref"), value.get("ref_uuid"))]


def parse_root(field_node, path: str) -> dict:
    """`(root (ref "x" [(uuid "…")]))` -> {'ref': ..., 'ref_uuid': ...}."""
    from .sexp_format import _parse_ref_field

    if len(field_node) != 2 or not isinstance(field_node[1], list):
        raise ValidationError(format_fatal_error(
            _("s-expr: root takes one (ref \"...\") node"),
            [_("in {path}: write (root (ref \"node\"))").format(path=path)]))
    name, uuid = _parse_ref_field(field_node[1], f"{path}.ref")
    out = {"ref": name}
    if uuid is not None:
        out["ref_uuid"] = uuid
    return out

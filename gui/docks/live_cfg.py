# gui/docks/live_cfg.py
"""`_cfg` as a LIVE lookup instead of a snapshot taken at construction.

Live fact (2026-10-20, profile 3ch-awg-tia-v103): a cell was re-read from the
selection and saved while a node form was open. The DOCK picked the new config
up (`refresh_ref_candidates`), the FORM kept the object its constructor had
stored, and its "Read current position" kept comparing the board against the
OLD cell — refusing a pair that is rigid on the board (2.653 mm at the FPGA).
A form lives across saves; a snapshot of the config does not.

ONE rule, one place: the widget stores a SUPPLIER (a plain zero-arg callable)
and exposes `_cfg` as a property that asks it at use time. A caller that passes
only the object gets that exact object back, so no call site has to change at
once; any legacy `self._cfg = obj` inside the class re-points the supplier
instead of shadowing the property (one rule, not two).

Usage: `class Form(QWidget): _cfg = live_cfg()` and, in `__init__`,
`self._cfg_supplier = cfg_supplier or (lambda: cfg)`.
"""
from __future__ import annotations

__all__ = ["live_cfg"]


def live_cfg(supplier_attr: str = "_cfg_supplier"):
    """The `_cfg` property: ask the supplier stored under `supplier_attr`."""

    def _get(self):
        supplier = getattr(self, supplier_attr, None)
        return supplier() if supplier is not None else None

    def _set(self, value):
        setattr(self, supplier_attr, lambda: value)

    return property(_get, _set)

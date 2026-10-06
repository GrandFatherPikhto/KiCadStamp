# gui/docks/cell_form_guard.py
"""Closed-combo guards for the CellDock form (plan
``plan_2026_10_05_celldock_anchor_role_clobber.md``; Claude, 2026-10-05).

THE BUG this module exists for. ``anchor_role_combo`` is a CLOSED QComboBox
built from the cell's OWN component roles. The form used to select the saved
``anchor_role`` BEFORE the new cell's roles were poured into the combo, and a
closed QComboBox that cannot find the text silently keeps/auto-selects its
FIRST item. So loading a cell whose ``anchor_role`` is "FPGA" while the combo
still held the previous cell's roles left the combo on the first role
("CH0_R_TERM_N"), and the next Save / "Update from selection" wrote THAT name
into the record — a silent, destructive clobber.

Two rules, both pure (no Qt) so the guards can drive them directly:

* ``anchor_role_selection`` — the value a closed role combo should SELECT given
  its items and the saved value. A saved value that is NOT among the roles is
  never replaced by "the first one": the combo stays empty and a warning is
  returned for the caller to show; the RECORD keeps its saved value (the
  rewrite invariant below).
* ``effective_anchor_role`` — the rewrite INVARIANT: the record's anchor_role
  is what the WIDGET says ONLY when the user touched it this session; otherwise
  the LOADED value wins, verbatim. The widget is never the source of truth for
  an untouched field.
"""
from __future__ import annotations

from typing import Optional

from kicadstamp.i18n import _

__all__ = ["anchor_role_selection", "effective_anchor_role"]


def anchor_role_selection(roles, saved_role) -> tuple:
    """(value_to_select, warning_or_None) for a closed role combo.

    ``saved_role`` empty -> ("", None) — the "(none)" state, nothing to warn
    about. Present among ``roles`` -> (saved_role, None): now that the items are
    populated, selecting it succeeds. NOT among the roles -> ("", warning): the
    cells are the truth, but the SAVED name must not be silently swapped for the
    first role — the caller keeps it on write (``effective_anchor_role``) and
    shows the warning so a human fixes the record in "Cell anchor"."""
    saved = (saved_role or "").strip()
    if not saved:
        return "", None
    if saved in set(roles or ()):
        return saved, None
    return "", _("anchor_role {role!r} is not a role of this cell — kept; "
                 "fix it in Cell anchor").format(role=saved)


def effective_anchor_role(mode, widget_text, loaded_role,
                          touched: bool) -> Optional[str]:
    """The ``anchor_role`` a Save / Refresh MUST use.

    "Role" mode only; otherwise None (the anchor_role is simply not written —
    the "(none)" state). When the user did not touch the picker this session,
    the LOADED role wins verbatim, whatever the closed combo happens to display
    — that is the invariant that turns a silent clobber into a no-op. When they
    DID touch it, their pick is the answer (an explicit empty pick is None)."""
    if mode != "role":
        return None
    if not touched:
        return (loaded_role or None)
    return (widget_text or "").strip() or None

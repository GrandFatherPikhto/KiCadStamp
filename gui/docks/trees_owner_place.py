# gui/docks/trees_owner_place.py
"""The anchor form of an EMBEDDED tree: locked, and showing the place its MODULE gives it.

plan_2026_11_15_embedded_tree_redraw_through_module, п. 7 (Denis, 15.11): «если дерево встроено
куда-то, пусть берёт координаты из дерева, куда оно встроено, а позиции якоря дерева блокируются
для ввода... Если в main power нет, то сами по себе».

Why lock instead of just showing: an embedded tree's own anchor NO LONGER decides where its content
stands — the module marker does. A form that kept accepting edits would write numbers into the file
that nothing reads, and the numbers it showed (its own shift/angle) would be a different place from
the one the board shows. So every anchor field goes read-only and ONE line says where the numbers
come from — the marker's xy/rotation in its owner, i.e. what the user actually edits.

Only DISPLAY: nothing is written anywhere on this path, and the tree's own anchor stays in the file
byte for byte. When the last marker is removed (Р4 of the same plan) the marker's place is written
into that anchor ONCE, and the form opens again by itself — because `owner_place()` then finds
nothing and `lock_anchor_form()` unlocks.

Two or more markers (п. 5): the fields stay locked and the line names every marker, because which
place to take is exactly what is unknown.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from PyQt6.QtWidgets import QLabel

from kicadstamp.i18n import _

from .trees_embedding import Placement, module_nodes, placements, several_markers_line

logger = logging.getLogger(__name__)

__all__ = [
    "OwnerPlace",
    "lock_anchor_form",
    "owner_place",
    "owner_place_line",
    "owner_place_refusal",
]


@dataclasses.dataclass(frozen=True)
class OwnerPlace:
    """Where the module puts a tree: every marker that places it, plus the FIRST marker's xy and
    rotation in its owner (the numbers the user edits in the OWNER's node form)."""

    tree: str
    markers: list
    marker_xy: Optional[tuple]
    marker_rotation: float

    @property
    def owner(self) -> str:
        return self.markers[0].owner

    @property
    def marker(self) -> str:
        return self.markers[0].marker

    @property
    def ambiguous(self) -> bool:
        return len(self.markers) > 1


def owner_place(trees, tree) -> Optional[OwnerPlace]:
    """`OwnerPlace` when a module places `tree`, None when nobody does (then the tree's own anchor
    is the truth and the form stays editable).

    The marker's own pose is read from the OWNER's node structure — the same numbers the marker's
    node form shows there, which is the whole point of the line: it names where to go and edit."""
    found = placements(trees, tree)
    if not found:
        return None
    first: Placement = found[0]
    owner = next((t for t in (trees or ()) if t.name == first.owner), None)
    marker_xy, marker_rotation = None, 0.0
    if owner is not None:
        for node in module_nodes(owner):
            if node.ref == first.marker:
                marker_xy = node.xy
                marker_rotation = node.rotation
                break
    return OwnerPlace(tree=tree.name, markers=found, marker_xy=marker_xy,
                      marker_rotation=marker_rotation)


def owner_place_line(place: OwnerPlace) -> str:
    """THE line the locked form shows (п. 7): the module, its owner, and the marker's own numbers.

    Two or more markers have no single place to name, so that case keeps the refusal wording —
    the form is locked either way."""
    if place.ambiguous:
        return several_markers_line(place.tree, place.markers)
    if place.marker_xy is None:
        return _("placed by module {marker!r} in {owner!r} — edit that marker there").format(
            marker=place.marker, owner=place.owner)
    x, y = place.marker_xy
    return _(
        "placed by module {marker!r} in {owner!r}: xy ({x:.3f}, {y:.3f}), rotation {rot:.1f}° — "
        "edit that marker there").format(
        marker=place.marker, owner=place.owner, x=x, y=y, rot=place.marker_rotation)


def _line_label(form) -> QLabel:
    """The form's ONE owner-place line, created on first use and kept on the form.

    Created HERE, not in the form, so the 6900-line widget does not grow for a feature it only
    delegates; inserted right under the picker rows so it reads as a reason for the greyed fields
    above it."""
    label = getattr(form, "_owner_place_label", None)
    if label is None:
        label = QLabel("")
        label.setWordWrap(True)
        layout = form.layout()
        if layout is not None:
            layout.insertWidget(1, label)
        form._owner_place_label = label
    return label


def lock_anchor_form(form, trees) -> Optional[OwnerPlace]:
    """Lock (or unlock) an `AnchorFormWidget` for the tree it edits; returns the place it locked to.

    Locked: every anchor field — mode, record/role/point/self rows, shift, tree settings, the
    "Read position" and "Show tree markers" buttons — is disabled and one line names the module.
    `Apply` is the caller's business (`apply_owner_place` refuses too), because the form's own
    buttons belong to the embedding context.

    Unlocked (marker gone): everything comes back and the line disappears. The one caller is
    `_load_settings`, so a reused form follows the buffer without an extra signal."""
    tree = getattr(form, "_tree", None)
    place = owner_place(trees, tree) if tree is not None else None
    was_locked = getattr(form, "_owner_place", None) is not None
    form._owner_place = place
    label = _line_label(form)
    if place is None:
        # Unlock only what THIS lock disabled: re-enabling a field the form itself greys out
        # ("the anchor does not resolve live") would hand back an editable field whose numbers
        # still mean something else — `_reload_settings_display` recomputes that rule.
        if was_locked:
            for widget in (form.mode_combo, form.record_row, form.role_row, form.point_row,
                           form.self_row, form.read_anchor_button,
                           form.show_markers_button):
                widget.setEnabled(True)
            form._reload_settings_display()
        label.setText("")
        label.setVisible(False)
        return None
    text = owner_place_line(place)
    for widget in (form.mode_combo, form.record_row, form.role_row, form.point_row,
                   form.self_row, form.read_anchor_button, form.show_markers_button):
        widget.setEnabled(False)
    form._set_shift_editable(False, reason=text)
    form._set_settings_editable(False, reason=text)
    label.setText(text)
    label.setVisible(True)
    return place


def owner_place_refusal(form) -> Optional[str]:
    """The reason `build_anchor()` must return NOTHING, or None when the form owns its anchor.

    The widgets are already disabled, but a disabled widget still answers `currentText()` and a
    programmatic write (`apply_and_redraw`, a test, a future caller) could reach the form anyway:
    this is the guard that keeps "the tree's own anchor is not what places it" true on the WRITE
    path too. It is wired into `build_anchor()` — the ONE place the form turns itself into a
    `TreeAnchor` — so every apply path is covered without a second rule in `apply()`."""
    place = getattr(form, "_owner_place", None)
    if place is None:
        return None
    if place.ambiguous:
        return several_markers_line(place.tree, place.markers)
    return _(
        "Tree {tree!r} is placed by module {marker!r} in {owner!r} — its own anchor is not what "
        "places it, so nothing is written; edit that marker in {owner!r} instead.").format(
        tree=place.tree, marker=place.marker, owner=place.owner)

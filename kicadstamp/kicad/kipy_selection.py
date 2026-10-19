# kicadstamp/kicad/kipy_selection.py
"""The selection seam over kipy (Ч2 of plan_2026_10_15_reread_copper_uuid_and_selection_any).

Sits next to ``kipy_footprint_safety.py`` and ``pynng_safety.py``: it exists because of what
kipy does NOT do, and it is the ONE place allowed to reach into kipy for it.

WHY THE SELECTION IS READ RAW. ``Board.get_selection()`` unwraps EVERY
``google.protobuf.Any`` it receives, and ``kipy.util.proto.unpack_any`` raises
``ValueError("Can't unpack empty Any protobuf message")`` when the ``type_url`` is empty.
KiCad returns exactly that for every DRC marker (``PCB_MARKER``) in the selection: selectable
in the editor, but not serialisable over IPC, and absent from ``get_selection_as_string()``
as well. One such marker used to take down the whole call, which the GUI's 400 ms selection
tick read as "lost connection" (Denis, live 2026-10-15: selecting the DRC-marked parts of a
board dropped the connection until the selection was cleared). Here such an item is SKIPPED,
never fatal; a ``type_url`` kipy has no mapping for (a KiCad newer than the kipy it was
generated against) is skipped the same way.

WHAT IS AND IS NOT PRIVATE. ``Board.client`` is public in kipy; ``Board._to_concrete_items``
is the very conversion ``Board.get_selection`` applies to its own unwrapped items and is
reached here as a documented seam — alongside ``close()``'s private reach into
``KiCadClient._conn`` (see docs/kicad.md). Nothing outside ``kicadstamp/kicad/`` imports kipy.

WHO LOGS. The counts are logged ONLY when they change: the GUI polls the selection every
~400 ms, and an unconditional line buries everything worth reading (the 2026-08-06 lesson for
``_last_selection_log_count``, applied here to the skipped count as well).

``selected_items(adapter)`` is the function the adapter calls; it keeps the adapter's
``ignore_selection`` short-circuit, its group expansion and its two log-counters, all of which
are adapter state.
"""
import logging
from typing import Any

from kipy.board_types import Group, unwrap as kipy_unwrap
from kipy.proto.common.commands.editor_commands_pb2 import GetSelection, SelectionResponse

from ..domain.board import board_item_from_kipy
from ..i18n import _

logger = logging.getLogger(__name__)


def read_raw_selection(board) -> tuple[list[Any], int]:
    """The editor's selection unwrapped ONE item at a time, plus how many
    items had to be skipped.

    See the module docstring for why ``Board.get_selection()`` cannot be used
    directly (an empty ``Any`` is what every DRC marker arrives as).

    Returns ``(items in the order KiCad reported them, skipped count)``.
    Groups stay kipy ``Group`` wrappers, exactly as ``get_selection()``
    returns them, so the expansion in ``selected_items`` is unchanged.
    """
    command = GetSelection()
    command.header.document.CopyFrom(board.document)
    response = board.client.send(command, SelectionResponse)
    items: list[Any] = []
    skipped = 0
    for raw_item in response.items:
        if not raw_item.type_url:
            # A DRC marker: selected in the editor, but KiCad sends an
            # empty Any for it (see the module docstring). Nothing to unpack.
            skipped += 1
            continue
        try:
            items.append(kipy_unwrap(raw_item))
        except NotImplementedError:
            # KiCad knows a type this kipy was not generated against —
            # also not a reason to lose the rest of the selection.
            logger.debug(_("Selected item of unknown type skipped: {url}").format(
                url=raw_item.type_url))
            skipped += 1
    return board._to_concrete_items(items), skipped


def _expand_groups(raw_selection, get_footprints, get_vias) -> list[Any]:
    """Group's .items property as received from the server is ALWAYS EMPTY
    (just a local cache wrapper); actual group members are in .proto.items
    (list of KIID). Expand groups into their real members by matching IDs
    against all footprints/vias on the board.

    ``get_footprints``/``get_vias`` are the adapter's own (cached) readers,
    passed in so this module owns the expansion without owning the caches."""
    direct_items = [board_item_from_kipy(item) for item in raw_selection
                    if not isinstance(item, Group)]
    group_uuids = set()
    for item in raw_selection:
        if isinstance(item, Group):
            for kiid in item.proto.items:
                group_uuids.add(str(kiid.value))

    if group_uuids:
        for fp in get_footprints():
            if fp.uuid in group_uuids:
                direct_items.append(fp)
        for via in get_vias():
            if via.uuid in group_uuids:
                direct_items.append(via)
    return direct_items


def selected_items(adapter) -> list[Any]:
    """The PCB editor's current selection, ready for the adapter's callers.

    Keeps the three adapter-owned pieces of state/behaviour:
    ``ignore_selection`` (a deliberate "nothing selected" run), the group
    expansion over the adapter's cached footprint/via readers, and the two
    log counters that make the 400 ms poll silent on an unchanged selection."""
    if adapter.ignore_selection:
        return []
    raw_selection, skipped = adapter._read_raw_selection()
    direct_items = _expand_groups(raw_selection, adapter.get_footprints, adapter.get_vias)

    if len(direct_items) != getattr(adapter, "_last_selection_log_count", None):
        logger.debug(_("Selected items (including groups expanded): {count}").format(
            count=len(direct_items)))
        adapter._last_selection_log_count = len(direct_items)
    if skipped != getattr(adapter, "_last_skipped_selection_count", None):
        adapter._last_skipped_selection_count = skipped
        if skipped:
            logger.info(_("Skipped {count} selected item(s) KiCad does not expose over IPC").format(
                count=skipped))
        else:
            logger.debug(_("The whole selection is readable over IPC again"))
    return direct_items

# kicadstamp/diagnostics/probe_2026_10_14_drag_position_update.py
"""Does the BASE drag handler update `dropIndicatorPosition()` for SYNTHETIC drag
events (no QDrag, no mouse) in this PyQt6 build?

The question the Ч3 acceptance (plan_2026_10_14_tree_group_move_and_mount_rebase)
left open: the fix is "call `super().dragMoveEvent(event)` first", but a CELL has
to prove it — and the acceptance notes that a synthetic event without
`source()` may be refused by the base in `InternalMove` mode, in which case no
honest cell exists and a structural one is the fallback.

This probe measures it on a BARE QTreeWidget (no dock, no overrides) so what it
reports is the base class' own behaviour, not ours.

Run:
    QT_QPA_PLATFORM=offscreen .venv/bin/python \
        kicadstamp/diagnostics/probe_2026_10_14_drag_position_update.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QPoint, Qt          # noqa: E402
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent  # noqa: E402
from PyQt6.QtWidgets import (QAbstractItemView, QApplication,  # noqa: E402
                             QTreeWidget, QTreeWidgetItem)


def _event(kind, pos, mime):
    """One synthetic drag event at `pos` — the SAME shape for enter and move."""
    return kind(pos, Qt.DropAction.MoveAction, mime,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def main() -> None:
    app = QApplication(sys.argv)
    widget = QTreeWidget()
    widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    widget.setDragEnabled(True)
    widget.setAcceptDrops(True)
    widget.setDropIndicatorShown(True)
    root = widget.invisibleRootItem()
    rows = []
    for name in ("A", "B", "C"):
        item = QTreeWidgetItem(root)
        item.setText(0, name)
        rows.append(item)
    widget.resize(240, 200)
    widget.show()
    app.processEvents()

    in_row = widget.visualItemRect(rows[0]).center()
    empty = QPoint(in_row.x(), widget.viewport().height() - 2)
    mime = QMimeData()

    print("source() of a synthetic event:",
          _event(QDragMoveEvent, in_row, mime).source())
    print("default:", widget.dropIndicatorPosition())
    widget.dragEnterEvent(_event(QDragEnterEvent, in_row, mime))
    print("after dragEnter over row A:", widget.dropIndicatorPosition())
    widget.dragMoveEvent(_event(QDragMoveEvent, empty, mime))
    print("after dragMove over the EMPTY space:", widget.dropIndicatorPosition())
    widget.dragMoveEvent(_event(QDragMoveEvent, in_row, mime))
    print("after dragMove back over row A:", widget.dropIndicatorPosition())


if __name__ == "__main__":
    main()

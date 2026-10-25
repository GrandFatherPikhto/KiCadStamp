# kicadstamp/diagnostics/probe_take_children_current_2026_10_25.py
"""Probe (Д-4.4 of the Ч2а acceptance): what happens to the CURRENT item when
`QTreeWidgetItem.takeChildren()` deletes the current CHILD?

The question decides whether `gui/docks/checks.py::_rebuild_dedupe_groups` needs its
explicit `self.dedupe.show_row()` after `setCurrentItem(row)`:

  * if Qt moves the current item to the PARENT and emits `currentItemChanged`, the
    QView already follows that signal and the explicit call is dead code;
  * if Qt says nothing, the panel can keep showing a group that no longer exists —
    then the call is the only thing that puts the row's page back.

Needs a QApplication and nothing else (no board, no KiCad, no project).

It lives in the PACKAGE (not in the gitignored `diagnostics/`) because
`gui/docks/checks.py::_rebuild_dedupe_groups` cites it as the measurement behind
"the dead `show_row()` call was dead".

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m \\
       kicadstamp.diagnostics.probe_take_children_current_2026_10_25
"""
import sys

from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    tree = QTreeWidget()
    tree.setColumnCount(1)
    row = QTreeWidgetItem(["row"])
    tree.addTopLevelItem(row)
    row.addChild(QTreeWidgetItem(["child A"]))
    row.addChild(QTreeWidgetItem(["child B"]))
    row.setExpanded(True)

    events: list = []

    def note(current, previous):
        events.append((None if current is None else current.text(0),
                       None if previous is None else previous.text(0)))

    tree.currentItemChanged.connect(note)

    tree.setCurrentItem(row.child(1))
    print("selecting child B ->", events)

    events.clear()
    row.takeChildren()
    print("takeChildren()    ->", events)
    current = tree.currentItem()
    print("current is the ROW:", current is row)
    print("children left:", row.childCount())
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

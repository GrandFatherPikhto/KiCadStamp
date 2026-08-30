# Hotkeys

All keyboard shortcuts in the KiCadStamp GUI. This list is meant to grow —
see [docs/gui.md](gui.md) for the full dock/feature documentation.

Hotkeys are **rebindable**: open the **Settings** tab of the Detail dock and use the
**Hotkeys** group (one key-sequence editor per action). Overrides are stored in
`gui_state.json["hotkeys"]` as `{action_id: shortcut}`; an absent entry means the code default
below.

| Hotkey | Where it acts | What it does |
|--------|---------------|--------------|
| `F2` | Config tree (any leaf) | **Rename** — the same as the context menu's "Rename...". |
| `Ctrl+O` | App-wide (Project dock / File menu) | **Open Root file...** — pick the project's root config. |
| `Ctrl+N` | App-wide (Project dock / File menu) | **New Root file...** — create a new root config. |
| `Ctrl+S` | App-wide (Project dock / File menu) | **Save** — write the Project tab's root settings. |
| `Ctrl+Shift+A` | Project dock | **Add...** — add schematic file(s) to the root's `schematic_files`. |
| `Ctrl+Shift+R` | Project dock | **Remove** — remove the selected schematic file. |

The app also has a **File** menu (`&File`) in the menu bar, built by FUNCTION rather than per
dock: **Open**/**New** reuse the Project dock's own actions (one action = button + hotkey + menu
entry — see `gui/hotkeys.py`), **Recent** is a submenu of the same `recent_root_files` the Project
dock's Recent combo reads, **Close** is a new operation that closes the current project (guarded by
an unsaved-changes prompt when the Project tab has uncommitted edits), and **&Quit** quits. The
**View** menu (2026-08-27) lists one checkable entry per top-level dock so a closed dock can be
brought back without restarting.

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
| `Ctrl+N` | App-wide (Project dock / File menu) | **Create Project...** — type a project NAME and pick a FOLDER; `<folder>/<name>/<name>.sexp` and the project's five infrastructure directories are created automatically (since 2026-09-24). |
| `Ctrl+S` | App-wide (File menu) | **Save** — commit all staged config changes of the whole project to disk (see the "Save model" section in [docs/gui.md](gui.md)). |
| `Ctrl+Shift+R` | Project dock | **Reload schematic sheets** — re-walk the project hierarchy and rewrite the root's `schematic_files`. |

The app also has a **File** menu (`&File`) in the menu bar, built by FUNCTION rather than per
dock: **&Project...** opens the Project dialog, **Create Project...** is the Project dock's own
action reused here (one action = button + hotkey + menu entry — see `gui/hotkeys.py`), **Save**
(`Ctrl+S`) commits the whole project's staged config
changes, **Discard unsaved changes...** drops them and reloads every dock from disk, **Recent
project** is a
submenu of the same `recent_root_files` the Project dock's Recent combo reads (it is rebuilt every
time the submenu opens, and carries one disabled row while the list is empty), **Close** is a new
operation that closes the current project (guarded by an unsaved-changes prompt when the working set
is dirty), and **&Quit** quits. The **View** menu (2026-08-27) lists one checkable entry per
top-level dock so a closed dock can be brought back without restarting.

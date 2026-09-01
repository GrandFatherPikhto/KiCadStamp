# gui/hotkeys.py
"""
QAction-based hotkey infrastructure (2026-08-30, plan
techdocs/handoff/deepseek/plan_2026_08_30_dock_toolbars_menus_hotkeys.md,
Этап 1).

A dock button that should get a hotkey gets a PARALLEL QAction (label, default
QKeySequence, callback — the same slot the button already calls) alongside its
existing `QPushButton.clicked.connect(...)` (see gui/docks/root_metadata.py,
the Этап-1 pilot dock). The button itself is left untouched — this step only
ADDS the QAction + shortcut, it does not remove or rewire buttons. (PyQt6.11
has NO QPushButton.setDefaultAction, so a button cannot simply adopt the
action; keeping the button's own clicked connection is the plan-sanctioned
"дублировать вызов callback'а" — one QAction still serves the hotkey and,
from Этап 1b, the File-menu entry.)

One QAction is the SINGLE source for the three presentations the plan's Этап 2
wants — hotkey, dock-local menu entry, toolbar entry: the same action can be
added to a menu/toolbar AND carry a shortcut, so there are never three
separate entities to keep in sync.

`action_id` is a STABLE string per dock+button (e.g. "root_metadata.save"),
deliberately NOT derived from the label text: i18n changes the text, the id
must stay stable — it is the key under which the user's per-action override is
stored in gui_state.json["hotkeys"], and the key the Settings tab's
reassignment UI (ConfiguratorDock, gui/docks/configurator.py) lists.

Storage (the same flat gui/settings.py JSON as everything else):
    "hotkeys": {action_id: shortcut_string, ...}
An absent entry == the default from code (plan: "отсутствие записи = дефолт из
кода"). set_shortcut() writes the override and re-applies it to the live
QAction immediately; an empty sequence removes the override (back to default).
"""
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QWidget

from . import settings

# action_id -> (human-readable label, default shortcut string). The label is
# stored here too (not only on the QAction) so the Settings tab can list the
# actions without needing a live dock instance.
HOTKEY_ACTIONS: Dict[str, Tuple[str, str]] = {}
# action_id -> the live QAction currently registered. Only one real instance
# of a given dock exists in a running app, so "last built wins" is fine;
# tests that build several docks just re-register the same ids.
_LIVE_ACTIONS: Dict[str, QAction] = {}


def override_for(action_id: str) -> Optional[str]:
    """The user's stored override for `action_id`, if any (absent entry ==
    use the code default)."""
    return (settings.state.get("hotkeys") or {}).get(action_id)


def default_for(action_id: str) -> str:
    return HOTKEY_ACTIONS.get(action_id, ("", ""))[1]


def get_shortcut(action_id: str) -> QKeySequence:
    """The effective shortcut for `action_id`: the user's override from
    gui_state.json when present, else the code default."""
    override = override_for(action_id)
    return QKeySequence(override) if override else QKeySequence(default_for(action_id))


def build_action(parent: QWidget, action_id: str, label: str,
                 default_shortcut: str,
                 callback: Optional[Callable[[], None]]) -> QAction:
    """Create a QAction with the dock/button's stable `action_id`, register it,
    apply the effective shortcut (override from gui_state.json or
    `default_shortcut`) and wire `callback` to triggered.

    `parent` is BOTH the action's owner AND the widget the action is added to
    (parent.addAction — this is what actually makes the shortcut active). Pass
    the main window for app-wide hotkeys that must work regardless of which
    dock tab is currently visible (a stack page hidden behind another tab has
    no active widget-level shortcuts of its own).
    """
    action = QAction(label, parent)
    action.setObjectName(action_id)
    HOTKEY_ACTIONS[action_id] = (label, default_shortcut)
    _LIVE_ACTIONS[action_id] = action
    action.setShortcut(get_shortcut(action_id))
    if callback is not None:
        action.triggered.connect(callback)
    parent.addAction(action)
    return action


def set_shortcut(action_id: str, shortcut_text: str) -> None:
    """Persist a user override for `action_id` ("" clears it, back to the
    code default — absent entry == default, see plan) and re-apply it to the
    live QAction, if any, right away.

    The live re-apply is defensive about a deleted QAction: the module-global
    registry can hold a stale reference after its owning window was torn down
    (multiple MainWindows built in one process — e.g. the test suite — or a
    future window rebuild). The override is persisted regardless; it applies
    to the next live action that registers this action_id."""
    hotkeys = dict(settings.state.get("hotkeys") or {})
    if shortcut_text:
        hotkeys[action_id] = shortcut_text
    else:
        hotkeys.pop(action_id, None)
    settings.state.set("hotkeys", hotkeys)
    action = _LIVE_ACTIONS.get(action_id)
    if action is not None:
        try:
            action.setShortcut(get_shortcut(action_id))
        except RuntimeError:
            # The wrapped C++ QAction was deleted — drop the stale reference
            # so the next build_action(action_id) starts clean.
            _LIVE_ACTIONS.pop(action_id, None)


def registered_hotkeys() -> List[Tuple[str, str, str]]:
    """Every registered hotkey as (action_id, label, default_shortcut),
    sorted by action_id — the Settings tab (ConfiguratorDock) lists these for
    reassignment."""
    return sorted((action_id, label, default)
                  for action_id, (label, default) in HOTKEY_ACTIONS.items())

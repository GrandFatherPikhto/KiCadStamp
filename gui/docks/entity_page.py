# gui/docks/entity_page.py
"""Compatibility shim — the entity page MOVED to gui/entity/page.py
(step 1 of plan_2026_10_09_entity_page; the class was renamed EntityInfoDock ->
EntityPage there).

Kept ONLY so the existing importers — gui/dock_hub.py, the diagnostics probe and
the guards — keep working unchanged until step 5 (the dropdown sweep) removes
the last of them. It is one alias, never a second implementation: a duplicate
would drift. The whitelist guard of tests/imprint/test_imprint_rename.py still
names THIS path until step 5, which is why the legacy-key note lives in
gui/entity/page.py without the legacy spelling.
"""
from gui.entity.page import EntityPage

# The pre-move name — the SAME class object, so `EntityInfoDock is EntityPage`.
EntityInfoDock = EntityPage

__all__ = ["EntityInfoDock", "EntityPage"]

#!/usr/bin/env python3
# tests/gui/test_read_only_verdict_paths.py
"""Ч2 (plan_2026_10_15_extract_and_read_paths_cleanup): EVERY reading path of
the Ч0 census refuses with the HONEST footer.

The property, one sentence: a path that only READS the board must never answer
with "Placement stopped, board not modified. Fix the config and run again." —
nothing was placed, and the thing to fix is usually the board or the schematic
(a duplicated role, a missing Role, a mirrored component), not the config. The
honest counterpart already existed since 2026-10-13 (`read_only_verdict()` /
`format_read_fatal`, kicadstamp/exceptions.py) and was wired into
gui/docks/live_position.py only; Ч2 wires the rest.

Rows = the census, ONE per reading path (not per call site — the wording of
each path is pinned by its own module's tests):

  * Extract from the selection    — template_extraction.py (4 sites)
  * Extract origin resolution     — template_selection.py (5)
  * net-trace Extract             — net_trace_extract.py (2)
  * Select cell / Fill from selection — gui/cell_identification.py (8)
  * Place marker / Read position  — gui/entity/anchor_workers.py (8)
  * the Refs tab's Take selection — gui/docks/cell_refs_tab.py (1)
  * Reread current position       — gui/docks/trees_dock.py (2, verdict from
                                    the caller — see the two cells below)
  * Record an imprint             — kicadstamp/imprint_capture.py (2)

NOT touched by Ч2, named so the omission is visible: gui/board_overlay.py (2
sites — a WRITE path, the overlay layer is drawn/erased there) and
gui/docks/entity_export.py (1 site — not about the board at all: a profile
import/export in config format 3).

Two further cells pin the CALLER-SUPPLIED half of the rule for the one shared
helper (`gui/docks/trees_dock.py:_resolve_live_offset`): the two READING
callers pass read_only_verdict(), the two WRITE-side callers (the rehang read
and trees_mount_rebase) pass nothing and keep the historical footer.

And ONE cell states the LAW over the whole code of those six modules plus
imprint_capture.py (dodelka Д-1 of the acceptance): no `format_fatal_error(`
call may survive there without a read-only verdict. The per-path rows above
guard one refusal each — a single call site reverted inside a module they
already cover stays green there, which is exactly how three of Denis's
mutations survived the first submission. The law cell scans EVERY call, so
it cannot be satisfied by having chosen a lucky representative.
"""
import ast
import logging
import pathlib
from types import SimpleNamespace

import pytest

import gui.docks.trees_dock as trees_mod
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError, read_only_verdict
from kicadstamp.i18n import _

#: The footer a WRITE path legitimately keeps (byte for byte, exceptions.py).
ORDINARY_VERDICT = _("Placement stopped, board not modified. "
                     "Fix the config and run again.")
#: The honest footer of a read-only refusal (rule 4, 2026-10-13).
READ_ONLY_VERDICT = read_only_verdict()


def _refusal(call) -> str:
    with pytest.raises(ValidationError) as ei:
        call()
    return str(ei.value)


# ── One factory per reading path ────────────────────────────────────────────

def _extract_from_the_selection(_monkeypatch):
    """template_extraction.extract_template_from_selection — an EMPTY selection
    is the first refusal of the extract (nothing to extract)."""
    from kicadstamp.template_extraction import extract_template_from_selection
    return _refusal(lambda: extract_template_from_selection(object(), "cell", items=[]))


def _extract_origin_resolution(_monkeypatch):
    """template_selection._find_origin — an origin asked for by a net that the
    selection does not contain."""
    from kicadstamp.template_selection import _find_origin
    return _refusal(lambda: _find_origin(
        [], [], "NO_SUCH_NET", None, None, object(), None, None, {}))


def _net_trace_extract(monkeypatch):
    """net_trace_extract.extract_net_trace — the anchor resolves, the net has
    no copper. The role resolver is stubbed: its OWN refusals are shared with
    the placement paths and are verdict-from-caller territory (unrelated)."""
    import kicadstamp.net_trace_extract as mod
    fp = SimpleNamespace(ref="R1", position=Vector2(0, 0), angle_deg=0.0)
    monkeypatch.setattr(mod, "resolve_footprint_by_role", lambda *a, **k: fp)
    adapter = SimpleNamespace(get_tracks=lambda: [], get_vias=lambda: [])
    return _refusal(lambda: mod.extract_net_trace(adapter, net="N", anchor_role="R"))


def _cell_identification(_monkeypatch):
    """gui/cell_identification.py — Select cell / Fill from selection with an
    empty selection (the FIRST refusal of the identification cascade: the
    cluster membership never even comes into play)."""
    from gui.cell_identification import identify_cell_instance
    cell = SimpleNamespace(name="c", components=[SimpleNamespace(role="R")])
    return _refusal(lambda: identify_cell_instance(cell, [], set()))


def _anchor_workers(_monkeypatch):
    """gui/entity/anchor_workers.py — "Place marker" / "Read position" with
    nothing selected on the board."""
    from gui.entity.anchor_workers import classify_selection_anchor
    return _refusal(lambda: classify_selection_anchor(object(), [], "Place marker"))


def _refs_tab_selection(_monkeypatch):
    """gui/docks/cell_refs_tab.py — the Refs tab's "Take selection" worker with
    an empty board selection."""
    from gui.docks.cell_refs_tab import read_selection_rows_worker
    adapter = SimpleNamespace(refresh_board=lambda: None,
                              get_selected_items=lambda: [])
    return _refusal(lambda: read_selection_rows_worker({"adapter": adapter}))


def _reread_current_position(monkeypatch):
    """gui/docks/trees_dock.py:_resolve_live_offset — the MIRRORED-base refusal
    a "Reread current position" / "Read current position" can hit, with the
    verdict its reading callers pass.

    The config probe is stubbed out too: the raise under test happens right
    after the BASE resolve, so no record and no board is needed — the helper
    is asked about a child whose base is mirrored."""
    monkeypatch.setattr(trees_mod, "_resolve_probe_ref",
                        lambda _cfg, _ref, _kind: (None, False))
    monkeypatch.setattr(trees_mod, "_resolve_node_base_pose",
                        lambda *a, **k: (Vector2(0, 0), 0.0, True))
    return _refusal(lambda: trees_mod._resolve_live_offset(
        None, None, {}, SimpleNamespace(name="t"), None, "CHILD", "placement",
        verdict=read_only_verdict()))


def _imprint_capture(_monkeypatch):
    """kicadstamp/imprint_capture.py — Record with a ref that is not on the
    board."""
    from kicadstamp.imprint_capture import capture_imprint
    adapter = SimpleNamespace(get_footprints=lambda: [])
    return _refusal(lambda: capture_imprint("amp", ["ZZ9"], adapter=adapter))


READING_PATHS = [
    ("extract from the selection", _extract_from_the_selection),
    ("extract origin resolution", _extract_origin_resolution),
    ("net-trace extract", _net_trace_extract),
    ("select cell / fill from selection", _cell_identification),
    ("place marker / read position", _anchor_workers),
    ("refs tab take selection", _refs_tab_selection),
    ("reread current position", _reread_current_position),
    ("record an imprint", _imprint_capture),
]


@pytest.mark.parametrize("label, factory", READING_PATHS,
                         ids=[label for label, _ in READING_PATHS])
def test_a_read_path_refuses_with_the_read_only_footer(label, factory, monkeypatch):
    """The ONE property of Ч2, per reading path: no "Fix the config", the
    read-only line present. The reason text itself is NOT asserted here — each
    path's own module tests own its wording; Ч2 changed ONLY the last line."""
    text = factory(monkeypatch)

    assert READ_ONLY_VERDICT in text, f"{label}: the read-only footer is missing"
    assert ORDINARY_VERDICT not in text, (
        f"{label}: the false 'Placement stopped … Fix the config' footer is back "
        "on a path that places nothing")


# ── The shared helper: verdict from the caller ─────────────────────────────

def test_the_write_side_caller_keeps_the_ordinary_footer(monkeypatch):
    """_resolve_live_offset's default (verdict=None) is the historical footer
    byte for byte — the Redraw/rehang half of the same helper is untouchable by
    Ч2: there a config edit really is being prepared."""
    monkeypatch.setattr(trees_mod, "_resolve_probe_ref",
                        lambda _cfg, _ref, _kind: (None, False))
    monkeypatch.setattr(trees_mod, "_resolve_node_base_pose",
                        lambda *a, **k: (Vector2(0, 0), 0.0, True))

    text = _refusal(lambda: trees_mod._resolve_live_offset(
        None, None, {}, SimpleNamespace(name="t"), None, "CHILD", "placement"))

    assert ORDINARY_VERDICT in text
    assert READ_ONLY_VERDICT not in text, (
        "a write-side caller must not inherit the read-only footer")


#: The reading modules of the Ч0 census — the scope of the law cell below, one
#: entry per PATH (several call sites each; the law covers all of them).
READING_MODULES = {
    "kicadstamp/template_extraction.py": "Extract from the selection (4 calls)",
    "kicadstamp/template_selection.py": "Extract origin resolution (5 calls)",
    "kicadstamp/net_trace_extract.py": "net-trace Extract (2 calls)",
    "gui/cell_identification.py": "Select cell / Fill from selection (8 calls)",
    "gui/entity/anchor_workers.py": "Place marker / Read position (8 calls)",
    "gui/docks/cell_refs_tab.py": "Refs tab Take selection (1 call)",
    "kicadstamp/imprint_capture.py": "Record an imprint (2 calls)",
}

#: (module, innermost function) -> WHY that one call may keep the ordinary
#: footer. EMPTY on purpose: every fatal in these modules is a read refusal, and
#: this list is where a future exception has to be NAMED (with its reason) in the
#: same commit that adds it — never a place to park a call that was never thought
#: about. A key that no longer matches a call is dead weight, not an error.
WRITE_PATH_EXCEPTIONS: dict[tuple[str, str], str] = {}

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: How many read-only refusals the census has to SEE before "no offenders" means
#: anything (30 call sites converted in the six modules + 2 in imprint_capture).
MIN_READ_ONLY_REFUSALS = 30

_FATAL_FORMATTERS = ("format_fatal_error", "format_read_fatal")


def _innermost_owner_calls(source: str, names: tuple[str, ...]):
    """[(innermost owner function, called name, verdict expression | None, line)]
    for every call of a name in `names` — one flat list.

    Parsed, never grepped, and attributed to the INNERMOST enclosing def, so a
    call inside a closure (or a comment mentioning the formatter) is judged where
    it really stands."""
    found: list[tuple[str, str, str | None, int]] = []

    def verdict_of(call: ast.Call) -> str | None:
        for kw in call.keywords:
            if kw.arg == "verdict":
                return ast.unparse(kw.value)
        return None

    def walk(node, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
            elif isinstance(child, ast.Call):
                name = (getattr(child.func, "id", None)
                        or getattr(child.func, "attr", None))
                if name in names:
                    found.append((owner, name, verdict_of(child), child.lineno))
                walk(child, owner)
            else:
                walk(child, owner)

    walk(ast.parse(source), "<module>")
    return found


def test_no_reading_module_keeps_a_fatal_without_the_read_only_verdict():
    """THE LAW of Ч2, as a census of the whole code of every reading module:
    none of them may raise a `format_fatal_error` that does not carry
    `read_only_verdict()` — neither directly, nor as `verdict=`. Exceptions live
    in WRITE_PATH_EXCEPTIONS by (module, function) WITH a reason, and there are
    none today.

    This is the Д-1 cell of the acceptance: Denis reverted ONE call site in each
    of template_selection, net_trace_extract and cell_identification, and the
    per-path rows stayed green — they each guard a chosen representative. The
    census sees all 32 refusals, so any reverted site turns it red."""
    offenders: list[str] = []
    read_only = 0
    for rel, label in READING_MODULES.items():
        path = REPO_ROOT / rel
        assert path.exists(), f"{rel} ({label}) moved — the census points nowhere"
        for owner, name, verdict, line in _innermost_owner_calls(
                path.read_text(encoding="utf-8"), _FATAL_FORMATTERS):
            if name == "format_read_fatal" or verdict == "read_only_verdict()":
                read_only += 1
                continue
            if (rel, owner) in WRITE_PATH_EXCEPTIONS:
                continue
            offenders.append(f"{rel}:{line} in {owner}() — {name}(verdict={verdict})")

    assert read_only >= MIN_READ_ONLY_REFUSALS, (
        f"the census saw only {read_only} read-only refusals (expected at least "
        f"{MIN_READ_ONLY_REFUSALS}) — the sweep is vacuous, not clean")
    assert offenders == [], (
        "a READING module keeps a fatal that claims 'Placement stopped, board not "
        "modified. Fix the config and run again.' — nothing was placed there. Use "
        "format_read_fatal, or pass verdict=read_only_verdict(), or name the call "
        "in WRITE_PATH_EXCEPTIONS with its reason:\n  " + "\n  ".join(offenders))


def _tree_offset_calls(source: str):
    """[(INNERMOST owner function, passed verdict expression or None)] for every
    `_resolve_live_offset(...)` CALL in trees_dock.py — parsed, never grepped,
    so a comment can never satisfy the cell below.

    The owner is the INNERMOST enclosing def (the reach read lives in the
    closure `_read_live` inside `_rehang_plan`), the same rule door_lint uses
    for attributing a call."""
    tree = ast.parse(source)
    found: list[tuple[str, str | None]] = []

    def walk(node, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
            elif isinstance(child, ast.Call):
                name = (getattr(child.func, "id", None)
                        or getattr(child.func, "attr", None))
                if name == "_resolve_live_offset":
                    verdict = None
                    for kw in child.keywords:
                        if kw.arg == "verdict":
                            verdict = ast.unparse(kw.value)
                    found.append((owner or "<module>", verdict))
                walk(child, owner)
            else:
                walk(child, owner)

    walk(tree, None)
    return found


def test_every_trees_dock_caller_states_its_verdict_by_role():
    """Decision 2 of the Ч0 review, as a census of the CALL SITES: the two
    READING callers of the shared helper pass read_only_verdict(), the two
    WRITE-side callers (the rehang read inside _rehang_plan and
    trees_mount_rebase, which is in another module) pass nothing.

    A new caller therefore has to CHOOSE — a mutation that drops one of the two
    read_only_verdict() calls, or one that hard-codes the read-only footer
    inside the helper, turns this cell red."""
    source = pathlib.Path(trees_mod.__file__).read_text(encoding="utf-8")
    calls = dict(_tree_offset_calls(source))

    assert calls["run_node_reread_worker"] == "read_only_verdict()", (
        "Reread current position reads — it must pass the read-only verdict")
    assert calls["_on_read_position"] == "read_only_verdict()", (
        "Read current position reads — it must pass the read-only verdict")
    assert calls["_read_live"] is None, (
        "the rehang read feeds a config edit — it keeps the ordinary footer")
    assert len(calls) == 3, (
        f"a new _resolve_live_offset caller appeared and did not choose a "
        f"verdict: {sorted(calls)}")

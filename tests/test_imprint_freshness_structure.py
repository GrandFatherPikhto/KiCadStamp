# tests/test_imprint_freshness_structure.py
"""Structural guards for the imprint-capture freshness fix — К4 and К6 of
plan_2026_09_22_imprint_stale_cache_sh2 (§3, Ш3).

The defect: an imprint capture read the adapter's CACHED footprint list
(``kicadstamp/imprint_capture.py:232``, and the membership read at ``:491``
inside ``build_imprint_diff``), and ``refresh_board()`` was never called on any
of the four GUI worker paths that start a capture — so a record froze at the
positions the cache held. Measured by the Ш1 gate on the test board: warm cache
vs a fresh read = 1.8063 mm on 11 refs, while the warm read cost zero board
reads. The fix is one GUI-side seam plus four call sites; everything below is the
part of the guard set that does not depend on the fix having been made, because
it is structural: it holds for code nobody has written yet.

Scope, deliberately, and named here so an empty cell is visible rather than
accidental:

  * К4 scans ``gui/`` ONLY. The core is excluded AS A MODULE with its reason stated
    below (pure core, А+), never by a hand-written list of names;
  * К6 pins the other side of the same line: the core stays clean, and it stays
    clean by TWO halves — an AST half (a call added through an alias is still a
    call) and a signature half (an added parameter like ``refresh=True`` changes
    CLI/MCP/apply behaviour without ever naming ``refresh_board``).

What К6 does NOT claim: a string-form reach (``getattr(adapter,
"refresh_board")``) is invisible to the AST half, and the signature half says
nothing about the BODY. Neither hole is claimed closed here.

The existing apply / CLI / MCP suites are NOT a cell in this file: they are green
independently of this step, so counting them as coverage this step earned would
be claiming a guard that predates it. They are the acceptance bar (планка), and
they are named as such in the step's report.
"""
import ast
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_CAPTURE_STARTERS = ("capture_imprint", "build_imprint_diff")
_REFRESH_SEAM = "refresh_board_before_live_read"
_CORE_MODULE = "kicadstamp/imprint_capture.py"
# The four worker bodies the fix must cover. Named here for a NON-VACUITY check
# only (see the cell below): a scan that walks zero files and finds zero call
# sites also reports "no offenders", which is a miss, not a kill (deepseek.md
# §38, the second fuse).
_EXPECTED_OWNERS = frozenset({"_run_record_capture", "_run_resource_capture",
                              "_run_reread", "_run_reread_apply"})


def _capture_starters(path: Path) -> list:
    """[(line, owner name, refreshed)] for every ``capture_imprint(...)`` /
    ``build_imprint_diff(...)`` CALL NODE in ``path`` — parsed, never grepped.
    A text scan would mistake the docstring/comment mentions of the same names
    for calls (``gui/docks/imprint.py``'s module docstring does exactly that),
    and it would follow a rename of the enclosing function nowhere."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Name)
                and func.id in _CAPTURE_STARTERS):
            continue
        owner = node
        while (not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
               and owner in parents):
            owner = parents[owner]
        refreshed = any(
            isinstance(sub, ast.Call)
            and ((isinstance(sub.func, ast.Name) and sub.func.id == _REFRESH_SEAM)
                 or (isinstance(sub.func, ast.Attribute)
                     and sub.func.attr == "refresh_board"))
            for sub in ast.walk(owner))
        found.append((node.lineno, getattr(owner, "name", "<module>"), refreshed))
    return found


def test_every_gui_capture_starter_refreshes_the_board_first():
    """К4 of plan_2026_09_22_imprint_stale_cache_sh2 (Ш3.1) — the CLASS cell:
    every function in ``gui/`` that starts a capture (``capture_imprint`` OR
    ``build_imprint_diff``) refreshes the board before it starts.

    This is the cell that catches a FIFTH worker, which is how this defect class
    has come back three times in a row (bbox 10.09 → imprint capture → MCP): each
    round fixed the branch that had been measured, and the next one was found by
    a human. A parametrized list of call sites is written by hand, and forgetting
    to extend a hand-written list is exactly the disease.

    The scope is ``gui/`` and it is NARROWER than "every capture in the repo", on
    purpose: ``kicadstamp/imprint_capture.py`` calls ``capture_imprint`` twice
    from inside ``build_imprint_diff`` itself, and a refresh there would reach
    apply / CLI / MCP — paths that must stay byte-identical (rule А+). The core is
    therefore excluded as a MODULE, not as a list of offending names: anything
    under ``gui/`` is in scope forever, and the reason the core is out lives in
    this docstring rather than in a silently shortened list.

    The seam may be either named form — the shared helper or a direct
    ``adapter.refresh_board()`` — because pinning the helper's NAME would pin the
    spelling, not the property. What is pinned is that SOME refresh happens
    inside the same function that starts the capture.
    """
    all_starters = [(path.relative_to(ROOT).as_posix(), line, owner, refreshed)
                    for path in sorted((ROOT / "gui").rglob("*.py"))
                    for line, owner, refreshed in _capture_starters(path)]

    # Non-vacuity first: a walker that finds nothing would report "no offenders"
    # and look green. Rule 38's second fuse — a verdict with zero findings is a
    # miss until the scan is shown to see the sites it is supposed to see.
    owners = {owner for _, _, owner, _ in all_starters}
    assert _EXPECTED_OWNERS <= owners, (
        "the AST walker did not see the four capture starters it exists for, so "
        "a green result below would mean nothing: "
        f"expected {sorted(_EXPECTED_OWNERS)}, walker saw {sorted(owners)}")

    offenders = [f"{rel}:{line} ({owner})"
                 for rel, line, owner, refreshed in all_starters if not refreshed]

    assert offenders == [], (
        "a capture is started without rebuilding the board first, so it records "
        "what the adapter's cache happens to hold instead of the live board "
        "(warm-vs-fresh measured 1.8063 mm on 11 refs): "
        f"{offenders}")


def test_core_imprint_capture_never_refreshes_the_board():
    """К6a of plan_2026_09_22_imprint_stale_cache_sh2 (Ш3.3) — the core stays
    pure.

    ``kicadstamp/imprint_capture.py`` must never call ``refresh_board``: that
    module is shared by ``apply``, the CLI and the MCP server, and its behaviour
    is frozen (rule А+). The freshness rule belongs to the GUI seam, where a live
    board and an owning socket exist. If the refresh were to move into the core,
    the CLI's reads would change silently — the same defect class, one layer
    down, with a bigger blast radius.

    Checked by AST rather than grep, so an aliased reach (``r = adapter
    .refresh_board; r()``) still registers. NOT claimed: a string-form reach
    (``getattr(adapter, "refresh_board")``) is invisible to this half.
    """
    tree = ast.parse((ROOT / _CORE_MODULE).read_text(encoding="utf-8"),
                     filename=_CORE_MODULE)

    # Non-vacuity: prove the parsed module IS the core module (a typo in the
    # path would otherwise make the scan below pass over an empty file).
    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"capture_imprint", "build_imprint_diff"} <= defined, (
        f"{_CORE_MODULE} does not define the two capture starters — the scan "
        f"below would be vacuous: {sorted(defined)}")

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "refresh_board":
            offenders.append((node.lineno, "attribute"))
        elif isinstance(node, ast.Name) and node.id == "refresh_board":
            offenders.append((node.lineno, "name"))

    assert offenders == [], (
        "the imprint core reaches for refresh_board, which drags a live-board "
        "refresh into apply/CLI/MCP and breaks rule А+ (the GUI seam is the only "
        f"place a refresh may happen): {offenders}")


# The signatures as they stand BEFORE this step (ca5dc2d). Frozen by hand, not
# derived: a test that recomputes the expectation from the code it checks pins
# nothing. An added parameter — `refresh=True` above all, which changes CLI/MCP
# behaviour without naming refresh_board anywhere — shows up here.
_FROZEN_SIGNATURES = {
    "capture_imprint": (
        ("name", "refs", "adapter", "pivot", "source_sheet", "sheet_names",
         "scope_sheet_paths", "scope_presets", "boundary_net_actions"),
        (inspect.Parameter.empty, inspect.Parameter.empty, None, None, None,
         None, None, None, None),
    ),
    "build_imprint_diff": (
        ("stored", "adapter", "scope_refs"),
        (inspect.Parameter.empty, inspect.Parameter.empty, None),
    ),
}


@pytest.mark.parametrize("fn_name", sorted(_FROZEN_SIGNATURES))
def test_core_imprint_signature_is_frozen(fn_name):
    """К6b of plan_2026_09_22_imprint_stale_cache_sh2 (Ш3.3) — the core's
    published surface is unchanged.

    К6a checks that the core does not CALL a refresh; that alone leaves the other
    way in: a new parameter. ``capture_imprint(..., refresh=True)`` would change
    what the CLI, the MCP server and apply do, without a single occurrence of the
    word ``refresh_board`` for an AST cell to catch. Parameter NAMES, their
    defaults and their kinds are therefore pinned here, per function — one test
    row each, so a failure names which signature moved.

    This is a ratchet for THIS step, not a claim that the core may never change:
    a deliberate change to the core is expected to arrive with an edit here, in
    the same commit, which is exactly the review the А+ rule wants.
    """
    from kicadstamp import imprint_capture

    names, defaults = _FROZEN_SIGNATURES[fn_name]
    params = list(inspect.signature(getattr(imprint_capture, fn_name)).parameters.values())

    assert tuple(p.name for p in params) == names, (
        f"{fn_name}: the parameter list of the imprint core moved — apply/CLI/MCP "
        "share this function and must stay byte-identical (rule А+)")
    assert tuple(p.default for p in params) == defaults, (
        f"{fn_name}: a parameter default of the imprint core moved — a new "
        "default (e.g. refresh=True) changes CLI/MCP behaviour silently")
    assert {p.kind for p in params} == {inspect.Parameter.POSITIONAL_OR_KEYWORD}, (
        f"{fn_name}: a parameter changed KIND — keyword-only parameters are how a "
        "new option slips in without touching call sites")

"""Mutation run for the MCP error-contract cells — §8 of
plan_2026_09_25_mcp_error_contract.

Run it by hand from the repository root (it drives pytest, it is not a test):

    .venv/bin/python -m kicadstamp.diagnostics.run_mcp_error_contract_mutations

The cells are Э1–Э6 in tests/test_mcp_error_contract.py. TWO halves of evidence
are required, and the other half is already taken: the cells were SEEN RED on the
base commit (c7a738a) before any production change — Э1[kipy-ConnectionError],
all three Э2 rows, both Э3 rows and Э5's second row, with Э1[builtin], Э4, Э5's
first row and Э6 green there by construction (they exist to kill м5/м6/м7, not to
describe the defect). Measured: 7 failed, 4 passed, which is exactly the colour
list the plan named in advance. This harness is the second half: it proves each
cell still BITES after the fix, by putting the OLD code back.

Machinery, fuses and the reading of a verdict are inherited VERBATIM from
run_mcp_freshness_mutations.py (plan_2026_09_24_mcp_stale_board), itself
inherited from run_imprint_freshness_mutations.py — kept because they protect
against failures that are invisible:

  * ``original.count(old) != 1`` — REFUSE an ambiguous template. м1 and м7 touch
    the SAME assignment (м7 rewrites the whole tail of the function that owns the
    three lines м1 edits), and both files here also carry sibling branches whose
    text is one word away from the anchor (api_error_message vs
    connection_error_message). Without this fuse ``replace(old, new, 1)`` edits a
    foreign line and the mutation reports GREEN: a silent miss is
    indistinguishable from a healthy guard, which is the worst failure of all
    because it soothes instead of shouting (deepseek.md §38);
  * a CONTROL id unrelated to every mutated body runs alongside the expected
    ones. It is a cell of the PREVIOUS entry (the seam rebuilds the board exactly
    once per call): it drives the same two modules end to end and asserts nothing
    about error classes, so it detects a broken module (an import error reddens
    everything) and stays silent about the properties under test. If the control
    goes red the run is reported as NOT A VERDICT, never as a killing;
  * the verdict is read off the EXIT CODE of a run of ONE FULL test id: 0 green,
    1 red, 2 (interrupted/collection error) and 5 ("no tests ran") are NOT
    verdicts;
  * a red on an id we did not expect is a FINDING, printed as one and declared
    below instead of narrowed away.

DECLARED DEVIATIONS FROM THE PLAN'S §8 WORDING — all three measured, none of them
a convenience, each printed by this run as a finding:

  * м3 — the plan expects "Э2 (the AS_BUSY part)" to redden. ALL THREE Э2 rows do,
    because every one of them asserts the api_error_message text (the generic rows
    through "KiCad returned API error: …"), and replacing the formatter with
    str(e) changes each of them. Declared in expect_red rather than narrowed;
  * м1 — Э5's second row reddens as well: it asserts that the tuple really carries
    kipy.errors.ConnectionError, which is the same property one level down.
    Demanding it stay green would be asking that cell to miss the mutation;
  * м5 — the plan expects Э4 to redden, and it does; the three Э2 rows redden with
    it (a catch-all hands them str(e) instead of the project's text) while BOTH Э3
    rows stay green, because a plain ToolError carrying kipy's own words still
    satisfies everything they assert. That asymmetry is the reason the cells are
    shaped the way they are: the "bug vs deliberate fatal" boundary (Э4) is what a
    catch-all breaks, and the connection cells are guarded by their text instead.

Every wait here is explicit (a pytest run at 300 s), so this module needs no entry
in the timeout sweep's exception list, and it builds no adapter of its own — no
live KiCad is touched, no profile is read, nothing is written to a board.
"""
import subprocess
import sys
from pathlib import Path

PY = sys.executable
PYTEST_TIMEOUT_S = 300

CONNECTION = "mcp_server/connection.py"
TOOLS = "mcp_server/tools.py"

_CELLS = "tests/test_mcp_error_contract.py::"
E1_BUILTIN = _CELLS + "test_execute_reconnects_once_after_a_connection_error[builtin-ConnectionError]"
E1_KIPY = _CELLS + "test_execute_reconnects_once_after_a_connection_error[kipy-ConnectionError]"
E2_REFRESH = _CELLS + "test_api_error_reaches_the_client_as_a_clear_tool_error[refresh_board]"
E2_READ = _CELLS + "test_api_error_reaches_the_client_as_a_clear_tool_error[get_footprints]"
E2_BUSY = _CELLS + "test_as_busy_tool_error_carries_the_unfinished_tool_explanation"
E3_FIRST = _CELLS + "test_kicad_unavailable_at_the_first_call_is_a_clear_error"
E3_RECONNECT = _CELLS + "test_kicad_unavailable_during_the_reconnect_is_a_clear_error"
E4 = _CELLS + "test_a_plain_runtime_error_still_reaches_the_client_as_a_crash"
E5_IMPORT = _CELLS + "test_importing_the_seam_does_not_pull_kipy"
E5_TUPLE = _CELLS + "test_the_reconnectable_tuple_holds_the_real_kipy_class"
E6 = _CELLS + "test_the_lazy_import_happens_at_most_once"

# The previous entry's cell: green under every mutation below (none of them
# touches the refresh or the counters), and it exercises both mutated modules
# through build_server + call_tool, so a module that stopped importing reddens it.
CONTROL = "tests/test_mcp_board_freshness.py::test_the_board_is_rebuilt_exactly_once_per_call[1]"


def _repo_root() -> Path:
    """The repository root: the nearest ancestor of this file that carries a
    pyproject.toml.

    WALKED, not counted (`Path(__file__).parent.parent` is right for
    diagnostics/ and WRONG for kicadstamp/diagnostics/), so the subprocess always
    runs pytest where the ids above exist — and, just as important here, so the
    mutations below always edit the worktree these cells are read from, never
    another checkout. Inherited verbatim from the freshness harness: there, a
    wrong root made every mutation come back "green" by absence."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise SystemExit(f"pyproject.toml not found above {here}")


ROOT = _repo_root()

# The tuple as the fix leaves it: three classes, the built-in one first.
RECONNECTABLE_TUPLE = (
    "        _RECONNECTABLE_ERRORS = (\n"
    "            ConnectionError,\n"
    "            _get_kipy_connection_error(),\n"
    "            _get_board_not_found_error(),\n"
    "        )\n"
)

# м1: the shipped defect, put back — the tuple is built from the built-in class
# alone, so a real IPC break (kipy's own, an unrelated hierarchy) flies past.
RECONNECTABLE_TUPLE_WITHOUT_KIPY = (
    "        _RECONNECTABLE_ERRORS = (\n"
    "            ConnectionError,\n"
    "            _get_board_not_found_error(),\n"
    "        )\n"
)

# м7: the cache removed — the tuple is rebuilt (and its imports re-run) per call.
RECONNECTABLE_CACHE = (
    "    global _RECONNECTABLE_ERRORS\n"
    "    if _RECONNECTABLE_ERRORS is None:\n"
    + RECONNECTABLE_TUPLE
    + "    return _RECONNECTABLE_ERRORS\n"
)
RECONNECTABLE_NO_CACHE = (
    "    return (\n"
    "        ConnectionError,\n"
    "        _get_kipy_connection_error(),\n"
    "        _get_board_not_found_error(),\n"
    "    )\n"
)

# м6: §6.1's trap in one line — the module stops being kipy-free at import.
IMPORTS = "import logging\nimport threading\n"
IMPORTS_WITH_EAGER_KIPY = (
    "import logging\n"
    "import threading\n"
    "\n"
    "import kipy  # MUTATION: the eager import §6.1 warns about\n"
)

# The two deliberate branches, and their neighbours (both files carry lines one
# word away from these — that is what the count fuse is for).
API_BRANCH = (
    "            if isinstance(exc, ApiError):\n"
    "                raise ToolError(api_error_message(exc)) from exc\n"
)
CONNECTION_BRANCH = (
    "            if isinstance(exc, KipyConnectionError):\n"
    "                raise ToolError(connection_error_message(exc)) from exc\n"
)
API_LINE = "                raise ToolError(api_error_message(exc)) from exc\n"
API_LINE_WITH_STR = "                raise ToolError(str(exc)) from exc\n"
EVERYTHING_IS_A_TOOL_ERROR = "            raise ToolError(str(exc)) from exc\n"

MUTATIONS = [
    dict(name="м1-no-kipy-class-in-the-reconnect-tuple", rel=CONNECTION,
         old=RECONNECTABLE_TUPLE, new=RECONNECTABLE_TUPLE_WITHOUT_KIPY,
         expect_red=[E1_KIPY, E5_TUPLE],
         expect_green=[E1_BUILTIN, E4, E6],
         note="м1 — the entry's whole defect, put back: the reconnect catches only "
              "the built-in ConnectionError, so kipy's flies past and the call "
              "leaves the seam as a crash. Э5's second row reddens too (declared "
              "in the module docstring): it asserts the same property one level "
              "down, in a subprocess without kipy pre-imported."),
    dict(name="м2-api-error-is-a-crash-again", rel=TOOLS,
         old=API_BRANCH, new="",
         expect_red=[E2_REFRESH, E2_READ, E2_BUSY],
         expect_green=[E1_KIPY, E3_FIRST, E3_RECONNECT, E4, E6],
         note="м2 — step 2 removed: an ApiError is neither PlacerError nor "
              "ValueError, so all three Э2 rows go back to reaching the client as "
              "mcp's crash wrapper (UnexpectedToolError), with the text withheld. "
              "Both Э3 rows stay green: they are about kipy's ConnectionError, "
              "which this mutation leaves alone."),
    dict(name="м3-the-project-s-words-replaced-by-str", rel=TOOLS,
         old=API_LINE, new=API_LINE_WITH_STR,
         expect_red=[E2_REFRESH, E2_READ, E2_BUSY],
         expect_green=[E1_KIPY, E3_FIRST, E3_RECONNECT, E4, E6],
         note="м3 — the text is no longer the project's: str(e) instead of "
              "api_error_message. The plan expects the AS_BUSY part to redden, and "
              "it does — the long \"finish the unfinished tool in KiCad\" sentence "
              "is the most valuable thing the fix carries. All three rows redden "
              "together (declared in the module docstring): each asserts the "
              "formatter's output, so a formatter swap is exactly what they must "
              "catch."),
    dict(name="м4-the-reconnect-failure-is-a-crash-again", rel=TOOLS,
         old=CONNECTION_BRANCH, new="",
         expect_red=[E3_FIRST, E3_RECONNECT],
         expect_green=[E1_KIPY, E2_REFRESH, E2_READ, E2_BUSY, E4, E6],
         note="м4 — step 3 removed, i.e. §6.3's own trap: the tuple is fixed, the "
              "reconnect happens, and then the SECOND failure (KiCad closed in "
              "between) leaves `execute` from inside the except handler and reaches "
              "the client as a crash. Э1 stays green because it is a seam-level "
              "cell: the reconnect itself still works — it is the failure of THAT "
              "reconnect that this mutation unhandles."),
    dict(name="м5-everything-becomes-a-tool-error", rel=TOOLS,
         old=API_BRANCH + CONNECTION_BRANCH + "            raise\n",
         new=EVERYTHING_IS_A_TOOL_ERROR,
         expect_red=[E4, E2_REFRESH, E2_READ, E2_BUSY],
         expect_green=[E1_KIPY, E3_FIRST, E3_RECONNECT, E6],
         note="м5 — the boundary erased: `except Exception` and straight to "
              "ToolError, so a RuntimeError (a bug) is presented to the model as a "
              "user-facing sentence with no traceback in the log. Э4 is the cell "
              "that exists for this, and it reddens. The three Э2 rows redden as "
              "well (declared): a catch-all gives them str(e) instead of the "
              "project's text. Both Э3 rows STAY GREEN — a plain ToolError carrying "
              "kipy's own words satisfies them — which is exactly why Э3's "
              "assertions are written on the text and the wrapper type, and why Э4 "
              "had to exist."),
    dict(name="м6-kipy-imported-at-module-level", rel=CONNECTION,
         old=IMPORTS, new=IMPORTS_WITH_EAGER_KIPY,
         expect_red=[E5_IMPORT],
         expect_green=[E1_KIPY, E3_FIRST, E5_TUPLE, E6],
         note="м6 — §6.1's silent architecture break: a top-level `import kipy` in "
              "the seam. Nothing else notices — kipy IS installed, so every other "
              "cell (and the whole plank) stays green, which is precisely why the "
              "property needed a cell of its own. Э5's SECOND row stays green: the "
              "lazy path still works, it simply is no longer the first to arrive. "
              "Э6 also stays green: its counter watches the lazy imports inside "
              "_reconnectable_errors(), which the eager line does not remove."),
    dict(name="м7-the-tuple-cache-removed", rel=CONNECTION,
         old=RECONNECTABLE_CACHE, new=RECONNECTABLE_NO_CACHE,
         expect_red=[E6],
         expect_green=[E1_KIPY, E1_BUILTIN, E3_FIRST, E5_IMPORT, E5_TUPLE],
         note="м7 — the cache removed: the tuple (and its two lazy imports) is "
              "rebuilt on every call. Behaviour is unchanged, which is why only Э6 "
              "notices — and Э6 counts IMPORTS rather than asserting tuple identity "
              "on purpose (the base commit already returned a fresh tuple each call, "
              "so an identity cell would have been red by its wording, not by the "
              "defect)."),
]


def _run_one(test_id: str) -> tuple:
    """('green' | 'red' | 'no-verdict', one evidence line) for ONE FULL test id."""
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=PYTEST_TIMEOUT_S)
    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [line for line in output.strip().splitlines() if line.strip()]
    evidence = lines[-1] if lines else "<no output>"
    if proc.returncode == 0:
        return "green", evidence
    if proc.returncode == 1 and "FAILED" in output:
        return "red", evidence
    return "no-verdict", f"exit {proc.returncode} | {evidence}"


def main() -> int:
    print(f"repo root: {ROOT}")
    problems = []
    for mutation in MUTATIONS:
        path = ROOT / mutation["rel"]
        original = path.read_text(encoding="utf-8")
        old = mutation["old"]
        count = original.count(old)
        if count != 1:
            print(f"!! {mutation['name']}: the template matches {count} sites in "
                  f"{mutation['rel']} — refusing to run a mutation whose edit is "
                  "ambiguous (it would edit a foreign line and report GREEN, "
                  "deepseek.md §38)")
            return 2
        mutated = original.replace(old, mutation["new"], 1)
        control_id = mutation.get("control", CONTROL)

        path.write_text(mutated, encoding="utf-8")
        try:
            control_status, control_evidence = _run_one(control_id)
            results = [(test_id, *_run_one(test_id))
                       for test_id in mutation["expect_red"]]
            untouched = [(test_id, *_run_one(test_id))
                         for test_id in mutation.get("expect_green", [])]
        finally:
            path.write_text(original, encoding="utf-8")

        print(f"\n=== {mutation['name']}  ({mutation['note']})")
        print(f"    control {control_id.split('::')[-1]}: {control_status.upper()}"
              f" | {control_evidence}")
        if control_status != "green":
            print("    NOT A VERDICT: the control cell did not stay green, so the "
                  "mutation broke the module rather than the behaviour — a red id "
                  "below would prove nothing")
            problems.append(f"{mutation['name']}: control was {control_status}")
            continue

        for test_id, status, evidence in results:
            cell = test_id.split("::")[-1]
            print(f"    {'KILLED' if status == 'red' else status.upper()}: {cell}")
            print(f"        | {evidence}")
            if status != "red":
                problems.append(f"{mutation['name']} -> {cell} ({status})")
        # The mutation must be SURGICAL: a sibling row that guards another
        # property has no business failing here. A sibling that went red would
        # mean the edit touched more than the template named — the silent miss
        # this harness exists to make visible.
        for test_id, status, evidence in untouched:
            cell = test_id.split("::")[-1]
            print(f"    {'untouched (green)' if status == 'green' else 'TOUCHED ' + status.upper()}"
                  f": {cell}")
            print(f"        | {evidence}")
            if status != "green":
                problems.append(f"{mutation['name']} -> {cell} "
                                f"(expected green, got {status})")

    # The revert sits in a `finally` above, but a failure to restore would leave
    # the tree mutated for whatever runs next — so it is verified, not assumed.
    for mutation in MUTATIONS:
        path = ROOT / mutation["rel"]
        if mutation["old"] not in path.read_text(encoding="utf-8"):
            print(f"!! {mutation['rel']} does not carry {mutation['name']}'s anchor "
                  "any more — the tree was NOT restored, check it by hand")
            return 2

    print("\n" + "=" * 72)
    if problems:
        print("!! not everything went as expected:")
        for problem in problems:
            print(f"   - {problem}")
        return 1
    print(f"all {len(MUTATIONS)} mutations KILLED a guard on every expected cell, and "
          "no cell outside the declared set moved — the reconnect tuple, both "
          "deliberate branches of the tool wrapper, the crash boundary and the "
          "seam's lazy import are pinned by tests that can fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Mutation run for the imprint-capture freshness guards — Ш4 of
plan_2026_09_22_imprint_stale_cache_sh2.

Run it by hand from the repository root (it drives pytest, it is not a test):

    .venv/bin/python -m kicadstamp.diagnostics.run_imprint_freshness_mutations

The step's guards are К1–К3 in tests/gui/test_imprint.py, К4 and К6 in
tests/test_imprint_freshness_structure.py and К5 in tests/test_imprint_capture.py.
The freshness cells were seen RED on the base commit (ca5dc2d) before the fix —
that is the one half of the evidence. This harness is the other half: it proves
each guard still BITES after the fix, by putting the OLD code back.

One mutation per fixed body (four bodies), plus one that attacks the structural
cell from the other side: an ADDITION of a fifth capture starter in gui/ with no
rebuild. The four removals are diff-shaped (`git show <commit> -- <file>` minus
that file); the addition cannot be, and is declared here as the exception it is.

Machinery, fuses and the reading of a verdict are inherited from
diagnostics/run_board_door_offender_mutations.py, with the fuses that step
learned the hard way kept and one of them tightened:

  * ``original.count(old) != 1`` — REFUSE an ambiguous template. The four call
    sites are textually identical in their call line (`refresh_board_before_live_read(
    getattr(payload.get("board"), "adapter", None))` appears four times), so each
    template is anchored on the COMMENT BLOCK above its call, which differs per
    body. Without this fuse, `replace(old, new, 1)` edits a foreign line and the
    mutation reports GREEN: a silent miss is indistinguishable from a healthy
    guard, which is the worst of all failures because it soothes instead of
    shouting (deepseek.md §38);
  * a CONTROL id that is unrelated to every mutated body is run alongside the
    expected one. If the control goes red, the mutation broke the module (an
    import error reddens everything) and the run is reported as NOT A VERDICT,
    never as a killing;
  * the verdict is read off the EXIT CODE of a run of ONE FULL test id: 0 green,
    1 red, 2 (interrupted/collection error) and 5 ("no tests ran") are NOT
    verdicts;
  * a red on an id we did not expect is a FINDING, and is printed as one.

Every wait here is explicit (a pytest run at 300 s), so this module needs no
entry in the timeout sweep's exception list and builds no adapter of its own.
"""
import subprocess
import sys
from pathlib import Path

PY = sys.executable
PYTEST_TIMEOUT_S = 300

HUB = "gui/dock_hub.py"
IMPRINT = "gui/docks/imprint.py"

# Cell 1 is parametrized over the two capture bodies, so it has one id PER BODY:
# М1 and М2 must each kill their own row, and (see expect_green below) leave the
# other row alone — that is what makes the parametrization a pair of guards
# instead of a row that quietly stands for both.
_K1_ROWS = ("tests/gui/test_imprint.py::"
            "test_capture_worker_records_the_live_position_not_the_cached_one[")
K1 = _K1_ROWS + "_run_record_capture]"
K1_RESOURCE = _K1_ROWS + "_run_resource_capture]"
K2 = ("tests/gui/test_imprint.py::"
      "test_reread_diff_and_apply_read_the_same_live_board_state")
K4 = ("tests/test_imprint_freshness_structure.py::"
      "test_every_gui_capture_starter_refreshes_the_board_first")
CONTROL = ("tests/test_imprint_freshness_structure.py::"
           "test_core_imprint_signature_is_frozen[build_imprint_diff]")


def _repo_root() -> Path:
    """The repository root: the nearest ancestor of this file that carries a
    pyproject.toml.

    WALKED, not counted. `Path(__file__).parent.parent` is correct for
    diagnostics/ and WRONG for kicadstamp/diagnostics/ — it would return the
    package directory, and the subprocess below would then run pytest inside
    `kicadstamp/`, where none of the ids above exist. Every mutation would come
    back "green" by absence, and the harness would report the opposite of the
    truth."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise SystemExit(f"pyproject.toml not found above {here}")


ROOT = _repo_root()

# Each template is the comment block ABOVE one call site plus the call itself —
# unique per body, unlike the call line alone. The bodies and their comments are
# read from the fix commit (`git show <commit> -- gui/dock_hub.py
# gui/docks/imprint.py`); the em dash inside them is copied verbatim.
_RECORD_CAPTURE_SEAM = (
    "            # A live read: rebuild the board first, or the capture records the\n"
    "            # positions the adapter's cache still holds (measured 1.8063 mm of\n"
    "            # lag on 11 refs — plan_2026_09_22_imprint_stale_cache_sh2 §1.1/§2.4).\n"
    "            refresh_board_before_live_read(getattr(payload.get(\"board\"), "
    "\"adapter\", None))\n")

_RESOURCE_CAPTURE_SEAM = (
    "            # Same live-read rule as _run_record_capture above: the Re-source\n"
    "            # capture must see the board as it is now, not as the cache has it.\n"
    "            refresh_board_before_live_read(getattr(payload.get(\"board\"), "
    "\"adapter\", None))\n")

_REREAD_SEAM = (
    "            # A live read: rebuild the board first, or the diff compares the\n"
    "            # stored record against a stale cache (plan_2026_09_22_imprint_\n"
    "            # stale_cache_sh2 §1.1/§2.4 — measured 1.8063 mm of lag).\n"
    "            refresh_board_before_live_read(getattr(payload.get(\"board\"), "
    "\"adapter\", None))\n")

_REREAD_APPLY_SEAM = (
    "            # Same live-read rule as _run_reread above: Apply rewrites the record\n"
    "            # from the board as it is now — what the user just saw in the diff.\n"
    "            refresh_board_before_live_read(getattr(payload.get(\"board\"), "
    "\"adapter\", None))\n")

_M5_ADDITION = '''


def _m5_capture_starter_without_a_refresh(adapter, refs):
    """M5 probe — appended by run_imprint_freshness_mutations.py, never committed.

    A FIFTH capture starter in gui/ with no rebuild of the board. It exists so the
    structural cell (К4) is shown to bite on code written after itself: the four
    known bodies were fixed by hand, and the class is exactly what a hand-written
    list of call sites forgets — which is how this defect returned three times
    (bbox 10.09 → imprint capture → MCP)."""
    return capture_imprint(name="m5-probe", refs=refs, adapter=adapter)
'''

MUTATIONS = [
    dict(name="m1-record-capture-without-refresh", rel=HUB,
         old=_RECORD_CAPTURE_SEAM, new="",
         expect_red=[K1, K4], expect_green=[K1_RESOURCE],
         note="М1 — _run_record_capture keeps its rebuild (the Record body)"),
    dict(name="m2-resource-capture-without-refresh", rel=HUB,
         old=_RESOURCE_CAPTURE_SEAM, new="",
         expect_red=[K1_RESOURCE, K4], expect_green=[K1],
         note="М2 — _run_resource_capture keeps its rebuild (the Re-source body); "
              "this mutation is the one that ONCE stayed green, when cell 1 was "
              "written for the Record body only"),
    dict(name="m3-reread-diff-without-refresh", rel=IMPRINT,
         old=_REREAD_SEAM, new="",
         expect_red=[K2, K4],
         note="М3 — _run_reread keeps its rebuild (the diff read)"),
    dict(name="m4-reread-apply-without-refresh", rel=IMPRINT,
         old=_REREAD_APPLY_SEAM, new="",
         expect_red=[K2, K4],
         note="М4 — _run_reread_apply keeps its rebuild (the Apply read)"),
    dict(name="m5-new-gui-starter-without-refresh", rel=IMPRINT,
         old=None, new=_M5_ADDITION,
         expect_red=[K4],
         note="М5 — the structural cell breaks from an ADDITION (not a diff)"),
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
        if old is None:
            mutated = original + mutation["new"]
        else:
            count = original.count(old)
            if count != 1:
                print(f"!! {mutation['name']}: the template matches {count} sites in "
                      f"{mutation['rel']} — refusing to run a mutation whose edit is "
                      "ambiguous (it would edit a foreign line and report GREEN, "
                      "deepseek.md §38)")
                return 2
            mutated = original.replace(old, mutation["new"], 1)

        path.write_text(mutated, encoding="utf-8")
        try:
            control_status, control_evidence = _run_one(CONTROL)
            results = [(test_id, *_run_one(test_id))
                       for test_id in mutation["expect_red"]]
            untouched = [(test_id, *_run_one(test_id))
                         for test_id in mutation.get("expect_green", [])]
        finally:
            path.write_text(original, encoding="utf-8")

        print(f"\n=== {mutation['name']}  ({mutation['note']})")
        print(f"    control {CONTROL.split('::')[-1]}: {control_status.upper()}"
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
        # The mutation must be SURGICAL: the sibling row of the same parametrized
        # cell guards another body and has no business failing here. A sibling that
        # went red too would mean the edit touched more than the template named —
        # which is the silent miss this harness exists to make visible.
        for test_id, status, evidence in untouched:
            cell = test_id.split("::")[-1]
            print(f"    {'untouched (green)' if status == 'green' else 'TOUCHED ' + status.upper()}"
                  f": {cell}")
            print(f"        | {evidence}")
            if status != "green":
                problems.append(f"{mutation['name']} -> {cell} "
                                f"(expected green, got {status})")

    print("\n" + "=" * 72)
    if problems:
        print("!! not everything went as expected:")
        for problem in problems:
            print(f"   - {problem}")
        return 1
    print(f"all {len(MUTATIONS)} mutations KILLED a guard on every expected cell — the "
          "four rebuilds and the structural class cell are pinned by tests that can "
          "fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# tests/checks/test_check_json_schema.py
"""Cells C1–C6 of Ч2б-1: the ONE `--json PATH` report of the four `tools/check_*.py` scripts.

Why this file exists: the Checks tab must show a check's findings as a tree, and the only
other way to do that is parsing the prose a human reads. So every script writes the same
machine report (`kicadstamp/check_report.py`) in the SAME run, and these cells pin the
promises that make it usable:

  C1  `--json` leaves the human report byte-identical (Copy and every older cell survive);
  C2  `result.exit` IS the exit code the caller gets;
  C3  every `problems[].text` appears verbatim in the report — one assembly, not a copy;
  C4  `kind` comes from the closed vocabulary and `key` is stable between runs;
  C5  NEW/GONE are reported by that key, for the THREE checks that have it (a dry-run merge
      has nothing to compare: decision of Denis, 26.10.2026);
  C6  `refs`/`to_node` name only things that EXIST — a guess would send the operator to the
      wrong node, and there is no "Открыть ячейку" fallback to hide it.

Each script is driven through its own smallest fixture below; placement reuses the fixture
of `test_check_placement_exit_code.py` (its `_run`) instead of growing a second copy.
"""
import json
from types import SimpleNamespace

import pytest

from kicadstamp import check_report
from tests.checks.test_check_placement_exit_code import _Fp, _run as _placement_run
from tests.support_env import isolate_home
from tools import check_config_records, check_footprints_vs_library, check_placement
from tools import merge_duplicate_mounts

PLACEMENT = "check_placement.py"
RECORDS = "check_config_records.py"
FOOTPRINTS = "check_footprints_vs_library.py"
MERGE = "merge_duplicate_mounts.py"

#: The three checks that keep a NEW/GONE baseline. `merge_duplicate_mounts.py` does not.
WITH_CHANGES = (PLACEMENT, RECORDS, FOOTPRINTS)


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path, monkeypatch):
    """The baseline lives under the home directory (and `%APPDATA%` decides where KiCad's
    settings are looked for on win32) — both isolated, same helper as everywhere else."""
    monkeypatch.delenv("KICAD10_FOOTPRINT_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return isolate_home(monkeypatch, tmp_path / "home")


def _fresh_home(monkeypatch, tmp_path, name):
    """A SECOND home: two runs that must both look like "the first run" get two baselines."""
    return isolate_home(monkeypatch, tmp_path / name)


# ── per-script fixtures ──────────────────────────────────────────────────────

#: placement: A and B agree on the frame, C stands 20 mm off its slot (one MISPLACED).
_PLACEMENT_BAD = [_Fp("R1", "A", "CL", 100.0, 100.0),
                  _Fp("R2", "B", "CL", 110.0, 100.0),
                  _Fp("R3", "C", "CL", 100.0, 120.0)]
_PLACEMENT_GOOD = [_Fp("R1", "A", "CL", 100.0, 100.0),
                   _Fp("R2", "B", "CL", 110.0, 100.0),
                   _Fp("R3", "C", "CL", 120.0, 100.0)]


def _tree_node(ref, role, cluster, sheet=None):
    anchor = SimpleNamespace(role=role, anchor_cluster=cluster, anchor_sheet=sheet)
    return SimpleNamespace(ref=ref, anchor=anchor, children=[])


def _entity(name, cell, cluster, sheet=None, refs=None):
    return SimpleNamespace(name=name, cell=cell, cluster=cluster, sheet=sheet,
                           refs=refs or {})


def _cell(*roles):
    return SimpleNamespace(components=[SimpleNamespace(role=r) for r in roles])


def _run_records(tmp_path, monkeypatch, capsys, *, variant, json_path):
    """config records: the board read is replaced, everything that DECIDES runs for real."""
    profile = tmp_path / "prof"
    profile.mkdir(exist_ok=True)
    sexp = profile / "config.sexp"
    sexp.write_text("(kicadstamp-config)\n", encoding="utf-8")
    parts = [check_config_records.Part("R1", "A", "CL", ("/",))]
    if variant == "bad":
        # a cell role (B) the board does not have, and an anchor on a tree node that names it
        parts.append(check_config_records.Part("R2", "A", "OTHER", ("/",)))
    # The REAL tree shape (Д-1 of the Ч2б-1 acceptance): a mount node anchored on the HOST's
    # pad, and the entity's own placement node as its CHILD with `ref == entity.name`. An
    # anchor lookup finds no (cluster, sheet) pair here — that is the defect the cell pins.
    placement_node = SimpleNamespace(ref="E", anchor=None, children=[])
    host_mount = SimpleNamespace(
        ref="mnt_host",
        anchor=SimpleNamespace(role="A", anchor_cluster="CL", anchor_sheet=None),
        children=[placement_node])
    trees = [SimpleNamespace(name="t", anchor=None,
                             nodes=[host_mount,
                                    _tree_node("n1", "Z" if variant == "bad" else "A", "CL")])]
    cfg = SimpleNamespace(entities=[_entity("E", "cell1", "CL")],
                          cells={"cell1": _cell("A", "B")},
                          net_traces=[], trees=trees, points={}, thermal_via_arrays=[])
    ctx = SimpleNamespace(sheet_names={}, root_sheet="Test02.kicad_sch")
    monkeypatch.setattr(check_config_records, "_load_copy",
                        lambda path, name: (profile, sexp, cfg, ctx))
    monkeypatch.setattr(check_config_records, "_read_board",
                        lambda cfg_file, context: parts)
    argv = [str(sexp)] + (["--json", str(json_path)] if json_path else [])
    code = check_config_records.main(argv)
    return code, capsys.readouterr().out


_BOARD_ONE = ('(kicad_pcb\n'
              '  (footprint "Lib:R1"\n'
              '    (property "Reference" "R1")\n'
              '    (property "Role" "A")\n'
              '    (property "Cluster" "CL")))\n')


def _run_footprints(tmp_path, monkeypatch, capsys, *, variant, json_path):
    lib_dir = tmp_path / "kicad-footprints"
    lib_dir.mkdir(exist_ok=True)
    pretty = lib_dir / "Lib.pretty"
    pretty.mkdir(exist_ok=True)
    models = " (model \"a.wrl\") (model \"b.wrl\")" if variant == "bad" else " (model \"a.wrl\")"
    (pretty / "R1.kicad_mod").write_text(f'(footprint "R1"{models})\n', encoding="utf-8")
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_BOARD_ONE, encoding="utf-8")
    argv = [str(board), "--lib-dir", str(lib_dir)]
    if json_path:
        argv += ["--json", str(json_path)]
    code = check_footprints_vs_library.main(argv)
    return code, capsys.readouterr().out


def _mount(ref):
    return {"kind": "mount", "ref": ref,
            "anchor": {"role": "A", "cluster": "CL", "pad": "1"},
            "xy": [1.0, 2.0], "children": []}


def _run_merge(tmp_path, monkeypatch, capsys, *, variant, json_path):
    cfg_path = tmp_path / "prof" / "config.sexp"
    cfg_path.parent.mkdir(exist_ok=True)
    cfg_path.write_text("(kicadstamp-config)\n", encoding="utf-8")
    nodes = [_mount("m1"), _mount("m2")] if variant == "bad" else [_mount("m1")]
    data = {"trees": [{"name": "t", "nodes": nodes}]}
    monkeypatch.setattr(merge_duplicate_mounts, "read_data", lambda path: data)
    argv = [str(cfg_path)] + (["--json", str(json_path)] if json_path else [])
    code = merge_duplicate_mounts.main(argv)
    return code, capsys.readouterr().out


def _run(script, tmp_path, monkeypatch, capsys, *, variant, with_json=True, home="home"):
    """Run ONE script on its smallest fixture. Returns (exit code, stdout, report|None).

    `home` names the home directory this run sees: a NEW/GONE baseline lives under it, so
    "two independent runs" is two names (C1), while "the same board twice" is one (C4/C5).
    `with_json=False` is the plain terminal run — the half C1 compares against."""
    isolate_home(monkeypatch, tmp_path / home)
    path = (tmp_path / f"{script}.json") if with_json else None
    if script == PLACEMENT:
        code, out = _placement_run(
            tmp_path, monkeypatch, capsys,
            _PLACEMENT_BAD if variant == "bad" else _PLACEMENT_GOOD,
            extra_args=(("--json", str(path)) if path else ()), home_name=home)
    elif script == RECORDS:
        code, out = _run_records(
            tmp_path, monkeypatch, capsys, variant=variant, json_path=path)
    elif script == FOOTPRINTS:
        code, out = _run_footprints(
            tmp_path, monkeypatch, capsys, variant=variant, json_path=path)
    elif script == MERGE:
        code, out = _run_merge(
            tmp_path, monkeypatch, capsys, variant=variant, json_path=path)
    else:                                          # pragma: no cover — a typo in a cell
        raise AssertionError(script)
    if path is None:
        return code, out, None
    assert path.is_file(), f"{script}: --json wrote no report"
    return code, out, json.loads(path.read_text(encoding="utf-8"))


def _all_problems(data):
    return [entry for section in data["sections"] for entry in section["problems"]]


# ── C1: the human report does not move ──────────────────────────────────────

@pytest.mark.parametrize("script", (PLACEMENT, RECORDS, FOOTPRINTS, MERGE))
def test_json_mode_leaves_the_human_report_byte_identical(script, tmp_path, monkeypatch,
                                                          capsys):
    """C1 — `--json PATH` writes a FILE and does not touch stdout. Both runs are "the first
    run" for their own home, so the CHANGES block matches too (a second run on one baseline
    would legitimately say `last run: …`)."""
    _code_plain, out_plain, _ = _run(script, tmp_path, monkeypatch, capsys, variant="bad",
                                     with_json=False, home="home_plain")
    _code_json, out_json, _data = _run(script, tmp_path, monkeypatch, capsys,
                                       variant="bad", home="home_json")

    assert out_json == out_plain, f"{script}: --json changed the human report"


# ── C2: the exit code is the one in the report ──────────────────────────────

@pytest.mark.parametrize("script,variant", [(PLACEMENT, "bad"), (RECORDS, "bad"),
                                            (FOOTPRINTS, "bad"), (MERGE, "bad"),
                                            (PLACEMENT, "good")])
def test_json_result_matches_the_exit_code(script, variant, tmp_path, monkeypatch, capsys):
    """C2 — `result.exit` is the code `main()` returned: the panel colours the row by it and
    must never have to guess from a count."""
    code, _out, data = _run(script, tmp_path, monkeypatch, capsys, variant=variant)

    assert data["result"]["exit"] == code
    assert data["schema"] == check_report.SCHEMA_VERSION
    assert data["script"] == script


# ── C3: one assembly of every line ──────────────────────────────────────────

@pytest.mark.parametrize("script", (PLACEMENT, RECORDS, FOOTPRINTS, MERGE))
def test_every_problem_text_appears_verbatim_in_the_report(script, tmp_path, monkeypatch,
                                                           capsys):
    """C3 — a problem's `text` is the very line stdout showed (modulo the indent `print`
    carries), so "Copy" and the tree can never disagree about what was found."""
    _code, out, data = _run(script, tmp_path, monkeypatch, capsys, variant="bad")

    problems = _all_problems(data)
    assert problems, f"{script}: the fixture is supposed to find something"
    for entry in problems:
        assert entry["text"].strip() in out, (script, entry["text"])


# ── C4: closed vocabulary, stable keys ──────────────────────────────────────

@pytest.mark.parametrize("script", (PLACEMENT, RECORDS, FOOTPRINTS, MERGE))
def test_kinds_are_from_the_vocabulary_and_keys_are_stable(script, tmp_path, monkeypatch,
                                                           capsys):
    """C4 — `kind` is one of `check_report.KINDS` (the GUI switches on it) and the SAME run
    twice produces the same KEYS: a line number or a timestamp in a key would report every
    finding as new on every run."""
    _fresh_home(monkeypatch, tmp_path, "home_first")
    _code, _out, first = _run(script, tmp_path, monkeypatch, capsys, variant="bad")
    assert {entry["kind"] for entry in _all_problems(first)} <= check_report.KINDS

    _code, _out, second = _run(script, tmp_path, monkeypatch, capsys, variant="bad")
    assert [entry["key"] for entry in _all_problems(second)] == \
        [entry["key"] for entry in _all_problems(first)]


def test_a_problem_without_a_key_gets_one_from_its_kind_and_text():
    """C4 — the DEFAULT key. Every script passes `key=` today, but the fallback is what the
    next check will use, and it must be derived from the FINDING: a clock or a line number
    would report the same problem as new on every run."""
    first = check_report.problem("MISPLACED", "   R12 is 1.250 mm off")
    second = check_report.problem("MISPLACED", "   R12 is 1.250 mm off")

    assert first["key"] == "MISPLACED|   R12 is 1.250 mm off"
    assert first["key"] == second["key"]


# ── C5: NEW / GONE by that key, for three scripts ───────────────────────────

@pytest.mark.parametrize("script", WITH_CHANGES)
def test_changes_are_reported_by_the_same_key(script, tmp_path, monkeypatch, capsys):
    """C5 — the first run is a BASELINE, an identical second run adds nothing, and a board
    that grew a problem reports exactly it as NEW (keyed, not re-parsed)."""
    _code, _out, first = _run(script, tmp_path, monkeypatch, capsys, variant="good")
    assert first["changes"]["baseline"] is True
    assert first["changes"]["new"] == []

    _code, _out, second = _run(script, tmp_path, monkeypatch, capsys, variant="good")
    assert second["changes"]["baseline"] is False
    assert second["changes"]["new"] == []

    _code, _out, third = _run(script, tmp_path, monkeypatch, capsys, variant="bad")
    new_keys = [entry["key"] for entry in third["changes"]["new"]]
    assert new_keys == [entry["key"] for entry in _all_problems(third)
                        if entry["key"] not in
                        {e["key"] for e in _all_problems(second)}]


def test_the_merge_report_has_no_changes(tmp_path, monkeypatch, capsys):
    """C5's other half — decision of Denis: a dry-run merge describes what stands in the
    config now, so it carries NO `changes` at all (nothing to compare, nothing to keep)."""
    _code, _out, data = _run(MERGE, tmp_path, monkeypatch, capsys, variant="bad")

    assert data["changes"] is None


# ── C6: addresses are not invented ──────────────────────────────────────────

def test_the_anchor_to_node_names_a_node_that_exists(tmp_path, monkeypatch, capsys):
    """C6 — a tree-node anchor problem carries `to_node` {tree, node} of THAT node (the
    panel's "К ноде в дереве"), and a node-less anchor carries no node at all."""
    _code, _out, data = _run(RECORDS, tmp_path, monkeypatch, capsys, variant="bad")

    anchors = next(section for section in data["sections"] if section["id"] == "anchors")
    node_problem = next(entry for entry in anchors["problems"]
                        if entry["to_node"] is not None)
    assert node_problem["to_node"] == {"tree": "t", "node": "n1", "cell": None}
    assert node_problem["refs"] == [], \
        "the address names no part, so there is nothing to highlight"


def test_the_placement_to_node_is_the_entitys_placement_node(tmp_path, monkeypatch, capsys):
    """C6 — an INSTANCE is an entity, and its "to the node" address is the placement node
    whose ref IS the entity's name (Д-1 of the Ч2б-1 acceptance). The fixture's tree holds
    that node with NO anchor at all, so an anchor lookup would report nothing."""
    _code, _out, data = _run(PLACEMENT, tmp_path, monkeypatch, capsys, variant="bad")

    problems = _all_problems(data)
    assert problems, "the fixture has a misplaced part"
    for entry in problems:
        assert entry["refs"], "a placement problem always names its part"
        assert entry["where"]["cluster"] == "CL"
        assert entry["to_node"] == {"tree": "t", "node": "E1", "cell": "cell1"}


def test_the_entity_to_node_is_the_placement_node_of_its_name(tmp_path, monkeypatch, capsys):
    """C6 for config records — same address rule, and the tree is the REAL shape: a mount
    node anchored on the host's pad with the entity's placement node as its child. The
    anchor lookup this used to do found ZERO addresses on the live profile (73 of 73 are
    placement nodes by name)."""
    _code, _out, data = _run(RECORDS, tmp_path, monkeypatch, capsys, variant="bad")

    entities = next(section for section in data["sections"] if section["id"] == "entities")
    problem = entities["problems"][0]
    assert problem["to_node"] == {"tree": "t", "node": "E", "cell": "cell1"}


# ── Д-2: "not checked" is a note, and the sections add up ───────────────────

@pytest.mark.parametrize("script", (PLACEMENT, RECORDS, FOOTPRINTS, MERGE))
def test_the_sections_add_up_to_the_problem_count(script, tmp_path, monkeypatch, capsys):
    """Д-2 — `result.problems` is the sum over the sections, so a reader that counts the tree
    and a reader that reads the header can never disagree. A finding that is counted but not
    listed (or listed twice) is the defect behind this cell."""
    _code, _out, data = _run(script, tmp_path, monkeypatch, capsys, variant="bad")

    assert sum(len(section["problems"]) for section in data["sections"]) == \
        data["result"]["problems"]


def test_a_missing_library_copy_is_a_note_not_a_problem(tmp_path, monkeypatch, capsys):
    """Д-2 — "we could not look" must not read like "we found damage": the footprint still
    prints its line, but it lands in `notes[]` as NOT_CHECKED and in `result.not_checked`,
    and `LIB_MISSING` is gone from the kind vocabulary."""
    _fresh_home(monkeypatch, tmp_path, "home_lib")
    lib_dir = tmp_path / "empty-lib"
    lib_dir.mkdir()
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_BOARD_ONE, encoding="utf-8")
    report = tmp_path / "report.json"

    code = check_footprints_vs_library.main(
        [str(board), "--lib-dir", str(lib_dir), "--json", str(report)])
    out = capsys.readouterr().out
    data = json.loads(report.read_text(encoding="utf-8"))

    assert code == 0, out
    assert "library copy not found — not checked" in out
    assert data["result"]["not_checked"] == 1
    assert data["result"]["problems"] == 0
    assert _all_problems(data) == []
    kinds = [note["kind"] for note in data["notes"]]
    assert kinds.count("NOT_CHECKED") == 1, kinds
    assert "LIB_MISSING" not in check_report.KINDS


def test_refs_of_every_check_exist_on_its_fixture(tmp_path, monkeypatch, capsys):
    """C6 — the other direction: a ref in `refs` is a part the check really saw. A ref taken
    from prose (`_elsewhere`'s "it stands on R7") would send the overlay somewhere else."""
    known = {RECORDS: {"R1", "R2"}, FOOTPRINTS: {"R1"}, MERGE: {"m1", "m2"}}
    _code, _out, records = _run(RECORDS, tmp_path, monkeypatch, capsys, variant="bad")
    _code, _out, footprints = _run(FOOTPRINTS, tmp_path, monkeypatch, capsys, variant="bad")
    _code, _out, merge = _run(MERGE, tmp_path, monkeypatch, capsys, variant="bad")

    for data in (records, footprints, merge):
        script = data["script"]
        for entry in _all_problems(data):
            assert set(entry["refs"]) <= known[script], (script, entry["refs"])

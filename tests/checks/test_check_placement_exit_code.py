# tests/checks/test_check_placement_exit_code.py
"""Exit codes of `tools/check_placement.py` — 0 clean, 1 problems, 2 refused.

The three codes are the ONE thing a caller may act on without reading the report: a shell `&&`
chain, and the GUI's Checks tab colouring its status line (plan_2026_10_22_checks_tab). Until
22.10.2026 the script printed its findings and then returned 0 anyway (`main()` ended `return 0`),
so "problems found" was indistinguishable from "clean" by code — a defect Denis called out.

A live KiCad is not needed here: the BOARD READ is the only thing replaced.
  * `create_board_adapter` — a stand-in with exactly the three calls the script makes
    (`refresh_board`, `get_board_filename`, `get_footprints`, `get_field_value`, `close`);
  * `_load_copy` — the temporary profile copy is replaced by in-memory cfg/ctx, so no
    `config.sexp` has to be a real profile (its OWN `_config_name`/copy logic is not the subject);
  * `resolve_sheet_path_names` — one fixed top sheet, so the fake footprints need no sheet path.

Everything that DECIDES the code runs for real: `_cluster_cells`, the consensus frame
(`cell_frame.fit_cell_frame`, `pair_deviation_mm`), the misplaced-part detection and the final
`return 1 if issues else 0`. That is what makes these cells a pin on the behaviour and not a copy
of it.

`HOME` is pointed at `tmp_path` for every cell: `_compare_with_last` remembers itself in
`~/.local/state/kicadstamp/placement_check_<profile>.json`, and a cell must never write into the
developer's real state (the cell below also asserts the real run DOES write there — that shared
baseline is what the GUI tab and the terminal have in common).
"""
from types import SimpleNamespace

from kicadstamp.utils.units import MM

from tests.support_env import isolate_home
from tools import check_placement


class _Fp:
    """The four attributes `check_placement` reads from a footprint: ref, position, angle, fields."""

    def __init__(self, ref, role, cluster, x_mm, y_mm, angle_deg=0.0):
        self.ref = ref
        self.position = SimpleNamespace(x=int(round(x_mm * MM)), y=int(round(y_mm * MM)))
        self.angle_deg = angle_deg
        self.fields = {"Role": role, "Cluster": cluster}


class _Adapter:
    """The board-read stand-in — the same calls `main()` makes, nothing more."""

    def __init__(self, board_file, footprints):
        self._board_file = board_file
        self._footprints = list(footprints)
        self.closed = False

    def refresh_board(self):
        pass

    def get_board_filename(self):
        return self._board_file

    def get_footprints(self):
        return list(self._footprints)

    def get_field_value(self, footprint, name):
        return footprint.fields.get(name)

    def close(self):
        self.closed = True


def _component(role, along_mm, across_mm):
    return SimpleNamespace(role=role, offset_along_mm=along_mm,
                           offset_across_mm=across_mm, angle_deg=0.0)


#: A three-role cell: A at the origin, B 10 mm along, C 20 mm along.
_COMPONENTS = [_component("A", 0.0, 0.0), _component("B", 10.0, 0.0),
               _component("C", 20.0, 0.0)]


def _run(tmp_path, monkeypatch, capsys, footprints, board_file="Test02.kicad_pcb"):
    """Run the real `main()` on a fake board; return (exit code, stdout)."""
    profile = tmp_path / "prof"
    profile.mkdir()
    sexp = profile / "config.sexp"
    # The FILE branch of main() (a path to the profile's .sexp) is the one the GUI passes.
    sexp.write_text("(kicadstamp-config)\n", encoding="utf-8")
    copy_dir = tmp_path / "copy"           # `_load_copy`'s temp dir — main() rmtree's it, so it
    copy_dir.mkdir()                       # must NEVER be tmp_path itself
    cfg = SimpleNamespace(entities=[SimpleNamespace(cluster="CL", cell="cell1")],
                          cells={"cell1": SimpleNamespace(components=_COMPONENTS)})
    ctx = SimpleNamespace(sheet_names={}, root_sheet="Test02.kicad_sch")

    monkeypatch.setattr(check_placement, "_load_copy",
                        lambda path, name: (copy_dir, sexp, cfg, ctx))
    adapter = _Adapter(str(tmp_path / board_file), footprints)
    monkeypatch.setattr(check_placement, "create_board_adapter", lambda **kwargs: adapter)
    monkeypatch.setattr(check_placement, "resolve_sheet_path_names",
                        lambda footprint, names: ["Channel_0"])
    # BOTH names of the home directory: `_compare_with_last` remembers itself under
    # `~/.local/state/...`, and `Path.home()` reads USERPROFILE on Windows — with HOME alone
    # this cell wrote into the runner's real profile and failed on `windows-latest` (Д-5(a)).
    isolate_home(monkeypatch, tmp_path / "home")

    code = check_placement.main([str(sexp)])
    return code, capsys.readouterr().out


def test_a_clean_board_exits_zero_and_remembers_the_run(tmp_path, monkeypatch, capsys):
    """0 — every role on its slot. The run also leaves the NEW/GONE baseline, under HOME."""
    footprints = [_Fp("R1", "A", "CL", 100.0, 100.0),
                  _Fp("R2", "B", "CL", 110.0, 100.0),
                  _Fp("R3", "C", "CL", 120.0, 100.0)]

    code, out = _run(tmp_path, monkeypatch, capsys, footprints)

    assert code == 0, out
    assert "with problems: 0" in out, out
    baseline = tmp_path / "home" / ".local" / "state" / "kicadstamp" / "placement_check_prof.json"
    assert baseline.is_file(), "a real run must remember itself (the GUI tab shares this file)"


def test_one_misplaced_part_exits_one(tmp_path, monkeypatch, capsys):
    """1 — the frame is trusted (A and B agree) and C stands 20 mm off its slot.

    This is the cell the defect was about: before the fix the same report came back with code 0.
    """
    footprints = [_Fp("R1", "A", "CL", 100.0, 100.0),
                  _Fp("R2", "B", "CL", 110.0, 100.0),
                  _Fp("R3", "C", "CL", 100.0, 120.0)]

    code, out = _run(tmp_path, monkeypatch, capsys, footprints)

    assert "MISPLACED C (R3)" in out, out
    assert code == 1, out


def test_a_wrong_board_open_exits_two(tmp_path, monkeypatch, capsys):
    """2 — KiCad has another board open than the profile is for: refuse, whatever the findings."""
    footprints = [_Fp("R1", "A", "CL", 100.0, 100.0),
                  _Fp("R2", "B", "CL", 110.0, 100.0),
                  _Fp("R3", "C", "CL", 120.0, 100.0)]

    code, out = _run(tmp_path, monkeypatch, capsys, footprints,
                     board_file="Other-Board.kicad_pcb")

    assert "REFUSED" in out, out
    assert code == 2, out

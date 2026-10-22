# tests/checks/test_check_footprints_lib_dir.py
"""Which footprint library directory `tools/check_footprints_vs_library.py` uses.

The script used to hardcode `/usr/share/kicad/footprints`, so on Windows (and on any machine
with KiCad elsewhere) it reported every footprint as "library copy not found — not checked" and
still exited 0. Denis, 22.10.2026: the search order must be KiCad's own, and a run that cannot
reach ANY library must refuse out loud instead of reporting nothing:

  1. `--lib-dir`, when given (a path that does not exist is refused, never ignored);
  2. `KICAD10_FOOTPRINT_DIR` — the environment first, then the variable of the same name in
     `kicad_common.json` (where KiCad itself keeps it);
  3. the platform default of a stock KiCad install;
  4. none of the above → exit 2 and ONE line naming every place that was looked at.

No KiCad and no library tree is needed: the cells build the directories they need under
`tmp_path`, and an autouse fixture points `HOME` at `tmp_path` and unsets the variable, so a
developer's own environment or `kicad_common.json` can never decide a cell.
"""
import json
import sys
from pathlib import Path

import pytest

from tools import check_footprints_vs_library as check


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path, monkeypatch):
    """No ambient library may leak in: HOME decides where kicad_common.json is looked for."""
    monkeypatch.delenv("KICAD10_FOOTPRINT_DIR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _dir(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def _write_common_json(home, value):
    cfg = home / ".config" / "kicad" / "10.0"
    cfg.mkdir(parents=True)
    (cfg / "kicad_common.json").write_text(
        json.dumps({"environment": {"vars": {"KICAD10_FOOTPRINT_DIR": str(value)}}}),
        encoding="utf-8")


def _settings_dir(monkeypatch, tmp_path, platform, *, appdata=True):
    """Run `_kicad_settings_dir()` AS `platform` — `HOME` and `%APPDATA%` both under tmp_path,
    so the cell can never read the developer's own KiCad install. Patching `sys.platform` is the
    suite's existing way to answer a platform question (tests/repo/test_process_exit_helper.py)."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    if appdata:
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    else:
        monkeypatch.delenv("APPDATA", raising=False)
    return check._kicad_settings_dir()


def test_the_settings_directory_is_the_linux_one(tmp_path, monkeypatch):
    """Д-2 — Linux: `~/.config/kicad/10.0` (the one path that was right before the fix)."""
    assert _settings_dir(monkeypatch, tmp_path, "linux") == \
        tmp_path / "home" / ".config" / "kicad" / "10.0"


def test_the_settings_directory_on_windows_is_appdata(tmp_path, monkeypatch):
    """Д-2 — Windows: `%APPDATA%\\kicad\\10.0`, the only place KiCad writes there. Looking under
    `~/.config` lost BOTH the KICAD10_FOOTPRINT_DIR lookup and the user's fp-lib-table."""
    assert _settings_dir(monkeypatch, tmp_path, "win32") == \
        tmp_path / "appdata" / "kicad" / "10.0"


def test_windows_without_appdata_falls_back_to_home(tmp_path, monkeypatch):
    """A headless Windows run without `%APPDATA%` still gets a deterministic directory."""
    assert _settings_dir(monkeypatch, tmp_path, "win32", appdata=False) == \
        tmp_path / "home" / "kicad" / "10.0"


def test_the_settings_directory_on_macos_is_library_preferences(tmp_path, monkeypatch):
    """Д-2 — macOS: `~/Library/Preferences/kicad/10.0`."""
    assert _settings_dir(monkeypatch, tmp_path, "darwin") == \
        tmp_path / "home" / "Library" / "Preferences" / "kicad" / "10.0"


def test_the_common_json_is_the_settings_directory_plus_its_name(tmp_path, monkeypatch):
    """ONE owner, asked on every platform: the settings FILE hangs off the SAME directory the
    library tables are read from — never a second copy of the path. The expectations are the
    literals, not the owner's own answer, so a wrong platform branch is red here too."""
    for platform, expected in (
            ("linux", tmp_path / "home" / ".config" / "kicad" / "10.0"),
            ("win32", tmp_path / "appdata" / "kicad" / "10.0"),
            ("darwin", tmp_path / "home" / "Library" / "Preferences" / "kicad" / "10.0")):
        _settings_dir(monkeypatch, tmp_path, platform)     # HOME/APPDATA/platform in place
        assert check._kicad_common_json() == expected / "kicad_common.json", platform


def test_the_users_fp_lib_table_is_read_from_the_windows_settings_directory(tmp_path,
                                                                           monkeypatch):
    """Д-2's second consumer: the user's `fp-lib-table` lives in the settings directory too, so on
    Windows it used to be looked for under `~/.config` — a directory KiCad never writes. The table
    is placed at the LITERAL %APPDATA% path (not where the owner would say), so a wrong platform
    branch leaves the nickname unresolved."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    directory = tmp_path / "appdata" / "kicad" / "10.0"
    directory.mkdir(parents=True)
    (directory / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "Mine")(type "KiCad")(uri "/libs/Mine.pretty")))',
        encoding="utf-8")
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)\n", encoding="utf-8")

    dirs = check._lib_dirs(board, tmp_path / "fallback")

    assert dirs["Mine"] == Path("/libs/Mine.pretty")


def test_the_lib_dir_argument_wins_over_the_environment_and_the_default(tmp_path, monkeypatch):
    """1 — `--lib-dir` is what KiCad would be told explicitly, so nothing else may override it."""
    given = _dir(tmp_path, "given")
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(_dir(tmp_path, "from_env")))

    path, refusal = check._resolve_lib_dir(
        ["--lib-dir", str(given)], candidates=[_dir(tmp_path, "platform")])

    assert (path, refusal) == (given, "")


def test_a_lib_dir_that_is_not_a_directory_is_refused_not_ignored(tmp_path):
    """A typo in `--lib-dir` must not fall through to a plausible default: name the path."""
    missing = tmp_path / "typo"

    path, refusal = check._resolve_lib_dir(["--lib-dir", str(missing)])

    assert path is None
    assert "is not a directory" in refusal and str(missing) in refusal


def test_the_environment_variable_beats_the_platform_default(tmp_path, monkeypatch):
    """2a — `KICAD10_FOOTPRINT_DIR` in the environment, before any platform guess."""
    from_env = _dir(tmp_path, "from_env")
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(from_env))

    path, refusal = check._resolve_lib_dir([], candidates=[_dir(tmp_path, "platform")])

    assert (path, refusal) == (from_env, "")


def test_the_kicad_common_json_variable_is_used_when_the_environment_has_none(tmp_path,
                                                                            _isolated_environment):
    """2b — the same variable inside kicad_common.json, which is where KiCad stores it."""
    from_json = _dir(tmp_path, "from_json")
    _write_common_json(_isolated_environment, from_json)

    path, refusal = check._resolve_lib_dir([], candidates=[_dir(tmp_path, "platform")])

    assert (path, refusal) == (from_json, "")


def test_the_platform_default_is_the_last_resort(tmp_path):
    """3 — a stock install is tried once both variables are silent, first existing candidate."""
    platform = _dir(tmp_path, "platform")

    path, refusal = check._resolve_lib_dir([], candidates=[tmp_path / "absent", platform])

    assert (path, refusal) == (platform, "")


def test_nothing_found_refuses_and_names_every_place_it_looked(tmp_path):
    """4 — the refusal must be actionable: every candidate path and both variable names."""
    absent = tmp_path / "absent"

    path, refusal = check._resolve_lib_dir([], candidates=[absent])

    assert path is None
    for named in ("--lib-dir (not given)", "$KICAD10_FOOTPRINT_DIR (not set)",
                  "kicad_common.json", str(absent), "Pass --lib-dir"):
        assert named in refusal, refusal


def test_main_exits_two_when_no_library_directory_is_found(tmp_path, monkeypatch, capsys):
    """The wiring, not just the resolver: no library → code 2 and the line, before any board work."""
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)\n", encoding="utf-8")
    monkeypatch.setattr(check, "_platform_lib_dirs", lambda: [])

    code = check.main([str(board)])

    out = capsys.readouterr().out
    assert code == 2, out
    assert "REFUSED: no KiCad footprint library directory found" in out, out


def test_main_checks_against_the_directory_it_resolved(tmp_path, capsys):
    """End to end on a two-line board: the resolved directory is the one actually compared, and
    a footprint whose 3D models are missing from its library copy is damage (exit 1)."""
    lib_dir = _dir(tmp_path, "kicad-footprints")
    pretty = lib_dir / "Lib.pretty"
    pretty.mkdir()
    (pretty / "R1.kicad_mod").write_text('(footprint "R1" (model "a.wrl"))\n', encoding="utf-8")
    board = tmp_path / "board.kicad_pcb"
    board.write_text(
        '(kicad_pcb\n'
        '  (footprint "Lib:R1"\n'
        '    (property "Reference" "R1")\n'
        '    (property "Role" "A")\n'
        '    (property "Cluster" "CL")))\n',
        encoding="utf-8")

    code = check.main([str(board), "--lib-dir", str(lib_dir)])

    out = capsys.readouterr().out
    assert f"library {lib_dir}" in out, out
    assert "3D models 0 of 1" in out, out
    assert code == 1, out

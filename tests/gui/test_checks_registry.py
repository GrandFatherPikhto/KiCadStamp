# tests/gui/test_checks_registry.py
"""Cells for `gui/checks_registry.py` — the DATA half of the Checks tab.

The tab itself only launches what these functions describe (plan_2026_10_22_checks_tab), so the
rules worth pinning live here, without a QApplication or a live KiCad:

  * the repository is found by WALKING UP to `pyproject.toml` + `tools/check_placement.py` —
    an installed package (or any tree without the scripts) is answered with None, never with a
    plausible guess;
  * every check in the table names a script that really exists in this tree (rename a tool and
    this cell goes red instead of the tab going quietly empty);
  * the command is `[interpreter, "-u", <script>, <profile .sexp>]` — `-u` is what makes the
    output arrive line by line through a pipe;
  * a missing project is refused HERE, so no programmatic call can run a check against nothing;
  * the exit code → level map is the vocabulary `gui/docks/_common` colours by, and anything
    that is not "clean" (None, an unknown code) is an error.
"""
from pathlib import Path

from gui import checks_registry
from gui.checks_registry import (CHECKS, TOOLS_DIR, command_for, exit_level,
                                 find_repo_root)
from tests.paths import REPO_ROOT


def test_the_repository_root_is_the_tree_that_carries_the_scripts():
    """The walk finds THIS checkout — and starts from a file, so callers may hand it one."""
    assert find_repo_root() == REPO_ROOT
    assert find_repo_root(Path(__file__)) == REPO_ROOT


def test_every_check_names_a_script_that_exists_in_this_tree():
    """A renamed or dropped tool must fail a cell, not leave the tab with a dead button.

    The list is LITERAL on purpose. It grew by one on 23.10 (`merge_duplicate_mounts.py`,
    plan_2026_10_23_checks_tab_top_level §7 — the mount-duplicate check) — the property the
    cell asserts is unchanged: every registry row names a script this tree really carries."""
    scripts = [check.script for check in CHECKS]
    assert scripts == ["check_placement.py", "check_config_records.py",
                       "check_footprints_vs_library.py", "merge_duplicate_mounts.py"]
    for check in CHECKS:
        assert (REPO_ROOT / TOOLS_DIR / check.script).is_file(), check.script
        assert check.title and check.tooltip, check


def test_a_tree_without_the_scripts_is_not_a_repository_root(tmp_path):
    """`pyproject.toml` alone is not enough: a wheel has one too."""
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert find_repo_root(tmp_path) is None


def test_no_project_is_refused_before_anything_is_built():
    """The buttons are disabled without a project; this is the same rule for a programmatic call."""
    argv, reason = command_for(CHECKS[0], None)

    assert argv is None
    assert "Open a project first" in reason


def test_an_installed_package_says_the_checks_need_a_working_copy(monkeypatch, tmp_path):
    """site-packages has no `tools/check_*.py`: say so in words, do not guess a path."""
    monkeypatch.setattr(checks_registry, "find_repo_root", lambda start=None: None)

    argv, reason = command_for(CHECKS[0], tmp_path / "prof" / "config.sexp")

    assert argv is None
    assert "only from a repository working copy" in reason


def test_a_missing_script_is_named(tmp_path):
    """A tree WITH `tools/` but without this script: the reason must carry the exact path."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    argv, reason = command_for(CHECKS[1], tmp_path / "config.sexp", root=tmp_path)

    assert argv is None
    assert str(tmp_path / TOOLS_DIR / "check_config_records.py") in reason


def test_the_command_is_dash_u_script_and_the_profile_file():
    """The exact argv the tab launches: unbuffered, the script, then the profile's `.sexp`."""
    profile = REPO_ROOT / "profiles" / "any" / "config.sexp"

    argv, reason = command_for(CHECKS[2], profile, root=REPO_ROOT, python="/opt/py")

    assert reason == ""
    assert argv == ["/opt/py", "-u",
                    str(REPO_ROOT / TOOLS_DIR / "check_footprints_vs_library.py"),
                    str(profile)]


def test_the_exit_code_vocabulary():
    """0 clean, 1 problems, 2 refused — and everything else is an error, never clean."""
    assert exit_level(0) == "success"
    assert exit_level(1) == "warn"
    assert exit_level(2) == "error"
    assert exit_level(99) == "error"
    assert exit_level(None) == "error"

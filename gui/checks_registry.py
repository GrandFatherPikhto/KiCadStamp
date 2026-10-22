# gui/checks_registry.py
"""The three CLI checks as DATA: which script, what it asks, and the command line that runs it.

The checks themselves stay in `tools/check_*.py` (see docs/check_scripts.md) — the CLI is the
one source of truth, and the GUI's Checks tab only LAUNCHES them (plan_2026_10_22_checks_tab).
This module is the one place that knows the LIST of checks and where the scripts live; the dock
owns nothing but the widgets and the process, so no check logic can leak into the GUI.

Qt-free on purpose: nothing here imports PyQt6, so these rules are testable without a
QApplication and the tab's Qt half stays small.

WHERE the scripts are found — by WALKING UP from this file to the first directory that carries
BOTH `pyproject.toml` and `tools/check_placement.py`. Counted depths are what the project's own
rule forbids (`tests/paths.py`, and the same lesson in
`kicadstamp/diagnostics/run_socket_owner_mutations.py`: `parent.parent` is right for
`diagnostics/` and wrong for `kicadstamp/diagnostics/`). An installed package (site-packages)
has no such directory at all — the tab then says the checks are available only from a working
copy, instead of guessing a plausible path and failing later.
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from kicadstamp.i18n import _

#: The directory (relative to the repository root) the check scripts live in.
TOOLS_DIR = "tools"

#: Both must exist for a directory to count as the repository root. The second one is a CHECK
#: SCRIPT, not `tools/` alone: a wheel or a hand-made folder with a `tools` directory must not
#: be mistaken for this tree.
_ROOT_MARKERS = ("pyproject.toml", f"{TOOLS_DIR}/check_placement.py")


@dataclass(frozen=True)
class Check:
    """One CLI check, described the way the tab needs it — never how it works."""

    #: File name inside `tools/`.
    script: str
    #: Button label (each one its OWN literal `_()` below: pybabel extracts literals).
    title: str
    #: One phrase: the question the check answers AND what it reads.
    tooltip: str


CHECKS: Tuple[Check, ...] = (
    Check("check_placement.py", _("Placement"),
          _("Is every part on its cluster's slot? Reads the LIVE board — this profile's "
            "board must be open in KiCad.")),
    Check("check_config_records.py", _("Config records"),
          _("Does every record of the .sexp address a part of its own cluster and sheet? "
            "Reads the LIVE board.")),
    Check("check_footprints_vs_library.py", _("Footprints vs library"),
          _("Are the saved board's footprints still what the library has? Reads the SAVED "
            ".kicad_pcb — save the board in KiCad first.")),
)


def find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """The working copy that carries `tools/`, or None for an installed package.

    `start` is a file or a directory (defaults to this file); the walk begins there and goes
    up. THE one way this project may locate its own tree — never a counted number of `parent`s.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if all((candidate / marker).is_file() for marker in _ROOT_MARKERS):
            return candidate
    return None


def command_for(check: Check, profile, root: Optional[Path] = None,
                python: Optional[str] = None) -> Tuple[Optional[List[str]], str]:
    """(argv, "") or (None, the reason to show the user).

    `profile` is the path of the profile's config FILE (what `root_metadata.root_path` holds) —
    all three scripts accept exactly that. A ``None`` profile is refused HERE, not passed on as
    an empty argument: the tab disables its buttons without a project, and a programmatic call
    must not be able to run a check against nothing.

    `python`/`root` are seams for tests (a fake interpreter, a bare tree); production passes
    neither, so the interpreter is `sys.executable` and the tree is found by `find_repo_root`.
    """
    if profile is None:
        return None, _("Open a project first — the checks run against the profile's config file.")
    tree = Path(root) if root is not None else find_repo_root()
    if tree is None:
        return None, _("The checks are available only from a repository working copy "
                       "(tools/check_*.py was not found).")
    script = tree / TOOLS_DIR / check.script
    if not script.is_file():
        return None, _("The check script was not found: {path}").format(path=script)
    # `-u` is not cosmetic: a child whose stdout is a PIPE block-buffers in 8 KB chunks, so
    # without it the tab would show the whole report at the END and never line by line.
    return [python or sys.executable, "-u", str(script), str(profile)], ""


#: What each exit code means — the codes every check speaks (docs/check_scripts.md):
#: 0 clean, 1 problems found, 2 refused.
EXIT_LEVELS = {0: "success", 1: "warn", 2: "error"}


def exit_level(code: Optional[int]) -> str:
    """The Qt-free LEVEL name for a finished run, from the vocabulary `gui/docks/_common`
    already owns (`LEVEL_STYLE`/`style_for_level`) — so the colour mapping lives in ONE place.

    None ("never started", "stopped by the user", "crashed") and any code the scripts do not
    use are errors: a check that did not report "clean" must never look clean.
    """
    return EXIT_LEVELS.get(code, "error")

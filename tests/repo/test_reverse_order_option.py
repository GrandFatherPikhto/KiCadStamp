# tests/repo/test_reverse_order_option.py
"""Ф3.1-б of plan_2026_09_27_repo_and_tests_transformation: `--reverse-order`.

The reverse run (docs/tests.md) was a bash-only pipeline — `find tests -name
'test_*.py' … | sort -r` — which a Windows checkout could not run at all. The
option in tests/conftest.py moves the reversal INTO pytest, so one command works
on both platforms. This cell proves the option actually reverses the FILE order of
the collected cells: without it, a `--reverse-order` that quietly did nothing
would make every "the reverse run is green" claim vacuous (rule 38).

Collection only (`--collect-only`) in a subprocess: no cell is executed, so the
integration files are never driven to write to a live board.
"""
import subprocess
import sys

from tests.paths import REPO_ROOT

_COLLECT = ["-m", "pytest", "--collect-only", "-q", "--no-header",
            "-p", "no:cacheprovider"]


def _collected_file_order(*extra: str) -> list[str]:
    """The ordered, de-duplicated list of test FILE paths pytest collected."""
    proc = subprocess.run([sys.executable, *_COLLECT, *extra],
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          timeout=300)
    files: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        path = line.split("::", 1)[0]
        if path not in files:
            files.append(path)
    assert files, f"collected nothing:\n{proc.stdout}\n{proc.stderr}"
    return files


def test_reverse_order_reverses_the_collected_file_order():
    direct = _collected_file_order()
    # Blind-scan guard (rule 38): a handful of files means the collection, not the
    # option, is what broke.
    assert len(direct) > 100, f"only {len(direct)} files collected — blind scan"

    reversed_files = _collected_file_order("--reverse-order")
    assert sorted(reversed_files) == sorted(direct), (
        "--reverse-order changed WHICH files are collected, not only their order")
    assert reversed_files == list(reversed(direct)), (
        "--reverse-order must reverse the FILE order of the collected cells; got "
        f"{reversed_files[:5]} … instead of {list(reversed(direct))[:5]} …")

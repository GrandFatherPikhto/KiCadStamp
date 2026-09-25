"""measure_upgrade_on_disk.py — what the on-disk upgrade costs, in numbers (Т4/У4).

Two questions Denis wrote into «Уточнения к Т4», answered by counting rather than
by arguing:

1. **Can the number come from the LOADER's own parse?** On a cold start the file
   is parsed once by the probe and once by the loader — the plan says so and asks
   whether the second one can be avoided.
2. **What does a repeat open cost once the probe is warm?**

Every `sexp_to_dict` call is counted, so the answer is a number per load, and both
states are measured on a copy of a REAL profile: already at CURRENT (nothing to
lift) and at format 1 (one lift happens). A copy, because the lift writes.

Run: python kicadstamp/diagnostics/measure_upgrade_on_disk.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import sexp_format
from kicadstamp.config.format_version import CURRENT_FORMAT
from kicadstamp.config.loader import load_config

FIXTURE = (Path(__file__).resolve().parents[2]
           / "tests" / "fixtures" / "internal_mount" / "config.sexp")


def _load_counting_parses(root: Path) -> tuple[int, float]:
    """One `load_config`, counting every parse the whole stack performs."""
    real = sexp_format.sexp_to_dict
    calls: list[bool] = []

    def wrapper(text, *args, **kwargs):
        calls.append(bool(kwargs.get("upgrade", True)))
        return real(text, *args, **kwargs)

    sexp_format.sexp_to_dict = wrapper
    started = time.perf_counter()
    try:
        load_config(str(root))
    finally:
        sexp_format.sexp_to_dict = real
    return len(calls), (time.perf_counter() - started) * 1000.0


def _copy_of_the_fixture(state: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    text = FIXTURE.read_text(encoding="utf-8")
    if state == "format-1":
        text = text.replace(f"  (version {CURRENT_FORMAT})\n", "")
    target = directory / "config.sexp"
    target.write_text(text, encoding="utf-8")
    return target


if __name__ == "__main__":
    for state in ("current", "format-1"):
        path = _copy_of_the_fixture(state)
        print(f"== {state}: {path.stat().st_size} bytes ==")
        for run in (1, 2, 3):
            calls, ms = _load_counting_parses(path)
            print(f"   load #{run}: parses = {calls:3d}   {ms:8.1f} ms")
        print(f"   .bak next to the file: {len(list(path.parent.glob('*.bak.*')))}")

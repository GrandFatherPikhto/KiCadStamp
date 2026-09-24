#!/usr/bin/env python3
"""measure_format_version_read.py — the price of reading the root `(version N)`.

T0.F measurement, kept as the standing rig for
`plan_2026_09_24_config_format_version.md` section 4, item T0.F. NOT ship code:
nothing in the product calls it, and it only ever reads.

Kept for the numbers it produced and for the CANDIDATE it measured. The SHIPPED
reader does not use the head regex below — config/format_version.py parses the
root and takes the number out where the file is parsed (so a hand edit that
moves the node is read correctly). The regex measures the cheap probe T0 was
asked about, which keeps the `0.137 / 0.649 ms` figures recorded in the plan's
section 10 reproducible.

It answers one question with numbers: if the format number is obtained through
a targeted probe cached by `(path, mtime_ns)`, how does that price compare with

  (a) parsing EVERY file of the include: graph in full (`sexp_to_dict`), and
  (b) plain `os.stat` over the same set of files,

and how much of one real `load_config` on the same copy the probe would take.

Everything is measured on a COPY of a live profile (see the plan's rule: the
live `profiles/` park is never opened, not even for reading). The rig never
writes to the profile it is pointed at.

Usage:
    python -m kicadstamp.diagnostics.measure_format_version_read [profile/config.sexp ...]

    With no arguments it measures its own bundled copies when present:
        profiles/heating-table/config.sexp
        profiles/3ch-awg-tia-v103/config.sexp

    python -m kicadstamp.diagnostics.measure_format_version_read --g

    `--g` runs the T0.G probe: a file whose root carries `(version 2)` is
    loaded through `load_config`, then a root that INCLUDES a file carrying
    `(version 1)` is loaded (today that must be a fatal "unsupported top-level
    key").
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
from pathlib import Path

# kicadstamp/diagnostics/ -> parents[2] is the repository root (this rig lives
# inside the package now, not in the gitignored top-level diagnostics/).
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sexpdata  # noqa: E402

from kicadstamp.config.includes import walk_include_tree  # noqa: E402
from kicadstamp.config.loader import _load_config_uncached, load_config  # noqa: E402
from kicadstamp.config.sexp_format import sexp_to_dict  # noqa: E402
from kicadstamp.utils import file_cache  # noqa: E402

# The same shape take_version must accept: a bare `(version N)` node.
VERSION_RE = re.compile(r"\(\s*version\s+(\d+)\s*\)")
# A targeted read looks at the file HEAD only; the grammar pins `(version N)`
# as the FIRST child of the root, so the head is enough in practice.
PROBE_HEAD_BYTES = 4096
REPEAT = 5


def clear_caches() -> None:
    """Drop both cache layers (single-file and graph-level) so a measured
    `load_config` runs its real body instead of returning a cached result."""
    file_cache._cache.clear()
    file_cache._keys_by_path.clear()
    file_cache._graph_cache.clear()
    file_cache._graph_keys_by_path.clear()


def graph_files(root: Path) -> list[Path]:
    """Every file of the include: graph, resolved through the SHARED walker
    (not a hand-rolled one), so the number belongs to the product's own code."""
    clear_caches()
    tree = walk_include_tree(str(root))
    files: list[Path] = []

    def rec(node) -> None:
        files.append(Path(node.path))
        for child in node.children:
            rec(child)

    rec(tree)
    return files


def probe_version(path: Path, cache: dict) -> int:
    """The candidate T1 read: os.stat + (on a cold cache entry) the file head.

    Returns the number found, or 1 when there is no `(version N)` at all —
    exactly the "no key means format 1" rule. The cache key is
    (path, mtime_ns), the same identity the single-file cache uses."""
    st = os.stat(path)
    key = (str(path), st.st_mtime_ns)
    hit = cache.get(key)
    if hit is not None:
        return hit
    with open(path, "r", encoding="utf-8") as f:
        head = f.read(PROBE_HEAD_BYTES)
        match = VERSION_RE.search(head)
        if match is None and len(head) == PROBE_HEAD_BYTES:
            match = VERSION_RE.search(f.read())
    version = int(match.group(1)) if match else 1
    cache[key] = version
    return version


def _best_ms(fn, repeat: int = REPEAT) -> float:
    """Minimum wall time over `repeat` runs, in milliseconds."""
    best = None
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return (best or 0.0) * 1000.0


def measure(root: Path) -> None:
    files = graph_files(root)
    sizes = {p: p.stat().st_size for p in files}
    # Warm the OS page cache first: the numbers below must describe CPU work,
    # not who touched the disk last.
    for p in files:
        p.read_bytes()

    def full_loads() -> None:
        for p in files:
            sexpdata.loads(p.read_text(encoding="utf-8"))

    def full_parse() -> None:
        for p in files:
            sexp_to_dict(p.read_text(encoding="utf-8"))

    def probe_cold() -> None:
        cache: dict = {}
        for p in files:
            probe_version(p, cache)

    def probe_warm() -> None:
        cache: dict = {}
        for p in files:  # first pass fills the cache
            probe_version(p, cache)
        for p in files:  # second pass is the warm one
            probe_version(p, cache)

    def stat_only() -> None:
        for p in files:
            os.stat(p)

    def one_load_config() -> None:
        clear_caches()
        _load_config_uncached(str(root))

    print(f"\n===== {root} =====")
    print(f"graph files: {len(files)}")
    total_bytes = sum(sizes.values())
    for p in files:
        print(f"    {p}  ({sizes[p]} bytes)")
    print(f"    total: {total_bytes} bytes")

    t_loads = _best_ms(full_loads)
    t_parse = _best_ms(full_parse)
    t_cold = _best_ms(probe_cold)
    t_warm = _best_ms(probe_warm)
    t_stat = _best_ms(stat_only)
    t_load = _best_ms(one_load_config, repeat=3)

    print("\n  one graph pass, milliseconds (best of %d):" % REPEAT)
    print(f"    sexpdata.loads only                  {t_loads:10.3f}")
    print(f"    sexp_to_dict (parse EVERYTHING)      {t_parse:10.3f}")
    print(f"    version probe, cold cache            {t_cold:10.3f}")
    print(f"    version probe, warm cache (stat only){t_warm:10.3f}")
    print(f"    os.stat only                         {t_stat:10.3f}")
    print(f"    load_config, uncached body           {t_load:10.3f}")
    print("\n  ratios:")
    print(f"    parse_everything / probe_warm = {t_parse / t_warm:.1f}x" if t_warm else "    probe_warm == 0")
    print(f"    parse_everything / os.stat    = {t_parse / t_stat:.1f}x" if t_stat else "    os.stat == 0")
    print(f"    probe_warm / os.stat          = {t_warm / t_stat:.2f}x" if t_stat else "    os.stat == 0")
    if t_load:
        print(f"    probe_warm as a share of one load_config = {100.0 * t_warm / t_load:.3f}%")
        print(f"    parse_everything as a share of one load_config = {100.0 * t_parse / t_load:.1f}%")


def g_probe() -> None:
    """T0.G: (version 2) in the ROOT loads; (version 1) in an INCLUDED file
    is fatal today (that is the `_resolve` unsupported-top-level-key path)."""
    with tempfile.TemporaryDirectory(prefix="t0g_version_probe_") as tmp:
        base = Path(tmp)
        root_v2 = base / "v2.sexp"
        root_v2.write_text("(kicadstamp-config\n  (version 2)\n  (cells)\n)\n", encoding="utf-8")

        child_v1 = base / "child_v1.sexp"
        child_v1.write_text("(kicadstamp-config\n  (version 1)\n  (cells)\n)\n", encoding="utf-8")
        root_inc = base / "root_inc.sexp"
        root_inc.write_text('(kicadstamp-config\n  (include "child_v1.sexp")\n)\n', encoding="utf-8")

        print("\n===== T0.G probe =====")
        print("raw sexp_to_dict of a root carrying (version 2):")
        print("   ", sexp_to_dict(root_v2.read_text(encoding="utf-8")))
        clear_caches()
        try:
            cfg, _ctx = load_config(str(root_v2))
            print(f"load_config(root with (version 2)): OK (Config built, layer={cfg.layer})")
        except Exception as exc:  # noqa: BLE001 - the probe reports whatever happens
            print(f"load_config(root with (version 2)): FATAL {type(exc).__name__}: {exc}")
        clear_caches()
        try:
            load_config(str(root_inc))
            print("load_config(root INCLUDING a file with (version 1)): OK (unexpected!)")
        except Exception as exc:  # noqa: BLE001
            first = str(exc).strip().splitlines()
            detail = first[1].strip() if len(first) > 1 else str(exc).strip()
            print(f"load_config(root INCLUDING a file with (version 1)): "
                  f"FATAL {type(exc).__name__}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", type=Path,
                        help="config.sexp roots to measure (copies only!)")
    parser.add_argument("--g", action="store_true", help="run the T0.G probe instead")
    args = parser.parse_args()

    if args.g:
        g_probe()
        return 0

    roots = args.roots
    if not roots:
        defaults = [REPO_ROOT / "profiles/heating-table/config.sexp",
                    REPO_ROOT / "profiles/3ch-awg-tia-v103/config.sexp"]
        roots = [p for p in defaults if p.exists()]
    missing = [p for p in roots if not p.exists()]
    for p in missing:
        print(f"missing root: {p} (copy ONE profile INTO this tree first)", file=sys.stderr)
    roots = [p for p in roots if p.exists()]
    if not roots:
        print("nothing to measure", file=sys.stderr)
        return 2
    for root in roots:
        measure(root)

    print("\n===== GUI: how many load_config calls one startup makes =====")
    print("  source of the estimate (not a guess): "
          "tests/gui/test_dock_hub_startup_reads.py")
    print("  test_dock_hub_startup_runs_graph_bodies_at_most_twice  -> "
          "len(load_calls) <= 2 per ONE DockHub construction (the heart of MainWindow.__init__);")
    print("  its docstring records the PRE-cache measurement on a real project: "
          "load_config body 6x, walk_include_tree body 11x.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

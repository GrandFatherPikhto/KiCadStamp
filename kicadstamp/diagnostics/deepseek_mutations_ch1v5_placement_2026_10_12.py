# kicadstamp/diagnostics/deepseek_mutations_ch1v5_placement_2026_10_12.py
"""Rule-38 mutation harness for the Ч1в-5 slice (plan_2026_10_08_remove_placer,
catalog ``tests/placement``): the root ``clone_placements:`` fixtures of these
cells were translated onto live carriers (``coordinate_placements`` /
``entities:`` / nested ``Cell.clone_placements`` / the on-disk registry rig) —
this harness proves the translation did NOT defang them.

Grown from ``deepseek_mutations_ch1v4b2_config_2026_10_12.py`` (rule 38: never
start the harness from a clean sheet) — the same machinery and the SAME
protectors:

  * ``if src.count(old) != 1`` — a template that is not unique refuses to run
    (the ``anchor_sheet`` check, for instance, exists VERBATIM in both the clone
    and the coordinate loader, so the template has to include the message line);
  * a verdict with ZERO red tests is a MISS, not a kill;
  * restore is from a COPY, never ``git checkout`` (the tree holds this slice's
    uncommitted work).

Rows (each names the cell it kills):
  * M1 — the coordinate loader stops refusing ``anchor_sheet`` without
    ``anchor_role``: killed by the translated anchor cell.
  * M2 — the nested CellPlacement loader drops ``mirror``: killed by the
    layer/mirror cell (which now rides ``Cell.clone_placements``).
  * M3 — ``_load_entity`` drops ``refs``: killed by the nets/refs cell (which
    now rides ``entities:``).
  * M4 — ``registry_upgrade._map_template_name`` no longer reports an ambiguous
    template: killed by the orphan/ambiguous sweep cell.
  * C1 — a cosmetic comment. MUST survive.

Run from the repo root:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_mutations_ch1v5_placement_2026_10_12
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ENTRIES = ROOT / "kicadstamp/config/entries.py"
REGISTRY_UPGRADE = ROOT / "kicadstamp/config/registry_upgrade.py"

CONFIG_TESTS = "tests/placement/test_clone_placement_config.py"
UPGRADE_TESTS = "tests/placement/test_registry_upgrade_on_disk.py"

# (id, file, old, new, expect, target test node(s))
MUTATIONS = [
    ("M1 anchor_sheet without anchor_role is no longer refused", ENTRIES,
     "    if anchor_sheet is not None and anchor_role is None:\n"
     "        raise ValidationError(format_fatal_error(\n"
     "            _(\"coordinate_placements entry {label!r}: anchor_sheet without anchor_role\")",
     "    if False:  # MUTATION: anchor_sheet check removed\n"
     "        raise ValidationError(format_fatal_error(\n"
     "            _(\"coordinate_placements entry {label!r}: anchor_sheet without anchor_role\")",
     "die", [f"{CONFIG_TESTS}::test_anchor_sheet_without_anchor_role_raises"]),
    ("M2 nested CellPlacement drops mirror", ENTRIES,
     "        rotation_deg=data.get('rotation_deg', 0.0),\n"
     "        mirror=bool(data.get('mirror', False)),\n"
     "        layer=layer,\n"
     "        sheet=data.get('sheet'),\n"
     "        cluster=data.get('cluster'),\n"
     "        nets=data.get('nets', {}) or {},\n"
     "        params=data.get('params', {}) or {},",
     "        rotation_deg=data.get('rotation_deg', 0.0),\n"
     "        mirror=False,  # MUTATION: mirror ignored\n"
     "        layer=layer,\n"
     "        sheet=data.get('sheet'),\n"
     "        cluster=data.get('cluster'),\n"
     "        nets=data.get('nets', {}) or {},\n"
     "        params=data.get('params', {}) or {},",
     "die", [f"{CONFIG_TESTS}::test_layer_and_mirror"]),
    ("M3 _load_entity drops refs", ENTRIES,
     "        by_selection=by_selection,\n"
     "        refs=data.get('refs', {}) or {},\n"
     "        layer=layer,\n"
     "        mirror=bool(data.get('mirror', False)),\n"
     "        comment=data.get('comment'),\n"
     "        uuid=data.get('uuid'),\n"
     "        cell_uuid=data.get('cell_uuid'),\n"
     "        imprint_uuid=data.get('imprint_uuid'),",
     "        by_selection=by_selection,\n"
     "        refs={},  # MUTATION: refs ignored\n"
     "        layer=layer,\n"
     "        mirror=bool(data.get('mirror', False)),\n"
     "        comment=data.get('comment'),\n"
     "        uuid=data.get('uuid'),\n"
     "        cell_uuid=data.get('cell_uuid'),\n"
     "        imprint_uuid=data.get('imprint_uuid'),",
     "die", [f"{CONFIG_TESTS}::test_nets_and_refs"]),
    ("M4 an ambiguous template is guessed instead of kept", REGISTRY_UPGRADE,
     "    if template_name not in idx.templates:\n"
     "        return None, \"orphan\"\n"
     "    uid = idx.templates[template_name]\n"
     "    if uid is None:\n"
     "        return None, \"ambiguous\"",
     "    if template_name not in idx.templates:\n"
     "        return None, \"orphan\"\n"
     "    uid = idx.templates[template_name]\n"
     "    if False:  # MUTATION: ambiguity ignored\n"
     "        return None, \"ambiguous\"",
     "die", [f"{UPGRADE_TESTS}::test_an_orphan_and_an_ambiguous_key_are_kept_with_a_warning"]),
    ("C1 cosmetic comment (control)", REGISTRY_UPGRADE,
     "def map_registry_key(key: str, idx: _NameIndex) -> tuple[str, str | None]:",
     "def map_registry_key(key: str, idx: _NameIndex) -> tuple[str, str | None]:  # control",
     "survive", [f"{UPGRADE_TESTS}::test_an_orphan_and_an_ambiguous_key_are_kept_with_a_warning"]),
]


def _drop_pyc(path: Path) -> None:
    """Drop the bytecode of the MUTATED file — a same-second mtime with stale
    bytecode is the classic rig trap (rule 38)."""
    for cache in path.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{path.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def _run(tests) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--tb=no", *tests],
        cwd=str(ROOT), capture_output=True, text=True, env=env)


def main() -> int:
    backups: dict[Path, Path] = {}
    for path in {m[1] for m in MUTATIONS}:
        bak = path.with_suffix(path.suffix + ".mutbak")
        shutil.copy(path, bak)
        backups[path] = bak
    failures: list[str] = []
    try:
        for mid, path, old, new, expect, targets in MUTATIONS:
            src = path.read_text(encoding="utf-8")
            count = src.count(old)
            if count != 1:
                failures.append(f"{mid}: template count {count} != 1")
                continue
            path.write_text(src.replace(old, new), encoding="utf-8")
            _drop_pyc(path)
            proc = _run(targets)
            red = proc.returncode != 0
            if expect == "die" and not red:
                failures.append(f"{mid}: MISS — no target cell went red")
            elif expect == "survive" and red:
                failures.append(f"{mid}: control was killed:\n{proc.stdout[-2000:]}")
            else:
                print(f"{mid}: {'KILLED' if red else 'survived'} "
                      f"({len(targets)} target(s))")
            path.write_text(src, encoding="utf-8")
            _drop_pyc(path)
    finally:
        for path, bak in backups.items():
            path.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            bak.unlink(missing_ok=True)
            _drop_pyc(path)
    if failures:
        print("\n".join(failures))
        return 1
    print("all mutations behaved as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# kicadstamp/diagnostics/deepseek_mutations_tree_reread_modules_2026_10_05.py
"""Acceptance mutations for plan_2026_10_05_tree_reread_modules.md (the
inter-node copper re-read sees `kind "module"` nodes, a NEW record is anchored on
the side it redraws from, and the copper INSIDE one module is that module's, not
the parent's), DeepSeek, 2026-10-05; extended for Т4 on 2026-10-07.

Grown from deepseek_mutations_refresh_mixed_2026_10_05.py (rule 38): the same
machinery — basename-resolved T under tests/, `_drop_pyc` for the mutated file,
the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing red
came back, a refused filter that matches no row, and a control that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/net/test_internode_capture_modules.py
(Т3 cells 1-7 + the Т4-1/Т4-2/Т4-3/Т4-4 cells) and tests/net/test_internode_capture.py
(the no-module regression + the Т2 addendum cells). Every row maps to a line of
the plan's own mutation list.

  * M1  module walk disabled                     -> cells 1-2 red
  * M2  node identity = label, not (cluster,sheet)-> cell 2 red
  * M3  anchor = always pads[0]                  -> cell 3 red
  * M4  anchor check WITH the selection step     -> cell 4 red
  * M5  no cycle guard                           -> cell 6 red (recursion)
  * M6  unsuitable piece raises instead of skip  -> cell 5 red
  * M7  role checked only on pads[0]             -> role-less-not-first red
  * M8  unknown-node pad BLOCKS the unit         -> cell (а) red
  * M9  module copper taken by the parent        -> module cell red (Т4-1)
  * M10 two different modules -> module          -> between-modules cell red (Т4-1)
  * M11 A2 narrowed-to-one is enough             -> chain-between-channels red (Т4-2)
  * M12 A4 module's own children not walked      -> own-child cell red (Т4-2)
  * M13 A6 fallback from pads[0]                 -> mid-via cell red (Т4-2)
  * M14 T4-3 module label without the sheet      -> three-channels cell red (Т4-3)
  * M15 T4-4 cache only the candidates           -> cascade-once cell red (Т4-4)
  * M16 T5-1 selection takes CLUSTER too         -> select-exactly-bridge red (Т5-1)
  * M17 T5-1 node key = label (channels merge)   -> another-instance cell red (Т5-1)
  * M18 T5-2 B2 skips the second record          -> missing-record cell red (Т5-2)
  * M19 T5-1 B1's classifier diverges from read  -> invariant cell red (Т5-1)
  * M20 T5-1 worker without a finally: close()   -> close-both-paths cell red (Т5-1)
  * K1  a cosmetic comment                       -> MUST survive

Run with the main checkout's interpreter; point it at another tree with
`KICADSTAMP_ACCEPT_ROOT`. An optional row-name prefix filter takes the rest of
argv.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))


def _interpreter() -> str:
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    env = os.environ.get("KICADSTAMP_PYTHON")
    return env if env else sys.executable


PY_BIN = _interpreter()

GUARDS = ["test_internode_capture_modules.py",
          "test_internode_capture.py",
          "test_select_internode_copper.py"]

NODES = "kicadstamp/internode_nodes.py"
CAPTURE = "kicadstamp/internode_capture.py"
COPPER = "kicadstamp/internode_copper.py"
SELECT = "gui/select_internode_copper.py"

MUTATIONS = [
    ("M1 module walk disabled", NODES,
     '            if node.kind == "module":',
     '            if False:  # MUTATION',
     "die", GUARDS, ()),
    ("M2 node identity = label", CAPTURE,
     "        out.node_key_by_ref[comp.ref] = key",
     "        out.node_key_by_ref[comp.ref] = label  # MUTATION",
     "die", GUARDS, ()),
    ("M3 anchor = always pads[0]", NODES,
     "    failed: list[str] = []\n    for pad in unit.pads:",
     "    return unit.pads[0], None  # MUTATION\n"
     "    failed: list[str] = []\n    for pad in unit.pads:",
     "die", GUARDS, ()),
    ("M4 anchor check WITH the selection step", NODES,
     "            list(candidates), self._adapter, set(),",
     "            list(candidates), self._adapter,\n"
     "            {i.ref for i in self._adapter.get_selected_items()},  # MUTATION",
     "die", GUARDS, ()),
    ("M5 no cycle guard", NODES,
     "                if nested is not None and nested.name not in seen:",
     "                if nested is not None:  # MUTATION",
     "die", GUARDS, ()),
    ("M6 unsuitable piece raises instead of skip", NODES,
     '    return None, AnchorSkip("no_candidate", roles=tuple(failed))',
     '    raise RuntimeError("MUTATION")  # MUTATION',
     "die", GUARDS, ()),
    ("M7 role checked only on pads[0]", NODES,
     "    for pad in unit.pads:\n"
     "        comp = components.get(pad.ref)\n"
     "        if comp is None or not comp.role:\n"
     '            return None, AnchorSkip("no_role", pad=pad)',
     "    for pad in unit.pads[:1]:  # MUTATION\n"
     "        comp = components.get(pad.ref)\n"
     "        if comp is None or not comp.role:\n"
     '            return None, AnchorSkip("no_role", pad=pad)',
     "die", GUARDS, ()),
    ("M8 unknown-node pad BLOCKS the unit", NODES,
     "    if not any(p.ref in node_sheet_by_ref for p in unit.pads):",
     "    if not all(p.ref in node_sheet_by_ref for p in unit.pads):  # MUTATION",
     "die", GUARDS, ()),
    # ── Т4 (2026-10-07) ────────────────────────────────────────────────────
    ("M9 module copper taken by the parent", COPPER,
     "        if module_owner_by_ref is not None:",
     "        if False:  # MUTATION",
     "die", GUARDS, ()),
    ("M10 two different modules -> module", COPPER,
     "            if (len(owner_ids) == 1\n"
     "                    and module_owner_by_ref.get(unit.pads[0].ref) is not None):",
     "            if module_owner_by_ref.get(unit.pads[0].ref) is not None:  # MUTATION",
     "die", GUARDS, ()),
    ("M11 A2 narrowed-to-one is enough", NODES,
     "        return len(narrowed) == 1 and narrowed[0].ref == expected_ref",
     "        return len(narrowed) == 1  # MUTATION",
     "die", GUARDS, ()),
    ("M12 A4 module own children not walked", NODES,
     "                # A module node's OWN children are ordinary nodes of THIS tree.\n"
     "                walk(node.children, owner)\n"
     "                continue",
     "                continue  # MUTATION",
     "die", GUARDS, ()),
    ("M13 A6 fallback from pads[0]", CAPTURE,
     "    tracks, vias = _items_from_unit(adapter, unit, components, point, "
     "anchor_pad,",
     "    anchor_pad = unit.pads[0]  # MUTATION\n"
     "    tracks, vias = _items_from_unit(adapter, unit, components, point, "
     "anchor_pad,",
     "die", GUARDS, ()),
    ("M14 T4-3 module label without the sheet", NODES,
     '        return f"{cluster}/{sheet}" if sheet else label',
     "        return label  # MUTATION",
     "die", GUARDS, ()),
    ("M15 T4-4 cache only the candidates", NODES,
     "        cached = self._narrow_cache.get(key)",
     "        cached = None  # MUTATION",
     "die", GUARDS, ()),
    # ── Т5 (2026-10-07) ────────────────────────────────────────────────────
    ("M16 T5-1 selection takes CLUSTER too", CAPTURE,
     "        if verdict is not CopperVerdict.INTERNODE:",
     "        if False:  # MUTATION",
     "die", GUARDS, ()),
    ("M17 T5-1 node key = label (channels merge)", NODES,
     '                    key = (getattr(entity, "cluster", None),\n'
     '                           getattr(entity, "sheet", None))',
     '                    key = (getattr(entity, "cluster", None), None)  # MUTATION',
     "die", GUARDS, ()),
    ("M18 T5-2 B2 skips the second record", SELECT,
     "        for nt in records:",
     "        for nt in records[:1]:  # MUTATION",
     "die", GUARDS, ()),
    ("M19 T5-1 B1 classifier diverges from read", CAPTURE,
     "    return classification.units, classification.discarded, \\\n"
     "        classification.warnings",
     "    return ([u for u in classification.units if len(u.pads) >= 3],  # MUTATION\n"
     "            classification.discarded,\n"
     "            classification.warnings)",
     "die", GUARDS, ()),
    ("M20 T5-1 worker without finally close", SELECT,
     '            "node_keys": ([] if units\n'
     '                          else _node_key_labels(payload["tree"], payload["cfg"])),\n'
     "        }\n"
     "    finally:\n"
     "        if adapter is not None:\n"
     "            adapter.close()",
     '            "node_keys": ([] if units\n'
     '                          else _node_key_labels(payload["tree"], payload["cfg"])),\n'
     "        }\n"
     "    finally:\n"
     "        if adapter is not None:\n"
     "            pass  # MUTATION",
     "die", GUARDS, ()),
    ("K1 cosmetic comment (control)", COPPER,
     '    MODULE = "module"',
     '    MODULE = "module"  # control',
     "survive", GUARDS, ()),
]


def _resolve_tests(basenames):
    """[repo-relative paths] for `basenames`, resolved under ROOT/tests.

    Refuses (SystemExit) when a name is not found, or is found TWICE (rule 38)."""
    found, problems = [], []
    for name in basenames:
        matches = sorted((ROOT / "tests").rglob(name))
        if len(matches) == 1:
            found.append(matches[0].relative_to(ROOT).as_posix())
        else:
            problems.append(f"{name}: {len(matches)} match(es)")
    if problems:
        raise SystemExit(
            "the acceptance rig cannot resolve its test list under tests/ — "
            "refusing to run with a blind or ambiguous T: " + "; ".join(problems))
    return found


def _drop_pyc(rel: str) -> None:
    """Drop the bytecode of the MUTATED file (the stale-.pyc trap, rule 38)."""
    f = ROOT / rel
    for cache in f.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{f.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def run(paths, extra=()):
    """(returncode, output); returncode None means the run did not finish."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q",
                            "--no-header", "-p", "no:cacheprovider", "-n", "auto"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return r.returncode, r.stdout + r.stderr


def main():
    only = sys.argv[1:]
    rows = [row for row in MUTATIONS
            if not only or any(row[0].startswith(prefix) for prefix in only)]
    if only and not rows:
        raise SystemExit(f"no mutation row matches {only} — nothing was measured")
    print(f"корень: {ROOT}")
    print(f"строк: {len(rows)} из {len(MUTATIONS)}")
    print(f"{'мутация':<44} {'ожидал':<9} {'вердикт':<16} что покраснело")
    print("-" * 125)
    for name, rel, old, new, expect, tests, extra in rows:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<44} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} "
                  f"шаблон встречается {n} раз")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            _drop_pyc(rel)
            code, out = run(_resolve_tests(tests), extra)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code is None:
                verdict, detail = "ЗАВИСЛА", "прогон не уложился в 300 с"
            elif code == 0:
                verdict, detail = "ВЫЖИЛА", "ни один сторож не покраснел"
            elif not failed or "no tests ran" in out:
                verdict, detail = "ПРОМАХ", f"ноль красных при выходе {code}"
            else:
                verdict = "УБИТА"
                detail = f"{len(failed)}: " + "; ".join(
                    l.split(" ")[1].split("::")[-1] for l in failed[:4])
            flag = ""
            if expect == "die" and verdict != "УБИТА":
                flag = "  <<< НАХОДКА: ждали смерти"
            if expect == "survive" and verdict == "УБИТА":
                flag = "  <<< НАХОДКА: ждали выживания"
            print(f"{name:<44} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            f.write_text(original, encoding="utf-8")
            _drop_pyc(rel)


if __name__ == "__main__":
    main()

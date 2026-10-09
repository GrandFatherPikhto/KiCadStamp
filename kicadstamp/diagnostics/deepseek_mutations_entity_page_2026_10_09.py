# kicadstamp/diagnostics/deepseek_mutations_entity_page_2026_10_09.py
"""Acceptance mutations for plan_2026_10_09_entity_page.md, ШАГИ 1-5
(страница сущности: перенос в gui/entity/page.py + комбобокс ячейки на потоке
change_cell_flow; ШАГ 2 — Explode на странице сущности; ШАГ 3 — таб «Refs»;
ШАГ 4 — таб «Anchor»; ШАГ 5 — адрес как АРГУМЕНТ: док без адреса не читает
плату, хранилище-откат мёртв), 2026-10-09.

Grown from deepseek_mutations_cells_entities_part1_2026_10_09.py (rule 38): the
SAME machinery — basename-resolved tests under tests/, `_drop_pyc` for the
mutated file, the `original.count(old) != 1` refusal (a non-unique template is a
MISS, not a kill), a verdict of «ПРОМАХ» when nothing red came back, and a
control that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/gui/docks/test_entity_page_cell_combo.py
(the combobox page) and tests/gui/docks/test_entity_page.py (the moved record
editor).

  * M1  комбобокс пишет своей записью мимо apply_cell_change  -> обход потока
  * M2  текущая неподходящая ячейка не серая (enabled)      -> выбор неверной
  * M3  нет снимка -> подбор включается (orphan снят)        -> список сжался
  * M4  текущая ячейка не выбрана (pos всегда 0)            -> чужая текущая
  * M5  неподходящие снова в списке (choose_cells)          -> чужие в списке
  * M6  текущая ячейка не помечена (current=False)          -> текущей нет
  * M7  open_tab снова открывает страницу ЯЧЕЙКИ             -> не та страница
  * M8  адрес Explode не от сущности (cluster=None)         -> чужой адрес
  * M9  таб Refs не добавлен на страницу сущности           -> таба нет
  * M10 адрес Refs не от ячейки ЗАПИСИ (cell=None)          -> ролей нет
  * M11 записи снимка не доходят до таба                    -> колонки пусты
  * M12 stop reload_overrides не перецелен                  -> чужая копия
  * M13 хук записи таба не поднят                           -> владельцы молчат
  * M14 отказ чужому кластеру снят (Read from selection)    -> чужой адрес прошёл
  * M15 таб Anchor не добавлен на страницу сущности         -> таба нет
  * M16 адрес Anchor не от записи сущности (cell=None)      -> адрес пуст
  * M17 CellDock без адреса читает плату                    -> адрес не спрошен
  * M18 откат к хранилищу без адреса                        -> чужой адрес
  * M19 push не пересчитывает страницу (Д1)                 -> старый вердикт
  * K1  cosmetic comment (control)                          -> MUST survive
  * K2  cosmetic comment в _sync_refs_tab (control)         -> MUST survive
  * K3  cosmetic comment в add_anchor_tab (control)         -> MUST survive
  * K4  cosmetic comment в read_instance_or_report (control) -> MUST survive
  * K5  cosmetic comment в _refresh_cell_state (control)    -> MUST survive

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

COMBO = ["test_entity_page_cell_combo.py", "test_entity_page.py"]
PART2 = ["test_change_cell.py", "test_entities_under_cells.py"]
# The cells that actually exercise a non-fitting OTHER cell (the combo page and
# the pure rule) — PART2 alone has no such cell, so M5 would survive there.
CHOICE = ["test_instance_candidates.py", "test_entity_page_cell_combo.py"]
EXPLODE = ["test_explode_page.py"]
# ШАГ 3: the Refs table's own guards plus the dashboard that pins the whole hub
# wiring; the store inventory is derived from the source (test_overrides_store_
# reload.py) and driven end to end (test_overrides_store_reload_gui.py).
REFS = ["test_entity_page_refs_tab.py", "test_cell_refs_tab.py"]
REFS_WIRED = ["test_entity_page_refs_tab.py", "test_phase3_wiring.py"]
STORE_CHAIN = ["test_overrides_store_reload.py", "test_overrides_store_reload_gui.py"]
# ШАГ 4: the ENTITY page's "Anchor" tab — the extracted Role + Marker anchor.
ANCHOR = ["test_anchor_tab.py"]
# ШАГ 5 (Т5-2/Т5-3): the ADDRESS is an ARGUMENT a door hands over; a dock with NO
# address reads NOTHING, and the deleted working-instance store never answers.
ADDRESS = ["test_entities_part3_address.py"]
# Д1: push — это ПЕРЕСЧЁТ страницы (комбобокс + read-only) по ПРИШЕДШЕМУ снимку.
REFRESH = ["test_entity_page_read_only.py", "test_entity_page_cell_combo.py"]

PAGE = "gui/entity/page.py"
FLOW = "gui/docks/change_cell_flow.py"
PURE = "gui/docks/instance_candidates.py"
WIRING = "gui/explode_wiring.py"
HUB = "gui/dock_hub.py"
ANCHOR_FILE = "gui/entity/anchor_tab.py"
ADDR = "gui/entity/address.py"

MUTATIONS = [
    ("M1 комбобокс пишет мимо apply_cell_change", PAGE,
     "        apply_cell_change(hub, self._entity_data, self._entity_file, chosen)\n",
     "        from pathlib import Path as _P  # MUTATION\n"
     "        from ..docks._common import upsert_list_entry as _up  # MUTATION\n"
     "        _d = dict(self._entity_data)  # MUTATION\n"
     "        _d[\"cell\"] = chosen  # MUTATION\n"
     "        _up(_P(self._entity_file), \"entities\", _d,\n"
     "            key_fn=lambda e: e.get(\"name\"))  # MUTATION\n",
     "die", COMBO, ()),
    ("M2 текущая неподходящая не серая", PAGE,
     "            if item is not None and not (cand.fits or choices.orphan):\n",
     "            if False:  # MUTATION\n",
     "die", COMBO, ()),
    ("M3 нет снимка -> подбор не раскрывается", FLOW,
     "    if not instance:\n",
     "    if False:  # MUTATION: no snapshot no longer offers every cell\n",
     "die", COMBO, ()),
    ("M4 текущая ячейка не выбрана", PAGE,
     "        pos = combo.findData(current) if current else -1\n",
     "        pos = 0  # MUTATION\n",
     "die", COMBO, ()),
    ("M5 неподходящие снова в списке", PURE,
     "        if cand.fits or cand.current:\n",
     "        if True:  # MUTATION\n",
     "die", CHOICE, ()),
    ("M6 текущая ячейка не помечена", PURE,
     "                             reason=reason, current=(cell.name == current))\n",
     "                             reason=reason, current=False)  # MUTATION\n",
     "die", COMBO, ()),
    ("M7 open_tab открывает не ту страницу", WIRING,
     "            hub.config_tree_dock.show_page(entity_page)\n",
     "            hub.config_tree_dock.show_page(hub._points_page)  # MUTATION\n",
     "die", EXPLODE, ()),
    ("M8 адрес Explode не от сущности", PAGE,
     '        self._explode_page.set_context(\n'
     '            raw.get("cell"), raw.get("cluster"), raw.get("sheet"),\n',
     '        self._explode_page.set_context(\n'
     '            raw.get("cell"), None, raw.get("sheet"),  # MUTATION\n',
     "die", EXPLODE, ()),
    ("M9 таб Refs не добавлен на страницу сущности", PAGE,
     '        self.tabs.addTab(widget, _("Refs"))\n',
     "        pass  # MUTATION: the tab is never added\n",
     "die", REFS, ()),
    ("M10 адрес Refs не от ячейки ЗАПИСИ", PAGE,
     "        cell_name = (self._entity_data or {}).get(\"cell\")\n",
     "        cell_name = None  # MUTATION\n",
     "die", REFS, ()),
    ("M11 записи снимка не доходят до таба", PAGE,
     "        return records_from_items(self._resolved_snapshot or self._board_snapshot())\n",
     "        return records_from_items([])  # MUTATION\n",
     "die", REFS_WIRED, ()),
    ("M12 stop reload_overrides не перецелен", HUB,
     "                        self.entity_dock.reload_overrides)\n",
     "                        self.fieldstool_dock.window.reload_overrides)  # MUTATION\n",
     "die", STORE_CHAIN, ()),
    ("M13 хук записи таба не поднят", PAGE,
     "        widget.on_overrides_written = self._on_refs_written\n",
     "        pass  # MUTATION: the store-write half is not wired\n",
     "die", REFS, ()),
    ("K1 cosmetic comment (control)", PAGE,
     "        self._cell_orphan = choices.orphan\n",
     "        self._cell_orphan = choices.orphan  # control\n",
     "survive", COMBO, ()),
    ("K2 cosmetic comment в _sync_refs_tab (control)", PAGE,
     "        if self._refs_tab is None:\n",
     "        if self._refs_tab is None:  # control\n",
     "survive", REFS, ()),
    # ── ШАГ 4: the Anchor tab extracted to the ENTITY page ────────────────
    ("M14 отказ чужому кластеру снят", ANCHOR_FILE,
     '        if self._cluster and read["cluster"] \\\n'
     '                and not cluster_prefix_match(read["cluster"], self._cluster):\n',
     "        if False:  # MUTATION\n",
     "die", ANCHOR, ()),
    ("M15 таб Anchor не добавлен на страницу", PAGE,
     '        self.tabs.addTab(widget, _("Anchor"))\n',
     "        pass  # MUTATION: the anchor tab is never added\n",
     "die", ANCHOR, ()),
    ("M16 адрес Anchor не от записи сущности", PAGE,
     '        self._anchor_tab.set_context(\n'
     '            raw.get("cell"), raw.get("cluster"), raw.get("sheet"),\n',
     '        self._anchor_tab.set_context(\n'
     '            None, raw.get("cluster"), raw.get("sheet"),  # MUTATION\n',
     "die", ANCHOR, ()),
    ("K3 cosmetic comment в add_anchor_tab (control)", PAGE,
     "        self._anchor_tab = widget\n",
     "        self._anchor_tab = widget  # control\n",
     "survive", ANCHOR, ()),
    # ── ШАГ 5 (Т5-2/Т5-3): no address — no board read ────────────────────
    ("M17 CellDock без адреса читает плату", ADDR,
     "    if address is not None:\n"
     "        return read_instance_of(address), None\n",
     "    if True:  # MUTATION: reads even with no address\n"
     "        return read_instance_of(address), None\n",
     "die", ADDRESS, ()),
    ("M18 откат к хранилищу без адреса", ADDR,
     "    address = expected_address if expected_address is not None else dock_address\n",
     "    address = expected_address if expected_address is not None else dock_address\n"
     "    if address is None:  # MUTATION: the removed store answers a pair\n"
     "        address = entity_address({\"cluster\": \"REMEMBERED\"})\n",
     "die", ADDRESS, ()),
    ("K4 cosmetic comment (control)", ADDR,
     "def read_instance_or_report(expected_address, dock_address, verb: str):\n",
     "def read_instance_or_report(expected_address, dock_address, verb: str):  # control\n",
     "survive", ADDRESS, ()),
    # ── Д1: push — ПЕРЕСЧЁТ по пришедшему снимку ─────────────────────────
    ("M19 push не пересчитывает страницу", PAGE,
     "        self._refresh_cell_state()\n",
     "        pass  # MUTATION: the push no longer re-judges the page\n",
     "die", REFRESH, ()),
    ("K5 cosmetic comment в _refresh_cell_state (control)", PAGE,
     "        self._fill_cell_combo(self._snapshot)\n",
     "        self._fill_cell_combo(self._snapshot)  # control\n",
     "survive", REFRESH, ()),
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

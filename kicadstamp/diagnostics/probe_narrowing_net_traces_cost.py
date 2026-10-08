#!/usr/bin/env python3
# kicadstamp/diagnostics/probe_narrowing_net_traces_cost.py
"""Замер п.0 плана ``plan_2026_10_08_narrowing_net_traces_cost.md`` (08.10.2026).

Задание: «Update from selection» читает медь всей платы по разу на КАЖДУЮ запись
``net_traces:`` (``_net_trace_owned`` → ``find_live_copper`` →
``match_net_trace_pieces`` → ``adapter.get_vias() + adapter.get_tracks()``), а
вместе с ней — роль-якоря каждой записи. Замер отвечает на вопрос «сколько» и
служит «до/после» для частей А (одно чтение на сужение, фильтр по цепям),
Б («Select cell» по текущему месту) и Д (одно действие = одно чтение конфига).

ДВА РЕЖИМА (оба ничего не пишут в продукт и не трогают ``profiles/``):

1. Счётчики за одно сужение::

       .venv/bin/python kicadstamp/diagnostics/probe_narrowing_net_traces_cost.py \
           --config profiles/3ch-awg-tia-v103/config.sexp [--cell fpga_pwr_spoke] \
                                                          [--entity fpga_vccio_139]

   Профиль КОПИРУЕТСЯ в собственный временный каталог ВНЕ ``profiles/``
   (``.kicadstamp-probe-*`` рядом с проектом, той же ГЛУБИНЫ: ссылки профиля
   отсчитываются от каталога конфига, и каталог из ``mktemp -d`` увёл бы их в
   ``/`` — проверено, FATAL ERROR «schematic_files … not found»). Удаляется
   только этот каталог. Затем на СЧИТАЮЩЕМ двойнике платы исполняется ОДНО
   ``narrow_mixed_selection`` (тот же путь, что у «Update from selection»), и
   печатается, сколько раз позваны ``get_vias`` / ``get_tracks`` /
   ``get_footprints``, ``find_live_copper``, ``plan_net_traces`` и
   ``_net_trace_owned``.

   ЧЕСТНО про числа: двойник отдаёт пустую медь, поэтому ГЛУБИНА резолва ролей
   (строки ``role_narrowing``) и величина одного чтения (в живом логе —
   «Retrieved 219 vias / 1256 tracks») здесь не воспроизводятся: первое требует
   живой платы, второе берётся из живого ``actions.log``. Замер считает то, что
   чинит часть А — ЧИСЛО чтений: было 4×N записей (via и дорожки × вычитание и
   перенос), должно стать 1. ``--board-items 219,1256`` подставляет величину
   живого чтения, чтобы показать и стоимость в предметах.

2. Разбор живого лога::

       .venv/bin/python kicadstamp/diagnostics/probe_narrowing_net_traces_cost.py \
           --log profiles/3ch-awg-tia-v103/logs/actions.log \
           --from 19:40:30 --to 19:41:50

   Потоковый (лог весит сотни МБ) счёт строк одного нажатия: «Retrieved N vias /
   tracks» (их сумма — цена одного полного чтения меди), ``net_traces entry``,
   строки сужения (``narrowed … by anchor_sheet|anchor_cluster|current selection|
   physical proximity``), ``Loading config``, «upgrade on disk skipped». Это «до»
   для частей А и Д; после правки тот же режим по свежему логу даёт «после».

Зонд — инструмент приёмки, не продукт: его никто не импортирует.
"""
from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

# ── общий счётчик ───────────────────────────────────────────────────────────

class Counters:
    """Счётчик вызовов и строк Лога — печатается как таблица «до/после»."""

    def __init__(self) -> None:
        self.calls: Counter = Counter()
        self.info_by_logger: Counter = Counter()

    def add(self, name: str, amount: int = 1) -> None:
        self.calls[name] += amount

    def report(self) -> list[str]:
        out = ["вызовов за ОДНО сужение:"]
        for name in sorted(self.calls):
            out.append(f"  {name:<28} {self.calls[name]}")
        if self.info_by_logger:
            out.append("INFO-строк Лога по логгерам:")
            for name in sorted(self.info_by_logger):
                out.append(f"  {name:<45} {self.info_by_logger[name]}")
        return out


class _InfoCounter(logging.Handler):
    """Считает INFO-записи по имени логгера (это то, что видел Денис в Логе)."""

    def __init__(self, counters: Counters) -> None:
        super().__init__(level=logging.INFO)
        self._counters = counters

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        if record.levelno == logging.INFO:
            self._counters.info_by_logger[record.name] += 1


# ── двойник платы ───────────────────────────────────────────────────────────

def _board_items(kind: str, size: int) -> list:
    """``size`` пустышек с одним ``uuid`` — столько предметов живое чтение меди
    отдаёт в реальном профиле (числа из живого лога). Предметы нужны только как
    размер СПИСКА: сопоставление по ним не идёт (у якорей нет живой геометрии)."""
    if size <= 0:
        return []
    return [SimpleNamespace(uuid=f"{kind}-{i}") for i in range(size)]


class CountingAdapter:
    """Двойник адаптера: считает КАЖДЫЙ вызов чтения платы.

    Ничего не изобретает: ``get_vias`` / ``get_tracks`` отдают список нужного
    размера, ``get_footprints`` — синтетическую деталь экземпляра, по которой
    сужение выбирает инстанс. ``__getattr__`` даёт пустую заглушку, чтобы
    глубокий резолв ролей падал в СВОЙ штатный обработчик («якорь не
    резолвится»), а не в AttributeError чужой формы."""

    def __init__(self, counters: Counters, footprints, fields, selected,
                 board_vias: int, board_tracks: int) -> None:
        self._counters = counters
        self._footprints = list(footprints)
        self._fields = dict(fields)
        self._selected = list(selected)
        self._board_vias = board_vias
        self._board_tracks = board_tracks

    # считаемые чтения платы
    def get_vias(self):
        self._counters.add("get_vias")
        self._counters.add("get_vias:items", self._board_vias)
        return _board_items("via", self._board_vias)

    def get_tracks(self):
        self._counters.add("get_tracks")
        self._counters.add("get_tracks:items", self._board_tracks)
        return _board_items("track", self._board_tracks)

    def get_footprints(self):
        self._counters.add("get_footprints")
        return list(self._footprints)

    def get_selected_items(self):
        self._counters.add("get_selected_items")
        return list(self._selected)

    def get_field_value(self, fp, name):
        self._counters.add("get_field_value")
        return self._fields.get((getattr(fp, "ref", None), name))

    def get_footprint(self, ref):
        self._counters.add("get_footprint")
        return next((f for f in self._footprints
                     if getattr(f, "ref", None) == ref), None)

    def __getattr__(self, name):
        def _stub(*_args, **_kwargs):
            self._counters.add(f"{name}:stub")
            return None

        return _stub


# ── режим 1: счётчики за одно сужение ───────────────────────────────────────

def _copy_profile(config_path: Path) -> tuple[Path, Path]:
    """``(копия config, её временный корень)`` — копия в СВОЁМ временном каталоге.

    ``profiles/`` не трогается вообще (правило Дениса 08.10:
    ``profiles/3ch-awg-tia-v103-copy`` — существующий каталог из списка «не
    трогать»), и живой профиль не трогается даже лифтом формата на диске: копия
    ОБЯЗАТЕЛЬНА. Ставится она на ТУ ЖЕ ГЛУБИНУ, что и исходный профиль: его
    ссылки отсчитываются от каталога конфига (``schematic_files:
    ../../../../KiCad/...``), и каталог из ``mktemp -d`` увёл бы этот подъём в
    ``/`` (проверено: FATAL ERROR «schematic_files … not found»).

    Логи и бэкапы не копируются: они весят гигабайты. Удаляется только этот
    каталог (``shutil.rmtree`` в :func:`run_counters`)."""
    profile = config_path.resolve().parent
    anchor = profile.parents[3]      # .../<Projects>: тот же «подъём на 4 уровня»
    temp_root = Path(tempfile.mkdtemp(prefix=".kicadstamp-probe-", dir=anchor))
    dest = temp_root / "w" / "profiles" / profile.name
    dest.parent.mkdir(parents=True)
    shutil.copytree(profile, dest, symlinks=True,
                    ignore=shutil.ignore_patterns("logs", "*.bak*", "*.bad"))
    return dest / config_path.name, temp_root


def _sheet_names(ctx, counters: Counters) -> dict:
    """Карта листов конфига — БЕЗ фатала: схема проекта может лежать вне
    досягаемости копии, а замер считает ЧТЕНИЯ платы, и для этого листовая карта
    не нужна (она нужна сужению — и то в меру разрешённости)."""
    try:
        return dict(getattr(ctx, "sheet_names", {}) or {})
    except Exception as e:  # noqa: BLE001 — незачем ронять замер из-за схемы
        counters.add("sheet_names:unavailable")
        print(f"(!) карта листов недоступна "
              f"({' '.join(str(e).split())[:120]}…) — для счёта чтений не нужна")
        return {}


def _instance_of(cfg, cell: str | None, entity: str | None):
    """``(cell, cluster, sheet)`` экземпляра для замера: из названной сущности,
    иначе — первая сущность с ячейкой. None-поля допустимы (sheet)."""
    entities = list(getattr(cfg, "entities", ()) or ())
    for e in entities:
        if entity and getattr(e, "name", None) == entity:
            return (getattr(e, "cell", None), getattr(e, "cluster", None),
                    getattr(e, "sheet", None))
    for e in entities:
        if getattr(e, "cell", None):
            return (getattr(e, "cell", None), getattr(e, "cluster", None),
                    getattr(e, "sheet", None))
    return (cell, None, None)


def _synthetic_board(cell_roles, cluster, net):
    """``(footprints, fields, selected)`` — выделение из ОДНОЙ детали экземпляра,
    одной via и одной дорожки на цепи первой записи ``net_traces``.

    Цепь берётся у записи, чтобы фильтр части А2 (запись, чья разрешённая цепь не
    среди цепей выделения, пропускается) НЕ спрятал замер: интересно, сколько
    записей планируется, когда выделение МОЖЕТ принадлежать одной из них."""
    from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
    from kicadstamp.domain.board import Footprint
    from kicadstamp.domain.geometry import BoardLayer, Vector2

    fp = Footprint(ref="U1", uuid="fp-u1", position=Vector2.from_xy_mm(0.0, 0.0),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = ()
    role = next(iter(sorted(cell_roles)), None)
    fields = {("U1", ROLE_FIELD_NAME): role, ("U1", CLUSTER_FIELD_NAME): cluster}
    selected = [SimpleNamespace(uuid="sel-via", net_name=net),
                SimpleNamespace(uuid="sel-track", net_name=net)]
    return [fp], fields, selected


def run_counters(args) -> int:
    import gui.mixed_selection as ms          # narrow_mixed_selection - его хозяин
    import kicadstamp.net_trace_planner as ntp
    import kicadstamp.selection_narrowing as sn

    counters = Counters()
    handler = _InfoCounter(counters)
    root_logger = logging.getLogger()
    # INFO-строки считаются только когда логгер их вообще пропускает: у корневого
    # уровень по умолчанию WARNING, а GUI держит INFO/DEBUG — ставим как в GUI.
    previous_level = root_logger.level
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    temp_root = None
    try:
        # Копия — ВСЕГДА: лифт формата на диске иначе тронул бы живой профиль.
        root, temp_root = _copy_profile(Path(args.config))
        from kicadstamp.config import load_config
        cfg, ctx = load_config(str(root))
        sheet_names = _sheet_names(ctx, counters)

        cell, cluster, sheet = _instance_of(cfg, args.cell, args.entity)
        cell_obj = (getattr(cfg, "cells", {}) or {}).get(cell)
        cell_roles = {getattr(c, "role", None)
                      for c in (getattr(cell_obj, "components", ()) or ())}
        cell_roles.discard(None)
        net_traces = list(getattr(cfg, "net_traces", ()) or ())
        net = str(getattr(net_traces[0], "net", "") or "") if net_traces else "N"

        footprints, fields, selected = _synthetic_board(cell_roles, cluster, net)
        adapter = CountingAdapter(counters, footprints, fields, selected,
                                  args.board_items[0], args.board_items[1])

        # Считаем и сами точки входа: сколько РАЗ залазят за медью платы.
        orig_flc, orig_pnt = ntp.find_live_copper, ntp.plan_net_traces
        orig_owned = sn._net_trace_owned

        def _counted(name, fn):
            def _call(*a, **k):
                counters.add(name)
                return fn(*a, **k)

            return _call

        ntp.find_live_copper = _counted("find_live_copper", orig_flc)
        ntp.plan_net_traces = _counted("plan_net_traces", orig_pnt)
        sn._net_trace_owned = _counted("_net_trace_owned", orig_owned)
        try:
            prelude = ms.narrow_mixed_selection(
                config_path=str(root), adapter=adapter,
                footprints=list(footprints),
                vias=[selected[0]], tracks=[selected[1]], cfg=cfg,
                sheet_names=sheet_names, cell_name=cell, cell_roles=cell_roles,
                remembered_cluster=cluster, remembered_sheet=sheet)
        finally:
            ntp.find_live_copper, ntp.plan_net_traces = orig_flc, orig_pnt
            sn._net_trace_owned = orig_owned

        verdict = "None — не по кластеру"
        if prelude is not None:
            verdict = "отказ" if prelude.refusal else "результат"
        print(f"профиль: {args.config}")
        print(f"копия:   {root} (удаляется целиком: {temp_root})")
        print(f"экземпляр: cell={cell!r} cluster={cluster!r} sheet={sheet!r}")
        print(f"net_traces в конфиге: {len(net_traces)}")
        print(f"цепь выделения: {net!r}")
        print(f"сужение вернуло: {verdict}")
        print()
        for line in counters.report():
            print(line)
        reads = counters.calls["get_vias"] + counters.calls["get_tracks"]
        print()
        print(f"ИТОГ: чтений меди платы за одно нажатие — {reads} "
              f"(get_vias {counters.calls['get_vias']}, "
              f"get_tracks {counters.calls['get_tracks']})")
        if args.board_items[0] or args.board_items[1]:
            items = (counters.calls["get_vias:items"]
                     + counters.calls["get_tracks:items"])
            print(f"      предметов прочитано — {items}")
        return 0
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(previous_level)
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


# ── режим 2: разбор живого лога ─────────────────────────────────────────────

_LOG_PATTERNS = (
    ("retrieved vias", re.compile(r"Retrieved (\d+) vias")),
    ("retrieved tracks", re.compile(r"Retrieved (\d+) tracks")),
    ("net_traces entry", re.compile(r"net_traces entry \(net ")),
    ("narrowing (role)", re.compile(r"narrowed to .* by (anchor_sheet|anchor_cluster"
                                    r"|current selection|physical proximity)")),
    ("loading config", re.compile(r"Loading config")),
    ("upgrade on disk skipped", re.compile(r"(format|registry schema) upgrade on "
                                           r"disk skipped")),
)
_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")


def run_log(args) -> int:
    counts: Counter = Counter()
    named: Counter = Counter()
    with open(args.log, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stamp = _STAMP.search(line)
            if stamp is None:
                continue
            day, clock = stamp.group(1), stamp.group(2)
            # Лог растёт днями: без даты окно «19:40» поймало бы и прошлые дни,
            # а `break` по времени обрывал проход на первой же СТАРОЙ строке —
            # так окно и оставалось пустым (проверено 08.10).
            if args.date and day != args.date:
                if day > args.date:
                    break
                continue
            if args.begin and clock < args.begin:
                continue
            if args.end and clock > args.end:
                if args.date:
                    break
                continue
            for name, pattern in _LOG_PATTERNS:
                match = pattern.search(line)
                if match is None:
                    continue
                counts[name] += 1
                if match.groups() and match.group(1).isdigit():
                    counts[name + " (sum)"] += int(match.group(1))
                # Кто писал — имя логгера стоит третьим полем записи.
                parts = line.split(" - ", 2)
                if len(parts) == 3:
                    logger_name = parts[2].split(" ", 1)[0]
                    named[f"{name} <- {logger_name}"] += 1
    print(f"лог: {args.log}")
    print(f"окно: {args.date or '(любой день)'} {args.begin or '-'} … {args.end or '-'}")
    for name in sorted(counts):
        print(f"  {name:<32} {counts[name]}")
    if named:
        print("кто писал (логгер):")
        for name in sorted(named):
            print(f"  {name:<72} {named[name]}")
    return 0


# ── режим 3: сколько чтений конфига за ОДНО обновление после записи ─────────

def _patch_everywhere(real, wrapper) -> list:
    """Подменяет функцию ВО ВСЕХ модулях, которые её уже импортировали.

    `from X import f` связывает имя в момент импорта, поэтому подмена только
    `X.f` не видна ни одному такому модулю. Возвращает ``[(модуль, имя), ...]`` —
    список мест, чтобы зонд вернул их как было."""
    import sys

    patched = []
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            name = real.__name__
            if getattr(mod, name, None) is real:
                setattr(mod, name, wrapper)
                patched.append((mod, name))
        except Exception:  # noqa: BLE001 — чужой модуль не повод падать
            continue
    return patched


def _counting(real, counters, name):
    """Счётчик вызовов + ОДНА строка «кто зовёт»: первый кадр ЗА ПРЕДЕЛАМИ
    самого читателя (сам читатель и файловый кэш в стеке не интересны)."""
    import traceback

    def _wrapped(*args, **kwargs):
        counters.add(name)
        frames = [f for f in traceback.extract_stack()[:-1]
                  if f.filename != real.__code__.co_filename]
        if frames:
            f = frames[-1]
            counters.add(f"{name} <- {Path(f.filename).name}:{f.lineno} {f.name}")
        return real(*args, **kwargs)

    _wrapped.__name__ = real.__name__
    return _wrapped


def run_fanout(args) -> int:
    """Сколько раз за ОДНО обновление интерфейса после записи (как его соединяет
    DockHub: `ConfigTreeDock.refresh()` + `graph_changed.emit()`) зовутся
    ``load_config`` / ``walk_include_tree`` / ``collect_section_entries`` — и КТО
    зовёт (одна строка стека на место вызова).

    Платы здесь не нужно: обновление доков после записи плату не читает. Профиль
    КОПИРУЕТСЯ во временный каталог (тот же `_copy_profile`), рабочий набор
    делается ГРЯЗНЫМ — это и есть живая ситуация Дениса: правка в памяти, на диск
    не записана."""
    import copy
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    counters = Counters()
    root, temp_root = _copy_profile(Path(args.config))
    try:
        app = QApplication.instance() or QApplication([])
        from gui.main_window import MainWindow

        window = MainWindow(timeout_ms=10, verbose=False)
        window._timer.stop()
        window._selection_timer.stop()
        patched: list = []
        real_fns: list = []
        try:
            window.root_metadata_dock.set_root_file(Path(root))

            # ГРЯЗНЫЙ рабочий набор: правка уходит в память, файл не тронут.
            from kicadstamp.config_working_set import WORKING_SET
            from kicadstamp.config_writer import _read_data
            data = copy.deepcopy(_read_data(Path(root)))
            data["_probe_marker"] = "dirty"
            WORKING_SET.enabled = True
            WORKING_SET.stage_write(Path(root), data)

            from kicadstamp.config import includes as includes_mod
            from kicadstamp.config import loader as loader_mod
            from gui.docks import rename as rename_mod

            for real, name in ((loader_mod.load_config, "load_config"),
                               (includes_mod.walk_include_tree, "walk_include_tree"),
                               (rename_mod.collect_section_entries,
                                "collect_section_entries")):
                real_fns.append(real)
                patched.extend(_patch_everywhere(
                    real, _counting(real, counters, name)))

            window.config_tree_dock.refresh()
            window.config_tree_dock.graph_changed.emit()
        finally:
            # Возврат подменённых имён — по одному модулю за раз, в обратном
            # порядке: в модуле мог оказаться и наш же счётчик.
            for mod, name in reversed(patched):
                for real in real_fns:
                    if real.__name__ == name:
                        setattr(mod, name, real)
            window._timer.stop()
            window._selection_timer.stop()
            window._poll_worker.stop()
            window.log_dock.remove_handler()
        print(f"профиль: {args.config}")
        print(f"копия:   {root}")
        print(f"рабочий набор грязный: {WORKING_SET.is_dirty()}")
        print()
        for line in counters.report():
            print(line)
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Замер п.0: чтения меди и чтения конфига на одно нажатие")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="живой профиль (копируется во временный)")
    source.add_argument("--log", help="живой actions.log для потокового разбора")
    # Режим, а не источник: --fanout идёт ВМЕСТЕ с --config.
    parser.add_argument("--fanout", action="store_true",
                        help="режим 3: сколько чтений конфига за одно обновление — "
                             "вместе с --config")
    parser.add_argument("--cell", default=None, help="ячейка экземпляра замера")
    parser.add_argument("--entity", default="fpga_vccio_139",
                        help="сущность экземпляра замера (cluster/sheet из неё)")
    parser.add_argument("--board-items", default="219,1256",
                        help="vias,tracks в одном живом чтении (из живого лога)")
    parser.add_argument("--date", default=None,
                        help="YYYY-MM-DD: без него окно берёт все дни")
    parser.add_argument("--from", dest="begin", default=None, help="HH:MM:SS")
    parser.add_argument("--to", dest="end", default=None, help="HH:MM:SS")
    args = parser.parse_args(argv)
    parts = [x for x in str(args.board_items or "").split(",") if x.strip()]
    args.board_items = (tuple(int(x) for x in parts) if len(parts) == 2 else (0, 0))
    if args.log:
        return run_log(args)
    if args.fanout:
        return run_fanout(args)
    return run_counters(args)


if __name__ == "__main__":
    sys.exit(main())

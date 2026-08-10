#!/usr/bin/env python3
"""
downgrade_nightly_sch.py — переводит *.kicad_sch, случайно засохранённые
KiCad nightly, обратно в формат stable, чтобы их снова открывал 10.0.5.

Основано на разборе 2026-08-09 (см.
techdocs/handoff/handoff_2026_08_09_nightly_sch_downgrade.md): между
eeschema 10.0 и 10.99 (на момент разбора) сменилась только шапка файла
(version/generator_version) плюс безобидное переформатирование отступов —
модель данных схемы не менялась. Патчим две строки в шапке и валидируем
результат стабильным kicad-cli (парсит всю иерархию — если бы модель
данных правда разошлась, netlist export упал бы с ошибкой).

ВАЖНО: это работает для *.kicad_sch, НЕ для *.kicad_pcb. У платы между
10.0 и nightly сменилась сама модель хранения геометрии футпринтов
((at x y) -> (transform (translate ...) (rotate ...))) — правкой шапки
такое не починить, нужен пересчёт геометрии.

Запуск (из корня репозитория):
  python tools/downgrade_nightly_sch.py boards/MyProject
  python tools/downgrade_nightly_sch.py boards/MyProject/fpga.kicad_sch
  python tools/downgrade_nightly_sch.py boards/MyProject --dry-run
  python tools/downgrade_nightly_sch.py boards/MyProject --no-verify
  python tools/downgrade_nightly_sch.py boards/MyProject --no-backup

Если в новой ситуации версии другие (nightly ушёл вперёд ещё дальше) —
подставь актуальные через --from-version/--from-generator-version и
--to-version/--to-generator-version (посмотреть можно в шапке любого
*.kicad_sch, который точно открывался в нужной stable-версии).
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Версии из инцидента 2026-08-09: KiCad nightly 10.99 (build e8929c86b9) vs stable 10.0.5.
DEFAULT_FROM_VERSION = "20260722"
DEFAULT_FROM_GENERATOR_VERSION = "10.99"
DEFAULT_TO_VERSION = "20260306"
DEFAULT_TO_GENERATOR_VERSION = "10.0"


def collect_files(target: Path) -> List[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.glob("*.kicad_sch"))
    sys.exit(f"[ошибка] не найдено: {target}")


def needs_patch(text: str, from_version: str, from_generator_version: str) -> bool:
    return (f'(version {from_version})' in text
            and f'(generator_version "{from_generator_version}")' in text)


def patch_text(text: str, from_version: str, from_generator_version: str,
                to_version: str, to_generator_version: str) -> str:
    text = text.replace(f'(version {from_version})', f'(version {to_version})', 1)
    text = text.replace(f'(generator_version "{from_generator_version}")',
                         f'(generator_version "{to_generator_version}")', 1)
    return text


def guess_root_sheet(directory: Path) -> Optional[Path]:
    """Корневой лист схемы обычно называется как файл проекта."""
    pro_files = list(directory.glob("*.kicad_pro"))
    if len(pro_files) == 1:
        candidate = pro_files[0].with_suffix(".kicad_sch")
        if candidate.is_file():
            return candidate
    candidate = directory / f"{directory.name}.kicad_sch"
    return candidate if candidate.is_file() else None


def verify_with_kicad_cli(root_sheet: Path) -> bool:
    if shutil.which("kicad-cli") is None:
        print("[предупреждение] kicad-cli не найден в PATH — пропускаю проверку "
              "(поставь stable kicad-cli, если хочешь автоматическую валидацию)")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "verify.net"
        result = subprocess.run(
            ["kicad-cli", "sch", "export", "netlist", "--output", str(out), str(root_sheet)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print("[ошибка] stable kicad-cli не смог собрать netlist после правки:")
            print(result.stderr)
            return False
        print(f"[ok] stable kicad-cli собрал netlist без ошибок ({out.stat().st_size} байт)")
        return True


def main():
    ap = argparse.ArgumentParser(
        description="Перевести *.kicad_sch из nightly-формата обратно в stable (правкой шапки)")
    ap.add_argument("target", help="Файл *.kicad_sch или директория проекта")
    ap.add_argument("--dry-run", action="store_true", help="Только показать, что было бы изменено")
    ap.add_argument("--no-backup", action="store_true", help="Не сохранять .bak-копии перед правкой")
    ap.add_argument("--no-verify", action="store_true",
                     help="Не проверять результат через stable kicad-cli (netlist export)")
    ap.add_argument("--from-version", default=DEFAULT_FROM_VERSION)
    ap.add_argument("--from-generator-version", default=DEFAULT_FROM_GENERATOR_VERSION)
    ap.add_argument("--to-version", default=DEFAULT_TO_VERSION)
    ap.add_argument("--to-generator-version", default=DEFAULT_TO_GENERATOR_VERSION)
    args = ap.parse_args()

    target = Path(args.target)
    files = collect_files(target)
    if not files:
        sys.exit(f"[ошибка] *.kicad_sch не найдены в {target}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    patched = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if not needs_patch(text, args.from_version, args.from_generator_version):
            print(f"[пропуск] {path} — шапка не совпадает с ожидаемой nightly-версией "
                  f"({args.from_version}/{args.from_generator_version}); "
                  f"либо уже stable, либо другая версия nightly")
            continue

        if args.dry_run:
            print(f"[dry-run] изменил бы: {path}")
            continue

        if not args.no_backup:
            backup = path.with_name(f"{path.name}.bak.{timestamp}")
            shutil.copy2(path, backup)
            print(f"[бэкап] {backup}")

        path.write_text(
            patch_text(text, args.from_version, args.from_generator_version,
                       args.to_version, args.to_generator_version),
            encoding="utf-8",
        )
        print(f"[patched] {path}")
        patched.append(path)

    if args.dry_run or not patched:
        return

    if args.no_verify:
        return

    directory = target if target.is_dir() else target.parent
    root_sheet = guess_root_sheet(directory)
    if root_sheet is None:
        print("[предупреждение] не удалось угадать корневой лист схемы для проверки — "
              "проверь вручную (открой проект в stable KiCad)")
        return
    verify_with_kicad_cli(root_sheet)


if __name__ == "__main__":
    main()

# kicadstamp/config_stage_report.py
"""«Куда ушла запись — и что об этом сказать»: ОДНО правило исхода записи
конфига (часть В плана ``plan_2026_10_08_narrowing_net_traces_cost``).

Вынесено из ``kicadstamp/config_writer.py`` по правилу 45 (файл у своего потолка
— в нём остаётся только проводка): само правило не изменилось, а
``config_writer`` ре-экспортирует оба имени, чтобы доки и клетки продолжали
импортировать их оттуда же.

  * ``is_staged_write`` — запись ушла в РАБОЧИЙ НАБОР (её сбросит глобальный
    Save) или легла на диск;
  * ``write_report_line`` — ФОРМУЛИРОВКА этого исхода: своя строка дока
    («Wrote'/'Overwrote 'x' in <file>») для НАСТОЯЩЕЙ записи и честное «Staged …
    — not saved yet (File → Save)», пока правку держит рабочий набор. Денис
    19:40:39 прочитал «Overwrote … in <file>» при нетронутом mtime файла — эту
    ложь правило и убирает.
"""
from __future__ import annotations

from pathlib import Path

from kicadstamp.i18n import _

from .config_working_set import WORKING_SET

__all__ = ["is_staged_write", "write_report_line"]


def is_staged_write(path) -> bool:
    """True ⇔ this file's new content went into the WORKING SET, not to disk.

    The GUI keeps every edit in the working set while a project is open and writes
    the files only on the global Save (config_working_set.py), so a writer CANNOT
    know from its own return value whether the bytes reached the disk."""
    return bool(WORKING_SET.enabled
                and WORKING_SET.is_staged(str(Path(path).resolve())))


def write_report_line(physical: str, subject: str, path, *, created: bool) -> str:
    """THE wording rule of a config write's outcome (part В of
    plan_2026_10_08_narrowing_net_traces_cost).

    `physical` is the dock's own sentence for a REAL write ("Wrote 'x' in <file>"
    / "Overwrote 'x' in <file>") — returned unchanged, so the CLI paths and every
    existing wording stay as they are. While the working set holds the change the
    SAME rule says so instead, because "Overwrote ... in <file>" was a lie there:
    the user read it as "saved" while the file's mtime never moved (the live
    19:40:39 line, right before Denis asked "похоже, ничего не записывается"):

      * ``created=True``  -> "Staged new <subject> — not saved yet (File → Save)";
      * ``created=False`` -> "Staged changes to <subject> — not saved yet
        (File → Save)".

    `subject` is the identity the caller already names (its quoted form, e.g.
    ``'dac0'``). ONE place decides, so no dock can drift from another."""
    if not is_staged_write(path):
        return physical
    return (_("Staged new {subject} — not saved yet (File → Save)") if created
            else _("Staged changes to {subject} — not saved yet (File → Save)")
            ).format(subject=subject)

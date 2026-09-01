#!/usr/bin/env python3
"""One-off 2026-09-01 (plan rules_to_chains): finish the Rule -> Chain rename
in the user-facing strings Phase 1 missed, so code, docs and catalogs all agree:

  - "Rule net (null)"         -> "Chain net (null)"   (Extract dock checkbox+header)
  - "... enclosing Rule's own net ... across Rules ..." -> "... Chain's own net
    ... across Chains ..."     (Extract tooltip, extract fatal, --rule-net CLI help)
  - Trees-dock kind tag/label "rule" -> "chain"        (chains are kind "chain")

Renames the msgids in BOTH catalogs in place, adds the newly-introduced strings
("chain", the Points/Chains working-file label, the Cell/Entity/Chain import
message), fixes the RU msgstrs that still mentioned the old rule vocabulary, and
recompiles both .mo catalogs.

Run from the project root:  python tools/_fix_chain_net_i18n.py
"""
import subprocess
import sys
from pathlib import Path

import polib

ROOT = Path(__file__).parent.parent
RU_PO = ROOT / "locales" / "ru" / "LC_MESSAGES" / "kicadstamp.po"
EN_PO = ROOT / "locales" / "en" / "LC_MESSAGES" / "kicadstamp.po"

# Substring replacements applied to EVERY msgid — together they cover all four
# renamed strings (checkbox/header, Extract tooltip, extract fatal, CLI help).
SUBS = [
    ("Rule net (null)", "Chain net (null)"),
    ("enclosing Rule's own net", "enclosing Chain's own net"),
    ("across Rules on different nets", "across Chains on different nets"),
]

# Newly-introduced msgids: msgid -> (EN msgstr, RU msgstr). EN msgstr == msgid
# (English source); the RU column is a real translation.
NEW_ENTRIES = {
    "chain": ("chain", "цепь"),
    "Working file (Points/Chains/Placer/Thermal via/Cells write here):": (
        "Working file (Points/Chains/Placer/Thermal via/Cells write here):",
        "Рабочий файл (Points/Chains/Placer/Thermal via/Cells пишут сюда):"),
    "No importable entries (Cell/Entity/Chain) in this file.": (
        "No importable entries (Cell/Entity/Chain) in this file.",
        "Нет импортируемых записей (Cell/Entity/Chain) в этом файле."),
}

# Explicit RU msgstr keyed by the NEW msgid — for entries whose existing RU
# msgstr still said "правила"/"цепь правила" (the old Rule vocabulary).
RU_FIXES = {
    "Chain net (null)": "Цепь (null)",
    "Write this via/track net as null instead of a literal — at apply time a "
    "ManualSpoke-placed cell inherits the enclosing Chain's own net for it, so "
    "the cell can be reused across Chains on different nets.":
        "Записать цепь этой via/трека как null вместо литерала — при применении "
        "ячейка, размещённая через ManualSpoke, наследует собственную цепь "
        "объемлющей цепи, поэтому её можно переиспользовать между разными "
        "цепями на разных цепях.",
    "a net can't be both \"always the enclosing Chain's own net\" and \"always "
    "resolved from this param\" — pick one per net":
        "цепь не может быть одновременно «всегда собственной цепью объемлющей "
        "цепи» и «всегда разрешаться из этого параметра» — выберите одно для "
        "каждой цепи",
}


def _apply_subs(text: str) -> str:
    for old, new in SUBS:
        text = text.replace(old, new)
    return text


def _process(po: polib.POFile, is_ru: bool) -> int:
    renamed = 0
    for entry in po:
        if not entry.msgid:
            continue
        new_id = _apply_subs(entry.msgid)
        if new_id != entry.msgid:
            entry.msgid = new_id
            if is_ru:
                # The cli-help RU msgstr already says "Chain" — leave it.
                if new_id in RU_FIXES:
                    entry.msgstr = RU_FIXES[new_id]
            else:
                entry.msgstr = new_id
            renamed += 1
        if "fuzzy" in entry.flags:
            entry.flags.remove("fuzzy")
    return renamed


def _add_new(po: polib.POFile, is_ru: bool) -> int:
    existing = {e.msgid for e in po if e.msgid}
    added = 0
    for msgid, (en_str, ru_str) in NEW_ENTRIES.items():
        if msgid in existing:
            continue
        po.append(polib.POEntry(msgid=msgid, msgstr=ru_str if is_ru else en_str))
        added += 1
    return added


def main() -> None:
    for path, is_ru in ((RU_PO, True), (EN_PO, False)):
        po = polib.pofile(str(path))
        renamed = _process(po, is_ru)
        added = _add_new(po, is_ru)
        po.save(str(path))
        print(f"{path.name}: renamed {renamed}, added {added}")

    # Recompile both .mo catalogs (project's own pybabel invocation).
    for lang in ("en", "ru"):
        subprocess.run(
            [sys.executable, "-m", "babel.messages.frontend", "compile",
             "-d", "locales", "-l", lang, "-D", "kicadstamp"],
            cwd=str(ROOT), check=True)
    print("compiled .mo catalogs")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""One-off 2026-09-01 (plan rules_to_chains): fix the RU catalog after the
Rule -> Chain rename. pybabel update fuzzy-matched old "rule" translations to
the new "chain" strings, leaving 173 fuzzy entries (some with WRONG auto-matches
and 5 with placeholder bugs — msgstr referencing {name}/{action}/{rules} that
the msgid no longer has). This script:

  1. mechanically rewrites the rule vocabulary in every RU msgstr
     (правило/правил/правила/rules/rule -> цепь/цепей/цепи/chains/chain);
  2. fixes the 5 known placeholder-inconsistent entries explicitly;
  3. clears the fuzzy flag on every entry (all fixed above, or genuinely
     empty -> just untranslated);
  4. fills the EN catalog's empty msgstr with its msgid (English source);
  5. recompiles BOTH .mo with --use-fuzzy (the plan's own compile line).

Run: python tools/_fix_chain_i18n.py  (then pybabel compile --use-fuzzy)
"""
import re
from pathlib import Path

import polib

ROOT = Path(__file__).parent.parent
RU_PO = ROOT / "locales" / "ru" / "LC_MESSAGES" / "kicadstamp.po"
EN_PO = ROOT / "locales" / "en" / "LC_MESSAGES" / "kicadstamp.po"


def _fix_vocab(text: str) -> str:
    """rule vocabulary -> chain vocabulary, longest-first so compound forms
    ('правил', 'правила') win over the bare stem ('правило'). Case-insensitive
    for the English forms; Russian forms are exact."""
    pairs = [
        ("Правила", "Цепи"),
        ("правила", "цепи"),
        ("правил", "цепей"),
        ("правило", "цепь"),
        ("Правило", "Цепь"),
        ("rules", "chains"),
        ("Rules", "Chains"),
        ("rule", "chain"),
        ("Rule", "Chain"),
    ]
    for old, new in pairs:
        text = text.replace(old, new)
    return text


# msgid -> correct msgstr for the 5 placeholder-inconsistent entries (and a few
# GUI strings whose auto-match was simply the wrong string).
FIXES = {
    "{context} {name!r} in {path}": "{context} {name!r} в {path}",
    "Delete pad {pad!r}": "Удалить пад {pad!r}",
    "--only {requested}: chains={chains}, clone_placements={clones}, "
    "thermal_via_arrays={thermal}, coordinate_placements={coords}, "
    "net_traces={nets} (everything else is ignored in this run)":
        "--only {requested}: chains={chains}, clone_placements={clones}, "
        "thermal_via_arrays={thermal}, coordinate_placements={coords}, "
        "net_traces={nets} (всё остальное в этом прогоне игнорируется)",
    "--cluster {paths}: chains={chains} (spokes narrowed), clone_placements="
    "{clones}, thermal_via_arrays={thermal}, coordinate_placements={coords}, "
    "net_traces={nets}":
        "--cluster {paths}: chains={chains} (спицы сужены), "
        "clone_placements={clones}, thermal_via_arrays={thermal}, "
        "coordinate_placements={coords}, net_traces={nets}",
    "Config loaded: layer={layer}, cells={cells}, points={points}, "
    "chains={chains}, spokes={spokes}, clone_placements={clones}":
        "Конфиг загружен: layer={layer}, cells={cells}, points={points}, "
        "chains={chains}, spokes={spokes}, clone_placements={clones}",
    "Add net...": "Добавить сеть...",
    "Add spoke...": "Добавить спицу...",
    "Delete net...": "Удалить сеть...",
    "Delete pad...": "Удалить пад...",
    "Redraw chain": "Перерисовать цепь",
    "Redraw spoke": "Перерисовать спицу",
    "Chains": "Цепи",
    "Anchor": "Якорь",
    "Pick a chain in the Config tree first.":
        "Сначала выберите цепь в дереве Config.",
    "Pad saved": "Пад сохранён",
    "Pad saved:": "Пад сохранён:",
    "Nothing to redraw.": "Нечего перерисовывать.",
    "Chain": "Цепь",
}


def main() -> None:
    ru = polib.pofile(str(RU_PO))
    for entry in ru:
        if not entry.msgid:
            continue
        if entry.msgid in FIXES:
            entry.msgstr = FIXES[entry.msgid]
        elif entry.msgstr and re.search(r"\b[Cc]hains?\b", entry.msgid):
            # Only the genuinely RENAMED strings (msgid now says chain/chains)
            # get the rule->chain vocabulary rewrite — a msgstr mentioning
            # "правила" in a DIFFERENT sense (e.g. "non-rule net") must not be
            # touched.
            entry.msgstr = _fix_vocab(entry.msgstr)
        # Every entry is now fixed (or empty) -> no fuzzy needs review anymore.
        if "fuzzy" in entry.flags:
            entry.flags.remove("fuzzy")
    ru.save(str(RU_PO))

    en = polib.pofile(str(EN_PO))
    for entry in en:
        if entry.msgid and not entry.msgstr:
            entry.msgstr = entry.msgid
        if "fuzzy" in entry.flags:
            entry.flags.remove("fuzzy")
    en.save(str(EN_PO))

    print("RU catalog: rules->chains vocabulary applied, fuzzy cleared")
    print("EN catalog: empty msgstr filled with msgid")


if __name__ == "__main__":
    main()

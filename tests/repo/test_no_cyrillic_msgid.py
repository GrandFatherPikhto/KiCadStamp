# tests/repo/test_no_cyrillic_msgid.py
"""Р2а-4 regression lock: a translatable source string `_(...)` must be ENGLISH.

The project's i18n contract (deepseek.md §9 / claude.md §16) is msgid ENGLISH,
the Russian text in `locales/ru`. A Russian msgid would render the interface in
Russian for an English user and can never be translated.

The rule is a run of TWO OR MORE Cyrillic letters inside `_("...")`. A single
Cyrillic letter is allowed on purpose: existing English msgids carry plan
markers like "(Ф4)", "(Т6)", "(Р43)".
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_CYRILLIC_WORD = re.compile(r'_\("(?:[^"\\]|\\.)*[\u0400-\u04FF]{2,}')


def _offenders() -> list:
    bad = []
    for top in ("gui", "kicadstamp", "mcp_server"):
        for path in sorted((REPO_ROOT / top).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if _CYRILLIC_WORD.search(line):
                    bad.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
    return bad


def test_no_russian_translatable_source_strings():
    bad = _offenders()
    assert not bad, (
        "translatable source strings must be ENGLISH (msgid English, the Russian "
        "text belongs in locales/ru). Found Russian words in _():\n" + "\n".join(bad))

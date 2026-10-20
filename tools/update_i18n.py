#!/usr/bin/env python
"""
Обновление файлов перевода gettext.
Запускать из корня проекта: python tools/update_i18n.py
Требуется установленный Babel (pip install babel).
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent

# Замороженные архивы (files/, old/, ...) — не наш код, сканировать не нужно;
# у pybabel extract нет ключа в babel.cfg для этого, только --ignore-dirs.
# --ignore-dirs ПОЛНОСТЬЮ заменяет дефолт (".* ._"), не дополняет его — поэтому
# дефолтные шаблоны дописаны явно, чтобы не потерять их.
# tests (2026-10-20): a test's `_("...")` is an ASSERTION about a product string,
# not a product string. Extracting them put test-only msgids into both catalogs,
# and `pybabel update` then fuzzy-matched them onto neighbouring PRODUCT entries
# ("Fix the config" got the translation of "Failed to load the config."), which
# broke tests/utils/test_i18n.py's duplicate/placeholder guards. The catalogs
# must describe the SHIPPED source only — the same scope test_i18n.py checks.
IGNORE_DIRS = [".*", "._", "files", "old", "arch", "test_sample", "venv", "tests"]


def pybabel(args):
    """Run the babel CLI through THIS interpreter (sys.executable), never a
    PATH-resolved ``pybabel`` binary.

    FIXED 2026-08-29: ``subprocess.run(["pybabel", ...])`` resolved to the
    system /usr/bin/pybabel (2.10.3), whose ``--ignore-dirs`` mishandled the
    space-separated patterns — a full run then scanned ``.venv`` and
    ``tools/old`` and polluted the catalog with site-packages junk. Running the
    interpreter's own babel (``babel.messages.frontend``) pins the version and
    behaviour to what ``pip install babel`` put in the venv, so ``--ignore-dirs``
    is honoured as documented.
    """
    return subprocess.run([sys.executable, "-m", "babel.messages.frontend", *args],
                          cwd=str(ROOT), check=True)


def main():
    # 1. Извлечение строк в .pot
    pybabel(["extract", "-F", "babel.cfg",
             "--ignore-dirs", " ".join(IGNORE_DIRS),
             "-o", "messages.pot", "."])

    # 2. Обновление/создание .po для каждого языка
    for lang in ("en", "ru"):
        po_file = ROOT / "locales" / lang / "LC_MESSAGES" / "kicadstamp.po"
        if po_file.exists():
            pybabel(["update", "-i", "messages.pot", "-d", "locales",
                     "-l", lang, "-D", "kicadstamp"])
        else:
            pybabel(["init", "-i", "messages.pot", "-d", "locales",
                     "-l", lang, "-D", "kicadstamp"])

    # 3. Компиляция .mo.
    # --use-fuzzy (2026-10-20): `pybabel update` marks a NEWLY extracted or
    # CHANGED msgid as fuzzy when it guesses a translation, and plain `compile`
    # SILENTLY DROPS fuzzy entries — so the .po/.mo pair went out of sync and
    # tests/utils/test_i18n.py::test_compiled_ru_mo_is_in_sync_with_po failed
    # (three entries extracted from test files after a re-extraction). Compiling
    # them is what the accept gates have always assumed; the guess is still
    # marked fuzzy in the .po, so a human pass can find it.
    for lang in ("en", "ru"):
        pybabel(["compile", "-d", "locales", "-l", lang, "-D", "kicadstamp",
                 "--use-fuzzy"])

    # 4. Удаление временного .pot
    (ROOT / "messages.pot").unlink(missing_ok=True)
    print("✅ Переводы обновлены.")


if __name__ == "__main__":
    main()

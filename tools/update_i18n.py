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
IGNORE_DIRS = [".*", "._", "files", "old", "arch", "test_sample", "venv"]


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

    # 3. Компиляция .mo
    for lang in ("en", "ru"):
        pybabel(["compile", "-d", "locales", "-l", lang, "-D", "kicadstamp"])

    # 4. Удаление временного .pot
    (ROOT / "messages.pot").unlink(missing_ok=True)
    print("✅ Переводы обновлены.")


if __name__ == "__main__":
    main()

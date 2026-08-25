# kicadstamp/i18n.py
import os
import sys
import gettext
import locale
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
# Source checkout: <repo>/locales. Installed package: <package>/locales (if
# locales are ever shipped as package-data — see pyproject.toml). Prefer the
# first candidate that actually exists on disk.
_LOCALE_CANDIDATES = (ROOT_DIR / "locales", Path(__file__).parent / "locales")
LOCALE_DIR = next((p for p in _LOCALE_CANDIDATES if p.is_dir()), _LOCALE_CANDIDATES[0])

# Global translation function, installed by setup_i18n().
_ = gettext.gettext  # fallback

def detect_language() -> str:
    """
    Detects the language from environment variables, or the system locale on
    Windows as a fallback.

    Precedence follows the standard POSIX/gettext order for message language
    (LANGUAGE is a gettext extension, takes priority over the LC_* family;
    LC_ALL overrides all other LC_* categories; LC_MESSAGES is the POSIX
    category specifically for message language, more precise than the
    catch-all LANG): LANGUAGE > LC_ALL > LC_MESSAGES > LANG. Deliberately
    simple binary rule, not full multi-language: if none of these are set, or
    none of them start with 'ru', the result is English.
    """
    # 1. LANGUAGE is a colon-separated priority list (gettext extension);
    #    only the first entry matters for our binary ru/non-ru choice.
    language_env = os.environ.get("LANGUAGE")
    if language_env:
        first = language_env.split(":")[0]
        if first:
            return "ru" if first.startswith("ru") else "en"

    # 2. Standard POSIX locale variables, in their real precedence order.
    lang_env = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
                or os.environ.get("LANG"))
    if lang_env:
        return "ru" if lang_env.startswith("ru") else "en"

    # 3. Windows fallback (none of the above are set) — Windows normally
    #    doesn't populate LANG/LC_* itself, unlike a POSIX shell.
    if sys.platform == "win32":
        try:
            import ctypes
            # LCID for Russian = 1049 (0x419)
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if lcid == 1049:
                return "ru"
        except Exception:
            pass
        try:
            loc = locale.getlocale()[0]  # (language_code, encoding)
            if loc and loc.startswith("ru"):
                return "ru"
        except Exception:
            pass

    # 4. Default: English.
    return "en"


def setup_i18n():
    """
    Initializes gettext: picks the language, loads the translation, installs
    _() globally. Returns the selected language code.
    """
    global _
    lang = detect_language()
    try:
        translation = gettext.translation(
            "kicadstamp",
            localedir=str(LOCALE_DIR),
            languages=[lang],
            fallback=True,
        )
    except Exception:
        translation = gettext.NullTranslations()
    translation.install()
    _ = translation.gettext  # _ is now available as a module-level global
    return lang
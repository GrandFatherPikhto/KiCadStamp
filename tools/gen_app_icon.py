#!/usr/bin/env python3
"""gen_app_icon.py — regenerate gui/app_icon.py (the embedded application
icon) from images/kicadstamp.ico.

2026-08-20 (requested live: "embed the icon into the app itself so it shows
at startup"): the window / Windows-taskbar / tray icon is base64-embedded
directly into Python source so it renders without depending on the images/
folder existing on disk (robust to any CWD and to frozen builds). Run this
tool to refresh the embedded copy whenever images/kicadstamp.ico changes;
the generated gui/app_icon.py is the single source of truth for the app icon.

Run from the repo root:
    .venv\\Scripts\\python.exe tools/gen_app_icon.py
"""
import base64
import pathlib
import textwrap

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ICO = REPO_ROOT / "images" / "kicadstamp.ico"
OUT_PATH = REPO_ROOT / "gui" / "app_icon.py"

_HEADER = '''\
# gui/app_icon.py
"""
Embedded application icon - images/kicadstamp.ico base64-encoded directly
into the source (2026-08-20, requested live: "embed the icon into the app
itself so it shows at startup"). The app shows the REAL icon at startup -
window, Windows taskbar, and the system tray - without depending on the
images/ folder existing on disk (robust to any CWD and to frozen builds).
This module is the single source of truth for the app icon; both
kicadstamp_gui.py's setWindowIcon and gui/main_window.py's tray icon load
from it. Regenerate with tools/gen_app_icon.py whenever images/kicadstamp.ico
changes.
"""
import base64
from PyQt6.QtGui import QIcon, QImage, QPixmap

'''

_BODY = '''\

def build_app_icon() -> QIcon:
    """QIcon built from the embedded .ico bytes. Requires an existing
    QGuiApplication (QPixmap needs one) - call after QApplication(...)."""
    image = QImage.fromData(base64.b64decode(_ICON_B64))
    return QIcon(QPixmap.fromImage(image))
'''


def _wrap_b64(b64: str) -> str:
    """Format the base64 as one parenthesized concatenation of <=76-char
    string literals — the conventional embedded-blob layout (readable in a
    diff, and textwrap.wrap splits the continuous string by width)."""
    quoted = "\n".join(f'    "{line}"' for line in textwrap.wrap(b64, 76))
    return f"_ICON_B64 = (\n{quoted}\n)\n"


def main() -> None:
    data = SRC_ICO.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    content = _HEADER + _wrap_b64(b64) + _BODY
    OUT_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(data)} bytes -> {len(b64)} base64 chars)")


if __name__ == "__main__":
    main()

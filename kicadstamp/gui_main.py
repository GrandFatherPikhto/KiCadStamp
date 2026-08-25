# kicadstamp/gui_main.py
"""Package entry point for the KiCadStamp GUI (see pyproject.toml
[project.scripts]: ``kicadstamp-gui = kicadstamp.gui_main:main``).

The repo-root script kicadstamp_gui.py is a thin wrapper that adds the repo
root to sys.path and calls this module's main(), so the ``python
kicadstamp_gui.py`` dev workflow keeps working unchanged.
"""
import argparse
import sys

# Explicit i18n init (P1-1, 2026-08-25) — same reason as kicadstamp/cli_main.py.
from kicadstamp.i18n import setup_i18n

setup_i18n()

# See kicadstamp_cli.py for why this is needed (UnicodeEncodeError on legacy
# console codepages with translated/typographic text).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from PyQt6.QtWidgets import QApplication

from kicadstamp import __version__
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _
from kicadstamp.logging_setup import setup_logging

from gui.app_icon import build_app_icon
from gui.main_window import MainWindow
from gui.single_instance import SingleInstanceGuard

_SINGLE_INSTANCE_NAME = "kicadstamp-gui-singleton"


def main():
    parser = argparse.ArgumentParser(description=_("KiCadStamp GUI"))
    parser.add_argument("--version", "-V", action="version",
                        version=f"kicadstamp-gui {__version__}")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                        help=_("IPC timeout in ms"))
    parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    args = parser.parse_args()

    # setup_logging() now returns the started QueueListener; stop it when the
    # app quits so its thread doesn't leak and buffered records are flushed.
    listener = setup_logging(verbose=args.verbose)

    app = QApplication(sys.argv)

    # Default icon for every window this app creates (taskbar/alt-tab/window
    # manager decorations) — the real kicadstamp.ico, base64-embedded into
    # gui/app_icon.py (2026-08-20, "sew the icon into the app itself") so it
    # renders from any CWD / frozen build without an images/ file dependency.
    # The same embedded icon feeds the system tray (gui/main_window.py's
    # _set_tray_enabled).
    app.setWindowIcon(build_app_icon())

    # On GNOME/Wayland, setWindowIcon() alone is not enough — the Shell
    # (taskbar/Activities overview/alt-tab) resolves the icon through the
    # app's Wayland app_id matched against an installed .desktop file's
    # basename, not the runtime QIcon. This must equal the .desktop file's
    # name without the extension (see packaging/kicadstamp.desktop) — on
    # X11 it also sets WM_CLASS the same way.
    app.setDesktopFileName("kicadstamp")

    guard = SingleInstanceGuard(_SINGLE_INSTANCE_NAME)
    if not guard.try_acquire():
        # Another instance is already running — it's been pinged to raise
        # itself, nothing left to do here.
        sys.exit(0)
    app.aboutToQuit.connect(guard.release)
    if listener is not None:
        app.aboutToQuit.connect(listener.stop)

    window = MainWindow(timeout_ms=args.timeout_ms, verbose=args.verbose)
    guard.activation_requested.connect(window.bring_to_front)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

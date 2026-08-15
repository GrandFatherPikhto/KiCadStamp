#!.venv/bin/python
"""
kicadstamp_gui.py — persistent PyQt6 GUI for browsing/tagging the live
board over kipy IPC, alongside kicadstamp_cli.py for scripted batch work.

Step 1 (see gui/main_window.py): connection lifecycle + a Role/Cluster tree
dock, click a component/group to highlight it on the real board. Meant to
be left open while working in KiCad, not run once and closed like the CLI
— hence the optional tray icon (minimize instead of quitting) and the
single-instance guard below (relaunching just raises the existing window
instead of opening a second one).

Usage:
    python kicadstamp_gui.py [--timeout-ms 20000] [--verbose]
"""
import argparse
import sys
from pathlib import Path

# Project root isn't guaranteed to be importable when this file runs as a
# bare script (python kicadstamp_gui.py / python -m kicadstamp_gui) from a
# working directory outside the repo, so add it to sys.path in that case.
# When this module is ever imported as part of a package, the package
# machinery already provides the path and a manual insert would be wrong.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).parent))

# See kicadstamp_cli.py for why this is needed (UnicodeEncodeError on legacy
# console codepages with translated/typographic text).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from kicadstamp import __version__
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _
from kicadstamp.logging_setup import setup_logging

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
    # manager decorations) — a real asset, unlike gui/tray_icon.py's
    # programmatic "K" glyph (that one's deliberately not a binary asset,
    # see its own docstring; the window icon has no such constraint). Missing
    # file is not fatal — Qt just falls back to no icon.
    icon_path = Path(__file__).parent / "images" / "kicadstamp.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

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
    # Same lifecycle contract as PollWorkerHandle ("created in MainWindow.__init__,
    # stopped on QApplication.aboutToQuit", see
    # techdocs/handoff/plan_2026_08_15_queue_based_logging.md) — listener.stop()
    # drains the remaining queue and joins the thread, safe to call exactly once.
    if listener is not None:
        app.aboutToQuit.connect(listener.stop)

    window = MainWindow(timeout_ms=args.timeout_ms, verbose=args.verbose)
    guard.activation_requested.connect(window.bring_to_front)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

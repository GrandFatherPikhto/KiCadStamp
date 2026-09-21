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

from PyQt6.QtWidgets import QApplication, QStyleFactory

from kicadstamp import __version__
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _
from kicadstamp.logging_setup import setup_logging

from gui import settings
from gui.app_icon import build_app_icon
from gui.color_schemes import available_color_schemes, load_color_scheme
from gui.docks._common import apply_compact_field_minimums
from gui.main_window import MainWindow
from gui.single_instance import SingleInstanceGuard

_SINGLE_INSTANCE_NAME = "kicadstamp-gui-singleton"


def apply_saved_qt_style(app: QApplication) -> None:
    """Apply the user-chosen Qt style (gui_state.json["qt_style"], picked in
    Settings > Appearance > Style, 2026-09-03 plan qt_style_setting) when it is
    a non-empty string AND exists on THIS machine/Qt build (QStyleFactory.keys())
    — any other value (absent, None, empty, or a name this build doesn't know,
    e.g. gui_state.json synced from another OS) is a silent no-op: today's
    default behaviour, unchanged. Never raises, never fatal — the same
    discipline _restore_window_state applies to window_geometry/dock_state."""
    saved_style = settings.state.get("qt_style")
    if isinstance(saved_style, str) and saved_style in QStyleFactory.keys():
        app.setStyle(saved_style)


def apply_saved_color_scheme(app: QApplication) -> None:
    """Apply the user-chosen built-in color scheme (gui_state.json
    ["color_scheme"], picked in Settings > Appearance > Color scheme,
    2026-09-03 plan color_scheme_setting) when it names a known built-in
    scheme — any other value (absent, None, empty, or an unknown name, e.g. a
    scheme removed in a later version) is a silent no-op: today's default
    behaviour, unchanged. Never raises, never fatal — the same discipline
    apply_saved_qt_style applies to qt_style."""
    saved_scheme = settings.state.get("color_scheme")
    if isinstance(saved_scheme, str) and saved_scheme in available_color_schemes():
        palette = load_color_scheme(saved_scheme)
        if palette is not None:
            app.setPalette(palette)


def resolve_timeout_ms(cli_value) -> int:
    """The IPC timeout to start with, in priority order (plan_2026_09_13_ipc_
    timeout_and_latency Э4):

      1. an EXPLICIT --timeout-ms flag;
      2. the saved gui_state.json["kicad_timeout_ms"] (Settings > KiCad);
      3. DEFAULT_TIMEOUT_MS.

    ``default=None`` on the argparse option is what distinguishes (1) from the
    others: comparing the value against DEFAULT_TIMEOUT_MS would be wrong,
    because passing exactly that number on the command line is still an
    explicit choice and must win over the saved setting. A stored value that is
    not a positive int (hand-edited file, a future format) falls through to
    the default rather than breaking startup — the same discipline
    _restore_window_state applies to window_geometry/dock_state."""
    if cli_value is not None:
        return cli_value
    saved = settings.state.get("kicad_timeout_ms")
    if isinstance(saved, int) and not isinstance(saved, bool) and saved > 0:
        return saved
    return DEFAULT_TIMEOUT_MS


def main():
    parser = argparse.ArgumentParser(description=_("KiCadStamp GUI"))
    parser.add_argument("--version", "-V", action="version",
                        version=f"kicadstamp-gui {__version__}")
    parser.add_argument("--timeout-ms", type=int, default=None,
                        help=_("IPC timeout in ms (overrides the saved setting)"))
    parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    args = parser.parse_args()

    # setup_logging() now returns the started QueueListener; stop it when the
    # app quits so its thread doesn't leak and buffered records are flushed.
    listener = setup_logging(verbose=args.verbose)

    app = QApplication(sys.argv)

    # ── The door's guard belongs HERE (plan_2026_09_21_board_door_enforcement) ──
    # The getter in gui/connection.py refuses a UI-thread board read that has no
    # sign, and who counts as "the UI thread" is an INJECTED predicate — this
    # process entry point is where the production GUI hands it over. Deliberately
    # NOT MainWindow.__init__ (gui/connection.py carries the full rationale): ~60
    # GUI tests build a real MainWindow and read stand-in boards from the main
    # thread, so a constructor-installed predicate would refuse every one of them.
    # This is also the ONE Qt entry point of the project (pyproject.toml
    # [project.scripts] → kicadstamp-gui; gui/ has no main() of its own).
    #
    # THE CALL IS DELIBERATELY ABSENT (Denis, 21.09.2026): the offenders of the
    # Т2 table (trees_dock._live_adapter, the placement dock's position and
    # selection reads, Extract) still read the board on the UI thread, so arming
    # the guard here would refuse them in the user's hands. Т5 closes the door,
    # and its LAST step is to import gui.worker.is_ui_thread here and hand it to
    # gui.connection.set_ui_thread_predicate — nothing else, at this exact place.
    #
    # Two watchdogs in tests/test_board_door_guard.py guard THIS decision from
    # both sides: ..._is_either_armed_or_says_why_not fails if this note is
    # deleted without arming the guard, and ..._arms_the_guard is xfail(strict)
    # while the line is absent, turning XPASS — a FAILURE, on purpose — the
    # moment it comes back, so the mark cannot be forgotten either way.
    # Snapshot the pristine palette BEFORE any override (setStyle/setPalette)
    # — stored on the app object itself as a dynamic property (the same
    # instance lives for the whole process) and reused for the clean "None"
    # rollback later in the session (2026-09-03, plan color_scheme_setting).
    app.setProperty("original_palette", app.palette())

    # User-chosen Qt style (Settings > Appearance > Style, plan 2026-09-03
    # qt_style_setting) — default is none, leaving the system Qt platform-theme
    # integration untouched. Fatal-safe: unknown/foreign stored name is a
    # silent no-op (see apply_saved_qt_style).
    apply_saved_qt_style(app)

    # User-chosen built-in color scheme (Settings > Appearance > Color scheme,
    # plan 2026-09-03 color_scheme_setting) — a QPalette override on top of the
    # style. Fatal-safe: absent/None/unknown name is a silent no-op (see
    # apply_saved_color_scheme).
    apply_saved_color_scheme(app)

    # 2026-08-30 (Denis): combos' minimum width == their widest item, which
    # floored narrow docks. App-wide compact field minimums — growth on
    # widening is untouched (see _common.apply_compact_field_minimums).
    apply_compact_field_minimums(app)

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

    window = MainWindow(timeout_ms=resolve_timeout_ms(args.timeout_ms),
                        verbose=args.verbose)
    guard.activation_requested.connect(window.bring_to_front)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

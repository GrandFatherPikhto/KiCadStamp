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
from gui.connection import set_ui_thread_predicate
from gui.docks._common import apply_compact_field_minimums
from gui.main_window import MainWindow
from gui.single_instance import SingleInstanceGuard
from gui.slot_exception_hook import install_slot_exception_hook
from gui.worker import is_ui_thread

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

    # ── The Qt-slot crash hook is ARMED HERE (plan_2026_09_24_slot_exception_hook) ─
    # An exception escaping a Qt slot makes PyQt6 call qFatal() while sys.excepthook
    # is the interpreter's default one: the process dies with SIGABRT and the user
    # loses the session. Measured 2026-09-24
    # (diagnostics/probe_slot_excepthook.py, one process per shape): 134 without the
    # hook and 0 with it — for a slot on the UI thread, for a slot on a QThread, and
    # even for a raise inside Qt's own C++ event dispatch (the shape
    # gui/ui_utils.py:277 writes its liveness guards for). The hook logs CRITICAL
    # with the traceback through the GUI's own Log bridge and writes a report into
    # diagnostics/; it never touches a widget, so it is safe on any thread.
    #
    # Deliberately NOT at import time: ~60 GUI tests import gui.main_window and
    # friends, and a hook installed then would hide a core dump from the Кq
    # watchdog (tests/gui/test_qt_slot_exception_capture.py) — the watchdog exists
    # to measure exactly that dump. And deliberately HERE, before MainWindow: the
    # window's constructor is covered too. Switchable off for a diagnostic run with
    # KICADSTAMP_SLOT_EXCEPTION_HOOK=0.
    #
    # NOT the board door's switch (set_ui_thread_predicate below): the door
    # testifies about a read left unsigned, this keeps a Python failure from ending
    # the session. Different jobs — and this line does NOT change the door's refusal
    # mode, which stays "log" for its own reason (an armed session is a live census).
    #
    # What it does NOT cover, measured and named: Qt's own qFatal (a QThread
    # destroyed while still running) has no Python exception in it, so no hook can
    # catch it; and a NON-Qt thread's failure is reported by threading, which
    # already keeps the process alive.
    install_slot_exception_hook()

    # ── The door's guard is ARMED HERE (plan_2026_09_23_door_s6_entry) ──────────
    # The getter in gui/connection.py refuses a UI-thread board read that has no
    # sign, and who counts as "the UI thread" is an INJECTED predicate — this
    # process entry point is where the production GUI hands it over. Deliberately
    # NOT MainWindow.__init__ (gui/connection.py carries the full rationale): ~60
    # GUI tests build a real MainWindow and read stand-in boards from the main
    # thread, so a constructor-installed predicate would refuse every one of them.
    # This is also the ONE Qt entry point of the project (pyproject.toml
    # [project.scripts] → kicadstamp-gui; gui/ has no main() of its own).
    #
    # ARMED SINCE 2026-09-23 (Ш6, the LAST step of the door effort; the call was
    # deliberately absent before that, Denis 21.09.2026). The mode is the USER'S —
    # refusal="log": an unsigned UI-thread read writes ONE red Log line naming the
    # caller, and the read goes ON. That dedup keeps the armed app usable: an
    # offender that runs in a loop cannot flood the Log (one line per file:line,
    # see gui/connection.py's _report_ui_thread_read), so the armed session is a
    # LIVE CENSUS of the places that really fire, not a wall of red.
    # Never "raise" in production: inside a Qt slot an exception is a core dump,
    # measured 2026-09-21 in diagnostics/probe_slot_exception.py (EXIT=134), and
    # the user would lose the session instead of reading a message.
    #
    # Ш6 ARMS THE GUARD AND DOES NOT FIX OFFENDERS: the remaining UI-thread reads
    # are closed in SEPARATE steps, one class at a time — presence ->
    # connection.is_connected; a hand-off -> a signed read whose cost is measured;
    # a live read on the UI thread -> a worker.
    #
    # Two watchdogs in tests/test_board_door_guard.py guard THIS decision from
    # both sides: ..._is_either_armed_or_says_why_not fails if the arming line is
    # gone and nothing says why, and ..._arms_the_guard asserts the line below IS
    # here. That second one was xfail(strict) while the call was deliberately
    # absent, so it turned XPASS — a FAILURE, on purpose — the moment the call
    # came back; the mark was removed the same day, exactly as it required.
    set_ui_thread_predicate(is_ui_thread, refusal="log")
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

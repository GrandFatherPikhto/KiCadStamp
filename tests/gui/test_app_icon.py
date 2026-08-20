# tests/gui/test_app_icon.py
"""The embedded application icon (gui/app_icon.py) — images/kicadstamp.ico
base64-encoded directly into the source, the single icon for the window,
Windows taskbar, and the system tray (2026-08-20, "sew the icon into the
app itself"). Verifies the embedded bytes decode to a valid 64x64 image on
this platform — QT_QPA_PLATFORM=offscreen is set by tests/gui/conftest.py,
i.e. the same display-less/headless path a Linux CI or session without a
display takes — and that the real MainWindow's tray actually ends up with a
non-null icon from it (the old programmatic "K" glyph is gone)."""
import base64

from PyQt6.QtGui import QImage

from gui.app_icon import _ICON_B64, build_app_icon


def test_embedded_bytes_decode_to_valid_64x64_image():
    # QImage.fromData needs no QApplication — pure CPU decode of the embedded
    # .ico, so this also proves the bytes are a real image, not a stub.
    image = QImage.fromData(base64.b64decode(_ICON_B64))
    assert not image.isNull()
    assert image.width() == 64
    assert image.height() == 64


def test_build_app_icon_returns_non_null_icon(qapp):
    # QPixmap needs a QGuiApplication (qapp fixture) — the offscreen platform
    # from conftest is the same environment Linux headless/CI would use.
    icon = build_app_icon()
    assert not icon.isNull()
    # Must render at the sizes the OS actually uses for taskbar/tray/window.
    for size in (16, 32, 48, 64):
        pixmap = icon.pixmap(size, size)
        assert not pixmap.isNull()
        assert (pixmap.width(), pixmap.height()) == (size, size)


def test_tray_uses_the_embedded_icon(real_main_window):
    # The tray must now carry the REAL embedded icon, not the old
    # programmatic "K" glyph (gui/tray_icon.py was removed).
    from PyQt6.QtWidgets import QSystemTrayIcon

    assert real_main_window._tray_icon is None
    real_main_window._dock_hub.configurator_dock.tray_checkbox.setChecked(True)
    assert isinstance(real_main_window._tray_icon, QSystemTrayIcon)
    assert not real_main_window._tray_icon.icon().isNull()

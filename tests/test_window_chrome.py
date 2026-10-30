"""Caption policy preserves native window controls and excludes desktop pets."""

import ctypes
import json
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QWidget

from frontend import window_chrome as chrome


def test_native_update_preserves_window_style_and_geometry():
    # A full resizable caption with system menu/minimize/maximize controls.
    styles = {chrome.GWL_STYLE: 0x00CF0000}
    original_style = styles[chrome.GWL_STYLE]
    messages, refreshed = [], []
    native = chrome._WindowsCaption.__new__(chrome._WindowsCaption)
    native.get_long = lambda _handle, index: styles[index]
    native.icon = 987
    native.send = lambda *args: messages.append(args)
    native.refresh = lambda *args: refreshed.append(args) or 1
    native.hide_icon(123)
    assert styles[chrome.GWL_STYLE] == original_style
    assert messages == [(123, chrome.WM_SETICON, 0, 987)]
    assert refreshed == [(123, None, 0, 0, 0, 0, chrome.FRAME_REFRESH)]
    native.hide_icon(123)
    assert len(refreshed) == 2
    # Frameless native windows must not have their frame changed.
    styles[chrome.GWL_STYLE] = 0x80000000
    native.hide_icon(456)
    assert len(messages) == 2


def test_only_captioned_windows_are_scheduled(monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending = []
    monkeypatch.setattr(chrome.QTimer, "singleShot", lambda _delay, call: pending.append(call))
    policy = chrome._CaptionPolicy(app, SimpleNamespace(hide_icon=lambda _handle: None))
    window, dialog = QWidget(), QDialog()
    child = QWidget(window)
    pet = QWidget(None, Qt.WindowType.FramelessWindowHint)
    menu = QMenu()
    try:
        for widget in (window, dialog, child, pet, menu):
            flags = widget.windowFlags()
            assert policy.eventFilter(widget, QEvent(QEvent.Type.Show)) is False
            assert widget.windowFlags() == flags
        assert len(pending) == 2
        # A close/delete before the queued callback must not create an HWND.
        for call in pending:
            call()
    finally:
        for widget in (window, dialog, pet, menu):
            widget.close()


def test_installer_is_idempotent(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(app, "platformName", lambda: "windows")
    monkeypatch.setattr(chrome, "_WindowsCaption", lambda: SimpleNamespace())
    installed = []
    monkeypatch.setattr(app, "installEventFilter", installed.append)
    monkeypatch.setattr(app, "_roxy_caption_policy", None, raising=False)
    chrome.install_titlebar_policy(app)
    chrome.install_titlebar_policy(app)
    assert len(installed) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API only")
def test_hidden_native_window_policy(tmp_path):
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        pytest.skip("Native Windows Qt only; window remains off screen")
    native = chrome._WindowsCaption()
    applied = []
    before = {}
    def hide(handle):
        applied.append(handle)
        before.update(style=native.get_long(handle, chrome.GWL_STYLE),
                      extended=native.get_long(handle, -20),
                      large=native.send(handle, 0x7F, 1, 0),
                      class_icon=class_icon(handle, -14),
                      class_small_icon=class_icon(handle, -34))
        native.hide_icon(handle)
    policy = chrome._CaptionPolicy(app, SimpleNamespace(hide_icon=hide))
    app.installEventFilter(policy)
    window = QWidget()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    visible = ctypes.WinDLL("user32").IsWindowVisible
    visible.argtypes = [ctypes.c_void_p]
    visible.restype = ctypes.c_int
    suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
    class_icon = getattr(ctypes.WinDLL("user32"), "GetClassLong" + suffix)
    class_icon.argtypes = [ctypes.c_void_p, ctypes.c_int]
    class_icon.restype = ctypes.c_size_t
    try:
        window.show()
        app.processEvents()
        handle = int(window.winId())
        assert not visible(handle), "Test window must never occupy the desktop"
        evidence = {
            "handle": handle, "policy_called": applied,
            "captioned": chrome._has_caption(window),
            "native_style": hex(native.get_long(handle, chrome.GWL_STYLE)),
            "native_extended": hex(native.get_long(handle, -20)),
            "small_icon": native.send(handle, 0x7F, 0, 0),
            "large_icon": native.send(handle, 0x7F, 1, 0),
            "small2_icon": native.send(handle, 0x7F, 2, 0),
            "native_visible": bool(visible(handle)),
            "class_icon": class_icon(handle, -14),
            "class_small_icon": class_icon(handle, -34),
        }
        (tmp_path / "native-caption.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence))
        assert applied == [handle]
        assert evidence["small_icon"] == int(native.icon)
        assert evidence["small2_icon"] == int(native.icon)
        assert evidence["large_icon"] == before["large"]
        assert evidence["class_icon"] == before["class_icon"]
        assert evidence["class_small_icon"] == before["class_small_icon"]
        assert native.get_long(handle, chrome.GWL_STYLE) == before["style"]
        assert native.get_long(handle, -20) == before["extended"]
    finally:
        app.removeEventFilter(policy)
        window.close()
        window.deleteLater()
        app.processEvents()
        native.close()

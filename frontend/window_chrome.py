"""Hide Windows caption icons without replacing the native title bar."""

import atexit
import ctypes
import logging
import sys
import weakref
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WM_SETICON = 0x0080
# Refresh the non-client frame without moving, resizing, raising or activating.
FRAME_REFRESH = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020


class _WindowsCaption:
    def __init__(self):
        api = ctypes.WinDLL("user32", use_last_error=True)
        suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
        self.get_long = getattr(api, "GetWindowLong" + suffix)
        self.get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_long.restype = ctypes.c_ssize_t
        self.send = api.SendMessageW
        self.send.argtypes = [wintypes.HWND, wintypes.UINT,
                              ctypes.c_size_t, ctypes.c_ssize_t]
        self.send.restype = ctypes.c_ssize_t
        self.refresh = api.SetWindowPos
        self.refresh.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        self.refresh.restype = wintypes.BOOL
        create = api.CreateIcon
        byte_pointer = ctypes.POINTER(ctypes.c_ubyte)
        create.argtypes = [wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int,
                           ctypes.c_ubyte, ctypes.c_ubyte, byte_pointer, byte_pointer]
        create.restype = wintypes.HICON
        self.destroy = api.DestroyIcon
        self.destroy.argtypes = [wintypes.HICON]
        self.destroy.restype = wintypes.BOOL
        # NULL falls back to the class/system icon. Use explicit transparent
        # artwork for ICON_SMALL only; keep the large/taskbar icon and all
        # window/class styles unchanged. Native caption spacing is retained.
        and_mask = (ctypes.c_ubyte * 128)(*([255] * 128))
        xor_mask = (ctypes.c_ubyte * 4096)()
        self.icon = create(None, 32, 32, 1, 32, and_mask, xor_mask)
        if not self.icon:
            raise ctypes.WinError(ctypes.get_last_error())
        atexit.register(self.close)

    def close(self):
        if self.icon:
            self.destroy(self.icon)
            self.icon = None

    def hide_icon(self, handle):
        if self.get_long(handle, GWL_STYLE) & WS_CAPTION != WS_CAPTION:
            return
        self.send(handle, WM_SETICON, 0, self.icon)
        if not self.refresh(handle, None, 0, 0, 0, 0, FRAME_REFRESH):
            raise ctypes.WinError(ctypes.get_last_error())


def _has_caption(widget):
    flags = widget.windowFlags()
    return (widget.isWindow()
            and widget.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog)
            and not flags & Qt.WindowType.FramelessWindowHint
            and bool(flags & Qt.WindowType.WindowTitleHint))


class _CaptionPolicy(QObject):
    def __init__(self, app, native):
        super().__init__(app)
        self.native = native

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.Show and isinstance(watched, QWidget)
                and _has_caption(watched)):
            # Apply after Qt has finished assigning the HWND's native styles.
            reference = weakref.ref(watched)
            QTimer.singleShot(0, lambda: self._apply(reference))
        return False

    def _apply(self, reference):
        widget = reference()
        if (widget is None or not isValid(widget) or not widget.isVisible()
                or not _has_caption(widget)):
            return
        try:
            self.native.hide_icon(int(widget.winId()))
        except OSError:
            logging.getLogger(__name__).exception("Unable to hide caption icon")


def install_titlebar_policy(app: QApplication):
    """Install once; menus, frameless desktop pet and bubbles are excluded."""
    if sys.platform != "win32" or app.platformName() != "windows":
        return
    if getattr(app, "_roxy_caption_policy", None) is None:
        policy = _CaptionPolicy(app, _WindowsCaption())
        app._roxy_caption_policy = policy
        app.installEventFilter(policy)

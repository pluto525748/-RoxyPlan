"""Shared window icon for the application and isolated UI previews.

The approved chat avatar is also the source for a future EXE .ico asset;
this module only sets Qt's application icon, not the executable's icon.
"""

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


APP_ICON_PATH = Path(__file__).resolve().parents[1] / "assets/chat/roxy_avatar.png"


def configure_application_icon(app: QApplication) -> None:
    """Set the default inherited by every window before creating the UI."""
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

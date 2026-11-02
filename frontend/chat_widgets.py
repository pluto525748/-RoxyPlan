"""Presentation widgets for the existing desktop chat."""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QTextDocument, QTextOption
from PySide6.QtWidgets import QLabel, QTextEdit, QWidget


CHAT_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "chat"


class ChatTexturePanel(QWidget):
    """Keep the quiet texture behind the text, independent of scrolling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.texture = QPixmap(str(CHAT_ASSETS / "chat_texture.png"))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 14, 14)
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), QColor("#FBFAF6"))
        if not self.texture.isNull():
            scaled = self.texture.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.setOpacity(0.38)
            painter.drawPixmap((self.width() - scaled.width()) // 2,
                               (self.height() - scaled.height()) // 2, scaled)
        painter.end()


class ChatTranscript(QTextEdit):
    """One selectable document retains selection and copying across messages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(10)
        avatar = QPixmap(str(CHAT_ASSETS / "roxy_avatar.png"))
        if not avatar.isNull():
            rounded = QPixmap(96, 96)
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            clip = QPainterPath()
            clip.addEllipse(0, 0, 96, 96)
            painter.setClipPath(clip)
            painter.fillRect(rounded.rect(), QColor("#E8ECF8"))
            painter.drawPixmap(
                QRectF(rounded.rect()), avatar,
                QRectF(avatar.width() * 0.16, avatar.height() * 0.20,
                       avatar.width() * 0.68, avatar.height() * 0.68),
            )
            painter.end()
            self.avatar_image = rounded.toImage()
            self._register_avatar()

    def _register_avatar(self):
        if hasattr(self, "avatar_image"):
            self.document().addResource(
                QTextDocument.ResourceType.ImageResource, QUrl("roxy-avatar"), self.avatar_image
            )

    def clear(self):
        super().clear()
        self._register_avatar()


class ChatDecoration(QLabel):
    """Fit a transparent illustration into existing header/footer space."""

    def __init__(self, filename, parent=None):
        super().__init__(parent)
        self.source = QPixmap(str(CHAT_ASSETS / filename))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.source.isNull():
            ratio = self.devicePixelRatioF()
            pixmap = self.source.scaled(
                round(self.width() * ratio), round(self.height() * ratio),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(ratio)
            self.setPixmap(pixmap)

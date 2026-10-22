"""信号发送卡片的缩放手柄及拖动入口。"""

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QFrame, QRubberBand, QWidget


class CornerGrip(QWidget):
    def __init__(self, card, sx, sy):
        super().__init__(card)
        self.card, self.sx, self.sy = card, sx, sy
        self.setCursor(Qt.SizeFDiagCursor if sx == sy else Qt.SizeBDiagCursor)
        self.setFixedSize(10, 10)
        self.band = None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.start = event.globalPosition().toPoint()
        self.original = self.card.geometry()
        self.target_size = self.original.size()
        self.band = QRubberBand(QRubberBand.Rectangle, self.card.parentWidget())
        self.band.setGeometry(self.original)
        self.band.show()
        self.grabMouse()
        self.grabKeyboard()

    def mouseMoveEvent(self, event):
        if self.band is None:
            return
        delta = event.globalPosition().toPoint() - self.start
        w, h = self.original.width(), self.original.height()
        scale = 1 + (self.sx * delta.x() * w + self.sy * delta.y() * h) / (w*w + h*h)
        hint = self.card.base_min_size
        lower = max(hint.width() / w, hint.height() / h, 180 / w)
        upper = max(lower, (self.card.parentWidget().width() - 4) / w)
        scale = max(lower, min(upper, scale))
        self.target_size = QSize(round(w * scale), round(h * scale))
        x = self.original.x() if self.sx > 0 else self.original.right() + 1 - self.target_size.width()
        y = self.original.y() if self.sy > 0 else self.original.bottom() + 1 - self.target_size.height()
        self.band.setGeometry(QRect(QPoint(x, y), self.target_size))

    def _finish(self, commit):
        if self.band is None:
            return
        self.band.hide()
        self.band.deleteLater()
        self.band = None
        self.releaseMouse()
        self.releaseKeyboard()
        if commit and self.card.panel is not None:
            self.card.panel._resize_card(self.card.signal_name, self.target_size)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._finish(True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._finish(False)


class SignalCard(QFrame):
    MIME = 'application/x-cartool-send-card'

    def __init__(self):
        super().__init__()
        self.panel = None
        self.signal_name = ''
        self.start = None
        self.grips = [CornerGrip(self, x, y) for x, y in ((-1,-1),(1,-1),(-1,1),(1,1))]
        self.setMouseTracking(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for grip in self.grips:
            grip.move(0 if grip.sx < 0 else self.width()-10,
                      0 if grip.sy < 0 else self.height()-10)
            grip.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() <= 38:
            self.start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.OpenHandCursor if event.position().y() <= 38 else Qt.ArrowCursor)
        if self.start is not None and event.buttons() & Qt.LeftButton:
            if (event.position().toPoint() - self.start).manhattanLength() >= QApplication.startDragDistance():
                self.start = None
                self.start_drag(event.position().toPoint())
        super().mouseMoveEvent(event)

    def start_drag(self, position):
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self.MIME, self.signal_name.encode('utf-8'))
        drag.setMimeData(mime)
        pixmap = self.grab()
        # 半透明预览，原位置保持占位。
        from PySide6.QtGui import QPainter, QPixmap
        preview = QPixmap(pixmap.size())
        preview.fill(Qt.transparent)
        painter = QPainter(preview)
        painter.setOpacity(0.65)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        drag.setPixmap(preview)
        drag.setHotSpot(position)
        drag.exec(Qt.MoveAction)
        if self.panel is not None:
            self.panel._insert_marker.hide()
            self.panel._drag_position = None
            self.panel._drag_scroll_timer.stop()

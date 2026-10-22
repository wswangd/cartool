"""紧凑信号测量卡片。"""

from PySide6.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from .signal_scope import SignalScope


class ElidedLabel(QLabel):
    def __init__(self, text="", parent=None) -> None:
        super().__init__(text, parent)
        self.full_text = text
        self.setTextFormat(Qt.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.full_text) + 2,
                     super().sizeHint().height())

    def display_text(self) -> str:
        return self.fontMetrics().elidedText(self.full_text, Qt.ElideRight,
                                             self.contentsRect().width())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(), self.alignment(), self.display_text())


class MeasurementCard(QFrame):
    swap_requested = Signal(object, object)
    MIME_TYPE = "application/x-cartool-measurement-card"

    def __init__(self, name: str, unit: str, description: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("measurementCard")
        self.setStyleSheet(
            "QFrame#measurementCard { background: white; border: 1px solid #DDE3E8; border-radius: 6px; }"
            "QFrame#measurementCard[dragOver=\"true\"] { border-color: #0078D4; background: #EDF6FF; }")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAcceptDrops(True)
        self.drag_group = parent
        self._drag_start = None
        self.scope = None
        self._valid_value = None
        self.name = name
        self.unit = unit
        self.name_label = ElidedLabel(name)
        self.name_label.setMinimumWidth(40)
        self.name_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.name_label.setToolTip(description + "\n拖到另一张卡片可交换位置")
        self.value_label = QLabel("—")
        self.value_label.setTextFormat(Qt.PlainText)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setMinimumWidth(40)
        self.value_label.setCursor(Qt.ArrowCursor)
        font = self.value_label.font()
        font.setBold(True)
        self.value_label.setFont(font)
        self.value_label.setStyleSheet("color: #9CA3AF;")
        self.unit_label = QLabel(unit)
        self.unit_label.setTextFormat(Qt.PlainText)
        self.unit_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.unit_label.setToolTip(unit)
        self.unit_label.setStyleSheet("color: #6B7280;")
        self.expand_button = QToolButton()
        self.expand_button.setCheckable(True)
        self.expand_button.setAutoRaise(True)
        self.expand_button.setCursor(Qt.ArrowCursor)
        self.expand_button.setArrowType(Qt.DownArrow)
        self.expand_button.setToolTip("展开波形")
        self.expand_button.setAccessibleName(f"展开 {name} 的波形")
        self.header_widget = QWidget()
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        self._header = header
        header.setSpacing(6)
        identity = QHBoxLayout()
        identity.setSpacing(6)
        identity.addWidget(self.name_label)
        identity.addWidget(self.unit_label)
        identity.addStretch(1)
        header.addLayout(identity, 1)
        header.addWidget(self.value_label)
        header.addWidget(self.expand_button)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 5, 6, 5)
        self._layout.setSpacing(4)
        self._layout.addWidget(self.header_widget)
        self.expand_button.toggled.connect(self._set_expanded)
        for label in (self.header_widget, self.name_label, self.unit_label):
            label.installEventFilter(self)
            label.setCursor(Qt.OpenHandCursor)

    def eventFilter(self, watched, event) -> bool:
        if watched not in (self.header_widget, self.name_label, self.unit_label):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
        elif event.type() == QEvent.MouseButtonRelease:
            self._drag_start = None
        elif event.type() == QEvent.MouseMove and self._drag_start is not None:
            if event.buttons() & Qt.LeftButton:
                distance = (event.globalPosition().toPoint() - self._drag_start).manhattanLength()
                if distance >= QApplication.startDragDistance():
                    self._drag_start = None
                    drag = QDrag(self)
                    mime = QMimeData()
                    mime.setData(self.MIME_TYPE, str(id(self)).encode("ascii"))
                    drag.setMimeData(mime)
                    drag.setPixmap(self.header_widget.grab().scaledToWidth(min(self.width(), 350)))
                    drag.setHotSpot(QPoint(16, 12))
                    drag.exec(Qt.MoveAction)
                    return True
        return super().eventFilter(watched, event)

    def _accepts_drag(self, source, mime) -> bool:
        return (isinstance(source, MeasurementCard) and source is not self
                and source.drag_group is self.drag_group
                and mime.hasFormat(self.MIME_TYPE)
                and bytes(mime.data(self.MIME_TYPE)) == str(id(source)).encode("ascii"))

    def _highlight_drop(self, active: bool) -> None:
        if bool(self.property("dragOver")) == active:
            return
        self.setProperty("dragOver", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragEnterEvent(self, event) -> None:
        if self._accepts_drag(event.source(), event.mimeData()):
            self._highlight_drop(True)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        allowed = self._accepts_drag(event.source(), event.mimeData())
        self._highlight_drop(allowed)
        if allowed:
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._highlight_drop(False)
        event.accept()

    def dropEvent(self, event) -> None:
        self._highlight_drop(False)
        if self._accepts_drag(event.source(), event.mimeData()):
            self.swap_requested.emit(event.source(), self)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _set_expanded(self, expanded: bool) -> None:
        self.expand_button.setArrowType(Qt.UpArrow if expanded else Qt.DownArrow)
        self.expand_button.setToolTip("收起波形" if expanded else "展开波形")
        if expanded and self.scope is None:
            self.scope = SignalScope()
            self.scope.set_signal(self.name, self.unit)
            self.scope.setFixedHeight(120)
            self.scope.setToolTip("移动鼠标查看采样值；收起后释放波形，再次展开重新采样")
            self._layout.addWidget(self.scope)
            self.scope.show()
        if not expanded and self.scope is not None:
            self._layout.removeWidget(self.scope)
            self.scope.hide()
            self.scope.deleteLater()
            self.scope = None
        self.updateGeometry()

    def set_value(self, text: str, valid: bool) -> None:
        if self.value_label.text() != text:
            self.value_label.setText(text)
            self.value_label.setToolTip(text)
        if self._valid_value != valid:
            self._valid_value = valid
            self.value_label.setStyleSheet("color: #000000;" if valid else "color: #9CA3AF;")

    def clear(self) -> None:
        self.set_value("—", False)
        if self.scope is not None:
            self.scope.clear()

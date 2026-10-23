"""单信号实时波形，保留有界的最近采样。"""

import math
from bisect import bisect_right
from collections import deque

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QDoubleSpinBox, QWidget, QToolTip


class SignalScope(QWidget):
    time_range_changed = Signal(float)
    cursor_moved = Signal(object)
    WINDOW_SECONDS = 300
    MAX_SAMPLES = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(220, 120)
        self.setMouseTracking(True)
        self.samples = deque(maxlen=self.MAX_SAMPLES)
        self.signal_name = "请选择测量信号"
        self.unit = ""
        self._hover_x = None
        self._origin = None
        self._view_time = None
        self._linked_cursor = None
        self._time_editor = QDoubleSpinBox(self)
        self._time_editor.setRange(1, 86400)
        self._time_editor.setDecimals(0)
        self._time_editor.setSuffix(" s")
        self._time_editor.hide()
        self._time_editor.installEventFilter(self)
        self._time_editor.editingFinished.connect(self._finish_time_edit)

    def _time_label_rect(self) -> QRectF:
        return QRectF(50, self.height() - 24, 80, 24)

    def _time_label_at(self, position):
        rect = self._time_label_rect()
        return rect if rect.contains(position) else None

    def mouseDoubleClickEvent(self, event) -> None:
        label = self._time_label_at(event.position())
        if event.button() == Qt.LeftButton and label is not None:
            self._time_editor.setValue(self.WINDOW_SECONDS)
            self._time_editor.setGeometry(max(0,int(label.left())-2),int(label.top())-2,105,max(25,int(label.height())+4))
            self._time_editor.raise_()
            self._time_editor.show()
            self._time_editor.setFocus()
            self._time_editor.selectAll()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._time_editor and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self._time_editor.hide()
            return True
        return super().eventFilter(watched, event)

    def _finish_time_edit(self) -> None:
        if self._time_editor.isHidden():
            return
        seconds = self._time_editor.value()
        self._time_editor.hide()
        self.set_window_seconds(seconds)
        self.time_range_changed.emit(seconds)

    def set_window_seconds(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("时间上限必须大于 0")
        self.WINDOW_SECONDS = seconds
        self.update()

    def _start_time(self) -> float:
        return (self._view_time if self._view_time is not None else self.samples[-1][0]) - self.WINDOW_SECONDS

    def set_signal(self, name: str, unit: str) -> None:
        self.signal_name = name
        self.unit = unit
        self.clear()

    def clear(self) -> None:
        self.samples.clear()
        self._origin = None
        self._hover_x = None
        self.update()

    def _cursor_plot(self):
        return QRectF(50,8,max(1,self.width()-60),max(1,self.height()-32))

    def _cursor_x(self, plot):
        if self._linked_cursor is not None:
            end = self._view_time if self._view_time is not None else (self.samples[-1][0] if self.samples else 0)
            return plot.left()+(self._linked_cursor-(end-self.WINDOW_SECONDS))/self.WINDOW_SECONDS*plot.width()
        return self._hover_x

    def set_linked_cursor(self, timestamp):
        self._linked_cursor = timestamp
        self.update()

    def mouseMoveEvent(self, event) -> None:
        over_time = self._time_label_at(event.position()) is not None
        self.setCursor(Qt.IBeamCursor if over_time else Qt.ArrowCursor)
        if over_time:
            QToolTip.showText(event.globalPosition().toPoint(), "双击修改时间上限", self)
        else:
            QToolTip.hideText()
        self._hover_x = event.position().x()
        plot = self._cursor_plot()
        end = self._view_time if self._view_time is not None else (self.samples[-1][0] if self.samples else None)
        timestamp = None
        if end is not None and plot.contains(event.position()):
            timestamp = end-self.WINDOW_SECONDS+(self._hover_x-plot.left())/plot.width()*self.WINDOW_SECONDS
        self.set_linked_cursor(timestamp)
        self.cursor_moved.emit(timestamp)
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_x = None
        self.set_linked_cursor(None)
        self.cursor_moved.emit(None)
        self.update()
        super().leaveEvent(event)

    def append(self, timestamp: float, value: float) -> None:
        if not math.isfinite(value) or not math.isfinite(timestamp):
            return
        if self.samples and timestamp < self.samples[-1][0]:
            self.clear()
        if self._origin is None:
            self._origin = timestamp
        self.samples.append((timestamp, value))
        while self.samples and self.samples[0][0] < timestamp - self.WINDOW_SECONDS:
            self.samples.popleft()
        self.update()

    @staticmethod
    def _time_tick_text(value: float, endpoint: bool = False) -> str:
        return f"{value:g}" + (" s" if endpoint else "")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#F7F8F6"))
        plot = QRectF(50, 8, max(1, self.width() - 60), max(1, self.height() - 32))
        values = [sample[1] for sample in self.samples]
        minimum, maximum = (min(values), max(values)) if values else (0, 1)
        span = maximum - minimum
        if not math.isfinite(span):
            return
        margin = span * 0.1 if span else max(abs(minimum) * 0.1, 1)
        minimum -= margin
        maximum += margin
        if not math.isfinite(maximum - minimum):
            return
        for index in range(5):
            position = plot.top() + plot.height() * index / 4
            painter.setPen(QPen(QColor("#BFC6BD"), 1, Qt.DotLine))
            painter.drawLine(QPointF(plot.left(), position), QPointF(plot.right(), position))
            painter.setPen(QColor("#0078D4"))
            tick = maximum - (maximum - minimum) * index / 4
            painter.drawText(QRectF(2, position - 9, 42, 18), Qt.AlignRight | Qt.AlignVCenter,
                             format(tick, ".4g"))
        major_count = 4 if plot.width() >= 300 else 2
        subdivisions = 5
        for index in range(major_count * subdivisions + 1):
            position = plot.left() + plot.width() * index / (major_count * subdivisions)
            major = index % subdivisions == 0
            painter.setPen(QPen(QColor("#BFC6BD"), 1, Qt.DotLine))
            if major:
                painter.drawLine(QPointF(position, plot.top()), QPointF(position, plot.bottom()))
            painter.setPen(QColor("#687568"))
            painter.drawLine(QPointF(position, plot.bottom()),
                             QPointF(position, plot.bottom() + (5 if major else 3)))
            if major:
                tick = -self.WINDOW_SECONDS + self.WINDOW_SECONDS * index / (major_count * subdivisions)
                text = self._time_tick_text(tick, index in (0, major_count * subdivisions))
                label = QRectF(position - 25, plot.bottom() + 5, 50, 18)
                if index == 0:
                    label.moveLeft(plot.left())
                elif index == major_count * subdivisions:
                    label.moveRight(plot.right())
                alignment = Qt.AlignLeft if index == 0 else (Qt.AlignRight if index == major_count * subdivisions else Qt.AlignHCenter)
                painter.drawText(label, alignment, text)
        hover_x = self._cursor_x(plot)
        if hover_x is not None and plot.left() <= hover_x <= plot.right():
            painter.setPen(QPen(QColor("#418AC7"),1))
            painter.drawLine(QPointF(hover_x,plot.top()),QPointF(hover_x,plot.bottom()))
        if not self.samples:
            painter.drawText(plot, Qt.AlignCenter, "等待信号数据")
            return
        end_time = self.samples[-1][0]
        points = self._step_points(plot, minimum, maximum)
        painter.save()
        painter.setClipRect(plot.adjusted(-2, -2, 2, 2))
        painter.setPen(QPen(QColor("#0078D4"), 2))
        painter.drawPolyline(points)
        painter.setBrush(QColor("#0078D4"))
        painter.drawEllipse(points[-1], 3, 3)
        painter.restore()
        sample = self.samples[-1]
        cursor_x = points[-1].x()
        if hover_x is not None and plot.left() <= hover_x <= plot.right():
            cursor_x = hover_x
            timestamp = self._start_time() + (cursor_x - plot.left()) / plot.width() * self.WINDOW_SECONDS
            timestamps = [entry[0] for entry in self.samples]
            if timestamp < timestamps[0] or timestamp > end_time:
                return
            sample = self.samples[bisect_right(timestamps, timestamp) - 1]
            painter.setPen(QPen(QColor("#418AC7"), 1))
            painter.drawLine(QPointF(cursor_x, plot.top()), QPointF(cursor_x, plot.bottom()))
        text = f"{sample[1]:.10g}"
        label_width = min(plot.width(), painter.fontMetrics().horizontalAdvance(text) + 14)
        label_y = plot.bottom() - (sample[1] - minimum) / (maximum - minimum) * plot.height()
        label = QRectF(max(plot.left(), min(cursor_x + 8, plot.right() - label_width)),
                       max(plot.top(), min(label_y - 30, plot.bottom() - 24)), label_width, 24)
        painter.setPen(QPen(QColor("#0078D4"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(label, 3, 3)
        painter.setPen(QColor("#263523"))
        painter.drawText(label, Qt.AlignCenter, text)

    def _step_points(self, plot: QRectF, minimum: float, maximum: float) -> QPolygonF:
        points = QPolygonF()
        end_time = self.samples[-1][0]
        for timestamp, value in self.samples:
            position = QPointF(
                plot.left() + (timestamp - self._start_time()) / self.WINDOW_SECONDS * plot.width(),
                plot.bottom() - (value - minimum) / (maximum - minimum) * plot.height())
            if points:
                points.append(QPointF(position.x(), points[-1].y()))
            points.append(position)
        return points

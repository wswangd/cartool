"""单信号实时波形，保留有界的最近采样。"""

import math
from bisect import bisect_right
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class SignalScope(QWidget):
    WINDOW_SECONDS = 10
    MAX_SAMPLES = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(220, 120)
        self.setMouseTracking(True)
        self.samples = deque(maxlen=self.MAX_SAMPLES)
        self.signal_name = "请选择测量信号"
        self.unit = ""
        self._hover_x = None

    def set_signal(self, name: str, unit: str) -> None:
        self.signal_name = name
        self.unit = unit
        self.clear()

    def clear(self) -> None:
        self.samples.clear()
        self._hover_x = None
        self.update()

    def mouseMoveEvent(self, event) -> None:
        self._hover_x = event.position().x()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_x = None
        self.update()
        super().leaveEvent(event)

    def append(self, timestamp: float, value: float) -> None:
        if not math.isfinite(value) or not math.isfinite(timestamp):
            return
        if self.samples and timestamp < self.samples[-1][0]:
            self.samples.clear()
        self.samples.append((timestamp, value))
        while self.samples and self.samples[0][0] < timestamp - self.WINDOW_SECONDS:
            self.samples.popleft()
        self.update()

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
            painter.setPen(QColor("#668F2E"))
            tick = maximum - (maximum - minimum) * index / 4
            painter.drawText(QRectF(2, position - 9, 42, 18), Qt.AlignRight | Qt.AlignVCenter,
                             format(tick, ".4g"))
        for index in range(11):
            position = plot.left() + plot.width() * index / 10
            painter.setPen(QPen(QColor("#BFC6BD"), 1, Qt.DotLine))
            painter.drawLine(QPointF(position, plot.top()), QPointF(position, plot.bottom()))
        painter.setPen(QColor("#687568"))
        painter.drawText(QRectF(plot.left(), plot.bottom() + 4, plot.width(), 20),
                         Qt.AlignLeft, "最近 10 秒")
        painter.drawText(QRectF(plot.left(), plot.bottom() + 4, plot.width(), 20),
                         Qt.AlignRight, "最新")
        if not self.samples:
            painter.drawText(plot, Qt.AlignCenter, "等待信号数据")
            return
        end_time = self.samples[-1][0]
        points = self._step_points(plot, minimum, maximum)
        painter.save()
        painter.setClipRect(plot.adjusted(-2, -2, 2, 2))
        painter.setPen(QPen(QColor("#7DAD38"), 2))
        painter.drawPolyline(points)
        painter.setBrush(QColor("#7DAD38"))
        painter.drawEllipse(points[-1], 3, 3)
        painter.restore()
        sample = self.samples[-1]
        cursor_x = plot.right()
        if self._hover_x is not None and plot.left() <= self._hover_x <= plot.right():
            cursor_x = self._hover_x
            timestamp = end_time - (plot.right() - cursor_x) / plot.width() * self.WINDOW_SECONDS
            timestamps = [entry[0] for entry in self.samples]
            if timestamp < timestamps[0]:
                return
            sample = self.samples[bisect_right(timestamps, timestamp) - 1]
            painter.setPen(QPen(QColor("#418AC7"), 1))
            painter.drawLine(QPointF(cursor_x, plot.top()), QPointF(cursor_x, plot.bottom()))
        text = f"{sample[1]:.10g}"
        label_width = min(plot.width(), painter.fontMetrics().horizontalAdvance(text) + 14)
        label_y = plot.bottom() - (sample[1] - minimum) / (maximum - minimum) * plot.height()
        label = QRectF(max(plot.left(), min(cursor_x + 8, plot.right() - label_width)),
                       max(plot.top(), min(label_y - 30, plot.bottom() - 24)), label_width, 24)
        painter.setPen(QPen(QColor("#7DAD38"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(label, 3, 3)
        painter.setPen(QColor("#263523"))
        painter.drawText(label, Qt.AlignCenter, text)

    def _step_points(self, plot: QRectF, minimum: float, maximum: float) -> QPolygonF:
        points = QPolygonF()
        end_time = self.samples[-1][0]
        for timestamp, value in self.samples:
            position = QPointF(
                plot.right() - (end_time - timestamp) / self.WINDOW_SECONDS * plot.width(),
                plot.bottom() - (value - minimum) / (maximum - minimum) * plot.height())
            if points:
                points.append(QPointF(position.x(), points[-1].y()))
            points.append(position)
        return points

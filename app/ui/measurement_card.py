"""紧凑信号测量卡片。"""

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QPainter, QPixmap, QPolygon, QPen, QColor
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from .signal_scope import SignalScope
from .signal_card import CornerGrip


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


class MeasurementGrip(CornerGrip):
    def __init__(self, card, sx, sy):
        super().__init__(card, sx, sy)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if not sx:
            self.setCursor(Qt.SizeVerCursor)
        elif not sy:
            self.setCursor(Qt.SizeHorCursor)

    def mouseMoveEvent(self, event):
        if self.band is None:
            return
        delta = event.globalPosition().toPoint() - self.start
        maximum = max(220, self.card.parentWidget().width()-12) if self.card.parentWidget() else 2000
        w = max(220, min(maximum, self.original.width()+self.sx*delta.x())) if self.sx else self.original.width()
        minimum_h = self.card.minimum_expanded_height() if self.card.scope else 36
        h = max(minimum_h, min(2000, self.original.height()+self.sy*delta.y())) if self.sy else self.original.height()
        self.target_size = QSize(w,h)
        x = self.original.x() if self.sx >= 0 else self.original.right()+1-w
        y = self.original.y() if self.sy >= 0 else self.original.bottom()+1-h
        self.band.setGeometry(QRect(QPoint(x,y), self.target_size))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.band is not None:
            self.mouseMoveEvent(event)
            self._finish(True)

    def _finish(self, commit):
        if self.band is None:
            return
        super()._finish(False)
        if commit:
            self.card.apply_size(self.target_size)


class MeasurementCard(QFrame):
    swap_requested = Signal(object, object)
    insert_requested = Signal(object, object, bool)
    resized = Signal()
    range_requested = Signal(float)
    remove_requested = Signal()
    merge_requested = Signal(object, object)
    unmerge_requested = Signal()
    selection_requested = Signal(object, bool)
    merge_selection_requested = Signal()
    auto_size_requested = Signal()
    size_requested = Signal(object, object)
    cursor_moved = Signal(object)
    move_out_requested = Signal(object)
    MIME_TYPE = "application/x-cartool-measurement-card"

    def __init__(self, name: str, unit: str, description: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("measurementCard")
        self.setStyleSheet(
            "QFrame#measurementCard { background: white; border: 1px solid #DDE3E8; border-radius: 6px; }"
            "QFrame#measurementCard[dragOver=\"true\"] { border-color: #DDE3E8; }"
            "QFrame#measurementCard[selected=\"true\"] { border: 2px solid #0078D4; }"
            "QToolTip { font-weight: normal; }")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.drag_group = parent
        self._drag_start = None
        self.scope = None
        self._time_max = 300.0
        self._custom_sizes = {}
        self._text_scale = 1.0
        self._merged_plot = None
        self._merge_center = False
        self._merge_hint = QLabel("松开合并", self)
        self._merge_hint.setStyleSheet("background:#DCEEFF; color:#005A9E; border:1px solid #86BFF0; padding:6px; font-weight:normal;")
        self._merge_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._merge_hint.hide()
        self._grips = [MeasurementGrip(self,x,y) for x,y in ((-1,-1),(1,-1),(-1,1),(1,1),(-1,0),(1,0),(0,-1),(0,1))]
        self._insert_before = True
        self._insert_mark = QFrame(self)
        self._insert_mark.setStyleSheet("background:#B8DFFF; border:none;")
        self._insert_mark.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._insert_mark.hide()
        self._valid_value = None
        self.name = name
        self.unit = unit
        self.name_label = ElidedLabel(name)
        name_font = self.name_label.font()
        name_font.setBold(True)
        self.name_label.setFont(name_font)
        self.name_label.setMinimumWidth(40)
        self.name_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.name_label.setToolTip(description + "\n拖动名称可插入到其他卡片前后")
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
        self._set_expand_icon(False)
        self.expand_button.setToolTip("展开波形")
        self.expand_button.setAccessibleName(f"展开 {name} 的波形")
        self.header_widget = QWidget()
        self.header_widget.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
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

    def apply_size(self, size):
        if self.scope is not None:
            size = QSize(size.width(), max(size.height(), self.minimum_expanded_height()))
        self.size_requested.emit(self, size)
        self._custom_sizes[self.scope is not None] = size
        self.resized.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for grip in sorted(self._grips, key=lambda g: bool(g.sx and g.sy)):
            corner = bool(grip.sx and grip.sy)
            thickness = 14 if corner else 6
            x = 0 if grip.sx < 0 else (self.width()-thickness if grip.sx > 0 else 14)
            y = 0 if grip.sy < 0 else (self.height()-thickness if grip.sy > 0 else 14)
            grip.setGeometry(x,y, self.width()-28 if not grip.sx else thickness,
                             self.height()-28 if not grip.sy else thickness)
            grip.raise_()
        if self.scope is None:
            self._text_scale = max(0.9, min(self.height()/36, self.width()/220))
        self._apply_text_style()

    def minimum_expanded_height(self):
        margins = self._layout.contentsMargins()
        height = self.header_widget.sizeHint().height() + margins.top() + margins.bottom()
        height += self._layout.spacing() + 80
        if self._merged_plot is not None:
            height += self._merged_legend.sizeHint().height() + self._layout.spacing()
        return max(160, height)

    def _apply_text_style(self):
        size = 9 if self.scope is not None else max(8,min(48,9*self._text_scale))
        for label, bold, color in ((self.name_label,True,"#000000"),
                                   (self.unit_label,False,"#6B7280"),
                                   (self.value_label,True,"#000000" if self._valid_value else "#9CA3AF")):
            if self._merged_plot is not None and label is not self.unit_label:
                color = self._merged_plot.COLORS[0]
            font = label.font()
            font.setPointSizeF(size)
            font.setBold(bold)
            label.setFont(font)
            label.setStyleSheet(
                f"QLabel {{ color: {color}; font-size: {size:g}pt; font-weight: {'bold' if bold else 'normal'}; }}"
                "QToolTip { font-weight: normal; }")

    def eventFilter(self, watched, event) -> bool:
        if watched not in (self.header_widget, self.name_label, self.unit_label):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.selection_requested.emit(self, bool(event.modifiers() & Qt.ControlModifier))
            self.setFocus(Qt.MouseFocusReason)
            self._drag_start = event.globalPosition().toPoint()
            event.accept()
            return True
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

    def _refresh_selection_border(self, selected):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def focusInEvent(self, event):
        self._refresh_selection_border(bool(getattr(self, '_multi_selected', self.hasFocus())))
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._refresh_selection_border(bool(getattr(self, '_multi_selected', False)))
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selection_requested.emit(self, bool(event.modifiers() & Qt.ControlModifier))
            self.setFocus(Qt.MouseFocusReason)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.remove_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        self.setFocus(Qt.MouseFocusReason)
        menu = QMenu(self)
        menu.addAction("恢复默认缩放", self.auto_size_requested.emit)
        if getattr(self, '_can_merge_selection', False):
            menu.addAction("合并选中信号", self.merge_selection_requested.emit)
            menu.addSeparator()
        if self._merged_plot is not None:
            move_menu = menu.addMenu("移出信号")
            for member in self._merged_plot.cards:
                move_menu.addAction(member.name, lambda checked=False, c=member: self.move_out_requested.emit(c))
            menu.addAction("解散分组", self.unmerge_requested.emit)
        menu.addSeparator()
        menu.addAction("删除", self.remove_requested.emit)
        menu.exec(event.globalPos())
        event.accept()

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
        self._insert_mark.setVisible(active and not self._merge_center)
        self._merge_hint.setVisible(active and self._merge_center)
        self.update()

    def _update_insert_position(self, position):
        x = position.x()/max(1,self.width())
        y = position.y()/max(1,self.height())
        self._merge_center = .25 < x < .75 and .25 < y < .75
        self._merge_hint.adjustSize()
        self._merge_hint.move(max(0,(self.width()-self._merge_hint.width())//2),max(0,(self.height()-self._merge_hint.height())//2))
        self._merge_hint.raise_()
        edge = min(((x,"left"),(1-x,"right"),(y,"top"),(1-y,"bottom")))[1]
        self._insert_before = edge in ("left","top")
        if edge in ("left","right"):
            self._insert_mark.setGeometry(0 if edge == "left" else self.width()-5,0,5,self.height())
        else:
            self._insert_mark.setGeometry(0,0 if edge == "top" else self.height()-5,self.width(),5)
        self._insert_mark.raise_()

    def dragEnterEvent(self, event) -> None:
        if self._accepts_drag(event.source(), event.mimeData()):
            self._update_insert_position(event.position())
            self._highlight_drop(True)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        allowed = self._accepts_drag(event.source(), event.mimeData())
        if allowed:
            self._update_insert_position(event.position())
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
            self._update_insert_position(event.position())
            self._highlight_drop(False)
            if self._merge_center:
                self.merge_requested.emit(event.source(), self)
            else:
                self.insert_requested.emit(event.source(), self, self._insert_before)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _set_expand_icon(self, expanded):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("white" if expanded else "#263238"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        points = [QPoint(4,10),QPoint(8,6),QPoint(12,10)] if expanded else [QPoint(4,6),QPoint(8,10),QPoint(12,6)]
        painter.drawPolyline(QPolygon(points))
        painter.end()
        self.expand_button.setArrowType(Qt.NoArrow)
        self.expand_button.setIcon(QIcon(pixmap))

    def _set_expanded(self, expanded: bool) -> None:
        if self._merged_plot is not None:
            if not expanded:
                for member in self._merged_plot.cards:
                    if member.scope is not None:
                        member.scope.clear()
                self._merged_plot._hover_x = None
                self._merged_plot.set_linked_cursor(None)
            self._merged_plot.setVisible(expanded)
            self._set_expand_icon(expanded)
            self.resized.emit()
            return
        if expanded and self.scope is None:
            self._text_scale = max(0.9,min(self.height()/36,self.width()/220))
            self._custom_sizes[False] = self.size()
            previous = self._custom_sizes.get(True)
            self._custom_sizes[True] = QSize(self.width(), previous.height() if previous else self.height()+140)
        elif not expanded and self.scope is not None:
            self._custom_sizes[True] = self.size()
            collapsed = self._custom_sizes.get(False, QSize(self.width(),36))
            self._custom_sizes[False] = QSize(self.width(),collapsed.height())
        self._set_expand_icon(expanded)
        self.expand_button.setToolTip("收起波形" if expanded else "展开波形")
        if expanded and self.scope is None:
            self.scope = SignalScope()
            self.scope.set_signal(self.name, self.unit)
            self.scope._view_time = getattr(self, "_shared_time", None)
            self.scope.set_window_seconds(self._time_max)
            self.scope.time_range_changed.connect(self._set_time_max)
            self.scope.cursor_moved.connect(self.cursor_moved.emit)
            self.scope.set_linked_cursor(getattr(self,"_linked_cursor",None))
            self.scope.setMinimumSize(0, 0)
            self.scope.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._layout.addWidget(self.scope)
            self.scope.show()
        if not expanded and self.scope is not None:
            self._layout.removeWidget(self.scope)
            self.scope.hide()
            self.scope.deleteLater()
            self.scope = None
        self._apply_text_style()
        self.updateGeometry()
        self.resized.emit()

    def _set_time_max(self, seconds: float) -> None:
        self._time_max = seconds
        self.range_requested.emit(seconds)
        if self.scope is not None:
            self.scope.set_window_seconds(seconds)

    def set_value(self, text: str, valid: bool) -> None:
        if self.value_label.text() != text:
            self.value_label.setText(text)
            self.value_label.setToolTip(text)
        if self._valid_value != valid:
            self._valid_value = valid
            self._apply_text_style()

    def clear(self) -> None:
        self.set_value("—", False)
        if self.scope is not None:
            self.scope.clear()

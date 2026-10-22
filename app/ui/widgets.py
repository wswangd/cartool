"""表格编辑委托：下拉框与数字输入。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QSplitter, QSplitterHandle, QStyle, QStyledItemDelegate, QStyleOptionViewItem


class CenteredCheckDelegate(QStyledItemDelegate):
    """居中绘制；每次按下（包括双击的第二次按下）切换一次。"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        rect = style.subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt, opt.widget)
        rect.moveCenter(opt.rect.center())
        state = opt.checkState
        opt.features &= ~QStyleOptionViewItem.HasCheckIndicator
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        opt.rect = rect
        opt.state &= ~(QStyle.State_On | QStyle.State_Off | QStyle.State_NoChange)
        opt.state |= QStyle.State_On if state == Qt.Checked else QStyle.State_Off
        style.drawPrimitive(QStyle.PE_IndicatorItemViewItemCheck, opt, painter, opt.widget)

    def editorEvent(self, event, model, option, index):
        flags = index.flags()
        if not (flags & Qt.ItemIsEnabled and flags & Qt.ItemIsUserCheckable):
            return False
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.MouseButtonRelease):
            if event.button() != Qt.LeftButton or not option.rect.contains(event.position().toPoint()):
                return False
            if event.type() == QEvent.MouseButtonRelease:
                return True
        elif event.type() == QEvent.KeyPress:
            if event.key() not in (Qt.Key_Space, Qt.Key_Select):
                return False
            if event.isAutoRepeat():
                return True
        else:
            return False
        current = index.data(Qt.CheckStateRole)
        checked = current == Qt.Checked or current == Qt.Checked.value
        return model.setData(index, Qt.Unchecked.value if checked else Qt.Checked.value,
                             Qt.CheckStateRole)


class OperationButton(QPushButton):
    """根据操作文字同步开始、停止按钮的视觉状态。"""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        # 自定义背景会绕过原生按钮的最小宽度，保留应用样式原有尺寸。
        self.setMinimumWidth(self.sizeHint().width())
        self.setText(text)

    def setText(self, text):
        super().setText(text)
        self._refresh_style()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self._refresh_style()

    def _refresh_style(self):
        if not self.isEnabled():
            self.setStyleSheet("")
            return
        text = self.text()
        stopping = any(word in text for word in ("停止", "暂停", "关闭设备"))
        color, hover = ("#C62828", "#B71C1C") if stopping else ("#0078D4", "#006CBD")
        self.setStyleSheet(f"""
            QPushButton {{ background-color: {color}; color: white;
                border: 1px solid {color}; border-radius: 4px; padding: 4px 10px; }}
            QPushButton:hover {{ background-color: {hover}; border-color: {hover}; }}
            QPushButton:pressed {{ background-color: {hover}; border-color: {hover}; }}
        """)


class ComboDelegate(QStyledItemDelegate):
    def __init__(self, options: tuple[str, ...] | list[str], parent=None) -> None:
        super().__init__(parent)
        self._options = list(options)

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(self._options)
        combo.setEditable(False)
        return combo

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)

    def setEditorData(self, editor: QComboBox, index) -> None:
        current = index.data()
        editor.setCurrentIndex(self._options.index(current) if current in self._options
                              else 0)

    def setModelData(self, editor: QComboBox, model, index) -> None:
        model.setData(index, editor.currentText())

    def paint(self, painter, option, index) -> None:
        # 只绘制背景（选中/交替行色），不绘制文字，由持久化编辑器控件显示
        from PySide6.QtWidgets import QStyleOptionViewItem
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # 填充单元格背景（处理选中高亮、交替行色等）
        opt.widget.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)
        # 不绘制文字，由持久化编辑器控件覆盖


class SpinDelegate(QStyledItemDelegate):
    def __init__(self, minimum: int, maximum: int, parent=None,
                 special_zero_text: str = "", step: int = 1) -> None:
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._zero_text = special_zero_text
        self._step = step

    def createEditor(self, parent, option, index):
        from PySide6.QtWidgets import QSpinBox
        spin = QSpinBox(parent)
        spin.setRange(self._min, self._max)
        spin.setSingleStep(self._step)
        if self._zero_text:
            spin.setSpecialValueText(self._zero_text)
        return spin

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)

    def setEditorData(self, editor, index) -> None:
        text = str(index.data() or "0").strip()
        try:
            value = int(text)
        except ValueError:
            value = self._min
        editor.setValue(max(self._min, min(self._max, value)))

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, str(editor.value()))

    def paint(self, painter, option, index) -> None:
        from PySide6.QtWidgets import QStyleOptionViewItem
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.widget.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)


class HoverSplitterHandle(QSplitterHandle):
    """通过进入/离开事件即时显示高亮，不依赖平台样式的 hover 支持。"""

    def __init__(self, orientation, parent) -> None:
        super().__init__(orientation, parent)
        self._hovered = False
        self._pressed = False
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._show_hover)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)

    def _show_hover(self) -> None:
        self._hovered = True
        self.update()

    def enterEvent(self, event) -> None:
        self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_timer.stop()
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._hover_timer.stop()
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pressed = False
        self._hovered = self.underMouse()
        self.update()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        splitter = self.splitter()
        index = next((i for i in range(1, splitter.count())
                      if splitter.handle(i) is self), None)
        if index is not None:
            sizes = splitter.sizes()
            total = sizes[index - 1] + sizes[index]
            axis = 'height' if splitter.orientation() == Qt.Vertical else 'width'
            before, after = splitter.widget(index - 1), splitter.widget(index)
            def preferred(widget):
                return max(1, getattr(widget.sizeHint(), axis)(),
                           getattr(widget.minimumSizeHint(), axis)())
            if total > 0:
                # 默认让分隔条前的内容恢复到自然尺寸；后侧折叠时优先恢复后侧。
                target = index if sizes[index] == 0 else index - 1
                other = index - 1 if target == index else index
                minimum_other = max(1, getattr(splitter.widget(other).minimumSizeHint(), axis)())
                desired = preferred(splitter.widget(target))
                sizes[target] = max(1, min(desired, total - min(minimum_other, total // 2)))
                sizes[other] = total - sizes[target]
            else:
                # 两个相邻区域都折叠时，从其他区域借出空间恢复内容。
                desired = [preferred(before), preferred(after)]
                available = sum(sizes)
                budget = min(sum(desired), max(2, available * 2 // 3))
                remaining = max(0, available - budget)
                sizes = [round(v * remaining / available) if available else 0 for v in sizes]
                sizes[index - 1] = max(1, round(budget * desired[0] / sum(desired)))
                sizes[index] = max(1, budget - sizes[index - 1])
            splitter.setSizes(sizes)
        self._pressed = False
        self._hovered = self.underMouse()
        self.update()
        event.accept()

    def paintEvent(self, event) -> None:
        if self._hovered or self._pressed:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.palette().highlight())
            painter.drawRoundedRect(self.rect(), 3, 3)


class HoverSplitter(QSplitter):
    """保留拖动热区，仅在悬停和拖动时显示分隔条。"""

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(7)

    def createHandle(self):
        return HoverSplitterHandle(self.orientation(), self)

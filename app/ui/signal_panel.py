"""信号实验面板：垂直滑杆设置物理值，按映射打包成报文周期发送。"""

from __future__ import annotations

import math

from .widgets import OperationButton

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QHBoxLayout, QLabel, QStyle, QStyleOptionButton, QStylePainter,
                               QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget, QLineEdit, QFrame)

from .. import signals
from ..frame import CanFrame
from ..signals import SignalDef
from .signal_card import SignalCard
from .mapping_dialog import MappingDialog, make_decimal_spin

CARD_WIDTH = 180
CARD_SPACING = 6
EMIT_DELAY_MS = 120
PERIOD_DEFAULT_MS = 100   # 映射配置中未设置周期的 CAN ID 的兜底周期
SLIDER_INT_LIMIT = 2_000_000_000  # QSlider 为 int32，缩放后范围钳制


def _decimal_places(value: float) -> int:
    """计算浮点数的小数位数（最多 9 位，避免浮点表示噪声）。"""
    if value == 0 or value == int(value):
        return 0
    s = f"{abs(value):.9f}".rstrip('0').rstrip('.')
    if '.' not in s:
        return 0
    return len(s.split('.')[1])


def _compute_scale(factor: float, *values: float) -> int:
    """计算使 factor 和所有 values 都能精确表示为整数的放大倍数。

    factor=1   -> scale=1（整数步进）
    factor=0.1 -> scale=10
    factor=0.0001 -> scale=10000
    同时确保 min/max/default 的精度不会在缩放时被截断。
    """
    all_vals = [factor] + [v for v in values if v != 0]
    places = max((_decimal_places(v) for v in all_vals), default=0)
    return 10 ** min(places, 9)


def _nice_tick_interval(total_range: int, step: int) -> int:
    """选择一个好看的刻度间隔，是 step 的整数倍，使得总刻度数在 5-10 之间。

    返回值（整数）是 slider 整数空间中的刻度间距，保证刻度精确对应采样点。
    """
    if total_range <= 0 or step <= 0:
        return step
    total_steps = total_range // step
    if total_steps <= 10:
        return step
    target = max(1, round(total_steps / 7))
    magnitude = 10 ** (len(str(target)) - 1)
    for nice in (1, 2, 5, 10):
        if nice * magnitude >= target:
            return nice * magnitude * step
    return 10 * magnitude * step


class NoWheelSlider(QSlider):
    """水平滑杆：仅在获得焦点（点击选中）后滚轮才改值，防止悬停误触。"""

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ValueComboBox(QComboBox):
    """合并数值编辑与状态选择，禁用滚轮误触。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setReadOnly(True)

    def wheelEvent(self, event) -> None:
        """禁用鼠标滚轮，防止误触修改。"""
        event.ignore()

class SignalCheckBox(QCheckBox):
    """仅单独绘制名称颜色，勾选指示器使用系统调色板。"""

    name_color = None

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._name_drag_start = None
        self.drag_card = None
        self.setMouseTracking(True)

    def _over_indicator(self, position):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self).adjusted(-3, -3, 3, 3).contains(position)

    def mousePressEvent(self, event):
        self._name_drag_start = None
        if event.button() == Qt.LeftButton and not self._over_indicator(event.position().toPoint()):
            self._name_drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = event.position().toPoint()
        self.setCursor(Qt.ArrowCursor if self._over_indicator(point) else Qt.OpenHandCursor)
        if (self._name_drag_start is not None and event.buttons() & Qt.LeftButton
                and (point - self._name_drag_start).manhattanLength() >= QApplication.startDragDistance()
                and self.drag_card is not None):
            self._name_drag_start = None
            self.setDown(False)
            self.drag_card.start_drag(self.mapTo(self.drag_card, point))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._name_drag_start = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator = QStyleOptionButton(option)
        indicator.rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        painter.drawPrimitive(QStyle.PE_IndicatorCheckBox, indicator)
        if self.name_color is not None:
            option.palette.setColor(QPalette.WindowText, QColor(self.name_color))
        text_rect = self.style().subElementRect(QStyle.SE_CheckBoxContents, option, self)
        self.style().drawItemText(painter, text_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextShowMnemonic,
                                  option.palette, self.isEnabled(), option.text, QPalette.WindowText)


class _Column:
    """一路信号的滑杆 + 启用 + 单位 + 编辑器控件集合。"""

    def __init__(self, sig: SignalDef) -> None:
        self.sig = sig
        # 根据信号分辨率动态计算放大倍数，保证滑杆每步精确对应一个分辨率
        self.scale = _compute_scale(sig.factor, sig.minimum, sig.maximum,
                                    sig.default)
        # 滑杆整数空间的单步 = factor * scale（整数，无精度损失）
        self.step = max(1, round(sig.factor * self.scale))
        # 显示精度：至少 4 位，同时覆盖 factor/min/max/default 的小数位
        self.decimals = max(_decimal_places(sig.factor),
                            _decimal_places(sig.minimum),
                            _decimal_places(sig.maximum),
                            _decimal_places(sig.default), 4)

        self.widget = SignalCard()
        self.widget.setObjectName("signalCard")
        self.widget.setMinimumWidth(CARD_WIDTH)
        self.widget.setStyleSheet(
            "QFrame#signalCard { background-color: #FFFFFF; border: 1px solid #D8DDE3; border-radius: 8px; }"
            "QFrame#signalCard[active=\"false\"] { background-color: palette(window); border-color: #E5E7EB; }")
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(4)

        # 滑杆范围对齐到 factor 网格：最小值向上取整、最大值向下取整，
        # 确保每个采样点都精确落在刻度上，无截断
        lo = max(round(sig.minimum * self.scale), -SLIDER_INT_LIMIT)
        hi = min(round(sig.maximum * self.scale), SLIDER_INT_LIMIT)
        if lo > 0:
            lo = ((lo + self.step - 1) // self.step) * self.step
        else:
            lo = (lo // self.step) * self.step
        hi = (hi // self.step) * self.step

        self.slider = NoWheelSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(max(lo, min(hi, round(sig.default * self.scale))))
        self.slider.setSingleStep(self.step)        # 单步 = 1 个分辨率
        self.slider.setPageStep(self.step * 10)     # 翻页 = 10 个分辨率
        self.slider.setToolTip(sig.name)
        # 刻度间隔 = 分辨率的整数倍，使刻度精确对应采样点
        tick_interval = _nice_tick_interval(hi - lo, self.step)
        self.slider.setTickPosition(QSlider.NoTicks)
        self.slider.setTickInterval(tick_interval)

        self.check = SignalCheckBox(sig.name)
        self.check.drag_card = self.widget
        name_font = self.check.font()
        name_font.setBold(True)
        self.check.setFont(name_font)
        self.check.setChecked(sig.enabled)

        self._value_font_size = 24
        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet("color: #000000; font-size: 24px; font-weight: 700;")
        self.value_label.setMinimumWidth(72)
        header_widget = QWidget()
        header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self.check)
        header.addStretch(1)
        unit_label = QLabel(sig.unit)
        unit_label.setStyleSheet("color: #6B7280;")
        header.addWidget(unit_label)
        layout.addWidget(header_widget)
        self.value_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.value_label, 1)


        self.spin = make_decimal_spin(sig.default, sig.minimum, sig.maximum)
        self.spin.setDecimals(self.decimals)
        self.spin.setSingleStep(sig.factor)
        self.spin.setStepType(QDoubleSpinBox.StepType.DefaultStepType)
        self.spin.setMinimumWidth(120)
        layout.addWidget(self.spin)
        self.combo: ValueComboBox | None = None
        self._state_idx = 0          # combo 真实选中索引
        if sig.editor == "state" and sig.states:
            self.spin.hide()
            self.combo = ValueComboBox()
            self.combo.setMinimumWidth(120)
            for state_index, state in enumerate(sig.states):
                self.combo.addItem(state.name)
                self.combo.setItemData(state_index,
                                       QBrush(QColor("#000000" if state_index == 0 else "#C62828")),
                                       Qt.ForegroundRole)
            self.combo.lineEdit().editingFinished.connect(self._on_combo_value_edited)
            layout.addWidget(self.combo)
            self._refresh_combo_display()
        slider_group = QWidget()
        slider_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        slider_layout = QVBoxLayout(slider_group)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(0)
        slider_layout.addWidget(self.slider)
        bounds = QHBoxLayout()
        bounds.setContentsMargins(0, 0, 0, 0)
        bounds.addWidget(QLabel(f"{sig.minimum:g}"))
        bounds.addStretch(1)
        bounds.addWidget(QLabel(f"{sig.maximum:g}"))
        slider_layout.addLayout(bounds)
        layout.addWidget(slider_group)
        self._refresh_value_display()
        self.widget.base_min_size = self.widget.minimumSizeHint()

    def set_display_scale(self, scale: float) -> None:
        font_size = max(18, min(96, round(24 * scale)))
        if self._value_font_size != font_size:
            self._value_font_size = font_size
            self._refresh_value_display()

    def phys_value(self) -> float:
        """当前物理值（精度与分辨率一致）。"""
        return self.slider.value() / self.scale

    def raw_value(self) -> int:
        if self.combo is not None:
            raw = self.sig.states[self._state_idx].raw
            if raw is not None:
                return raw
        return self.sig.phys_to_raw(self.phys_value())

    def active_state(self) -> SignalState | None:
        """返回当前激活的 SignalState（如果有 combo 且选中了状态）。"""
        if self.combo is not None:
            return self.sig.states[self._state_idx]
        return None

    def set_phys(self, value: float) -> None:
        clamped = max(self.sig.minimum, min(self.sig.maximum, float(value)))
        self.slider.setValue(round(clamped * self.scale))

    def set_state(self, name: str) -> None:
        if self.combo is not None and name:
            for i, state in enumerate(self.sig.states):
                if state.name == name:
                    self._state_idx = i
                    self.combo.blockSignals(True)
                    self.combo.setCurrentIndex(i)
                    self.combo.blockSignals(False)
                    self._refresh_combo_display()
                    return

    def _refresh_combo_display(self) -> None:
        """同步状态选择、数值显示和编辑权限。"""
        self._refresh_value_display()
        if self.combo is None:
            return
        editable = self.check.isChecked() and self._state_idx == 0 and self.sig.states[0].raw is None
        self.spin.setEnabled(editable)
        self.slider.setEnabled(self.check.isChecked()
                               and self.sig.states[self._state_idx].raw is None)
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(self._state_idx)
        self.combo.lineEdit().setReadOnly(not editable)
        self.combo.setEditText(f"{self.phys_value():.{self.decimals}f}" if self._state_idx == 0
                               else self.sig.states[self._state_idx].name)
        self.combo.blockSignals(False)
        color = ("#9CA3AF" if not self.check.isChecked()
                 else "#C62828" if self._state_idx else "#000000")
        self.combo.setStyleSheet(
            f"QComboBox {{ font-weight: normal; color: {color}; }}"
            f"QComboBox QLineEdit {{ color: {color}; }}")

    def _on_combo_value_edited(self) -> None:
        if self.combo.lineEdit().isReadOnly():
            return
        try:
            value = float(self.combo.currentText())
        except ValueError:
            self._refresh_combo_display()
            return
        if math.isfinite(value):
            self.set_phys(value)
        self._refresh_combo_display()

    def _refresh_value_display(self) -> None:
        value = self.phys_value()
        abnormal = self.combo is not None and self._state_idx != 0
        active = self.check.isChecked()
        color = "#9CA3AF" if not active else "#C62828" if abnormal else "#000000"
        self.value_label.setStyleSheet(
            f"color: {color}; font-size: {self._value_font_size}px; font-weight: 700;")
        if self.combo is not None:
            raw = self.sig.states[self._state_idx].raw
            if raw is not None:
                value = self.sig.raw_to_phys(raw)
        text = f"{value:.{self.decimals}f}".rstrip("0").rstrip(".")
        self.value_label.setText(text if text != "-0" else "0")
        self.widget.setProperty("active", self.check.isChecked())
        name_color = "#C62828" if abnormal else ("#000000" if active else "#6B7280")
        self.check.name_color = name_color
        self.check.update()
        editable = active and (self.combo is None or
                               (self._state_idx == 0 and self.sig.states[0].raw is None))
        self.spin.setEnabled(editable)
        self.slider.setEnabled(active and (self.combo is None or
                               self.sig.states[self._state_idx].raw is None))
        if self.combo is not None:
            self.combo.setEnabled(active)
        for label in self.widget.findChildren(QLabel):
            if label is not self.value_label:
                label.setEnabled(active)
        self.widget.style().unpolish(self.widget)
        self.widget.style().polish(self.widget)

class SignalPanel(QWidget):
    start_requested = Signal(object, object)   # list[CanFrame], dict[int,int] cycles
    stop_requested = Signal()
    values_changed = Signal(object)            # list[CanFrame]，节流后发出
    mapping_changed = Signal(object)           # list[str] 信号名列表，映射配置变更时发出
    message = Signal(str)

    def __init__(self, defs: list[SignalDef], parent=None) -> None:
        super().__init__(parent)
        self._defs = defs
        self._cycles: dict[int, int] = signals.load_cycles()
        self._columns: list[_Column] = []
        self._card_order = []
        self._card_sizes = {}
        self._auto_size = False
        self._running = False
        self._bus_enabled = False
        self._external_locked = False
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.timeout.connect(self._emit_values)
        self._build()
        self._rebuild_columns()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索信号名称或单位")
        self.search_edit.setMinimumWidth(300)
        self.search_edit.setMaximumWidth(420)
        top.addWidget(self.search_edit)
        self.enabled_only = QCheckBox("仅显示启用")
        top.addWidget(self.enabled_only)
        top.addStretch(1)

        self.mapping_button = QPushButton("信号配置")
        self.mapping_button.setFixedWidth(90)
        top.addWidget(self.mapping_button)
        self.start_button = OperationButton("开始")
        self.start_button.setEnabled(False)
        self.start_button.setFixedWidth(90)
        top.addWidget(self.start_button)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.scroll, 1)

        self.mapping_button.clicked.connect(self._on_mapping)
        self.start_button.clicked.connect(self._on_start_clicked)
        self.search_edit.textChanged.connect(self._layout_cards)
        self.enabled_only.toggled.connect(self._layout_cards)

    def _rebuild_columns(self) -> None:
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        container = QWidget()
        container.setObjectName("signalCardsContainer")
        container.installEventFilter(self)
        container.setAcceptDrops(True)
        self.scroll.viewport().setAcceptDrops(True)
        self._insert_marker = QFrame(container)
        self._insert_marker.setStyleSheet("background: #B8DFFF; border: 1px solid #86BFF0; border-radius: 2px;")
        self._insert_marker.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._insert_marker.hide()
        self._insert_target = None
        self._drag_position = None
        if not hasattr(self, '_drag_scroll_timer'):
            self._drag_scroll_timer = QTimer(self)
            self._drag_scroll_timer.setInterval(40)
            self._drag_scroll_timer.timeout.connect(self._drag_scroll)
        container.setStyleSheet("QWidget#signalCardsContainer { background-color: white; }")
        grid = QGridLayout()
        self._card_grid = grid
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(CARD_SPACING)
        grid.setAlignment(Qt.AlignTop)
        
        self._columns = []
        for index, sig in enumerate(self._defs):
            column = _Column(sig)
            self._columns.append(column)
            column.widget.setParent(container)
            column.widget.panel = self
            column.widget.signal_name = sig.name
            column.slider.valueChanged.connect(lambda v, c=column: self._on_slider(c, v))
            column.check.toggled.connect(
                lambda v, idx=index: self._on_check_toggled(idx, v))
            if column.spin is not None:
                column.spin.valueChanged.connect(
                    lambda v, c=column: self._on_spin(c, float(v)))
            if column.combo is not None:
                column.combo.currentIndexChanged.connect(
                    lambda _=0, c=column: self._on_state(c))
        
        self.scroll.setWidget(container)
        self._layout_cards()

    def _set_all_checked(self, checked: bool) -> None:
        for column in self._columns:
            column.check.blockSignals(True)
            column.check.setChecked(checked)
            column.check.blockSignals(False)
            column.sig.enabled = checked
            column._refresh_combo_display()
        self._layout_cards()
        self._emit_values()

    def _show_selection_menu(self, position) -> None:
        menu = QMenu(self)
        menu.addAction("全选", lambda: self._set_all_checked(True))
        menu.addAction("全不选", lambda: self._set_all_checked(False))
        menu.addSeparator()
        menu.addAction("恢复默认缩放", self._auto_adjust_cards)
        menu.exec(position)

    def contextMenuEvent(self, event) -> None:
        child = self.childAt(event.pos())
        if child is not None and any(c.widget is child or c.widget.isAncestorOf(child)
                                     for c in self._columns):
            event.ignore()
            return
        self._show_selection_menu(event.globalPos())
        event.accept()

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            source = event.source()
            if isinstance(source, SignalCard) and source.panel is self and event.mimeData().hasFormat(SignalCard.MIME):
                event.setDropAction(Qt.MoveAction)
                event.accept()
                self._drag_position = watched.mapToGlobal(event.position().toPoint())
                self._update_insert_marker(self.scroll.widget().mapFromGlobal(self._drag_position))
                if event.type() == QEvent.Drop:
                    if self._insert_target:
                        self._move_card(source.signal_name, *self._insert_target)
                    self._insert_marker.hide()
                    self._drag_position = None
                    self._drag_scroll_timer.stop()
                else:
                    self._drag_scroll_timer.start()
                return True
        if event.type() == QEvent.DragLeave:
            self._insert_marker.hide()
            self._drag_position = None
            self._drag_scroll_timer.stop()
        if event.type() == QEvent.ContextMenu and (watched is self.scroll.viewport() or
                                                   watched is self.scroll.widget()):
            child = watched.childAt(event.pos())
            if child is None or not any(c.widget is child or c.widget.isAncestorOf(child)
                                        for c in self._columns):
                self._show_selection_menu(event.globalPos())
                return True
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._layout_cards)
        return super().eventFilter(watched, event)

    def _layout_cards(self) -> None:
        if not hasattr(self, "_card_grid"):
            return
        grid = self._card_grid
        while grid.count():
            grid.takeAt(0)
        for column_index in range(grid.columnCount()):
            grid.setColumnStretch(column_index, 0)
            grid.setColumnMinimumWidth(column_index, 0)
        query = self.search_edit.text().strip().casefold()
        visible = []
        for column in self._ordered_columns():
            matched = (not query or query in f"{column.sig.name} {column.sig.unit}".casefold())
            matched = matched and (not self.enabled_only.isChecked() or column.check.isChecked())
            column.widget.setVisible(matched)
            if matched:
                visible.append(column)
        width = max(1, self.scroll.viewport().width() - 4)
        minimum_width = max([CARD_WIDTH] + [c.widget.base_min_size.width() for c in visible])
        minimum_height = max([1] + [c.widget.base_min_size.height() for c in visible])
        count = max(1, min(len(visible), (width + CARD_SPACING) // (minimum_width + CARD_SPACING)))
        height = minimum_height
        if self._auto_size and visible:
            available_height = max(1, self.scroll.viewport().height() - 4)
            choices = []
            for columns in range(1, count + 1):
                rows = math.ceil(len(visible) / columns)
                h = (available_height - CARD_SPACING * (rows - 1)) // rows
                if h >= minimum_height:
                    w = (width - CARD_SPACING * (columns - 1)) // columns
                    choices.append((min(w / minimum_width, h / minimum_height), columns, h))
            if choices:
                _, count, height = max(choices)
        default_width = max(minimum_width, (width - CARD_SPACING * (count - 1)) // count)
        placed = []
        for column in visible:
            size = self._card_sizes.get(column.sig.name, [default_width, height])
            w = min(width, max(column.widget.base_min_size.width(), size[0]))
            h = max(column.widget.base_min_size.height(), size[1])
            # 从上到下、从左到右寻找可容纳的位置，不再等待整行最高卡片。
            xs = sorted({2} | {r.right() + 1 + CARD_SPACING for r in placed})
            ys = sorted({2} | {r.bottom() + 1 + CARD_SPACING for r in placed})
            target = None
            for y in ys:
                for x in xs:
                    if x + w > width + 2:
                        continue
                    candidate = QRect(x, y, w, h)
                    if not any(candidate.intersects(r.adjusted(-CARD_SPACING, -CARD_SPACING,
                                                               CARD_SPACING, CARD_SPACING)) for r in placed):
                        target = candidate
                        break
                if target is not None:
                    break
            if target is None:
                target = QRect(2, max((r.bottom() + 1 + CARD_SPACING for r in placed), default=2), w, h)
            column.set_display_scale(min(w / max(CARD_WIDTH, column.widget.base_min_size.width()),
                                         h / column.widget.base_min_size.height()))
            column.widget.setGeometry(target)
            placed.append(target)
        self.scroll.widget().setMinimumHeight(max((r.bottom() + 3 for r in placed), default=4))

    def _auto_adjust_cards(self) -> None:
        self._card_sizes.clear()
        self._auto_size = True
        self._layout_cards()

    def _resize_card(self, name, size) -> None:
        self._card_sizes[name] = [size.width(), size.height()]
        self._layout_cards()

    def _drag_scroll(self) -> None:
        if self._drag_position is None:
            return
        point = self.scroll.viewport().mapFromGlobal(self._drag_position)
        delta = -14 if point.y() < 30 else (14 if point.y() > self.scroll.viewport().height() - 30 else 0)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + delta)
        self._update_insert_marker(self.scroll.widget().mapFromGlobal(self._drag_position))

    def _update_insert_marker(self, point) -> None:
        visible = [c for c in self._ordered_columns() if not c.widget.isHidden()]
        if not visible:
            return
        target = min(visible, key=lambda c: (
            max(c.widget.y()-point.y(), 0, point.y()-c.widget.geometry().bottom()) * 10000
            + abs(c.widget.geometry().center().x()-point.x())))
        rect = target.widget.geometry()
        before = point.x() <= rect.center().x()
        self._insert_target = (target.sig.name, before)
        x = rect.left()-3 if before else rect.right()+1
        self._insert_marker.setGeometry(max(0,x), rect.top(), 5, rect.height())
        self._insert_marker.show()
        self._insert_marker.raise_()

    def _move_card(self, name, target, before) -> None:
        visible = [c.sig.name for c in self._ordered_columns() if not c.widget.isHidden()]
        if name not in visible or target not in visible or name == target:
            return
        all_names = [c.sig.name for c in self._ordered_columns()]
        slots = [i for i, n in enumerate(all_names) if n in visible]
        visible.remove(name)
        visible.insert(visible.index(target) + (0 if before else 1), name)
        for i, n in zip(slots, visible):
            all_names[i] = n
        self._card_order = all_names
        self._layout_cards()

    def _ordered_columns(self):
        rank = {name: i for i, name in enumerate(self._card_order)}
        return sorted(self._columns, key=lambda c: rank.get(c.sig.name, len(rank)))

    def card_layout_settings(self) -> dict:
        return {"auto_size": self._auto_size, "sizes": self._card_sizes.copy(),
                "order": [c.sig.name for c in self._ordered_columns()]}

    def restore_card_layout(self, settings) -> None:
        if not isinstance(settings, dict):
            return
        self._card_order = list(dict.fromkeys(n for n in settings.get("order", []) if isinstance(n, str))) if isinstance(settings.get("order", []), list) else []
        self._auto_size = bool(settings.get("auto_size", False))
        sizes = settings.get("sizes", {})
        if isinstance(sizes, dict):
            self._card_sizes = {name: list(size) for name, size in sizes.items()
                                if isinstance(size, (list, tuple)) and len(size) == 2
                                and all(type(v) is int and 1 <= v <= 3000 for v in size)}
        self._layout_cards()

    # ------------------------------------------------------------------ 联动

    def _on_check_toggled(self, idx: int, checked: bool) -> None:
        """启用状态变更时同步到 defs，使映射配置中也能看到最新状态。"""
        if idx < len(self._defs):
            self._defs[idx].enabled = checked
            self._columns[idx]._refresh_combo_display()
        if self.enabled_only.isChecked():
            self._layout_cards()
        self._emit_values()

    def _on_slider(self, column: _Column, scaled: int) -> None:
        # 将滑杆值对齐到分辨率网格
        aligned = round(scaled / column.step) * column.step
        if aligned != scaled:
            column.slider.blockSignals(True)
            column.slider.setValue(aligned)
            column.slider.blockSignals(False)
            scaled = aligned
        if column.spin is not None:
            column.spin.blockSignals(True)
            column.spin.setValue(scaled / column.scale)
            column.spin.blockSignals(False)
        column._refresh_combo_display()
        self._schedule_emit()

    def _on_spin(self, column: _Column, value: float) -> None:
        column.slider.blockSignals(True)
        column.slider.setValue(round(float(value) * column.scale))
        column.slider.blockSignals(False)
        column._refresh_combo_display()
        self._schedule_emit()

    def _on_state(self, column: _Column) -> None:
        new_idx = column.combo.currentIndex()
        # 如果 currentIndex 无效（用户输入自定义文本），保持之前的状态
        if 0 <= new_idx < len(column.sig.states):
            column._state_idx = new_idx
            raw = column.sig.states[column._state_idx].raw
            column.slider.setEnabled(raw is None)
            if column._state_idx == 0 and raw is None:
                column.slider.setValue(round(column.sig.default * column.scale))
        column._refresh_combo_display()
        self._schedule_emit()

    def _schedule_emit(self) -> None:
        if not self._emit_timer.isActive():
            self._emit_timer.start(EMIT_DELAY_MS)

    def _emit_values(self) -> None:
        self.values_changed.emit(self.current_frames())

    def current_frames(self) -> list[CanFrame]:
        raws = {c.sig.name: c.raw_value() for c in self._columns
                if c.check.isChecked()}
        # 构建激活状态映射，用于应用 byte_overrides
        active_states = {}
        for c in self._columns:
            if c.check.isChecked():
                state = c.active_state()
                if state is not None:
                    active_states[c.sig.name] = state
        return signals.pack_frames(self._defs, raws, active_states)

    # ------------------------------------------------------------------ 运行

    def _on_start_clicked(self) -> None:
        if self._running:
            self.stop_requested.emit()
            self.set_running(False)
            return

        if not self._bus_enabled or self._external_locked:
            return
        frames = self.current_frames()
        if not frames:
            self.message.emit("没有启用的信号，请先勾选至少一路信号")
            return

        # 每个 CAN ID 的周期都来自映射配置；未配置的用 100ms 兜底
        cycles: dict[int, int] = {}
        for frame in frames:
            cycles[frame.frame_id] = self._cycles.get(frame.frame_id,
                                                      PERIOD_DEFAULT_MS)
        self.set_running(True)
        self.start_requested.emit(frames, cycles)

    def set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.setText("停止" if running else "开始")
        self.mapping_button.setEnabled(not running)

    def set_bus_enabled(self, enabled: bool) -> None:
        self._bus_enabled = enabled
        self.start_button.setEnabled(enabled and not self._external_locked)

    def set_external_lock(self, locked: bool) -> None:
        """外部锁定/解锁：数据模拟运行时禁用信号实验的开始和映射按钮。"""
        self._external_locked = locked
        self.start_button.setEnabled(self._bus_enabled and not locked)
        self.mapping_button.setEnabled(not locked)
        if locked:
            self.start_button.setText("运行中")
        elif not self._running:
            self.start_button.setText("开始")

    # ------------------------------------------------------------------ 映射

    def _on_mapping(self) -> None:
        dialog = MappingDialog(self._defs, self._cycles, self)
        if dialog.exec() != MappingDialog.Accepted:
            return
        self._defs = dialog.defs()
        self._cycles = dialog.cycles()
        try:
            signals.save_signals(self._defs, self._cycles)
        except signals.StorageError as exc:
            self.message.emit(str(exc))
        self._rebuild_columns()
        self._schedule_emit()
        self.message.emit("信号配置已保存到 signals.json")
        self.mapping_changed.emit([d.name for d in self._defs])

    # ------------------------------------------------------------------ CSV 数据驱动

    def apply_csv_values(self, values: dict[str, float]) -> None:
        """将 CSV 数据行的物理值写入对应信号的滑杆/数值框，阻塞信号避免递归。"""
        for col in self._columns:
            if col.sig.name not in values:
                continue
            val = values[col.sig.name]
            clamped = max(col.sig.minimum, min(col.sig.maximum, float(val)))
            col.slider.blockSignals(True)
            col.slider.setValue(round(clamped * col.scale))
            col.slider.blockSignals(False)
            if col.spin is not None:
                col.spin.blockSignals(True)
                col.spin.setValue(clamped)
                col.spin.blockSignals(False)
            col._refresh_combo_display()
        # 触发信号值更新发送
        self._schedule_emit()

    def clear_csv_highlight(self) -> None:
        """清除所有信号列的 CSV 高亮（已禁用）。"""
        pass

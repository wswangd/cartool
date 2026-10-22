"""信号实验面板：垂直滑杆设置物理值，按映射打包成报文周期发送。"""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea,
                               QSlider, QVBoxLayout, QWidget, QLineEdit, QFrame)

from .. import signals
from ..frame import CanFrame
from ..signals import SignalDef
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

        self.widget = QFrame()
        self.widget.setObjectName("signalCard")
        self.widget.setMinimumWidth(CARD_WIDTH)
        self.widget.setStyleSheet(
            "QFrame#signalCard { background-color: #FFFFFF; border: 1px solid #D8DDE3; border-radius: 8px; }"
            "QFrame#signalCard[active=\"false\"] { background-color: #F3F4F6; border-color: #E5E7EB; }")
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

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

        self.check = QCheckBox(sig.name)
        name_font = self.check.font()
        name_font.setBold(True)
        self.check.setFont(name_font)
        self.check.setChecked(sig.enabled)

        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet("color: #000000; font-size: 24px; font-weight: 700;")
        self.value_label.setMinimumWidth(72)
        header = QHBoxLayout()
        header.addWidget(self.check, 1)
        unit_label = QLabel(sig.unit)
        unit_label.setStyleSheet("color: #6B7280;")
        header.addWidget(unit_label)
        layout.addLayout(header)
        layout.addWidget(self.value_label)


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
        layout.addWidget(self.slider)
        bounds = QHBoxLayout()
        bounds.addWidget(QLabel(f"{sig.minimum:g}"))
        bounds.addStretch(1)
        bounds.addWidget(QLabel(f"{sig.maximum:g}"))
        layout.addLayout(bounds)
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
            f"color: {color}; font-size: 24px; font-weight: 700;")
        if self.combo is not None:
            raw = self.sig.states[self._state_idx].raw
            if raw is not None:
                value = self.sig.raw_to_phys(raw)
        text = f"{value:.{self.decimals}f}".rstrip("0").rstrip(".")
        self.value_label.setText(text if text != "-0" else "0")
        self.widget.setProperty("active", self.check.isChecked())
        self.check.setStyleSheet("" if self.check.isChecked() else "color: #6B7280;")
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
        self._running = False
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
        self.search_edit.setMaximumWidth(260)
        top.addWidget(self.search_edit)
        self.enabled_only = QCheckBox("仅显示启用")
        top.addWidget(self.enabled_only)
        top.addStretch(1)

        self.mapping_button = QPushButton("信号配置")
        self.mapping_button.setFixedWidth(90)
        top.addWidget(self.mapping_button)
        self.start_button = QPushButton("开始")
        self.start_button.setFixedWidth(90)
        top.addWidget(self.start_button)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
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
        container.setStyleSheet("QWidget#signalCardsContainer { background-color: #ECEFF1; }")
        grid = QGridLayout(container)
        self._card_grid = grid
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(CARD_SPACING)
        grid.setAlignment(Qt.AlignTop)
        
        self._columns = []
        for index, sig in enumerate(self._defs):
            column = _Column(sig)
            self._columns.append(column)
            column.widget.setParent(container)
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

    def eventFilter(self, watched, event) -> bool:
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
        for column in self._columns:
            matched = (not query or query in f"{column.sig.name} {column.sig.unit}".casefold())
            matched = matched and (not self.enabled_only.isChecked() or column.check.isChecked())
            column.widget.setVisible(matched)
            if matched:
                visible.append(column)
        width = max(1, self.scroll.viewport().width() - 4)
        card_width = max([CARD_WIDTH] + [column.widget.minimumSizeHint().width()
                                        for column in visible])
        count = max(1, min(len(visible),
                           (width + CARD_SPACING) // (card_width + CARD_SPACING)))
        for index, column in enumerate(visible):
            grid.addWidget(column.widget, index // count, index % count)
        for column_index in range(count):
            grid.setColumnStretch(column_index, 1)

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

    def set_external_lock(self, locked: bool) -> None:
        """外部锁定/解锁：数据模拟运行时禁用信号实验的开始和映射按钮。"""
        self.start_button.setEnabled(not locked)
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

"""信号实验面板：垂直滑杆设置物理值，按映射打包成报文周期发送。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSlider,
                               QVBoxLayout, QWidget)

from .. import signals
from ..frame import CanFrame
from ..signals import SignalDef
from .mapping_dialog import MappingDialog, make_decimal_spin

SLIDER_HEIGHT = 240
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
    """垂直滑杆：仅在获得焦点（点击选中）后滚轮才改值，防止悬停误触。"""

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ValueComboBox(QComboBox):
    """可编辑但只读的 combo，下拉关闭后恢复自定义显示（数值或状态名）。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self._hide_callback = None

    def hidePopup(self) -> None:
        if self._hide_callback:
            self._hide_callback()
        super().hidePopup()


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

        self.widget = QWidget()
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 滑杆范围对齐到 factor 网格：最小值向上取整、最大值向下取整，
        # 确保每个采样点都精确落在刻度上，无截断
        lo = max(round(sig.minimum * self.scale), -SLIDER_INT_LIMIT)
        hi = min(round(sig.maximum * self.scale), SLIDER_INT_LIMIT)
        if lo > 0:
            lo = ((lo + self.step - 1) // self.step) * self.step
        else:
            lo = (lo // self.step) * self.step
        hi = (hi // self.step) * self.step

        self.slider = NoWheelSlider(Qt.Vertical)
        self.slider.setRange(lo, hi)
        self.slider.setValue(max(lo, min(hi, round(sig.default * self.scale))))
        self.slider.setSingleStep(self.step)        # 单步 = 1 个分辨率
        self.slider.setPageStep(self.step * 10)     # 翻页 = 10 个分辨率
        self.slider.setFixedHeight(SLIDER_HEIGHT)
        self.slider.setToolTip(sig.name)
        # 刻度间隔 = 分辨率的整数倍，使刻度精确对应采样点
        tick_interval = _nice_tick_interval(hi - lo, self.step)
        self.slider.setTickPosition(QSlider.TicksBothSides)
        self.slider.setTickInterval(tick_interval)
        layout.addWidget(self.slider, 0, Qt.AlignHCenter)

        self.check = QCheckBox(sig.name)
        self.check.setChecked(sig.enabled)
        layout.addWidget(self.check)

        layout.addWidget(QLabel(f"单位: {sig.unit}"))


        self.spin = None
        self.combo: ValueComboBox | None = None
        self._state_idx = 0          # combo 真实选中索引
        if sig.editor == "state" and sig.states:
            self.combo = ValueComboBox()
            for state in sig.states:
                self.combo.addItem(state.name)
            self.combo._hide_callback = lambda c=self.combo: self._on_combo_hide(c)
            layout.addWidget(self.combo)
            self._refresh_combo_display()
        else:
            # 数值框精度与分辨率一致，单步 = factor（分辨率）
            self.spin = make_decimal_spin(sig.default, sig.minimum,
                                          sig.maximum)
            self.spin.setDecimals(self.decimals)
            self.spin.setSingleStep(sig.factor)
            self.spin.setStepType(QDoubleSpinBox.StepType.DefaultStepType)
            layout.addWidget(self.spin)

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
                    return

    def _refresh_combo_display(self) -> None:
        """正常状态时 combo 显示当前数值，其他状态显示状态名；下拉项始终保持原始名称。"""
        if self.combo is None:
            return
        if self._state_idx == 0:
            val = self.phys_value()
            self.combo.blockSignals(True)
            self.combo.setEditText(f"{val:.{self.decimals}f}")
            self.combo.blockSignals(False)
        else:
            name = self.sig.states[self._state_idx].name
            self.combo.blockSignals(True)
            self.combo.setEditText(name)
            self.combo.blockSignals(False)

    def _on_combo_hide(self, combo: QComboBox) -> None:
        """下拉关闭后：处理重新选择同一项的情况，并恢复自定义显示。"""
        new_idx = combo.currentIndex()
        if new_idx != self._state_idx:
            # 用户选了不同项，但 currentIndex 相同则 _on_state 不会触发
            self._state_idx = new_idx
            raw = self.sig.states[self._state_idx].raw
            self.slider.setEnabled(raw is None)
            if self._state_idx == 0 and raw is None:
                self.slider.setValue(round(self.sig.default * self.scale))
        elif self._state_idx == 0 and self.sig.states[0].raw is None:
            # 仍在"正常"，重置到默认值
            self.slider.setValue(round(self.sig.default * self.scale))
        self._refresh_combo_display()


class SignalPanel(QWidget):
    start_requested = Signal(object, object)   # list[CanFrame], dict[int,int] cycles
    stop_requested = Signal()
    values_changed = Signal(object)            # list[CanFrame]，节流后发出
    message = Signal(str)

    def __init__(self, defs: list[SignalDef], parent=None) -> None:
        super().__init__(parent)
        self._defs = defs
        self._cycles: dict[int, int] = signals.load_cycles()
        self._columns: list[_Column] = []
        self._running = False
        self._loading = False
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.timeout.connect(self._emit_values)
        self._build()
        self._rebuild_columns()
        self.refresh_experiments()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        top = QHBoxLayout()

        self.experiment = QComboBox()
        self.experiment.setMinimumWidth(120)
        top.addWidget(self.experiment)
        self.experiment_name = QLineEdit()
        self.experiment_name.setPlaceholderText("实验名称")
        top.addWidget(self.experiment_name, 1)
        self.add_experiment = QPushButton("添加实验")
        top.addWidget(self.add_experiment)
        self.mapping_button = QPushButton("映射配置")
        top.addWidget(self.mapping_button)
        self.start_button = QPushButton("开始")
        self.start_button.setFixedWidth(90)
        top.addWidget(self.start_button)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.scroll, 1)

        self.add_experiment.clicked.connect(self._on_add_experiment)
        self.experiment.currentTextChanged.connect(self._on_load_experiment)
        self.mapping_button.clicked.connect(self._on_mapping)
        self.start_button.clicked.connect(self._on_start_clicked)

    def _rebuild_columns(self) -> None:
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(4)
        self._columns = []
        for col_index, sig in enumerate(self._defs):
            column = _Column(sig)
            self._columns.append(column)
            grid.addWidget(column.widget, 0, col_index)
            column.slider.valueChanged.connect(lambda v, c=column: self._on_slider(c, v))
            column.check.toggled.connect(
                lambda v, idx=col_index: self._on_check_toggled(idx, v))
            if column.spin is not None:
                column.spin.valueChanged.connect(
                    lambda v, c=column: self._on_spin(c, float(v)))
            if column.combo is not None:
                column.combo.currentIndexChanged.connect(
                    lambda _=0, c=column: self._on_state(c))
        grid.setColumnStretch(len(self._defs), 1)
        self.scroll.setWidget(container)

    # ------------------------------------------------------------------ 联动

    def _on_check_toggled(self, idx: int, checked: bool) -> None:
        """启用状态变更时同步到 defs，使映射配置中也能看到最新状态。"""
        if idx < len(self._defs):
            self._defs[idx].enabled = checked
        self._schedule_emit()

    def _on_slider(self, column: _Column, scaled: int) -> None:
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
        column._state_idx = column.combo.currentIndex()
        raw = column.sig.states[column._state_idx].raw
        column.slider.setEnabled(raw is None)
        if column._state_idx == 0 and raw is None:
            column.slider.setValue(round(column.sig.default * column.scale))
        column._refresh_combo_display()
        self._schedule_emit()

    def _schedule_emit(self) -> None:
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
        self.start_requested.emit(frames, cycles)
        self.set_running(True)

    def set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.setText("停止" if running else "开始")
        self.mapping_button.setEnabled(not running)

    # ------------------------------------------------------------------ 实验

    def refresh_experiments(self, select: str = "") -> None:
        names = list(signals.load_experiments())
        self._loading = True
        self.experiment.clear()
        self.experiment.addItem("")
        self.experiment.addItems(names)
        if select:
            self.experiment.setCurrentText(select)
        self._loading = False

    def _snapshot(self) -> dict:
        return {
            "signals": {
                c.sig.name: {
                    "enabled": c.check.isChecked(),
                    "value": c.phys_value(),
                    "state": (c.sig.states[c._state_idx].name
                              if c.combo is not None else ""),
                } for c in self._columns
            },
        }

    def _on_add_experiment(self) -> None:
        name = self.experiment_name.text().strip()
        if not name:
            name = f"实验{self.experiment.count()}"
        try:
            signals.save_experiment(name, self._snapshot())
        except signals.StorageError as exc:
            self.message.emit(str(exc))
            return
        self.experiment_name.setText(name)
        self.refresh_experiments(select=name)
        self.message.emit(f"实验\u201c{name}\u201d已保存")

    def _on_load_experiment(self, name: str) -> None:
        if self._loading or not name:
            return
        snapshot = signals.load_experiments().get(name)
        if not isinstance(snapshot, dict):
            return
        values = snapshot.get("signals", {})
        for column in self._columns:
            data = values.get(column.sig.name)
            if not isinstance(data, dict):
                continue
            column.check.setChecked(bool(data.get("enabled", False)))
            column.set_state(str(data.get("state", "")))
            state_raw = column.sig.states[column._state_idx].raw \
                if column.combo is not None else None
            if state_raw is None:
                try:
                    column.set_phys(float(data.get("value", column.sig.default)))
                except (TypeError, ValueError):
                    pass
            column._refresh_combo_display()
        self._schedule_emit()
        self.message.emit(f"已载入实验\u201c{name}\u201d")

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
        self.message.emit("信号映射已保存到 signals.json")

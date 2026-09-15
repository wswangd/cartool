"""信号实验面板：垂直滑杆设置物理值，按映射打包成报文周期发送。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from .. import signals
from ..frame import CanFrame
from ..signals import SignalDef
from ..zlgcan_api import BAUD_OPTIONS
from .mapping_dialog import MappingDialog

SLIDER_HEIGHT = 240
EMIT_DELAY_MS = 120


class _Column:
    """一路信号的滑杆 + 启用 + 单位 + 编辑器控件集合。"""

    def __init__(self, sig: SignalDef) -> None:
        self.sig = sig
        self.widget = QWidget()
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(sig.minimum, sig.maximum)
        self.slider.setValue(sig.default)
        self.slider.setFixedHeight(SLIDER_HEIGHT)
        self.slider.setToolTip(sig.name)
        layout.addWidget(self.slider, 0, Qt.AlignHCenter)

        self.check = QCheckBox(sig.name)
        self.check.setChecked(sig.enabled)
        layout.addWidget(self.check)

        layout.addWidget(QLabel(f"单位: {sig.unit}"))

        self.spin: QSpinBox | None = None
        self.combo: QComboBox | None = None
        if sig.editor == "state" and sig.states:
            self.combo = QComboBox()
            for state in sig.states:
                self.combo.addItem(state.name)
            layout.addWidget(self.combo)
        else:
            self.spin = QSpinBox()
            self.spin.setRange(sig.minimum, sig.maximum)
            self.spin.setValue(sig.default)
            layout.addWidget(self.spin)

    def raw_value(self) -> int:
        if self.combo is not None:
            raw = self.sig.state_raw(self.combo.currentText())
            if raw is not None:
                return raw
        return self.sig.phys_to_raw(self.slider.value())

    def set_phys(self, value: int) -> None:
        self.slider.setValue(max(self.sig.minimum, min(self.sig.maximum, value)))

    def set_state(self, name: str) -> None:
        if self.combo is not None and name:
            index = self.combo.findText(name)
            if index >= 0:
                self.combo.setCurrentIndex(index)


class SignalPanel(QWidget):
    start_requested = Signal(object, object)   # list[CanFrame], dict[int,int] cycles
    stop_requested = Signal()
    values_changed = Signal(object)            # list[CanFrame]，节流后发出
    device_change_requested = Signal(int, str)  # 索引、波特率标签
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
        top.addWidget(QLabel("索引"))
        self.device_index = QComboBox()
        self.device_index.addItems([str(i) for i in range(16)])
        top.addWidget(self.device_index)
        top.addWidget(QLabel("波特率"))
        self.baud = QComboBox()
        self.baud.addItems(BAUD_OPTIONS)
        self.baud.setCurrentText("250kbps")
        top.addWidget(self.baud)
        top.addWidget(QLabel("默认周期(ms)"))
        self.period = QSpinBox()
        self.period.setRange(10, 10000)
        self.period.setValue(100)
        top.addWidget(self.period)

        top.addSpacing(12)
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

        self.device_index.currentIndexChanged.connect(self._on_device_change)
        self.baud.currentTextChanged.connect(self._on_device_change)
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
                column.spin.valueChanged.connect(lambda v, c=column: self._on_spin(c, v))
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

    def _on_slider(self, column: _Column, value: int) -> None:
        if column.spin is not None:
            column.spin.blockSignals(True)
            column.spin.setValue(value)
            column.spin.blockSignals(False)
        self._schedule_emit()

    def _on_spin(self, column: _Column, value: int) -> None:
        column.slider.blockSignals(True)
        column.slider.setValue(value)
        column.slider.blockSignals(False)
        self._schedule_emit()

    def _on_state(self, column: _Column) -> None:
        raw = column.sig.state_raw(column.combo.currentText())
        column.slider.setEnabled(raw is None)
        self._schedule_emit()

    def _schedule_emit(self) -> None:
        self._emit_timer.start(EMIT_DELAY_MS)

    def _emit_values(self) -> None:
        self.values_changed.emit(self.current_frames())

    def current_frames(self) -> list[CanFrame]:
        raws = {c.sig.name: c.raw_value() for c in self._columns
                if c.check.isChecked()}
        return signals.pack_frames(self._defs, raws)

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
        # 构建 CAN ID → 周期映射，未配置的用全局默认
        default_period = self.period.value()
        cycles: dict[int, int] = {}
        for frame in frames:
            if frame.frame_id not in cycles:
                cycles[frame.frame_id] = self._cycles.get(frame.frame_id, default_period)
        self.start_requested.emit(frames, cycles)
        self.set_running(True)

    def set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.setText("停止" if running else "开始")
        self.period.setEnabled(not running)
        self.mapping_button.setEnabled(not running)

    # ------------------------------------------------------------------ 设备

    def _on_device_change(self) -> None:
        if self._loading:
            return
        self.device_change_requested.emit(int(self.device_index.currentText()),
                                          self.baud.currentText())

    def sync_device(self, index: int, baud_label: str) -> None:
        self._loading = True
        self.device_index.setCurrentText(str(index))
        if baud_label in BAUD_OPTIONS:
            self.baud.setCurrentText(baud_label)
        self._loading = False

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
            "period_ms": self.period.value(),
            "signals": {
                c.sig.name: {
                    "enabled": c.check.isChecked(),
                    "value": c.slider.value(),
                    "state": c.combo.currentText() if c.combo is not None else "",
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
        self.message.emit(f"实验“{name}”已保存")

    def _on_load_experiment(self, name: str) -> None:
        if self._loading or not name:
            return
        snapshot = signals.load_experiments().get(name)
        if not isinstance(snapshot, dict):
            return
        try:
            self.period.setValue(int(snapshot.get("period_ms", self.period.value())))
        except (TypeError, ValueError):
            pass
        values = snapshot.get("signals", {})
        for column in self._columns:
            data = values.get(column.sig.name)
            if not isinstance(data, dict):
                continue
            column.check.setChecked(bool(data.get("enabled", False)))
            column.set_state(str(data.get("state", "")))
            state_raw = column.sig.state_raw(column.combo.currentText()) \
                if column.combo is not None else None
            if state_raw is None:
                try:
                    column.set_phys(int(data.get("value", column.sig.default)))
                except (TypeError, ValueError):
                    pass
        self._schedule_emit()
        self.message.emit(f"已载入实验“{name}”")

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

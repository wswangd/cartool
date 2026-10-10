"""信号实验面板：垂直滑杆设置物理值，按映射打包成报文周期发送。"""

from __future__ import annotations

import csv
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea,
                               QSlider, QVBoxLayout, QWidget)

from .. import signals
from ..frame import CanFrame
from ..signals import SignalDef
from .mapping_dialog import MappingDialog, make_decimal_spin

SLIDER_HEIGHT = 90
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
        self.slider.setTickPosition(QSlider.NoTicks)
        self.slider.setTickInterval(tick_interval)
        layout.addWidget(self.slider, 0, Qt.AlignHCenter)

        self.check = QCheckBox(sig.name)
        self.check.setChecked(sig.enabled)
        layout.addWidget(self.check, alignment=Qt.AlignHCenter)

        unit_label = QLabel(f"单位: {sig.unit}")
        unit_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(unit_label)


        self.spin = None
        self.combo: ValueComboBox | None = None
        self._state_idx = 0          # combo 真实选中索引
        if sig.editor == "state" and sig.states:
            self.combo = ValueComboBox()
            self.combo.setMinimumWidth(120)
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
            self.spin.setMinimumWidth(120)
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
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.timeout.connect(self._emit_values)
        self._csv_rows: list[dict[str, float]] = []
        self._csv_signal_names: list[str] = []
        # 每个 CAN ID 独立跟踪播放进度：周期、当前行、累计时间、循环次数
        self._playback_states: dict[int, dict] = {}
        self._playback_timer = QTimer(self)
        self._playback_timer.timeout.connect(self._on_playback_tick)
        self._build()
        self._rebuild_columns()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        top = QHBoxLayout()

        self.csv_label = QLabel("未导入CSV")
        top.addWidget(self.csv_label, 1)
        self.import_csv_button = QPushButton("导入CSV")
        top.addWidget(self.import_csv_button)
        self.send_mode = QComboBox()
        self.send_mode.addItem("循环发送")
        self.send_mode.addItem("单次发送")
        self.send_mode.setMinimumWidth(100)
        top.addWidget(self.send_mode)
        self.mapping_button = QPushButton("映射配置")
        top.addWidget(self.mapping_button)
        self.start_button = QPushButton("开始")
        self.start_button.setFixedWidth(90)
        top.addWidget(self.start_button)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.progress_label = QLabel("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.progress_label)

        self.import_csv_button.clicked.connect(self._on_import_csv)
        self.mapping_button.clicked.connect(self._on_mapping)
        self.start_button.clicked.connect(self._on_start_clicked)

    def _rebuild_columns(self) -> None:
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(0)
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
            self._stop_playback()
            self.stop_requested.emit()
            self.set_running(False)
            return

        frames = self.current_frames()
        if not frames:
            self.message.emit("没有启用的信号，请先勾选至少一路信号")
            return

        if self._csv_rows:
            self._init_playback_states()
            self._apply_csv_row(0)
            self._start_playback()

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
        self.import_csv_button.setEnabled(not running)
        if not running:
            self._stop_playback()

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

    # ------------------------------------------------------------------ CSV

    def _on_import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入数据", "", "数据文件 (*.csv *.xlsx *.xls)")
        if not path:
            return
        sig_names = {sig.name for sig in self._defs}
        headers: list[str] = []
        rows: list[dict[str, float]] = []
        parse_error: str | None = None

        if path.lower().endswith((".xlsx", ".xls")):
            try:
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True, data_only=True)
                ws = wb.active
                row_iter = ws.iter_rows(values_only=True)
                raw_headers = next(row_iter, None)
                if not raw_headers:
                    parse_error = "空文件"
                else:
                    headers = [str(h).strip() if h else "" for h in raw_headers]
                    for h in headers:
                        if h and h not in sig_names:
                            parse_error = f"信号\u201c{h}\u201d在映射配置中不存在"
                            break
                    if parse_error is None:
                        for line_no, row in enumerate(row_iter, start=2):
                            if not any(cell is not None and str(cell).strip() for cell in row):
                                continue
                            values: dict[str, float] = {}
                            for i, cell in enumerate(row):
                                if i >= len(headers):
                                    break
                                if cell is None or str(cell).strip() == "":
                                    continue
                                try:
                                    values[headers[i]] = float(str(cell).strip())
                                except ValueError:
                                    parse_error = f"第{line_no}行数据格式错误：\u201c{cell}\u201d"
                                    break
                            if parse_error is not None:
                                break
                            rows.append(values)
                wb.close()
            except Exception as exc:
                QMessageBox.critical(self, "导入错误", f"读取Excel文件失败：{exc}")
                return
        else:
            for enc in ("utf-8-sig", "gbk", "latin-1"):
                headers = []
                rows = []
                parse_error = None
                try:
                    with open(path, encoding=enc) as f:
                        reader = csv.reader(f)
                        raw_headers = next(reader, None)
                        if not raw_headers:
                            parse_error = "空文件"
                            break
                        headers = [h.strip() for h in raw_headers]
                        for h in headers:
                            if h not in sig_names:
                                parse_error = (
                                    f"信号\u201c{h}\u201d在映射配置中不存在")
                                break
                        if parse_error is not None:
                            break
                        for line_no, row in enumerate(reader, start=2):
                            if not any(cell.strip() for cell in row):
                                continue
                            values: dict[str, float] = {}
                            for i, cell in enumerate(row):
                                if i >= len(headers):
                                    break
                                try:
                                    values[headers[i]] = float(cell.strip())
                                except ValueError:
                                    parse_error = (
                                        f"第{line_no}行数据格式错误："
                                        f"\u201c{cell.strip()}\u201d")
                                    break
                            if parse_error is not None:
                                break
                            rows.append(values)
                    break
                except UnicodeDecodeError:
                    continue
                except OSError as exc:
                    QMessageBox.critical(self, "导入错误", str(exc))
                    return
            else:
                QMessageBox.critical(
                    self, "导入错误", "无法识别文件编码，请使用 UTF-8 或 GBK")
                return

        if parse_error is not None:
            QMessageBox.warning(self, "导入错误", parse_error)
            return
        if not rows:
            QMessageBox.warning(self, "导入错误", "文件中没有数据行")
            return
        self._csv_signal_names = headers
        self._csv_rows = rows
        self._playback_states = {}
        self._update_progress_label()
        self.csv_label.setText(os.path.basename(path))
        self.message.emit(
            f"已导入：{len(rows)} 行数据，{len(headers)} 路信号")

    def _apply_csv_row(self, index: int) -> None:
        """将 CSV 某一行的物理值写入对应信号的滑杆/数值框，阻塞信号避免递归。"""
        row = self._csv_rows[index]
        for col in self._columns:
            if col.sig.name not in row:
                continue
            val = row[col.sig.name]
            clamped = max(col.sig.minimum, min(col.sig.maximum, float(val)))
            col.slider.blockSignals(True)
            col.slider.setValue(round(clamped * col.scale))
            col.slider.blockSignals(False)
            if col.spin is not None:
                col.spin.blockSignals(True)
                col.spin.setValue(clamped)
                col.spin.blockSignals(False)
            col._refresh_combo_display()

    def _init_playback_states(self) -> None:
        """为每个启用的 CAN ID 初始化独立的播放进度。"""
        csv_names = set(self._csv_signal_names)
        self._playback_states = {}
        for col in self._columns:
            if col.sig.name in csv_names and col.check.isChecked():
                cid = col.sig.can_id
                if cid in self._playback_states:
                    continue
                self._playback_states[cid] = {
                    "period": self._cycles.get(cid, PERIOD_DEFAULT_MS),
                    "row_index": 0,
                    "accum": 0,
                    "total_rows": len(self._csv_rows),
                    "cycle_count": 0,
                    "done": False,
                }

    def _start_playback(self) -> None:
        if not self._playback_states:
            return
        min_period = min(s["period"] for s in self._playback_states.values())
        self._playback_timer.setInterval(max(min_period, 10))
        self._playback_timer.start()

    def _stop_playback(self) -> None:
        self._playback_timer.stop()

    def _on_playback_tick(self) -> None:
        if not self._playback_states:
            self._stop_playback()
            return
        tick = self._playback_timer.interval()
        dirty_cids: list[int] = []

        for cid, state in self._playback_states.items():
            state["accum"] += tick
            if state["accum"] >= state["period"]:
                state["accum"] -= state["period"]
                if state["done"]:
                    dirty_cids.append(cid)
                    continue
                idx = state["row_index"]
                if idx < state["total_rows"]:
                    self._apply_csv_row(idx)
                    dirty_cids.append(cid)
                    state["row_index"] = idx + 1
                    self._update_progress_label()

        if dirty_cids:
            frames = self.current_frames()
            wanted = {f.frame_id for f in frames if f.frame_id in dirty_cids}
            to_emit = [f for f in frames if f.frame_id in wanted]
            if to_emit:
                self.values_changed.emit(to_emit)

        for state in self._playback_states.values():
            if not state["done"] and state["row_index"] >= state["total_rows"]:
                state["done"] = True

        if all(s["done"] for s in self._playback_states.values()):
            if self.send_mode.currentIndex() == 0:
                for state in self._playback_states.values():
                    state["row_index"] = 0
                    state["accum"] = 0
                    state["cycle_count"] += 1
                    state["done"] = False
                self._update_progress_label()
            else:
                self._stop_playback()
                self.stop_requested.emit()
                self.set_running(False)
                self.message.emit("CSV发送完成")

    def _update_progress_label(self) -> None:
        if not self._playback_states:
            total = len(self._csv_rows)
            if total == 0:
                self.progress_label.setText("")
            else:
                self.progress_label.setText(f"共 {total} 行，未开始")
            return
        # 以"完成时间最晚"的 CAN ID 为整体进度基准（period × total_rows 最大）
        leader = max(self._playback_states.values(),
                     key=lambda s: s["period"] * s["total_rows"])
        total = leader["total_rows"]
        current = min(leader["row_index"], total)
        pct = current / total * 100 if total else 0
        cycle = leader["cycle_count"] + 1
        self.progress_label.setText(
            f"进度: {current}/{total} ({pct:.1f}%)  循环: 第{cycle}次")

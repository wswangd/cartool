"""主窗口：组装各面板并管理 CAN 通道与工作线程的生命周期。"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (QGroupBox, QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import signals, storage
from ..bus import BACKEND_SIM, BusBackend, BusConfig, create_bus
from .widgets import HoverSplitter
from ..frame import CanFrame
from ..storage import StorageError
from ..worker import BusWorker
from ..zlgcan_api import ZcanApi, ZlgError
from .device_panel import DevicePanel
from .list_panel import ListPanel
from .measurement_panel import MeasurementPanel
from .send_panel import SendPanel
from .signal_panel import SignalPanel
from .simulate_panel import SimulatePanel
from .trace_panel import TracePanel
from .tab_workspace import TabWorkspace

APP_TITLE = "CANTool — 自定义报文发送工具"
SINGLE_SEND_UID = 0
SIGNAL_UID_BASE = 1_000_000


class MainWindow(QMainWindow):
    @property
    def tabs(self):
        return self.tab_workspace.primary

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        available = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1180, available.width() - 24),
                    min(820, available.height() - 24))

        self._api: ZcanApi | None = None
        self._bus: BusBackend | None = None
        self._worker: BusWorker | None = None
        self._reported_errors: set[str] = set()
        self._tx_total = 0
        self._rx_total = 0
        self._err_total = 0
        self._signal_uids: list[int] = []
        self._signal_cycles: dict[int, int] = {}
        self._signal_warning = ""

        self._build()
        self._wire()
        self._load_settings()
        self._set_connected(False)
        if self._signal_warning:
            self._show_message(self._signal_warning, 10000)

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        self.device_panel = DevicePanel()
        self.send_panel = SendPanel()

        self.list_panel = ListPanel()
        list_group = QGroupBox("列表发送")
        list_group.setMinimumHeight(32)
        list_layout = QVBoxLayout(list_group)
        list_layout.addWidget(self.list_panel)

        try:
            signal_defs = signals.load_signals()
        except StorageError as exc:
            signal_defs = signals.default_signals()
            self._signal_warning = f"signals.json 读取失败，已使用内置默认映射：{exc}"
        self.signal_panel = SignalPanel(signal_defs)
        self.simulate_panel = SimulatePanel()

        self._send_splitter = HoverSplitter(Qt.Vertical)
        self._send_splitter.addWidget(self.send_panel)
        self._send_splitter.addWidget(list_group)
        self.send_panel.setMinimumHeight(32)
        list_group.setMinimumHeight(32)
        self._send_splitter.setStretchFactor(0, 0)
        self._send_splitter.setStretchFactor(1, 1)
        self._send_splitter.setCollapsible(0, True)
        self._send_splitter.setCollapsible(1, True)

        self.tab_workspace = TabWorkspace()
        self.tabs.addTab(self._send_splitter, "报文发送")
        self.tabs.addTab(self.signal_panel, "信号发送")
        self.tabs.addTab(self.simulate_panel, "数据模拟")
        self.measurement_panel = MeasurementPanel()
        self.tabs.addTab(self.measurement_panel, "信号测量")

        self.trace_panel = TracePanel()
        self.tabs.addTab(self.trace_panel, "报文显示")

        self._main_splitter = HoverSplitter(Qt.Vertical)
        self._main_splitter.addWidget(self.device_panel)
        self._main_splitter.addWidget(self.tab_workspace)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 3)
        self.tabs.setMinimumHeight(32)
        self._main_splitter.setHandleWidth(7)
        self._send_splitter.setHandleWidth(7)
        self._main_splitter.setCollapsible(0, True)
        self._main_splitter.setCollapsible(1, True)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setSpacing(6)
        layout.addWidget(self._main_splitter, 1)
        self.setCentralWidget(central)

        self.tx_label = self._status_label("发送: 0")
        self.rx_label = self._status_label("接收: 0")
        self.err_label = self._status_label("错误帧: 0")
        self.bus_label = self._status_label("通道状态: —")
        for label in (self.tx_label, self.rx_label, self.err_label, self.bus_label):
            self.statusBar().addPermanentWidget(label)

    def _status_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setMinimumWidth(96)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    def _wire(self) -> None:
        self.device_panel.connect_requested.connect(self._on_connect)
        self.device_panel.disconnect_requested.connect(self._on_disconnect)
        self.device_panel.reset_requested.connect(self._on_reset)
        self.device_panel.clear_buffer_requested.connect(self._on_clear_buffer)

        self.send_panel.add_to_list_requested.connect(self._on_add_to_list)
        self.send_panel.periodic_requested.connect(self._on_single_periodic)
        self.send_panel.periodic_stopped.connect(self._on_single_periodic_off)
        self.send_panel.input_error.connect(self._show_message)

        self.list_panel.send_frames.connect(self._on_send_frames)
        self.list_panel.start_tasks.connect(self._on_start_tasks)
        self.list_panel.start_sequence.connect(self._on_start_sequence)
        self.list_panel.stop_tasks.connect(self._on_stop_tasks)
        self.list_panel.selection_changed.connect(self._on_list_selection_changed)
        self.list_panel.message.connect(self._show_message)
        self.list_panel.model.parse_error.connect(self._show_message)

        self.trace_panel.message.connect(self._show_message)
        self.measurement_panel.message.connect(self._show_message)

        self.signal_panel.start_requested.connect(self._on_signal_start)
        self.signal_panel.stop_requested.connect(self._on_signal_stop)
        self.signal_panel.values_changed.connect(self._on_signal_values)
        self.signal_panel.message.connect(self._show_message)

        self.simulate_panel.playback_row.connect(self._on_simulate_row)
        self.simulate_panel.playback_started.connect(self._on_simulate_started)
        self.simulate_panel.playback_stopped.connect(self._on_simulate_stopped)
        self.simulate_panel.playback_finished.connect(self._on_simulate_finished)
        self.simulate_panel.message.connect(self._show_message)

        # 将映射配置的信号名同步给数据模拟面板，用于 CSV 导入校验
        self.signal_panel.mapping_changed.connect(
            self.simulate_panel.set_signal_names)
        self.simulate_panel.set_signal_names(
            [d.name for d in self.signal_panel._defs])

        clear_action = QAction("清空报文显示", self)
        clear_action.setShortcut("Ctrl+L")
        clear_action.triggered.connect(self.trace_panel.clear)
        self.addAction(clear_action)

    # -------------------------------------------------------------- 连接管理

    def _on_connect(self) -> None:
        cfg = self.device_panel.config()
        if self._api is None and cfg.backend != BACKEND_SIM:
            try:
                self._api = ZcanApi()
            except (ZlgError, OSError) as exc:
                QMessageBox.critical(self, "加载驱动失败", str(exc))
                return
        try:
            bus = create_bus(cfg, self._api)
            bus.open()
        except ZlgError as exc:
            QMessageBox.critical(self, "打开设备失败", str(exc))
            return

        self._bus = bus
        self._reported_errors.clear()
        self._tx_total = self._rx_total = self._err_total = 0
        self._worker = BusWorker(bus)
        self._worker.records.connect(self.trace_panel.add_records)
        self._worker.records.connect(self.measurement_panel.add_records)
        self._worker.progress.connect(self._on_progress)
        self._worker.task_done.connect(self._on_task_done)
        self._worker.sequence_done.connect(self.list_panel.sequence_finished)
        self._worker.sequence_progress.connect(self.list_panel.show_sequence_progress)
        self._worker.failure.connect(self._on_failure)
        self._worker.status.connect(self._on_bus_status)
        self._worker.counters.connect(self._on_counters)
        self._worker.start()

        info = getattr(bus, "info", "")
        description = cfg.describe() + (f"   {info}" if info else "")
        self.device_panel.set_connected(True, description)
        self._set_connected(True)
        self._show_message(f"已连接：{description}")

    def _on_disconnect(self) -> None:
        self._teardown()
        self.device_panel.set_connected(False, "未连接")
        self._set_connected(False)
        self._show_message("已断开连接")

    def _teardown(self) -> None:
        self.simulate_panel.stop()
        if self._worker is not None:
            self._worker.stop_all_periodic()
            self._worker.shutdown()
            self._worker.deleteLater()
            self._worker = None
        self._signal_uids = []
        self._signal_cycles = {}
        self.signal_panel.set_running(False)
        self.signal_panel.set_external_lock(False)
        self.simulate_panel.set_enabled(True)
        if self._bus is not None:
            try:
                self._bus.close()
            except ZlgError:
                pass
            self._bus = None
        self.send_panel.set_running(False)

    def _set_connected(self, connected: bool) -> None:
        self.measurement_panel.set_connected(connected)
        self.signal_panel.set_bus_enabled(connected)
        self.simulate_panel.set_bus_enabled(connected)
        self.send_panel.set_bus_enabled(connected)
        self.list_panel.set_bus_enabled(connected)
        if not connected:
            self.bus_label.setText("通道状态: —")

    def _on_reset(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.reset()
        except ZlgError as exc:
            self._show_message(f"复位失败：{exc}")
            return
        self._show_message("通道已复位")

    def _on_clear_buffer(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.clear_buffer()
        except ZlgError as exc:
            self._show_message(f"清空缓冲失败：{exc}")
            return
        self._show_message("已清空设备接收缓冲")

    # ------------------------------------------------------------------ 发送

    def _on_send_frame(self, frame: CanFrame) -> None:
        self._on_send_frames([frame])

    def _on_send_frames(self, frames: list[CanFrame]) -> None:
        if self._worker is None:
            self._show_message("请先打开设备")
            return
        self._worker.send_now(frames)

    def _on_add_to_list(self, frame: CanFrame) -> None:
        self.list_panel.add_frame(frame, period_ms=self.send_panel.period_ms.value())
        self._show_message("已加入列表")

    def _on_single_periodic(self, frame: CanFrame, period_ms: int, repeat: int) -> None:
        if self._worker is None:
            self.send_panel.set_running(False)
            self._show_message("请先打开设备")
            return
        self._worker.start_periodic(SINGLE_SEND_UID, frame, period_ms, repeat)

    def _on_single_periodic_off(self) -> None:
        if self._worker is not None:
            self._worker.stop_periodic(SINGLE_SEND_UID)

    def _on_start_tasks(self, tasks: list[tuple]) -> None:
        if self._worker is None:
            self._show_message("请先打开设备")
            return
        for uid, frame, period_ms, repeat in tasks:
            if period_ms > 0:
                self._worker.start_periodic(uid, frame, period_ms, repeat)

    def _on_stop_tasks(self, uids: list[int]) -> None:
        if self._worker is None:
            return
        self._worker.stop_sequence()
        for uid in uids:
            self._worker.stop_periodic(uid)

    def _on_list_selection_changed(self, tasks, sequential: bool) -> None:
        if self._worker is None:
            return
        if sequential:
            self._worker.update_sequence_entries(tasks)
            return
        for uid, frame, period, repeat, enabled in tasks:
            if enabled:
                self._worker.start_periodic(uid, frame, max(1, period), repeat)
            else:
                self._worker.stop_periodic(uid)

    def _on_start_sequence(self, tasks: list[tuple], repeats: int,
                           interval_ms: int, cycle_interval_ms: int) -> None:
        if self._worker is not None:
            self._worker.start_sequence(tasks, repeats, interval_ms, cycle_interval_ms)

    # ---------------------------------------------------------------- 信号实验

    def _on_signal_start(self, frames: list[CanFrame],
                         cycles: dict[int, int]) -> None:
        if self._worker is None:
            self.signal_panel.set_running(False)
            QMessageBox.warning(self, "提示", "请先打开设备")
            return
        self._signal_cycles = cycles
        self._signal_uids = [SIGNAL_UID_BASE + frame.frame_id for frame in frames]
        for uid, frame in zip(self._signal_uids, frames):
            period = cycles.get(frame.frame_id, 100)
            self._worker.start_periodic(uid, frame, period, 0)
        id_summary = ", ".join(f"{fid:08X}={cycles.get(fid, 100)}ms"
                               for fid in sorted(cycles))
        self._show_message(f"信号发送已开始：{len(frames)} 路报文（{id_summary}）")
        # 禁用数据模拟面板
        self.simulate_panel.set_enabled(False)

    def _on_signal_stop(self) -> None:
        if self._worker is not None:
            for uid in self._signal_uids:
                self._worker.stop_periodic(uid)
        self._signal_uids = []
        self._signal_cycles = {}
        self._show_message("信号发送已停止")
        # 启用数据模拟面板
        self.simulate_panel.set_enabled(True)

    def _on_signal_values(self, frames: list[CanFrame]) -> None:
        if self._worker is None or not (self.signal_panel._running or self.simulate_panel.is_playing()):
            return
        wanted = {SIGNAL_UID_BASE + frame.frame_id: frame for frame in frames}
        for uid in list(self._signal_uids):
            if uid not in wanted:
                self._worker.stop_periodic(uid)
        if not wanted:
            self._signal_uids = []
            if self.simulate_panel.is_playing():
                self._signal_cycles = {}
            else:
                self._on_signal_stop()
                self.signal_panel.set_running(False)
            return
        for uid, frame in wanted.items():
            can_id = frame.frame_id
            period = (self.simulate_panel.row_period.value() if self.simulate_panel.is_playing()
                      else self._signal_cycles.get(can_id, self.signal_panel._cycles.get(can_id, 100)))
            if uid in self._signal_uids:
                self._worker.update_frame(uid, frame)
            else:
                self._worker.start_periodic(uid, frame, period, 0)
                self._signal_cycles.setdefault(can_id, period)
        self._signal_uids = list(wanted)

    # -------------------------------------------------------------- 数据模拟

    def _on_simulate_row(self, values: dict) -> None:
        """数据模拟播放一行数据时，将值写入信号实验面板的滑杆。"""
        self.signal_panel.apply_csv_values(values)
        self._on_signal_values(self.signal_panel.current_frames())

    def _on_simulate_started(self) -> None:
        """数据模拟开始播放时，自动启动信号实验发送。"""
        if self._worker is None:
            self.simulate_panel.stop()
            QMessageBox.warning(self, "提示", "请先打开设备")
            return
        # 获取当前信号面板的报文并启动周期发送
        self.signal_panel.apply_csv_values(self.simulate_panel.current_values())
        frames = self.signal_panel.current_frames()
        # 停止之前的信号实验发送
        if self._signal_uids:
            for uid in self._signal_uids:
                self._worker.stop_periodic(uid)
        self._signal_uids = [SIGNAL_UID_BASE + frame.frame_id for frame in frames]
        # 所有报文统一使用行周期发送，不使用映射配置中的周期
        row_period = self.simulate_panel.row_period.value()
        cycles: dict[int, int] = {}
        for frame in frames:
            cycles[frame.frame_id] = row_period
        self._signal_cycles = cycles
        for uid, frame in zip(self._signal_uids, frames):
            self._worker.start_periodic(uid, frame, row_period, 0)
        self._show_message(f"数据模拟已开始：{len(frames)} 路报文，行周期={row_period}ms")
        # 锁定信号实验面板
        self.signal_panel.set_external_lock(True)

    def _on_simulate_stopped(self) -> None:
        """用户手动停止数据模拟时，停止信号实验发送并解锁面板。"""
        if self._worker is not None:
            for uid in self._signal_uids:
                self._worker.stop_periodic(uid)
        self._signal_uids = []
        self._signal_cycles = {}
        self._show_message("数据模拟已停止")
        # 解锁信号实验面板，清除 CSV 高亮
        self.signal_panel.set_external_lock(False)
        self.signal_panel.clear_csv_highlight()

    def _on_simulate_finished(self) -> None:
        """数据模拟播放完成时，停止信号实验发送。"""
        if self._worker is not None:
            for uid in self._signal_uids:
                self._worker.stop_periodic(uid)
        self._signal_uids = []
        self._signal_cycles = {}
        self._show_message("数据模拟已完成")
        # 解锁信号实验面板，清除 CSV 高亮
        self.signal_panel.set_external_lock(False)
        self.signal_panel.clear_csv_highlight()

    # -------------------------------------------------------------- 工作线程

    def _on_progress(self, updates: dict) -> None:
        for uid, sent in updates.items():
            self.list_panel.model.set_sent(uid, sent)

    def _on_task_done(self, uid: int) -> None:
        if uid == SINGLE_SEND_UID:
            self.send_panel.set_running(False)
            self._show_message("单帧定时发送已按设定次数完成")
        else:
            self.list_panel.mark_task_finished(uid)

    def _on_counters(self, tx: int, rx: int, err: int) -> None:
        self._tx_total, self._rx_total, self._err_total = tx, rx, err
        self.tx_label.setText(f"发送: {tx}")
        self.rx_label.setText(f"接收: {rx}")
        self.err_label.setText(f"错误帧: {err}")

    def _on_bus_status(self, status: dict | None) -> None:
        if status is None:
            return
        if not status.get("available", True):
            self.bus_label.setText("通道状态: 设备不支持读取")
            self.bus_label.setStyleSheet("color:#9E9E9E;")
            return
        if status.get("bus_off"):
            self.bus_label.setText("通道状态: BUS OFF")
            self.bus_label.setStyleSheet("color:#C62828;")
            return
        self.bus_label.setStyleSheet("")
        self.bus_label.setText(
            f"通道状态: TEC {status.get('tx_err_counter', 0)} / "
            f"REC {status.get('rx_err_counter', 0)}")

    def _on_failure(self, message: str) -> None:
        self._show_message(message)
        if message in self._reported_errors:
            return
        self._reported_errors.add(message)
        QMessageBox.warning(self, "CAN 操作失败", message)

    def _show_message(self, message: str, timeout: int = 6000) -> None:
        self.statusBar().showMessage(message, timeout)

    # ------------------------------------------------------------------ 配置

    def _load_settings(self) -> None:
        settings = storage.load_settings()
        bus = settings.get("bus")
        if isinstance(bus, dict):
            try:
                self.device_panel.set_config(BusConfig.from_dict(bus))
            except (TypeError, ValueError, KeyError):
                pass
        trace = settings.get("trace", {})
        if isinstance(trace, dict):
            try:
                self.trace_panel.set_capacity(1000)
                self.trace_panel.set_autoscroll(bool(trace.get("autoscroll", True)))
                self.trace_panel.set_direction_filter(str(trace.get("direction", "全部")))
            except (TypeError, ValueError):
                pass
        self.signal_panel.restore_card_layout(settings.get("signal_card_layout"))
        send_splitter_state = settings.get("send_splitter")
        if isinstance(send_splitter_state, str):
            self._send_splitter.restoreState(QByteArray.fromBase64(send_splitter_state.encode("ascii")))
        main_splitter_state = settings.get("main_splitter_tabs")
        if isinstance(main_splitter_state, str):
            self._main_splitter.restoreState(QByteArray.fromBase64(main_splitter_state.encode("ascii")))
        geometry = settings.get("geometry")
        if isinstance(geometry, str):
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        send = settings.get("send", {})
        list_header = settings.get("list_header")
        if isinstance(list_header, str):
            try:
                self.list_panel.table.horizontalHeader().restoreState(
                    QByteArray.fromBase64(list_header.encode("ascii")))
            except (UnicodeEncodeError, ValueError):
                pass
            self.list_panel._refresh_send_state()
        if isinstance(send, dict) and "period_ms" in send:
            try:
                self.send_panel.period_ms.setValue(int(send["period_ms"]))
            except (TypeError, ValueError):
                pass

    def _save_settings(self) -> None:
        storage.save_settings({
            "bus": self.device_panel.config().to_dict(),
            "trace": {
                "capacity": self.trace_panel.capacity.value(),
                "autoscroll": self.trace_panel.autoscroll.isChecked(),
                "direction": self.trace_panel.direction.currentText(),
            },
            "send": {"period_ms": self.send_panel.period_ms.value()},
            "signal_card_layout": self.signal_panel.card_layout_settings(),
            "list_header": bytes(self.list_panel.table.horizontalHeader().saveState().toBase64()).decode("ascii"),
            "send_splitter": bytes(self._send_splitter.saveState().toBase64()).decode("ascii"),
            "main_splitter_tabs": bytes(self._main_splitter.saveState().toBase64()).decode("ascii"),
            "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
        })

    def closeEvent(self, event: QCloseEvent) -> None:
        running = len(self.list_panel.running_uids())
        if self.send_panel.is_running():
            running += 1
        running += len(self._signal_uids)
        if running:
            answer = QMessageBox.question(
                self, "确认退出", f"还有 {running} 路定时发送正在运行，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self._teardown()
        self._save_settings()
        event.accept()

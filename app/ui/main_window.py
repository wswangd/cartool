"""主窗口：组装各面板并管理 CAN 通道与工作线程的生命周期。"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (QGroupBox, QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import signals, storage
from ..bus import BACKEND_SIM, BusBackend, BusConfig, create_bus
from ..frame import CanFrame
from ..storage import StorageError
from ..worker import BusWorker
from ..zlgcan_api import BAUD_OPTIONS, ZcanApi, ZlgError
from .device_panel import DevicePanel
from .list_panel import ListPanel
from .send_panel import SendPanel
from .signal_panel import SignalPanel
from .trace_panel import TracePanel

APP_TITLE = "CANTool — 自定义报文发送工具"
SINGLE_SEND_UID = 0
SIGNAL_UID_BASE = 1_000_000


class MainWindow(QMainWindow):
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
        list_layout = QVBoxLayout(list_group)
        list_layout.addWidget(self.list_panel)

        try:
            signal_defs = signals.load_signals()
        except StorageError as exc:
            signal_defs = signals.default_signals()
            self._signal_warning = f"signals.json 读取失败，已使用内置默认映射：{exc}"
        self.signal_panel = SignalPanel(signal_defs)

        send_tab = QWidget()
        send_layout = QVBoxLayout(send_tab)
        send_layout.setContentsMargins(0, 0, 0, 0)
        send_layout.addWidget(self.send_panel)
        send_layout.addWidget(list_group, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(send_tab, "报文发送")
        self.tabs.addTab(self.signal_panel, "信号实验")

        self.trace_panel = TracePanel()
        trace_group = QGroupBox("报文显示")
        trace_layout = QVBoxLayout(trace_group)
        trace_layout.addWidget(self.trace_panel)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setSpacing(6)
        layout.addWidget(self.device_panel)
        layout.addWidget(self.tabs, 3)
        layout.addWidget(trace_group, 2)
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

        self.send_panel.send_requested.connect(self._on_send_frame)
        self.send_panel.batch_requested.connect(self._on_batch)
        self.send_panel.add_to_list_requested.connect(self._on_add_to_list)
        self.send_panel.periodic_requested.connect(self._on_single_periodic)
        self.send_panel.periodic_stopped.connect(self._on_single_periodic_off)
        self.send_panel.input_error.connect(self._show_message)

        self.list_panel.send_frames.connect(self._on_send_frames)
        self.list_panel.start_tasks.connect(self._on_start_tasks)
        self.list_panel.stop_tasks.connect(self._on_stop_tasks)
        self.list_panel.message.connect(self._show_message)
        self.list_panel.model.parse_error.connect(self._show_message)

        self.trace_panel.message.connect(self._show_message)

        self.signal_panel.start_requested.connect(self._on_signal_start)
        self.signal_panel.stop_requested.connect(self._on_signal_stop)
        self.signal_panel.values_changed.connect(self._on_signal_values)
        self.signal_panel.device_change_requested.connect(self._on_signal_device)
        self.signal_panel.message.connect(self._show_message)

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
        self._worker.progress.connect(self._on_progress)
        self._worker.task_done.connect(self._on_task_done)
        self._worker.failure.connect(self._on_failure)
        self._worker.status.connect(self._on_bus_status)
        self._worker.counters.connect(self._on_counters)
        self._worker.start()

        info = getattr(bus, "info", "")
        description = cfg.describe() + (f"   {info}" if info else "")
        self.device_panel.set_connected(True, description)
        self.signal_panel.sync_device(cfg.device_index, cfg.baud)
        self._set_connected(True)
        self._show_message(f"已连接：{description}")

    def _on_disconnect(self) -> None:
        self._teardown()
        self.device_panel.set_connected(False, "未连接")
        self._set_connected(False)
        self._show_message("已断开连接")

    def _teardown(self) -> None:
        if self._worker is not None:
            self._worker.stop_all_periodic()
            self._worker.shutdown()
            self._worker.deleteLater()
            self._worker = None
        if self._signal_uids:
            self._signal_uids = []
            self._signal_cycles = {}
            self.signal_panel.set_running(False)
        if self._bus is not None:
            try:
                self._bus.close()
            except ZlgError:
                pass
            self._bus = None
        if self.send_panel.periodic_check.isChecked():
            self.send_panel.periodic_check.setChecked(False)

    def _set_connected(self, connected: bool) -> None:
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

    def _on_batch(self, frame: CanFrame, count: int) -> None:
        self._on_send_frames([frame.copy() for _ in range(count)])

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
            self.send_panel.periodic_check.setChecked(False)
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
        for uid in uids:
            self._worker.stop_periodic(uid)

    # ---------------------------------------------------------------- 信号实验

    def _on_signal_start(self, frames: list[CanFrame],
                         cycles: dict[int, int]) -> None:
        if self._worker is None:
            self.signal_panel.set_running(False)
            self._show_message("请先打开设备")
            return
        self._signal_cycles = cycles
        self._signal_uids = [SIGNAL_UID_BASE + frame.frame_id for frame in frames]
        for uid, frame in zip(self._signal_uids, frames):
            period = cycles.get(frame.frame_id, 100)
            self._worker.start_periodic(uid, frame, period, 0)
        id_summary = ", ".join(f"{fid:X}={cycles.get(fid, 100)}ms"
                               for fid in sorted(cycles))
        self._show_message(f"信号实验已开始：{len(frames)} 路报文（{id_summary}）")

    def _on_signal_stop(self) -> None:
        if self._worker is not None:
            for uid in self._signal_uids:
                self._worker.stop_periodic(uid)
        self._signal_uids = []
        self._signal_cycles = {}
        self._show_message("信号实验已停止")

    def _on_signal_values(self, frames: list[CanFrame]) -> None:
        if not self._signal_uids or self._worker is None:
            return
        wanted = {SIGNAL_UID_BASE + frame.frame_id: frame for frame in frames}
        for uid in list(self._signal_uids):
            if uid not in wanted:
                self._worker.stop_periodic(uid)
        for uid, frame in wanted.items():
            can_id = frame.frame_id
            period = self._signal_cycles.get(can_id, 100)
            if uid in self._signal_uids:
                self._worker.update_frame(uid, frame)
            else:
                self._worker.start_periodic(uid, frame, period, 0)
        self._signal_uids = list(wanted)

    def _on_signal_device(self, index: int, baud_label: str) -> None:
        if self._worker is not None:
            cfg = self.device_panel.config()
            self.signal_panel.sync_device(cfg.device_index, cfg.baud)
            self._show_message("设备已连接，请断开后再修改索引或波特率")
            return
        self.device_panel.device_index.setValue(index)
        if baud_label in BAUD_OPTIONS:
            self.device_panel.baud.setCurrentText(baud_label)

    # -------------------------------------------------------------- 工作线程

    def _on_progress(self, updates: dict) -> None:
        for uid, sent in updates.items():
            self.list_panel.model.set_sent(uid, sent)

    def _on_task_done(self, uid: int) -> None:
        if uid == SINGLE_SEND_UID:
            check = self.send_panel.periodic_check
            check.blockSignals(True)
            check.setChecked(False)
            check.blockSignals(False)
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
                self.trace_panel.set_capacity(int(trace.get("capacity", 5000)))
                self.trace_panel.set_autoscroll(bool(trace.get("autoscroll", True)))
                self.trace_panel.set_direction_filter(str(trace.get("direction", "全部")))
            except (TypeError, ValueError):
                pass
        geometry = settings.get("geometry")
        if isinstance(geometry, str):
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        send = settings.get("send", {})
        if isinstance(send, dict) and "period_ms" in send:
            try:
                self.send_panel.period_ms.setValue(int(send["period_ms"]))
            except (TypeError, ValueError):
                pass
        signal = settings.get("signal", {})
        if isinstance(signal, dict) and "period_ms" in signal:
            try:
                self.signal_panel.period.setValue(int(signal["period_ms"]))
            except (TypeError, ValueError):
                pass
        cfg = self.device_panel.config()
        self.signal_panel.sync_device(cfg.device_index, cfg.baud)

    def _save_settings(self) -> None:
        storage.save_settings({
            "bus": self.device_panel.config().to_dict(),
            "trace": {
                "capacity": self.trace_panel.capacity.value(),
                "autoscroll": self.trace_panel.autoscroll.isChecked(),
                "direction": self.trace_panel.direction.currentText(),
            },
            "send": {"period_ms": self.send_panel.period_ms.value()},
            "signal": {"period_ms": self.signal_panel.period.value()},
            "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
        })

    def closeEvent(self, event: QCloseEvent) -> None:
        running = len(self.list_panel.running_uids())
        if self.send_panel.periodic_check.isChecked():
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

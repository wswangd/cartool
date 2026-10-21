"""设备连接面板。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..bus import BACKEND_NAMES, BACKEND_SIM, BACKEND_ZLG, BusConfig
from ..zlgcan_api import BAUD_OPTIONS, BAUD_TIMING, DEVICE_TYPES, MODE_NAMES

CHANNELS = ("CAN1", "CAN2", "CAN3", "CAN4")
CUSTOM_BAUD = "自定义…"


class DevicePanel(QGroupBox):
    connect_requested = Signal()
    disconnect_requested = Signal()
    reset_requested = Signal()
    clear_buffer_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("设备连接", parent)
        self._connected = False
        self._build()
        self._wire()
        self.set_connected(False, "未连接")

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        self.backend = QComboBox()
        for value, name in BACKEND_NAMES.items():
            self.backend.addItem(name, value)

        self.device_type = QComboBox()
        for value, name in DEVICE_TYPES:
            self.device_type.addItem(f"{name} ({value})", value)

        self.device_index = QSpinBox()
        self.device_index.setRange(0, 15)

        self.channel = QComboBox()
        self.channel.addItems(CHANNELS)

        self.baud = QComboBox()
        self.baud.addItems(BAUD_OPTIONS + (CUSTOM_BAUD,))
        self.baud.setCurrentText("500kbps")

        self.timing0 = QLineEdit("00")
        self.timing1 = QLineEdit("1C")
        for edit in (self.timing0, self.timing1):
            edit.setFixedWidth(46)
            edit.setAlignment(Qt.AlignCenter)
            edit.setEnabled(False)

        self.mode = QComboBox()
        for value, name in MODE_NAMES.items():
            self.mode.addItem(name, value)

        self.acc_code = QLineEdit("00000000")
        self.acc_mask = QLineEdit("FFFFFFFF")
        for edit in (self.acc_code, self.acc_mask):
            edit.setFixedWidth(90)
            edit.setAlignment(Qt.AlignCenter)

        advanced = QWidget()
        advanced_layout = QHBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(QLabel("AccCode"))
        advanced_layout.addWidget(self.acc_code)
        advanced_layout.addWidget(QLabel("AccMask"))
        advanced_layout.addWidget(self.acc_mask)
        advanced_layout.addStretch(1)
        self.show_advanced = QCheckBox("显示滤波与自定义波特率")
        self.show_advanced.toggled.connect(advanced.setVisible)
        advanced.setVisible(False)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("接口"))
        row1.addWidget(self.backend)
        row1.addWidget(QLabel("设备类型"))
        row1.addWidget(self.device_type)
        row1.addWidget(QLabel("索引"))
        row1.addWidget(self.device_index)
        row1.addWidget(QLabel("通道"))
        row1.addWidget(self.channel)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("波特率"))
        row2.addWidget(self.baud)
        row2.addWidget(QLabel("Timing0"))
        row2.addWidget(self.timing0)
        row2.addWidget(QLabel("Timing1"))
        row2.addWidget(self.timing1)
        row2.addWidget(QLabel("工作模式"))
        row2.addWidget(self.mode)
        row2.addStretch(1)

        self.toggle_button = QPushButton("打开设备")
        self.reset_button = QPushButton("复位通道")
        self.clear_button = QPushButton("清空缓冲")
        self.status_label = QLabel("未连接")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.led = QLabel("●")
        self.led.setStyleSheet("color: #B0BEC5; font-size: 16px;")

        row3 = QHBoxLayout()
        row3.addWidget(self.led)
        row3.addWidget(self.toggle_button)
        row3.addWidget(self.reset_button)
        row3.addWidget(self.clear_button)
        row3.addSpacing(12)
        row3.addWidget(self.status_label, 1)

        self.settings_widget = QWidget()
        settings_layout = QVBoxLayout(self.settings_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.addLayout(row1)
        settings_layout.addLayout(row2)
        settings_layout.addWidget(self.show_advanced)
        settings_layout.addWidget(advanced)
        layout = QVBoxLayout(self)
        layout.addWidget(self.settings_widget)
        layout.addLayout(row3)

    def _wire(self) -> None:
        self.backend.currentIndexChanged.connect(self._on_backend_changed)
        self.baud.currentTextChanged.connect(self._on_baud_changed)
        self.toggle_button.clicked.connect(self._on_toggle)
        self.reset_button.clicked.connect(self.reset_requested)
        self.clear_button.clicked.connect(self.clear_buffer_requested)
        self._on_backend_changed()

    # ------------------------------------------------------------------ 行为

    def _on_backend_changed(self) -> None:
        is_zlg = self.backend.currentData() == BACKEND_ZLG
        for widget in (self.device_type, self.device_index):
            widget.setEnabled(is_zlg and not self._connected)
        self.channel.setEnabled(not self._connected)

    def _on_baud_changed(self, text: str) -> None:
        if text == CUSTOM_BAUD:
            self.timing0.setEnabled(True)
            self.timing1.setEnabled(True)
        else:
            timing0, timing1 = BAUD_TIMING.get(text, (0x00, 0x1C))
            self.timing0.setText(f"{timing0:02X}")
            self.timing1.setText(f"{timing1:02X}")
            self.timing0.setEnabled(False)
            self.timing1.setEnabled(False)

    def _on_toggle(self) -> None:
        if self._connected:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit()

    def _hex(self, edit: QLineEdit, default: int, bits: int) -> int:
        text = edit.text().strip().replace("0x", "").replace("0X", "")
        try:
            value = int(text, 16) if text else default
        except ValueError:
            value = default
        return max(0, min((1 << bits) - 1, value))

    def config(self) -> BusConfig:
        backend = self.backend.currentData()
        if backend == BACKEND_SIM:
            return BusConfig(backend=BACKEND_SIM, channel=self.channel.currentIndex(),
                             timing0=0x00, timing1=0x1C)
        if self.baud.currentText() == CUSTOM_BAUD:
            timing0 = self._hex(self.timing0, 0x00, 8)
            timing1 = self._hex(self.timing1, 0x1C, 8)
        else:
            timing0, timing1 = BAUD_TIMING[self.baud.currentText()]
        return BusConfig(
            backend=BACKEND_ZLG,
            device_type=self.device_type.currentData(),
            device_index=self.device_index.value(),
            channel=self.channel.currentIndex(),
            timing0=timing0,
            timing1=timing1,
            mode=self.mode.currentData(),
            acc_code=self._hex(self.acc_code, 0, 32),
            acc_mask=self._hex(self.acc_mask, 0xFFFFFFFF, 32),
        )

    def set_config(self, cfg: BusConfig) -> None:
        index = self.backend.findData(cfg.backend)
        if index >= 0:
            self.backend.setCurrentIndex(index)
        index = self.device_type.findData(cfg.device_type)
        if index >= 0:
            self.device_type.setCurrentIndex(index)
        self.device_index.setValue(int(cfg.device_index))
        self.channel.setCurrentIndex(max(0, min(len(CHANNELS) - 1, int(cfg.channel))))
        label = cfg.baud
        self.baud.setCurrentText(label if label in BAUD_OPTIONS else CUSTOM_BAUD)
        self.timing0.setText(f"{cfg.timing0:02X}")
        self.timing1.setText(f"{cfg.timing1:02X}")
        index = self.mode.findData(cfg.mode)
        if index >= 0:
            self.mode.setCurrentIndex(index)
        self.acc_code.setText(f"{cfg.acc_code:08X}")
        self.acc_mask.setText(f"{cfg.acc_mask:08X}")

    def set_connected(self, connected: bool, description: str) -> None:
        self._connected = connected
        self.toggle_button.setText("关闭设备" if connected else "打开设备")
        self.reset_button.setEnabled(connected)
        self.clear_button.setEnabled(connected)
        self.backend.setEnabled(not connected)
        self.baud.setEnabled(not connected)
        self.mode.setEnabled(not connected)
        self.acc_code.setEnabled(not connected)
        self.acc_mask.setEnabled(not connected)
        self.show_advanced.setEnabled(not connected)
        self.status_label.setText(description)
        self.status_label.setToolTip(description)
        self.led.setStyleSheet(
            "color: #2E7D32; font-size: 16px;" if connected
            else "color: #B0BEC5; font-size: 16px;")
        self._on_backend_changed()

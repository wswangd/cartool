"""单帧发送面板。"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout)

from ..frame import (DLC_MAX, DLC_OPTIONS, FORMAT_OPTIONS, SEND_TYPE_OPTIONS,
                     TYPE_OPTIONS, CanFrame, FrameFormatError)

_BYTE_RE = "[0-9A-Fa-f]{0,2}"
_ID_RE = "[0-9A-Fa-f]{0,8}"


class SendPanel(QGroupBox):
    send_requested = Signal(object)             # CanFrame
    batch_requested = Signal(object, int)       # CanFrame, 次数
    add_to_list_requested = Signal(object)      # CanFrame
    periodic_requested = Signal(object, int, int)  # CanFrame, 周期ms, 次数
    periodic_stopped = Signal()
    input_error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__("报文发送", parent)
        self._build()
        self._wire()
        self._refresh_bytes()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        hex_validator = QRegularExpressionValidator(QRegularExpression(_BYTE_RE))
        id_validator = QRegularExpressionValidator(QRegularExpression(_ID_RE))

        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMAT_OPTIONS)
        self.type_combo = QComboBox()
        self.type_combo.addItems(TYPE_OPTIONS)
        self.send_type_combo = QComboBox()
        self.send_type_combo.addItems(SEND_TYPE_OPTIONS)

        self.id_edit = QLineEdit("7E0")
        self.id_edit.setValidator(id_validator)
        self.id_edit.setFixedWidth(110)
        self.id_edit.setAlignment(Qt.AlignCenter)

        self.dlc_combo = QComboBox()
        self.dlc_combo.addItems(DLC_OPTIONS)
        self.dlc_combo.setCurrentText("8")

        self.byte_edits: list[QLineEdit] = []
        for _ in range(DLC_MAX):
            edit = QLineEdit("00")
            edit.setValidator(hex_validator)
            edit.setFixedWidth(34)
            edit.setAlignment(Qt.AlignCenter)
            self.byte_edits.append(edit)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("帧格式"))
        row1.addWidget(self.format_combo)
        row1.addWidget(QLabel("帧类型"))
        row1.addWidget(self.type_combo)
        row1.addWidget(QLabel("发送类型"))
        row1.addWidget(self.send_type_combo)
        row1.addWidget(QLabel("ID(hex)"))
        row1.addWidget(self.id_edit)
        row1.addWidget(QLabel("数据长度"))
        row1.addWidget(self.dlc_combo)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("数据(hex)"))
        for edit in self.byte_edits:
            row2.addWidget(edit)
        row2.addStretch(1)

        self.send_button = QPushButton("发送")
        self.add_button = QPushButton("加入列表")
        self.batch_count = QSpinBox()
        self.batch_count.setRange(1, 100000)
        self.batch_count.setValue(10)
        self.batch_button = QPushButton("批量发送")

        self.periodic_check = QCheckBox("定时自动发送")
        self.period_ms = QSpinBox()
        self.period_ms.setRange(1, 600000)
        self.period_ms.setValue(100)
        self.period_ms.setSuffix(" ms")
        self.period_repeat = QSpinBox()
        self.period_repeat.setRange(0, 10000000)
        self.period_repeat.setValue(0)
        self.period_repeat.setSpecialValueText("无限")
        self.period_repeat.setToolTip("发送次数，0 表示无限")

        row3 = QHBoxLayout()
        row3.addWidget(self.send_button)
        row3.addWidget(self.add_button)
        row3.addSpacing(16)
        row3.addWidget(QLabel("批量次数"))
        row3.addWidget(self.batch_count)
        row3.addWidget(self.batch_button)
        row3.addSpacing(16)
        row3.addWidget(self.periodic_check)
        row3.addWidget(QLabel("周期"))
        row3.addWidget(self.period_ms)
        row3.addWidget(QLabel("次数"))
        row3.addWidget(self.period_repeat)
        row3.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)

    def _wire(self) -> None:
        self.send_button.clicked.connect(self._on_send)
        self.add_button.clicked.connect(self._on_add)
        self.batch_button.clicked.connect(self._on_batch)
        self.periodic_check.toggled.connect(self._on_periodic_toggled)
        self.type_combo.currentTextChanged.connect(self._refresh_bytes)
        self.dlc_combo.currentTextChanged.connect(self._refresh_bytes)
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        self.period_ms.valueChanged.connect(self._on_periodic_edited)
        self.period_repeat.valueChanged.connect(self._on_periodic_edited)

    def _refresh_bytes(self) -> None:
        remote = self.type_combo.currentText() == "远程帧"
        dlc = self.dlc
        for i, edit in enumerate(self.byte_edits):
            edit.setEnabled(not remote and i < dlc)
        if remote:
            for edit in self.byte_edits:
                edit.setStyleSheet("background: #ECEFF1;")
        else:
            for i, edit in enumerate(self.byte_edits):
                edit.setStyleSheet("" if i < dlc else "background: #F5F5F5; color:#9E9E9E;")

    def _on_format_changed(self, text: str) -> None:
        limit = 0x1FFFFFFF if text == "扩展帧" else 0x7FF
        self.id_edit.setToolTip(f"最大值 0x{limit:X}")

    @property
    def dlc(self) -> int:
        try:
            return int(self.dlc_combo.currentText())
        except ValueError:
            return 0

    def frame(self) -> CanFrame:
        data = bytes(int(edit.text() or "0", 16) for edit in self.byte_edits[:self.dlc])
        return CanFrame(
            frame_id=int(self.id_edit.text().strip() or "0", 16),
            data=data,
            dlc=self.dlc,
            extended=self.format_combo.currentText() == "扩展帧",
            remote=self.type_combo.currentText() == "远程帧",
            send_type=self.send_type_combo.currentIndex(),
        )

    def _validated(self) -> CanFrame | None:
        try:
            frame = self.frame()
        except (FrameFormatError, ValueError) as exc:
            self.input_error.emit(str(exc) if isinstance(exc, FrameFormatError)
                                  else f"输入不合法：{exc}")
            return None
        return frame

    # ------------------------------------------------------------------ 动作

    def _on_send(self) -> None:
        frame = self._validated()
        if frame:
            self.send_requested.emit(frame)

    def _on_batch(self) -> None:
        frame = self._validated()
        if frame:
            self.batch_requested.emit(frame, self.batch_count.value())

    def _on_add(self) -> None:
        frame = self._validated()
        if frame:
            self.add_to_list_requested.emit(frame)

    def _on_periodic_toggled(self, checked: bool) -> None:
        if not checked:
            self.periodic_stopped.emit()
            return
        frame = self._validated()
        if frame is None:
            self.periodic_check.blockSignals(True)
            self.periodic_check.setChecked(False)
            self.periodic_check.blockSignals(False)
            return
        self.periodic_requested.emit(frame, self.period_ms.value(),
                                     self.period_repeat.value())

    def _on_periodic_edited(self) -> None:
        if not self.periodic_check.isChecked():
            return
        frame = self._validated()
        if frame is None:
            return
        self.periodic_requested.emit(frame, self.period_ms.value(),
                                     self.period_repeat.value())

    def set_bus_enabled(self, enabled: bool) -> None:
        for widget in (self.send_button, self.batch_button, self.periodic_check):
            widget.setEnabled(enabled)
        if not enabled and self.periodic_check.isChecked():
            self.periodic_check.setChecked(False)

    def set_frame(self, frame: CanFrame) -> None:
        self.format_combo.setCurrentText(frame.format_text)
        self.type_combo.setCurrentText(frame.type_text)
        self.send_type_combo.setCurrentIndex(frame.send_type)
        self.id_edit.setText(f"{frame.frame_id:X}")
        self.dlc_combo.setCurrentText(str(frame.dlc))
        for i, edit in enumerate(self.byte_edits):
            edit.setText(f"{frame.data[i]:02X}" if i < len(frame.data) else "00")
        self._refresh_bytes()

"""单帧发送面板。"""

from __future__ import annotations

from .widgets import OperationButton

from PySide6.QtCore import QEvent, QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout)

from ..frame import (DLC_MAX, DLC_OPTIONS, FORMAT_OPTIONS, SEND_TYPE_OPTIONS,
                     TYPE_OPTIONS, CanFrame, FrameFormatError)

_BYTE_RE = "[0-9A-Fa-f]{0,2}"
_ID_RE = "[0-9A-Fa-f]{0,8}"


class SendPanel(QGroupBox):
    add_to_list_requested = Signal(object)      # CanFrame
    periodic_requested = Signal(object, int, int)  # CanFrame, 周期ms, 次数
    periodic_stopped = Signal()
    input_error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__("报文发送", parent)
        self._backspace_target: QLineEdit | None = None
        self._running = False
        self._bus_enabled = False
        self._build()
        self._wire()
        self._refresh_bytes()
        self.set_bus_enabled(False)

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

        self.id_edit = QLineEdit("100")
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
            edit.setMaxLength(2)
            edit.setFixedWidth(34)
            edit.setAlignment(Qt.AlignCenter)
            edit.installEventFilter(self)
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

        self.send_button = OperationButton("发送")
        self.add_button = QPushButton("加入列表")
        self.period_ms = QSpinBox()
        self.period_ms.setRange(1, 600000)
        self.period_ms.setSingleStep(5)
        self.period_ms.setValue(100)
        self.period_ms.setSuffix(" ms")
        self.period_repeat = QSpinBox()
        self.period_repeat.setRange(0, 10000000)
        self.period_repeat.setValue(0)
        self.period_repeat.setSpecialValueText("无限")
        self.period_repeat.setToolTip("发送次数，0 表示无限")

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("次数"))
        row3.addWidget(self.period_repeat)
        row3.addWidget(QLabel("周期"))
        row3.addWidget(self.period_ms)
        row3.addWidget(self.add_button)
        row3.addWidget(self.send_button)
        row3.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)

    def _wire(self) -> None:
        self.send_button.clicked.connect(self._on_send)
        self.add_button.clicked.connect(self._on_add)
        self.type_combo.currentTextChanged.connect(self._refresh_bytes)
        self.dlc_combo.currentTextChanged.connect(self._refresh_bytes)
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        for index, edit in enumerate(self.byte_edits):
            edit.textEdited.connect(
                lambda text, byte_index=index: self._on_byte_edited(byte_index, text))

    def _refresh_bytes(self) -> None:
        remote = self.type_combo.currentText() == "远程帧"
        dlc = self.dlc
        for i, edit in enumerate(self.byte_edits):
            edit.setEnabled(not self._running and not remote and i < dlc)
        if remote:
            for edit in self.byte_edits:
                edit.setStyleSheet("background: #ECEFF1;")
        else:
            for i, edit in enumerate(self.byte_edits):
                edit.setStyleSheet("" if i < dlc else "background: #F5F5F5; color:#9E9E9E;")

    def _on_format_changed(self, text: str) -> None:
        limit = 0x1FFFFFFF if text == "扩展帧" else 0x7FF
        self.id_edit.setToolTip(f"最大值 0x{limit:X}")

    def _on_byte_edited(self, index: int, text: str) -> None:
        """输入完整字节后将焦点移到下一个有效数据字节。"""
        current = self.byte_edits[index]
        if len(text) != 2 or current.cursorPosition() != 2:
            return
        next_index = index + 1
        if next_index >= self.dlc:
            return
        next_edit = self.byte_edits[next_index]
        if next_edit.isEnabled():
            next_edit.setFocus()
            next_edit.selectAll()

    def eventFilter(self, watched, event) -> bool:
        if watched not in self.byte_edits or event.type() != QEvent.KeyPress:
            return super().eventFilter(watched, event)

        returned_by_backspace = watched is self._backspace_target
        self._backspace_target = None
        if (event.key() in (Qt.Key_Left, Qt.Key_Right)
                and not (event.modifiers() & ~Qt.KeypadModifier)):
            moving_left = event.key() == Qt.Key_Left
            if watched.hasSelectedText():
                position = watched.selectionStart()
                if not moving_left:
                    position += len(watched.selectedText())
                watched.setCursorPosition(position)
                return True
            position = watched.cursorPosition()
            if moving_left and position > 0:
                watched.setCursorPosition(position - 1)
            elif not moving_left and position < len(watched.text()):
                watched.setCursorPosition(position + 1)
            else:
                index = self.byte_edits.index(watched)
                target_index = index - 1 if moving_left else index + 1
                if 0 <= target_index < self.dlc:
                    target = self.byte_edits[target_index]
                    if target.isEnabled():
                        target.setFocus()
                        target.setCursorPosition(len(target.text()) if moving_left else 0)
            return True
        if event.key() == Qt.Key_Backspace:
            if returned_by_backspace:
                return super().eventFilter(watched, event)
            index = self.byte_edits.index(watched)
            selected_all = (watched.hasSelectedText()
                            and watched.selectedText() == watched.text())
            if index > 0 and (not watched.text() or watched.cursorPosition() == 0
                              or selected_all):
                previous = self.byte_edits[index - 1]
                if previous.isEnabled():
                    previous.setFocus()
                    previous.selectAll()
                    self._backspace_target = previous
                    return True
        return super().eventFilter(watched, event)

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
        if self._running:
            self.periodic_stopped.emit()
            self.set_running(False)
            return
        if not self._bus_enabled:
            return
        frame = self._validated()
        if frame:
            self.set_running(True)
            self.periodic_requested.emit(frame, self.period_ms.value(),
                                         self.period_repeat.value())

    def _on_add(self) -> None:
        frame = self._validated()
        if frame:
            self.add_to_list_requested.emit(frame)

    def is_running(self) -> bool:
        return self._running

    def set_running(self, running: bool) -> None:
        self._running = running
        self.send_button.setText("停止" if running else "发送")
        for widget in (self.format_combo, self.type_combo, self.send_type_combo,
                       self.id_edit, self.dlc_combo, self.period_ms, self.period_repeat):
            widget.setEnabled(not running)
        self._refresh_bytes()

    def set_bus_enabled(self, enabled: bool) -> None:
        self._bus_enabled = enabled
        self.send_button.setEnabled(enabled)
        if not enabled:
            self.set_running(False)

    def set_frame(self, frame: CanFrame) -> None:
        self.format_combo.setCurrentText(frame.format_text)
        self.type_combo.setCurrentText(frame.type_text)
        self.send_type_combo.setCurrentIndex(frame.send_type)
        self.id_edit.setText(f"{frame.frame_id:08X}")
        self.dlc_combo.setCurrentText(str(frame.dlc))
        for i, edit in enumerate(self.byte_edits):
            edit.setText(f"{frame.data[i]:02X}" if i < len(frame.data) else "00")
        self._refresh_bytes()

"""报文显示面板：收发日志、过滤、暂停与导出。"""

from __future__ import annotations

from pathlib import Path

from .widgets import OperationButton

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QTableView, QVBoxLayout, QWidget)

from .. import storage
from ..frame import RX, TX, TraceRecord, parse_id
from ..models import TraceModel
from ..storage import StorageError

DIRECTIONS = ("全部", "仅接收", "仅发送")


class TracePanel(QWidget):
    message = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = TraceModel(self)
        self._paused = False
        self._hidden = 0
        self._filter_ids: set[int] | None = None
        self._last_dir = str(Path.home())
        self._build()
        self._wire()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        self.pause_button = OperationButton("暂停")
        self.pause_button.setCheckable(True)
        self.clear_button = QPushButton("清空")
        self.save_button = QPushButton("保存报文")

        self.autoscroll = QCheckBox("自动滚动")
        self.autoscroll.setChecked(True)

        self.direction = QComboBox()
        self.direction.addItems(DIRECTIONS)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤 ID，多个用逗号分隔，留空不过滤")
        self.filter_edit.setFixedWidth(240)
        self.filter_edit.setClearButtonEnabled(True)

        self.capacity = QSpinBox()
        self.capacity.setRange(100, TraceModel.MAX_CAPACITY)
        self.capacity.setSingleStep(1000)
        self.capacity.setValue(TraceModel.DEFAULT_CAPACITY)
        self.capacity.setSuffix(" 行")

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color:#616161;")

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.pause_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.save_button)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.autoscroll)
        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel("方向"))
        toolbar.addWidget(self.direction)
        toolbar.addWidget(QLabel("过滤"))
        toolbar.addWidget(self.filter_edit)
        toolbar.addWidget(QLabel("容量"))
        toolbar.addWidget(self.capacity)
        toolbar.addStretch(1)
        toolbar.addWidget(self.info_label)

        self.table = QTableView()
        self.table.setStyleSheet(
            "QTableView::item:selected { color: white; background-color: palette(highlight); }")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header_font = self.table.font()
        header_font.setBold(True)
        header.setFont(header_font)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        for column, width in ((0, 66), (1, 96), (2, 92), (3, 48), (4, 66),
                              (5, 66), (6, 96), (7, 48)):
            header.resizeSection(column, width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)

    def _wire(self) -> None:
        self.pause_button.toggled.connect(self._on_pause)
        self.clear_button.clicked.connect(self.clear)
        self.save_button.clicked.connect(self._on_save)
        self.capacity.valueChanged.connect(self.model.set_capacity)
        self.filter_edit.textChanged.connect(self._on_filter_changed)

    # ------------------------------------------------------------------ 行为

    def add_records(self, records: list[TraceRecord]) -> None:
        if self._paused:
            self._hidden += len(records)
            self._update_info()
            return
        accepted = [record for record in records if self._accept(record)]
        self._hidden += len(records) - len(accepted)
        if not accepted:
            self._update_info()
            return
        self.model.append(accepted)
        if self.autoscroll.isChecked() and not self.table.selectionModel().selectedRows():
            self.table.scrollToBottom()
        self._update_info()

    def _accept(self, record: TraceRecord) -> bool:
        direction = self.direction.currentText()
        if direction == "仅接收" and record.direction != RX:
            return False
        if direction == "仅发送" and record.direction != TX:
            return False
        if self._filter_ids is not None and record.frame.frame_id not in self._filter_ids:
            return False
        return True

    def _on_filter_changed(self, text: str) -> None:
        text = text.strip()
        if not text:
            self._filter_ids = None
            self._update_info()
            return
        ids = set()
        for token in text.replace("，", ",").replace(" ", ",").split(","):
            if not token:
                continue
            try:
                ids.add(parse_id(token))
            except ValueError:
                self.message.emit(f"过滤 ID '{token}' 不是合法的十六进制数，已忽略")
        self._filter_ids = ids or None
        self._update_info()

    def _on_pause(self, checked: bool) -> None:
        self._paused = checked
        self.pause_button.setText("继续" if checked else "暂停")
        if not checked:
            self._hidden = 0
        self._update_info()

    def _update_info(self) -> None:
        text = f"显示 {self.model.rowCount()} / 容量 {self.model.capacity}"
        if self._paused:
            text += f"  |  已暂停，忽略 {self._hidden} 帧"
        elif self._hidden:
            text += f"  |  已过滤 {self._hidden} 帧"
        if self.model.dropped:
            text += f"  |  滚动丢弃 {self.model.dropped}"
        self.info_label.setText(text)

    def clear(self) -> None:
        self._hidden = 0
        self.model.clear()
        self._update_info()

    def _on_save(self) -> None:
        records = self.model.records()
        if not records:
            self.message.emit("当前没有可保存的报文")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存报文日志", str(Path(self._last_dir) / "can_log.csv"),
            "CSV (*.csv)")
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        try:
            storage.export_trace_csv(Path(path), records)
        except (StorageError, OSError) as exc:
            self.message.emit(f"保存失败：{exc}")
            return
        self.message.emit(f"已保存 {len(records)} 帧到 {path}")

    def set_capacity(self, value: int) -> None:
        self.capacity.setValue(value)

    def set_autoscroll(self, value: bool) -> None:
        self.autoscroll.setChecked(value)

    def set_direction_filter(self, text: str) -> None:
        if text in DIRECTIONS:
            self.direction.setCurrentText(text)

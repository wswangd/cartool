"""列表发送面板：报文条目管理、批量发送与定时发送。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QMenu,
                               QPushButton, QTableView, QVBoxLayout, QWidget)

from .. import storage
from ..frame import (CanFrame, DLC_OPTIONS, FORMAT_OPTIONS, SEND_TYPE_OPTIONS,
                      SendItem, TYPE_OPTIONS)
from ..models import (COL_DLC, COL_DATA, COL_ENABLED, COL_FORMAT, COL_ID,
                      COL_NOTE, COL_PERIOD, COL_REPEAT, COL_SEND_TYPE, COL_SENT,
                      COL_TYPE, SendListModel)
from ..storage import StorageError
from .widgets import ComboDelegate, SpinDelegate


class ListPanel(QWidget):
    send_frames = Signal(object)     # list[CanFrame]
    start_tasks = Signal(object)     # list[tuple[uid, CanFrame, period_ms, repeat]]
    stop_tasks = Signal(object)      # list[uid]
    message = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = SendListModel(self)
        self._running: set[int] = set()
        self._last_dir = str(Path.home())
        self._editor_columns = {COL_FORMAT, COL_TYPE, COL_SEND_TYPE, COL_DLC,
                                COL_PERIOD, COL_REPEAT}
        self._build()
        self._wire()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.setItemDelegateForColumn(COL_FORMAT, ComboDelegate(FORMAT_OPTIONS, self))
        self.table.setItemDelegateForColumn(COL_TYPE, ComboDelegate(TYPE_OPTIONS, self))
        self.table.setItemDelegateForColumn(COL_SEND_TYPE,
                                            ComboDelegate(SEND_TYPE_OPTIONS, self))
        self.table.setItemDelegateForColumn(COL_DLC,
                                            ComboDelegate(DLC_OPTIONS, self))
        self.table.setItemDelegateForColumn(COL_PERIOD,
                                            SpinDelegate(0, 600000, self))
        self.table.setItemDelegateForColumn(COL_REPEAT,
                                            SpinDelegate(0, 10000000, self, "无限"))

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(COL_DATA, QHeaderView.Stretch)
        for column, width in ((COL_ENABLED, 44), (COL_ID, 90), (COL_FORMAT, 80),
                              (COL_TYPE, 76), (COL_SEND_TYPE, 110), (COL_DLC, 52),
                              (COL_PERIOD, 80), (COL_REPEAT, 66), (COL_NOTE, 130)):
            header.resizeSection(column, width)

        buttons = (
            ("新增", self._on_add), ("删除", self._on_remove), ("清空", self._on_clear),
            ("导入…", self._on_import), ("导出…", self._on_export),
        )
        send_buttons = (
            ("发送选中", self._on_send_selected), ("全部发送", self._on_send_all),
            ("开始定时", self._on_start_periodic), ("停止定时", self._on_stop_periodic),
        )
        toolbar = QHBoxLayout()
        for text, slot in buttons:
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addSpacing(20)
        self._send_buttons: list[QPushButton] = []
        for text, slot in send_buttons:
            button = QPushButton(text)
            button.clicked.connect(slot)
            self._send_buttons.append(button)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.count_label = QPushButton("0 条")
        self.count_label.setFlat(True)
        self.count_label.setEnabled(False)
        toolbar.addWidget(self.count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)

    def _wire(self) -> None:
        self.model.rowsInserted.connect(self._on_rows_inserted)
        self.model.rowsRemoved.connect(self._on_rows_removed)
        self.model.modelReset.connect(self._on_model_reset)
        self.model.parse_error.connect(self.message)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.model.dataChanged.connect(self._on_data_changed)

    def _open_editors(self, first: int, last: int) -> None:
        for row in range(first, last + 1):
            for col in self._editor_columns:
                self.table.openPersistentEditor(self.model.index(row, col))

    def _close_editors(self, first: int, last: int) -> None:
        for row in range(first, last + 1):
            for col in self._editor_columns:
                self.table.closePersistentEditor(self.model.index(row, col))

    def _on_rows_inserted(self, parent, first: int, last: int) -> None:
        self._refresh_count()
        self._open_editors(first, last)

    def _on_rows_removed(self, parent, first: int, last: int) -> None:
        self._refresh_count()
        self._close_editors(first, last)

    def _on_model_reset(self) -> None:
        self._refresh_count()
        self._open_editors(0, self.model.rowCount() - 1)

    def _refresh_count(self) -> None:
        self.count_label.setText(f"{self.model.rowCount()} 条")

    # ------------------------------------------------------------------ 动作

    def add_frame(self, frame: CanFrame, note: str = "", period_ms: int = 0,
                  repeat: int = 0) -> None:
        self.model.append(frame, note=note, period_ms=period_ms, repeat=repeat)

    def _on_add(self) -> None:
        self.model.append(CanFrame(frame_id=0x100, data=bytes(8), dlc=8))
        self.table.scrollToBottom()

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def _on_remove(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.message.emit("请先选中要删除的行")
            return
        removed = [item.uid for row in rows if (item := self.model.item(row))]
        self.stop_tasks.emit([uid for uid in removed if uid in self._running])
        self._running.difference_update(removed)
        self.model.remove_rows(rows)

    def _on_clear(self) -> None:
        if not self.model.rowCount():
            return
        self.stop_tasks.emit(list(self._running))
        self._running.clear()
        self.model.clear()
        self.model.reset_sent()

    def _on_send_selected(self) -> None:
        frames = [item.frame.copy() for row in self._selected_rows()
                  if (item := self.model.item(row))]
        self._dispatch(frames, "选中")

    def _on_send_all(self) -> None:
        frames = [item.frame.copy() for item in self.model.enabled_items()]
        self._dispatch(frames, "启用")

    def _dispatch(self, frames: list[CanFrame], label: str) -> None:
        if not frames:
            self.message.emit(f"没有可发送的{label}报文")
            return
        self.send_frames.emit(frames)

    def _on_start_periodic(self) -> None:
        tasks = []
        for item in self.model.enabled_items():
            if item.period_ms <= 0:
                continue
            tasks.append((item.uid, item.frame.copy(), item.period_ms, item.repeat))
            self._running.add(item.uid)
        if not tasks:
            self.message.emit("没有可定时的报文：需勾选启用并把周期设为大于 0")
            return
        self.model.set_sending(True)
        self.start_tasks.emit(tasks)
        self.message.emit(f"已启动 {len(tasks)} 条定时报文")

    def _on_stop_periodic(self) -> None:
        if not self._running:
            self.message.emit("当前没有正在定时的报文")
            return
        uids = list(self._running)
        self._running.clear()
        self.model.set_sending(False)
        self.stop_tasks.emit(uids)
        self.message.emit(f"已停止 {len(uids)} 条定时报文")

    def mark_task_finished(self, uid: int) -> None:
        if uid in self._running:
            self._running.discard(uid)
            if not self._running:
                self.model.set_sending(False)
            self.message.emit(f"第 {self.model.row_of_uid(uid) + 1} 行报文已按设定次数发送完毕")

    def running_uids(self) -> list[int]:
        return list(self._running)

    def _on_data_changed(self, top_left, bottom_right, roles=()) -> None:
        if top_left.column() == COL_SENT:
            return
        for row in range(top_left.row(), bottom_right.row() + 1):
            item = self.model.item(row)
            if item and item.uid in self._running and item.period_ms > 0:
                self.start_tasks.emit([(item.uid, item.frame.copy(),
                                        item.period_ms, item.repeat)])

    # ------------------------------------------------------------- 导入导出

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入报文列表", self._last_dir,
            "报文列表 (*.json *.csv *.txt);;JSON (*.json);;CSV (*.csv *.txt)")
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        try:
            items = storage.load_items(Path(path))
        except StorageError as exc:
            self.message.emit(f"导入失败：{exc}")
            return
        if not items:
            self.message.emit("文件里没有有效报文")
            return
        for item in items:
            item.uid = self.model.new_uid()
        self.model.append_items(items)
        self.message.emit(f"已导入 {len(items)} 条报文")

    def _on_export(self) -> None:
        items = self.model.items()
        if not items:
            self.message.emit("列表是空的，没有可导出的报文")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出报文列表", str(Path(self._last_dir) / "报文列表.csv"),
            "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        try:
            storage.save_list(Path(path), items)
        except StorageError as exc:
            self.message.emit(f"导出失败：{exc}")
            return
        self.message.emit(f"已导出 {len(items)} 条报文到 {path}")

    # ------------------------------------------------------------- 右键菜单

    def _on_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        menu = QMenu(self)
        if index.isValid():
            row = index.row()
            item = self.model.item(row)
            running = item.uid in self._running
            menu.addAction("立即发送本帧", lambda: self._dispatch([item.frame.copy()], ""))
            if running:
                menu.addAction("停止本行定时", lambda: self._stop_one(item.uid))
            else:
                menu.addAction("开始本行定时", lambda: self._start_one(item))
            menu.addSeparator()
            menu.addAction("在下方插入行", lambda: self._insert_below(row))
            menu.addAction("删除本行", lambda: self._remove_row(row))
            menu.addSeparator()
        menu.addAction("全部发送", self._on_send_all)
        menu.addAction("开始定时", self._on_start_periodic)
        menu.addAction("停止定时", self._on_stop_periodic)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _start_one(self, item: SendItem) -> None:
        if item.period_ms <= 0:
            self.message.emit("该行周期为 0，请先设置周期(ms)")
            return
        self._running.add(item.uid)
        self.start_tasks.emit([(item.uid, item.frame.copy(), item.period_ms,
                                item.repeat)])

    def _stop_one(self, uid: int) -> None:
        self._running.discard(uid)
        self.stop_tasks.emit([uid])
        if not self._running:
            self.model.set_sending(False)

    def _remove_row(self, row: int) -> None:
        self.table.selectRow(row)
        self._on_remove()

    def _insert_below(self, row: int) -> None:
        source = self.model.item(row)
        frame = source.frame.copy() if source else CanFrame()
        item = SendItem(uid=self.model.new_uid(), frame=frame,
                        period_ms=source.period_ms if source else 0,
                        repeat=source.repeat if source else 0)
        self.model.insert_row(row + 1, item)

    def set_bus_enabled(self, enabled: bool) -> None:
        for button in self._send_buttons:
            button.setEnabled(enabled)
        if not enabled:
            self._running.clear()
            self.model.set_sending(False)

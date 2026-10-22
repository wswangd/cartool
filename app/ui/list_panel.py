"""列表发送面板：报文条目管理、批量发送与定时发送。"""

from __future__ import annotations

from pathlib import Path

from .widgets import OperationButton

from PySide6.QtCore import QEvent, QItemSelectionModel, QPersistentModelIndex, QSize, Qt, Signal
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLayout, QMenu, QPushButton, QSpinBox, QTableView,
                               QVBoxLayout, QWidget)

from .. import storage
from ..frame import (CanFrame, DLC_OPTIONS, FORMAT_OPTIONS, SEND_TYPE_OPTIONS,
                      SendItem, TYPE_OPTIONS)
from ..models import (COL_DLC, COL_DATA, COL_ENABLED, COL_FORMAT, COL_ID,
                      COL_NOTE, COL_PERIOD, COL_REPEAT, COL_SEND_TYPE, COL_SENT,
                      COL_TYPE, SendListModel)
from ..storage import StorageError
from .widgets import CenteredCheckDelegate, ComboDelegate, SpinDelegate


class ListPanel(QWidget):
    send_frames = Signal(object)     # list[CanFrame]
    start_tasks = Signal(object)     # list[tuple[uid, CanFrame, period_ms, repeat]]
    start_sequence = Signal(object, int, int, int)
    stop_tasks = Signal(object)      # list[uid]
    message = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = SendListModel(self)
        self._running: set[int] = set()
        self._bus_enabled = False
        self._last_dir = str(Path.home())
        self._editor_columns = {COL_FORMAT, COL_TYPE, COL_SEND_TYPE, COL_DLC,
                                COL_PERIOD, COL_REPEAT}
        self._build()
        self._wire()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_ENABLED, CenteredCheckDelegate(self.table))
        self.table.hideColumn(COL_SENT)
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
                                            SpinDelegate(0, 600000, self, step=5))
        self.table.setItemDelegateForColumn(COL_REPEAT,
                                            SpinDelegate(0, 10000000, self, "无限"))

        header = self.table.horizontalHeader()
        header_font = self.table.font()
        header_font.setBold(True)
        header.setFont(header_font)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(40)
        for column, width in ((COL_ENABLED, 44), (COL_ID, 90), (COL_FORMAT, 80),
                              (COL_TYPE, 76), (COL_SEND_TYPE, 110), (COL_DLC, 52),
                              (COL_DATA, 240), (COL_PERIOD, 80), (COL_REPEAT, 66),
                              (COL_NOTE, 160)):
            header.resizeSection(column, width)

        buttons = (
            ("新增", self._on_add), ("删除", self._on_remove), ("清空", self._on_clear),
            ("导入", self._on_import), ("导出", self._on_export),
        )
        toolbar = QHBoxLayout()
        actions = QWidget()
        actions.setLayout(toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        for text, slot in buttons:
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        controls = QWidget()
        toolbar = QHBoxLayout(controls)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addWidget(QLabel("发送模式"))
        self.send_mode = QComboBox()
        self.send_mode.addItems(["并行发送", "顺序发送"])
        self.send_mode.setToolTip("并行：各行独立按周期发送；顺序循环：使用报文间隔和列表间隔")
        toolbar.addWidget(self.send_mode)
        self.loop_label = QLabel("循环次数")
        toolbar.addWidget(self.loop_label)
        self.loop_count = QSpinBox()
        self.loop_count.setRange(0, 10000000)
        self.loop_count.setSpecialValueText("无限")
        self.loop_count.setFixedWidth(90)
        toolbar.addWidget(self.loop_count)
        self.interval_label = QLabel("报文间隔")
        self.interval_ms = QSpinBox()
        self.cycle_interval_label = QLabel("列表间隔")
        self.cycle_interval_ms = QSpinBox()
        for label, editor in ((self.interval_label, self.interval_ms),
                              (self.cycle_interval_label, self.cycle_interval_ms)):
            editor.setRange(1, 600000)
            editor.setSingleStep(5)
            editor.setValue(100)
            editor.setSuffix(" ms")
            editor.setFixedWidth(max(125, editor.sizeHint().width()))
            toolbar.addWidget(label)
            toolbar.addWidget(editor)
        self.interval_ms.setToolTip("同一轮中，相邻两条报文之间的间隔")
        self.cycle_interval_ms.setToolTip("本轮最后一条到下一轮第一条报文的间隔")
        self.start_button = OperationButton("发送")
        self.start_button.clicked.connect(self._on_toggle_send)
        toolbar.addWidget(self.start_button)
        self._refresh_send_state()
        toolbar.addStretch(1)
        self._toolbar_layout = QBoxLayout(QBoxLayout.LeftToRight)
        self._toolbar_layout.setSpacing(8)
        self._toolbar_layout.addWidget(actions)
        self._toolbar_layout.addWidget(controls, 1)
        self._toolbar_groups = (actions, controls)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._toolbar_layout)
        layout.addWidget(self.table)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_toolbar_layout()

    def minimumSizeHint(self) -> QSize:
        size = super().minimumSizeHint()
        if hasattr(self, "_toolbar_groups"):
            size.setWidth(max(widget.minimumSizeHint().width()
                              for widget in self._toolbar_groups))
        return size

    def _update_toolbar_layout(self) -> None:
        if not hasattr(self, "_toolbar_groups"):
            return
        required = sum(widget.minimumSizeHint().width() for widget in self._toolbar_groups) + 8
        direction = QBoxLayout.TopToBottom if self.width() < required else QBoxLayout.LeftToRight
        if self._toolbar_layout.direction() != direction:
            self._toolbar_layout.setDirection(direction)

    def _wire(self) -> None:
        self.model.rowsInserted.connect(self._on_rows_inserted)
        self.model.rowsRemoved.connect(self._on_rows_removed)
        self.model.modelReset.connect(self._on_model_reset)
        self.model.parse_error.connect(self.message)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.model.dataChanged.connect(self._on_data_changed)
        self.table.selectionModel().selectionChanged.connect(self._refresh_editor_selection)
        self.send_mode.currentIndexChanged.connect(self._refresh_send_state)

    def _open_editors(self, first: int, last: int) -> None:
        for row in range(first, last + 1):
            for col in self._editor_columns:
                self.table.openPersistentEditor(self.model.index(row, col))
                editor = self.table.indexWidget(self.model.index(row, col))
                if editor is not None:
                    index = QPersistentModelIndex(self.model.index(row, col))
                    for target in [editor, *editor.findChildren(QWidget)]:
                        target._send_list_index = index
                        target.installEventFilter(self)
        self._refresh_send_state()
        self._refresh_editor_selection()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            index = getattr(watched, "_send_list_index", None)
            if index is not None and index.isValid():
                selection = self.table.selectionModel()
                if not selection.isRowSelected(index.row()):
                    flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
                    if not event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                        flags |= QItemSelectionModel.Clear
                    selection.setCurrentIndex(self.model.index(index.row(), index.column()), flags)
                self._refresh_editor_selection()
        return super().eventFilter(watched, event)

    def _refresh_editor_selection(self, *args) -> None:
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        palette = self.table.palette()
        background = palette.highlight().color().name()
        foreground = palette.highlightedText().color().name()
        popup_background = palette.base().color().name()
        popup_foreground = palette.text().color().name()
        for row in range(self.model.rowCount()):
            for column in self._editor_columns:
                editor = self.table.indexWidget(self.model.index(row, column))
                if editor is not None:
                    editor.setStyleSheet(
                        f"QComboBox, QSpinBox, QLineEdit {{ background-color: {background}; color: {foreground}; }}"
                        f"QComboBox QAbstractItemView {{ background-color: {popup_background}; color: {popup_foreground};"
                        f" selection-background-color: {background}; selection-color: {foreground}; }}"
                        if row in selected else "")
                    if isinstance(editor, QComboBox):
                        # 弹层有独立的容器，不能继承选中单元格的蓝色背景。
                        view = editor.view()
                        view.setStyleSheet(
                            f"QAbstractItemView {{ background: {popup_background}; color: {popup_foreground};"
                            f" border: 1px solid #B8BEC5; selection-background-color: {background};"
                            f" selection-color: {foreground}; }}")
                        view.window().setStyleSheet(
                            f"QFrame {{ background: {popup_background}; border: 1px solid #B8BEC5; }}")

    def _close_editors(self, first: int, last: int) -> None:
        for row in range(first, last + 1):
            for col in self._editor_columns:
                self.table.closePersistentEditor(self.model.index(row, col))

    def _on_rows_inserted(self, parent, first: int, last: int) -> None:
        self._refresh_count()
        self._open_editors(first, last)

    def _on_rows_removed(self, parent, first: int, last: int) -> None:
        self._refresh_count()
        # QTableView 已随模型移除被删除行的编辑器；此时旧行号属于后续行。
        self._refresh_send_state()
        self._refresh_editor_selection()

    def _on_model_reset(self) -> None:
        self._refresh_count()
        self._open_editors(0, self.model.rowCount() - 1)

    def _refresh_count(self) -> None:
        self.table.viewport().update()

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
        self._refresh_send_state()
        self.model.remove_rows(rows)

    def _on_clear(self) -> None:
        if not self.model.rowCount():
            return
        self.stop_tasks.emit(list(self._running))
        self._running.clear()
        self._refresh_send_state()
        self.model.clear()
        self.model.reset_sent()

    def _dispatch(self, frames: list[CanFrame], label: str) -> None:
        if not frames:
            self.message.emit(f"没有可发送的{label}报文")
            return
        self.send_frames.emit(frames)

    def _on_toggle_send(self) -> None:
        if self._running:
            self._on_stop_periodic()
        else:
            self._on_start_periodic()

    def _on_start_periodic(self) -> None:
        if not self._bus_enabled or self._running:
            return
        self.table.setFocus(Qt.OtherFocusReason)
        enabled = self.model.enabled_items()
        sequential = self.send_mode.currentIndex() == 1
        if not enabled or (not sequential and any(item.period_ms <= 0 and item.repeat != 1 for item in enabled)):
            self.message.emit("请勾选至少一条报文；次数不为 1 的启用行需设置大于 0 的周期")
            return
        tasks = []
        single_frames = []
        for item in enabled:
            if not sequential and item.repeat == 1:
                single_frames.append(item.frame.copy())
                continue
            tasks.append((item.uid, item.frame.copy(), item.period_ms, item.repeat))
            self._running.add(item.uid)
        if single_frames:
            self.send_frames.emit(single_frames)
        if not tasks:
            self.message.emit(f"已发送 {len(single_frames)} 条单次报文")
            return
        self._refresh_send_state()
        if self.send_mode.currentIndex() == 1:
            self.start_sequence.emit(tasks, self.loop_count.value(),
                                     self.interval_ms.value(), self.cycle_interval_ms.value())
        else:
            self.start_tasks.emit(tasks)
        self.message.emit(f"已启动 {len(tasks)} 条定时报文")

    def _on_stop_periodic(self) -> None:
        if not self._running:
            self.message.emit("当前没有正在定时的报文")
            return
        uids = list(self._running)
        self._running.clear()
        self._refresh_send_state()
        self.stop_tasks.emit(uids)
        self.message.emit(f"已停止 {len(uids)} 条定时报文")

    def mark_task_finished(self, uid: int) -> None:
        if uid in self._running:
            self._running.discard(uid)
            self._refresh_send_state()
            self.message.emit(f"第 {self.model.row_of_uid(uid) + 1} 行报文已按设定次数发送完毕")

    def running_uids(self) -> list[int]:
        return list(self._running)

    def sequence_finished(self) -> None:
        self._running.clear()
        self._refresh_send_state()
        self.message.emit("列表循环发送完成")

    def show_sequence_progress(self, uid: int, cycle: int) -> None:
        if uid not in self._running or self.send_mode.currentIndex() != 1:
            return
        row = self.model.row_of_uid(uid)
        self.table.selectRow(row)
        self.table.scrollTo(self.model.index(row, COL_ID))

    def _on_data_changed(self, top_left, bottom_right, roles=()) -> None:
        if top_left.column() <= COL_REPEAT <= bottom_right.column():
            self._refresh_send_state()
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
            item.enabled = False
            item.uid = self.model.new_uid()
        self.model.append_items(items)
        self.message.emit(f"已导入 {len(items)} 条报文")

    def _on_export(self) -> None:
        self.table.setFocus(Qt.OtherFocusReason)
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
        if self._running:
            menu = QMenu(self)
            menu.addAction("停止", self._on_stop_periodic)
            menu.exec(self.table.viewport().mapToGlobal(position))
            return
        index = self.table.indexAt(position)
        menu = QMenu(self)
        if index.isValid():
            row = index.row()
            item = self.model.item(row)
            running = item.uid in self._running
            if running:
                menu.addAction("停止本行定时", lambda: self._stop_one(item.uid))
            menu.addSeparator()
            menu.addAction("在下方插入行", lambda: self._insert_below(row))
            menu.addAction("删除本行", lambda: self._remove_row(row))
            menu.addSeparator()
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _start_one(self, item: SendItem) -> None:
        if not self._bus_enabled:
            return
        if item.period_ms <= 0:
            self.message.emit("该行周期为 0，请先设置周期(ms)")
            return
        self._running.add(item.uid)
        self._refresh_send_state()
        self.start_tasks.emit([(item.uid, item.frame.copy(), item.period_ms,
                                item.repeat)])

    def _stop_one(self, uid: int) -> None:
        self._running.discard(uid)
        self.stop_tasks.emit([uid])
        self._refresh_send_state()

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
        self._bus_enabled = enabled
        if not enabled:
            self._running.clear()
        self._refresh_send_state()

    def _refresh_send_state(self) -> None:
        self.model.set_sending(bool(self._running))
        self.start_button.setText("停止" if self._running else "发送")
        self.start_button.setEnabled(self._bus_enabled)
        self.send_mode.setEnabled(not self._running)
        sequential = self.send_mode.currentIndex() == 1
        self.table.setColumnHidden(COL_PERIOD, sequential)
        self.table.setColumnHidden(COL_REPEAT, sequential)
        self.table.setColumnHidden(COL_SENT, True)
        self.loop_label.setVisible(sequential)
        self.loop_count.setVisible(sequential)
        self.loop_count.setEnabled(not self._running)
        for widget in (self.interval_label, self.interval_ms,
                       self.cycle_interval_label, self.cycle_interval_ms):
            widget.setVisible(sequential)
            widget.setEnabled(not self._running)
        self.table.setEnabled(True)
        if self._running:
            self.table.setFocus()
        for row in range(self.model.rowCount()):
            for column in self._editor_columns:
                editor = self.table.indexWidget(self.model.index(row, column))
                if editor is not None:
                    single = self.model.item(row).repeat == 1
                    editor.setEnabled((not sequential and not (column == COL_PERIOD and single))
                                      if column in (COL_REPEAT, COL_PERIOD) else True)
                    editor.setAttribute(Qt.WA_TransparentForMouseEvents, bool(self._running))
                    editor.setFocusPolicy(Qt.NoFocus if self._running else Qt.StrongFocus)
                    if isinstance(editor, QSpinBox):
                        editor.setReadOnly(bool(self._running))
                    if isinstance(editor, QComboBox) and editor.lineEdit() is not None:
                        editor.lineEdit().setReadOnly(bool(self._running))
        for button in self.findChildren(QPushButton):
            if button.text() in ("新增", "删除", "清空", "导入"):
                button.setEnabled(not self._running)
        self._update_toolbar_layout()

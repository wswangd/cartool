"""DBC 信号选择与接收报文实时测量。"""

from pathlib import Path
import math

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSpinBox, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..frame import RX
from .measurement_card import ElidedLabel, MeasurementCard


class MeasurementPanel(QWidget):
    message = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._messages = {}
        self._cards = {}
        self._card_order = []
        self._items = {}
        self._visible_keys = set()
        self._selected_messages = set()
        self._layout_keys = None
        self._pending = {}
        self._running = False
        self._connected = False
        self._last_dir = str(Path.home())
        self._errors = 0
        self.import_button = QPushButton("导入 DBC")
        self.encoding_combo = QComboBox()
        for label, encoding in (("自动编码", None), ("UTF-8", "utf-8-sig"),
                                ("中文 GB18030", "gb18030"), ("西文 CP1252", "cp1252")):
            self.encoding_combo.addItem(label, encoding)
        self.encoding_combo.setToolTip("单位或中文乱码时，选择文件实际编码后重新导入")
        self.start_button = QPushButton("开始测量")
        self.start_button.setEnabled(False)
        self.update_period = QSpinBox()
        self.update_period.setRange(5, 100000)
        self.update_period.setSingleStep(5)
        self.update_period.setValue(200)
        self.update_period.setSuffix(" ms")
        self.update_period.setMinimumWidth(125)
        self.update_period.setToolTip("控制数值及波形刷新周期，不改变 CAN 报文发送周期")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索报文、ID 或信号")
        self.search.setClearButtonEnabled(True)
        self.file_label = ElidedLabel("未导入 DBC")
        self.toolbar = QHBoxLayout()
        self.toolbar.addWidget(self.file_label, 1)
        for widget in (self.import_button, self.encoding_combo,
                       QLabel("更新周期"), self.update_period):
            self.toolbar.addWidget(widget)
        self.toolbar.addWidget(self.start_button)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["报文 / 信号"])
        self.tree.setMinimumWidth(0)
        self.selector_panel = QWidget()
        self.selector_panel.setMinimumWidth(0)
        selector_layout = QVBoxLayout(self.selector_panel)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(4)
        selector_layout.addWidget(self.search)
        selector_layout.addWidget(self.tree, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.viewport().installEventFilter(self)
        self.container = QWidget()
        columns = QHBoxLayout(self.container)
        columns.setContentsMargins(2, 2, 2, 2)
        columns.setSpacing(6)
        self.empty_label = QLabel("请导入 DBC，并在左侧勾选需要测量的信号")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color: #6B7280;")
        columns.addWidget(self.empty_label, 1)
        self._column_widgets = []
        self._columns = []
        for index in range(2):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(5)
            column_layout.addStretch(1)
            columns.addWidget(column, 1)
            self._column_widgets.append(column)
            self._columns.append(column_layout)
        self.scroll.setWidget(self.container)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.selector_panel)
        self.splitter.addWidget(self.scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([220, 880])
        self.status = QLabel("导入 DBC，勾选信号后开始测量；只解析接收的数据帧。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.toolbar)
        layout.addWidget(self.splitter, 1)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setInterval(self.update_period.value())
        self._timer.timeout.connect(self._flush)
        self.import_button.clicked.connect(self._import_file)
        self.start_button.clicked.connect(self._toggle)
        self.search.textChanged.connect(self._filter)
        self.tree.itemChanged.connect(self._filter)
        self.update_period.valueChanged.connect(self._update_interval)
        self.tree.currentItemChanged.connect(self._select_tree_signal)
        self._layout_cards()

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 DBC", self._last_dir, "DBC 文件 (*.dbc)")
        if not path:
            return
        try:
            self.load_dbc(path)
        except ImportError:
            self.message.emit("缺少 DBC 依赖，请运行 python -m pip install -r requirements.txt")
        except Exception as exc:
            self.message.emit(f"DBC 导入失败：{exc}")

    def load_dbc(self, path: str) -> None:
        import cantools

        source = Path(path).read_bytes()
        selected_encoding = self.encoding_combo.currentData()
        western_units = any(marker in source for marker in (b"\xb0C", b"\xb0F", b"\xb5s", b"\xb5A"))
        encodings = ([selected_encoding] if selected_encoding else
                     ["utf-8-sig"] + (["cp1252", "gb18030"] if western_units
                                       else ["gb18030", "cp1252"]))
        text = None
        for encoding in encodings:
            try:
                text = source.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("文件编码不匹配，请切换编码后重新导入")
        database = cantools.database.load_string(text, database_format="dbc", strict=False)
        messages = {}
        warnings = []
        for entry in database.messages:
            if entry.length > 8 or entry.is_fd:
                continue
            invalid = set()
            for signal in entry.signals:
                start = (signal.start if signal.byte_order == "little_endian" else
                         8 * (signal.start // 8) + 7 - signal.start % 8)
                if start < 0 or start + signal.length > entry.length * 8:
                    invalid.add(signal.name)
            while True:
                dependents = {signal.name for signal in entry.signals
                              if signal.multiplexer_signal in invalid}
                if dependents <= invalid:
                    break
                invalid.update(dependents)
            if invalid:
                warnings.extend(f"{entry.name}.{name}：超出报文长度或依赖无效复用信号"
                                for name in sorted(invalid))
                entry.signals[:] = [signal for signal in entry.signals if signal.name not in invalid]
            try:
                entry.refresh(strict=True)
            except cantools.database.Error as exc:
                warnings.append(f"{entry.name}：{exc}")
                continue
            if entry.signals:
                messages[entry.frame_id, entry.is_extended_frame] = entry
        if not messages:
            detail = "；".join(warnings[:3])
            raise ValueError(f"文件中没有可测量的经典 CAN 报文。{detail}")
        self.set_running(False)
        self._messages = messages
        for card in self._cards.values():
            for column in self._columns:
                column.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards.clear()
        self._card_order.clear()
        self._items.clear()
        self._visible_keys.clear()
        self._layout_keys = None
        self._errors = 0
        self.tree.blockSignals(True)
        self.tree.clear()
        for key, entry in messages.items():
            parent = QTreeWidgetItem(self.tree, [f"{entry.name} (0x{entry.frame_id:X})"])
            parent.setFlags(parent.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
            for signal in entry.signals:
                item = QTreeWidgetItem(parent, [signal.name])
                item.setCheckState(0, Qt.Unchecked)
                description = f"{signal.name}\n{entry.name} · 0x{entry.frame_id:X} · {signal.unit or ''}"
                card = MeasurementCard(signal.name, signal.unit or "", description, self.container)
                self._cards[key, signal.name] = card
                self._card_order.append((key, signal.name))
                card.swap_requested.connect(self._swap_cards)
                self._items[key, signal.name] = item
                item.setData(0, Qt.UserRole, (key, signal.name))
            parent.setExpanded(True)
        self.tree.blockSignals(False)
        self._last_dir = str(Path(path).parent)
        self.file_label.setText(Path(path).name)
        self.file_label.full_text = Path(path).name
        self.file_label.setToolTip(str(path))
        skipped = len(database.messages) - len(messages)
        self.status.setText(f"已加载 {len(messages)} 条报文、{len(self._cards)} 个信号；"
                            f"编码 {encoding}，跳过 {skipped} 条报文，{len(warnings)} 项定义警告。"
                            "请先勾选信号，拖拽信息行可交换位置。")
        self.status.setToolTip("\n".join(warnings))
        self.file_label.setToolTip(str(path) + "\n" + "\n".join(warnings))
        if warnings:
            self.message.emit(f"DBC 已部分导入，跳过不安全定义：{warnings[0]}。详情见文件名提示。")
        self.start_button.setEnabled(self._connected)
        self._filter()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.set_running(False)
        self.start_button.setEnabled(connected and bool(self._messages))

    def set_running(self, running: bool) -> None:
        self._running = running
        self._pending.clear()
        self.start_button.setText("停止测量" if running else "开始测量")
        self.import_button.setEnabled(not running)
        self.encoding_combo.setEnabled(not running)
        if running:
            for card in self._cards.values():
                card.clear()
            self._timer.start()
            self._update_interval(self.update_period.value())
        else:
            self._timer.stop()
            if self._messages:
                self.status.setText("测量已停止，保留最后一次测量值。")

    def _update_interval(self, period: int) -> None:
        self._timer.setInterval(period)
        if self._running:
            self.status.setText(f"测量中：每 {period}ms 刷新接收报文的最新值及波形。")

    def eventFilter(self, watched, event) -> bool:
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._layout_cards)
        return super().eventFilter(watched, event)

    def _layout_cards(self) -> None:
        visible = tuple(key for key in self._card_order if key in self._visible_keys)
        count = 2 if self.scroll.viewport().width() >= 700 else 1
        state = count, visible
        if state == self._layout_keys:
            return
        self._layout_keys = state
        for card in self._cards.values():
            for column in self._columns:
                column.removeWidget(card)
        self.empty_label.setVisible(not visible)
        self._column_widgets[0].setVisible(bool(visible))
        self._column_widgets[1].setVisible(bool(visible) and count == 2)
        for index, key in enumerate(visible):
            column = self._columns[index % count]
            column.insertWidget(column.count() - 1, self._cards[key])
            self._cards[key].show()

    def _swap_cards(self, source, target) -> None:
        keys = {id(card): key for key, card in self._cards.items()}
        source_key, target_key = keys.get(id(source)), keys.get(id(target))
        if (source_key is None or target_key is None or source is target
                or source.isHidden() or target.isHidden()):
            return
        source_index = self._card_order.index(source_key)
        target_index = self._card_order.index(target_key)
        self._card_order[source_index], self._card_order[target_index] = target_key, source_key
        self._layout_cards()

    def _select_tree_signal(self, item, previous) -> None:
        if item is not None:
            key = item.data(0, Qt.UserRole)
            card = self._cards.get(key)
            if card is not None and not card.isHidden():
                self.scroll.ensureWidgetVisible(card)
                card.expand_button.setFocus()

    def _toggle(self) -> None:
        if not self._running and not any(
                item.checkState(0) == Qt.Checked for item in self._items.values()):
            self.message.emit("请至少勾选一个测量信号")
            return
        self.set_running(not self._running)

    def add_records(self, records) -> None:
        if not self._running:
            return
        for record in records:
            frame = record.frame
            key = frame.frame_id, frame.extended
            if (record.direction == RX and not record.error and not frame.remote
                    and key in self._selected_messages):
                self._pending[key] = record

    def _flush(self) -> None:
        from cantools.database.errors import DecodeError

        pending, self._pending = self._pending, {}
        for key, record in pending.items():
            if key not in self._selected_messages:
                continue
            entry = self._messages[key]
            try:
                values = entry.decode(record.frame.payload, decode_choices=False)
            except (ValueError, DecodeError) as exc:
                self._errors += 1
                self.status.setText(f"解析失败 {self._errors} 次：{entry.name}：{exc}")
                continue
            for signal in entry.signals:
                card = self._cards[key, signal.name]
                if self._items[key, signal.name].checkState(0) != Qt.Checked:
                    continue
                value = values.get(signal.name)
                valid = value is not None and math.isfinite(value)
                text = format(value, ".10g") if valid else "—"
                card.set_value(text, valid)
                scope = card.scope
                if valid and scope is not None and not card.isHidden():
                    scope.append(record.wall_time, value)

    def _filter(self, *args) -> None:
        query = self.search.text().strip().casefold()
        self._visible_keys.clear()
        self._selected_messages = {key for (key, name), item in self._items.items()
                                   if item.checkState(0) == Qt.Checked}
        for key, entry in self._messages.items():
            any_match = False
            for signal in entry.signals:
                item = self._items[key, signal.name]
                matched = not query or query in f"{entry.name} {entry.frame_id:X} {signal.name}".casefold()
                item.setHidden(not matched)
                any_match = any_match or matched
                signal_key = key, signal.name
                hidden = not matched or item.checkState(0) != Qt.Checked
                if not hidden:
                    self._visible_keys.add(signal_key)
                self._cards[signal_key].setVisible(not hidden)
            if entry.signals:
                self._items[key, entry.signals[0].name].parent().setHidden(not any_match)
        self._layout_cards()

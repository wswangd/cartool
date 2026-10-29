"""DBC 信号选择与接收报文实时测量。"""

from pathlib import Path
import math
import time
from html import escape

from .widgets import OperationButton

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QGridLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .widgets import HoverSplitter
from ..frame import RX
from .measurement_card import ElidedLabel, MeasurementCard
from .merged_scope import MergedScope


class MeasurementPanel(QWidget):
    message = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._messages = {}
        self._cards = {}
        self._merge_groups = {}
        self._selected_cards = set()
        self._card_order = []
        self._items = {}
        self._visible_keys = set()
        self._selected_messages = set()
        self._layout_keys = None
        self._pending = {}
        self._running = False
        self._connected = False
        self._last_dir = str(Path.home())
        self._time_range = 300
        self._cursor_time = None
        self._scope_clock = time.time()
        self._errors = 0
        self.import_button = QPushButton("导入 DBC")
        self.start_button = OperationButton("开始测量")
        self.start_button.setEnabled(False)
        self.update_period = QSpinBox()
        self.update_period.setRange(5, 100000)
        self.update_period.setSingleStep(5)
        self.update_period.setValue(200)
        self.update_period.setSuffix(" ms")
        self.update_period.setMinimumWidth(125)
        self.update_period.setToolTip("控制数值及波形刷新周期，不改变 CAN 报文发送周期")
        self.search = QLineEdit()
        self.search.setContextMenuPolicy(Qt.NoContextMenu)
        self.search.setPlaceholderText("搜索报文、ID 或信号")
        self.search.setClearButtonEnabled(True)
        self.file_label = ElidedLabel("未导入 DBC")
        self.toolbar = QHBoxLayout()
        self.toolbar.addWidget(self.file_label, 1)
        for widget in (self.import_button,
                       QLabel("更新周期"), self.update_period):
            self.toolbar.addWidget(widget)
        self.toolbar.addWidget(self.start_button)
        self.tree = QTreeWidget()
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
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
        self.scroll.viewport().setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll.viewport().customContextMenuRequested.connect(
            lambda pos: self._card_context_menu(self.scroll.viewport().mapToGlobal(pos)))
        self.container = QWidget()
        self.container.setObjectName("measurementCardsContainer")
        self.container.setStyleSheet("QWidget#measurementCardsContainer { background-color: white; }")
        self.container.setContextMenuPolicy(Qt.CustomContextMenu)
        self.container.customContextMenuRequested.connect(
            lambda pos: self._card_context_menu(self.container.mapToGlobal(pos)))
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
        self.splitter = HoverSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.selector_panel)
        self.splitter.addWidget(self.scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([220, 880])
        self.status = QLabel(self)
        self.status.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.addLayout(self.toolbar)
        layout.addWidget(self.splitter, 1)
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
        western_units = any(marker in source for marker in (b"\xb0C", b"\xb0F", b"\xb5s", b"\xb5A"))
        encodings = ["utf-8-sig"] + (["cp1252", "gb18030"] if western_units
                                     else ["gb18030", "cp1252"])
        text = None
        for encoding in encodings:
            try:
                text = source.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别文件编码，请将文件转换为 UTF-8 后重新导入")
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
        self._selected_cards.clear()
        self._merge_groups.clear()
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
                card._time_max = self._time_range
                card._shared_time = self._scope_clock
                card.range_requested.connect(self._set_time_range)
                card.remove_requested.connect(
                    lambda signal_key=(key,signal.name): self._remove_measurement(signal_key))
                card.cursor_moved.connect(self._set_linked_cursor)
                card.selection_requested.connect(self._select_card)
                card.auto_size_requested.connect(lambda c=card: self._auto_size_selected(c))
                card.size_requested.connect(self._resize_selected_cards)
                card.merge_selection_requested.connect(self._merge_selected_cards)
                card.merge_requested.connect(self._confirm_merge)
                card.move_out_requested.connect(lambda member, owner=(key,signal.name): self._move_out_signal(owner,member))
                card.unmerge_requested.connect(lambda signal_key=(key,signal.name): self._unmerge(signal_key))
                card.insert_requested.connect(self._insert_cards)
                card.resized.connect(self._relayout_cards)
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

    def _tree_context_menu(self, position) -> None:
        menu = QMenu(self.tree)
        menu.addAction("全部收起", self.tree.collapseAll)
        menu.addAction("全部展开", self.tree.expandAll)
        menu.addSeparator()
        menu.addAction("全选", lambda: self._check_all_signals(True))
        menu.addAction("全不选", lambda: self._check_all_signals(False))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _card_context_menu(self, position) -> None:
        child = self.container.childAt(self.container.mapFromGlobal(position))
        if child is not None and any(card is child or card.isAncestorOf(child)
                                     for card in self._cards.values()):
            return
        menu = QMenu(self)
        menu.addAction("恢复默认缩放", self._auto_size_cards)
        menu.addAction("收起波形图", self._collapse_waveforms)
        menu.exec(position)

    def _auto_size_cards(self) -> None:
        for card in self._cards.values():
            self._auto_size_card(card,relayout=False)
        self._relayout_cards()

    def _resize_selected_cards(self, source, size):
        from PySide6.QtCore import QSize
        selected = [self._cards[key] for key in self._selected_cards
                    if key in self._cards and not self._cards[key].isHidden()]
        if source not in selected or len(selected)<2:
            return
        sx,sy = size.width()/max(1,source.width()),size.height()/max(1,source.height())
        maximum = max(220,self.scroll.viewport().width()-12)
        for card in selected:
            if card is source:
                continue
            minimum_height = 160 if card.scope else 36
            card._custom_sizes[card.scope is not None] = QSize(
                max(220,min(maximum,round(card.width()*sx))),
                max(minimum_height,min(2000,round(card.height()*sy))))

    def _auto_size_selected(self, source):
        selected = [self._cards[key] for key in self._selected_cards
                    if key in self._cards and not self._cards[key].isHidden()]
        targets = selected if source in selected else [source]
        for card in targets:
            self._auto_size_card(card, relayout=False)
        self._relayout_cards()

    def _auto_size_card(self, card, relayout=True) -> None:
        card._custom_sizes.clear()
        card._text_scale = 1.0
        if card._merged_plot is not None:
            from PySide6.QtCore import QSize
            lanes = len({member.unit for member in card._merged_plot.cards})
            card._custom_sizes[True] = QSize(min(350,max(220,self.scroll.viewport().width()-12)),100+160*(1 if lanes == 2 else lanes))
        if relayout:
            self._relayout_cards()

    def _collapse_waveforms(self) -> None:
        for card in self._cards.values():
            card.expand_button.setChecked(False)
        self._relayout_cards()

    def _check_all_signals(self, checked: bool) -> None:
        self.tree.blockSignals(True)
        try:
            state = Qt.Checked if checked else Qt.Unchecked
            for item in self._items.values():
                item.setCheckState(0, state)
        finally:
            self.tree.blockSignals(False)
        self._filter()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._layout_cards)
        return super().eventFilter(watched, event)

    def _relayout_cards(self):
        self._layout_keys = None
        self._layout_cards()

    def _layout_cards(self) -> None:
        members = {key for owner,group in self._merge_groups.items() for key in group if key != owner}
        visible = tuple(key for key in self._card_order if key not in members and
                        (key in self._visible_keys or any(k in self._visible_keys for k in self._merge_groups.get(key,[]))))
        for key in members:
            self._cards[key].hide()
        count = max(1, (self.scroll.viewport().width()-7)//355)
        self._layout_keys = count, visible
        self.container.layout().setEnabled(False)
        for widget in self._column_widgets:
            widget.hide()
        width = max(220,self.scroll.viewport().width()-12)
        default_w = min(350,width)
        placed = []
        self.empty_label.setVisible(not visible)
        self.empty_label.setGeometry(0,0,width,80)
        for key in visible:
            card = self._cards[key]
            for column in self._columns:
                column.removeWidget(card)
            if card.parentWidget() is not self.container:
                card.setParent(self.container)
            size = card._custom_sizes.get(card.scope is not None)
            w = min(width,size.width()) if size else default_w
            h = size.height() if size else (160 if card.scope else 36)
            if card._merged_plot is not None and not card.expand_button.isChecked():
                h = 40+card._merged_legend.sizeHint().height()
            elif card.scope is not None:
                h = max(h, card.minimum_expanded_height())
            xs = sorted({0} | {rect.right()+6 for rect in placed})
            ys = sorted({0} | {rect.bottom()+6 for rect in placed})
            target = None
            for y in ys:
                for x in xs:
                    if x+w > width:
                        continue
                    candidate = QRect(x,y,w,h)
                    if not any(candidate.intersects(rect.adjusted(-5,-5,5,5)) for rect in placed):
                        target = candidate
                        break
                if target is not None:
                    break
            if target is None:
                target = QRect(0,max((rect.bottom()+6 for rect in placed),default=0),w,h)
            card.setFixedSize(w,h)
            card.move(target.topLeft())
            card.show()
            card._layout.activate()
            placed.append(target)
        self.container.setMinimumHeight(max(80,max((rect.bottom()+1 for rect in placed),default=0)))
        self._refresh_card_selection()

    def _remove_measurement(self, key):
        keys = list(self._merge_groups.get(key,[key]))
        self.tree.blockSignals(True)
        for signal_key in keys:
            self._items[signal_key].setCheckState(0,Qt.Unchecked)
        self.tree.blockSignals(False)
        self._filter()

    def _refresh_group_legend(self, card):
        for member, label in card._merged_values:
            label.setText(member.value_label.text())
        card._apply_text_style()

    def _select_card(self, card, additive):
        lookup = {id(c):key for key,c in self._cards.items()}
        key = lookup.get(id(card))
        if key is None:
            return
        if additive:
            if key in self._selected_cards:
                self._selected_cards.remove(key)
            else:
                self._selected_cards.add(key)
        else:
            self._selected_cards = {key}
        self._refresh_card_selection()

    def _refresh_card_selection(self):
        self._selected_cards = {key for key in self._selected_cards
                                if key in self._cards and not self._cards[key].isHidden()}
        for key,card in self._cards.items():
            card._multi_selected = key in self._selected_cards
            card._can_merge_selection = card._multi_selected and len(self._selected_cards)>1
            card._refresh_selection_border(card._multi_selected)

    def _merge_selected_cards(self):
        keys = [key for key in self._card_order if key in self._selected_cards and not self._cards[key].isHidden()]
        if len(keys)<2:
            return
        answer = QMessageBox.question(self,"合并信号","是否合并显示？",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        target = keys[0]
        for key in keys[1:]:
            self._merge_signals(key,target)
        self._selected_cards = {target}
        self._refresh_card_selection()

    def _confirm_merge(self, source, target):
        lookup = {id(card):key for key,card in self._cards.items()}
        source_key,target_key = lookup.get(id(source)),lookup.get(id(target))
        if source_key is None or target_key is None or source is target:
            return
        answer = QMessageBox.question(self,"合并信号","是否合并显示？",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer == QMessageBox.Yes:
            self._merge_signals(source_key,target_key)

    def _merge_signals(self, source_key, target_key):
        keys = list(dict.fromkeys(self._merge_groups.get(target_key,[target_key])+self._merge_groups.get(source_key,[source_key])))
        for owner in (source_key,target_key):
            if owner in self._merge_groups:
                self._unmerge(owner,relayout=False)
        for key in keys:
            self._cards[key].expand_button.setChecked(True)
        target = self._cards[target_key]
        target._premerge_sizes = dict(target._custom_sizes)
        self._merge_groups[target_key] = keys
        target._merged_plot = MergedScope([self._cards[key] for key in keys],target)
        target._merged_plot._view_time = self._scope_clock
        target._merged_plot.set_window_seconds(self._time_range)
        target._merged_plot.time_range_changed.connect(self._set_time_range)
        target._merged_plot.cursor_moved.connect(self._set_linked_cursor)
        target._merged_plot.set_linked_cursor(self._cursor_time)
        target._merged_legend = QWidget(target)
        target._merged_legend.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        rows = QVBoxLayout(target._merged_legend)
        rows.setContentsMargins(0,0,0,0)
        rows.setSpacing(4)
        target._merged_values = []
        target._wave_checks = []
        primary_check = QCheckBox()
        primary_check.setChecked(True)
        primary_check.setToolTip("显示/隐藏此信号波形")
        primary_check.toggled.connect(lambda visible,c=target: self._set_group_wave_visible(c,c,visible))
        target._header.insertWidget(0,primary_check)
        target._primary_wave_check = primary_check
        target._wave_checks.append(primary_check)
        for row,key in enumerate(keys[1:]):
            member = self._cards[key]
            color = MergedScope.COLORS[(row+1)%len(MergedScope.COLORS)]
            name = ElidedLabel(member.name)
            name.setMinimumWidth(40)
            name.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Preferred)
            name.setToolTip(member.name)
            unit = QLabel(member.unit)
            value = QLabel(member.value_label.text())
            value.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
            value.setMinimumWidth(40)
            unit.setSizePolicy(QSizePolicy.Fixed,QSizePolicy.Preferred)
            for label in (name,unit,value):
                label.setTextFormat(Qt.PlainText)
                label.setStyleSheet(f"color:{color}; font-size:9pt;")
            unit.setStyleSheet("color:#6B7280; font-size:9pt;")
            for label in (name,value):
                font = label.font()
                font.setBold(True)
                label.setFont(font)
                label.setStyleSheet(f"color:{color}; font-size:9pt; font-weight:bold;")
            data_row = QWidget()
            data_row.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
            row_layout = QHBoxLayout(data_row)
            row_layout.setContentsMargins(0,0,0,0)
            row_layout.setSpacing(6)
            identity = QHBoxLayout()
            identity.setSpacing(6)
            identity.addWidget(name)
            identity.addWidget(unit)
            identity.addStretch(1)
            check = QCheckBox()
            check.setChecked(True)
            check.setToolTip("显示/隐藏此信号波形")
            check.toggled.connect(lambda visible,c=target,m=member: self._set_group_wave_visible(c,m,visible))
            row_layout.addWidget(check)
            target._wave_checks.append(check)
            row_layout.addLayout(identity,1)
            row_layout.addWidget(value)
            spacer = QWidget()
            spacer.setFixedSize(target.expand_button.sizeHint())
            row_layout.addWidget(spacer)
            rows.addWidget(data_row)
            target._merged_values.append((member,value))
        target._layout.addWidget(target._merged_legend)
        target._merged_plot.setMinimumSize(0, 0)
        target._merged_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        target._layout.addWidget(target._merged_plot)
        target.scope.hide()
        target.name_label.full_text = target.name
        target.name_label.update()
        self._refresh_group_legend(target)
        from PySide6.QtCore import QSize
        target._custom_sizes[True] = QSize(max(500,target.width()),100+160*(1 if len({self._cards[key].unit for key in keys}) == 2 else len({self._cards[key].unit for key in keys})))
        self._layout_cards()

    def _set_group_wave_visible(self, owner, member, visible):
        plot = owner._merged_plot
        if plot is None:
            return
        if visible:
            plot.hidden_cards.discard(id(member))
        else:
            plot.hidden_cards.add(id(member))
        plot.update()

    def _move_out_signal(self, owner, member):
        lookup = {id(card):key for key,card in self._cards.items()}
        removed = lookup.get(id(member))
        keys = list(self._merge_groups.get(owner,[]))
        if removed not in keys:
            return
        remaining = [key for key in keys if key != removed]
        self._unmerge(owner,relayout=False)
        if len(remaining)>1:
            for key in remaining[1:]:
                self._merge_signals(key,remaining[0])
        self._filter()

    def _unmerge(self, owner, relayout=True):
        keys = self._merge_groups.pop(owner,None)
        if keys is None:
            return
        card = self._cards[owner]
        for widget in (card._merged_plot,card._merged_legend):
            card._layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        card._header.removeWidget(card._primary_wave_check)
        card._primary_wave_check.hide()
        card._primary_wave_check.deleteLater()
        card._merged_plot = None
        card._custom_sizes = dict(getattr(card,"_premerge_sizes",{}))
        card._apply_text_style()
        card.name_label.setMinimumWidth(40)
        card.name_label.setMaximumWidth(16777215)
        card.name_label.full_text = card.name
        card.name_label.update()
        if card.scope is not None:
            card.scope.show()
        if relayout:
            self._filter()

    def _insert_cards(self, source, target, before):
        keys = {id(card): key for key,card in self._cards.items()}
        source_key,target_key = keys.get(id(source)),keys.get(id(target))
        if source_key is None or target_key is None or source is target:
            return
        visible = [key for key in self._card_order if key in self._visible_keys]
        if source_key not in visible or target_key not in visible:
            return
        slots = [i for i,key in enumerate(self._card_order) if key in visible]
        visible.remove(source_key)
        visible.insert(visible.index(target_key)+(0 if before else 1),source_key)
        for i,key in zip(slots,visible):
            self._card_order[i] = key
        self._layout_cards()

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

    def _set_linked_cursor(self, timestamp):
        self._cursor_time = timestamp
        for card in self._cards.values():
            card._linked_cursor = timestamp
            if card.scope is not None:
                card.scope.set_linked_cursor(timestamp)
            if card._merged_plot is not None:
                card._merged_plot.set_linked_cursor(timestamp)

    def _set_time_range(self, seconds):
        self._time_range = seconds
        for card in self._cards.values():
            card._time_max = seconds
            if card.scope is not None:
                card.scope.set_window_seconds(seconds)
            if card._merged_plot is not None:
                card._merged_plot.set_window_seconds(seconds)

    def _flush(self) -> None:
        group_owners = {key:self._cards[owner] for owner,keys in self._merge_groups.items() for key in keys}
        self._scope_clock = time.time()
        for card in self._cards.values():
            card._shared_time = self._scope_clock
            if card.scope is not None:
                card.scope._view_time = self._scope_clock
                if not card.scope.isHidden() and not card.isHidden():
                    card.scope.update()
            if card._merged_plot is not None:
                card._merged_plot._view_time = self._scope_clock
                card._merged_plot.WINDOW_SECONDS = self._time_range
                if card.expand_button.isChecked() and not card.isHidden():
                    card._merged_plot.update()
                self._refresh_group_legend(card)
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
                owner = group_owners.get((key,signal.name))
                sampling = (owner.expand_button.isChecked() and not owner.isHidden()) if owner is not None else not card.isHidden()
                if valid and scope is not None and sampling:
                    scope.append(record.wall_time, value)

        for card in self._cards.values():
            if card._merged_plot is not None:
                self._refresh_group_legend(card)

    def _filter(self, *args) -> None:
        for owner,group in list(self._merge_groups.items()):
            if any(self._items[key].checkState(0) != Qt.Checked for key in group):
                self._unmerge(owner, relayout=False)
        for key,card in self._cards.items():
            if self._items[key].checkState(0) != Qt.Checked and (card.scope is not None or card._custom_sizes):
                card.expand_button.setChecked(False)
                card._custom_sizes.clear()
                card._text_scale = 1.0
                card.clear()
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

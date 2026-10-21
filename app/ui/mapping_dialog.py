"""信号映射配置对话框：编辑 signals.json 里的信号定义与报文映射。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHeaderView, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import signals
from ..signals import SignalDef, SignalState

HEADERS = ("名称", "单位", "最小", "最大", "默认", "启用", "CAN ID(hex)", "扩展帧",
           "起始bit", "长度(bit)", "字节顺序", "分辨率", "偏移", "类型", "状态(名=原始值)")
CYCLE_HEADERS = ("CAN ID(hex)", "发送周期(ms)")
DECIMAL_PLACES = 4          # 物理值/分辨率/偏移统一精确到小数点后 4 位
MIN_DECIMAL_STEP = 0.0001   # 最小步进 1e-4，保证可精确输入到第 4 位小数


class NoWheelSpinBox(QSpinBox):
    """整数数值框：完全禁用滚轮，防止误触修改。

    只能通过键盘输入或点击编辑来修改数值。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 移除上下按钮，只支持手动输入
        self.setButtonSymbols(QSpinBox.NoButtons)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """4 位小数值框：完全禁用滚轮，防止误触修改。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 移除上下按钮，只支持手动输入
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelComboBox(QComboBox):
    """下拉菜单：完全禁用滚轮，防止误触修改。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


def make_decimal_spin(value: float, low: float = -1e9,
                      high: float = 1e9) -> NoWheelDoubleSpinBox:
    """构建统一的 4 位小数数值输入框。

    - setDecimals(4)：界面固定显示 4 位小数（如 1.0000、-40.0000），格式统一；
    - AdaptiveDecimalStepType：点击上下箭头时按光标所在位步进，最细到 0.0001；
    - 键盘输入超过 4 位小数时自动四舍五入到第 4 位，保证精度口径一致；
    - 完全禁用滚轮，防止误触修改。
    """
    spin = NoWheelDoubleSpinBox()
    spin.setDecimals(DECIMAL_PLACES)
    spin.setRange(low, high)
    spin.setSingleStep(MIN_DECIMAL_STEP)
    spin.setStepType(QDoubleSpinBox.AdaptiveDecimalStepType)
    spin.setValue(float(value))
    return spin


def make_int_spin(value: int, low: int, high: int) -> NoWheelSpinBox:
    """构建整数数值框；未聚焦时滚轮不改值。"""
    spin = NoWheelSpinBox()
    spin.setRange(low, high)
    spin.setValue(int(value))
    return spin


class MappingDialog(QDialog):
    def __init__(self, defs: list[SignalDef],
                 cycles: dict[int, int] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("信号映射配置")
        self.resize(1150, 620)
        self._defs = [SignalDef(name=d.name, unit=d.unit, minimum=d.minimum,
                                maximum=d.maximum, default=d.default,
                                enabled=d.enabled, can_id=d.can_id,
                                extended=d.extended, start_bit=d.start_bit,
                                bit_length=d.bit_length, byte_order=d.byte_order,
                                factor=d.factor, offset=d.offset, editor=d.editor,
                                states=list(d.states)) for d in defs]
        self._cycles = dict(cycles) if cycles else {}

        # --- 信号表（名称列锁定 + 数据列可滚动） ---
        DATA_HEADERS = HEADERS[1:]  # 去掉"名称"

        bold_font = QFont()
        bold_font.setBold(True)

        self.name_table = QTableWidget(len(self._defs), 1)
        self.name_table.setHorizontalHeaderLabels(["名称"])
        self.name_table.verticalHeader().setVisible(False)
        self.name_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self.name_table.horizontalHeader().setFont(bold_font)
        self.name_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 垂直滚动条保留功能但隐藏外观，用于接收同步信号
        self.name_table.verticalScrollBar().setStyleSheet(
            "QScrollBar { width: 0px; }")
        self.name_table.setEditTriggers(QAbstractItemView.AllEditTriggers)

        self.data_table = QTableWidget(len(self._defs), len(DATA_HEADERS))
        self.data_table.setHorizontalHeaderLabels(DATA_HEADERS)
        self.data_table.verticalHeader().setVisible(False)
        self.data_table.horizontalHeader().setFont(bold_font)
        self.data_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.data_table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        for row, sig in enumerate(self._defs):
            self._fill_row(row, sig)
        self.data_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.data_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.name_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        # 让名称表宽度精确匹配列宽
        self.name_table.resizeColumnsToContents()
        col_width = self.name_table.columnWidth(0)
        self.name_table.setMaximumWidth(col_width + 4)

        # 同步行高，确保两表行对齐
        for row in range(self.data_table.rowCount()):
            self.name_table.setRowHeight(row, self.data_table.rowHeight(row))

        # 双向滚动同步，用标志位防止死循环
        self._syncing_scroll = False

        def _data_to_name(val: int) -> None:
            if self._syncing_scroll:
                return
            self._syncing_scroll = True
            self.name_table.verticalScrollBar().setValue(val)
            self._syncing_scroll = False

        def _name_to_data(val: int) -> None:
            if self._syncing_scroll:
                return
            self._syncing_scroll = True
            self.data_table.verticalScrollBar().setValue(val)
            self._syncing_scroll = False

        self.data_table.verticalScrollBar().valueChanged.connect(_data_to_name)
        self.name_table.verticalScrollBar().valueChanged.connect(_name_to_data)

        # 用零间距容器包裹两个表，消除间隔
        # 名称表底部添加占位，高度等于数据表水平滚动条高度，保证两表可视区域等高
        name_wrapper = QWidget()
        name_layout = QVBoxLayout(name_wrapper)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(0)
        name_layout.addWidget(self.name_table, 1)
        sb_height = self.data_table.horizontalScrollBar().height()
        spacer = QWidget()
        spacer.setFixedHeight(sb_height)
        name_layout.addWidget(spacer)

        tables_widget = QWidget()
        tables_layout = QHBoxLayout(tables_widget)
        tables_layout.setContentsMargins(0, 0, 0, 0)
        tables_layout.setSpacing(0)
        tables_layout.addWidget(name_wrapper)
        tables_layout.addWidget(self.data_table, 1)

        hint = QLabel('物理值 = 原始值 × 分辨率 + 偏移；最小/最大/默认/分辨率/偏移均精确到'
                      '小数点后 4 位；状态列不写原始值表示该状态仍用滑杆值；状态支持字节覆盖'
                      '语法：名称@bN=V 表示选中该状态时强制 byte N 写入值 V（如 内部故障@b5=3）。'
                      '起始bit 范围 0-63。数值框已禁用滚轮，需点击选中后用键盘或上下箭头修改；'
                      '回车不退出，完成后请点击\u201c确定\u201d保存，ESC 取消。')
        hint.setWordWrap(True)

        # --- 报文周期表 ---
        self.cycle_table = QTableWidget(0, len(CYCLE_HEADERS))
        self.cycle_table.setHorizontalHeaderLabels(CYCLE_HEADERS)
        self.cycle_table.verticalHeader().setVisible(False)
        self.cycle_table.horizontalHeader().setFont(bold_font)
        self.cycle_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._refresh_cycle_table()

        cycle_label = QLabel("报文发送周期（按 CAN ID 设置，未列出的 ID 用默认 100ms）")

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        self.add_btn = QPushButton("添加信号")
        self.del_btn = QPushButton("删除信号")
        self.up_btn = QPushButton("上移")
        self.down_btn = QPushButton("下移")
        self.import_btn = QPushButton("导入JSON")
        self.export_btn = QPushButton("导出JSON")
        self.add_btn.clicked.connect(self._on_add_signal)
        self.del_btn.clicked.connect(self._on_delete_signal)
        self.up_btn.clicked.connect(lambda: self._on_move_row(-1))
        self.down_btn.clicked.connect(lambda: self._on_move_row(1))
        self.import_btn.clicked.connect(self._on_import_json)
        self.export_btn.clicked.connect(self._on_export_json)
        toolbar.addWidget(self.add_btn)
        toolbar.addWidget(self.del_btn)
        toolbar.addWidget(self.up_btn)
        toolbar.addWidget(self.down_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.import_btn)
        toolbar.addWidget(self.export_btn)

        # --- 按钮 ---
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.ok_btn = QPushButton("确定")
        cancel = QPushButton("取消")
        self.ok_btn.clicked.connect(self._on_accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.ok_btn)
        buttons.addWidget(cancel)

        # 所有按钮都禁止成为对话框默认按钮：表格输入框中按回车不会触发任何按钮
        # （不会误添加信号，也不会直接退出配置界面）；保存请点击"确定"，ESC 取消。
        for btn in (self.add_btn, self.del_btn, self.up_btn, self.down_btn,
                    self.import_btn, self.export_btn, self.ok_btn, cancel):
            btn.setAutoDefault(False)
            btn.setDefault(False)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(tables_widget, 1)
        layout.addWidget(hint)
        layout.addWidget(cycle_label)
        layout.addWidget(self.cycle_table)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ 填充

    def _fill_row(self, row: int, sig: SignalDef) -> None:
        set_name = self.name_table.setCellWidget
        set_cell = self.data_table.setCellWidget
        set_name(row, 0, QLineEdit(sig.name))
        set_cell(row, 0, QLineEdit(sig.unit))

        set_cell(row, 1, make_decimal_spin(sig.minimum))
        set_cell(row, 2, make_decimal_spin(sig.maximum))
        set_cell(row, 3, make_decimal_spin(sig.default))

        check = QTableWidgetItem()
        check.setFlags(check.flags() | Qt.ItemIsUserCheckable)
        check.setCheckState(Qt.Checked if sig.enabled else Qt.Unchecked)
        self.data_table.setItem(row, 4, check)

        can_id_edit = QLineEdit(f"{sig.can_id:08X}")
        can_id_edit.setPlaceholderText("如 1A2")
        can_id_edit._last_can_id = sig.can_id  # 记录上次生效的 CAN ID
        can_id_edit.editingFinished.connect(
            lambda e=can_id_edit, r=row: self._on_can_id_edited(e, r))
        set_cell(row, 5, can_id_edit)

        ext = QTableWidgetItem()
        ext.setFlags(ext.flags() | Qt.ItemIsUserCheckable)
        ext.setCheckState(Qt.Checked if sig.extended else Qt.Unchecked)
        self.data_table.setItem(row, 6, ext)

        set_cell(row, 7, make_int_spin(sig.start_bit, 0, 63))
        set_cell(row, 8, make_int_spin(sig.bit_length,
                                       signals.MIN_BIT_LENGTH,
                                       signals.MAX_BIT_LENGTH))

        combo_order = NoWheelComboBox()
        combo_order.addItems(signals.BYTE_ORDERS)
        combo_order.setCurrentText(sig.byte_order)
        set_cell(row, 9, combo_order)

        set_cell(row, 10, make_decimal_spin(sig.factor, -100000, 100000))
        set_cell(row, 11, make_decimal_spin(sig.offset, -1000000, 1000000))

        combo_editor = NoWheelComboBox()
        combo_editor.addItems(["数值", "状态"])
        combo_editor.setCurrentIndex(0 if sig.editor == "value" else 1)
        set_cell(row, 12, combo_editor)

        # 构建状态字符串，包含 byte_overrides
        state_parts = []
        for s in sig.states:
            part = s.name
            if s.raw is not None:
                part += f"={s.raw}"
            if s.byte_overrides:
                overrides = ",".join(f"b{b}=0x{v:X}" for b, v in s.byte_overrides)
                part += f"@{overrides}"
            state_parts.append(part)
        states = ";".join(state_parts)
        set_cell(row, 13, QLineEdit(states))

    def _widget(self, row: int, col: int):
        if col == 0:
            return self.name_table.cellWidget(row, 0)
        return self.data_table.cellWidget(row, col - 1)

    # ------------------------------------------------------------------ 周期表

    def _on_can_id_edited(self, edit: QLineEdit, row: int) -> None:
        """CAN ID 输入框编辑完成后：小写自动转大写，并同步更新周期表。"""
        text = edit.text().strip()
        old_id: int | None = getattr(edit, "_last_can_id", None)

        if not text:
            if old_id is not None:
                edit.setText(f"{old_id:08X}")
            return

        # 小写自动转大写
        upper = text.upper()
        if upper != text:
            edit.setText(upper)

        # 校验十六进制格式
        try:
            new_id = int(upper, 16)
        except ValueError:
            QMessageBox.warning(
                self, "CAN ID 无效",
                f"\u201c{upper}\u201d 不是有效的十六进制数")
            if old_id is not None:
                edit.setText(f"{old_id:08X}")
            return

        # 检查是否与其他行重复
        for r in range(self.data_table.rowCount()):
            if r == row:
                continue
            other_text = self._widget(r, 6).text().strip()
            if other_text:
                try:
                    if int(other_text, 16) == new_id:
                        QMessageBox.warning(
                            self, "CAN ID 重复",
                            f"ID \u201c{new_id:08X}\u201d 已被第 {r + 1} 行使用")
                        edit.setText(f"{old_id:08X}" if old_id is not None
                                     else "")
                        return
                except ValueError:
                    continue

        # 将旧 ID 的周期迁移到新 ID
        if old_id is not None and old_id != new_id:
            if old_id in self._cycles:
                self._cycles[new_id] = self._cycles.pop(old_id)
        edit._last_can_id = new_id
        self._refresh_cycle_table()

    def _refresh_cycle_table(self) -> None:
        """从信号表收集所有 CAN ID，更新周期表。保留已设周期。"""
        can_ids: set[int] = set()
        for row in range(self.data_table.rowCount()):
            try:
                can_id_text = self._widget(row, 6).text().strip()
                if can_id_text:
                    can_ids.add(int(can_id_text, 16))
            except (ValueError, AttributeError):
                continue
        self.cycle_table.setRowCount(0)
        for can_id in sorted(can_ids):
            row = self.cycle_table.rowCount()
            self.cycle_table.insertRow(row)
            id_edit = QLineEdit(f"{can_id:08X}")
            id_edit.setReadOnly(True)
            self.cycle_table.setCellWidget(row, 0, id_edit)
            spin_cycle = make_int_spin(self._cycles.get(can_id, 100), 10, 100000)
            spin_cycle.setSuffix(" ms")
            self.cycle_table.setCellWidget(row, 1, spin_cycle)

    def _collect_cycles(self) -> dict[int, int]:
        """从周期表收集 CAN ID → 周期映射。"""
        result: dict[int, int] = {}
        for row in range(self.cycle_table.rowCount()):
            id_edit = self.cycle_table.cellWidget(row, 0)
            spin = self.cycle_table.cellWidget(row, 1)
            if id_edit and spin:
                try:
                    can_id = int(id_edit.text(), 16)
                    result[can_id] = spin.value()
                except ValueError:
                    continue
        return result

    # ------------------------------------------------------------------ 增删移

    def _on_add_signal(self) -> None:
        row = self.data_table.rowCount()
        self.name_table.insertRow(row)
        self.data_table.insertRow(row)
        sig = SignalDef(name=f"信号{row + 1}", unit="", minimum=0, maximum=100,
                       default=0, enabled=False, can_id=0x100, start_bit=0,
                       bit_length=16, editor="value")
        self._fill_row(row, sig)
        self._refresh_cycle_table()
        # 焦点移到新行名称输入框，避免焦点停留在按钮上时按回车重复添加
        name_edit = self.name_table.cellWidget(row, 0)
        if name_edit is not None:
            name_edit.setFocus()
            name_edit.selectAll()

    def _on_delete_signal(self) -> None:
        rows = {idx.row() for idx in self.data_table.selectedIndexes()}
        if not rows:
            QMessageBox.warning(self, "删除信号", "请先选中要删除的行")
            return
        if self.data_table.rowCount() <= len(rows):
            QMessageBox.warning(self, "删除信号", "至少需要保留一路信号")
            return
        for row in sorted(rows, reverse=True):
            self.name_table.removeRow(row)
            self.data_table.removeRow(row)
        self._refresh_cycle_table()
        self.data_table.setFocus()

    def _on_move_row(self, direction: int) -> None:
        """direction=-1 上移，+1 下移。支持连续多行整体移动。"""
        rows = sorted({idx.row() for idx in self.data_table.selectedIndexes()})
        if not rows:
            QMessageBox.warning(self, "移动信号", "请先选中要移动的行")
            return
        if not all(rows[i + 1] - rows[i] == 1 for i in range(len(rows) - 1)):
            QMessageBox.warning(self, "移动信号", "请选中连续的多行")
            return
        target = rows[0] + direction
        if direction < 0 and target < 0:
            return  # 已在顶部
        if direction > 0 and rows[-1] + 1 >= self.data_table.rowCount():
            return  # 已在底部
        defs_to_move = [self._collect_row_def(r) for r in rows]
        for r in sorted(rows, reverse=True):
            self.name_table.removeRow(r)
            self.data_table.removeRow(r)
        for i, sig in enumerate(defs_to_move):
            self.name_table.insertRow(target + i)
            self.data_table.insertRow(target + i)
            self._fill_row(target + i, sig)
        for i in range(len(defs_to_move)):
            self.data_table.selectRow(target + i)
        self._refresh_cycle_table()

    # ------------------------------------------------------------------ 导入导出

    def _on_import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入信号配置", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            QMessageBox.warning(self, "导入失败", f"解析 JSON 失败：{exc}")
            return
        items = payload.get("signals") if isinstance(payload, dict) else payload
        if not isinstance(items, list) or not items:
            QMessageBox.warning(self, "导入失败", "JSON 中找不到 signals 数组")
            return
        try:
            defs = [SignalDef.from_dict(raw) for raw in items if isinstance(raw, dict)]
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", f"信号定义无效：{exc}")
            return
        self.name_table.setRowCount(0)
        self.data_table.setRowCount(0)
        for row, sig in enumerate(defs):
            self.name_table.insertRow(row)
            self.data_table.insertRow(row)
            self._fill_row(row, sig)
        raw_cycles = payload.get("cycles", {}) if isinstance(payload, dict) else {}
        self._cycles = {}
        for key, value in raw_cycles.items():
            try:
                self._cycles[int(str(key), 16)] = int(value)
            except (TypeError, ValueError):
                continue
        self._refresh_cycle_table()
        QMessageBox.information(self, "导入成功", f"已导入 {len(defs)} 路信号")

    def _on_export_json(self) -> None:
        defs = self._collect_defs()
        if defs is None:
            return
        cycles = self._collect_cycles()
        path, _ = QFileDialog.getSaveFileName(
            self, "导出信号配置", "signals.json", "JSON 文件 (*.json)")
        if not path:
            return
        payload = {"version": 1,
                   "note": "信号与报文映射配置：can_id/extended/start_bit/bit_length/"
                           "byte_order/factor/offset 决定物理值如何装进报文",
                   "signals": [sig.to_dict() for sig in defs],
                   "cycles": {f"{k:08X}": v for k, v in cycles.items()}}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"写入文件失败：{exc}")
            return
        QMessageBox.information(self, "导出成功", f"已导出 {len(defs)} 路信号到\n{path}")

    # ------------------------------------------------------------------ 解析

    @staticmethod
    def _parse_states(text: str) -> list[SignalState]:
        """解析状态字符串，支持字节覆盖语法。

        语法：
          名称=原始值                    # 传统格式
          名称@bN=V                      # 字节覆盖（bN 表示 byte N，V 为值）
          名称=原始值@bN=V               # 组合：原始值 + 字节覆盖
          名称@bN=V,bM=W                 # 多字节覆盖
        示例：
          正常;内部故障@b5=3;开路@b5=5
        """
        states = []
        for token in text.replace("；", ";").split(";"):
            token = token.strip()
            if not token:
                continue
            # 分离字节覆盖部分
            name_part, _, override_part = token.partition("@")
            name_part = name_part.strip()
            override_part = override_part.strip()
            # 解析原始值
            name, _, raw = name_part.partition("=")
            name = name.strip()
            raw = raw.strip()
            value = None
            if raw:
                value = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
            # 解析字节覆盖
            byte_overrides = []
            if override_part:
                for item in override_part.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    # 格式：bN=V 或 bN=0xV
                    if item.lower().startswith("b"):
                        item = item[1:]  # 去掉 'b' 前缀
                    byte_str, _, val_str = item.partition("=")
                    byte_str = byte_str.strip()
                    val_str = val_str.strip()
                    if byte_str and val_str:
                        try:
                            byte_offset = int(byte_str)
                            byte_value = int(val_str, 16) if val_str.lower().startswith("0x") else int(val_str)
                            byte_overrides.append((byte_offset, byte_value))
                        except (ValueError, TypeError):
                            pass  # 忽略无效的覆盖
            states.append(SignalState(name, value, byte_overrides))
        return states

    def _collect_row_def(self, row: int) -> SignalDef:
        """解析单行表格内容为 SignalDef。校验失败抛 ValueError/TypeError。"""
        can_id_text = self._widget(row, 6).text().strip()
        editor = "value" if self._widget(row, 13).currentIndex() == 0 else "state"
        return SignalDef(
            name=self._widget(row, 0).text().strip(),
            unit=self._widget(row, 1).text(),
            minimum=self._widget(row, 2).value(),
            maximum=self._widget(row, 3).value(),
            default=self._widget(row, 4).value(),
            enabled=self.data_table.item(row, 4).checkState() == Qt.Checked,
            can_id=int(can_id_text, 16) if can_id_text else 0,
            extended=self.data_table.item(row, 6).checkState() == Qt.Checked,
            start_bit=self._widget(row, 8).value(),
            bit_length=self._widget(row, 9).value(),
            byte_order=self._widget(row, 10).currentText(),
            factor=self._widget(row, 11).value(),
            offset=self._widget(row, 12).value(),
            editor=editor,
            states=self._parse_states(self._widget(row, 14).text()),
        )

    def _collect_defs(self) -> list[SignalDef] | None:
        """解析当前表格内容，校验失败返回 None。"""
        parsed: list[SignalDef] = []
        for row in range(self.data_table.rowCount()):
            try:
                sig = self._collect_row_def(row)
            except (ValueError, TypeError) as exc:
                QMessageBox.warning(self, "配置无效", f"第 {row + 1} 行：{exc}")
                return None
            parsed.append(sig)
        names = [sig.name for sig in parsed]
        if len(names) != len(set(names)):
            QMessageBox.warning(self, "配置无效", "信号名称不能重复")
            return None
        return parsed

    def _on_accept(self) -> None:
        defs = self._collect_defs()
        if defs is None:
            return
        self._defs = defs
        self._cycles = self._collect_cycles()
        self.accept()

    def defs(self) -> list[SignalDef]:
        return self._defs

    def cycles(self) -> dict[int, int]:
        return self._cycles

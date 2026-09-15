"""信号映射配置对话框：编辑 signals.json 里的信号定义与报文映射。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHeaderView, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .. import signals
from ..signals import SignalDef, SignalState

HEADERS = ("名称", "单位", "最小", "最大", "默认", "启用", "CAN ID(hex)", "扩展帧",
           "起始bit", "长度(bit)", "字节序", "系数", "偏移", "编辑器", "状态(名=原始值)")
CYCLE_HEADERS = ("CAN ID(hex)", "发送周期(ms)")


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

        # --- 信号表 ---
        self.table = QTableWidget(len(self._defs), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for row, sig in enumerate(self._defs):
            self._fill_row(row, sig)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

        hint = QLabel("物理值 = 原始值 × 系数 + 偏移；状态列格式：故障=65533;开路=65534，"
                      "不写原始值表示该状态仍用滑杆值。起始bit 范围 0-63。")
        hint.setWordWrap(True)

        # --- 报文周期表 ---
        self.cycle_table = QTableWidget(0, len(CYCLE_HEADERS))
        self.cycle_table.setHorizontalHeaderLabels(CYCLE_HEADERS)
        self.cycle_table.verticalHeader().setVisible(False)
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
        ok = QPushButton("确定")
        cancel = QPushButton("取消")
        ok.clicked.connect(self._on_accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)
        layout.addWidget(hint)
        layout.addWidget(cycle_label)
        layout.addWidget(self.cycle_table)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ 填充

    def _fill_row(self, row: int, sig: SignalDef) -> None:
        set_cell = self.table.setCellWidget
        set_cell(row, 0, QLineEdit(sig.name))
        set_cell(row, 1, QLineEdit(sig.unit))

        spin_min = QSpinBox()
        spin_min.setRange(-2000000000, 2000000000)
        spin_min.setValue(sig.minimum)
        set_cell(row, 2, spin_min)

        spin_max = QSpinBox()
        spin_max.setRange(-2000000000, 2000000000)
        spin_max.setValue(sig.maximum)
        set_cell(row, 3, spin_max)

        spin_def = QSpinBox()
        spin_def.setRange(-2000000000, 2000000000)
        spin_def.setValue(sig.default)
        set_cell(row, 4, spin_def)

        check = QTableWidgetItem()
        check.setFlags(check.flags() | Qt.ItemIsUserCheckable)
        check.setCheckState(Qt.Checked if sig.enabled else Qt.Unchecked)
        self.table.setItem(row, 5, check)

        set_cell(row, 6, QLineEdit(f"{sig.can_id:X}"))

        ext = QTableWidgetItem()
        ext.setFlags(ext.flags() | Qt.ItemIsUserCheckable)
        ext.setCheckState(Qt.Checked if sig.extended else Qt.Unchecked)
        self.table.setItem(row, 7, ext)

        spin_start = QSpinBox()
        spin_start.setRange(0, 63)
        spin_start.setValue(sig.start_bit)
        set_cell(row, 8, spin_start)

        spin_bits = QSpinBox()
        spin_bits.setRange(signals.MIN_BIT_LENGTH, signals.MAX_BIT_LENGTH)
        spin_bits.setValue(sig.bit_length)
        set_cell(row, 9, spin_bits)

        combo_order = QComboBox()
        combo_order.addItems(signals.BYTE_ORDERS)
        combo_order.setCurrentText(sig.byte_order)
        set_cell(row, 10, combo_order)

        spin_factor = QDoubleSpinBox()
        spin_factor.setRange(-100000, 100000)
        spin_factor.setDecimals(4)
        spin_factor.setValue(sig.factor)
        set_cell(row, 11, spin_factor)

        spin_offset = QDoubleSpinBox()
        spin_offset.setRange(-1000000, 1000000)
        spin_offset.setDecimals(4)
        spin_offset.setValue(sig.offset)
        set_cell(row, 12, spin_offset)

        combo_editor = QComboBox()
        combo_editor.addItems(["数值", "状态"])
        combo_editor.setCurrentIndex(0 if sig.editor == "value" else 1)
        set_cell(row, 13, combo_editor)

        states = ";".join(s.name if s.raw is None else f"{s.name}={s.raw}"
                          for s in sig.states)
        set_cell(row, 14, QLineEdit(states))

    def _widget(self, row: int, col: int):
        return self.table.cellWidget(row, col)

    # ------------------------------------------------------------------ 周期表

    def _refresh_cycle_table(self) -> None:
        """从信号表收集所有 CAN ID，更新周期表。保留已设周期。"""
        can_ids: set[int] = set()
        for row in range(self.table.rowCount()):
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
            id_edit = QLineEdit(f"{can_id:X}")
            id_edit.setReadOnly(True)
            self.cycle_table.setCellWidget(row, 0, id_edit)
            spin_cycle = QSpinBox()
            spin_cycle.setRange(10, 10000)
            spin_cycle.setValue(self._cycles.get(can_id, 100))
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
        row = self.table.rowCount()
        self.table.insertRow(row)
        sig = SignalDef(name=f"信号{row + 1}", unit="", minimum=0, maximum=100,
                       default=0, enabled=False, can_id=0x100, start_bit=0,
                       bit_length=16, editor="value")
        self._fill_row(row, sig)
        self._refresh_cycle_table()

    def _on_delete_signal(self) -> None:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        if not rows:
            QMessageBox.warning(self, "删除信号", "请先选中要删除的行")
            return
        if self.table.rowCount() <= len(rows):
            QMessageBox.warning(self, "删除信号", "至少需要保留一路信号")
            return
        for row in sorted(rows, reverse=True):
            self.table.removeRow(row)
        self._refresh_cycle_table()

    def _on_move_row(self, direction: int) -> None:
        """direction=-1 上移，+1 下移。支持连续多行整体移动。"""
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.warning(self, "移动信号", "请先选中要移动的行")
            return
        if not all(rows[i + 1] - rows[i] == 1 for i in range(len(rows) - 1)):
            QMessageBox.warning(self, "移动信号", "请选中连续的多行")
            return
        target = rows[0] + direction
        if direction < 0 and target < 0:
            return  # 已在顶部
        if direction > 0 and rows[-1] + 1 >= self.table.rowCount():
            return  # 已在底部
        # 收集选中行和目标行的数据，重新填充
        defs_to_move = [self._collect_row_def(r) for r in rows]
        # 删除原行（从大到小）
        for r in sorted(rows, reverse=True):
            self.table.removeRow(r)
        # 在目标位置插入
        for i, sig in enumerate(defs_to_move):
            self.table.insertRow(target + i)
            self._fill_row(target + i, sig)
        # 保持选中
        for i in range(len(defs_to_move)):
            self.table.selectRow(target + i)
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
        self.table.setRowCount(0)
        for row, sig in enumerate(defs):
            self.table.insertRow(row)
            self._fill_row(row, sig)
        # 导入 cycles
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
                   "cycles": {f"{k:X}": v for k, v in cycles.items()}}
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
        states = []
        for token in text.replace("；", ";").split(";"):
            token = token.strip()
            if not token:
                continue
            name, _, raw = token.partition("=")
            raw = raw.strip()
            value = None
            if raw:
                value = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
            states.append(SignalState(name.strip(), value))
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
            enabled=self.table.item(row, 5).checkState() == Qt.Checked,
            can_id=int(can_id_text, 16) if can_id_text else 0,
            extended=self.table.item(row, 7).checkState() == Qt.Checked,
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
        for row in range(self.table.rowCount()):
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

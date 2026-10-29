"""数据模拟面板：导入 CSV/Excel 数据，按周期逐行驱动信号值发送。"""

from __future__ import annotations

import csv
import os

from .widgets import OperationButton

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTableView, QVBoxLayout,
                               QWidget)

PREVIEW_MAX = 10_000  # 预览表格最大显示行数


class CsvTableModel(QAbstractTableModel):
    """CSV 数据的只读 Model，供 QTableView 虚拟滚动显示。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._names: list[str] = []
        self._rows: list[dict[str, float]] = []
        self._highlight_row: int = -1

    def set_data(self, names: list[str],
                 rows: list[dict[str, float]]) -> None:
        self.beginResetModel()
        self._names = names
        self._rows = rows
        self._highlight_row = -1
        self.endResetModel()

    def set_highlight(self, row: int) -> None:
        """高亮指定行（播放时跟随当前行）。"""
        old = self._highlight_row
        self._highlight_row = row
        if 0 <= old < len(self._rows):
            self.dataChanged.emit(
                self.index(old, 0),
                self.index(old, max(len(self._names) - 1, 0)))
        if 0 <= row < len(self._rows) and row != old:
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, max(len(self._names) - 1, 0)))

    # ---- QAbstractTableModel 接口 ----

    def rowCount(self, parent=QModelIndex()) -> int:
        return min(len(self._rows), PREVIEW_MAX)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._names)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.DisplayRole:
            name = self._names[index.column()]
            val = self._rows[index.row()].get(name, "")
            return f"{val:g}" if isinstance(val, float) else str(val)
        if role == Qt.BackgroundRole and index.row() == self._highlight_row:
            from PySide6.QtGui import QBrush, QPalette
            from PySide6.QtWidgets import QApplication
            palette = QApplication.palette()
            return QBrush(palette.color(QPalette.Active, QPalette.Highlight))
        if role == Qt.ForegroundRole and index.row() == self._highlight_row:
            from PySide6.QtGui import QBrush, QColor
            return QBrush(QColor("#FFFFFF"))
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._names[section]
        return None


class SimulatePanel(QWidget):
    """数据模拟面板：导入 CSV/Excel，按行周期播放数据。"""

    csv_loaded = Signal(object, object)   # list[str] signal_names, list[dict[str,float]] rows
    playback_row = Signal(object)         # dict[str, float] 当前行数据
    playback_started = Signal()
    playback_stopped = Signal()
    playback_finished = Signal()
    message = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._csv_rows: list[dict[str, float]] = []
        self._csv_signal_names: list[str] = []
        self._expected_names: list[str] = []  # 映射配置的信号名
        self._last_dir = str(os.path.expanduser("~"))
        self._playing = False
        self._enabled = True
        self._bus_enabled = False
        self._row_index = 0
        self._cycle_count = 0
        self._playback_timer = QTimer(self)
        self._playback_timer.timeout.connect(self._on_tick)
        self._build()

    # ------------------------------------------------------------------ 界面

    def _build(self) -> None:
        # 工具栏
        toolbar = QHBoxLayout()

        self.csv_label = QLabel("未导入文件")
        toolbar.addWidget(self.csv_label, 1)

        self.import_button = QPushButton("导入文件")
        toolbar.addWidget(self.import_button)

        toolbar.addWidget(QLabel("发送模式"))
        self.send_mode = QComboBox()
        self.send_mode.addItem("循环发送")
        self.send_mode.addItem("单次发送")
        self.send_mode.setMinimumWidth(100)
        toolbar.addWidget(self.send_mode)

        toolbar.addWidget(QLabel("周期"))
        self.row_period = QSpinBox()
        self.row_period.setRange(1, 600000)
        self.row_period.setSingleStep(5)
        self.row_period.setValue(100)
        self.row_period.setSuffix(" ms")
        toolbar.addWidget(self.row_period)

        self.start_button = OperationButton("发送")
        self.start_button.setEnabled(False)
        self.start_button.setFixedWidth(90)
        toolbar.addWidget(self.start_button)

        # 数据预览表格（使用 Model/View 支持大数据量）
        self._model = CsvTableModel(self)
        self.preview = QTableView()
        self.preview.setModel(self._model)
        self.preview.setAlternatingRowColors(True)
        self.preview.setEditTriggers(QTableView.NoEditTriggers)
        self.preview.setSelectionBehavior(QTableView.SelectRows)
        self.preview.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.preview.verticalHeader().setVisible(False)
        self.preview.verticalHeader().setDefaultSectionSize(22)
        header = self.preview.horizontalHeader()
        header_font = self.preview.font()
        header_font.setBold(True)
        header.setFont(header_font)
        header.setStretchLastSection(True)

        # 进度
        self.progress_label = QLabel("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.progress_label)

        self.import_button.clicked.connect(self._on_import)
        self.start_button.clicked.connect(self._on_start_clicked)

    # ------------------------------------------------------------------ 导入

    def _on_import(self) -> None:
        if not self._enabled:
            self.message.emit("信号发送正在运行，请先停止信号发送")
            return
        if self._playing:
            self.message.emit("请先停止播放再导入数据")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入数据", self._last_dir,
            "数据文件 (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls)")
        if not path:
            return
        self._last_dir = os.path.dirname(path)
        headers: list[str] = []
        rows: list[dict[str, float]] = []
        parse_error: str | None = None

        if path.lower().endswith((".xlsx", ".xls")):
            try:
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True, data_only=True)
                ws = wb.active
                row_iter = ws.iter_rows(values_only=True)
                raw_headers = next(row_iter, None)
                if not raw_headers:
                    parse_error = "空文件"
                else:
                    headers = [str(h).strip() if h else "" for h in raw_headers]
                    for line_no, row in enumerate(row_iter, start=2):
                        if not any(cell is not None and str(cell).strip() for cell in row):
                            continue
                        values: dict[str, float] = {}
                        for i, cell in enumerate(row):
                            if i >= len(headers) or not headers[i]:
                                continue
                            if cell is None or str(cell).strip() == "":
                                continue
                            try:
                                values[headers[i]] = float(str(cell).strip())
                            except ValueError:
                                parse_error = f"第{line_no}行数据格式错误：\u201c{cell}\u201d"
                                break
                        if parse_error is not None:
                            break
                        rows.append(values)
                wb.close()
            except Exception as exc:
                QMessageBox.critical(self, "导入错误", f"读取Excel文件失败：{exc}")
                return
        else:
            for enc in ("utf-8-sig", "gbk", "latin-1"):
                headers = []
                rows = []
                parse_error = None
                try:
                    with open(path, encoding=enc) as f:
                        reader = csv.reader(f)
                        raw_headers = next(reader, None)
                        if not raw_headers:
                            parse_error = "空文件"
                            break
                        headers = [h.strip() for h in raw_headers]
                        for line_no, row in enumerate(reader, start=2):
                            if not any(cell.strip() for cell in row):
                                continue
                            values: dict[str, float] = {}
                            for i, cell in enumerate(row):
                                if i >= len(headers):
                                    break
                                try:
                                    values[headers[i]] = float(cell.strip())
                                except ValueError:
                                    parse_error = (
                                        f"第{line_no}行数据格式错误："
                                        f"\u201c{cell.strip()}\u201d")
                                    break
                            if parse_error is not None:
                                break
                            rows.append(values)
                    break
                except UnicodeDecodeError:
                    continue
                except OSError as exc:
                    QMessageBox.critical(self, "导入错误", str(exc))
                    return
            else:
                QMessageBox.critical(
                    self, "导入错误", "无法识别文件编码，请使用 UTF-8 或 GBK")
                return

        if parse_error is not None:
            QMessageBox.warning(self, "导入错误", parse_error)
            return
        if not rows:
            QMessageBox.warning(self, "导入错误", "文件中没有数据行")
            return

        # ── 信号名校验：与映射配置比对，不符则拒绝导入 ──
        if self._expected_names:
            csv_set = set(headers)
            expected_set = set(self._expected_names)
            extra = csv_set - expected_set      # CSV 中多出的
            missing = expected_set - csv_set     # 映射配置中有但 CSV 缺的
            if extra or missing:
                parts: list[str] = []
                if extra:
                    parts.append(f"CSV 中多余的信号（{len(extra)} 路）：\n  "
                                 + "、".join(sorted(extra)))
                if missing:
                    parts.append(f"映射配置缺失的信号（{len(missing)} 路）：\n  "
                                 + "、".join(sorted(missing)))
                msg = ("CSV 信号与映射配置不一致，已取消导入。\n\n"
                       + "\n\n".join(parts))
                QMessageBox.critical(self, "信号校验失败", msg)
                return

        self._csv_signal_names = headers
        self._csv_rows = rows
        self._row_index = 0
        self._cycle_count = 0
        self._update_progress()
        self.csv_label.setText(os.path.basename(path))
        self._fill_preview()
        self.csv_loaded.emit(headers, rows)
        self.message.emit(f"已导入：{len(rows)} 行数据，{len(headers)} 路信号")

    def _fill_preview(self) -> None:
        """将导入的数据填充到预览表格（最多显示 PREVIEW_MAX 行）。"""
        self._model.set_data(self._csv_signal_names, self._csv_rows)

    # ------------------------------------------------------------------ 播放

    def _on_start_clicked(self) -> None:
        if self._playing:
            self._stop()
            return
        if not self._enabled:
            self.message.emit("信号发送正在运行，请先停止信号发送")
            return
        if not self._bus_enabled:
            return
        if not self._csv_rows:
            self.message.emit("请先导入数据文件")
            return
        self._playing = True
        self._row_index = 0
        self._cycle_count = 0
        self.start_button.setText("停止")
        self._refresh_controls()
        self.send_mode.setEnabled(False)
        self.row_period.setEnabled(False)
        self._playback_timer.start(max(self.row_period.value(), 10))
        self._update_progress()
        self.playback_started.emit()

    def _stop(self) -> None:
        self._playing = False
        self._playback_timer.stop()
        self.start_button.setText("发送")
        self._refresh_controls()
        self.send_mode.setEnabled(True)
        self.row_period.setEnabled(True)
        self._update_progress()
        self.playback_stopped.emit()

    def _on_tick(self) -> None:
        if not self._playing or not self._csv_rows:
            self._stop()
            return
        if self._row_index >= len(self._csv_rows):
            if self.send_mode.currentIndex() == 0:
                # 循环发送
                self._row_index = 0
                self._cycle_count += 1
            else:
                # 单次发送
                self._stop()
                self.playback_finished.emit()
                self.message.emit("CSV 数据发送完成")
                return
        row = self._csv_rows[self._row_index]
        self.playback_row.emit(row)
        self._row_index += 1
        self._update_progress()
        # 高亮当前行
        display_row = min(self._row_index - 1, PREVIEW_MAX - 1)
        self._model.set_highlight(display_row)
        # 自动滚动到可见的区域，并更新滚动条
        idx = self._model.index(display_row, 0)
        if idx.isValid():
            self.preview.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
            self.preview.viewport().update()

    def _update_progress(self) -> None:
        total = len(self._csv_rows)
        if not self._playing:
            if total == 0:
                self.progress_label.setText("")
            else:
                self.progress_label.setText(f"共 {total} 行，未开始")
            return
        current = self._row_index
        pct = current / total * 100 if total else 0
        self.progress_label.setText(
            f"进度: {current}/{total} ({pct:.1f}%)  循环: 第{self._cycle_count + 1}次")

    # ------------------------------------------------------------------ 外部接口

    def signal_names(self) -> list[str]:
        return list(self._csv_signal_names)

    def csv_rows(self) -> list[dict[str, float]]:
        return list(self._csv_rows)

    def current_values(self) -> dict[str, float]:
        if not self._csv_rows:
            return {}
        index = max(0, min(self._row_index - 1, len(self._csv_rows) - 1))
        return dict(self._csv_rows[index])

    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        if self._playing:
            self._stop()

    def set_enabled(self, enabled: bool) -> None:
        """启用/禁用开始按钮和导入按钮，用于与信号实验互斥。"""
        self._enabled = enabled
        self._refresh_controls()

    def set_bus_enabled(self, enabled: bool) -> None:
        self._bus_enabled = enabled
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        self.start_button.setEnabled(self._bus_enabled and (self._playing or self._enabled))
        self.import_button.setEnabled(self._enabled and not self._playing)
        self.import_button.setToolTip(
            "请先停止数据模拟" if self._playing else
            "信号发送正在运行，请先停止信号发送" if not self._enabled else "")

    def set_signal_names(self, names: list[str]) -> None:
        """设置映射配置的信号名列表，用于导入 CSV 时校验。"""
        self._expected_names = list(names)

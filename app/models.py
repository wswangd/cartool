"""表格数据模型：列表发送区与报文显示区。"""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, Qt, Signal)
from PySide6.QtGui import QBrush, QColor

from .frame import (RX, TX, SEND_TYPE_OPTIONS, TRACE_HEADERS, CanFrame,
                    FrameFormatError, SendItem, TraceRecord, format_data,
                    format_id, parse_data, parse_id)

# ----------------------------------------------------------------- 列表发送

COL_ENABLED, COL_ID, COL_FORMAT, COL_TYPE, COL_SEND_TYPE, COL_DLC, \
    COL_DATA, COL_PERIOD, COL_REPEAT, COL_NOTE, COL_SENT = range(11)

SEND_HEADERS = ("发送", "ID(hex)", "帧格式", "帧类型", "发送类型",
                "长度", "数据(hex)", "周期(ms)", "次数", "备注", "已发")


class SendListModel(QAbstractTableModel):
    """列表发送区的报文条目，可直接在表格里编辑。"""

    parse_error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[SendItem] = []
        self._uid_rows: dict[int, int] = {}
        self._next_uid = 1
        self._sending = False  # 定时发送中，锁定下拉框编辑

    # ------------------------------------------------------------- 数据访问

    def items(self) -> list[SendItem]:
        return list(self._items)

    def item(self, row: int) -> SendItem | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_of_uid(self, uid: int) -> int:
        return self._uid_rows.get(uid, -1)

    def new_uid(self) -> int:
        uid = self._next_uid
        self._next_uid += 1
        return uid

    def enabled_items(self) -> list[SendItem]:
        return [item for item in self._items if item.enabled]

    # ------------------------------------------------------------- 增删改

    def append(self, frame: CanFrame, note: str = "", period_ms: int = 0,
               repeat: int = 0, enabled: bool = True) -> SendItem:
        item = SendItem(uid=self.new_uid(), frame=frame, enabled=enabled, note=note,
                        period_ms=period_ms, repeat=repeat)
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(item)
        self._uid_rows[item.uid] = row
        self.endInsertRows()
        return item

    def insert_row(self, row: int, item: SendItem) -> None:
        row = max(0, min(row, len(self._items)))
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.insert(row, item)
        self._reindex()
        self.endInsertRows()

    def append_items(self, items: Iterable[SendItem]) -> None:
        new_items = list(items)
        if not new_items:
            return
        first = len(self._items)
        self.beginInsertRows(QModelIndex(), first, first + len(new_items) - 1)
        self._items.extend(new_items)
        self._reindex()
        self.endInsertRows()

    def remove_rows(self, rows: Sequence[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self._items):
                self.beginRemoveRows(QModelIndex(), row, row)
                item = self._items.pop(row)
                self._uid_rows.pop(item.uid, None)
                self.endRemoveRows()
        self._reindex()

    def clear(self) -> None:
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self._uid_rows.clear()
        self.endResetModel()

    def _reindex(self) -> None:
        self._uid_rows = {item.uid: row for row, item in enumerate(self._items)}

    def set_sent(self, uid: int, sent: int) -> None:
        row = self._uid_rows.get(uid)
        if row is None:
            return
        self._items[row].sent = sent
        index = self.index(row, COL_SENT)
        self.dataChanged.emit(index, index, [Qt.DisplayRole])

    def reset_sent(self) -> None:
        for row, item in enumerate(self._items):
            item.sent = 0
        if self._items:
            self.dataChanged.emit(self.index(0, COL_SENT),
                                  self.index(len(self._items) - 1, COL_SENT),
                                  [Qt.DisplayRole])

    def set_sending(self, sending: bool) -> None:
        """定时发送开始/停止时调用，锁定下拉框编辑防止重叠。"""
        self._sending = sending

    def item_is_periodic(self, row: int) -> bool:
        item = self.item(row)
        return bool(item and item.enabled and item.period_ms > 0)

    # ------------------------------------------------------------- 模型接口

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(SEND_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return SEND_HEADERS[section]
        return str(section + 1)

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        col = index.column()
        if col == COL_ENABLED:
            return base | Qt.ItemIsUserCheckable
        if self._sending:
            return base
        if col == COL_SENT:
            return base
        # 定时发送中，锁定下拉框列防止编辑器重叠
        if self._sending and col in (COL_FORMAT, COL_TYPE, COL_SEND_TYPE):
            return base
        return base | Qt.ItemIsEditable

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        frame = item.frame
        col = index.column()

        if role == Qt.CheckStateRole and col == COL_ENABLED:
            return Qt.Checked if item.enabled else Qt.Unchecked
        if role == Qt.TextAlignmentRole:
            if col in (COL_ID, COL_DATA, COL_DLC, COL_PERIOD, COL_REPEAT, COL_SENT):
                return int(Qt.AlignCenter)
            return None
        if role not in (Qt.DisplayRole, Qt.EditRole):
            return None

        if col == COL_ID:
            return format_id(frame.frame_id, frame.extended) if role == Qt.DisplayRole \
                else f"{frame.frame_id:08X}"
        if col == COL_FORMAT:
            return frame.format_text
        if col == COL_TYPE:
            return frame.type_text
        if col == COL_SEND_TYPE:
            return frame.send_type_text
        if col == COL_DLC:
            return str(frame.dlc)
        if col == COL_DATA:
            return format_data(frame.data, frame.dlc) if role == Qt.DisplayRole \
                else format_data(frame.data)
        if col == COL_PERIOD:
            return str(item.period_ms)
        if col == COL_REPEAT:
            return item.repeat_text
        if col == COL_NOTE:
            return item.note
        if col == COL_SENT:
            return str(item.sent)
        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        item = self._items[index.row()]
        frame = item.frame
        col = index.column()

        if role == Qt.CheckStateRole and col == COL_ENABLED:
            if isinstance(value, int):
                item.enabled = value == Qt.Checked.value
            else:
                item.enabled = bool(value)
            self.dataChanged.emit(index, index, [role])
            return True
        if role != Qt.EditRole or col == COL_SENT:
            return False

        try:
            if col == COL_NOTE:
                item.note = str(value)
            elif col == COL_ID:
                frame.frame_id = parse_id(str(value))
                frame.validate()
            elif col == COL_FORMAT:
                frame.extended = str(value) == "扩展帧"
                frame.validate()
            elif col == COL_TYPE:
                frame.remote = str(value) == "远程帧"
            elif col == COL_SEND_TYPE:
                frame.send_type = SEND_TYPE_OPTIONS.index(str(value))
            elif col == COL_DLC:
                frame.dlc = int(str(value))
                frame.validate()
            elif col == COL_DATA:
                frame.data = parse_data(str(value))
                frame.dlc = min(max(frame.dlc, len(frame.data)), 8)
                frame.validate()
            elif col == COL_PERIOD:
                item.period_ms = max(0, int(str(value)))
            elif col == COL_REPEAT:
                text = str(value).strip()
                item.repeat = 0 if text in ("", "无限", "0") else max(0, int(text))
            else:
                return False
        except (FrameFormatError, ValueError) as exc:
            self.parse_error.emit(str(exc))
            return False

        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True


# ----------------------------------------------------------------- 报文显示

TRACE_HEADERS = ("序号", "时间", "设备时间戳(us)", "方向", "帧格式", "帧类型", "ID(hex)", "长度",
                 "数据(hex)")

_COLOR_TX = QColor("#1565C0")
_COLOR_RX = QColor("#1B1B1B")
_COLOR_ERR = QColor("#C62828")


class TraceModel(QAbstractTableModel):
    """收发报文日志，超出容量时丢弃最旧的记录。"""

    DEFAULT_CAPACITY = 1000
    MAX_CAPACITY = 500000

    def __init__(self, parent=None, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__(parent)
        self._records: list[TraceRecord] = []
        self._capacity = max(100, int(capacity))
        self._dropped = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def set_capacity(self, capacity: int) -> None:
        self._capacity = max(100, min(self.MAX_CAPACITY, int(capacity)))
        self._trim()

    @property
    def dropped(self) -> int:
        return self._dropped

    def records(self) -> list[TraceRecord]:
        return list(self._records)

    def append(self, new_records: Sequence[TraceRecord]) -> None:
        if not new_records:
            return
        first = len(self._records)
        self.beginInsertRows(QModelIndex(), first, first + len(new_records) - 1)
        self._records.extend(new_records)
        self.endInsertRows()
        self._trim()

    def _trim(self) -> None:
        overflow = len(self._records) - self._capacity
        if overflow <= 0:
            return
        self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
        del self._records[:overflow]
        self.endRemoveRows()
        self._dropped += overflow

    def clear(self) -> None:
        if not self._records:
            self._dropped = 0
            return
        self.beginResetModel()
        self._records.clear()
        self._dropped = 0
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(TRACE_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return TRACE_HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._records[index.row()]
        frame = record.frame
        col = index.column()

        if role == Qt.ForegroundRole:
            if record.error:
                return QBrush(_COLOR_ERR)
            return QBrush(_COLOR_TX if record.direction == TX else _COLOR_RX)
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignCenter) if col != 8 else None
        if role != Qt.DisplayRole:
            return None

        if col == 0:
            return str(record.seq)
        if col == 1:
            return record.time_text
        if col == 2:
            return str(record.dev_timestamp) if record.dev_timestamp else ""
        if col == 3:
            return record.direction
        if col == 4:
            return frame.format_text
        if col == 5:
            return "错误帧" if record.error else frame.type_text
        if col == 6:
            return format_id(frame.frame_id, frame.extended)
        if col == 7:
            return str(frame.dlc)
        if col == 8:
            return format_data(frame.payload) if not frame.remote else ""
        return None

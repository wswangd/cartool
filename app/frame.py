"""CAN 帧数据结构、文本解析与格式化。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

STD_ID_MAX = 0x7FF
EXT_ID_MAX = 0x1FFFFFFF
DLC_MAX = 8

SEND_TYPES = (
    (0, "正常发送"),
    (1, "单次发送"),
    (2, "自发自收"),
    (3, "单次自发自收"),
)
SEND_TYPE_NAMES = dict(SEND_TYPES)
SEND_TYPE_OPTIONS = tuple(name for _, name in SEND_TYPES)
FORMAT_OPTIONS = ("标准帧", "扩展帧")
TYPE_OPTIONS = ("数据帧", "远程帧")
DLC_OPTIONS = tuple(str(i) for i in range(DLC_MAX + 1))

TRACE_HEADERS = ("序号", "时间", "设备时间戳(us)", "方向", "帧格式", "帧类型", "ID(hex)", "长度",
                 "数据(hex)")

RX = "RX"
TX = "TX"

_SEP_RE = re.compile(r"[\s,;:\-_]+")
_HEX_RE = re.compile(r"\A(?:0[xX])?[0-9A-Fa-f]+\Z")


class FrameFormatError(ValueError):
    """用户输入的报文文本不合法，消息可直接展示给用户。"""


def parse_id(text: str) -> int:
    """按十六进制解析仲裁 ID，如 '1A3' / '0x1A3' -> 419。"""
    raw = text.strip().replace("_", "")
    if not raw:
        raise FrameFormatError("ID 不能为空")
    if not _HEX_RE.match(raw):
        raise FrameFormatError(f"ID '{text}' 不是合法的十六进制数")
    value = int(raw, 16)
    if value > EXT_ID_MAX:
        raise FrameFormatError(f"ID 0x{value:X} 超出 29 位范围 (最大 0x{EXT_ID_MAX:X})")
    return value


def parse_data(text: str) -> bytes:
    """解析数据字节，接受 '01 02'、'01,02'、'1 2'、'0102AB' 等写法。"""
    raw = text.strip()
    if not raw:
        return b""
    joined = []
    for token in _SEP_RE.split(raw):
        if not token:
            continue
        if token.lower().startswith("0x"):
            token = token[2:]
        if not re.fullmatch(r"[0-9A-Fa-f]+", token):
            raise FrameFormatError(f"'{token}' 不是合法的十六进制字节")
        if len(token) == 1:
            token = "0" + token
        elif len(token) % 2:
            raise FrameFormatError(f"'{token}' 的字符数必须是偶数")
        joined.append(token)
    data = bytes.fromhex("".join(joined))
    if len(data) > DLC_MAX:
        raise FrameFormatError(f"数据最多 {DLC_MAX} 字节，当前 {len(data)} 字节")
    return data


def format_id(arb_id: int, extended: bool = False) -> str:
    return f"0x{arb_id:08X}" if extended else f"0x{arb_id:03X}"


def format_data(data: bytes, dlc: int | None = None) -> str:
    payload = data if dlc is None else data[:dlc]
    return " ".join(f"{b:02X}" for b in payload)


@dataclass
class CanFrame:
    """一帧经典 CAN 报文（CAN 2.0A/B）。"""

    frame_id: int = 0
    data: bytes = b""
    dlc: int = -1
    extended: bool = False
    remote: bool = False
    send_type: int = 0

    def __post_init__(self) -> None:
        if self.dlc < 0:
            self.dlc = len(self.data)
        self.validate()

    def validate(self) -> None:
        if not 0 <= self.dlc <= DLC_MAX:
            raise FrameFormatError(f"数据长度必须在 0~{DLC_MAX} 之间，当前 {self.dlc}")
        if len(self.data) > DLC_MAX:
            raise FrameFormatError(f"数据最多 {DLC_MAX} 字节，当前 {len(self.data)} 字节")
        limit = EXT_ID_MAX if self.extended else STD_ID_MAX
        if not 0 <= self.frame_id <= limit:
            kind = "扩展帧" if self.extended else "标准帧"
            raise FrameFormatError(
                f"{kind} ID 0x{self.frame_id:X} 超出范围 (最大 0x{limit:X})"
            )
        if self.send_type not in SEND_TYPE_NAMES:
            raise FrameFormatError(f"未知的发送类型 {self.send_type}")

    @property
    def payload(self) -> bytes:
        """实际上总线的 dlc 个字节，不足补 0x00。"""
        return (self.data + bytes(DLC_MAX))[: self.dlc]

    @property
    def id_text(self) -> str:
        return format_id(self.frame_id, self.extended)

    @property
    def data_text(self) -> str:
        return format_data(self.data, self.dlc)

    @property
    def format_text(self) -> str:
        return "扩展帧" if self.extended else "标准帧"

    @property
    def type_text(self) -> str:
        return "远程帧" if self.remote else "数据帧"

    @property
    def send_type_text(self) -> str:
        return SEND_TYPE_NAMES[self.send_type]

    def copy(self) -> CanFrame:
        return CanFrame(
            frame_id=self.frame_id,
            data=self.data,
            dlc=self.dlc,
            extended=self.extended,
            remote=self.remote,
            send_type=self.send_type,
        )


@dataclass
class TraceRecord:
    """收发日志中的一行。"""

    seq: int
    wall_time: float
    direction: str
    frame: CanFrame
    dev_timestamp: int = 0
    error: bool = False

    @staticmethod
    def make(direction: str, frame: CanFrame, seq: int, dev_timestamp: int = 0,
             error: bool = False) -> TraceRecord:
        return TraceRecord(seq, time.time(), direction, frame, dev_timestamp, error)

    @property
    def time_text(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.wall_time)) + \
            f".{int(self.wall_time * 1000) % 1000:03d}"


@dataclass
class SendItem:
    """列表发送区的一行。"""

    uid: int
    frame: CanFrame = field(default_factory=CanFrame)
    enabled: bool = True
    note: str = ""
    period_ms: int = 0
    repeat: int = 0
    sent: int = 0

    @property
    def repeat_text(self) -> str:
        return "无限" if self.repeat <= 0 else str(self.repeat)

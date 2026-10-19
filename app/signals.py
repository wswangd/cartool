"""信号级实验定义：物理值 ↔ CAN 原始值 ↔ 报文的打包规则。

信号与报文的对应关系（CAN ID、起始bit、长度、字节序、分辨率/偏移）全部来自
signals.json，可用界面里的"映射配置"修改；文件不存在时生成内置默认表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .frame import CanFrame
from .storage import APP_DIR, StorageError

SIGNALS_PATH = APP_DIR / "signals.json"
EXPERIMENTS_PATH = APP_DIR / "experiments.json"

BYTE_ORDERS = ("Intel", "Motorola")
MIN_BIT_LENGTH = 1
MAX_BIT_LENGTH = 64
EDITORS = ("value", "state")
NORMAL_STATE = "正常"


@dataclass
class SignalState:
    name: str
    raw: int | None = None      # None 表示该状态下仍使用滑杆值
    # 字节覆盖：选中此状态时，强制向帧的指定字节写入指定值
    # 格式：[(byte_offset, value), ...]，byte_offset 为帧内字节索引 0-7
    byte_overrides: list[tuple[int, int]] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"name": self.name, "raw": self.raw}
        if self.byte_overrides:
            # 序列化为 [[byte_offset, value], ...] 格式
            d["byte_overrides"] = [[b, v] for b, v in self.byte_overrides]
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> "SignalState":
        value = raw.get("raw")
        # 解析 byte_overrides
        byte_overrides = []
        raw_overrides = raw.get("byte_overrides", [])
        if isinstance(raw_overrides, list):
            for item in raw_overrides:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    try:
                        byte_overrides.append((int(item[0]), int(item[1])))
                    except (TypeError, ValueError):
                        pass
        return cls(name=str(raw.get("name", NORMAL_STATE)),
                   raw=None if value in (None, "") else int(value),
                   byte_overrides=byte_overrides)


@dataclass
class SignalDef:
    name: str
    unit: str
    minimum: float
    maximum: float
    default: float
    enabled: bool
    can_id: int
    extended: bool = False
    start_bit: int = 0
    bit_length: int = 16
    byte_order: str = "Intel"
    factor: float = 1.0
    offset: float = 0.0
    editor: str = "value"
    states: list[SignalState] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 物理值统一按浮点处理，支持小数；同时保持纯整数场景的 JSON 整洁
        self.minimum = float(self.minimum)
        self.maximum = float(self.maximum)
        self.default = float(self.default)
        self.validate()

    # ------------------------------------------------------------------ 校验

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("信号名称不能为空")
        if self.minimum > self.maximum:
            raise ValueError(f"{self.name}: 最小值不能大于最大值")
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError(f"{self.name}: 默认值必须在最小/最大值之间")
        if not MIN_BIT_LENGTH <= self.bit_length <= MAX_BIT_LENGTH:
            raise ValueError(f"{self.name}: 长度只能在 {MIN_BIT_LENGTH}-{MAX_BIT_LENGTH} 之间")
        if not 0 <= self.start_bit <= 63:
            raise ValueError(f"{self.name}: 起始bit只能在 0-63 之间")
        if self.start_bit + self.bit_length > 64:
            raise ValueError(f"{self.name}: 起始bit + 长度超出 64 位报文范围")
        if self.byte_order not in BYTE_ORDERS:
            raise ValueError(f"{self.name}: 字节序只能是 {BYTE_ORDERS}")
        if self.editor not in EDITORS:
            raise ValueError(f"{self.name}: 编辑器类型只能是 {EDITORS}")
        if not self.factor:
            raise ValueError(f"{self.name}: 分辨率不能为 0")
        if not 0 <= self.can_id <= (0x1FFFFFFF if self.extended else 0x7FF):
            raise ValueError(f"{self.name}: CAN ID 超出帧格式范围")

    # ------------------------------------------------------------------ 换算

    @property
    def max_raw(self) -> int:
        return (1 << self.bit_length) - 1

    def phys_to_raw(self, phys: float) -> int:
        raw = round((phys - self.offset) / self.factor)
        return max(0, min(self.max_raw, raw))

    def raw_to_phys(self, raw: int) -> float:
        return raw * self.factor + self.offset

    def state_raw(self, state_name: str) -> int | None:
        for state in self.states:
            if state.name == state_name:
                return state.raw
        return None

    # ------------------------------------------------------------------ 序列化

    @staticmethod
    def _num(value: float) -> float | int:
        """整数值的浮点量以 int 输出，保持 JSON 整洁（100.0 -> 100）。"""
        return int(value) if float(value).is_integer() else float(value)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "unit": self.unit,
            "min": self._num(self.minimum), "max": self._num(self.maximum),
            "default": self._num(self.default), "enabled": self.enabled,
            "can_id": f"{self.can_id:08X}", "extended": self.extended,
            "start_bit": self.start_bit, "bit_length": self.bit_length,
            "byte_order": self.byte_order,
            "factor": self._num(self.factor), "offset": self._num(self.offset),
            "editor": self.editor,
            "states": [s.to_dict() for s in self.states],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SignalDef":
        states = [SignalState.from_dict(s) for s in raw.get("states", [])
                  if isinstance(s, dict)]
        # 兼容旧配置：start_byte → start_bit (×8)
        start_bit = raw.get("start_bit")
        if start_bit is None:
            start_bit = int(raw.get("start_byte", 0)) * 8
        return cls(
            name=str(raw.get("name", "")).strip(),
            unit=str(raw.get("unit", "")),
            minimum=float(raw.get("min", 0)),
            maximum=float(raw.get("max", 100)),
            default=float(raw.get("default", 0)),
            enabled=bool(raw.get("enabled", True)),
            can_id=int(str(raw.get("can_id", "0")), 16),
            extended=bool(raw.get("extended", False)),
            start_bit=int(start_bit),
            bit_length=int(raw.get("bit_length", 16)),
            byte_order=str(raw.get("byte_order", "Intel")),
            factor=float(raw.get("factor", 1.0)),
            offset=float(raw.get("offset", 0.0)),
            editor=str(raw.get("editor", "value")),
            states=states,
        )


# ---------------------------------------------------------------------- 打包

def pack_frames(defs: list[SignalDef], raws: dict[str, int],
                active_states: dict[str, SignalState] | None = None) -> list[CanFrame]:
    """把已启用信号的原始值按 CAN ID 分组装进 8 字节报文。

    active_states: 信号名 → 当前激活状态的映射，用于应用 byte_overrides。
    """
    groups: dict[tuple[int, bool], bytearray] = {}
    for sig in defs:
        raw = raws.get(sig.name)
        if raw is None:
            continue
        buffer = groups.setdefault((sig.can_id, sig.extended), bytearray(8))
        _put_raw(buffer, sig, raw)
    # 应用字节覆盖：遍历所有启用信号，如果其激活状态有 byte_overrides，则写入
    if active_states:
        for sig in defs:
            state = active_states.get(sig.name)
            if state and state.byte_overrides:
                buffer = groups.get((sig.can_id, sig.extended))
                if buffer is not None:
                    for byte_offset, value in state.byte_overrides:
                        if 0 <= byte_offset < 8:
                            buffer[byte_offset] = value & 0xFF
    return [CanFrame(frame_id=can_id, data=bytes(buffer), dlc=8, extended=extended)
            for (can_id, extended), buffer in sorted(groups.items())]


def _put_raw(buffer: bytearray, sig: SignalDef, raw: int) -> None:
    """按 bit 级将信号原始值写入 8 字节报文缓冲区。

    bit 线性编号：bit 0 = byte0 LSB, bit 7 = byte0 MSB, bit 8 = byte1 LSB, ...
    Intel: 信号 bit i (LSB=0) → 报文 bit (start_bit + i)，向高位增长
    Motorola: start_bit 指向 MSB 位置，字节内从 MSB→LSB，跨字节跳到下一字节 MSB
    """
    raw = max(0, min(sig.max_raw, int(raw)))
    for i in range(sig.bit_length):
        if sig.byte_order == "Intel":
            bit = (raw >> i) & 1                       # LSB first
            pos = sig.start_bit + i
        else:  # Motorola sawtooth
            bit = (raw >> (sig.bit_length - 1 - i)) & 1  # MSB first
            byte_offset = sig.start_bit // 8 + i // 8
            bit_in_byte = sig.start_bit % 8 - (i % 8)
            if bit_in_byte < 0:
                byte_offset += 1
                bit_in_byte += 8
            pos = byte_offset * 8 + bit_in_byte
        byte_pos = pos // 8
        bit_in_byte_pos = pos % 8
        if 0 <= byte_pos < len(buffer):
            if bit:
                buffer[byte_pos] |= (1 << bit_in_byte_pos)
            else:
                buffer[byte_pos] &= ~(1 << bit_in_byte_pos)


# ------------------------------------------------------------------ 默认信号

def _fault_states() -> list[SignalState]:
    return [SignalState(NORMAL_STATE, None),
            SignalState("故障", 65533),
            SignalState("开路", 65534),
            SignalState("短路", 65535)]


def _sig(name, unit, minimum, maximum, default, enabled, can_id, start_bit,
         editor="value", offset=0.0) -> SignalDef:
    return SignalDef(name=name, unit=unit, minimum=minimum, maximum=maximum,
                     default=default, enabled=enabled, can_id=can_id,
                     start_bit=start_bit, offset=offset, editor=editor,
                     states=_fault_states() if editor == "state" else [])


def default_signals() -> list[SignalDef]:
    """与参考界面一致的 17 路信号；报文映射为占位默认值，需按实际协议修改。"""
    return [
        _sig("转速", "rpm", 0, 8000, 0, True, 0x100, 0),
        _sig("扭矩", "%", 0, 100, 0, True, 0x100, 16),
        _sig("车速", "km/h", 0, 250, 0, False, 0x100, 32),
        _sig("DPF温度", "℃", 0, 1000, 0, False, 0x100, 48),
        _sig("上游温度", "℃", 0, 1000, 0, True, 0x101, 0),
        _sig("下游温度", "℃", 0, 1000, 0, False, 0x101, 16),
        _sig("上游NOX", "ppm", 0, 2000, 0, False, 0x101, 32, editor="state"),
        _sig("下游NOX", "ppm", 0, 2000, 0, False, 0x101, 48, editor="state"),
        _sig("环境温度", "℃", -40, 125, 25, False, 0x102, 0, offset=-40),
        _sig("大气压力", "0.1kpa", 0, 2500, 1000, False, 0x102, 16),
        _sig("冷却水温", "℃", -40, 150, 25, True, 0x102, 32, offset=-40),
        _sig("尿素浓度", "0.01%", 0, 10000, 0, False, 0x102, 48, editor="state"),
        _sig("尿素温度", "℃", -40, 125, 25, False, 0x103, 0, offset=-40),
        _sig("尿素液位", "0.1%", 0, 1000, 600, False, 0x103, 16),
        _sig("管路压力", "kp", 0, 2000, 600, False, 0x103, 32),
        _sig("尿素喷射量", "g/h", 0, 20000, 0, False, 0x103, 48),
        _sig("排气流量", "kg/h", 0, 2000, 0, True, 0x104, 0),
    ]


# ------------------------------------------------------------------ 文件读写

def load_signals() -> list[SignalDef]:
    if not SIGNALS_PATH.exists():
        defs = default_signals()
        try:
            save_signals(defs)
        except StorageError:
            pass
        return defs
    try:
        payload = json.loads(SIGNALS_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        raise StorageError(f"signals.json 解析失败：{exc}") from exc
    items = payload.get("signals") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise StorageError("signals.json 里找不到 signals 数组")
    try:
        return [SignalDef.from_dict(raw) for raw in items if isinstance(raw, dict)]
    except (TypeError, ValueError) as exc:
        raise StorageError(f"signals.json 信号定义无效：{exc}") from exc


def load_cycles() -> dict[int, int]:
    """读取 CAN ID → 发送周期(ms) 映射，缺失时返回空 dict。"""
    if not SIGNALS_PATH.exists():
        return {}
    try:
        payload = json.loads(SIGNALS_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    raw_cycles = payload.get("cycles", {}) if isinstance(payload, dict) else {}
    result: dict[int, int] = {}
    for key, value in raw_cycles.items():
        try:
            result[int(str(key), 16)] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def save_signals(defs: list[SignalDef], cycles: dict[int, int] | None = None) -> None:
    payload = {"version": 1,
               "note": "信号与报文映射配置：can_id/extended/start_bit/bit_length/"
                       "byte_order/factor/offset 决定物理值如何装进报文",
               "signals": [sig.to_dict() for sig in defs]}
    if cycles:
        payload["cycles"] = {f"{k:X}": v for k, v in cycles.items()}
    try:
        SIGNALS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"写入 signals.json 失败：{exc}") from exc


def load_experiments() -> dict[str, dict]:
    if not EXPERIMENTS_PATH.exists():
        return {}
    try:
        payload = json.loads(EXPERIMENTS_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_experiment(name: str, snapshot: dict) -> None:
    experiments = load_experiments()
    experiments[name] = snapshot
    try:
        EXPERIMENTS_PATH.write_text(
            json.dumps(experiments, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"写入 experiments.json 失败：{exc}") from exc


def delete_experiment(name: str) -> None:
    experiments = load_experiments()
    if experiments.pop(name, None) is None:
        return
    try:
        EXPERIMENTS_PATH.write_text(
            json.dumps(experiments, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"写入 experiments.json 失败：{exc}") from exc

"""CAN 通道后端：真实 ZLG 设备与无硬件调试用的仿真回环。"""

from __future__ import annotations

import itertools
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

from .frame import CanFrame
from .zlgcan_api import (BAUD_TIMING, DEVICE_TYPES, MODE_NAMES, ZcanApi, ZlgError)

BACKEND_ZLG = "zlg"
BACKEND_SIM = "sim"
BACKEND_NAMES = {BACKEND_ZLG: "ZLG USBCAN", BACKEND_SIM: "仿真回环"}

ReceivedFrame = tuple[CanFrame, int, bool]


def baud_label(timing0: int, timing1: int) -> str:
    for label, (t0, t1) in BAUD_TIMING.items():
        if (t0, t1) == (timing0, timing1):
            return label
    return f"自定义(T0=0x{timing0:02X},T1=0x{timing1:02X})"


@dataclass
class BusConfig:
    backend: str = BACKEND_ZLG
    device_type: int = 4
    device_index: int = 0
    channel: int = 0
    timing0: int = 0x00
    timing1: int = 0x1C
    mode: int = 0
    acc_code: int = 0x00000000
    acc_mask: int = 0xFFFFFFFF

    @property
    def baud(self) -> str:
        return baud_label(self.timing0, self.timing1)

    def describe(self) -> str:
        if self.backend == BACKEND_SIM:
            return f"仿真回环 CH{self.channel} {self.baud}"
        names = dict(DEVICE_TYPES)
        dev = names.get(self.device_type, f"设备类型{self.device_type}")
        mode = MODE_NAMES.get(self.mode, str(self.mode))
        return f"{dev}[{self.device_index}] CH{self.channel} {self.baud} {mode}"

    def to_dict(self) -> dict:
        return {
            "backend": self.backend, "device_type": self.device_type,
            "device_index": self.device_index, "channel": self.channel,
            "timing0": self.timing0, "timing1": self.timing1, "mode": self.mode,
            "acc_code": self.acc_code, "acc_mask": self.acc_mask,
        }

    @staticmethod
    def from_dict(raw: dict) -> "BusConfig":
        cfg = BusConfig()
        for key in ("backend", "device_type", "device_index", "channel", "timing0",
                    "timing1", "mode", "acc_code", "acc_mask"):
            if key in raw:
                setattr(cfg, key, raw[key])
        return cfg


class BusBackend(ABC):
    """一次通道连接的抽象，收发以外的逻辑与具体硬件无关。"""

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def transmit(self, frames: list[CanFrame]) -> int: ...

    @abstractmethod
    def receive(self, limit: int = 500, wait_ms: int = 0) -> list[ReceivedFrame]: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    def status(self) -> dict | None:
        return None

    def clear_buffer(self) -> None:
        self.receive(limit=100000)

    def reset(self) -> None:
        self.clear_buffer()


class ZlgBus(BusBackend):
    def __init__(self, config: BusConfig, api: ZcanApi | None = None) -> None:
        self._config = config
        self._api = api or ZcanApi()
        self._device_handle: int | None = None
        self._channel_handle: int | None = None
        self._info = ""

    @property
    def is_open(self) -> bool:
        return self._channel_handle is not None

    @property
    def info(self) -> str:
        return self._info

    def open(self) -> None:
        cfg = self._config
        try:
            self._device_handle = self._api.open_device(cfg.device_type, cfg.device_index)
            self._info = self._api.device_info(self._device_handle)
            self._channel_handle = self._api.init_can(
                self._device_handle, cfg.channel, cfg.timing0, cfg.timing1,
                cfg.mode, cfg.acc_code, cfg.acc_mask)
            self._api.start_can(self._channel_handle)
            self._api.clear_buffer(self._channel_handle)
        except ZlgError:
            self.close()
            raise

    def close(self) -> None:
        if self._channel_handle is not None:
            try:
                self._api.reset_can(self._channel_handle)
            except ZlgError:
                pass
            self._channel_handle = None
        if self._device_handle is not None:
            try:
                self._api.close_device(self._device_handle)
            except ZlgError:
                pass
            self._device_handle = None

    def _channel(self) -> int:
        if self._channel_handle is None:
            raise ZlgError("通道未打开")
        return self._channel_handle

    def transmit(self, frames: list[CanFrame]) -> int:
        return self._api.transmit(self._channel(), frames)

    def receive(self, limit: int = 500, wait_ms: int = 0) -> list[ReceivedFrame]:
        return self._api.receive(self._channel(), limit, wait_ms)

    def clear_buffer(self) -> None:
        if self.is_open:
            self._api.clear_buffer(self._channel_handle)

    def reset(self) -> None:
        self._api.reset_can(self._channel())
        self._api.start_can(self._channel())
        self._api.clear_buffer(self._channel())

    def status(self) -> dict | None:
        if not self.is_open:
            return None
        result = self._api.channel_status(self._channel_handle)
        if result is None:
            return None
        # USBCAN-I/II 不支持读 SJA1000 寄存器，驱动返回 OK 但各字段填 0xFF
        if (result["reg_status"] == 0xFF and result["rx_err_counter"] == 0xFF
                and result["tx_err_counter"] == 0xFF):
            return {"available": False}
        result["available"] = True
        result["bus_off"] = bool(result["reg_status"] & 0x80)
        result["error_code"] = self._api.channel_err_info(self._channel_handle) or 0
        return result


class SimBus(BusBackend):
    """把发出的帧原样回环到接收缓冲，用于没有硬件时验证界面与调度逻辑。"""

    def __init__(self, config: BusConfig) -> None:
        self._config = config
        self._open = False
        self._rx: deque[ReceivedFrame] = deque(maxlen=100000)
        self._tick = itertools.count(1)
        self._started = 0.0
        self._tx_count = 0

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True
        self._started = time.perf_counter()
        self._rx.clear()

    def close(self) -> None:
        self._open = False
        self._rx.clear()

    def transmit(self, frames: list[CanFrame]) -> int:
        if not self._open:
            raise ZlgError("通道未打开")
        for frame in frames:
            elapsed_us = int((time.perf_counter() - self._started) * 1e6)
            self._rx.append((frame.copy(), elapsed_us, False))
        self._tx_count += len(frames)
        return len(frames)

    def receive(self, limit: int = 500, wait_ms: int = 0) -> list[ReceivedFrame]:
        if not self._open:
            return []
        batch = []
        while self._rx and len(batch) < limit:
            batch.append(self._rx.popleft())
        return batch

    def status(self) -> dict | None:
        if not self._open:
            return None
        return {"rx_err_counter": 0, "tx_err_counter": 0, "bus_off": False,
                "error_code": 0, "sim_tx": self._tx_count}


def create_bus(config: BusConfig, api: ZcanApi | None = None) -> BusBackend:
    if config.backend == BACKEND_SIM:
        return SimBus(config)
    return ZlgBus(config, api)

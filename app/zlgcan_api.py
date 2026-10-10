"""周立功 zlgcan.dll 的 ctypes 封装。

与厂商自带 zlgcan.py 的区别：显式声明 argtypes/restype，句柄统一用 c_void_p，
避免 64 位 Python 下指针被截断成 c_int；所有调用加锁，允许多线程安全使用。
"""

from __future__ import annotations

import os
import sys
import threading
from ctypes import (Structure, Union, WinDLL, byref, c_int, c_ubyte, c_uint,
                    c_ulonglong, c_ushort, c_void_p)
from pathlib import Path
from typing import Sequence

from .frame import CanFrame, DLC_MAX

# 打包成 exe 后，lib 目录作为只读资源被 PyInstaller 释放到 _internal/lib；
# 开发时放在项目根目录。
if getattr(sys, "frozen", False):
    LIB_DIR = Path(sys._MEIPASS).resolve() / "lib"
else:
    LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
DLL_PATH = LIB_DIR / "zlgcan.dll"

STATUS_OK = 1
INVALID_HANDLE = 0
TYPE_CAN = 0
TYPE_CANFD = 1

# 经典 CAN 波特率与 SJA1000 时序寄存器 (Timing0, Timing1) 对照
BAUD_TIMING: dict[str, tuple[int, int]] = {
    "1000kbps": (0x00, 0x14),
    "800kbps": (0x00, 0x16),
    "500kbps": (0x00, 0x1C),
    "250kbps": (0x01, 0x1C),
    "125kbps": (0x03, 0x1C),
    "100kbps": (0x04, 0x1C),
    "50kbps": (0x09, 0x1C),
    "20kbps": (0x18, 0x1C),
    "10kbps": (0x31, 0x1C),
    "5kbps": (0x62, 0x1C),
}
BAUD_OPTIONS = tuple(BAUD_TIMING)

# 走经典 CAN (timing0/timing1) 初始化路径的设备，CANFD 设备需要另一套时序配置
DEVICE_TYPES: tuple[tuple[int, str], ...] = (
    (4, "USBCAN-II"),
    (3, "USBCAN-I"),
    (20, "USBCAN-E-U"),
    (21, "USBCAN-2E-U"),
    (31, "USBCAN-4E-U"),
    (34, "USBCAN-8E-U"),
)

MODE_NORMAL = 0
MODE_LISTEN_ONLY = 1
MODE_NAMES = {MODE_NORMAL: "正常模式", MODE_LISTEN_ONLY: "只听模式"}


class ZlgError(RuntimeError):
    """ZLG 接口调用失败。"""


class ZCAN_DEVICE_INFO(Structure):
    _fields_ = [
        ("hw_Version", c_ushort), ("fw_Version", c_ushort),
        ("dr_Version", c_ushort), ("in_Version", c_ushort),
        ("irq_Num", c_ushort), ("can_Num", c_ubyte),
        ("str_Serial_Num", c_ubyte * 20), ("str_hw_Type", c_ubyte * 40),
        ("reserved", c_ushort * 4),
    ]

    @staticmethod
    def _text(raw) -> str:
        return bytes(raw).split(b"\0")[0].decode("ascii", errors="replace").strip()

    @staticmethod
    def _version(value: int) -> str:
        return f"V{value >> 8:x}.{value & 0xFF:02x}"

    def summary(self) -> str:
        return (f"{self._text(self.str_hw_Type)}  SN:{self._text(self.str_Serial_Num)}  "
                f"通道数:{self.can_Num}  硬件:{self._version(self.hw_Version)}  "
                f"固件:{self._version(self.fw_Version)}")


class _CAN_INIT_CONFIG(Structure):
    _fields_ = [
        ("acc_code", c_uint), ("acc_mask", c_uint), ("reserved", c_uint),
        ("filter", c_ubyte), ("timing0", c_ubyte), ("timing1", c_ubyte),
        ("mode", c_ubyte),
    ]


class _CONFIG_UNION(Union):
    _fields_ = [("can", _CAN_INIT_CONFIG)]


class ZCAN_CHANNEL_INIT_CONFIG(Structure):
    _fields_ = [("can_type", c_uint), ("config", _CONFIG_UNION)]


class ZCAN_CAN_FRAME(Structure):
    _fields_ = [
        ("can_id", c_uint, 29), ("err", c_uint, 1), ("rtr", c_uint, 1),
        ("eff", c_uint, 1), ("can_dlc", c_ubyte), ("_pad", c_ubyte),
        ("_res0", c_ubyte), ("_res1", c_ubyte), ("data", c_ubyte * DLC_MAX),
    ]


class ZCAN_TRANSMIT_DATA(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("transmit_type", c_uint)]


class ZCAN_RECEIVE_DATA(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("timestamp", c_ulonglong)]


class ZCAN_CHANNEL_ERR_INFO(Structure):
    _fields_ = [("error_code", c_uint), ("passive_ErrData", c_ubyte * 3),
                ("arLost_ErrData", c_ubyte)]


class ZCAN_CHANNEL_STATUS(Structure):
    _fields_ = [("errInterrupt", c_ubyte), ("regMode", c_ubyte), ("regStatus", c_ubyte),
                ("regALCapture", c_ubyte), ("regECCapture", c_ubyte),
                ("regEWLimit", c_ubyte), ("regRECounter", c_ubyte),
                ("regTECounter", c_ubyte), ("Reserved", c_ubyte)]


class ZcanApi:
    """zlgcan.dll 的线程安全封装。"""

    def __init__(self, dll_path: Path | str = DLL_PATH) -> None:
        dll_path = Path(dll_path)
        if not dll_path.exists():
            raise ZlgError(f"未找到 {dll_path}，请确认 lib 目录随程序一起分发")
        # zlgcan.dll 运行时按名字加载 kerneldlls 下的设备驱动，需登记这两个搜索目录
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(dll_path.parent))
            kernels = dll_path.parent / "kerneldlls"
            if kernels.is_dir():
                os.add_dll_directory(str(kernels))
        self._dll = WinDLL(str(dll_path))
        self._lock = threading.RLock()
        self._rx_buffer: tuple[int, object] | None = None
        self._declare_prototypes()

    def _declare_prototypes(self) -> None:
        d = self._dll
        d.ZCAN_OpenDevice.restype = c_void_p
        d.ZCAN_OpenDevice.argtypes = [c_uint, c_uint, c_uint]
        d.ZCAN_CloseDevice.restype = c_uint
        d.ZCAN_CloseDevice.argtypes = [c_void_p]
        d.ZCAN_GetDeviceInf.restype = c_uint
        d.ZCAN_GetDeviceInf.argtypes = [c_void_p, c_void_p]
        d.ZCAN_IsDeviceOnLine.restype = c_uint
        d.ZCAN_IsDeviceOnLine.argtypes = [c_void_p]
        d.ZCAN_InitCAN.restype = c_void_p
        d.ZCAN_InitCAN.argtypes = [c_void_p, c_uint, c_void_p]
        d.ZCAN_StartCAN.restype = c_uint
        d.ZCAN_StartCAN.argtypes = [c_void_p]
        d.ZCAN_ResetCAN.restype = c_uint
        d.ZCAN_ResetCAN.argtypes = [c_void_p]
        d.ZCAN_ClearBuffer.restype = c_uint
        d.ZCAN_ClearBuffer.argtypes = [c_void_p]
        d.ZCAN_ReadChannelErrInfo.restype = c_uint
        d.ZCAN_ReadChannelErrInfo.argtypes = [c_void_p, c_void_p]
        d.ZCAN_ReadChannelStatus.restype = c_uint
        d.ZCAN_ReadChannelStatus.argtypes = [c_void_p, c_void_p]
        d.ZCAN_GetReceiveNum.restype = c_uint
        d.ZCAN_GetReceiveNum.argtypes = [c_void_p, c_uint]
        d.ZCAN_Transmit.restype = c_uint
        d.ZCAN_Transmit.argtypes = [c_void_p, c_void_p, c_uint]
        d.ZCAN_Receive.restype = c_uint
        d.ZCAN_Receive.argtypes = [c_void_p, c_void_p, c_uint, c_int]

    # ------------------------------------------------------------------ 设备

    def open_device(self, device_type: int, device_index: int = 0) -> int:
        with self._lock:
            handle = self._dll.ZCAN_OpenDevice(c_uint(device_type),
                                               c_uint(device_index), c_uint(0))
        if not handle:
            raise ZlgError(
                f"打开设备失败 (类型 {device_type}, 索引 {device_index})；"
                "请检查设备是否插好、驱动是否安装、是否被其它程序占用"
            )
        return handle

    def close_device(self, device_handle: int) -> None:
        with self._lock:
            self._dll.ZCAN_CloseDevice(c_void_p(device_handle))

    def device_info(self, device_handle: int) -> str:
        info = ZCAN_DEVICE_INFO()
        with self._lock:
            ret = self._dll.ZCAN_GetDeviceInf(c_void_p(device_handle), byref(info))
        return info.summary() if ret == STATUS_OK else "读取设备信息失败"

    def is_online(self, device_handle: int) -> bool:
        with self._lock:
            return self._dll.ZCAN_IsDeviceOnLine(c_void_p(device_handle)) == STATUS_OK

    # ------------------------------------------------------------------ 通道

    def init_can(self, device_handle: int, channel: int, timing0: int, timing1: int,
                 mode: int = MODE_NORMAL, acc_code: int = 0,
                 acc_mask: int = 0xFFFFFFFF) -> int:
        cfg = ZCAN_CHANNEL_INIT_CONFIG()
        cfg.can_type = TYPE_CAN
        cfg.config.can.acc_code = acc_code
        cfg.config.can.acc_mask = acc_mask
        cfg.config.can.filter = 0
        cfg.config.can.timing0 = timing0
        cfg.config.can.timing1 = timing1
        cfg.config.can.mode = mode
        with self._lock:
            handle = self._dll.ZCAN_InitCAN(c_void_p(device_handle), c_uint(channel),
                                            byref(cfg))
        if not handle:
            raise ZlgError(f"初始化通道 CH{channel} 失败，请检查波特率与设备索引")
        return handle

    def start_can(self, channel_handle: int) -> None:
        with self._lock:
            ret = self._dll.ZCAN_StartCAN(c_void_p(channel_handle))
        if ret != STATUS_OK:
            raise ZlgError("启动通道失败")

    def reset_can(self, channel_handle: int) -> None:
        with self._lock:
            ret = self._dll.ZCAN_ResetCAN(c_void_p(channel_handle))
        if ret != STATUS_OK:
            raise ZlgError("复位通道失败")

    def clear_buffer(self, channel_handle: int) -> None:
        with self._lock:
            self._dll.ZCAN_ClearBuffer(c_void_p(channel_handle))

    def channel_status(self, channel_handle: int) -> dict[str, int] | None:
        status = ZCAN_CHANNEL_STATUS()
        with self._lock:
            ret = self._dll.ZCAN_ReadChannelStatus(c_void_p(channel_handle),
                                                   byref(status))
        if ret != STATUS_OK:
            return None
        return {
            "err_interrupt": status.errInterrupt,
            "reg_status": status.regStatus,
            "al_capture": status.regALCapture,
            "ec_capture": status.regECCapture,
            "rx_err_counter": status.regRECounter,
            "tx_err_counter": status.regTECounter,
        }

    def channel_err_info(self, channel_handle: int) -> int | None:
        info = ZCAN_CHANNEL_ERR_INFO()
        with self._lock:
            ret = self._dll.ZCAN_ReadChannelErrInfo(c_void_p(channel_handle),
                                                    byref(info))
        return info.error_code if ret == STATUS_OK else None

    # ------------------------------------------------------------------ 收发

    def transmit(self, channel_handle: int, frames: Sequence[CanFrame]) -> int:
        if not frames:
            return 0
        count = len(frames)
        buffer = (ZCAN_TRANSMIT_DATA * count)()
        for i, frame in enumerate(frames):
            payload = frame.payload
            buffer[i].transmit_type = frame.send_type
            buffer[i].frame.can_id = frame.frame_id
            buffer[i].frame.eff = 1 if frame.extended else 0
            buffer[i].frame.rtr = 1 if frame.remote else 0
            buffer[i].frame.err = 0
            buffer[i].frame.can_dlc = frame.dlc
            for j, byte in enumerate(payload):
                buffer[i].frame.data[j] = byte
        with self._lock:
            sent = self._dll.ZCAN_Transmit(c_void_p(channel_handle), byref(buffer),
                                           c_uint(count))
        if sent != count:
            raise ZlgError(f"发送失败：请求 {count} 帧，实际发出 {sent} 帧（总线可能未连接或波特率不匹配）")
        return sent

    def receive(self, channel_handle: int, limit: int = 500,
                wait_ms: int = 0) -> list[tuple[CanFrame, int, bool]]:
        pending = self._get_receive_num(channel_handle)
        if not pending:
            if wait_ms <= 0:
                return []
            with self._lock:
                pending = self._dll.ZCAN_GetReceiveNum(c_void_p(channel_handle),
                                                       c_uint(TYPE_CAN))
            if not pending:
                return []
        size = min(int(pending), limit)
        buffer = self._buffer_for(size)
        with self._lock:
            got = self._dll.ZCAN_Receive(c_void_p(channel_handle), byref(buffer),
                                         c_uint(size), c_int(wait_ms))
        result: list[tuple[CanFrame, int, bool]] = []
        for i in range(got):
            raw = buffer[i].frame
            dlc = raw.can_dlc if raw.can_dlc <= DLC_MAX else DLC_MAX
            result.append((
                CanFrame(
                    frame_id=raw.can_id,
                    data=bytes(raw.data[:dlc]),
                    dlc=dlc,
                    extended=bool(raw.eff),
                    remote=bool(raw.rtr),
                ),
                int(buffer[i].timestamp),
                bool(raw.err),
            ))
        return result

    def _get_receive_num(self, channel_handle: int) -> int:
        with self._lock:
            return int(self._dll.ZCAN_GetReceiveNum(c_void_p(channel_handle),
                                                    c_uint(TYPE_CAN)))

    def _buffer_for(self, size: int):
        if self._rx_buffer is None or self._rx_buffer[0] < size:
            self._rx_buffer = (size, (ZCAN_RECEIVE_DATA * size)())
        return self._rx_buffer[1]

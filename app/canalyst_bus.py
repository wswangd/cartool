"""CANalyst-II ControlCAN x64 backend; ABI follows the supplied ControlCAN.h."""
from ctypes import Structure, WinDLL, POINTER, byref, c_uint32 as U32, c_uint16 as U16, c_uint8 as U8, c_char, c_int
import os
import threading

from .bus import BusBackend
from .frame import CanFrame
from .zlgcan_api import LIB_DIR, ZlgError


class BoardInfo(Structure):
    _fields_ = [(n, U16) for n in ('hw', 'fw', 'driver', 'library', 'irq')] + [
        ('channels', U8), ('serial', c_char * 20), ('model', c_char * 40), ('reserved', U16 * 4)]


class InitConfig(Structure):
    _fields_ = [('code', U32), ('mask', U32), ('reserved', U32),
                ('filter', U8), ('timing0', U8), ('timing1', U8), ('mode', U8)]


class CanObject(Structure):
    _fields_ = [('id', U32), ('timestamp', U32), ('timeflag', U8), ('sendtype', U8),
                ('remote', U8), ('extended', U8), ('length', U8),
                ('data', U8 * 8), ('reserved', U8 * 3)]


class CanStatus(Structure):
    _fields_ = [(n, U8) for n in ('interrupt', 'mode', 'status', 'arbitration',
                                 'error', 'limit', 'rx', 'tx')] + [('reserved', U32)]


class ErrorInfo(Structure):
    _fields_ = [('code', U32), ('passive', U8 * 3), ('arbitration', U8)]


class CanalystBus(BusBackend):
    def __init__(self, config, dll=None):
        self._config = config
        self._open = False
        self._info = ''
        self._lock = threading.RLock()
        directory = LIB_DIR / 'canalyst'
        try:
            self._dll_directory = os.add_dll_directory(str(directory)) if dll is None else None
            self._dll = dll if dll is not None else WinDLL(str(directory / 'ControlCAN.dll'))
            signatures = {
                'OpenDevice': [U32, U32, U32], 'CloseDevice': [U32, U32],
                'InitCAN': [U32, U32, U32, POINTER(InitConfig)],
                'ReadBoardInfo': [U32, U32, POINTER(BoardInfo)],
                'ReadCANStatus': [U32, U32, U32, POINTER(CanStatus)],
                'ReadErrInfo': [U32, U32, U32, POINTER(ErrorInfo)],
                'Transmit': [U32, U32, U32, POINTER(CanObject), U32],
                'Receive': [U32, U32, U32, POINTER(CanObject), U32, c_int],
            }
            for name in ('GetReceiveNum', 'ClearBuffer', 'StartCAN', 'ResetCAN'):
                signatures[name] = [U32, U32, U32]
            for name, args in signatures.items():
                fn = getattr(self._dll, 'VCI_' + name)
                fn.argtypes, fn.restype = args, U32
        except (OSError, AttributeError) as exc:
            raise ZlgError(f'加载 CANalyst-II 驱动失败：{exc}') from exc

    @property
    def is_open(self):
        return self._open

    @property
    def info(self):
        return self._info

    def _call(self, name, *args, channel=True):
        cfg = self._config
        prefix = (4, cfg.device_index, cfg.channel) if channel else (4, cfg.device_index)
        with self._lock:
            return getattr(self._dll, 'VCI_' + name)(*prefix, *args)

    def _require(self, name, *args, channel=True):
        if self._call(name, *args, channel=channel) != 1:
            raise ZlgError(f'CANalyst-II {name} 失败，请检查连接、驱动及设备占用')

    def open(self):
        cfg = self._config
        if cfg.channel not in (0, 1):
            raise ZlgError('CANalyst-II 仅支持 CAN1 / CAN2')
        self._require('OpenDevice', 0, channel=False)
        self._open = True
        try:
            board = BoardInfo()
            self._require('ReadBoardInfo', byref(board), channel=False)
            version = lambda value: f'{value >> 8:X}.{value & 255:02X}'
            text = lambda value: value.decode('gb18030', errors='replace').strip()
            self._info = (f'{text(board.model)} SN:{text(board.serial)} 通道数:{board.channels} '
                          f'硬件:V{version(board.hw)} 固件:V{version(board.fw)} '
                          f'驱动:V{version(board.driver)} 动态库:V{version(board.library)} '
                          f'板载中断:0x{board.irq:X}')
            init = InitConfig(cfg.acc_code, cfg.acc_mask, 0, 1, cfg.timing0, cfg.timing1, cfg.mode)
            self._require('InitCAN', byref(init))
            self._require('StartCAN')
            self.clear_buffer()
        except Exception:
            self.close()
            raise

    def close(self):
        if self._open:
            try:
                self._call('ResetCAN')
            finally:
                self._call('CloseDevice', channel=False)
                self._open = False

    def transmit(self, frames):
        if not self._open:
            raise ZlgError('通道未打开')
        if not frames:
            return 0
        buffer = (CanObject * len(frames))()
        for obj, frame in zip(buffer, frames):
            obj.id, obj.sendtype = frame.frame_id, frame.send_type
            obj.remote, obj.extended, obj.length = frame.remote, frame.extended, frame.dlc
            for index, byte in enumerate(frame.payload):
                obj.data[index] = byte
        sent = self._call('Transmit', buffer, len(frames))
        if sent != len(frames):
            raise ZlgError(f'CANalyst-II 发送失败：请求 {len(frames)} 帧，返回 {sent}')
        return sent

    def receive(self, limit=500, wait_ms=0):
        if not self._open or limit <= 0:
            return []
        pending = self._call('GetReceiveNum')
        if pending == 0xFFFFFFFF:
            raise ZlgError('CANalyst-II 读取接收数量失败')
        if not pending and wait_ms <= 0:
            return []
        size = min(pending or limit, limit)
        buffer = (CanObject * size)()
        count = self._call('Receive', buffer, size, wait_ms)
        if count == 0xFFFFFFFF or count > size:
            raise ZlgError('CANalyst-II 接收失败')
        result = []
        for obj in buffer[:count]:
            if obj.length > 8:
                raise ZlgError('CANalyst-II 返回无效数据长度')
            frame = CanFrame(frame_id=obj.id, extended=bool(obj.extended), remote=bool(obj.remote),
                             dlc=obj.length, data=bytes(obj.data[:obj.length]))
            result.append((frame, obj.timestamp * 100 if obj.timeflag else None, False))
        return result

    def clear_buffer(self):
        self._require('ClearBuffer')

    def reset(self):
        self._require('ResetCAN')
        self._require('StartCAN')

    def status(self):
        status, error = CanStatus(), ErrorInfo()
        if self._call('ReadCANStatus', byref(status)) != 1:
            return None
        code = error.code if self._call('ReadErrInfo', byref(error)) == 1 else 0
        return {'rx_err_counter': status.rx, 'tx_err_counter': status.tx,
                'bus_off': bool(status.status & 0x80), 'error_code': code}

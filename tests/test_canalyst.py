import ctypes
import os
import unittest
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from app.bus import BusConfig, BACKEND_CANALYST
from app.canalyst_bus import CanalystBus, CanObject, BoardInfo, InitConfig
from app.frame import CanFrame
from app.zlgcan_api import ZlgError


class Function:
    def __init__(self, callback):
        self.callback = callback
    def __call__(self, *args):
        return self.callback(*args)


class CanalystTests(unittest.TestCase):
    def fake(self):
        dll = SimpleNamespace()
        for name in ('OpenDevice', 'CloseDevice', 'InitCAN', 'ReadBoardInfo', 'ReadCANStatus',
                     'ReadErrInfo', 'Transmit', 'Receive', 'GetReceiveNum', 'ClearBuffer', 'StartCAN', 'ResetCAN'):
            setattr(dll, 'VCI_' + name, Function(lambda *args: 1))
        return dll

    def test_abi_and_send_receive(self):
        self.assertEqual(ctypes.sizeof(CanObject), 24)
        self.assertEqual(ctypes.sizeof(BoardInfo), 80)
        self.assertEqual(ctypes.sizeof(InitConfig), 16)
        dll = self.fake()
        def board(device, index, pointer):
            pointer._obj.model = b'CANalyst-II'
            pointer._obj.serial = b'01900012BC0'
            pointer._obj.channels = 2
            pointer._obj.hw = 0x900
            return 1
        dll.VCI_ReadBoardInfo.callback = board
        bus = CanalystBus(BusConfig(backend=BACKEND_CANALYST, channel=1), dll)
        bus.open()
        self.assertIn('01900012BC0', bus.info)
        self.assertIn('V9.00', bus.info)
        def transmit(device, index, channel, buffer, count):
            self.assertEqual((device, channel, count), (4, 1, 1))
            self.assertEqual(buffer[0].id, 0x18F00400)
            self.assertEqual(buffer[0].extended, 1)
            self.assertEqual(bytes(buffer[0].data), bytes(range(8)))
            return count
        dll.VCI_Transmit.callback = transmit
        frame = CanFrame(frame_id=0x18F00400, extended=True, data=bytes(range(8)), dlc=8)
        self.assertEqual(bus.transmit([frame]), 1)
        def receive(device, index, channel, buffer, count, wait):
            buffer[0].id = 0x123
            buffer[0].length = 2
            buffer[0].data[0:2] = [1, 2]
            return 1
        dll.VCI_Receive.callback = receive
        records = bus.receive()
        self.assertEqual(records[0][0].payload, b'\x01\x02')
        self.assertIsNone(records[0][1])
        dll.VCI_Transmit.callback = lambda *args: 0
        with self.assertRaises(ZlgError):
            bus.transmit([frame])
        bus.close()
        self.assertFalse(bus.is_open)

    def test_failed_init_closes_device(self):
        dll = self.fake()
        closed = []
        dll.VCI_CloseDevice.callback = lambda *args: closed.append(args) or 1
        dll.VCI_InitCAN.callback = lambda *args: 0
        bus = CanalystBus(BusConfig(backend=BACKEND_CANALYST), dll)
        with self.assertRaises(ZlgError):
            bus.open()
        self.assertFalse(bus.is_open)
        self.assertEqual(len(closed), 1)

    def test_device_panel(self):
        from PySide6.QtWidgets import QApplication
        from app.ui.device_panel import DevicePanel
        app = QApplication.instance() or QApplication([])
        panel = DevicePanel()
        panel.set_config(BusConfig(backend=BACKEND_CANALYST, channel=1))
        self.assertEqual(panel.config().backend, BACKEND_CANALYST)
        self.assertEqual(panel.config().channel, 1)
        self.assertFalse(panel.channel.model().item(2).isEnabled())
        panel.close()

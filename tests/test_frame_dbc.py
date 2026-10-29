import os
import struct
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cantools
from PySide6.QtWidgets import QApplication

from app.ui.measurement_panel import MeasurementPanel


DBC_PATH = Path(__file__).resolve().parents[1] / "outputs/struofview/STRUOFVIEW_2022-10-26_ENGINE_DRAFT.dbc"


class FrameDbcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = cantools.database.load_file(str(DBC_PATH), encoding="gbk", strict=True)
        cls.application = QApplication.instance() or QApplication([])

    def decode(self, name, payload):
        return self.database.get_message_by_name(name).decode(payload, decode_choices=False)

    def test_engine_physical_values(self):
        values = self.decode("ENGINE", struct.pack("<HBBHH", 1500, 80, 60, 90, 200))
        self.assertEqual(values, dict(N=1500, Torque=80, ENGINE_n=60, Vspeed=90, QinjLperH=10))

    def test_sensor_byte_layouts(self):
        values = self.decode("SENSOR1", bytes([120, 120, 65, 0, 125, 0, 130, 0]))
        self.assertEqual((values["TDEF"], values["LDEF"], values["CDEF"]), (25, 50, 32.5))
        values = self.decode("SENSOR2", bytes([10, 2, 0x34, 0x12, 125, 0, 0x56, 0x78]))
        self.assertEqual(values["PRESS_AD"], 10)
        self.assertEqual(values["supply_dc_voltage_level"], 2)
        self.assertEqual(values["PRESS_Value"], 0x1234)
        self.assertEqual(values["NH3_Load_low16"], 0x5678)
        values = self.decode("SENSOR4", bytes([12, 0, 0x34, 0, 0x12, 0x78, 0x56, 0]))
        self.assertEqual(values["NOxD_LowByte"] + 256 * values["NOxD_HighByte"], 0x1234)
        self.assertEqual(values["NOxU"], 0x5678)

    def test_signed_temperatures_and_big_endian_cvn(self):
        values = self.decode("Vehicle_PARA3", bytes([44, 236, 246, 0x12, 0x34, 0x56, 0x78, 0]))
        self.assertEqual(values["Turb_Pressure_low8"], 44)
        self.assertEqual(values["InterCool_Temp"], -20)
        self.assertEqual(values["Cool_Temp"], -10)
        self.assertEqual(values["APP_FAI_CVN"], 0x12345678)
        values = self.decode("Vehicle_PARA4", struct.pack("<hhhh", -273, -40, 123, 450))
        self.assertEqual([values[name] for name in ("DOC_EGTU", "DPF_EGTU", "SCR_EGTU", "SCR_EGTD")],
                         [-273, -40, 123, 450])

    def test_pump_state_and_new_adc_frames(self):
        values = self.decode("WORK2_DOSER", struct.pack("<HHBBH", 100, 200, 5, 0, 300))
        self.assertEqual(values["Pumpstate_doser"], 5)
        self.assertEqual(values["T3_EOBD_selected_pump"], 300)
        values = self.decode("SCREFF3", struct.pack("<HHHH", 100, 200, 300, 4095))
        self.assertEqual([values[f"ADC_Channel_{channel}"] for channel in range(8, 12)],
                         [100, 200, 300, 4095])

    def test_application_import_keeps_all_messages_and_signals(self):
        panel = MeasurementPanel()
        panel.load_dbc(str(DBC_PATH))
        self.assertEqual(len(panel._messages), 44)
        self.assertEqual(len(panel._cards), 169)
        self.assertTrue(all(message.is_extended_frame for message in self.database.messages))
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()

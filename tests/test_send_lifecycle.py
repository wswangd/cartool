import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app import signals
from app.bus import BACKEND_SIM, BusConfig
from app.ui.main_window import MainWindow
from app.ui.mapping_dialog import MappingDialog


class SendLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(MainWindow, "_load_settings"), patch(
                "app.ui.main_window.signals.load_signals", side_effect=signals.default_signals):
            self.window = MainWindow()
        self.window._worker = Mock()
        self.window._set_connected(True)

    def tearDown(self):
        self.window._teardown()
        self.window.signal_panel._emit_timer.stop()
        self.window.deleteLater()

    def start_signals(self):
        self.window.signal_panel._columns[0].check.setChecked(True)
        self.window.signal_panel._on_start_clicked()
        self.assertTrue(self.window._signal_uids)
        self.assertFalse(self.window.simulate_panel.import_button.isEnabled())

    def test_disconnect_unlocks_import_and_reconnect_does_not_send(self):
        self.start_signals()
        self.window._on_disconnect()
        self.assertFalse(self.window.signal_panel._running)
        self.assertFalse(self.window._signal_uids)
        self.assertTrue(self.window.simulate_panel.import_button.isEnabled())
        self.assertFalse(self.window.signal_panel.start_button.isEnabled())
        self.assertFalse(self.window.simulate_panel.start_button.isEnabled())
        self.window.device_panel.set_config(BusConfig(backend=BACKEND_SIM))
        worker = Mock()
        bus = Mock(info="test")
        with patch("app.ui.main_window.create_bus", return_value=bus), patch(
                "app.ui.main_window.BusWorker", return_value=worker):
            self.window._on_connect()
        self.assertTrue(self.window.signal_panel.start_button.isEnabled())
        self.assertTrue(self.window.simulate_panel.start_button.isEnabled())
        worker.start.assert_called_once()
        worker.start_periodic.assert_not_called()
        self.assertTrue(self.window.simulate_panel.import_button.isEnabled())

    def test_disconnect_stops_simulation_and_unlocks_both_panels(self):
        simulator = self.window.simulate_panel
        simulator._csv_rows = [{"value": 1}]
        simulator._on_start_clicked()
        self.assertTrue(simulator.is_playing())
        self.assertFalse(self.window.signal_panel.start_button.isEnabled())
        self.window._on_disconnect()
        self.assertFalse(simulator.is_playing())
        self.assertFalse(simulator._playback_timer.isActive())
        self.assertTrue(simulator.import_button.isEnabled())
        self.assertFalse(self.window.signal_panel.start_button.isEnabled())
        self.assertFalse(self.window.simulate_panel.start_button.isEnabled())
        self.assertTrue(self.window.signal_panel.mapping_button.isEnabled())

    def test_disabling_all_signals_stops_and_unlocks_import(self):
        self.start_signals()
        for column in self.window.signal_panel._columns:
            column.check.setChecked(False)
        self.window.signal_panel._emit_values()
        self.assertFalse(self.window._signal_uids)
        self.assertFalse(self.window.signal_panel._running)
        self.assertTrue(self.window.simulate_panel.import_button.isEnabled())

    def test_external_unlock_during_playback_does_not_enable_import(self):
        simulator = self.window.simulate_panel
        simulator._csv_rows = [{"value": 1}]
        simulator._on_start_clicked()
        simulator.set_enabled(True)
        self.assertFalse(simulator.import_button.isEnabled())
        simulator.stop()
        self.assertTrue(simulator.import_button.isEnabled())

    def test_signal_labels(self):
        index = self.window.tabs.indexOf(self.window.signal_panel)
        self.assertEqual(self.window.tabs.tabText(index), "信号发送")
        self.assertEqual(self.window.signal_panel.mapping_button.text(), "信号配置")
        dialog = MappingDialog(signals.default_signals(), {}, self.window)
        self.assertEqual(dialog.windowTitle(), "信号配置")
        dialog.deleteLater()

    def test_simulation_can_select_deselect_and_resume_while_playing(self):
        panel = self.window.signal_panel
        for column in panel._columns:
            column.check.setChecked(False)
        first, second = panel._columns[:2]
        second.sig.can_id = 0x601
        simulator = self.window.simulate_panel
        simulator.row_period.setValue(50)
        first_value = min(first.sig.maximum, first.sig.default + first.sig.factor * 10)
        simulator._csv_rows = [{first.sig.name: first_value, second.sig.name: second.sig.default}]
        simulator._on_start_clicked()
        self.assertTrue(simulator.is_playing())
        self.assertFalse(self.window._signal_uids)
        self.assertAlmostEqual(first.phys_value(), first_value)
        first.check.setChecked(True)
        self.assertEqual(len(self.window._signal_uids), 1)
        arguments = self.window._worker.start_periodic.call_args.args
        self.assertEqual(arguments[2:], (50, 0))
        self.assertEqual(arguments[1].data, panel.current_frames()[0].data)
        second.check.setChecked(True)
        self.assertEqual(len(self.window._signal_uids), 2)
        self.assertEqual(self.window._worker.start_periodic.call_args.args[2:], (50, 0))
        first.check.setChecked(False)
        second.check.setChecked(False)
        self.assertFalse(self.window._signal_uids)
        self.assertTrue(simulator.is_playing())
        first.check.setChecked(True)
        self.assertEqual(len(self.window._signal_uids), 1)
        self.assertEqual(self.window._worker.start_periodic.call_args.args[2:], (50, 0))

    def test_each_simulation_row_updates_frames_without_waiting_for_debounce(self):
        panel = self.window.signal_panel
        for column in panel._columns:
            column.check.setChecked(False)
        first = panel._columns[0]
        first.check.setChecked(True)
        simulator = self.window.simulate_panel
        simulator._csv_rows = [{first.sig.name: first.sig.default}]
        simulator._on_start_clicked()
        self.window._worker.update_frame.reset_mock()
        value = min(first.sig.maximum, first.sig.default + first.sig.factor * 20)
        self.window._on_simulate_row({first.sig.name: value})
        self.assertAlmostEqual(first.phys_value(), value)
        self.window._worker.update_frame.assert_called()
        self.assertEqual(self.window._worker.update_frame.call_args.args[1].data,
                         panel.current_frames()[0].data)
        panel._emit_timer.stop()
        panel._schedule_emit()
        timer_id = panel._emit_timer.timerId()
        panel._schedule_emit()
        self.assertEqual(panel._emit_timer.timerId(), timer_id)

    def test_checking_signal_while_idle_does_not_start_sending(self):
        panel = self.window.signal_panel
        for column in panel._columns:
            column.check.setChecked(False)
        panel._columns[0].check.setChecked(True)
        self.window._worker.start_periodic.assert_not_called()


if __name__ == "__main__":
    unittest.main()

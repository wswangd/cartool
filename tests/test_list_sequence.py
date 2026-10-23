import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.bus import BusConfig, SimBus
from app.frame import CanFrame
from app.models import COL_REPEAT
from app.ui.list_panel import ListPanel
from app.worker import BusWorker


class ListSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bus = SimBus(BusConfig())
        self.bus.open()
        self.worker = BusWorker(self.bus)
        self.tasks = [(index + 1, CanFrame(frame_id=256 + index, data=b'\x01'),
                       period, 1) for index, period in enumerate((10, 20, 30))]

    def test_sequence_order_intervals_and_completion(self):
        completed = []
        self.worker.sequence_done.connect(lambda: completed.append(True))
        with patch('app.worker.time.perf_counter', return_value=0):
            self.worker.start_sequence(self.tasks, 2, 10, 50)
        for current in (0, .01, .02, .07, .08, .09):
            with patch('app.worker.time.perf_counter', return_value=current):
                self.worker._run_sequence(current + 1e-9)
                self.worker._run_sequence(current + .001)
        self.assertEqual([frame.frame_id for frame, _, _ in self.bus.receive()],
                         [256, 257, 258, 256, 257, 258])
        self.assertEqual(completed, [True])

    def test_snapshot_and_stop(self):
        self.worker.start_sequence(self.tasks)
        self.tasks[0][1].frame_id = 999
        self.worker._run_sequence(float('inf'))
        self.assertEqual(self.bus.receive()[0][0].frame_id, 256)
        self.worker.stop_sequence()
        self.worker._run_sequence(float('inf'))
        self.assertEqual(self.bus.receive(), [])

    def test_live_sequence_selection_preserves_next_and_pauses_when_empty(self):
        self.worker.start_sequence(self.tasks)
        self.worker._run_sequence(float('inf'))
        self.worker.update_sequence_entries(self.tasks[1:])
        self.worker._run_sequence(float('inf'))
        self.assertEqual([frame.frame_id for frame, _, _ in self.bus.receive()], [256, 257])
        self.worker.update_sequence_entries([])
        self.worker._run_sequence(float('inf'))
        self.assertEqual(self.bus.receive(), [])
        self.worker.update_sequence_entries(self.tasks)
        self.worker._run_sequence(float('inf'))
        self.assertEqual(self.bus.receive()[0][0].frame_id, 256)

    def test_panel_modes_and_state(self):
        panel = ListPanel()
        panel.add_frame(self.tasks[0][1], period_ms=10)
        panel.set_bus_enabled(True)
        panel.send_mode.setCurrentIndex(1)
        editor = panel.table.indexWidget(panel.model.index(0, COL_REPEAT))
        self.assertFalse(editor.isEnabled())
        started = []
        panel.start_sequence.connect(
            lambda tasks, count, interval, gap: started.append((tasks, count, interval, gap)))
        panel.loop_count.setValue(2)
        panel.interval_ms.setValue(10)
        panel.cycle_interval_ms.setValue(50)
        panel.model.item(0).period_ms = 0
        panel.start_button.click()
        self.assertEqual(started[0][1], 2)
        self.assertEqual(started[0][2:], (10, 50))
        self.assertTrue(panel.start_button.isEnabled())
        self.assertEqual(panel.start_button.text(), '停止')
        self.assertTrue(panel.table.isEnabled())
        self.assertFalse(panel.model.flags(panel.model.index(0, 1)) & Qt.ItemIsEditable)
        panel.sequence_finished()
        self.assertEqual(panel.start_button.text(), '发送')
        self.assertTrue(panel.start_button.isEnabled())
        self.assertTrue(panel.table.isEnabled())
        panel.send_mode.setCurrentIndex(0)
        panel.model.item(0).period_ms = 10
        self.assertTrue(editor.isEnabled())
        parallel = []
        panel.start_tasks.connect(parallel.append)
        panel._on_start_periodic()
        self.assertEqual(len(parallel), 1)
        panel.start_button.click()
        self.assertEqual(panel.start_button.text(), '发送')
        self.assertTrue(panel.start_button.isEnabled())


if __name__ == '__main__':
    unittest.main()

import unittest

from app.bus import BusConfig, SimBus
from app.frame import CanFrame, TraceRecord, RX, TX
from app.worker import BusWorker
from app.models import TraceModel
from PySide6.QtCore import Qt


class TraceTimingTests(unittest.TestCase):
    def test_columns_and_cached_display(self):
        model = TraceModel()
        record = TraceRecord.make(TX, CanFrame(0x100), 1)
        model.append([record])
        self.assertEqual(model.columnCount(), 9)
        self.assertEqual(model.headerData(8, Qt.Horizontal), "数据(hex)")
        self.assertEqual(model.data(model.index(0, 0)), "1")
        cached = record._display_text
        self.assertEqual(model.data(model.index(0, 6)), "0x00000100")
        self.assertIs(record._display_text, cached)

    def test_queue_preserves_per_frame_clocks(self):
        worker = BusWorker(SimBus(BusConfig()))
        records = [
            TraceRecord.make(TX, CanFrame(0x100), 1, monotonic_ns=1000000),
            TraceRecord.make(TX, CanFrame(0x101), 2, monotonic_ns=2000000),
            TraceRecord.make(TX, CanFrame(0x100), 3, monotonic_ns=6500000),
            TraceRecord.make(RX, CanFrame(0x100), 4, 10000, monotonic_ns=7000000),
            TraceRecord.make(RX, CanFrame(0x100), 5, 11250, monotonic_ns=7000000),
            TraceRecord.make(RX, CanFrame(0x100), 6, 100, monotonic_ns=8000000),
        ]
        worker._queue_records(records)
        self.assertEqual(worker._pending_records, records)
        self.assertEqual(records[2].monotonic_ns, 6500000)
        self.assertEqual(records[4].dev_timestamp, 11250)

    def test_ui_batching_preserves_every_record_and_original_time(self):
        worker = BusWorker(SimBus(BusConfig()))
        batches = []
        worker.records.connect(batches.append)
        records = [TraceRecord.make(TX, CanFrame(0x100), index + 1,
                                   monotonic_ns=index * 5000000,
                                   wall_time_ns=1700000000000000000 + index * 5000000)
                   for index in range(4)]
        worker._last_records = 1
        worker._queue_records(records)
        worker._emit_records(1.01)
        self.assertEqual(batches, [])
        worker._emit_records(1.051)
        self.assertEqual(batches, [records])
        self.assertTrue(records[1].time_text.endswith(".005000"))
        worker._queue_records([TraceRecord.make(TX, CanFrame(0x101), 5)])
        worker._emit_records(1.052, force=True)
        self.assertEqual(len(batches), 2)

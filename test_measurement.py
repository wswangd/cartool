import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QMimeData, QPointF, QRectF, Qt
from unittest.mock import Mock, patch

from app.frame import CanFrame, TraceRecord, RX, TX
from app.ui.measurement_panel import MeasurementPanel


DBC = '''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 256 Engine: 8 ECU
 SG_ Speed : 0|16@1+ (0.1,0) [0|6553.5] "rpm" ECU
 SG_ Temp : 23|16@0- (0.5,-40) [-16424|16343.5] "UNIT" ECU
'''


class MeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "test.dbc"
        self.panel = MeasurementPanel()

    def tearDown(self):
        self.panel.set_running(False)
        self.panel.deleteLater()
        self.folder.cleanup()

    def load(self, unit="°C", encoding="utf-8", extra="", select=True):
        self.path.write_bytes((DBC.replace("UNIT", unit) + extra).encode(encoding))
        self.panel.load_dbc(str(self.path))
        if select:
            for item in self.panel._items.values():
                item.setCheckState(0, Qt.Checked)

    def card(self, name="Speed"):
        return self.panel._cards[(256, False), name]

    def test_unit_encodings(self):
        for encoding, unit in (("utf-8", "°C"), ("gb18030", "摄氏度"), ("cp1252", "°C")):
            with self.subTest(encoding=encoding):
                self.load(unit, encoding)
                self.assertEqual(self.card("Temp").unit, unit)

    def test_explicit_encoding(self):
        self.panel.encoding_combo.setCurrentIndex(3)
        self.load("µm", "cp1252")
        self.assertEqual(self.card("Temp").unit, "µm")

    def test_out_of_bounds_signals_are_skipped(self):
        self.load(extra=' SG_ Bad : 60|16@1+ (1,0) [0|65535] "" ECU\n'
                        ' SG_ BadMotorola : 56|2@0+ (1,0) [0|3] "" ECU\n')
        self.assertEqual(len(self.panel._cards), 2)
        self.assertIn("Engine.Bad", self.panel.status.toolTip())
        self.assertIn("Engine.BadMotorola", self.panel.status.toolTip())
        self.decode()

    def decode(self):
        self.panel.set_connected(True)
        self.panel._toggle()
        frame = CanFrame(256, bytes.fromhex("D2 04 FF EC 00 00 00 00"))
        self.panel.add_records([TraceRecord.make(RX, frame, 1)])
        self.panel._flush()
        self.assertEqual(self.card().value_label.text(), "123.4")
        self.assertEqual(self.card("Temp").value_label.text(), "-50")

    def test_decode(self):
        self.load()
        self.decode()

    def test_invalid_import_preserves_previous(self):
        self.load()
        self.path.write_text("not a dbc", encoding="utf-8")
        with self.assertRaises(Exception):
            self.panel.load_dbc(str(self.path))
        self.assertEqual(len(self.panel._cards), 2)

    def test_short_frame_and_tx(self):
        self.load()
        self.panel.set_connected(True)
        self.panel._toggle()
        self.panel.add_records([TraceRecord.make(TX, CanFrame(256, bytes(8)), 1)])
        self.panel._flush()
        self.assertEqual(self.card().value_label.text(), "—")
        self.panel.add_records([TraceRecord.make(RX, CanFrame(256, b"\x00"), 2)])
        self.panel._flush()
        self.assertEqual(self.panel._errors, 1)
        self.panel.set_connected(False)
        self.assertFalse(self.panel._running)

    def test_refresh_period_and_cards(self):
        self.assertEqual(self.panel.update_period.value(), 200)
        self.assertEqual(self.panel._timer.interval(), 200)
        self.assertFalse(hasattr(self.panel, "table"))
        self.load()
        self.decode()
        self.panel.update_period.setValue(500)
        self.assertEqual(self.panel._timer.interval(), 500)
        self.assertTrue(self.panel._timer.isActive())
        self.assertIn("500ms", self.panel.status.text())

    def test_independent_expandable_waveforms(self):
        self.load()
        speed = ((256, False), "Speed")
        temperature = ((256, False), "Temp")
        self.assertIsNone(self.card().scope)
        self.assertIsNone(self.card("Temp").scope)
        self.card().expand_button.click()
        self.card("Temp").expand_button.click()
        self.assertFalse(self.card().scope.isHidden())
        self.assertFalse(self.card("Temp").scope.isHidden())
        self.assertEqual(self.card().expand_button.arrowType(), Qt.UpArrow)
        self.decode()
        self.assertEqual(self.card().scope.samples[-1][1], 123.4)
        self.assertEqual(self.card("Temp").scope.samples[-1][1], -50)
        self.assertEqual(self.card("Temp").scope.unit, "°C")
        self.card().expand_button.click()
        self.assertIsNone(self.card().scope)
        self.assertFalse(self.card("Temp").scope.isHidden())
        frame = CanFrame(256, bytes.fromhex("D2 04 FF EC 00 00 00 00"))
        self.panel.add_records([TraceRecord.make(RX, frame, 2)])
        self.panel._flush()
        self.assertIsNone(self.card().scope)
        self.assertEqual(len(self.card("Temp").scope.samples), 2)
        self.card().expand_button.click()
        self.assertEqual(len(self.card().scope.samples), 0)
        self.panel.add_records([TraceRecord.make(RX, frame, 3)])
        self.panel._flush()
        self.assertEqual(len(self.card().scope.samples), 1)
        self.panel.search.setText("nothing")
        self.assertTrue(self.card().isHidden())
        self.assertTrue(self.card("Temp").isHidden())
        self.panel.search.clear()
        self.assertFalse(self.card().isHidden())
        self.assertFalse(self.card("Temp").isHidden())
        self.panel._items[speed].setCheckState(0, Qt.Unchecked)
        self.assertTrue(self.card().isHidden())
        self.panel._items[speed].setCheckState(0, Qt.Checked)
        self.assertFalse(self.card().isHidden())
        self.panel.set_running(False)
        self.assertEqual(len(self.card().scope.samples), 1)
        self.panel.set_running(True)
        self.assertFalse(self.card().scope.samples)
        self.assertFalse(self.card("Temp").scope.samples)

    def test_waveform_bounds_step_and_render(self):
        self.load()
        self.card().expand_button.click()
        scope = self.card().scope
        for index in range(3000):
            scope.append(index / 1000, index)
        self.assertLessEqual(len(scope.samples), 2000)
        scope.append(30, 42)
        self.assertEqual(len(scope.samples), 1)
        scope.append(31, float("nan"))
        self.assertEqual(len(scope.samples), 1)
        scope.append(31, 10)
        points = scope._step_points(QRectF(0, 0, 100, 100), 0, 50)
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0].y(), points[1].y())
        self.assertEqual(points[1].x(), points[2].x())
        self.panel.resize(1100, 650)
        self.panel.show()
        self.application.processEvents()
        self.assertFalse(self.panel.grab().isNull())
        self.load()
        self.assertIsNone(self.card().scope)
        self.assertFalse(self.card().expand_button.isChecked())

    def test_responsive_independent_columns(self):
        self.load(extra=' SG_ Extra : 32|8@1+ (1,0) [0|255] "" ECU\n')
        self.panel.resize(1200, 600)
        self.panel.show()
        for index in range(4):
            self.application.processEvents()
        self.assertEqual(self.panel._layout_keys[0], 2)
        speed = self.card()
        temperature = self.card("Temp")
        extra = self.card("Extra")
        self.assertNotEqual(speed.parentWidget(), temperature.parentWidget())
        self.assertEqual(speed.parentWidget(), extra.parentWidget())
        initial_y = temperature.y()
        speed.expand_button.click()
        self.application.processEvents()
        self.assertEqual(temperature.y(), initial_y)
        self.assertEqual(speed.scope.height(), 120)
        self.assertGreater(extra.y(), speed.y() + 120)
        self.panel.splitter.setSizes([700, 450])
        for index in range(4):
            self.application.processEvents()
        self.assertEqual(self.panel._layout_keys[0], 1)
        self.assertEqual(speed.parentWidget(), temperature.parentWidget())
        self.assertEqual(self.panel.scroll.horizontalScrollBar().maximum(), 0)
        self.assertFalse(hasattr(self.panel, "selector_button"))
        self.assertIs(self.panel.search.parentWidget(), self.panel.selector_panel)
        self.assertIs(self.panel.tree.parentWidget(), self.panel.selector_panel)
        self.assertLess(self.panel.search.geometry().bottom(), self.panel.tree.y())

    def test_long_name_does_not_overlap_value(self):
        from app.ui.measurement_card import MeasurementCard

        card = MeasurementCard("VeryLongSignalName" * 8, "km/h", "Full description")
        card.resize(350, card.sizeHint().height())
        card.set_value("123456.789", True)
        card.show()
        self.application.processEvents()
        self.assertLessEqual(card.name_label.geometry().right(), card.unit_label.x())
        self.assertLessEqual(card.unit_label.geometry().right(), card.value_label.x())
        self.assertLessEqual(card.value_label.geometry().right(), card.expand_button.x())
        self.assertEqual(card.unit_label.text(), "km/h")
        self.assertGreaterEqual(card.unit_label.width(), card.unit_label.sizeHint().width())
        self.assertIn("…", card.name_label.display_text())
        self.assertIn("Full description", card.name_label.toolTip())
        card.deleteLater()

    def test_import_defaults_to_no_selection(self):
        self.load(select=False)
        self.assertTrue(all(item.checkState(0) == Qt.Unchecked
                            for item in self.panel._items.values()))
        self.assertTrue(all(card.isHidden() for card in self.panel._cards.values()))
        self.assertFalse(self.panel.empty_label.isHidden())
        self.assertEqual(self.panel.tree.topLevelItem(0).checkState(0), Qt.Unchecked)
        self.panel.set_connected(True)
        self.panel._toggle()
        self.assertFalse(self.panel._running)
        self.panel._items[(256, False), "Speed"].setCheckState(0, Qt.Checked)
        self.assertFalse(self.card().isHidden())
        self.assertTrue(self.card("Temp").isHidden())

    def test_drag_swap_preserves_signal_data_and_filter_order(self):
        self.load()
        source, target = self.card(), self.card("Temp")
        source.expand_button.click()
        source.scope.append(100, 42)
        original = list(self.panel._card_order)
        mime = QMimeData()
        mime.setData(source.MIME_TYPE, str(id(source)).encode("ascii"))
        self.assertTrue(target._accepts_drag(source, mime))
        self.assertFalse(source._accepts_drag(source, mime))
        self.assertFalse(target._accepts_drag(source, QMimeData()))
        event = Mock()
        event.source.return_value = source
        event.mimeData.return_value = mime
        target._layout.activate()
        event.position.return_value = QPointF(target._header.geometry().center())
        target.dragEnterEvent(event)
        self.assertTrue(target.property("dragOver"))
        target.dropEvent(event)
        event.accept.assert_called()
        self.assertFalse(target.property("dragOver"))
        self.assertEqual(self.panel._card_order, list(reversed(original)))
        self.assertEqual(self.panel._layout_keys[1], tuple(reversed(original)))
        self.assertEqual(source.scope.samples[-1][1], 42)
        self.assertTrue(source.expand_button.isChecked())
        self.panel.search.setText("Temp")
        self.panel.search.clear()
        self.assertEqual(self.panel._layout_keys[1], tuple(reversed(original)))
        source.drag_group = object()
        self.assertFalse(target._accepts_drag(source, mime))

    def test_name_restores_after_resize_and_value_updates(self):
        from app.ui.measurement_card import MeasurementCard

        card = MeasurementCard("ECU_EGTD", "°C", "description")
        card.resize(180, card.sizeHint().height())
        card.show()
        self.application.processEvents()
        card.resize(650, card.height())
        card.set_value("-273", True)
        self.application.processEvents()
        self.assertEqual(card.name_label.display_text(), "ECU_EGTD")
        self.assertEqual(card.name_label.text(), "ECU_EGTD")
        self.assertNotEqual(card.value_label.cursor().shape(), Qt.OpenHandCursor)
        event = QEvent(QEvent.MouseButtonPress)
        card.eventFilter(card.value_label, event)
        self.assertIsNone(card._drag_start)
        card.deleteLater()

    def test_whole_card_accepts_swap_from_header(self):
        self.load()
        source, target = self.card(), self.card("Temp")
        target.expand_button.click()
        self.panel.resize(1200, 600)
        self.panel.show()
        self.application.processEvents()
        original = list(self.panel._card_order)
        mime = QMimeData()
        mime.setData(source.MIME_TYPE, str(id(source)).encode("ascii"))
        event = Mock()
        event.source.return_value = source
        event.mimeData.return_value = mime
        event.position.return_value = QPointF(target.scope.geometry().center())
        target.dragEnterEvent(event)
        self.assertTrue(target.property("dragOver"))
        target.dragMoveEvent(event)
        target.dropEvent(event)
        event.ignore.assert_not_called()
        event.accept.assert_called()
        self.assertEqual(self.panel._card_order, list(reversed(original)))

    def test_toolbar_order_and_lazy_scope_release(self):
        self.assertIs(self.panel.toolbar.itemAt(0).widget(), self.panel.file_label)
        self.assertIs(self.panel.toolbar.itemAt(self.panel.toolbar.count() - 1).widget(),
                      self.panel.start_button)
        self.load()
        self.decode()
        self.assertTrue(all(card.scope is None for card in self.panel._cards.values()))
        card = self.card()
        card.expand_button.click()
        self.assertIsNotNone(card.scope)
        card.expand_button.click()
        self.assertIsNone(card.scope)
        self.panel.add_records([TraceRecord.make(RX, CanFrame(256, bytes(8)), 2)])
        self.panel._flush()
        self.assertIsNone(card.scope)
        with patch.object(card.value_label, "setStyleSheet") as stylesheet:
            card.set_value(card.value_label.text(), True)
            stylesheet.assert_not_called()

    def test_unselected_messages_are_not_queued(self):
        self.load()
        self.decode()
        for item in self.panel._items.values():
            item.setCheckState(0, Qt.Unchecked)
        self.panel.add_records([TraceRecord.make(RX, CanFrame(256, bytes(8)), 2)])
        self.assertFalse(self.panel._pending)


if __name__ == "__main__":
    unittest.main()

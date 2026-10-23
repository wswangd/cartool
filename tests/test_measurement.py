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

    def test_western_unit_auto_encoding(self):
        self.load("µs", "cp1252")
        self.assertEqual(self.card("Temp").unit, "µs")

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
        self.assertEqual(self.card().expand_button.arrowType(), Qt.NoArrow)
        self.assertFalse(self.card().expand_button.icon().isNull())
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
        self.assertIsNone(self.card().scope)
        self.panel.set_running(True)
        self.assertIsNone(self.card().scope)
        self.assertFalse(self.card("Temp").scope.samples)

    def test_waveform_time_range_and_left_to_right(self):
        self.load()
        card = self.card()
        card.expand_button.click()
        card._set_time_max(20)
        scope = card.scope
        self.assertEqual(scope._time_tick_text(-20, True), "-20 s")
        self.assertEqual(scope._time_tick_text(0, True), "0 s")
        self.assertEqual(scope._time_tick_text(-10), "-10")
        scope._view_time = None
        scope.append(100, 10)
        scope.append(105, 20)
        points = scope._step_points(QRectF(0, 0, 100, 100), 0, 50)
        self.assertEqual(points[0].x(), 75)
        self.assertEqual(points[-1].x(), 100)
        scope.append(125, 30)
        self.assertEqual(scope._step_points(QRectF(0, 0, 100, 100), 0, 50)[-1].x(), 100)
        card.expand_button.click()
        card.expand_button.click()
        self.assertEqual(card._time_max, 20)
        self.assertEqual(card.scope.WINDOW_SECONDS, 20)
        card.scope.append(200, 10)
        card.scope._view_time = None
        self.assertEqual(card.scope._step_points(QRectF(0, 0, 100, 100), 0, 50)[0].x(), 100)

    def test_waveform_bounds_step_and_render(self):
        self.load()
        self.card().expand_button.click()
        scope = self.card().scope
        self.assertEqual(scope.WINDOW_SECONDS, 300)
        scope.set_window_seconds(10)
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
        self.assertGreater(temperature.x(), speed.x())
        self.assertEqual(speed.x(), extra.x())
        initial_y = temperature.y()
        speed.expand_button.click()
        self.application.processEvents()
        self.assertEqual(temperature.y(), initial_y)
        self.assertGreaterEqual(speed.scope.height(), 120)
        self.assertEqual(extra.x(), temperature.x())
        self.assertGreater(extra.y(), temperature.geometry().bottom())
        self.assertLess(extra.y(), speed.geometry().bottom())
        self.assertFalse(extra.geometry().intersects(speed.geometry()))
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

    def test_card_delete_unchecks_tree_and_can_restore(self):
        from PySide6.QtTest import QTest
        self.load()
        card = self.card()
        item = self.panel._items[(256, False), "Speed"]
        self.assertFalse(hasattr(card, 'enable_check'))
        QTest.keyClick(card, Qt.Key_Delete)
        self.assertEqual(item.checkState(0), Qt.Unchecked)
        self.assertTrue(card.isHidden())
        item.setCheckState(0, Qt.Checked)
        self.assertFalse(card.isHidden())
        card.remove_requested.emit()
        self.assertEqual(item.checkState(0), Qt.Unchecked)

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

    def test_drag_insert_preserves_signal_data_and_filter_order(self):
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
        event.position.return_value = QPointF(target.width()/2, target.height()-1)
        target.dragMoveEvent(event)
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

    def test_card_insert_indicator_four_directions(self):
        self.load()
        card = self.card()
        w,h = card.width(),card.height()
        for point, before, vertical in ((QPointF(1,h/2),True,True),
                                        (QPointF(w-1,h/2),False,True),
                                        (QPointF(w/2,1),True,False),
                                        (QPointF(w/2,h-1),False,False)):
            card._update_insert_position(point)
            self.assertEqual(card._insert_before,before)
            rect = card._insert_mark.geometry()
            self.assertEqual(rect.width()==5,vertical)

    def test_vertical_scale_and_expand_preserve_size_and_font(self):
        from PySide6.QtCore import QSize
        self.load()
        self.panel.resize(1200,700)
        self.panel.show()
        self.application.processEvents()
        card = self.card()
        card.apply_size(QSize(350,36))
        self.application.processEvents()
        before = card.name_label.font().pointSizeF()
        card.apply_size(QSize(350,72))
        self.application.processEvents()
        enlarged = card.name_label.font().pointSizeF()
        self.assertGreater(enlarged,before)
        card.expand_button.click()
        self.application.processEvents()
        self.assertEqual(card.width(),350)
        self.assertGreater(card.height(),72)
        self.assertEqual(card.name_label.font().pointSizeF(),9)
        card.apply_size(QSize(350,320))
        self.application.processEvents()
        card.expand_button.click()
        self.application.processEvents()
        self.assertEqual(card.height(),72)
        card.expand_button.click()
        self.application.processEvents()
        self.assertEqual(card.height(),320)
        self.assertEqual(card.name_label.font().pointSizeF(),9)

    def test_linked_cursor_same_time_across_different_widths_and_groups(self):
        self.load()
        source,target = self.card(),self.card("Temp")
        source.expand_button.click()
        target.expand_button.click()
        for card in (source,target):
            card.scope._view_time = 1000
        source.scope.cursor_moved.emit(850)
        self.assertEqual(self.panel._cursor_time,850)
        for card in (source,target):
            plot = card.scope._cursor_plot()
            self.assertAlmostEqual(card.scope._cursor_x(plot),plot.center().x())
        self.panel._merge_signals(((256,False),'Speed'),((256,False),'Temp'))
        target._merged_plot._view_time = 1000
        self.assertEqual(target._merged_plot._linked_cursor,850)
        plot = target._merged_plot._cursor_plot()
        self.assertAlmostEqual(target._merged_plot._cursor_x(plot),plot.center().x())
        target._merged_plot.cursor_moved.emit(None)
        self.assertIsNone(source.scope._linked_cursor)
        self.assertIsNone(target.scope._linked_cursor)
        self.assertIsNone(target._merged_plot._linked_cursor)

    def test_merged_restore_default_width_matches_single_card(self):
        self.load()
        self.panel.resize(1200,700)
        self.panel.show()
        self.application.processEvents()
        target = self.card('Temp')
        self.panel._merge_signals(((256,False),'Speed'),((256,False),'Temp'))
        self.panel._auto_size_card(target)
        self.application.processEvents()
        self.assertEqual(target.width(),350)
        self.assertGreater(target.height(),160)
        expected = target.size()
        self.panel._auto_size_cards()
        self.application.processEvents()
        self.assertEqual(target.size(),expected)

    def test_collapsed_group_releases_history_but_updates_values(self):
        self.load()
        source,target = self.card(),self.card("Temp")
        self.panel._merge_signals(((256,False),'Speed'),((256,False),'Temp'))
        self.panel.set_connected(True)
        self.panel.set_running(True)
        frame = CanFrame(256,bytes.fromhex('D2 04 FF EC 00 00 00 00'))
        def feed():
            self.panel.add_records([TraceRecord.make(RX,frame,1)])
            self.panel._flush()
        feed()
        self.assertTrue(source.scope.samples)
        target.expand_button.setChecked(False)
        self.assertFalse(source.scope.samples)
        self.assertFalse(target.scope.samples)
        feed()
        self.assertEqual(source.value_label.text(),'123.4')
        self.assertFalse(source.scope.samples)
        target.expand_button.setChecked(True)
        self.assertFalse(source.scope.samples)
        feed()
        self.assertEqual(len(source.scope.samples),1)
        target._wave_checks[1].setChecked(False)
        feed()
        self.assertEqual(len(source.scope.samples),2)

    def test_group_wave_visibility_move_out_and_delete_reset(self):
        self.load()
        source,target = self.card(),self.card("Temp")
        source.expand_button.click()
        source.scope.append(100,42)
        self.panel._merge_signals(((256,False),'Speed'),((256,False),'Temp'))
        target._wave_checks[1].setChecked(False)
        self.assertIn(id(source),target._merged_plot.hidden_cards)
        self.assertEqual(self.panel._items[(256,False),'Speed'].checkState(0),Qt.Checked)
        self.assertEqual(source.scope.samples[-1][1],42)
        target._wave_checks[1].setChecked(True)
        self.assertNotIn(id(source),target._merged_plot.hidden_cards)
        self.panel._move_out_signal(((256,False),'Temp'),source)
        self.assertFalse(self.panel._merge_groups)
        self.assertFalse(source.isHidden())
        source.remove_requested.emit()
        self.panel._items[(256,False),'Speed'].setCheckState(0,Qt.Checked)
        self.assertIsNone(source.scope)
        self.assertFalse(source.expand_button.isChecked())
        self.assertFalse(source._custom_sizes)

    def test_multi_selection_resize_and_auto_size(self):
        from PySide6.QtCore import QSize
        self.load()
        self.panel.resize(1400,900)
        self.panel.show()
        self.application.processEvents()
        source,target = self.card(),self.card("Temp")
        self.panel._select_card(source,False)
        self.panel._select_card(target,True)
        before = target.size()
        source.apply_size(QSize(source.width()+50,source.height()*2))
        self.application.processEvents()
        self.assertEqual(target.height(),before.height()*2)
        self.assertGreater(target.width(),before.width())
        self.panel._auto_size_selected(source)
        self.assertFalse(source._custom_sizes)
        self.assertFalse(target._custom_sizes)

    def test_ctrl_name_click_selection_and_merge(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QMessageBox
        self.load()
        self.panel.resize(1200,700)
        self.panel.show()
        self.application.processEvents()
        source,target = self.card(),self.card("Temp")
        QTest.mouseClick(source.name_label,Qt.LeftButton)
        QTest.mouseClick(target.name_label,Qt.LeftButton,Qt.ControlModifier)
        self.assertEqual(len(self.panel._selected_cards),2)
        self.assertTrue(source.property('selected'))
        self.assertTrue(target.property('selected'))
        QTest.mouseClick(target.name_label,Qt.LeftButton,Qt.ControlModifier)
        self.assertEqual(len(self.panel._selected_cards),1)
        QTest.mouseClick(target.name_label,Qt.LeftButton,Qt.ControlModifier)
        with patch('app.ui.measurement_panel.QMessageBox.question',return_value=QMessageBox.No):
            self.panel._merge_selected_cards()
        self.assertFalse(self.panel._merge_groups)
        with patch('app.ui.measurement_panel.QMessageBox.question',return_value=QMessageBox.Yes):
            self.panel._merge_selected_cards()
        self.assertEqual(len(self.panel._merge_groups),1)
        self.assertEqual(len(self.panel._selected_cards),1)

    def test_center_drop_requests_merge_and_edges_insert(self):
        from PySide6.QtWidgets import QMessageBox
        self.load()
        source,target = self.card(),self.card("Temp")
        mime = QMimeData()
        mime.setData(source.MIME_TYPE,str(id(source)).encode('ascii'))
        event = Mock()
        event.source.return_value = source
        event.mimeData.return_value = mime
        event.position.return_value = QPointF(target.width()/2,target.height()/2)
        with patch('app.ui.measurement_panel.QMessageBox.question',return_value=QMessageBox.No):
            target.dragEnterEvent(event)
            self.assertTrue(target._merge_center)
            target.dropEvent(event)
        self.assertFalse(self.panel._merge_groups)
        with patch('app.ui.measurement_panel.QMessageBox.question',return_value=QMessageBox.Yes):
            target.dropEvent(event)
        self.assertTrue(self.panel._merge_groups)
        self.assertTrue(source.isHidden())
        self.assertIsNotNone(target._merged_plot)

    def test_merge_waveforms_preserves_samples_and_unmerges(self):
        self.load()
        source,target = self.card(),self.card("Temp")
        source.expand_button.click()
        source.scope.append(100,42)
        self.panel._merge_signals(((256,False),'Speed'),((256,False),'Temp'))
        self.assertEqual(source.scope.samples[-1][1],42)
        self.assertEqual(len(target._merged_plot.cards),2)
        self.assertEqual(target.name_label.full_text,target.name)
        self.assertEqual(len(target._merged_values),1)
        restored_sizes = dict(target._premerge_sizes)
        self.panel.resize(1200,800)
        self.panel.show()
        self.application.processEvents()
        row = target._merged_legend.layout().itemAt(0).widget()
        identity = row.layout().itemAt(1).layout()
        name_widget = identity.itemAt(0).widget()
        unit_widget = identity.itemAt(1).widget()
        self.assertGreater(unit_widget.x(),name_widget.geometry().right())

        source.set_value('43',True)
        self.panel._refresh_group_legend(target)
        self.assertEqual(target._merged_values[0][1].text(),'43')
        self.assertIn('#0078D4',target.name_label.styleSheet())
        target._merged_plot._hover_x = 100

        self.panel.resize(1200,800)
        self.panel.show()
        self.application.processEvents()
        self.panel._flush()
        target._merged_plot.grab()
        self.panel._set_time_range(120)
        self.assertEqual(target._merged_plot.WINDOW_SECONDS,120)
        self.panel._unmerge(((256,False),'Temp'))
        self.assertFalse(self.panel._merge_groups)
        self.assertFalse(source.isHidden())
        self.assertIsNone(target._merged_plot)
        self.assertEqual(target._custom_sizes,restored_sizes)
        self.application.processEvents()
        self.assertLess(target.header_widget.y(),12)
        self.assertEqual(source.scope.samples[-1][1],42)

    def test_measurement_card_resize_text_and_waveform(self):
        from PySide6.QtCore import QSize
        self.load()
        self.panel.resize(1200, 700)
        self.panel.show()
        self.application.processEvents()
        card = self.card()
        font_before = card.name_label.font().pointSizeF()
        card.apply_size(QSize(650, 80))
        self.application.processEvents()
        self.assertGreater(card.name_label.font().pointSizeF(), font_before)
        self.assertEqual(card.height(), 80)
        card.expand_button.click()
        card.scope.append(100, 42)
        card.apply_size(QSize(650, 320))
        self.application.processEvents()
        self.assertGreater(card.scope.height(), 200)
        self.assertEqual(card.scope.samples[-1][1], 42)
        self.assertEqual(len(card._grips), 8)
        self.assertGreater(card._grips[4].height(), 100)
        card.expand_button.click()
        self.application.processEvents()
        self.assertEqual(card.height(), 80)

    def test_small_waveforms_stay_inside_card_border(self):
        from PySide6.QtCore import QSize

        self.load()
        self.panel.resize(1200, 700)
        self.panel.show()
        card = self.card()
        card.expand_button.click()
        for width in (350, 260, 220):
            card.apply_size(QSize(width, 160))
            self.application.processEvents()
            card._layout.activate()
            self.assertTrue(card.rect().adjusted(3, 3, -3, -3).contains(card.scope.geometry()),
                            (card.size(), card.scope.geometry()))
        self.panel._merge_signals(((256, False), "Temp"), ((256, False), "Speed"))
        card.apply_size(QSize(220, 160))
        self.application.processEvents()
        card._layout.activate()
        self.assertTrue(card.rect().adjusted(3, 3, -3, -3).contains(card._merged_plot.geometry()),
                        (card.size(), card._merged_plot.geometry()))

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
        event.position.return_value = QPointF(target.width()/2, target.height()-1)
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

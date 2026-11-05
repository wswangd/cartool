import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QCursor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from app.frame import CanFrame, TraceRecord, TX, RX
from app.ui.trace_panel import TracePanel


class TraceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_tx_rx_error_text_is_white_with_and_without_focus(self):
        panel = TracePanel()
        panel.resize(1400, 400)
        records = [TraceRecord.make(direction, CanFrame(0x123, bytes(8)), row + 1)
                   for row, direction in enumerate((TX, RX, RX))]
        records[2].error = True
        panel.add_records(records)
        panel.show()
        self.app.processEvents()
        for row in range(3):
            panel.table.selectRow(row)
            for focus in (panel.table, panel.filter_edit):
                focus.setFocus()
                self.app.processEvents()
                image = panel.table.viewport().grab().toImage()
                for column in (6, 8):
                    rect = panel.table.visualRect(panel.model.index(row, column)).adjusted(5, 3, -5, -3)
                    colors = [image.pixelColor(horizontal, vertical)
                              for vertical in range(rect.top(), rect.bottom())
                              for horizontal in range(rect.left(), rect.right())]
                    white = sum(color.red() > 230 and color.green() > 230 and color.blue() > 230
                                for color in colors)
                    blue = sum(color.blue() > 120 and color.red() < 100 for color in colors)
                    self.assertGreater(blue, len(colors) // 2)
                    self.assertGreater(white, 10)
        panel.table.clearSelection()
        self.app.processEvents()
        self.assertIsNotNone(panel.model.index(0, 6).data(Qt.ForegroundRole))
        panel.table.setMouseTracking(True)
        rect = panel.table.visualRect(panel.model.index(0, 8))
        panel.activateWindow()
        QTest.mouseMove(panel.table.viewport(), panel.table.visualRect(panel.model.index(1, 0)).center())
        self.app.processEvents()
        QTest.mouseMove(panel.table.viewport(), rect.center())
        self.app.processEvents()
        QCursor.setPos(panel.table.viewport().mapToGlobal(rect.center()))
        image = QImage(panel.table.viewport().size(), QImage.Format_RGB32)
        image.fill(Qt.white)
        option = QStyleOptionViewItem()
        option.initFrom(panel.table)
        option.widget = panel.table
        option.rect = rect
        option.state |= QStyle.State_MouseOver
        painter = QPainter(image)
        panel.table.itemDelegate().paint(painter, option, panel.model.index(0, 8))
        painter.end()
        rect = rect.adjusted(5, 3, -5, -3)
        colors = [image.pixelColor(horizontal, vertical)
                  for vertical in range(rect.top(), rect.bottom())
                  for horizontal in range(rect.left(), rect.right())]
        self.assertGreater(sum(color.blue() > 120 and color.red() < 100 for color in colors), len(colors) // 2)
        self.assertGreater(sum(color.red() > 230 and color.green() > 230 and color.blue() > 230
                               for color in colors), 10)
        panel.close()
        panel.deleteLater()

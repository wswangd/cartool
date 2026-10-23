import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QScrollBar

from app.ui.widgets import VerticalScrollBarMenuFilter


class ScrollBarMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_vertical_scrollbar_menu_is_blocked(self):
        menu_filter = VerticalScrollBarMenuFilter()
        for widget, blocked in ((QScrollBar(Qt.Vertical), True),
                                (QScrollBar(Qt.Horizontal), False),
                                (QLineEdit(), False)):
            event = QContextMenuEvent(QContextMenuEvent.Mouse, QPoint())
            self.assertEqual(menu_filter.eventFilter(widget, event), blocked)
            self.assertFalse(menu_filter.eventFilter(widget, QEvent(QEvent.Resize)))

    def test_keyboard_scrolling_still_works(self):
        menu_filter = VerticalScrollBarMenuFilter()
        self.app.installEventFilter(menu_filter)
        try:
            scrollbar = QScrollBar(Qt.Vertical)
            scrollbar.setValue(50)
            QTest.keyClick(scrollbar, Qt.Key_Down)
            self.assertEqual(scrollbar.value(), 51)
            QTest.keyClick(scrollbar, Qt.Key_Up)
            self.assertEqual(scrollbar.value(), 50)
            event = QContextMenuEvent(QContextMenuEvent.Mouse, QPoint())
            self.app.sendEvent(scrollbar, event)
            self.assertTrue(event.isAccepted())
        finally:
            self.app.removeEventFilter(menu_filter)


if __name__ == "__main__":
    unittest.main()

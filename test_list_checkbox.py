import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.ui.list_panel import ListPanel


class CheckboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_clicks_double_click_keyboard_and_lock(self):
        panel = ListPanel()
        panel.resize(1200, 400)
        panel.show()
        panel._on_add()
        self.app.processEvents()
        index = panel.model.index(0, 0)
        rect = panel.table.visualRect(index)
        viewport = panel.table.viewport()
        state = panel.model.item(0).enabled
        for point in [rect.center(), rect.topLeft()+QPoint(3, 3)] * 20:
            QTest.mouseClick(viewport, Qt.LeftButton, pos=point, delay=1)
            state = not state
            self.assertEqual(panel.model.item(0).enabled, state)
        # 原生双击事件序列：第一次 press/release，第二次 double-click/release。
        QTest.mouseClick(viewport, Qt.LeftButton, pos=rect.center())
        self.assertEqual(panel.model.item(0).enabled, not state)
        QTest.mouseDClick(viewport, Qt.LeftButton, pos=rect.center())
        self.assertEqual(panel.model.item(0).enabled, state)
        QTest.mouseRelease(viewport, Qt.LeftButton, pos=rect.center())
        self.assertEqual(panel.model.item(0).enabled, state)
        panel.table.setCurrentIndex(index)
        QTest.keyClick(panel.table, Qt.Key_Space)
        state = not state
        self.assertEqual(panel.model.item(0).enabled, state)
        panel.model.set_sending(True)
        QTest.mouseClick(viewport, Qt.LeftButton, pos=rect.center())
        self.assertEqual(panel.model.item(0).enabled, state)
        panel.close()
        panel.deleteLater()


if __name__ == '__main__':
    unittest.main()

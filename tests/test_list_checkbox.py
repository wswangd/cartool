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
        self.assertEqual(panel.model.item(0).enabled, not state)
        panel.close()
        panel.deleteLater()

    def test_live_selection_can_pause_all_and_resume(self):
        for mode in (0, 1):
            panel = ListPanel()
            panel._on_add()
            panel.model.item(0).period_ms = 100
            panel.model.item(0).repeat = 0
            panel.model.item(0).enabled = True
            panel.set_bus_enabled(True)
            panel.send_mode.setCurrentIndex(mode)
            updates = []
            panel.selection_changed.connect(lambda tasks, sequential: updates.append((tasks, sequential)))
            panel._on_start_periodic()
            index = panel.model.index(0, 0)
            panel.model.setData(index, Qt.Unchecked.value, Qt.CheckStateRole)
            self.assertFalse(panel.running_uids())
            self.assertEqual(panel.start_button.text(), "停止")
            self.assertFalse(panel.send_mode.isEnabled())
            self.assertEqual(updates[-1][1], bool(mode))
            self.assertEqual(updates[-1][0], [] if mode else [
                (panel.model.item(0).uid, panel.model.item(0).frame,
                 panel.model.item(0).period_ms, panel.model.item(0).repeat, False)])
            panel.model.setData(index, Qt.Checked.value, Qt.CheckStateRole)
            self.assertTrue(panel.running_uids())
            panel._on_stop_periodic()
            self.assertEqual(panel.start_button.text(), "发送")
            panel.deleteLater()

    def test_selected_text_uses_white_for_cells_and_editors(self):
        panel = ListPanel()
        panel._on_add()
        panel.table.selectRow(0)
        self.assertIn("QTableView::item:selected { color: white;", panel.table.styleSheet())
        for column in panel._editor_columns:
            editor = panel.table.indexWidget(panel.model.index(0, column))
            self.assertIn("color: #ffffff", editor.styleSheet())
        panel.table.clearSelection()
        for column in panel._editor_columns:
            editor = panel.table.indexWidget(panel.model.index(0, column))
            self.assertEqual(editor.styleSheet(), "")
        panel.deleteLater()


if __name__ == '__main__':
    unittest.main()

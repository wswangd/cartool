import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from app.ui.trace_panel import TracePanel


class TraceGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_growing_statistics_does_not_grow_minimum_width(self):
        panel = TracePanel()
        panel.resize(1100, 400)
        panel.show()
        panel._update_info()
        self.app.processEvents()
        width = panel.minimumSizeHint().width()
        panel._hidden = 999999999
        panel.model._dropped = 999999999
        panel._update_info()
        self.app.processEvents()
        self.assertEqual(panel.minimumSizeHint().width(), width)
        self.assertEqual(panel.width(), 1100)
        self.assertEqual(panel.info_label.toolTip(), panel.info_label.text())
        self.assertLess(panel.filter_edit.minimumWidth(), 240)
        panel.close()
        panel.deleteLater()

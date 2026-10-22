import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QComboBox, QSpinBox
from app.ui.list_panel import ListPanel
from app.models import COL_PERIOD, COL_REPEAT


class ListDeleteEditorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_editors_survive_middle_last_and_multiple_deletes(self):
        panel = ListPanel()
        panel.resize(1300, 500)
        panel.show()
        for _ in range(10):
            panel._on_add()
        self.app.processEvents()
        for rows in ([4], [8], [1, 3, 5], [0]):
            removed_uids = {panel.model.item(r).uid for r in rows}
            panel.model.remove_rows(rows)
            self.app.processEvents()
            for row in range(panel.model.rowCount()):
                self.assertNotIn(panel.model.item(row).uid, removed_uids)
                for col in panel._editor_columns:
                    index = panel.model.index(row, col)
                    self.assertTrue(panel.table.isPersistentEditorOpen(index), (row, col))
                    editor = panel.table.indexWidget(index)
                    self.assertIsInstance(editor, QSpinBox if col in (COL_PERIOD, COL_REPEAT) else QComboBox)
                    self.assertFalse(editor.isHidden())
        panel.close()
        panel.deleteLater()


if __name__ == '__main__':
    unittest.main()

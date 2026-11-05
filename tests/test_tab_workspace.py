import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QVBoxLayout, QWidget
from PySide6.QtTest import QTest
from app.ui.tab_workspace import TabWorkspace, WorkspaceTabBar


class TabWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_blocks_underlying_controls_and_restores_them(self):
        workspace = TabWorkspace()
        page = QWidget()
        checkbox = QCheckBox("control")
        QVBoxLayout(page).addWidget(checkbox)
        workspace.primary.addTab(page, "page")
        workspace.show()
        self.app.processEvents()
        workspace.set_drag_page(page)
        try:
            QTest.mouseClick(checkbox, Qt.LeftButton)
            self.assertFalse(checkbox.isChecked())
            self.assertTrue(self.app.property("workspaceTabDragging"))
        finally:
            workspace.set_drag_page(None)
        QTest.mouseClick(checkbox, Qt.LeftButton)
        self.assertTrue(checkbox.isChecked())
        self.assertFalse(self.app.property("workspaceTabDragging"))
        workspace.close()
        workspace.deleteLater()

    def test_insert_before_after_without_swapping(self):
        workspace = TabWorkspace()
        group = workspace.primary
        pages = [QWidget() for _ in range(5)]
        for index, page in enumerate(pages):
            group.addTab(page, str(index))
        workspace.move_page(pages[0], group, index=4)
        self.assertEqual([group.widget(index) for index in range(5)],
                         [pages[1], pages[2], pages[3], pages[0], pages[4]])
        workspace.move_page(pages[4], group, index=1)
        self.assertEqual([group.widget(index) for index in range(5)],
                         [pages[1], pages[4], pages[2], pages[3], pages[0]])
        workspace.deleteLater()

    def test_drag_from_split_preview_back_to_header_inserts_after(self):
        workspace = TabWorkspace()
        workspace.resize(900, 700)
        group = workspace.primary
        pages = [QWidget() for _ in range(3)]
        for index, page in enumerate(pages):
            group.addTab(page, str(index))
        workspace.show()
        self.app.processEvents()
        mime = QMimeData()
        mime.setData(WorkspaceTabBar.MIME, b"tab")
        workspace.drag_page = pages[0]
        lower = QPoint(100, group.height() - 5)
        enter = QDragEnterEvent(lower, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(group, enter)
        self.assertTrue(enter.isAccepted())
        self.assertGreater(workspace.preview.height(), 100)
        rect = group.tabBar().tabRect(1)
        point = group.tabBar().mapTo(group, QPoint(rect.right() - 2, rect.center().y()))
        move = QDragMoveEvent(point, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(group, move)
        self.assertEqual(workspace.preview.width(), 3)
        drop = QDropEvent(QPointF(point), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(group, drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual([group.widget(index) for index in range(3)], [pages[1], pages[0], pages[2]])
        self.assertEqual(workspace.splitter.count(), 1)
        workspace.drag_page = None
        workspace.close()
        workspace.deleteLater()

    def test_split_merge_and_page_identity(self):
        workspace = TabWorkspace()
        workspace.resize(900, 700)
        pages = [QWidget() for _ in range(5)]
        for index, page in enumerate(pages):
            workspace.primary.addTab(page, str(index))
        workspace.show()
        self.app.processEvents()
        original = workspace.primary
        workspace.move_page(pages[4], original, "below")
        self.app.processEvents()
        self.assertEqual(workspace.splitter.count(), 2)
        lower = workspace.splitter.widget(1)
        self.assertIs(lower.widget(0), pages[4])
        self.assertLessEqual(abs(workspace.splitter.sizes()[0] - workspace.splitter.sizes()[1]), 2)
        workspace.move_page(pages[0], lower)
        self.assertEqual(lower.count(), 2)
        workspace.move_page(pages[4], original)
        workspace.move_page(pages[0], original)
        self.assertEqual(workspace.splitter.count(), 1)
        self.assertEqual(original.count(), 5)
        workspace.close()
        workspace.deleteLater()

    def test_move_primary_last_page_and_drop_regions(self):
        workspace = TabWorkspace()
        workspace.resize(900, 700)
        first, second = QWidget(), QWidget()
        workspace.primary.addTab(first, "first")
        workspace.primary.addTab(second, "second")
        workspace.show()
        self.app.processEvents()
        workspace.move_page(second, workspace.primary, "above")
        upper = workspace.splitter.widget(0)
        workspace.move_page(first, upper)
        self.assertIs(workspace.primary, upper)
        self.assertEqual(workspace.splitter.count(), 1)
        self.app.processEvents()
        self.assertEqual(workspace.destination(upper, QPoint(20, 5)), "merge")
        self.assertEqual(workspace.destination(upper, QPoint(20, upper.height() - 5)), "below")
        workspace.close()
        workspace.deleteLater()

    def test_two_row_limit_and_full_collapse(self):
        workspace = TabWorkspace()
        workspace.resize(900, 700)
        pages = [QWidget() for _ in range(4)]
        for index, page in enumerate(pages):
            page.setMinimumHeight(180)
            workspace.primary.addTab(page, str(index))
        workspace.show()
        self.app.processEvents()
        workspace.move_page(pages[3], workspace.primary, "below")
        lower = workspace.splitter.widget(1)
        workspace.move_page(pages[2], workspace.primary, "below")
        self.assertEqual(workspace.splitter.count(), 2)
        self.assertEqual(lower.count(), 2)
        workspace.move_page(pages[2], lower, "above")
        self.assertEqual(workspace.splitter.count(), 2)
        self.assertIs(workspace.splitter.widget(0).currentWidget(), pages[2])
        workspace.splitter.setSizes([700, 0])
        self.app.processEvents()
        self.assertEqual(workspace.splitter.sizes()[1], 0)
        workspace.splitter.setSizes([350, 350])
        self.app.processEvents()
        self.assertGreater(workspace.splitter.sizes()[1], 0)
        workspace.close()
        workspace.deleteLater()

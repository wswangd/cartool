"""可在上下分组间拖动的功能标签页。"""

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QDrag, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QAbstractItemView, QRubberBand, QStackedWidget, QTabBar, QTabWidget, QToolTip, QVBoxLayout, QWidget

from .widgets import HoverSplitter


class WorkspaceTabBar(QTabBar):
    MIME = "application/x-cartool-workspace-tab"

    def __init__(self, workspace, group):
        super().__init__(group)
        self.workspace = workspace
        self.group = group
        self.origin = None
        self.page = None
        self.setMovable(False)
        self.setAcceptDrops(True)
        self.setDrawBase(False)
        self.setStyleSheet(
            "QTabBar { background-color: palette(window); }"
            "QTabBar::tab { background-color: palette(window); border: 0;"
            " padding: 6px 12px; margin: 0;"
            " border-top-left-radius: 6px; border-top-right-radius: 6px; }"
            "QTabBar::tab:selected { background-color: white; }"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        painter.setRenderHint(QPainter.Antialiasing)
        for index in range(self.count()):
            rect = self.tabRect(index)
            if not rect.intersects(event.rect()):
                continue
            selected = index == self.currentIndex()
            if selected:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("white"))
                painter.drawRoundedRect(rect, 6, 6)
                painter.drawRect(rect.adjusted(0, 6, 0, 0))
            painter.setPen(self.palette().color(
                QPalette.Active, QPalette.WindowText))
            painter.drawText(rect.adjusted(12, 0, -12, 0),
                             Qt.AlignCenter | Qt.TextShowMnemonic, self.tabText(index))


class WorkspaceTabs(QTabWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        painter.fillRect(QRect(0, 0, self.width(), self.tabBar().geometry().bottom() + 1),
                         self.palette().window())


class WorkspaceDragTabBar(WorkspaceTabBar):

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index != self.currentIndex():
            color = self.palette().color(QPalette.Active, QPalette.WindowText)
            for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
                option.palette.setColor(role, color)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.origin = event.position().toPoint()
            index = self.tabAt(self.origin)
            self.page = self.group.widget(index) if index >= 0 else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.origin = None
        self.page = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        position = event.position().toPoint()
        if (self.origin is not None and self.page is not None
                and event.buttons() & Qt.LeftButton
                and (position - self.origin).manhattanLength() >= QApplication.startDragDistance()):
            page = self.page
            self.origin = None
            release = QMouseEvent(QEvent.MouseButtonRelease, event.position(), event.globalPosition(),
                                  Qt.LeftButton, Qt.NoButton, event.modifiers())
            super().mouseReleaseEvent(release)
            self.workspace.set_drag_page(page)
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(self.MIME, b"tab")
            drag.setMimeData(mime)
            try:
                drag.exec(Qt.MoveAction)
            finally:
                self.workspace.set_drag_page(None)
                self.workspace.preview.hide()
            return
        super().mouseMoveEvent(event)


class TabWorkspace(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_page = None
        self.splitter = HoverSplitter(Qt.Vertical)
        self.groups = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)
        self.primary = self.new_group()
        self.preview = QRubberBand(QRubberBand.Rectangle, self)
        self.preview.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)

    def new_group(self):
        if len(self.groups) >= 2:
            raise ValueError("最多支持上下两行标签页")
        group = WorkspaceTabs()
        group.setDocumentMode(True)
        group.setStyleSheet(
            "QTabWidget::pane { border: 0; margin: 0; padding: 0; background-color: white; }"
            "QStackedWidget#qt_tabwidget_stackedwidget { background-color: white; }"
            "QStackedWidget#qt_tabwidget_stackedwidget > QWidget { background-color: white; }"
        )
        group.setTabBar(WorkspaceDragTabBar(self, group))
        stack = group.findChild(QStackedWidget)
        stack.layout().setContentsMargins(0, 0, 0, 0)
        group.setMovable(False)
        group.setAcceptDrops(True)
        group.setMinimumHeight(40)
        self.groups.append(group)
        self.splitter.addWidget(group)
        self.splitter.setCollapsible(self.splitter.indexOf(group), True)
        return group

    def set_drag_page(self, page):
        self.drag_page = page
        QApplication.instance().setProperty("workspaceTabDragging", page is not None)
        if page is not None:
            QToolTip.hideText()
            for widget in self.window().findChildren(QWidget):
                QApplication.sendEvent(widget, QEvent(QEvent.Leave))
        for view in self.window().findChildren(QAbstractItemView):
            view.viewport().update()

    def destination(self, group, position):
        bar = group.tabBar()
        if 0 <= position.y() <= bar.geometry().bottom():
            return "merge"
        content_top = bar.height()
        height = max(1, group.height() - content_top)
        relative = position.y() - content_top
        if relative < height * 0.25:
            return "above"
        if relative > height * 0.75:
            return "below"
        return "merge"

    def insertion_index(self, group, position):
        bar = group.tabBar()
        local = bar.mapFrom(group, position)
        for index in range(bar.count()):
            if local.x() < bar.tabRect(index).center().x():
                return index
        return bar.count()

    def move_page(self, page, target, mode="merge", index=-1):
        source = next((group for group in self.groups if group.indexOf(page) >= 0), None)
        if source is None or target not in self.groups:
            return
        if mode != "merge" and len(self.groups) == 2:
            target = self.splitter.widget(0 if mode == "above" else 1)
            mode = "merge"
        if mode != "merge" and source is target and source.count() == 1:
            return
        source_index = source.indexOf(page)
        title, icon = source.tabText(source_index), source.tabIcon(source_index)
        if mode != "merge":
            position = self.splitter.indexOf(target) + (mode == "below")
            target = self.new_group()
            self.splitter.insertWidget(position, target)
        if source is target and index >= 0 and source_index < index:
            index -= 1
        source.removeTab(source_index)
        target.insertTab(target.count() if index < 0 else index, page, icon, title)
        target.setCurrentWidget(page)
        if source.count() == 0 and len(self.groups) > 1:
            if self.primary is source:
                self.primary = target
            self.groups.remove(source)
            source.hide()
            source.setParent(None)
            source.deleteLater()
        if mode != "merge":
            self.splitter.setSizes([max(1, self.height() // len(self.groups))] * len(self.groups))

    def eventFilter(self, watched, event):
        if (self.drag_page is not None and isinstance(watched, QWidget)
                and (watched is self.window() or self.window().isAncestorOf(watched))
                and event.type() in (QEvent.Enter, QEvent.HoverEnter, QEvent.HoverMove,
                                     QEvent.MouseMove, QEvent.MouseButtonPress,
                                     QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
                                     QEvent.Wheel, QEvent.ToolTip, QEvent.ContextMenu)):
            event.accept()
            return True
        if (self.drag_page is None or not isinstance(watched, QWidget)
                or not self.isAncestorOf(watched)):
            return False
        if event.type() == QEvent.DragLeave:
            self.preview.hide()
            return False
        if event.type() not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        if not event.mimeData().hasFormat(WorkspaceTabBar.MIME):
            return False
        point = watched.mapTo(self, event.position().toPoint())
        target = next((group for group in self.groups
                       if QRect(group.mapTo(self, QPoint()), group.size()).contains(point)), None)
        if target is None:
            return False
        local = target.mapFrom(self, point)
        on_header = 0 <= local.y() <= target.tabBar().geometry().bottom()
        insertion = self.insertion_index(target, local) if on_header else -1
        mode = self.destination(target, local)
        if mode != "merge" and len(self.groups) == 2:
            target = self.splitter.widget(0 if mode == "above" else 1)
            local = target.mapFrom(self, point)
            mode = "merge"
        event.setDropAction(Qt.MoveAction)
        event.accept()
        if event.type() == QEvent.Drop:
            self.preview.hide()
            self.move_page(self.drag_page, target, mode, insertion)
        else:
            rect = QRect(target.mapTo(self, QPoint()), target.size())
            if on_header:
                bar = target.tabBar()
                x = (bar.tabRect(insertion).left() if insertion < bar.count()
                     else bar.tabRect(bar.count() - 1).right() + 1)
                origin = bar.mapTo(self, QPoint(x, 0))
                rect = QRect(max(0, origin.x() - 1), origin.y(), 3, bar.height())
            elif mode == "above":
                rect.setHeight(rect.height() // 2)
            elif mode == "below":
                rect.setTop(rect.top() + rect.height() // 2)
            self.preview.setGeometry(rect)
            self.preview.show()
            self.preview.raise_()
        return True

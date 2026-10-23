import os
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication
from app.signals import default_signals
from app.ui.signal_panel import SignalPanel


class CardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = SignalPanel(default_signals())
        self.panel.resize(1100, 650)
        self.panel.show()
        self.app.processEvents()

    def tearDown(self):
        self.panel._emit_timer.stop()
        self.panel._drag_scroll_timer.stop()
        self.panel.close()
        self.panel.deleteLater()

    def names(self):
        return [c.sig.name for c in self.panel._ordered_columns()]

    def test_reorder_and_persistence(self):
        original = self.names()
        self.panel._move_card(original[2], original[0], True)
        self.assertEqual(self.names()[:3], [original[2], original[0], original[1]])
        settings = self.panel.card_layout_settings()
        self.panel.restore_card_layout(settings)
        self.assertEqual(self.panel.card_layout_settings(), settings)
        self.panel._auto_adjust_cards()
        self.assertEqual(self.names(), settings['order'])

    def test_filtered_move_keeps_hidden_slots(self):
        original = self.names()
        self.panel._columns[1].widget.hide()
        self.panel._move_card(original[2], original[0], True)
        self.assertEqual(self.names()[:3], [original[2], original[1], original[0]])

    def test_corner_resize_ratio_cancel_and_nonoverlap(self):
        card = self.panel._columns[0].widget
        original = card.size()
        grip = card.grips[-1]
        press = Mock()
        press.button.return_value = Qt.LeftButton
        press.globalPosition.return_value = QPointF(100, 100)
        grip.mousePressEvent(press)
        move = Mock()
        move.globalPosition.return_value = QPointF(200, 180)
        grip.mouseMoveEvent(move)
        self.assertAlmostEqual(grip.target_size.width()/grip.target_size.height(),
                               original.width()/original.height(), delta=0.02)
        self.assertEqual(card.size(), original)
        grip._finish(False)
        self.assertFalse(self.panel._card_sizes)
        grip.mousePressEvent(press)
        grip.mouseMoveEvent(move)
        grip.mouseReleaseEvent(press)
        self.assertIn(card.signal_name, self.panel._card_sizes)
        cards = [c.widget for c in self.panel._columns if not c.widget.isHidden()]
        for i, a in enumerate(cards):
            for b in cards[i+1:]:
                self.assertFalse(a.geometry().intersects(b.geometry()))

    def test_enlarged_card_scales_value_and_neighbors_fill_gap(self):
        first = self.panel._columns[0]
        normal = first.widget.size()
        self.panel._card_sizes[first.sig.name] = [round(normal.width()*1.5), round(normal.height()*2)]
        self.panel._layout_cards()
        self.assertGreater(first._value_font_size, 24)
        right = [c.widget.geometry() for c in self.panel._columns[1:]
                 if c.widget.x() > first.widget.geometry().right()]
        self.assertTrue(any(r.top() > first.widget.y() and
                            r.top() < first.widget.geometry().bottom() for r in right))
        self.panel._card_sizes.clear()
        self.panel._layout_cards()
        self.assertEqual(first._value_font_size, 24)

    def test_auto_sizing_scales_numbers_when_viewport_grows(self):
        self.panel._auto_adjust_cards()
        self.app.processEvents()
        before = self.panel._columns[0]._value_font_size
        self.panel.resize(1100, 1400)
        self.app.processEvents()
        self.panel._layout_cards()
        first = self.panel._columns[0]
        self.assertGreater(first._value_font_size, before)

    def test_insert_marker_before_and_after(self):
        card = self.panel._columns[0].widget
        rect = card.geometry()
        self.panel._update_insert_marker(rect.topLeft())
        self.assertEqual(self.panel._insert_target, (card.signal_name, True))
        self.assertFalse(self.panel._insert_marker.isHidden())
        self.panel._update_insert_marker(rect.topRight())
        self.assertEqual(self.panel._insert_target, (card.signal_name, False))


if __name__ == '__main__':
    unittest.main()

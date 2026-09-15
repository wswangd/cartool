"""表格编辑委托：下拉框与数字输入。"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QStyledItemDelegate


class ComboDelegate(QStyledItemDelegate):
    def __init__(self, options: tuple[str, ...] | list[str], parent=None) -> None:
        super().__init__(parent)
        self._options = list(options)

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(self._options)
        combo.setEditable(False)
        return combo

    def setEditorData(self, editor: QComboBox, index) -> None:
        current = index.data()
        editor.setCurrentIndex(self._options.index(current) if current in self._options
                              else 0)

    def setModelData(self, editor: QComboBox, model, index) -> None:
        model.setData(index, editor.currentText())


class SpinDelegate(QStyledItemDelegate):
    def __init__(self, minimum: int, maximum: int, parent=None,
                 special_zero_text: str = "") -> None:
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._zero_text = special_zero_text

    def createEditor(self, parent, option, index):
        from PySide6.QtWidgets import QSpinBox
        spin = QSpinBox(parent)
        spin.setRange(self._min, self._max)
        if self._zero_text:
            spin.setSpecialValueText(self._zero_text)
        return spin

    def setEditorData(self, editor, index) -> None:
        text = str(index.data() or "0").strip()
        try:
            value = int(text)
        except ValueError:
            value = self._min
        editor.setValue(max(self._min, min(self._max, value)))

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, str(editor.value()))

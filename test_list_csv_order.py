import csv
import tempfile
import unittest
from pathlib import Path
from app.frame import CanFrame, SendItem
from app.models import SEND_HEADERS
from app.storage import save_list_csv, load_list_csv


class ListCsvOrderTests(unittest.TestCase):
    def test_export_order_and_enabled_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'list.csv'
            item = SendItem(uid=1, frame=CanFrame(0x101, bytes(8)), enabled=False,
                            note='测试备注', period_ms=85, repeat=3)
            save_list_csv(path, [item])
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0], list(SEND_HEADERS[1:-1]))
            self.assertEqual(rows[1][0], '00000101')
            self.assertEqual(rows[1][-1], item.note)
            result = load_list_csv(path)[0]
            self.assertFalse(result['enabled'])
            self.assertEqual(result['period_ms'], 85)
            self.assertEqual(result['note'], item.note)

    def test_old_note_first_file_still_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.csv'
            path.write_text('备注,ID(hex),帧格式,帧类型,发送类型,长度,数据(hex),周期(ms),次数\n旧文件,101,标准帧,数据帧,正常发送,1,01,50,无限\n', encoding='utf-8-sig')
            result = load_list_csv(path)[0]
            self.assertEqual(result['note'], '旧文件')
            self.assertEqual(result['period_ms'], 50)
            self.assertFalse(result['enabled'])


if __name__ == '__main__':
    unittest.main()

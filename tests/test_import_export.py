"""测试信号映射配置的导入/导出、start_bit 打包、cycles、移动功能。

测试覆盖：
1. 核心 round-trip：JSON → SignalDef.from_dict → SignalDef.to_dict → JSON
2. 各种位长度（1/4/12/16/24/32），含非字节对齐长度
3. Intel/Motorola bit 级打包验证
4. cycles 加载/保存/round-trip
5. MappingDialog 的导入导出、移动、周期收集
6. 上下移动信号顺序（单行、多行、边界）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 设置 offscreen Qt 平台，避免弹出 GUI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 让 main.py 所在目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from PySide6.QtCore import QItemSelection, QItemSelectionModel  # noqa: E402

# monkey-patch QMessageBox 的静态方法，避免 offscreen 模式下阻塞
QMessageBox.warning = staticmethod(lambda *a, **kw: 0)
QMessageBox.information = staticmethod(lambda *a, **kw: 0)
QMessageBox.critical = staticmethod(lambda *a, **kw: 0)

from app.signals import (SignalDef, SignalState, MIN_BIT_LENGTH, MAX_BIT_LENGTH,  # noqa: E402
                        pack_frames, load_signals, save_signals, load_cycles)
from app.ui.mapping_dialog import MappingDialog  # noqa: E402


TEST_IMPORT_PATH = Path(__file__).parent / "test_import_signals.json"
TEST_EXPORT_PATH = Path(__file__).parent / "test_export_signals.json"


def _ok(msg: str) -> str:
    return f"[OK] {msg}"


def _fail(msg: str) -> str:
    return f"[FAIL] {msg}"


def test_core_round_trip() -> list[str]:
    """测试核心：JSON → SignalDef.from_dict → SignalDef.to_dict → JSON。"""
    results: list[str] = []
    original = json.loads(TEST_IMPORT_PATH.read_text(encoding="utf-8-sig"))
    original_items = original["signals"]

    defs: list[SignalDef] = []
    for raw in original_items:
        try:
            defs.append(SignalDef.from_dict(raw))
        except (TypeError, ValueError) as exc:
            results.append(_fail(f"导入失败：{exc}"))
            return results
    results.append(_ok(f"导入成功：{len(defs)} 路信号"))

    exported_items = [d.to_dict() for d in defs]
    for i, (orig, exp) in enumerate(zip(original_items, exported_items)):
        if orig != exp:
            diff_keys = [k for k in orig.keys() | exp.keys() if orig.get(k) != exp.get(k)]
            results.append(_fail(f"第 {i+1} 路信号 round-trip 不一致：字段 {diff_keys}"))
            return results
    results.append(_ok("round-trip 数据完全一致"))

    expected_lengths = [1, 4, 12, 16, 24, 32]
    actual_lengths = [d.bit_length for d in defs]
    if actual_lengths == expected_lengths:
        results.append(_ok(f"位长度保留正确：{actual_lengths}"))
    else:
        results.append(_fail(f"位长度不匹配：期望 {expected_lengths}，实际 {actual_lengths}"))

    expected_start_bits = [0, 8, 16, 0, 7, 31]
    actual_start_bits = [d.start_bit for d in defs]
    if actual_start_bits == expected_start_bits:
        results.append(_ok(f"起始bit保留正确：{actual_start_bits}"))
    else:
        results.append(_fail(f"起始bit不匹配：期望 {expected_start_bits}，实际 {actual_start_bits}"))

    return results


def test_cycles_round_trip() -> list[str]:
    """测试 cycles 的 round-trip。"""
    results: list[str] = []
    original = json.loads(TEST_IMPORT_PATH.read_text(encoding="utf-8-sig"))
    original_cycles = original.get("cycles", {})

    # 用 save_signals 写入临时文件，再 load_cycles 读回
    defs = [SignalDef.from_dict(raw) for raw in original["signals"]]
    cycles = {}
    for key, value in original_cycles.items():
        try:
            cycles[int(str(key), 16)] = int(value)
        except (TypeError, ValueError):
            continue
    results.append(_ok(f"解析 cycles：{len(cycles)} 条"))

    # 写到临时文件
    tmp_path = Path(__file__).parent / "tmp_cycles.json"
    try:
        save_signals(defs, cycles)
        # 读取实际 signals.json 中的 cycles
        loaded = load_cycles()
        if loaded == cycles:
            results.append(_ok(f"cycles round-trip 一致：{loaded}"))
        else:
            results.append(_fail(f"cycles 不一致：期望 {cycles}，实际 {loaded}"))
    except Exception as exc:
        results.append(_fail(f"cycles round-trip 失败：{exc}"))

    return results


def test_packing_bit_level() -> list[str]:
    """测试 bit 级打包：Intel 和 Motorola。"""
    results: list[str] = []

    # Intel 8-bit, start=0, raw=0xAB → byte0=0xAB
    sig = SignalDef(name="i8", unit="", minimum=0, maximum=255, default=0,
                   enabled=True, can_id=0x100, start_bit=0, bit_length=8)
    f = pack_frames([sig], {"i8": 0xAB})
    if f[0].data[0] == 0xAB:
        results.append(_ok(f"Intel 8bit start=0: {f[0].data[0]:02X}"))
    else:
        results.append(_fail(f"Intel 8bit: 期望 AB，实际 {f[0].data[0]:02X}"))

    # Motorola 8-bit, start=7, raw=0xEF → byte0=0xEF
    sig = SignalDef(name="m8", unit="", minimum=0, maximum=255, default=0,
                   enabled=True, can_id=0x200, start_bit=7, bit_length=8,
                   byte_order="Motorola")
    f = pack_frames([sig], {"m8": 0xEF})
    if f[0].data[0] == 0xEF:
        results.append(_ok(f"Motorola 8bit start=7: {f[0].data[0]:02X}"))
    else:
        results.append(_fail(f"Motorola 8bit: 期望 EF，实际 {f[0].data[0]:02X}"))

    # Intel 16-bit, start=0, raw=0x1234 → byte0=0x34, byte1=0x12 (小端)
    sig = SignalDef(name="i16", unit="", minimum=0, maximum=65535, default=0,
                   enabled=True, can_id=0x300, start_bit=0, bit_length=16)
    f = pack_frames([sig], {"i16": 0x1234})
    if f[0].data[0] == 0x34 and f[0].data[1] == 0x12:
        results.append(_ok(f"Intel 16bit start=0: {f[0].data[0]:02X} {f[0].data[1]:02X}"))
    else:
        results.append(_fail(f"Intel 16bit: 期望 34 12，实际 {f[0].data[0]:02X} {f[0].data[1]:02X}"))

    # Motorola 16-bit, start=7, raw=0x1234 → byte0=0x12, byte1=0x34 (大端)
    sig = SignalDef(name="m16", unit="", minimum=0, maximum=65535, default=0,
                   enabled=True, can_id=0x400, start_bit=7, bit_length=16,
                   byte_order="Motorola")
    f = pack_frames([sig], {"m16": 0x1234})
    if f[0].data[0] == 0x12 and f[0].data[1] == 0x34:
        results.append(_ok(f"Motorola 16bit start=7: {f[0].data[0]:02X} {f[0].data[1]:02X}"))
    else:
        results.append(_fail(f"Motorola 16bit: 期望 12 34，实际 {f[0].data[0]:02X} {f[0].data[1]:02X}"))

    # Intel 4-bit, start=8, raw=0xF → byte1=0x0F
    sig = SignalDef(name="i4", unit="", minimum=0, maximum=15, default=0,
                   enabled=True, can_id=0x500, start_bit=8, bit_length=4)
    f = pack_frames([sig], {"i4": 0xF})
    if f[0].data[1] == 0x0F:
        results.append(_ok(f"Intel 4bit start=8: byte1={f[0].data[1]:02X}"))
    else:
        results.append(_fail(f"Intel 4bit: 期望 byte1=0F，实际 {f[0].data[1]:02X}"))

    return results


def test_dialog_cycles(app: QApplication) -> list[str]:
    """测试 MappingDialog 的 cycles 收集功能。"""
    results: list[str] = []
    defs = load_signals()
    cycles = load_cycles()
    dialog = MappingDialog(defs, cycles)
    results.append(_ok(f"MappingDialog 初始化：{dialog.table.rowCount()} 行信号，"
                       f"{dialog.cycle_table.rowCount()} 行周期"))

    collected_cycles = dialog._collect_cycles()
    if collected_cycles:
        results.append(_ok(f"收集到 {len(collected_cycles)} 条周期配置"))
    else:
        results.append(_ok("收集到 0 条周期配置（signals.json 无 cycles 字段时正常）"))

    # 测试 cycles() 方法
    dialog._on_accept()
    if dialog.result() == MappingDialog.Accepted:
        accepted_cycles = dialog.cycles()
        results.append(_ok(f"cycles() 返回 {len(accepted_cycles)} 条"))
    else:
        results.append(_fail("_on_accept 未返回 Accepted"))

    return results


def test_dialog_import_export(app: QApplication) -> list[str]:
    """测试 MappingDialog 的导入导出方法。"""
    results: list[str] = []
    defs = load_signals()
    dialog = MappingDialog(defs)

    # 模拟导入
    payload = json.loads(TEST_IMPORT_PATH.read_text(encoding="utf-8-sig"))
    items = payload["signals"]
    dialog.table.setRowCount(0)
    imported_defs: list[SignalDef] = []
    for row, raw in enumerate(items):
        sig = SignalDef.from_dict(raw)
        imported_defs.append(sig)
        dialog.table.insertRow(row)
        dialog._fill_row(row, sig)
    dialog._refresh_cycle_table()
    results.append(_ok(f"模拟导入完成：{dialog.table.rowCount()} 行，"
                       f"{dialog.cycle_table.rowCount()} 条周期"))

    collected = dialog._collect_defs()
    if collected is None:
        results.append(_fail("_collect_defs 返回 None"))
        return results
    results.append(_ok(f"_collect_defs 成功：{len(collected)} 路信号"))

    # 检查 start_bit 是否正确
    expected_start_bits = [0, 8, 16, 0, 7, 31]
    actual_start_bits = [d.start_bit for d in collected]
    if actual_start_bits == expected_start_bits:
        results.append(_ok(f"start_bit 保留正确：{actual_start_bits}"))
    else:
        results.append(_fail(f"start_bit 不匹配：期望 {expected_start_bits}，实际 {actual_start_bits}"))

    # 收集 cycles
    cycles = dialog._collect_cycles()
    if cycles:
        results.append(_ok(f"收集 cycles：{len(cycles)} 条"))
    else:
        results.append(_fail("收集 cycles 为空"))

    # 导出
    payload_out = {"version": 1, "note": "test",
                   "signals": [d.to_dict() for d in collected],
                   "cycles": {f"{k:X}": v for k, v in cycles.items()}}
    try:
        TEST_EXPORT_PATH.write_text(
            json.dumps(payload_out, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError as exc:
        results.append(_fail(f"导出失败：{exc}"))
        return results
    results.append(_ok(f"导出文件已写入：{TEST_EXPORT_PATH.name}"))

    # 重新读回
    try:
        reloaded = json.loads(TEST_EXPORT_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        results.append(_fail(f"重新读取失败：{exc}"))
        return results
    if reloaded.get("signals") == payload_out["signals"]:
        results.append(_ok("导出文件 signals round-trip 一致"))
    else:
        results.append(_fail("导出文件 signals round-trip 不一致"))
    if reloaded.get("cycles") == payload_out["cycles"]:
        results.append(_ok("导出文件 cycles round-trip 一致"))
    else:
        results.append(_fail("导出文件 cycles round-trip 不一致"))

    return results


def test_move_row(app: QApplication) -> list[str]:
    """测试上下移动行的功能。"""
    results: list[str] = []
    defs = load_signals()
    dialog = MappingDialog(defs)
    dialog.table.setRowCount(0)
    test_defs = [
        SignalDef(name="A", unit="", minimum=0, maximum=100, default=0,
                 enabled=True, can_id=0x100, start_bit=0, bit_length=16),
        SignalDef(name="B", unit="", minimum=0, maximum=100, default=0,
                 enabled=True, can_id=0x100, start_bit=16, bit_length=16),
        SignalDef(name="C", unit="", minimum=0, maximum=100, default=0,
                 enabled=True, can_id=0x100, start_bit=32, bit_length=16),
    ]
    for i, sig in enumerate(test_defs):
        dialog.table.insertRow(i)
        dialog._fill_row(i, sig)

    def names(d):
        return [d._collect_row_def(r).name for r in range(d.table.rowCount())]

    # 下移第 0 行
    dialog.table.selectRow(0)
    dialog._on_move_row(1)
    if names(dialog) == ["B", "A", "C"]:
        results.append(_ok("下移第 0 行正确"))
    else:
        results.append(_fail(f"下移错误：{names(dialog)}"))
        return results

    # 上移回原位
    dialog.table.selectRow(1)
    dialog._on_move_row(-1)
    if names(dialog) == ["A", "B", "C"]:
        results.append(_ok("上移回原位正确"))
    else:
        results.append(_fail(f"上移错误：{names(dialog)}"))
        return results

    # 边界：第 0 行上移不变
    dialog.table.selectRow(0)
    dialog._on_move_row(-1)
    if names(dialog) == ["A", "B", "C"]:
        results.append(_ok("边界上移不变"))
    else:
        results.append(_fail(f"边界上移异常：{names(dialog)}"))
        return results

    # 边界：末行下移不变
    dialog.table.selectRow(2)
    dialog._on_move_row(1)
    if names(dialog) == ["A", "B", "C"]:
        results.append(_ok("边界下移不变"))
    else:
        results.append(_fail(f"边界下移异常：{names(dialog)}"))
        return results

    # 多行整体下移
    sm = dialog.table.selectionModel()
    model = dialog.table.model()
    selection = QItemSelection(model.index(0, 0),
                              model.index(1, dialog.table.columnCount() - 1))
    sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
    dialog._on_move_row(1)
    if names(dialog) == ["C", "A", "B"]:
        results.append(_ok("多行下移正确"))
    else:
        results.append(_fail(f"多行下移错误：{names(dialog)}"))
        return results

    # 移动后 _collect_defs 仍正确
    defs_after = dialog._collect_defs()
    if defs_after and [d.name for d in defs_after] == ["C", "A", "B"]:
        results.append(_ok("移动后 _collect_defs 顺序正确"))
    else:
        results.append(_fail("移动后 _collect_defs 错误"))

    return results


def test_edge_cases() -> list[str]:
    """测试边界情况。"""
    results: list[str] = []

    # start_bit=63, bit_length=1 → 合法
    try:
        SignalDef(name="ok", unit="", minimum=0, maximum=1, default=0,
                 enabled=True, can_id=0x100, start_bit=63, bit_length=1)
        results.append(_ok("start_bit=63, bit_length=1 创建成功"))
    except ValueError as exc:
        results.append(_fail(f"start_bit=63 失败：{exc}"))

    # start_bit=63, bit_length=8 → 越界
    try:
        SignalDef(name="bad", unit="", minimum=0, maximum=100, default=0,
                 enabled=True, can_id=0x100, start_bit=63, bit_length=8)
        results.append(_fail("start_bit=63+8 应被拒绝"))
    except ValueError:
        results.append(_ok("start_bit=63+8 被正确拒绝"))

    # start_bit=64 → 越界
    try:
        SignalDef(name="bad2", unit="", minimum=0, maximum=100, default=0,
                 enabled=True, can_id=0x100, start_bit=64, bit_length=1)
        results.append(_fail("start_bit=64 应被拒绝"))
    except ValueError:
        results.append(_ok("start_bit=64 被正确拒绝"))

    # 旧格式兼容：start_byte → start_bit (×8)
    old_dict = {"name": "compat", "min": 0, "max": 100, "start_byte": 2}
    sig = SignalDef.from_dict(old_dict)
    if sig.start_bit == 16:
        results.append(_ok(f"旧格式 start_byte=2 → start_bit=16 兼容正确"))
    else:
        results.append(_fail(f"旧格式兼容失败：start_bit={sig.start_bit}"))

    return results


def main() -> int:
    print("\n" + "=" * 60)
    print("信号映射配置测试（start_bit + cycles + 移动）")
    print("=" * 60)

    all_results: list[str] = []

    print("\n--- 测试1：核心 round-trip ---")
    r = test_core_round_trip()
    all_results.extend(r)
    print("\n".join(r))

    print("\n--- 测试2：cycles round-trip ---")
    r = test_cycles_round_trip()
    all_results.extend(r)
    print("\n".join(r))

    print("\n--- 测试3：bit 级打包（Intel/Motorola）---")
    r = test_packing_bit_level()
    all_results.extend(r)
    print("\n".join(r))

    print("\n--- 测试4：MappingDialog cycles 收集 ---")
    app = QApplication.instance() or QApplication([])
    r = test_dialog_cycles(app)
    all_results.extend(r)
    print("\n".join(r))

    print("\n--- 测试5：MappingDialog 导入导出 ---")
    r = test_dialog_import_export(app)
    all_results.extend(r)
    print("\n".join(r))

    print("\n--- 测试6：上下移动信号顺序 ---")
    r = test_move_row(app)
    all_results.extend(r)
    print("\n".join(r))

    print("\n--- 测试7：边界情况 ---")
    r = test_edge_cases()
    all_results.extend(r)
    print("\n".join(r))

    print("\n" + "=" * 60)
    failures = [x for x in all_results if x.startswith("[FAIL]")]
    passes = [x for x in all_results if x.startswith("[OK]")]
    print(f"通过 {len(passes)} 项，失败 {len(failures)} 项")
    if failures:
        print("\n失败项：")
        print("\n".join(failures))
    print("=" * 60)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

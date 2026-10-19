#!/usr/bin/env python
"""测试 CSV 导出列顺序"""
from pathlib import Path
from app.frame import CanFrame, TraceRecord
from app.storage import export_trace_csv
import time

# 创建测试报文
frame = CanFrame(
    frame_id=0x123,
    data=b'\x01\x02\x03\x04\x05\x06\x07\x08',
    dlc=8,
    extended=False,
    remote=False,
    send_type=0
)

# 创建测试记录（仿真模式，dev_timestamp=0）
record1 = TraceRecord(
    seq=1,
    wall_time=time.time(),
    direction='TX',
    frame=frame,
    dev_timestamp=0,
    error=False
)

# 创建测试记录（有设备时间戳）
record2 = TraceRecord(
    seq=2,
    wall_time=time.time(),
    direction='RX',
    frame=frame,
    dev_timestamp=123456789,
    error=False
)

# 导出 CSV
output_path = Path('test_csv_export.csv')
export_trace_csv(output_path, [record1, record2])

# 读取并显示内容
print("=" * 60)
print("CSV 导出内容：")
print("=" * 60)
content = output_path.read_text(encoding='utf-8-sig')
print(content)
print("=" * 60)

# 验证列数
lines = content.strip().split('\n')
print(f"\n总行数: {len(lines)}")
for i, line in enumerate(lines):
    cols = line.split(',')
    print(f"第{i+1}行列数: {len(cols)}")
    if i == 0:
        print(f"  表头: {cols}")
    else:
        print(f"  数据: {cols}")

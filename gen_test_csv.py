"""生成 cartool 实验数据模板的大 CSV 测试文件。"""
import csv
import os
import random

NAMES = [
    "转速", "扭矩", "车速", "DPF温度", "上游温度", "下游温度",
    "上游NOX", "下游NOX", "环境温度", "大气压力", "冷却水温",
    "尿素浓度", "尿素温度", "尿素液位", "管路压力", "尿素喷射量", "排气流量",
]

RANGES = [
    (0, 8000), (-125, 125), (0, 251), (0, 1000), (0, 1000), (0, 1000),
    (0, 3012), (0, 3012), (-40, 125), (0, 125), (-40, 210),
    (0, 62.5), (-40, 210), (0, 100), (0, 1500), (0, 10000), (0, 12851),
]

DECIMALS = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1]

ROW_COUNT = 50_000
OUTPUT = "cartool_test_50k.csv"


def main():
    path = os.path.join(os.path.dirname(__file__), OUTPUT)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(NAMES)
        for _ in range(ROW_COUNT):
            row = []
            for (lo, hi), dec in zip(RANGES, DECIMALS):
                row.append(round(random.uniform(lo, hi), dec))
            w.writerow(row)
    size = os.path.getsize(path)
    print(f"已生成 {path}")
    print(f"  行数: {ROW_COUNT:,}")
    print(f"  列数: {len(NAMES)}")
    print(f"  大小: {size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()

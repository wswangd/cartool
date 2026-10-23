"""验证 WLTC CSV 数据质量。"""
import csv
from pathlib import Path

path = Path(__file__).resolve().parent / "wltc_diesel_1800s.csv"
with open(path, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

print(f"总行数: {len(rows)}")

# 各时间点采样
samples = [
    (0, "冷启动 t=0s"),
    (1000, "Low段 t=100s"),
    (3000, "Low段 t=300s"),
    (5000, "Low段 t=500s"),
    (6000, "Medium段 t=600s"),
    (8000, "Medium段 t=800s"),
    (10500, "High段 t=1050s"),
    (12000, "High段 t=1200s"),
    (14000, "High段 t=1400s"),
    (15000, "ExtraHigh段 t=1500s"),
    (16000, "ExtraHigh段 t=1600s"),
    (17500, "ExtraHigh段 t=1750s"),
]

print(f"\n{'时间点':<22} {'车速':>7} {'转速':>7} {'扭矩':>7} {'上游温度':>8} {'DPF温度':>8} {'上游NOX':>8} {'下游NOX':>8} {'尿素喷射':>8} {'排气流量':>8}")
print("-" * 120)
for idx, label in samples:
    r = rows[idx]
    print(f"{label:<22} {r['车速']:>7} {r['转速']:>7} {r['扭矩']:>7} {r['上游温度']:>8} {r['DPF温度']:>8} {r['上游NOX']:>8} {r['下游NOX']:>8} {r['尿素喷射量']:>8} {r['排气流量']:>8}")

# 统计
speeds = [float(r["车速"]) for r in rows]
rpms = [float(r["转速"]) for r in rows]
torques = [float(r["扭矩"]) for r in rows]
t_ups = [float(r["上游温度"]) for r in rows]
nox_ups = [float(r["上游NOX"]) for r in rows]
nox_downs = [float(r["下游NOX"]) for r in rows]
urea_rates = [float(r["尿素喷射量"]) for r in rows]
exhaust_flows = [float(r["排气流量"]) for r in rows]

print(f"\n{'信号':<10} {'最小值':>10} {'最大值':>10} {'平均值':>10}")
print("-" * 45)
for name, data in [("车速", speeds), ("转速", rpms), ("扭矩", torques),
                    ("上游温度", t_ups), ("上游NOX", nox_ups), ("下游NOX", nox_downs),
                    ("尿素喷射量", urea_rates), ("排气流量", exhaust_flows)]:
    print(f"{name:<10} {min(data):>10.1f} {max(data):>10.1f} {sum(data)/len(data):>10.1f}")

# 检查各阶段车速范围
print("\n各阶段车速范围:")
phase_ranges = [(0, 5890, "Low"), (5890, 10220, "Medium"),
                (10220, 14770, "High"), (14770, 18000, "ExtraHigh")]
for start, end, name in phase_ranges:
    phase_speeds = speeds[start:min(end, len(speeds))]
    print(f"  {name:<12}: {min(phase_speeds):.1f} ~ {max(phase_speeds):.1f} km/h")

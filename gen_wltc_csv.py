"""生成国六柴油车 WLTC 瞬态工况台架测试 CSV 数据。

模拟信号（17 路，与 signals.json 一致）：
  转速、扭矩、车速、DPF温度、上游温度、下游温度、
  上游NOX、下游NOX、环境温度、大气压力、冷却水温、
  尿素浓度、尿素温度、尿素液位、管路压力、尿素喷射量、排气流量

工况特征：
  - WLTC Class 3 总时长 1800s，按 100ms 采样 = 18000 行
  - 四个速度阶段：Low / Medium / High / ExtraHigh
  - 发动机转速/扭矩按阶段特征包络生成
  - 排气温度跟随发动机负荷变化，带热惯性延迟
  - DPF 捕集 NOX，SCR 还原 NOX，下游 NOX 显著低于上游
  - 尿素喷射量跟随上游 NOX 成正比（化学计量比 ~1.5）
"""

import csv
import math
import os
import random

# ── 工况参数 ──────────────────────────────────────────────
DT = 0.1                 # 采样周期 100ms
DURATION = 1800          # WLTC 总时长 1800s
N_ROWS = int(DURATION / DT)

# WLTC Class 3 各阶段时间范围（秒）
PHASES = [
    (0, 589, "Low"),           # 0-589s   低速段
    (589, 1022, "Medium"),     # 589-1022s 中速段
    (1022, 1477, "High"),      # 1022-1477s 高速段
    (1477, 1800, "ExtraHigh"), # 1477-1800s 超高速段
]

# 各阶段速度/转速/扭矩包络: (v_max, rpm_idle, rpm_peak, torque_max)
PHASE_PROFILE = {
    "Low":        (56,  700,  2200,  280),
    "Medium":     (78,  700,  2800,  350),
    "High":       (102, 700,  3500,  420),
    "ExtraHigh":  (135, 700,  4000,  500),
}


def _noise(amp: float) -> float:
    return random.gauss(0, amp)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── 预生成速度曲线（避免递归调用）──────────────────────────
_speed_cache: list[float] = []


def _build_speed_curve():
    """预生成完整速度曲线，使用多段加减速拼接。"""
    global _speed_cache
    _speed_cache = []
    random.seed(42)

    for i in range(N_ROWS):
        t = i * DT
        for start, end, name in PHASES:
            if start <= t < end:
                v_max = PHASE_PROFILE[name][0]
                progress = (t - start) / (end - start)
                # 多频率叠加模拟真实 WLTC 加减速波动
                # 主周期覆盖整个阶段，叠加高频微振
                envelope = (
                    0.40 * (1 - math.cos(2 * math.pi * progress * 1.5))
                    + 0.30 * (1 - math.cos(2 * math.pi * progress * 3.7 + 0.5))
                    + 0.18 * (1 - math.cos(2 * math.pi * progress * 7.3 + 1.2))
                    + 0.12 * (1 - math.cos(2 * math.pi * progress * 13.1 + 2.0))
                ) / 1.8  # 归一化
                base = v_max * envelope
                # 怠速段：在阶段起始和结束附近降速
                idle_zone = 0.03  # 3% 的阶段时间为怠速过渡
                if progress < idle_zone:
                    base *= progress / idle_zone
                elif progress > (1 - idle_zone):
                    base *= (1 - progress) / idle_zone
                speed = _clamp(base + _noise(0.5), 0, v_max)
                _speed_cache.append(speed)
                break
        else:
            _speed_cache.append(0.0)


def _vehicle_speed(t: float) -> float:
    """从预生成曲线获取车速。"""
    idx = int(t / DT)
    idx = _clamp(idx, 0, len(_speed_cache) - 1)
    return _speed_cache[idx]


def _engine_rpm(t: float, speed: float) -> float:
    """转速：与车速正相关，怠速时不低于 700。"""
    for start, end, name in PHASES:
        if start <= t < end:
            rpm_peak = PHASE_PROFILE[name][2]
            v_max = PHASE_PROFILE[name][0]
            # 转速 = 怠速 + 与车速成正比的增量（非线性）
            ratio = speed / max(v_max, 1)
            rpm = 700 + (rpm_peak - 700) * (ratio ** 0.8) * 0.75
            return _clamp(rpm + _noise(25), 650, rpm_peak)
    return 700.0


def _engine_torque(t: float, speed: float, rpm: float) -> float:
    """扭矩：加速时高扭矩，匀速时低扭矩，减速时为负扭矩。"""
    for start, end, name in PHASES:
        if start <= t < end:
            t_max = PHASE_PROFILE[name][3]
            v_max = PHASE_PROFILE[name][0]
            # 加速度估算：用前方差分，不跨越阶段边界
            t_next = min(t + DT, end - 0.001)
            idx_next = min(int(t_next / DT), len(_speed_cache) - 1)
            speed_next = _speed_cache[idx_next]
            accel = (speed_next - speed) / max(DT, t_next - t)
            if accel > 0.5:
                # 加速：高扭矩
                load = min(1.0, accel / 2.5)
                torque = t_max * load * (0.4 + 0.6 * speed / v_max)
            elif accel < -0.5:
                # 减速：负扭矩（发动机制动）
                torque = -t_max * 0.15 * min(1.0, abs(accel) / 2.0)
            else:
                # 匀速：低扭矩克服阻力
                torque = t_max * 0.12 * (speed / v_max) ** 0.5
            return _clamp(torque + _noise(3), -100, t_max)
    return 0.0


def _exhaust_temp_up(t: float, rpm: float, torque: float) -> float:
    """上游排气温度：跟随发动机负荷，带热惯性。"""
    warmup = min(1.0, t / 120.0)  # 2 分钟暖机
    load_factor = _clamp((rpm / 4000) * 0.4 + (torque / 500) * 0.6, 0, 1)
    base = 150 + 480 * load_factor * warmup
    return _clamp(base + _noise(5), 80, 650)


def _exhaust_temp_down(t_up: float) -> float:
    """下游排气温度：比上游低 20-60℃（DPF 散热）。"""
    delta = 20 + 40 * (1 - _clamp((t_up - 150) / 500, 0, 1))
    return _clamp(t_up - delta + _noise(3), 60, 600)


def _dpf_temp(t_up: float) -> float:
    """DPF 温度：介于上下游之间，略高于上游（捕集放热）。"""
    return _clamp(t_up + 5 + _noise(3), 80, 660)


def _nox_up(rpm: float, torque: float, t: float) -> float:
    """上游 NOX：高温高负荷时生成量大。"""
    warmup = min(1.0, max(0, (t - 60) / 120))  # 暖机后开始生成
    load = _clamp((rpm / 4000) * 0.5 + (max(0, torque) / 500) * 0.5, 0, 1)
    base = 2500 * load * warmup
    return _clamp(base + _noise(50), 0, 3000)


def _nox_down(nox_up: float, t_down: float, urea_rate: float) -> float:
    """下游 NOX：SCR 还原后大幅降低。"""
    # SCR 转化效率：温度 > 200℃ 后有效，最高 ~95%
    scr_eff = 0.0
    if t_down > 200:
        scr_eff = min(0.95, 0.5 + 0.45 * (t_down - 200) / 300)
    remaining = nox_up * (1 - scr_eff)
    return _clamp(remaining + _noise(10), 0, 1500)


def _urea_rate(nox_up: float, nox_down: float) -> float:
    """尿素喷射量：与 NOX 削减量成正比（化学计量比 ~1.5）。"""
    nox_removed = max(0, nox_up - nox_down)
    # 化学计量比系数 + 过量喷射系数 1.5
    rate = nox_removed * 0.008 * 1.5
    return _clamp(rate + _noise(0.5), 0, 5000)


def _urea_concentration(t: float) -> float:
    """尿素浓度（%）：32.5% AdBlue 溶液，缓慢消耗。"""
    return _clamp(32.5 - t / DURATION * 2.0 + _noise(0.1), 28, 32.5)


def _urea_temp(t_down: float) -> float:
    """尿素温度：跟随排气温度但滞后。"""
    return _clamp(t_down * 0.3 + 20 + _noise(1), -10, 80)


def _urea_level(t: float) -> float:
    """尿素液位：随消耗缓慢下降。"""
    return _clamp(100 - t / DURATION * 30 + _noise(0.3), 60, 100)


def _pipe_pressure(rpm: float, torque: float) -> float:
    """管路压力：与发动机排气量正相关。"""
    base = 50 + (rpm / 4000) * 80 + (max(0, torque) / 500) * 40
    return _clamp(base + _noise(2), 20, 200)


def _exhaust_flow(rpm: float, torque: float) -> float:
    """排气流量：与转速和扭矩正相关。"""
    base = (rpm / 4000) * 5 + (max(0, torque) / 500) * 8
    return _clamp(base + _noise(0.2), 0.5, 15)


def _coolant_temp(t: float, rpm: float, torque: float) -> float:
    """冷却水温：暖机后稳定在 80-95℃。"""
    warmup = min(1.0, t / 300)  # 5 分钟暖机
    load = (rpm / 4000) * 0.3 + (max(0, torque) / 500) * 0.7
    base = 25 + 70 * warmup + 10 * load
    return _clamp(base + _noise(0.5), -20, 110)


def main():
    random.seed(42)
    path = os.path.join(os.path.dirname(__file__), "wltc_diesel_1800s.csv")

    # 预生成速度曲线
    _build_speed_curve()

    # 表头与 signals.json 信号名完全一致
    headers = [
        "转速", "扭矩", "车速", "T5", "T6", "T7",
        "上游 NOx", "下游 NOx", "环境温度", "冷却水温",
        "尿素浓度", "尿素温度", "尿素液位", "大气压力",
        "排气流量", "管路压力", "尿素喷射量",
    ]

    rows = []
    for i in range(N_ROWS):
        t = i * DT
        speed = _speed_cache[i]
        rpm = _engine_rpm(t, speed)
        torque = _engine_torque(t, speed, rpm)
        t_up = _exhaust_temp_up(t, rpm, torque)
        t_down = _exhaust_temp_down(t_up)
        nox_up = _nox_up(rpm, torque, t)
        # 先估算下游 NOX（用粗略喷射量）
        nox_down_est = nox_up * 0.5
        urea_rate = _urea_rate(nox_up, nox_down_est)
        nox_down = _nox_down(nox_up, t_down, urea_rate)
        urea_rate = _urea_rate(nox_up, nox_down)  # 用精确下游 NOX 重算

        # 数据顺序与 headers / signals.json 一致
        row = [
            round(rpm, 1),                                        # 转速
            round(torque, 1),                                     # 扭矩
            round(speed, 1),                                      # 车速
            round(_dpf_temp(t_up), 1),                            # T5 (DPF温度)
            round(t_up, 1),                                       # T6 (上游温度)
            round(t_down, 1),                                     # T7 (下游温度)
            round(nox_up, 1),                                     # 上游 NOx
            round(nox_down, 1),                                   # 下游 NOx
            round(_clamp(25 + _noise(0.5), 20, 30), 1),          # 环境温度
            round(_coolant_temp(t, rpm, torque), 1),              # 冷却水温
            round(_urea_concentration(t), 2),                     # 尿素浓度
            round(_urea_temp(t_down), 1),                         # 尿素温度
            round(_urea_level(t), 1),                             # 尿素液位
            round(_clamp(101.3 + _noise(0.1), 100, 103), 1),     # 大气压力
            round(_exhaust_flow(rpm, torque), 1),                 # 排气流量
            round(_pipe_pressure(rpm, torque), 1),                # 管路压力
            round(urea_rate, 1),                                  # 尿素喷射量
        ]
        rows.append(row)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)

    size = os.path.getsize(path)
    print(f"已生成 {path}")
    print(f"  行数: {N_ROWS:,} （{DURATION}s × {int(1/DT)}Hz）")
    print(f"  列数: {len(headers)}")
    print(f"  大小: {size / 1024 / 1024:.1f} MB")
    print(f"  工况: WLTC Class 3 (Low/Medium/High/ExtraHigh)")

    # 快速验证
    speeds = [r[2] for r in rows]
    rpms = [r[0] for r in rows]
    print(f"\n  车速范围: {min(speeds):.1f} ~ {max(speeds):.1f} km/h")
    print(f"  转速范围: {min(rpms):.0f} ~ {max(rpms):.0f} rpm")

    # 各阶段峰值车速
    phase_ranges = [(0, 5890, "Low"), (5890, 10220, "Medium"),
                    (10220, 14770, "High"), (14770, 18000, "ExtraHigh")]
    for start, end, name in phase_ranges:
        ps = speeds[start:min(end, len(speeds))]
        print(f"  {name} 车速: {min(ps):.1f} ~ {max(ps):.1f} km/h")


if __name__ == "__main__":
    main()

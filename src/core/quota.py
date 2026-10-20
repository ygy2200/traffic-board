# -*- coding: utf-8 -*-
"""月流量配额：计费周期窗口计算 + 阈值告警状态机（纯函数，可单测）。

设计（对标 NetSpeedTray 的 data-cap 功能，2026-10 调研确认其为
市场验证的高频需求）：
- 月总量按「计费周期」算而非自然月：reset_day=10 表示每月 10 号重置，
  今天 8 号 → 统计上个月 10 号至今的用量（符合运营商/机场套餐口径）。
- 告警两档：80% 预警一次、100% 超限一次；同周期同档不重复轰炸
  （hysteresis：跨周期或升档才再提醒）。
- cap<=0 表示不限额（默认关，不做 surprise 告警）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

WARN_RATIO = 0.8


@dataclass
class QuotaState:
    """告警状态机的持久状态（内存即可，重启后重新按用量判定）。"""
    cycle_id: str = ""       # 当前周期标识（如 "2026-10"）
    level: int = 0           # 已告警档位 0/1(80%)/2(100%)


def month_window(now_ts: float, reset_day: int) -> Tuple[float, float]:
    """返回 (周期起, 周期止) 的 unix 时间戳。reset_day: 1-28。

    今天日号 >= reset_day → [本月 reset_day 00:00, 下月 reset_day 00:00)
    今天日号 <  reset_day → [上月 reset_day 00:00, 本月 reset_day 00:00)
    """
    reset_day = max(1, min(28, int(reset_day or 1)))
    lt = time.localtime(now_ts)
    y, m, d = lt.tm_year, lt.tm_mon, lt.tm_mday
    if d >= reset_day:
        start_y, start_m = y, m
    else:
        start_m = m - 1
        start_y = y
        if start_m <= 0:
            start_m, start_y = 12, y - 1
    start = _ts(start_y, start_m, reset_day)
    end_y, end_m = (start_y + 1, 1) if start_m == 12 else (start_y, start_m + 1)
    return start, _ts(end_y, end_m, reset_day)


def _ts(y: int, m: int, d: int) -> float:
    """本地时区当日 00:00 的时间戳（time.mktime 正确处理时区/夏令时）。"""
    return float(time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)))


def cycle_id(now_ts: float, reset_day: int) -> str:
    """周期标识：用于跨周期重置告警状态。"""
    start, _ = month_window(now_ts, reset_day)
    lt = time.localtime(start)
    return f"{lt.tm_year:04d}-{lt.tm_mon:02d}"


def cap_level(used_bytes: int, cap_bytes: int) -> int:
    """0=无需告警 / 1=达到 80% / 2=超限。cap<=0 → 永远 0。"""
    if cap_bytes <= 0 or used_bytes < 0:
        return 0
    ratio = used_bytes / cap_bytes
    if ratio >= 1.0:
        return 2
    if ratio >= WARN_RATIO:
        return 1
    return 0


def next_alert(state: QuotaState, used_bytes: int, cap_bytes: int,
               now_ts: float, reset_day: int) -> Optional[Tuple[int, str]]:
    """告警状态机：返回 (档位, 文案) 或 None（不提醒）。

    规则：换周期 → 状态重置；档位只升不降（同一周期内 100% 后不再重复
    报 80%）；同档不重复。
    """
    if cap_bytes <= 0:
        return None
    cid = cycle_id(now_ts, reset_day)
    if state.cycle_id != cid:
        state.cycle_id, state.level = cid, 0     # 新周期重新起算
    level = cap_level(used_bytes, cap_bytes)
    if level <= state.level:
        return None
    state.level = level
    if level == 1:
        return 1, (f"本月流量已用 {used_bytes / cap_bytes:.0%}"
                   f"（{_gb(used_bytes)} / {_gb(cap_bytes)}）——接近套餐上限")
    return 2, (f"本月流量已超套餐上限！已用 {_gb(used_bytes)}，"
               f"上限 {_gb(cap_bytes)}")


def _gb(n: int) -> str:
    return f"{n / (1024 ** 3):.1f}GB"


if __name__ == "__main__":
    # 自测：2026-10-20（日号 20）
    now = time.mktime((2026, 10, 20, 12, 0, 0, 0, 0, -1))
    s, e = month_window(now, 10)
    assert time.strftime("%Y-%m-%d", time.localtime(s)) == "2026-10-10", s
    assert time.strftime("%Y-%m-%d", time.localtime(e)) == "2026-11-10", e
    # 日号 8 < reset 10 → 上周期
    now2 = time.mktime((2026, 10, 8, 12, 0, 0, 0, 0, -1))
    s2, _ = month_window(now2, 10)
    assert time.strftime("%Y-%m-%d", time.localtime(s2)) == "2026-09-10", s2
    # 跨年：1 月 5 日，reset 10 → 上年 12-10
    now3 = time.mktime((2027, 1, 5, 12, 0, 0, 0, 0, -1))
    s3, _ = month_window(now3, 10)
    assert time.strftime("%Y-%m-%d", time.localtime(s3)) == "2026-12-10", s3
    # reset_day 钳制
    s4, _ = month_window(now, 99)
    assert time.strftime("%d", time.localtime(s4)) == "28"
    # 档位
    assert cap_level(0, 100) == 0 and cap_level(79, 100) == 0
    assert cap_level(80, 100) == 1 and cap_level(99, 100) == 1
    assert cap_level(100, 100) == 2 and cap_level(150, 100) == 2
    assert cap_level(50, 0) == 0                      # 未启用
    # 状态机：同档不重复、升档再报、换周期重置
    st = QuotaState()
    assert next_alert(st, 80, 100, now, 10) is not None      # 80% 首报
    assert next_alert(st, 85, 100, now, 10) is None          # 同档不重复
    assert next_alert(st, 100, 100, now, 10) is not None     # 升档再报
    assert next_alert(st, 120, 100, now, 10) is None         # 同档不重复
    st2 = QuotaState()
    next_alert(st2, 100, 100, now, 10)
    now_next = time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1))
    assert next_alert(st2, 10, 100, now_next, 10) is None    # 新周期重置
    assert next_alert(st2, 80, 100, now_next, 10) is not None
    print("quota 自测通过")

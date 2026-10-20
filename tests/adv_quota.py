# -*- coding: utf-8 -*-
"""对抗测试：月流量配额——计费周期窗口、阈值档位、告警状态机。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.quota import (  # noqa: E402
    QuotaState, cap_level, cycle_id, month_window, next_alert)

_TS = lambda y, m, d, h=12: time.mktime((y, m, d, h, 0, 0, 0, 0, -1))  # noqa: E731


def _fmt_date(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def test_month_window_basic():
    now = _TS(2026, 10, 20)                      # 日号 20 >= reset 10 → 本周期
    s, e = month_window(now, 10)
    assert _fmt_date(s) == "2026-10-10" and _fmt_date(e) == "2026-11-10", (s, e)
    # 日号 8 < reset 10 → 上周期
    s2, _ = month_window(_TS(2026, 10, 8), 10)
    assert _fmt_date(s2) == "2026-09-10", s2
    # reset=1（自然月）
    s3, _ = month_window(now, 1)
    assert _fmt_date(s3) == "2026-10-01", s3
    # 跨年：1 月 5 日 reset 10 → 上年 12-10 起
    s4, _ = month_window(_TS(2027, 1, 5), 10)
    assert _fmt_date(s4) == "2026-12-10", s4
    # 边界：日号 == reset_day 算本周期（28 号 reset 28 → 2-28 起，3-28 止）
    s5, e5 = month_window(_TS(2026, 2, 28), 28)
    assert _fmt_date(s5) == "2026-02-28" and _fmt_date(e5) == "2026-03-28", (s5, e5)
    # 日号 27 < reset 28 → 上周期
    s6, _ = month_window(_TS(2026, 2, 27), 28)
    assert _fmt_date(s6) == "2026-01-28", s6
    print("计费周期窗口 PASS（含跨年/月末边界）")


def test_reset_day_clamp():
    # 0/负值 → 1（自然月）；>28 → 28（月末保护，2 月无 29-31）
    for bad in (0, -5):
        s, _ = month_window(_TS(2026, 10, 20), bad)
        assert time.strftime("%d", time.localtime(s)) == "01", bad
    for big in (99, 1000):
        s, _ = month_window(_TS(2026, 10, 20), big)
        assert time.strftime("%d", time.localtime(s)) == "28", big
    print("reset_day 钳制 PASS")


def test_cap_levels():
    assert cap_level(0, 100) == 0 and cap_level(79, 100) == 0
    assert cap_level(80, 100) == 1 and cap_level(99, 100) == 1
    assert cap_level(100, 100) == 2 and cap_level(150, 100) == 2
    assert cap_level(50, 0) == 0                  # 未启用
    assert cap_level(-5, 100) == 0                # 畸形负值
    print("阈值档位 PASS")


def test_alert_state_machine():
    now = _TS(2026, 10, 20)
    st = QuotaState()
    # 首报 80%
    a = next_alert(st, 80, 100, now, 10)
    assert a is not None and a[0] == 1 and "80%" in a[1], a
    # 同档不重复
    assert next_alert(st, 85, 100, now, 10) is None
    # 升档再报
    a2 = next_alert(st, 100, 100, now, 10)
    assert a2 is not None and a2[0] == 2 and "超" in a2[1], a2
    assert next_alert(st, 120, 100, now, 10) is None
    # 未启用不报
    assert next_alert(QuotaState(), 999, 0, now, 10) is None
    # 换周期重置（下周期 15 号）
    now_next = _TS(2026, 11, 15)
    assert next_alert(st, 10, 100, now_next, 10) is None      # 新周期 level 归零
    assert next_alert(st, 80, 100, now_next, 10) is not None  # 新周期可再报
    # 周期 id 语义
    assert cycle_id(now, 10) == "2026-10"
    assert cycle_id(_TS(2026, 10, 8), 10) == "2026-09"
    print("告警状态机 PASS（同档不重复/升档再报/跨周期重置）")


def test_evil_inputs():
    # 畸形参数不得抛异常
    for bad_cap in (0, -1):
        assert next_alert(QuotaState(), 100, bad_cap, _TS(2026, 10, 20), 10) is None
    s, e = month_window(_TS(2026, 10, 20), 0)
    assert s < e
    st = QuotaState(cycle_id="bogus", level=99)   # 脏状态：level 异常高
    assert next_alert(st, 50, 100, _TS(2026, 10, 20), 10) is None
    print("畸形输入免疫 PASS")


if __name__ == "__main__":
    test_month_window_basic()
    test_reset_day_clamp()
    test_cap_levels()
    test_alert_state_machine()
    test_evil_inputs()
    print("adv_quota 全部通过")

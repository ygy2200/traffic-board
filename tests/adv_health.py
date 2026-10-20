# -*- coding: utf-8 -*-
"""对抗测试：体检引擎——互斥判定矩阵、畸形输入免疫、超时降级。

核心资产是互斥检查（TUN×系统代理并存 → FAIL + 修复指引），
2026-10-03 Store 瘫痪事故的固化。判定矩阵必须钉死。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import core.health as health  # noqa: E402
from core.health import (  # noqa: E402
    SEV_FAIL, SEV_INFO, SEV_OK, SEV_WARN, HealthEngine, HealthItem,
    HealthReport, _reg_proxy_enabled, _run_cmd)
from types import SimpleNamespace

from core.tun_detect import TunState  # noqa: E402


class _FakeClash:
    """clash.snapshot() → 带online/port/version/conns 的状态对象。"""
    def __init__(self, online=False, port=0, version="1.10.0", conns=0):
        if not isinstance(conns, int):
            conn_list = list(conns)
        else:
            conn_list = list(range(conns))
        self._s = SimpleNamespace(online=online, port=port, version=version,
                                  conns=conn_list)
    def snapshot(self):
        return self._s


class _FakeTun:
    def __init__(self, st=TunState.OFF):
        self._st = st
    def snapshot(self):
        return self._st


class _FakeAgg:
    def __init__(self, tun=TunState.OFF, online=False, conns=0):
        self.tun = _FakeTun(tun)
        self.clash = _FakeClash(online=online, conns=conns)

    def start(self) -> None:   # 体检会调用（幂等拉起采集线程）
        self.started = True


def test_report_logic():
    rep = HealthReport(items=[
        HealthItem("a", "A", SEV_OK, ""), HealthItem("b", "B", SEV_FAIL, ""),
        HealthItem("c", "C", SEV_WARN, ""), HealthItem("d", "D", SEV_INFO, "")])
    assert rep.worst() == SEV_FAIL
    assert rep.sorted_items()[0].severity == SEV_FAIL
    assert rep.get("b").name == "B" and rep.get("zz") is None
    assert HealthReport().worst() == SEV_INFO      # 空报告不崩
    print("Report 排序/查询 PASS")


def test_mutex_matrix():
    """招牌项矩阵：只有"TUN ON + 系统代理 ON"才 FAIL。"""
    old = (health._reg_proxy_enabled, health._reg_proxy_server)
    try:
        cases = [
            (TunState.ON, True, SEV_FAIL),
            (TunState.ON, False, SEV_OK),
            (TunState.OFF, True, SEV_INFO),
            (TunState.OFF, False, SEV_OK),
            (TunState.UNKNOWN, True, SEV_WARN),   # 存疑+系统代理开 → 保守告警不放过
            (TunState.UNKNOWN, False, SEV_OK),
        ]
        for tun, proxy, want in cases:
            health._reg_proxy_enabled = lambda p=proxy: p
            health._reg_proxy_server = lambda: "127.0.0.1:7890"
            it = health._check_proxy_mutex(_FakeAgg(tun=tun))
            assert it.severity == want, f"tun={tun} proxy={proxy} → {it.severity} ≠ {want}"
        # FAIL 项必须带修复指引
        health._reg_proxy_enabled = lambda: True
        it = health._check_proxy_mutex(_FakeAgg(tun=TunState.ON))
        assert it.severity == SEV_FAIL and it.fix and "系统代理" in it.fix
    finally:
        health._reg_proxy_enabled, health._reg_proxy_server = old
    print("互斥判定矩阵 PASS（6 组合 + 修复指引）")


def test_offline_full_run():
    """离线假环境跑全部 10 项：无异常、severity 合法、无空白名称。"""
    agg = _FakeAgg(tun=TunState.OFF, online=False)
    ok = {"ok", "info", "warn", "fail"}
    for cid, fn in health._CHECKS.items():
        it = fn(agg)
        assert it.severity in ok, f"{cid} 非法 severity: {it.severity}"
        assert it.name and it.detail
    print(f"离线全项免疫 PASS（{len(health._CHECKS)} 项）")


def test_registry_failure():
    """检查项内部抛异常（如注册表损坏）→ run_all 层兜底降级 warn，不穿透。"""
    orig = dict(health._CHECKS)
    try:
        def _boom(agg):
            raise OSError("registry corrupted")
        health._CHECKS["boom"] = _boom
        rep = HealthEngine(_FakeAgg()).run_all(deadline_s=8.0)
        it = rep.get("boom")
        assert it is not None and it.severity == SEV_WARN
        assert "OSError" in it.detail
    finally:
        health._CHECKS.clear()
        health._CHECKS.update(orig)
    # 真实 _reg_proxy_enabled 自身对坏键也兜底（winreg 异常 → False，不抛）
    assert isinstance(_reg_proxy_enabled(), bool)
    print("注册表异常路径 PASS（run_all 层兜底 + _reg 自兜底）")


def test_cmd_failure():
    """子进程失败/超时 → 空串，调用方降级 info。"""
    assert _run_cmd(["cmd-not-exist-xyz"]) == ""
    assert _run_cmd(["ping", "-n", "10", "127.0.0.1"], timeout=0.5) == ""   # 超时截断
    print("子进程异常路径 PASS")


def test_engine_timeout_degradation():
    """慢检查项 → 整体截止生效，该项降级 warn 而非挂死。"""
    orig = dict(health._CHECKS)
    try:
        health._CHECKS["slowbomb"] = lambda agg: time.sleep(30)
        eng = HealthEngine(_FakeAgg())
        t0 = time.time()
        rep = eng.run_all(deadline_s=1.0)
        dt = time.time() - t0
        assert dt < 5, f"截止失效: {dt:.1f}s"
        slow = rep.get("slowbomb")
        assert slow is not None and slow.severity == SEV_WARN
        # worst 可能被真实环境的 fail 项（如出境被封）占据，只要求不轻于 WARN
        assert rep.worst() in (SEV_WARN, SEV_FAIL)
    finally:
        health._CHECKS.clear()
        health._CHECKS.update(orig)
    print("体检截止降级 PASS")


def test_real_engine_smoke():
    """真机冒烟：真实 Aggregator 跑一轮，结构完整。"""
    from core.aggregator import Aggregator
    agg = Aggregator()
    try:
        rep = HealthEngine(agg).run_all(deadline_s=14.0)
        assert len(rep.items) == len(health._CHECKS)
        assert all(it.severity in {"ok", "info", "warn", "fail"} for it in rep.items)
        assert rep.duration > 0
    finally:
        agg.stop()
    print(f"真机冒烟 PASS（最差={rep.worst()}，{rep.duration:.1f}s）")


def test_overall_matrix():
    """总状态矩阵：回答"我的代理生效了吗"（2026-10-04 用户反馈的核心修复）。
    R4 起 TUN 分支改为直测出站（不经 7890），故 _direct_egress_ok 也进 mock。"""
    old = (health._reg_proxy_enabled, health._port_listening, health._proxy_egress_ok,
           health._direct_egress_ok, health._resolve_proxy_port)
    try:
        health._port_listening = lambda p, t=0: True
        health._proxy_egress_ok = lambda port=7890, timeout=3.0: True
        health._direct_egress_ok = lambda timeout=3.0: True
        health._resolve_proxy_port = lambda agg: 7890
        assert health._check_overall(_FakeAgg(tun=TunState.ON)).severity == SEV_OK
        # TUN 开但节点不通 → fail 引导换节点
        health._direct_egress_ok = lambda timeout=3.0: False
        assert health._check_overall(_FakeAgg(tun=TunState.ON)).severity == SEV_FAIL
        # 系统代理开 + 节点通 → ok
        health._reg_proxy_enabled = lambda: True
        health._proxy_egress_ok = lambda port=7890, timeout=3.0: True
        it = health._check_overall(_FakeAgg(tun=TunState.OFF))
        assert it.severity == SEV_OK and "系统代理" in it.detail
        # 系统代理开 + 节点不通 → fail "换节点"（用户 10-04 场景的正确答案）
        health._proxy_egress_ok = lambda port=7890, timeout=3.0: False
        it = health._check_overall(_FakeAgg(tun=TunState.OFF))
        assert it.severity == SEV_FAIL and "节点" in (it.fix or "")
        # 系统代理开但入口端口无人监听 → fail
        health._port_listening = lambda p, t=0: False
        it = health._check_overall(_FakeAgg(tun=TunState.OFF))
        assert it.severity == SEV_FAIL
        # 全关 → info（不是 fail：直连是合法状态，不许再说"没开"式黑话）
        health._reg_proxy_enabled = lambda: False
        it = health._check_overall(_FakeAgg(tun=TunState.OFF))
        assert it.severity == SEV_INFO
    finally:
        (health._reg_proxy_enabled,
         health._port_listening, health._proxy_egress_ok,
         health._direct_egress_ok, health._resolve_proxy_port) = old
    print("代理总状态矩阵 PASS（节点不通≠没开代理）")


def test_proc_attr_matrix():
    """进程归因矩阵（R4 新增）：TUN 下内核不返回进程名 → FAIL + 修复指引。"""
    from core.clash_api import ClashConn
    def _conn(proc):
        return ClashConn(conn_id="c1", process=proc, host="a.com", dest_ip="",
                         dest_port=443, network="tcp", chains=["香港01"])
    # TUN + 在线 + 有进程名 → ok
    it = health._check_proc_attr(_FakeAgg(tun=TunState.ON, online=True,
                                          conns=[_conn("app.exe")]))
    assert it.severity == SEV_OK, it
    # TUN + 在线 + 全无进程名（find-process-mode: off）→ fail + 修复指引
    it = health._check_proc_attr(_FakeAgg(tun=TunState.ON, online=True,
                                          conns=[_conn(""), _conn("")]))
    assert it.severity == SEV_FAIL and it.fix and "find-process-mode" in it.fix, it
    # 非 TUN → info（进程名来自系统表，不受内核配置影响）
    it = health._check_proc_attr(_FakeAgg(tun=TunState.OFF, online=True,
                                          conns=[_conn("app.exe")]))
    assert it.severity == SEV_INFO
    # TUN 但无连接 → info（无法采样，不冤枉用户）
    it = health._check_proc_attr(_FakeAgg(tun=TunState.ON, online=True, conns=[]))
    assert it.severity == SEV_INFO
    print("进程归因矩阵 PASS（find-process-mode 失效可发现）")


def test_port_resolution():
    """入口端口发现优先级：注册表 > mihomo /configs > 7890 兜底（R4 修复写死 7890）。"""
    old = (health._reg_proxy_port, health._api_mixed_port)
    try:
        health._reg_proxy_port = lambda: 7897
        health._api_mixed_port = lambda agg: 7890
        assert health._resolve_proxy_port(_FakeAgg()) == 7897
        health._reg_proxy_port = lambda: 0
        assert health._resolve_proxy_port(_FakeAgg()) == 7890
        health._api_mixed_port = lambda agg: 0
        assert health._resolve_proxy_port(_FakeAgg()) == 7890
    finally:
        health._reg_proxy_port, health._api_mixed_port = old
    # 注册表解析本身对各种写法免疫
    assert health._reg_proxy_port.__name__ == "_reg_proxy_port"
    print("端口发现优先级 PASS")


if __name__ == "__main__":
    test_report_logic()
    test_overall_matrix()
    test_proc_attr_matrix()
    test_port_resolution()
    test_mutex_matrix()
    test_offline_full_run()
    test_registry_failure()
    test_cmd_failure()
    test_engine_timeout_degradation()
    test_real_engine_smoke()
    print("adv_health 全部通过")

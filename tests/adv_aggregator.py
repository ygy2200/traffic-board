# -*- coding: utf-8 -*-
"""对抗测试：聚合层对畸形进程/网络数据的免疫 + 并发安全。"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.aggregator import Aggregator  # noqa: E402
from core.clash_api import ClashConn, ClashState  # noqa: E402
from core.connections import ConnectionPoller, Snapshot, SysConn  # noqa: E402


def _fake_sys(pairs):
    snap = Snapshot()
    for i, (ip, port, name, proto) in enumerate(pairs, start=1):
        snap.conns.append(SysConn(proto=proto, raddr_ip=ip, raddr_port=port,
                                  laddr_port=0, pid=i, proc_name=name))
    return snap


def _fake_clash_state(conns, up_total=0, down_total=0, version="1.10.0"):
    st = ClashState(online=True, version=version,
                    upload_total=up_total, download_total=down_total)
    st.conns = conns
    return st


def test_evil_sysdata(monkey_agg: Aggregator):
    evil_pairs = [
        ("999.999.999.999", 80, "bad-ip.exe", "tcp"),               # 非法 IP
        ("", 80, "empty-ip.exe", "tcp"),                            # 空 IP
        ("x" * 3000, 80, "long-ip.exe", "tcp"),                     # 超长 IP
        ("93.184.216.34", 65536, "overflow-port.exe", "tcp"),       # 溢出端口
        ("93.184.216.34", -1, "neg-port.exe", "tcp"),               # 负端口
        ("1.2.3.4", 80, "proc\nwith\nnewlines.exe", "tcp"),         # 名字含换行
        ("1.2.3.4", 80, "进程" * 500, "tcp"),                       # 超长 unicode 名
        ("127.0.0.1", 7890, "to-proxy.exe", "tcp"),                 # 发往本机代理端口
        ("", 0, "udp-only.exe", "udp"),                             # UDP 无远端
    ]
    monkey_agg.poller.poll = lambda: _fake_sys(evil_pairs)  # type: ignore
    monkey_agg.clash.state.__dict__["online"] = False
    b = monkey_agg.build()
    names = [a.proc_name for a in b.apps]
    assert "bad-ip.exe" in names and "to-proxy.exe" in names
    proxy_app = next(a for a in b.apps if a.proc_name == "to-proxy.exe")
    assert proxy_app.conns[0].channel == "proxy"
    direct = next(a for a in b.apps if a.proc_name == "bad-ip.exe")
    assert direct.conns[0].channel == "local"  # 非法 IP 保守归"本地"，不误标外网直连
    print("系统连接表畸形数据 PASS")


def test_evil_dns_map(monkey_agg: Aggregator):
    monkey_agg.dns.ip_map = {"1.2.3.4": "evil\x00null.com", "": "", "2.2.2.2": "域名" * 3000}
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "a.exe", "tcp")])
    monkey_agg.clash.state.__dict__["online"] = False
    b = monkey_agg.build()
    a = b.apps[0]
    assert "evil\x00null.com" in a.conns[0].target  # 注入字符原样展示（UI 层为 QLabel 非富文本）
    print("DNS 映射注入 PASS")


def test_concurrent_build():
    agg2 = Aggregator()
    agg2.start = lambda: None
    agg2.clash.state.__dict__["online"] = False
    errs = []

    def worker():
        try:
            for _ in range(20):
                agg2.build()
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errs, f"并发 build 异常: {errs}"
    print("4 线程并发 build PASS")



def _reset_cum(agg: Aggregator) -> None:
    """各 rank_cum 用例独立：清空共享累计状态。"""
    agg._proc_cum.clear()
    agg._conn_last.clear()
    agg._proc_rate.clear()
    agg._cum_flushed.clear()
    agg._cum_last_t = 0.0

def _conn(cid, proc, up, down, host="cdn.example.com"):
    return ClashConn(conn_id=cid, process=proc, host=host, dest_ip="1.2.3.4",
                     dest_port=443, network="tcp", chains=["香港01", "机场组"],
                     upload=up, download=down)


def test_rank_cum_monotonic(monkey_agg: Aggregator):
    _reset_cum(monkey_agg)
    """排行数值必须单调：连接断开不能让字节回退（用户报"数值不准"根因）。"""
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "app.exe", "tcp")])
    # 第 1 拍：连接 A 传了 100/200
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 100, 200)],
                                               up_total=100, down_total=200)
    b1 = monkey_agg.build()
    a1 = next(a for a in b1.apps if a.proc_name == "app.exe")
    assert (a1.upload, a1.download) == (100, 200), f"首拍累计错误: {a1.upload},{a1.download}"
    # 第 2 拍：连接 A 涨到 150/260
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 150, 260)],
                                               up_total=150, down_total=260)
    b2 = monkey_agg.build()
    a2 = next(a for a in b2.apps if a.proc_name == "app.exe")
    assert (a2.upload, a2.download) == (150, 260)
    # 第 3 拍：连接 A 断开，新连接 B 传 30/40 —— 累计必须保持 150/260 起步，不回退
    monkey_agg.clash.state = _fake_clash_state([_conn("c2", "app.exe", 30, 40)],
                                               up_total=180, down_total=300)
    b3 = monkey_agg.build()
    a3 = next(a for a in b3.apps if a.proc_name == "app.exe")
    assert a3.upload >= 150 and a3.download >= 260, \
        f"连接断开后数值回退: {a3.upload},{a3.download}"
    assert (a3.upload, a3.download) == (180, 300), f"断开连接最后增量丢失: {a3.upload},{a3.download}"
    print("排行累计单调性（断开不回退）PASS")


def test_rank_cum_mihomo_restart(monkey_agg: Aggregator):
    _reset_cum(monkey_agg)
    """mihomo 重启（全新 conn_id）：旧累计保留、新连接从零差分。"""
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "app.exe", "tcp")])
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 1000, 2000)],
                                               up_total=1000, down_total=2000)
    monkey_agg.build()
    # 内核重启：全新 conn_id、字节从零开始；总量指标变小属正常（它是活跃连接总和）
    monkey_agg.clash.state = _fake_clash_state([_conn("c9", "app.exe", 10, 20)],
                                               up_total=10, down_total=20)
    b = monkey_agg.build()
    a = next(x for x in b.apps if x.proc_name == "app.exe")
    assert (a.upload, a.download) == (1010, 2020), \
        f"内核重启后累计异常: {a.upload},{a.download}"
    print("mihomo 重启累计保持 PASS")


def test_rank_cum_multi_pid(monkey_agg: Aggregator):
    """同名进程多 pid：合并为一行（防字节秒间漂移），数值为全量累计。"""
    _reset_cum(monkey_agg)
    monkey_agg.poller.poll = lambda: _fake_sys([
        ("1.2.3.4", 443, "multi.exe", "tcp"),
        ("8.8.8.8", 443, "multi.exe", "tcp"),   # 同名第二进程（直连）
    ])
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "multi.exe", 100, 100)],
                                               up_total=100, down_total=100)
    b = monkey_agg.build()
    rows = [a for a in b.apps if a.proc_name == "multi.exe"]
    assert len(rows) == 1, f"同名进程未合并: {len(rows)} 行"
    assert len(rows[0].conns) == 3   # 系统表 2 条 + Clash 1 条（目标串不同不去重）
    assert (rows[0].upload, rows[0].download) == (100, 100)
    print("同名多 pid 合并且不重复覆盖 PASS")


def test_rank_cum_negative_delta(monkey_agg: Aggregator):
    _reset_cum(monkey_agg)
    """畸形数据：单连接字节倒退（总量未回退）不得产生负增量。"""
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "app.exe", "tcp")])
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 500, 900)],
                                               up_total=500, down_total=900)
    monkey_agg.build()
    # c1 倒退，但新增 c2 使总量不回退（排除内核重启语义）
    monkey_agg.clash.state = _fake_clash_state(
        [_conn("c1", "app.exe", 100, 50), _conn("c2", "app.exe", 400, 900)],
        up_total=500, down_total=950)
    b = monkey_agg.build()
    a = next(x for x in b.apps if x.proc_name == "app.exe")
    # c1 倒退被吞（累计保持 500/900），c2 从零差分计入 400/900
    assert (a.upload, a.download) == (900, 1800), f"负增量污染累计: {a.upload},{a.download}"
    print("单连接字节倒退防护 PASS")


def test_legacy_pid_zero_no_cross_attribution(monkey_agg: Aggregator):
    """R4：Clash 有连接但系统表查不到 pid 时（find-process-mode off / 进程刚起），
    _pid_of 返回 0。0 不能当 apps 的键——否则所有无名进程并成一行，第一个到的
    进程名给所有人戴帽，后到的软件整行消失（用户看到"discord 吃了 telegram 的量"）。"""
    _reset_cum(monkey_agg)
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "known.exe", "tcp")])
    monkey_agg.clash.state = _fake_clash_state([
        _conn("c1", "discord.exe", 100, 200, host="discord.com"),
        _conn("c2", "telegram.exe", 7000, 8000, host="telegram.org"),
        _conn("c3", "", 5, 6, host="unnamed.com"),               # mihomo 没给出进程名
    ], up_total=7105, down_total=8206)
    b = monkey_agg.build()
    rows = {a.proc_name: a for a in b.apps}
    assert {"discord.exe", "telegram.exe", "未知进程"} <= set(rows), \
        f"无名进程被并成一行，软件整行消失: {sorted(rows)}"
    assert (rows["discord.exe"].upload, rows["discord.exe"].download) == (100, 200)
    assert (rows["telegram.exe"].upload, rows["telegram.exe"].download) == (7000, 8000)
    assert [c.target for c in rows["discord.exe"].conns] == ["discord.com"], \
        "跨进程串了连接明细"
    assert [c.target for c in rows["telegram.exe"].conns] == ["telegram.org"]
    assert rows["discord.exe"].pid == 0 and rows["telegram.exe"].pid == 0
    print("无名进程不并行的按名归因（pid=0 串名防护）PASS")


def test_legacy_pid_zero_merges_with_real_pid(monkey_agg: Aggregator):
    """同软件既有 pid=0 的 Clash 行又有真实 pid 的系统行：合并成一行且 pid 取真实值。"""
    _reset_cum(monkey_agg)
    monkey_agg.poller.poll = lambda: _fake_sys([("8.8.8.8", 443, "app.exe", "tcp")])
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 50, 60)])
    b = monkey_agg.build()
    rows = [a for a in b.apps if a.proc_name == "app.exe"]
    assert len(rows) == 1, f"pid=0 行与真实 pid 行未合并: {len(rows)}"
    assert rows[0].pid == 1, f"pid 仍是占位 0: {rows[0].pid}"
    assert len(rows[0].conns) == 2
    assert (rows[0].upload, rows[0].download) == (50, 60)
    print("pid=0 行与真实 pid 行按名合并并回填 pid PASS")


def test_local_semantics():
    f = ConnectionPoller.is_local
    assert f("127.0.0.1") and f("::1") and f("192.168.1.5") and f("10.0.0.1") and f("fe80::1")
    assert not f("8.8.8.8") and not f("93.184.216.34")
    assert f("") and f("999.999.999.999") and f("not an ip")  # 非法输入按本地处理（防误标外网）
    print("本地网段判定 PASS")


# ---------- v1.6 TUN 单源模式 ----------

def _tun_conn(cid, proc, up, down, host="cdn.example.com", network="tcp",
              chains=("香港01", "机场组")):
    return ClashConn(conn_id=cid, process=proc, host=host,
                     dest_ip="1.2.3.4" if not host else "", dest_port=443,
                     network=network, chains=list(chains), upload=up, download=down)


def test_tun_single_source(monkey_agg: Aggregator):
    """TUN 单源：不用 psutil、不用 DNS；UDP/裸 IP/畸形 process 全兜住。"""
    _reset_cum(monkey_agg)
    monkey_agg.tun.snapshot = lambda: TunState.ON  # type: ignore

    def _psutil_must_not_run():
        raise AssertionError("TUN 单源模式不得调用 psutil 全表扫描")
    monkey_agg.poller.poll = _psutil_must_not_run  # type: ignore

    monkey_agg.clash.state = _fake_clash_state([
        _tun_conn("c1", "app.exe", 100, 200, host="a.com"),
        _tun_conn("c2", "app.exe", 10, 20, host="", network="udp"),      # UDP 裸 IP
        _tun_conn("c3", "x" * 4096, 0, 0, host="b.com"),                 # 超长进程名
        _tun_conn("c4", "direct.exe", 5, 8, chains=("DIRECT",)),         # 直连出站
        ClashConn(conn_id="", process="", host="", chains=[]),           # 空进程/空 id
    ])
    b = monkey_agg.build()
    assert b.data_mode == "tun" and b.tun_state == "on"
    assert b.sys_conn_count == 0 and b.dns_count == 0          # 两条重路径均未消费
    names = {a.proc_name for a in b.apps}
    assert "app.exe" in names and "direct.exe" in names and "未知进程" in names
    app = next(a for a in b.apps if a.proc_name == "app.exe")
    assert len(app.conns) == 2 and app.pid == 0                # 同名合并；pid 无源
    udp = next(cv for cv in app.conns if cv.proto == "udp")
    assert udp.target == "1.2.3.4:443" and udp.channel == "proxy"   # UDP 也有真实五元组+出站
    assert (app.upload, app.download) == (110, 220)            # 累计器语义不变
    direct = next(a for a in b.apps if a.proc_name == "direct.exe")
    assert direct.conns[0].channel == "direct"                 # DIRECT 出站不算代理
    assert monkey_agg.tun.snapshot() is TunState.ON
    print("TUN 单源构建（psutil/DNS 零消费 + UDP/畸形兜底）PASS")


def test_tun_unknown_falls_back(monkey_agg: Aggregator):
    """TUN 判定 UNKNOWN（佐证未命中）→ 保守回退 legacy，不许单源。"""
    _reset_cum(monkey_agg)
    monkey_agg.tun.snapshot = lambda: TunState.UNKNOWN  # type: ignore
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "app.exe", "tcp")])
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 1, 2)])
    b = monkey_agg.build()
    assert b.data_mode == "legacy" and b.tun_state == "unknown"
    assert b.sys_conn_count == 1                                # psutil 有被消费
    print("UNKNOWN 回退 legacy PASS")


def test_mode_switch_cum_continuous(monkey_agg: Aggregator):
    """legacy → TUN → legacy 模式来回切，进程累计数值单调连续。"""
    _reset_cum(monkey_agg)
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "app.exe", "tcp")])
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 100, 100)])
    b1 = monkey_agg.build()
    assert b1.data_mode == "legacy"
    monkey_agg.tun.snapshot = lambda: TunState.ON  # type: ignore
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 150, 180)])
    b2 = monkey_agg.build()
    a2 = next(x for x in b2.apps if x.proc_name == "app.exe")
    assert (a2.upload, a2.download) == (150, 180)
    monkey_agg.tun.snapshot = lambda: TunState.OFF  # type: ignore
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 200, 260)])
    b3 = monkey_agg.build()
    a3 = next(x for x in b3.apps if x.proc_name == "app.exe")
    assert (a3.upload, a3.download) == (200, 260), f"模式切换累计断裂: {a3.upload},{a3.download}"
    print("模式切换累计连续 PASS")


def test_force_legacy(monkey_agg: Aggregator):
    """设置页"强制传统模式"：即使 TUN=ON 也走 legacy。"""
    _reset_cum(monkey_agg)
    monkey_agg.tun.snapshot = lambda: TunState.ON  # type: ignore
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "app.exe", "tcp")])
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 1, 1)])
    monkey_agg.set_force_legacy(True)
    try:
        b = monkey_agg.build()
        assert b.data_mode == "legacy" and b.sys_conn_count == 1
    finally:
        monkey_agg.set_force_legacy(False)
    print("强制传统模式开关 PASS")


def test_tun_key_unique(monkey_agg: Aggregator):
    """UI 行键唯一性：TUN 下多软件 key 不得互相覆盖（pid 全 0 的历史坑）。"""
    _reset_cum(monkey_agg)
    monkey_agg.tun.snapshot = lambda: TunState.ON  # type: ignore
    monkey_agg.clash.state = _fake_clash_state([
        _tun_conn("c1", "a.exe", 1, 1), _tun_conn("c2", "b.exe", 2, 2)])
    b = monkey_agg.build()
    keys = [a.key for a in b.apps]
    assert len(keys) == len(set(keys)) == 2, f"key 冲突: {keys}"
    print("TUN 行键唯一 PASS")


def test_proc_rate_ema(monkey_agg: Aggregator):
    """实时速率（R5）：秒级差分 / 真实耗时 + EMA 平滑；停流的软件速率归零。"""
    import time
    _reset_cum(monkey_agg)
    monkey_agg.tun.snapshot = lambda: TunState.ON  # type: ignore
    # 第 1 拍：无时间基（dt=0），速率必为 0（UI 显示"——"，不凭空造数）
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 100, 200)])
    b1 = monkey_agg.build()
    a1 = next(a for a in b1.apps if a.proc_name == "app.exe")
    assert a1.rate_up == 0 and a1.rate_down == 0, "无时间基不得造速率"
    # 第 2 拍（隔 0.3s）：新增 300/600 → 瞬时 ≈ (1000, 2000) B/s，首见给瞬时值
    time.sleep(0.3)
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 400, 800)])
    b2 = monkey_agg.build()
    a2 = next(a for a in b2.apps if a.proc_name == "app.exe")
    assert 600 < a2.rate_up < 1400, f"瞬时速率异常: {a2.rate_up:.0f}"
    assert 1200 < a2.rate_down < 2800, f"瞬时速率异常: {a2.rate_down:.0f}"
    # 第 3 拍：无新增 → EMA 向 0 衰减（停流的软件速率归零，不赖榜）
    time.sleep(0.3)
    monkey_agg.clash.state = _fake_clash_state([_conn("c1", "app.exe", 400, 800)])
    b3 = monkey_agg.build()
    a3 = next(a for a in b3.apps if a.proc_name == "app.exe")
    assert a3.rate_up < a2.rate_up and a3.rate_down < a2.rate_down, "停流未衰减"
    # 第 4 拍：连接彻底消失 → 该行不在榜；累计值保持单调不回退
    time.sleep(0.3)
    monkey_agg.clash.state = _fake_clash_state([])
    monkey_agg.build()
    assert monkey_agg._proc_cum["app.exe"] == (400, 800)
    print("实时速率 EMA（差分/平滑/衰减）PASS")


def test_dns_lifecycle_follows_mode(monkey_agg: Aggregator):
    """R6：DNS 读取器跟随数据源模式启停（TUN 单源不消费 DNS → 停掉常驻
    PowerShell 省内存；已停不得重复 stop；回 legacy 要能再拉起）。"""
    calls = {"start": 0, "stop": 0}

    class _FakeDns:
        def start(self):
            calls["start"] += 1
        def stop(self):
            calls["stop"] += 1
        def is_running(self):
            return calls["start"] > calls["stop"]
        def snapshot(self):
            return {}

    monkey_agg.dns = _FakeDns()          # type: ignore
    monkey_agg._started = True           # 模拟真实启动（守卫的本意）
    monkey_agg.poller.poll = lambda: _fake_sys([("1.2.3.4", 443, "app.exe", "tcp")])
    # legacy 模式：拉起
    monkey_agg.tun.snapshot = lambda: TunState.OFF  # type: ignore
    monkey_agg.clash.state = _fake_clash_state([])
    monkey_agg.build()
    assert calls["start"] == 1 and calls["stop"] == 0, calls
    # TUN 模式：停一次
    monkey_agg.tun.snapshot = lambda: TunState.ON  # type: ignore
    monkey_agg.clash.state = _fake_clash_state([_tun_conn("c1", "app.exe", 1, 2)])
    monkey_agg.build()
    assert calls["stop"] == 1, calls
    # 持续 TUN：不得重复 stop（is_running 守卫）
    monkey_agg.build()
    assert calls["stop"] == 1, f"重复 stop: {calls}"
    # 回 legacy：重新拉起
    monkey_agg.tun.snapshot = lambda: TunState.OFF  # type: ignore
    monkey_agg.build()
    assert calls["start"] == 2 and calls["stop"] == 1, calls
    monkey_agg._started = False
    print("DNS 读取器跟随数据源模式启停 PASS")


if __name__ == "__main__":
    from core.tun_detect import TunState  # noqa: E402
    agg = Aggregator()
    agg.start = lambda: None  # 隔离：阻止 build() 拉起真实数据源线程
    agg.clash.state.__dict__["online"] = False
    agg.tun.snapshot = lambda: TunState.OFF  # type: ignore
    test_evil_sysdata(agg)
    test_evil_dns_map(agg)
    test_rank_cum_monotonic(agg)
    test_rank_cum_mihomo_restart(agg)
    test_rank_cum_multi_pid(agg)
    test_legacy_pid_zero_no_cross_attribution(agg)
    test_legacy_pid_zero_merges_with_real_pid(agg)
    test_rank_cum_negative_delta(agg)
    test_concurrent_build()
    test_local_semantics()
    test_tun_single_source(agg)
    test_tun_unknown_falls_back(agg)
    test_mode_switch_cum_continuous(agg)
    test_force_legacy(agg)
    test_tun_key_unique(agg)
    test_proc_rate_ema(agg)
    test_dns_lifecycle_follows_mode(agg)
    agg.stop()
    print("adv_aggregator 全部通过")

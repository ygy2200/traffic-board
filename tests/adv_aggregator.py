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


def test_local_semantics():
    f = ConnectionPoller.is_local
    assert f("127.0.0.1") and f("::1") and f("192.168.1.5") and f("10.0.0.1") and f("fe80::1")
    assert not f("8.8.8.8") and not f("93.184.216.34")
    assert f("") and f("999.999.999.999") and f("not an ip")  # 非法输入按本地处理（防误标外网）
    print("本地网段判定 PASS")


if __name__ == "__main__":
    agg = Aggregator()
    agg.start = lambda: None  # 隔离：阻止 build() 拉起真实数据源线程
    agg.clash.state.__dict__["online"] = False
    test_evil_sysdata(agg)
    test_evil_dns_map(agg)
    test_rank_cum_monotonic(agg)
    test_rank_cum_mihomo_restart(agg)
    test_rank_cum_multi_pid(agg)
    test_rank_cum_negative_delta(agg)
    test_concurrent_build()
    test_local_semantics()
    agg.stop()
    print("adv_aggregator 全部通过")

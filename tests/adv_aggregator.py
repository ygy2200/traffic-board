# -*- coding: utf-8 -*-
"""对抗测试：聚合层对畸形进程/网络数据的免疫 + 并发安全。"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.aggregator import Aggregator  # noqa: E402
from core.connections import ConnectionPoller, Snapshot, SysConn  # noqa: E402


def _fake_sys(pairs):
    snap = Snapshot()
    for i, (ip, port, name, proto) in enumerate(pairs, start=1):
        snap.conns.append(SysConn(proto=proto, raddr_ip=ip, raddr_port=port,
                                  laddr_port=0, pid=i, proc_name=name))
    return snap


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
    test_concurrent_build()
    test_local_semantics()
    agg.stop()
    print("adv_aggregator 全部通过")

"""对抗测试：物理网卡甄别（TUN 双重计数修复）+ 历史累计差分（尾字节修复）。"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.nics import physical_nics  # noqa: E402


def _st(isup=True, speed=1000, mtu=1500):
    return NS(isup=isup, speed=speed, mtu=mtu, duplex=2)


def test_physical_only():
    got = physical_nics({"以太网": _st(), "WLAN": _st(isup=False, speed=0)})
    assert got == {"以太网"}, got
    print("纯物理拓扑 PASS")


def test_tun_double_count():
    """TUN 虚拟网卡必须排除（否则全机流量虚高 2 倍）。"""
    got = physical_nics({
        "以太网": _st(speed=1000),                    # 物理
        "Wintun": _st(speed=0),                       # mihomo wintun
        "Loopback Pseudo-Interface 1": _st(speed=1073),
        "TAP-Windows Adapter": _st(speed=0),
        "以太网 2": _st(speed=0),                      # wintun 在部分驱动下显示为此名
    })
    assert got == {"以太网"}, got
    print("TUN 虚拟网卡排除 PASS")


def test_substring_false_positive():
    """防误伤：含 tap/lo 子串的物理网卡名不得被排除。"""
    got = physical_nics({
        "Ethernet Adapter": _st(),
        "Local Area Connection": _st(),
        "以太网": _st(),
    })
    assert got == {"Ethernet Adapter", "Local Area Connection", "以太网"}, got
    print("子串误伤防护 PASS")


def test_fallbacks_never_empty():
    # 全虚拟且全启用：兜底 1 生效（不返回空集导致 0 流量）
    got = physical_nics({"Wintun": _st(speed=0), "Tap": _st(speed=0)})
    assert got == {"Wintun", "Tap"}, got
    # 唯一物理网卡不报速率：兜底 3 生效
    got2 = physical_nics({"以太网": _st(speed=0)})
    assert got2 == {"以太网"}, got2
    print("逐级兜底（绝不返回空）PASS")


def test_evil_input():
    assert physical_nics({}) == set()
    assert physical_nics(None) == set()
    assert physical_nics("not a dict") == set()
    got = physical_nics({"": _st(), "  \t\n ": _st(speed=0), "OK": _st()})
    assert got == {"OK"}, got            # 空/纯空白名按虚拟排除
    # 缺字段的对象不得抛异常
    got2 = physical_nics({"无字段": NS(), "坏速率": NS(isup=True, speed="abc")})
    assert "无字段" in got2 or True       # 只要求不抛
    print("畸形输入免疫 PASS")


def test_proc_cum_deltas_tail_bytes():
    """历史差分必须抓到连接关闭瞬间的尾字节（R3 修复点）。"""
    from core.aggregator import Aggregator
    from core.clash_api import ClashConn, ClashState
    from core.tun_detect import TunState

    agg = Aggregator()
    agg.start = lambda: None
    agg.tun.snapshot = lambda: TunState.ON

    def _conn(cid, up, down):
        return ClashConn(conn_id=cid, process="app.exe", host="a.com",
                         dest_ip="", dest_port=443, network="tcp",
                         chains=["香港01"], upload=up, download=down)

    # 第 1 拍：连接传 100/200
    agg.clash.state = ClashState(online=True, conns=[_conn("c1", 100, 200)])
    agg.build()
    d1 = agg.proc_cum_deltas()
    assert d1.get("app.exe") == (100, 200), d1
    # 第 2 拍：连接涨到 150/260
    agg.clash.state = ClashState(online=True, conns=[_conn("c1", 150, 260)])
    agg.build()
    d2 = agg.proc_cum_deltas()
    assert d2.get("app.exe") == (50, 60), d2
    # 第 3 拍：连接直接消失（两次落库间关闭）——尾字节 0/0 但连接已断，
    # 旧实现此分钟该软件 0 记录且 150/260 之后无增量；新实现增量已在 d2 收齐
    agg.clash.state = ClashState(online=True, conns=[])
    agg.build()
    d3 = agg.proc_cum_deltas()
    assert d3 == {}, d3                  # 无新增量（150/260 已在 d2 全部结算）
    # 累计值保持不回退（与实时排行同源）
    assert agg._proc_cum["app.exe"] == (150, 260)
    agg.stop()
    print("历史累计差分（尾字节不丢）PASS")


if __name__ == "__main__":
    test_physical_only()
    test_tun_double_count()
    test_substring_false_positive()
    test_fallbacks_never_empty()
    test_evil_input()
    test_proc_cum_deltas_tail_bytes()
    print("adv_nics 全部通过")

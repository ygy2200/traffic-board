# -*- coding: utf-8 -*-
"""数据聚合：系统连接表 + DNS 缓存 + Clash API → 每软件的渠道画像。

渠道判定优先级：
1. Clash API 在线：按进程名合并其连接（域名/节点/字节最全）
2. 系统连接表：目标=本机代理端口 → 经代理；本机/内网 → 本地；其余 → 直连
域名解析：Clash host > DNS 缓存 > 裸 IP。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .appnames import display_name
from .clash_api import ClashApiPoller
from .connections import ConnectionPoller, Snapshot
from .dns_cache import DnsCacheReader

_CHANNEL_PROXY = "proxy"
_CHANNEL_DIRECT = "direct"
_CHANNEL_LOCAL = "local"
_CHANNEL_LABEL = {_CHANNEL_PROXY: "代理", _CHANNEL_DIRECT: "直连", _CHANNEL_LOCAL: "本地"}


@dataclass
class ConnView:
    proto: str
    target: str            # 域名或 IP:port
    channel: str
    node: str = ""         # 代理节点（仅代理渠道）
    upload: int = 0
    download: int = 0
    from_clash: bool = False
    conn_id: str = ""      # Clash 连接 id（用于断开）
    raddr_ip: str = ""     # 裸 IP（Wireshark 过滤用）
    raddr_port: int = 0


@dataclass
class AppView:
    pid: int
    proc_name: str
    display: str
    conns: List[ConnView] = field(default_factory=list)

    @property
    def proxy_count(self) -> int:
        return sum(1 for c in self.conns if c.channel == _CHANNEL_PROXY)

    @property
    def direct_count(self) -> int:
        return sum(1 for c in self.conns if c.channel in (_CHANNEL_DIRECT, _CHANNEL_LOCAL))

    @property
    def upload(self) -> int:
        return sum(c.upload for c in self.conns)

    @property
    def download(self) -> int:
        return sum(c.download for c in self.conns)


@dataclass
class Board:
    apps: List[AppView] = field(default_factory=list)
    clash_online: bool = False
    clash_version: str = ""
    dns_count: int = 0
    sys_conn_count: int = 0
    sys_partial: bool = False


class Aggregator:
    """组装三个数据源为一次看板快照。所有源均可缺席。"""

    def __init__(self, proxy_ports: Optional[List[int]] = None) -> None:
        self.poller = ConnectionPoller()
        self.dns = DnsCacheReader()
        self.clash = ClashApiPoller()
        from .history import HistoryStore
        self.history = HistoryStore()
        from .known import KnownPeers
        self.known = KnownPeers()
        self.proxy_ports = set(proxy_ports or [7890, 7897, 10809])
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self.dns.start()
            self.clash.start()

    def stop(self) -> None:
        if self._started:
            self.dns.stop()
            self.clash.stop()
            self.history.close()
            self.known.close()

    def build(self) -> Board:
        self.start()
        sys_snap: Snapshot = self.poller.poll()
        dns_map: Dict[str, str] = self.dns.snapshot()
        clash = self.clash.snapshot()

        board = Board(
            clash_online=clash.online,
            clash_version=clash.version,
            dns_count=len(dns_map),
            sys_conn_count=len(sys_snap.conns),
            sys_partial=sys_snap.partial,
        )

        apps: Dict[int, AppView] = {}

        def app_of(pid: int, proc_name: str) -> AppView:
            if pid not in apps:
                apps[pid] = AppView(pid=pid, proc_name=proc_name, display=display_name(proc_name))
            return apps[pid]

        # --- Clash 连接（在线时提供代理侧明细；DIRECT 的也会重复出现在系统表，去重见下） ---
        clash_used_targets: set = set()
        if clash.online:
            for cc in clash.conns:
                proc_name = cc.process or "未知进程"
                pid = self._pid_of(sys_snap, proc_name)
                node = self._node_of(cc.chains)
                target = cc.host or (f"{cc.dest_ip}:{cc.dest_port}" if cc.dest_ip else "未知")
                channel = _CHANNEL_PROXY if node else _CHANNEL_DIRECT
                cv = ConnView(proto=cc.network, target=target, channel=channel,
                              node=node, upload=cc.upload, download=cc.download,
                              from_clash=True, conn_id=cc.conn_id,
                              raddr_ip=cc.dest_ip, raddr_port=cc.dest_port)
                app_of(pid, proc_name).conns.append(cv)
                clash_used_targets.add((proc_name, target, cv.proto))

        # --- 系统连接表（全量兜底，去 Clash 已覆盖的进程+目标） ---
        for sc in sys_snap.conns:
            if sc.proto == "udp":
                target = f"UDP 端口 {sc.laddr_port}"
                channel = _CHANNEL_LOCAL
                node = ""
            else:
                if not ConnectionPoller.is_local(sc.raddr_ip):
                    channel = _CHANNEL_DIRECT
                elif sc.raddr_port in self.proxy_ports:
                    channel, _ = _CHANNEL_PROXY, sc.raddr_port  # 发往本机代理端口=经代理
                else:
                    channel = _CHANNEL_LOCAL
                if channel == _CHANNEL_PROXY:
                    target = f"{dns_map.get(sc.raddr_ip, sc.raddr_ip)}:{sc.raddr_port}（代理入口）"
                    node = ""
                else:
                    dom = dns_map.get(sc.raddr_ip)
                    target = f"{dom}:{sc.raddr_port}" if dom else f"{sc.raddr_ip}:{sc.raddr_port}"
                    node = ""
            key = (sc.proc_name, target, sc.proto)
            if clash.online and channel == _CHANNEL_DIRECT and key in clash_used_targets:
                continue  # Clash 已按域名展示同进程同目标，避免双份
            app_of(sc.pid, sc.proc_name).conns.append(
                ConnView(proto=sc.proto, target=target, channel=channel, node=node,
                         raddr_ip=sc.raddr_ip, raddr_port=sc.raddr_port))

        board.apps = sorted(apps.values(), key=lambda a: (-len(a.conns), a.display.lower()))
        return board

    def _pid_of(self, snap: Snapshot, proc_name: str) -> int:
        for sc in snap.conns:
            if sc.proc_name.lower() == proc_name.lower():
                return sc.pid
        return 0

    @staticmethod
    def _node_of(chains: List[str]) -> str:
        """chains[0] 为实际出口（实测 mihomo 返回 ['香港01', '机场组']）；
        'DIRECT'/'REJECT' 等伪节点不算。"""
        for name in (chains or []):
            if name and name not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "GLOBAL"):
                return name
        return ""


CHANNEL_LABEL = _CHANNEL_LABEL

if __name__ == "__main__":
    agg = Aggregator()
    agg.start()
    import time
    time.sleep(3)
    b = agg.build()
    print(f"Clash: {'在线 ' + b.clash_version if b.clash_online else '离线(降级)'} | "
          f"DNS映射 {b.dns_count} | 系统连接 {b.sys_conn_count} | 软件数 {len(b.apps)}")
    for a in b.apps[:6]:
        print(f"  {a.display} ({a.proc_name}) 连接{len(a.conns)} 代理{a.proxy_count}")
        for cv in a.conns[:3]:
            tag = CHANNEL_LABEL[cv.channel] + (f"·{cv.node}" if cv.node else "")
            print(f"     [{tag}] {cv.target} ↑{cv.upload} ↓{cv.download}")
    agg.stop()

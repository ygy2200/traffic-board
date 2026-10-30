# -*- coding: utf-8 -*-
"""数据聚合：TUN 单源（mihomo 全系统连接表）或 legacy 双源 → 每软件渠道画像。

数据源调度（build 时判定）：
1. TUN 单源模式：Clash API 在线 + TUN 判定 ON（TunDetector 双确认）→
   mihomo /connections 即全系统连接表（实测 process 100% 覆盖），
   跳过 psutil 全表扫描与 DNS 缓存消费（提速主路径）
2. legacy 双源模式：其余情况走原有三源合并，逻辑不变

渠道判定：Clash chains 含真节点 → 代理，否则直连；
legacy 下系统表另按目标端口/网段判定。
域名解析：Clash host > DNS 缓存 > 裸 IP（legacy）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .appnames import display_name
from .clash_api import ClashApiPoller, ClashState
from .connections import ConnectionPoller, Snapshot
from .dns_cache import DnsCacheReader
from .tun_detect import TunDetector, TunState

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
    _upload_override: Optional[int] = None    # 进程级单调累计（见 Aggregator._update_proc_cum）
    _download_override: Optional[int] = None
    rate_up: float = 0.0                      # 实时速率 B/s（EMA 平滑，R5 新增）
    rate_down: float = 0.0

    @property
    def key(self) -> str:
        """UI 行键：按软件而非 pid（mihomo TUN 单源无 pid，legacy 同名合并后
        pid 也只是代表，按 pid 键控会让全 0 行互相覆盖）。"""
        return self.proc_name.lower()

    @property
    def proxy_count(self) -> int:
        return sum(1 for c in self.conns if c.channel == _CHANNEL_PROXY)

    @property
    def direct_count(self) -> int:
        return sum(1 for c in self.conns if c.channel in (_CHANNEL_DIRECT, _CHANNEL_LOCAL))

    @property
    def upload(self) -> int:
        if self._upload_override is not None:
            return self._upload_override
        return sum(c.upload for c in self.conns)

    @upload.setter
    def upload(self, v: int) -> None:
        self._upload_override = v

    @property
    def download(self) -> int:
        if self._download_override is not None:
            return self._download_override
        return sum(c.download for c in self.conns)

    @download.setter
    def download(self, v: int) -> None:
        self._download_override = v


@dataclass
class Board:
    apps: List[AppView] = field(default_factory=list)
    clash_online: bool = False
    clash_version: str = ""
    dns_count: int = 0
    sys_conn_count: int = 0
    sys_partial: bool = False
    data_mode: str = "legacy"   # 'tun'=TUN 单源 | 'legacy'=传统双源
    tun_state: str = "off"      # TunState.value（on/off/unknown）
    session_up: int = 0         # 本次运行全机累计上行字节（PollWorker 填）
    session_down: int = 0       # 本次运行全机累计下行字节


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
        self.tun = TunDetector(self.clash)
        self._force_legacy = False
        self._started = False
        # 进程级单调累计（修复排行数值随连接断开回退的问题）
        self._conn_last: Dict[str, tuple] = {}    # conn_id -> (up, down) 最后快照
        self._proc_cum: Dict[str, tuple] = {}     # proc_name(lower) -> (up, down) 累计
        self._cum_flushed: Dict[str, tuple] = {}  # 已写入历史的累计水位（差分用）
        self._proc_rate: Dict[str, tuple] = {}    # proc_name(lower) -> (up_bps, down_bps) EMA
        self._cum_last_t: float = 0.0             # 上次 _update_proc_cum 的单调时钟

    def set_force_legacy(self, force: bool) -> None:
        """设置页"强制传统模式"开关（排查用）；下一次 build 生效。"""
        self._force_legacy = bool(force)

    def start(self) -> None:
        if not self._started:
            self._started = True
            self.clash.start()
            self.tun.start()
            # dns 不在此启动：由 build() 按数据源模式管理（legacy 才需要），
            # 且仅当本 Aggregator 真实启动过（测试 stub start() 时不启真进程）

    def stop(self) -> None:
        if self._started:
            self.dns.stop()
            self.clash.stop()
            self.tun.stop()
            self.history.close()
            self.known.close()

    def build(self) -> Board:
        self.start()
        clash = self.clash.snapshot()
        tun = self.tun.snapshot()
        use_tun = (clash.online and not self._force_legacy and tun == TunState.ON)
        # DNS 缓存只在 legacy 模式被消费（TUN 单源的域名来自 Clash host 字段）。
        # 跟随模式启停：TUN 下停掉常驻 PowerShell（省 ~60MB 内存 + 周期 wakeup，
        # 也是历史遗留"强杀后 PowerShell 孤儿进程"的根治）；legacy 下幂等拉起。
        # _started 守卫：测试 stub 掉 start() 时不碰真实子进程。
        if self._started:
            if use_tun:
                if self.dns.is_running():
                    self.dns.stop()
            else:
                self.dns.start()
        if use_tun:
            board = self._build_tun(clash)
            board.data_mode = "tun"
        else:
            board = self._build_legacy(clash)
            board.data_mode = "legacy"
        board.tun_state = tun.value
        return board

    def _build_tun(self, clash: ClashState) -> Board:
        """TUN 单源：mihomo 连接表即全系统连接表。跳过 psutil 与 DNS 缓存。"""
        board = Board(clash_online=True, clash_version=clash.version)
        self._update_proc_cum(clash)
        apps: Dict[str, AppView] = {}
        for cc in clash.conns:
            proc_name = cc.process or "未知进程"
            k = proc_name.lower()
            a = apps.get(k)
            if a is None:
                a = apps[k] = AppView(pid=0, proc_name=proc_name,
                                      display=display_name(proc_name))
            node = self._node_of(cc.chains)
            target = cc.host or (f"{cc.dest_ip}:{cc.dest_port}" if cc.dest_ip else "未知")
            a.conns.append(ConnView(
                proto=cc.network, target=target,
                channel=_CHANNEL_PROXY if node else _CHANNEL_DIRECT,
                node=node, upload=cc.upload, download=cc.download,
                from_clash=True, conn_id=cc.conn_id,
                raddr_ip=cc.dest_ip, raddr_port=cc.dest_port))
        # 排行显示进程级单调累计值（与 legacy 相同语义，防连接断开回退）
        for a in apps.values():
            cum = self._proc_cum.get(a.proc_name.lower())
            if cum and (cum[0] or cum[1]):
                a.upload, a.download = cum
            rate = self._proc_rate.get(a.proc_name.lower())
            if rate:
                a.rate_up, a.rate_down = rate
        board.apps = sorted(apps.values(),
                            key=lambda a: (-len(a.conns), a.display.lower()))
        return board

    def _build_legacy(self, clash: ClashState) -> Board:
        """传统双源：系统连接表 + DNS 缓存 + Clash API（v1.5.5 原逻辑）。"""
        sys_snap: Snapshot = self.poller.poll()
        dns_map: Dict[str, str] = self.dns.snapshot()

        board = Board(
            clash_online=clash.online,
            clash_version=clash.version,
            dns_count=len(dns_map),
            sys_conn_count=len(sys_snap.conns),
            sys_partial=sys_snap.partial,
        )

        apps: Dict[int | str, AppView] = {}

        def app_of(pid: int, proc_name: str) -> AppView:
            # pid=0 = Clash 有这条连接但系统表里找不到该进程的 pid（find-process-mode
            # 关闭、进程刚起、权限不足）。0 不能当键：否则全部无名进程挤进同一个
            # AppView，第一个到的进程名给所有人戴帽，后到的软件整行从排行里消失。
            # 无名时按名分桶，同名合仍交给下面的 merged（它会回填真实 pid）。
            k = pid if pid else "n:" + proc_name.lower()
            if k not in apps:
                apps[k] = AppView(pid=pid, proc_name=proc_name, display=display_name(proc_name))
            return apps[k]

        # --- Clash 连接（在线时提供代理侧明细；DIRECT 的也会重复出现在系统表，去重见下） ---
        clash_used_targets: set = set()
        clash_used_procs: set = set()
        pid_map: Dict[str, int] = {}     # proc_name(lower) -> pid，一次 build 建一次索引
        for sc in sys_snap.conns:
            pid_map.setdefault(sc.proc_name.lower(), sc.pid)
        if clash.online:
            self._update_proc_cum(clash)
            for cc in clash.conns:
                proc_name = cc.process or "未知进程"
                pid = self._pid_of(sys_snap, proc_name, pid_map)
                node = self._node_of(cc.chains)
                target = cc.host or (f"{cc.dest_ip}:{cc.dest_port}" if cc.dest_ip else "未知")
                channel = _CHANNEL_PROXY if node else _CHANNEL_DIRECT
                cv = ConnView(proto=cc.network, target=target, channel=channel,
                              node=node, upload=cc.upload, download=cc.download,
                              from_clash=True, conn_id=cc.conn_id,
                              raddr_ip=cc.dest_ip, raddr_port=cc.dest_port)
                app_of(pid, proc_name).conns.append(cv)
                clash_used_targets.add((proc_name, target, cv.proto))
                clash_used_procs.add(proc_name.lower())

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
            if clash.online and channel == _CHANNEL_PROXY \
                    and (key in clash_used_targets
                         or sc.proc_name.lower() in clash_used_procs):
                # Clash 已按真实域名展示该进程的代理流量；
                # "127.0.0.1:7890（代理入口）"行无信息量，省去（2026-10-04 复盘项）
                continue
            app_of(sc.pid, sc.proc_name).conns.append(
                ConnView(proto=sc.proto, target=target, channel=channel, node=node,
                         raddr_ip=sc.raddr_ip, raddr_port=sc.raddr_port))

        # 同名进程合并为一行：Clash 流量按进程名归因，多实例分行会让字节
        # 在行间秒级漂移（pid 归因随系统表快照变化），且"软件排行"本应按软件聚合
        merged: Dict[str, AppView] = {}
        for a in apps.values():
            k = a.proc_name.lower()
            if k in merged:
                m = merged[k]
                m.conns.extend(a.conns)
                if a.pid and (not m.pid or a.pid < m.pid):
                    m.pid = a.pid
            else:
                merged[k] = a

        # Clash per-conn 字节是"连接生命周期累计"，连接断开即从快照消失，
        # 直接求和会让排行数值跳变回退。改用进程级单调累计覆盖显示值：
        # 只要累计器里有该软件的值就显示（该软件的 Clash 连接暂时消失时数值不闪零）
        if clash.online:
            for a in merged.values():
                cum = self._proc_cum.get(a.proc_name.lower())
                if cum and (cum[0] or cum[1]):
                    a.upload, a.download = cum
                rate = self._proc_rate.get(a.proc_name.lower())
                if rate:
                    a.rate_up, a.rate_down = rate

        board.apps = sorted(merged.values(), key=lambda a: (-len(a.conns), a.display.lower()))
        return board

    def _update_proc_cum(self, clash: ClashState) -> None:
        """以 conn_id 差分累加每进程字节。断开连接最后一次增量已被计入，
        故累计单调递增。内核重启后连接 id 全新、从零差分，旧累计自然保留
        （uploadTotal/downloadTotal 是当前活跃连接总和，会随断开下降，
        不能用作重启检测）。单连接字节倒退用 max(0,·) 吞掉防负增量。

        同时维护每进程实时速率（EMA 平滑）：本次差分 / 距上次 build 的
        真实耗时。速率只反映"现在谁在跑量"，与累计值解耦（R5 排行新增
        按速率排序的数据源）。"""
        now = time.monotonic()
        dt = now - self._cum_last_t if self._cum_last_t else 0.0
        self._cum_last_t = now
        seen = set()
        raw: Dict[str, tuple] = {}               # 本次 tick 每进程原始增量
        for cc in clash.conns:
            seen.add(cc.conn_id)
            lu, ld = self._conn_last.get(cc.conn_id, (0, 0))
            du, dd = max(0, cc.upload - lu), max(0, cc.download - ld)
            if du or dd:
                key = (cc.process or "未知进程").lower()
                cu, cd = self._proc_cum.get(key, (0, 0))
                self._proc_cum[key] = (cu + du, cd + dd)
                ru, rd = raw.get(key, (0, 0))
                raw[key] = (ru + du, rd + dd)
            self._conn_last[cc.conn_id] = (cc.upload, cc.download)
        for gone in [cid for cid in self._conn_last if cid not in seen]:
            del self._conn_last[gone]
        # 速率 EMA：首见直接给瞬时值（无预热偏差）；之后 0.6 旧 + 0.4 新；
        # 无增量的进程向 0 衰减（停了的软件速率归零，不会赖在榜上）。
        # 无可用时间基（首拍/时钟异常）时完全不更新——宁可没有速率也不造假。
        usable_dt = dt if 0.05 < dt < 10.0 else 0.0
        if not usable_dt:
            return
        for key in set(raw) | set(self._proc_rate):
            prev = self._proc_rate.get(key)
            nu, nd = raw.get(key, (0, 0))
            inst_u, inst_d = nu / usable_dt, nd / usable_dt
            if prev is None:
                self._proc_rate[key] = (inst_u, inst_d)
            else:
                self._proc_rate[key] = (prev[0] * 0.6 + inst_u * 0.4,
                                        prev[1] * 0.6 + inst_d * 0.4)

    def proc_cum_deltas(self) -> Dict[str, tuple]:
        """自上次调用以来每进程累计字节增量（历史落库专用）。

        与 _proc_cum 同源：连接断开瞬间的尾字节已被 _update_proc_cum
        计入累计，分钟差分不会再丢关闭连接的尾部（R3 修复：旧实现按
        活跃连接快照求和做差，连接在两次落库间关闭即少算）。
        返回值：{proc_name(lower): (up_delta, down_delta)}，只含有增量的项。"""
        deltas: Dict[str, tuple] = {}
        for key, (u, d) in self._proc_cum.items():
            lu, ld = self._cum_flushed.get(key, (0, 0))
            du, dd = u - lu, d - ld
            if du or dd:
                deltas[key] = (max(0, du), max(0, dd))
            self._cum_flushed[key] = (u, d)
        return deltas

    def _pid_of(self, snap: Snapshot, proc_name: str,
                name_map: Optional[Dict[str, int]] = None) -> int:
        """按进程名找 pid（展示用）。name_map 可传入预建索引避免
        O(连接数×系统连接数) 线性扫（_build_legacy 每次 build 都会调）。"""
        if name_map is None:
            name_map = {}
            for sc in snap.conns:
                name_map.setdefault(sc.proc_name.lower(), sc.pid)
        return name_map.get(proc_name.lower(), 0)

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
          f"模式 {b.data_mode}(tun={b.tun_state}) | DNS映射 {b.dns_count} | "
          f"系统连接 {b.sys_conn_count} | 软件数 {len(b.apps)}")
    for a in b.apps[:6]:
        print(f"  {a.display} ({a.proc_name}) 连接{len(a.conns)} 代理{a.proxy_count}")
        for cv in a.conns[:3]:
            tag = CHANNEL_LABEL[cv.channel] + (f"·{cv.node}" if cv.node else "")
            print(f"     [{tag}] {cv.target} ↑{cv.upload} ↓{cv.download}")
    agg.stop()

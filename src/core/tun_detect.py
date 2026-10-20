# -*- coding: utf-8 -*-
"""TUN 模式判定器：fake-ip DNS 行为探测（主证据）+ mihomo /configs 自报（辅证据）。

判定结果供 Aggregator 选数据源：
- ON      : DNS 行为探测返回 fake-ip（198.18.0.0/15）——TUN dns-hijack 生效，
            即全系统 DNS/流量正被 mihomo 接管 → 单源模式
- UNKNOWN : API 自报 TUN 开但行为探测不符（redir-host 模式 / 劫持失效）→
            调用方应保守回退 legacy 双源（防直连流量漏报）
- OFF     : 行为探测非 fake-ip 且 API 未报 TUN / API 离线 / 异常

对抗性设计（2026-10-03 本机实测教训）：
1. FlClash 0.8.98 的 TUN 由 HelperService 实现，**运行中 /configs 的
   tun.enable 恒为 false**（实测），API 自报只能当辅助证据；
2. psutil.net_if_addrs 枚举不到 wintun 虚拟网卡（实测），网卡证据不可用；
3. 雷神等加速器劫持路由但不劫持 DNS 到 fake-ip，不会造成假 ON；
4. 探测域名用 www.google.com（FlClash fake-ip-filter 不含它，恒返回 fake-ip；
   mihomo redir-host 模式下返回真实 IP → 判 UNKNOWN 回退，方向保守）。
"""
from __future__ import annotations

import ipaddress
import os
import socket
import struct
import threading
from enum import Enum
from typing import List, Optional, Tuple

from .clash_api import ClashApiPoller

_FAKEIP_NET = ipaddress.ip_network("198.18.0.0/15")   # mihomo fake-ip 默认段
_PROBE_DOMAIN = "www.google.com"
_PERIOD = 15.0          # 正常判定周期（秒）——TUN 开关切换后看板最迟 15s 跟上
_PERIOD_MAX = 120.0     # 连续异常后的退避上限


class TunState(str, Enum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


def fakeip_probe(server: str = "8.8.8.8", domain: str = _PROBE_DOMAIN,
                 timeout: float = 1.5) -> Optional[str]:
    """向公共 DNS 发一次 A 查询（UDP 53）。

    TUN dns-hijack（any:53）生效时，该查询被 mihomo 截走并返回 fake-ip。
    返回：'fake'（命中 198.18.0.0/15，TUN 接管铁证）/
          'real'（拿到其他地址，未被劫持或真实递归）/
          None（超时/畸形应答）。"""
    txid = os.urandom(2)
    labels = domain.split(".")
    if not all(p and p.isascii() and len(p) <= 63 for p in labels):
        return None
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in labels) + b"\x00"
    pkt = txid + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + qname + b"\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(pkt, (server, 53))
        data, _ = s.recvfrom(512)
    except Exception:
        return None
    finally:
        s.close()
    if len(data) < 12 or data[:2] != txid:
        return None
    addrs = _parse_a_records(data)
    if not addrs:
        return None
    for a in addrs:
        try:
            if ipaddress.ip_address(a) in _FAKEIP_NET:
                return "fake"
        except ValueError:
            continue
    return "real"


def _parse_a_records(data: bytes) -> List[str]:
    """从 DNS 应答提取 A 记录（畸形输入返回空表，不抛异常）。"""
    try:
        i = 12
        while data[i]:                       # 跳过 question qname
            i += data[i] + 1
        i += 5                               # 0 终止 + qtype + qclass
        addrs: List[str] = []
        while i + 12 <= len(data):
            if data[i] & 0xC0:               # 压缩指针
                i += 2
            else:
                while data[i]:               # 内联域名
                    i += data[i] + 1
                i += 1
            rtype, _rc, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
            i += 10
            if rtype == 1 and rdlen == 4:
                addrs.append(".".join(str(b) for b in data[i:i + 4]))
            i += rdlen
        return addrs
    except Exception:
        return []


def evaluate(probe: Optional[str], api_tun: bool) -> TunState:
    """纯函数判定（可单测）。probe=fakeip_probe() 结果；api_tun=/configs 自报。"""
    if probe == "fake":
        return TunState.ON            # 行为铁证：DNS 已被 mihomo 接管
    if api_tun:
        return TunState.UNKNOWN       # API 说开但行为不符 → 存疑，保守回退
    return TunState.OFF


class TunDetector:
    """后台线程周期判定 TUN 状态；依赖 ClashApiPoller 提供端口/在线状态。"""

    def __init__(self, poller: ClashApiPoller, secret: str = "") -> None:
        self._poller = poller
        self._secret = secret
        self._state = TunState.OFF
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._session = None                  # 首次用时才建（requests 懒加载，R6）

    def _ensure_session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="tun-detector")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=7)   # 与 ClashApiPoller 同规：退出必须 join

    def snapshot(self) -> TunState:
        with self._lock:
            return self._state

    # ---- internals ----
    def _api_tun(self, port: int) -> Tuple[bool, bool]:
        """(api_tun, api_ok)：GET /configs 的 tun.enable。任何异常 = (False, False)。"""
        try:
            r = self._ensure_session().get(
                f"http://127.0.0.1:{port}/configs",
                headers=({"Authorization": f"Bearer {self._secret}"}
                         if self._secret else {}),
                timeout=2)
            data = r.json() if r.ok else None
            if isinstance(data, dict) and isinstance(data.get("tun"), dict):
                return data["tun"].get("enable") is True, True
            return False, r.ok
        except Exception:
            return False, False

    def _run(self) -> None:
        period = _PERIOD
        fails = 0
        while not self._stop.is_set():
            poller = self._poller.snapshot()
            if not poller.online or not poller.port:
                # API 离线时行为探测仍可独立判 ON（TUN 在跑但 API 抓不到的极端态）
                st = evaluate(fakeip_probe(), api_tun=False)
                self._store(st)
                if st is not TunState.ON:
                    period = _PERIOD     # API 离线不累计退避（快速跟随其恢复）
            else:
                api_tun, api_ok = self._api_tun(poller.port)
                probe = fakeip_probe()
                st = evaluate(probe, api_tun)
                self._store(st)
                if probe is None or (not api_ok):
                    fails += 1           # 探测超时或 API 异常：退避（网络抖动别空转）
                    if fails >= 3:
                        period = min(period * 2, _PERIOD_MAX)
                else:
                    fails = 0
                    period = _PERIOD
            self._stop.wait(period)

    def _store(self, st: TunState) -> None:
        with self._lock:
            self._state = st


if __name__ == "__main__":
    # 自测：对真实本机跑判定（TUN 开→on；关→off）
    from core.clash_api import ClashApiPoller
    import time
    p = ClashApiPoller()
    p.start()
    d = TunDetector(p)
    d.start()
    time.sleep(3)
    print(f"TUN state: {d.snapshot().value}  (API online={p.snapshot().online})")
    d.stop()
    p.stop()

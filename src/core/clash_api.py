# -*- coding: utf-8 -*-
"""Clash/mihomo 内核 API 自动发现与连接数据拉取（可选增强源）。

自动探测本机常见外部控制器端口；在线时提供进程级代理明细
（域名、节点链、上下行字节）。离线或未开 API 时静默降级。
不绑定 FlClash：任何 mihomo/Clash.Meta 内核均适用。
"""
from __future__ import annotations

import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

_CANDIDATE_PORTS = [9090, 9097, 9095, 9091, 19090]


@dataclass
class ClashConn:
    conn_id: str = ""
    process: str = ""
    host: str = ""
    dest_ip: str = ""
    dest_port: int = 0
    network: str = "tcp"
    chains: List[str] = field(default_factory=list)
    rule: str = ""
    upload: int = 0
    download: int = 0


@dataclass
class ClashState:
    online: bool = False
    port: int = 0
    version: str = ""
    conns: List[ClashConn] = field(default_factory=list)
    upload_total: int = 0
    download_total: int = 0


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def parse_connections_payload(data: dict, version: str = "") -> ClashState:
    """把 /connections 的 JSON 解析为 ClashState。任意字段畸形均不抛异常。"""
    if not isinstance(data, dict):
        return ClashState()
    state = ClashState(online=True, version=version,
                       upload_total=_int(data.get("uploadTotal")),
                       download_total=_int(data.get("downloadTotal")))
    for c in data.get("connections") or []:
        if not isinstance(c, dict):
            continue
        m = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
        chains = c.get("chains")
        port_val = _int(m.get("destinationPort"))
        state.conns.append(ClashConn(
            conn_id=str(c.get("id") or ""),
            process=str(m.get("process") or ""),
            host=str(m.get("host") or ""),
            dest_ip=str(m.get("destinationIP") or ""),
            dest_port=port_val if 0 <= port_val <= 65535 else 0,
            network=str(m.get("network") or "tcp"),
            chains=chains if isinstance(chains, list) else [],
            rule=str(c.get("rule") or ""),
            upload=_int(c.get("upload")),
            download=_int(c.get("download")),
        ))
    return state


class ClashApiPoller:
    """后台线程 1 秒轮询 /connections；断线自动重探测。"""

    def __init__(self, secret: str = "") -> None:
        self.secret = secret
        self.state = ClashState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._session = requests.Session()
        self._last_port: Optional[int] = None   # 上次成功端口，重探测优先试它

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="clash-api-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # 等轮询线程退出：最坏情况 = 一个 GET 超时(1.5s) + _stop.wait(1.0)，
        # 探测循环已响应停止信号。避免 os._exit 时进程终止例程杀掉阻塞中的线程。
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=7)

    def snapshot(self) -> ClashState:
        with self._lock:
            return self.state

    def close_connection(self, conn_id: str) -> bool:
        """断开一条代理连接（DELETE /connections/:id）。"""
        if not self.state.online or not conn_id:
            return False
        port = self.state.port or 9090
        try:
            r = self._session.delete(f"http://127.0.0.1:{port}/connections/{conn_id}",
                                     headers=self._headers(), timeout=3)
            return 200 <= r.status_code < 300
        except Exception:
            return False

    # ---- internals ----
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"} if self.secret else {}

    def _probe(self) -> Optional[int]:
        # 上次成功的端口优先（重探测/重启线程时秒级命中，不必扫完整候选表）
        ports = ([self._last_port] if self._last_port else []) + \
                [p for p in _CANDIDATE_PORTS if p != self._last_port]
        for port in ports:
            if self._stop.is_set():   # 停止信号下立即放弃探测，保证线程快速退出
                return None
            try:
                r = self._session.get(f"http://127.0.0.1:{port}/version",
                                      headers=self._headers(), timeout=1.2)
                if r.ok:
                    self._version = r.json().get("version", "")
                    self._last_port = port
                    return port
            except Exception:
                continue
        return None

    _version = ""

    def _run(self) -> None:
        port = self._probe()
        fail = 0
        while not self._stop.is_set():
            if port is None:
                time.sleep(3)
                port = self._probe()
                continue
            try:
                r = self._session.get(f"http://127.0.0.1:{port}/connections",
                                      headers=self._headers(), timeout=1.5)
                r.raise_for_status()
                state = parse_connections_payload(r.json(), getattr(self, "_version", ""))
                state.port = port
                with self._lock:
                    self.state = state
                fail = 0
            except Exception:
                fail += 1
                if fail >= 10:  # 连续 10 秒失败才视为离线（mihomo 偶发卡顿不应清空排行数据）
                    with self._lock:
                        self.state = ClashState(online=False)
                    port = None
            self._stop.wait(1.0)


if __name__ == "__main__":
    poller = ClashApiPoller()
    poller.start()
    time.sleep(3)
    st = poller.snapshot()
    print(f"online={st.online} port={st.port} version={st.version} conns={len(st.conns)}")
    for c in st.conns[:5]:
        print(f"  {c.process:18.18} {c.host or c.dest_ip} 链={c.chains[:2]} ↑{c.upload} ↓{c.download}")
    poller.stop()

# -*- coding: utf-8 -*-
"""Clash/mihomo 内核 API 自动发现与连接数据拉取（可选增强源）。

自动探测本机常见外部控制器端口；在线时提供进程级代理明细
（域名、节点链、上下行字节）。离线或未开 API 时静默降级。
不绑定 FlClash：任何 mihomo/Clash.Meta 内核均适用。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

_CANDIDATE_PORTS = [9090, 9097, 9095, 9091, 19090]


@dataclass
class ClashConn:
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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="clash-api-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> ClashState:
        with self._lock:
            return self.state

    # ---- internals ----
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"} if self.secret else {}

    def _probe(self) -> Optional[int]:
        for port in _CANDIDATE_PORTS:
            try:
                r = self._session.get(f"http://127.0.0.1:{port}/version",
                                      headers=self._headers(), timeout=0.8)
                if r.ok:
                    self._version = r.json().get("version", "")
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
                if fail >= 3:  # 连续失败视为离线，重新探测
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

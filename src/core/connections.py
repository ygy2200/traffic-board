# -*- coding: utf-8 -*-
"""系统连接表采集（psutil）：所有软件的 TCP/UDP 连接 + 进程归属。

注意：Windows 非管理员只能拿到当前用户进程的完整归属；
提权后覆盖全系统进程。UDP 表无远端地址（系统限制），明细依赖其他数据源。
"""
from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import psutil

_LOCAL_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]

_TCP_ESTABLISHED = {"ESTABLISHED", "SYN_SENT"}


@dataclass
class SysConn:
    proto: str            # 'tcp' | 'udp'
    raddr_ip: str
    raddr_port: int
    laddr_port: int
    pid: int
    proc_name: str
    channel_hint: str = ""   # 'proxy-port' | 'local' | '' (空=待判定)


@dataclass
class Snapshot:
    conns: List[SysConn] = field(default_factory=list)
    partial: bool = False    # True=非管理员，归属覆盖不全


class ConnectionPoller:
    """1 秒级轮询系统连接表；进程名缓存避免高频 psutil.Process 调用。"""

    def __init__(self) -> None:
        self._pid_names: Dict[int, str] = {}
        self._lock = threading.Lock()

    def poll(self) -> Snapshot:
        snap = Snapshot()
        try:
            raw = psutil.net_connections(kind="inet")
        except psutil.AccessDenied:
            snap.partial = True
            return snap
        for c in raw:
            if c.pid is None or c.pid <= 0:
                continue
            proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
            if proto == "tcp":
                if c.status not in _TCP_ESTABLISHED or not c.raddr:
                    continue
            else:
                if not c.laddr:  # udp 仅记录本地端口占用
                    continue
            proc_name = self._name_of(c.pid)
            sc = SysConn(
                proto=proto,
                raddr_ip=c.raddr.ip if c.raddr else "",
                raddr_port=c.raddr.port if c.raddr else 0,
                laddr_port=c.laddr.port if c.laddr else 0,
                pid=c.pid,
                proc_name=proc_name,
            )
            snap.conns.append(sc)
        snap.conns.sort(key=lambda s: s.pid)
        return snap

    def _name_of(self, pid: int) -> str:
        with self._lock:
            if pid in self._pid_names:
                return self._pid_names[pid]
        name = ""
        try:
            p = psutil.Process(pid)
            name = p.name() or ""
        except Exception:
            pass
        with self._lock:
            self._pid_names[pid] = name
        return name

    @staticmethod
    def is_local(ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return True  # 解析不了按本地处理，避免误标外网
        return any(addr in net for net in _LOCAL_NETS)


if __name__ == "__main__":
    poller = ConnectionPoller()
    snap = poller.poll()
    print(f"连接数: {len(snap.conns)} (partial={snap.partial})")
    for sc in snap.conns[:8]:
        tgt = f"{sc.raddr_ip}:{sc.raddr_port}" if sc.raddr_ip else f"udp本地:{sc.laddr_port}"
        print(f"  pid={sc.pid:<7} {sc.proc_name:22.22} {sc.proto:3} {tgt}")

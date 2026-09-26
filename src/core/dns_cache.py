# -*- coding: utf-8 -*-
"""Windows 系统 DNS 缓存读取：IP -> 域名 映射。

主路线：常驻 PowerShell 进程轮询 Get-DnsClientCache（公开 cmdlet）。
理由：裸调 dnsapi.DnsGetCacheDataTable 的内部结构未公开且随版本变化
（实测 wDataLength 恒 0、RDATA 指针链难定位），公开 cmdlet 输出稳定。
降级：PowerShell 不可用时返回空映射，不影响其他数据源。
"""
from __future__ import annotations

import json
import subprocess
import threading
from typing import Dict, Optional

_PS_SCRIPT = (
    "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "while($true){"
    "$c=Get-DnsClientCache -ErrorAction SilentlyContinue |"
    " Where-Object {$_.Type -eq 1 -or $_.Type -eq 28} |"
    " Select-Object Entry,Data | ConvertTo-Json -Compress;"
    "if(-not $c){$c='[]'}"
    "Write-Output $c;"
    "[Console]::Out.Flush();"
    "Start-Sleep -Milliseconds __MS__}"
)


class DnsCacheReader:
    """后台线程持续产出 {ip: domain}。ip_map 属性随时可读（快照）。"""

    def __init__(self, interval_sec: float = 1.0) -> None:
        self.ip_map: Dict[str, str] = {}
        self._interval_ms = int(interval_sec * 1000)
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="dns-cache-reader")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass

    def snapshot(self) -> Dict[str, str]:
        with self._lock:
            return dict(self.ip_map)

    # ---- internals ----
    def _run(self) -> None:
        try:
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                 _PS_SCRIPT.replace("__MS__", str(self._interval_ms))],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                creationflags=flags,
            )
        except Exception:
            return
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            items = self._parse(line)
            new_map: Dict[str, str] = {}
            for it in items:
                ip, domain = str(it.get("Data", "")).rstrip("."), str(it.get("Entry", "")).rstrip(".")
                if ip and domain and not ip.endswith(".in-addr.arpa"):
                    new_map[ip] = domain
            if new_map:
                with self._lock:
                    self.ip_map = new_map

    @staticmethod
    def _parse(line: str) -> list:
        try:
            data = json.loads(line)
        except Exception:
            return []
        if isinstance(data, dict):
            return [data]
        return data if isinstance(data, list) else []


if __name__ == "__main__":
    import time

    r = DnsCacheReader()
    r.start()
    time.sleep(3)
    m = r.snapshot()
    print(f"映射条数: {len(m)}")
    for ip, dom in list(m.items())[:6]:
        print(f"  {ip:18} -> {dom}")
    r.stop()

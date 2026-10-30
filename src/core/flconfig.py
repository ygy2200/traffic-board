# -*- coding: utf-8 -*-
"""读 FlClash / mihomo 自己写的运行时配置：控制器密钥、端口、find-process-mode。

为什么需要：外部控制器 API 是按进程字节数的唯一来源，而 FlClash 默认给控制器
设了 secret。请求不带密钥时内核回 401，旧代码把 401 当作"端口没人监听"跳过，
于是看板永远显示"API 离线"、所有字节列恒为 0，界面上却只有波形在动（走网卡计数）。

密钥只驻留内存：不写日志、不进 CSV 导出、不显示在界面上。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

_LINE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


@dataclass
class FlConfig:
    secret: str = ""
    controller_port: int = 0
    mixed_port: int = 0
    find_process_mode: str = ""
    path: str = ""


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _as_port(v: str) -> int:
    """external-controller 可能是 "127.0.0.1:9090"、":9090" 或裸 9090。"""
    v = _unquote(v)
    tail = v.rsplit(":", 1)[-1] if ":" in v else v
    try:
        p = int(tail)
    except ValueError:
        return 0
    return p if 0 < p <= 65535 else 0


def candidate_paths() -> List[Path]:
    roots = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            roots.append(Path(base))
    roots.append(Path.home() / ".config")
    out: List[Path] = []
    for root in roots:
        for rel in ("com.follow/clash", "com.follow.clash", "Clash Verge",
                    "io.github.clash-verge-rev.clash-verge-rev", "mihomo", "clash"):
            out.append(root / rel / "config.yaml")
    out.append(Path.home() / ".config" / "mihomo" / "config.yaml")
    return [p for p in out if not any(q in p.parts for q in ("cache", "logs"))]


def parse_config(text: str) -> FlConfig:
    """解析顶层键（缩进行属于子树，忽略），不依赖 yaml 库。"""
    cfg = FlConfig()
    for line in text.splitlines():
        if not line or line.lstrip()[0:1] in ("#",) or line[:1].isspace():
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2)
        if val.strip().startswith("#"):
            continue
        if key == "secret":
            cfg.secret = _unquote(val)
        elif key == "external-controller":
            cfg.controller_port = _as_port(val)
        elif key == "mixed-port":
            cfg.mixed_port = _as_port(val)
        elif key == "find-process-mode":
            cfg.find_process_mode = _unquote(val)
    return cfg


def read() -> FlConfig:
    """按候选路径找配置：优先返回带密钥的那份，否则第一份能解析的。"""
    first: FlConfig = FlConfig()
    for p in candidate_paths():
        try:
            if not p.is_file() or p.stat().st_size > 2_000_000:
                continue
            cfg = parse_config(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        cfg.path = str(p)
        if not first.path:
            first = cfg
        if cfg.secret:
            return cfg
    return first

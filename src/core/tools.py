# -*- coding: utf-8 -*-
"""工具集：Wireshark 定位、CSV 导出（防公式注入）、开机自启（HKCU Run）。"""
from __future__ import annotations

import csv
import os
import winreg

_WIRESHARK_CANDIDATES = [
    r"C:\Program Files\Wireshark\Wireshark.exe",
    r"C:\Program Files (x86)\Wireshark\Wireshark.exe",
    r"D:\Program Files\Wireshark\Wireshark.exe",
    r"E:\Program Files\Wireshark\Wireshark.exe",
]


def find_wireshark() -> str:
    """定位 Wireshark.exe：注册表 → 常见路径。找不到返回空串。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\Wireshark") as k:
            path, _ = winreg.QueryValueEx(k, "InstallPath")
            exe = os.path.join(path, "Wireshark.exe")
            if os.path.isfile(exe):
                return exe
    except OSError:
        pass
    for exe in _WIRESHARK_CANDIDATES:
        if os.path.isfile(exe):
            return exe
    return ""


def _csv_safe(value: str) -> str:
    """防 CSV 公式注入：以 = + - @ 或 Tab/回车开头的值前置单引号。"""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def export_connections_csv(path: str, rows: list) -> int:
    """导出连接列表为 UTF-8 BOM CSV。rows: [(软件, 进程, 目标, 渠道, 节点, 下载, 上传)]。返回行数。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["软件", "进程", "目标", "渠道", "节点", "下载", "上传"])
        n = 0
        for r in rows:
            w.writerow([_csv_safe(c) for c in r])
            n += 1
    return n


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "TrafficBoard"


def get_autostart(exe_path: str = "") -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _RUN_NAME)
            if exe_path:
                return exe_path.lower() in str(val).lower()
            return True
    except OSError:
        return False


def set_autostart(enable: bool, exe_path: str) -> bool:
    """写/删 HKCU Run 键。返回是否成功。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
            else:
                try:
                    winreg.DeleteValue(k, _RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False

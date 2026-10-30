# -*- coding: utf-8 -*-
"""单实例：再次启动时把已在跑的实例窗口叫到前台，本进程安静退出。

为什么不用互斥量单独解决：CreateMutex 只能判"有没有"，用户双击桌面图标后
仍然什么都不发生（程序缩在托盘里），这正是"图标没反应"的由来。
为什么不用 QLocalServer 单独解决：实测在 Windows 上同名管道已被别的进程
占着时 `listen()` 依然返回 True（Qt 允许多个监听者并存），所以"抢占判定"
不能靠 listen 失败，必须用命名互斥量做仲裁。

于是两段各管一件事：命名互斥量（Local\\ 前缀，免提权）判定谁是第一个，
QLocalServer 命名管道负责"第二次启动请把窗口亮出来"。管道随进程消失，
不留 stale 锁文件。

任何一环失败（互斥量拿不到、管道监听不起来）都只降级、不拒绝启动——
宁可多开一个实例，也不能让用户点不开程序。
"""
import ctypes
import time
from typing import Callable, Optional, Tuple

SERVER_NAME = "TrafficBoard-instance"
_MUTEX_HOLDERS: list = []      # 句柄必须活到进程结束，被 GC 就等于释放了

ERROR_ALREADY_EXISTS = 183


def notify_running_instance(name: str = SERVER_NAME) -> bool:
    """有活着的实例就连上去让它显示主窗口并返回 True；没有返回 False。"""
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show")
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def _acquire_mutex(name: str) -> bool:
    """True = 本进程是第一个拿到名字的。False = 已有实例（或拿不到，降级放行）。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        h = kernel32.CreateMutexW(None, False, "Local\\" + name)
        if h in (None, ctypes.c_void_p(-1).value, -1):
            return True                      # 拿不到句柄：不据此拒绝启动
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _MUTEX_HOLDERS.append(h)             # 不 close：进程活着互斥量就活着
        return True
    except Exception:
        return True


def claim(on_show: Callable[[], None], name: str = SERVER_NAME) -> Tuple[str, Optional[object]]:
    """占住实例名并注册激活回调。

    ('taken', None)      已有实例且已成功让它显示主窗口，调用方应退出；
    ('mine', server)     本进程是首个实例——必须一直持有返回的 server，
                         对象被回收管道就没了；
    ('degraded', None)   没有可激活的实例，但管道起不来：照常启动。
    """
    if notify_running_instance(name):
        return "taken", None
    if not _acquire_mutex(name):
        # 并发启动：另一进程刚抢到名字，等它的管道出现（最多 1.2s，只在真竞态里走）
        for _ in range(8):
            time.sleep(0.15)
            if notify_running_instance(name):
                return "taken", None
    from PySide6.QtNetwork import QLocalServer
    srv = QLocalServer()
    srv.removeServer(name)        # 清掉非 Windows 平台可能残留的 socket 文件
    if not srv.listen(name):
        return "degraded", None

    def _accept() -> None:
        conn = srv.nextPendingConnection()
        if conn is not None:
            conn.waitForReadyRead(200)
            conn.deleteLater()
        on_show()

    srv.newConnection.connect(_accept)
    return "mine", srv

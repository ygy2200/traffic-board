# -*- coding: utf-8 -*-
"""离屏渲染/冒烟脚本的统一收尾。

生产 closeEvent 用 TerminateProcess 跳过 CRT 清理（见 ui/main_window._hard_exit）。
测试脚本若走 Python 正常退出，解释器拆除阶段会撞上仍存活的 Qt 对象：
实测退出码 0xC0000409（fail-fast）或挂死，导致"打印全部通过"与退出码不一致。
收尾统一走与生产相同的硬终止路径，退出码才是可信信号。

真实退出路径（closeEvent → worker.wait → agg.stop → TerminateProcess）由
tests/adv_process_exit.py 端到端覆盖，本模块只负责渲染类脚本的收尾。
"""
import ctypes
import os
import sys


def hard_exit(code: int = 0) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        if kernel32.TerminateProcess(ctypes.c_void_p(-1), code & 0xFFFFFFFF):
            return
    except Exception:
        pass
    os._exit(code)

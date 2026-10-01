# -*- coding: utf-8 -*-
"""真实启动→运行→退出 单次实测（外部循环调用 N 次观察偶发崩溃）。

用法: python tests/manual_exit_once.py tray|close
  tray  = 托盘菜单退出路径 (_quit_app)
  close = exit_on_close 开启时点 × 路径 (closeEvent 直接真退)
退出码 0 = 干净退出；非 0 或 stderr 有 traceback = 竞态复现。
"""
import os
import sys

import faulthandler
faulthandler.enable()   # 段错误时打印 Python 回溯，定位崩溃点

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from PySide6.QtCore import QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.aggregator import Aggregator      # noqa: E402
from ui.main_window import MainWindow       # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "tray"
assert mode in ("tray", "close"), mode

app = QApplication(sys.argv)
agg = Aggregator()
w = MainWindow(agg)

if mode == "close":
    w.exit_on_close = True

w.show()
QTimer.singleShot(3000, w._quit_app if mode == "tray" else w.close)
sys.exit(app.exec())   # 正常时 closeEvent 内 os._exit(0) 不会走到这

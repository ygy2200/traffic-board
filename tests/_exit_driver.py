# -*- coding: utf-8 -*-
"""子进程驱动：走生产退出路径，由 adv_process_exit.py 断言退出码。

mode=quit  → 托盘菜单退出（_quit_app → closeEvent 退出分支 → TerminateProcess）
mode=close → 设置"关窗即退出"后点 X（exit_on_close=True → 同一分支）
mode=hide  → 默认关窗最小化到托盘：closeEvent 必须 ignore，进程在 close 后仍存活
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _qt_harness import use_temp_data_dir  # noqa: E402
use_temp_data_dir("exit")   # 测试专属数据目录：绝不往用户真实历史库写

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from _qt_harness import hard_exit  # noqa: E402
from core.aggregator import Aggregator  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "quit"
ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 2

app = QApplication.instance() or QApplication([])
agg = Aggregator()
agg.start()
win = MainWindow(agg)
win.resize(900, 640)
win.show()

_state = {"n": 0}


def _step():
    _state["n"] += 1
    if _state["n"] < ticks:
        return
    if mode == "quit":
        win._quit_app()
    elif mode == "close":
        win.exit_on_close = True
        win.close()
    else:
        win.close()
        # 关窗只隐藏不退出：能走到这里说明进程确实还活着
        print("ALIVE_AFTER_CLOSE")
        hard_exit(0)


_timer = QTimer()
_timer.setInterval(450)
_timer.timeout.connect(_step)
_timer.start()

rc = app.exec()
agg.stop()
hard_exit(rc)

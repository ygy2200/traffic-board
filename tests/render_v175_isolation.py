# -*- coding: utf-8 -*-
"""R24 验收：start_worker=False 时主窗口真的不起采集线程。

这不是像素闸门，是「测试隔离」的运行时证据：静态扫描只能证明参数写了，
不能证明参数有效。离屏套件依赖这条，否则全量回归会随用户是否开着 FlClash 漂。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))


def _child() -> int:
    use_temp_data_dir("v175")
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    win.resize(1000, 700)

    assert win.worker is not None, "MainWindow 没有 worker 对象，热更新限额那条路会断"
    assert not win.worker.isRunning(), "start_worker=False 了，PollWorker 居然还在跑"
    # isFinished() 对「从未 start」的 QThread 是 False，别拿它当隔离判据

    # 设置页改限额走 worker.set_quota；未启动时也必须能接住，不能崩
    win.worker.set_quota(10.0, 5)
    assert win.worker._quota_cap_gb == 10.0 and win.worker._quota_reset_day == 5

    win.worker.stop()
    assert win.worker.wait(1000), "未启动的 worker 在 stop/wait 时挂住了"
    print("start_worker=False：PollWorker 未运行，set_quota/stop/wait 均可安全调用 PASS")
    return 0


if __name__ == "__main__":
    hard_exit(_child())

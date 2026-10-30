# -*- coding: utf-8 -*-
"""R10 冒烟：帮助页/设置页新内容实例化 + 主窗口五页导航 + 退出路径。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.aggregator import Aggregator  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from _qt_harness import hard_exit  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication([])
    qs = QSettings("TrafficBoard", "TrafficBoard")
    had = dict((k, qs.value(k)) for k in qs.allKeys())
    for k in qs.allKeys():
        qs.remove(k)

    agg = Aggregator()
    agg.start = lambda: None                     # 隔离真实采集线程
    agg.clash.state.__dict__["online"] = False
    win = MainWindow(agg)
    win.resize(1000, 700)
    win.show()
    app.processEvents()

    # 五个页面都能切换且不炸（FluentWindow.stackedWidget 持有全部子页）
    page_ids = {"流量": "dashPage", "帮助": "helpPage", "设置": "settingsPage",
                "历史": "historyPage", "体检": "healthPage"}
    sw = win.stackedWidget
    names = [sw.widget(i).objectName() for i in range(sw.count())]
    assert sorted(names) == sorted(page_ids.values()), names
    for label, obj in page_ids.items():
        page = next(sw.widget(i) for i in range(sw.count())
                    if sw.widget(i).objectName() == obj)
        win.switchTo(page)
        app.processEvents()
    # 帮助页新章节渲染（find-process-mode / 月流量上限 FAQ）
    from ui.help_page import _SECTIONS
    titles = [t for t, _ in _SECTIONS]
    assert "各页面都是干什么的" in titles, titles
    joined = "\n".join(line for _, lines in _SECTIONS for line in lines)
    assert "find-process-mode" in joined and "月流量上限" in joined
    # 设置页新卡片存在
    assert win.page_settings.sp_cap.value() == 0
    assert win.page_settings.sp_reset.value() == 1

    # 恢复用户真设置必须在硬杀退出之前：_quit_app() 走 closeEvent → TerminateProcess，
    # 它后面的任何语句都不会执行（此前这里的还原代码是死代码，每次跑都抹掉用户设置）。
    for k, v in had.items():
        qs.setValue(k, v)
    qs.sync()
    assert set(qs.allKeys()) == set(had), f"设置未还原: {sorted(qs.allKeys())}"

    print("五页导航 + 帮助页新章节 + 设置页限额卡 PASS")

    # 退出路径：托盘退出 → closeEvent → worker.wait → agg.stop → TerminateProcess(0)
    # 进程在此消失；rc=0 即退出路径通过（端到端覆盖另见 adv_process_exit.py）
    win._quit_app()
    agg.stop()
    return 1   # 走到这里说明 _quit_app 没有终止进程 = 退出路径坏了


if __name__ == "__main__":
    hard_exit(main())

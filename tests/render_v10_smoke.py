# -*- coding: utf-8 -*-
"""R10 冒烟：帮助页/设置页新内容实例化 + 主窗口五页导航 + 退出路径。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

# 本脚本会清空整棵设置树来测默认态——所以必须让父进程持有还原责任：
# 进程内还原实测会被被测控件自己的 QSettings 实例缓存覆盖（曾经抹过用户设置）
if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))
use_temp_data_dir("v10")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.aggregator import Aggregator  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication([])
    qs = QSettings("TrafficBoard", "TrafficBoard")
    for k in qs.allKeys():
        qs.remove(k)                             # 从干净状态测默认导航/限额；父进程负责还原

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

    print("五页导航 + 帮助页新章节 + 设置页限额卡 PASS")

    # 退出路径：托盘退出 → closeEvent → worker.wait → agg.stop → TerminateProcess(0)
    # 进程在此消失；rc=0 即退出路径通过（端到端覆盖另见 adv_process_exit.py）
    win._quit_app()
    agg.stop()
    return 1   # 走到这里说明 _quit_app 没有终止进程 = 退出路径坏了


if __name__ == "__main__":
    hard_exit(main())

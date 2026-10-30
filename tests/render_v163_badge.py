# -*- coding: utf-8 -*-
"""v1.6.3 离屏验收：悬浮速率条位置必须跨启动保留（不再被主窗口重置到 (100,100)）。

SpeedBadge 自己已经做了「读 badge/pos + 屏幕合法性校验 + 回落默认位」，
主窗口 _init_badge 里再 move(100,100) 就是把用户拖好的位置每次启动抹掉。

本脚本会写用户的 QSettings，所以走 run_isolated()：断言在子进程里做，
设置快照/还原由父进程用 reg.exe 在进程外完成——同进程内的"还原"实测会被
被测控件自己的 QSettings 实例缓存覆盖掉，不可信。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402


def _child() -> None:
    use_temp_data_dir("v163")
    from PySide6.QtCore import QPoint, QSettings  # noqa: I001
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    qs = QSettings("TrafficBoard", "TrafficBoard")

    def inside(delta_x: int, delta_y: int) -> QPoint:
        """取一个确定落在主屏可用区内、且不与默认位/(100,100) 重合的点。"""
        g = QGuiApplication.primaryScreen().availableGeometry()
        p = QPoint(g.left() + delta_x, g.bottom() - 60 - delta_y)
        assert g.contains(p), f"哨兵位不在屏幕内: {p} vs {g}"
        return p

    def main_window():
        from core.aggregator import Aggregator
        from ui.main_window import MainWindow
        agg = Aggregator()
        agg.start = lambda: None            # 隔离：不拉真实采集线程
        return MainWindow(agg)

    # 1) 用户拖好并保存过的位置，重启后必须原样回来
    sentinel = inside(23, 41)
    qs.setValue("badge/pos", sentinel)
    w = main_window()
    got = w.badge.pos()
    assert got == sentinel, f"用户拖好的悬浮条位置被重置: 存 {sentinel} 得 {got}"
    print("1) 保存位原样恢复 PASS  %s" % (got,))

    # 2) 保存位在屏幕外（分辨率变了）要回落到屏内，否则悬浮条直接丢失
    qs.setValue("badge/pos", QPoint(999999, 999999))
    p2 = main_window().badge.pos()
    assert any(scr.availableGeometry().contains(p2) for scr in QGuiApplication.screens()), \
        f"屏幕外的保存位没有回落，悬浮条会丢: {p2}"
    print("2) 屏幕外回落屏内 PASS  %s" % (p2,))

    hard_exit(0)


def _parent() -> int:
    rc = run_isolated(str(HERE / "render_v163_badge.py"))
    if rc != 0:
        print("render_v163_badge 失败 rc=%s" % rc)
        return rc
    print("render_v163_badge 全部通过")
    return 0


if __name__ == "__main__":
    if am_child():
        try:
            _child()
        except AssertionError:
            import traceback
            traceback.print_exc()
            hard_exit(1)
        except Exception:
            import traceback
            traceback.print_exc()
            hard_exit(2)
    elif os.name != "nt":
        _child()                      # 非 Windows 没有注册表可污染
    else:
        sys.exit(_parent())

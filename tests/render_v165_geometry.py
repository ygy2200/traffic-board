# -*- coding: utf-8 -*-
"""v1.6.3 R9 验收：主窗口大小/位置跨启动保留，且不会把窗口丢到屏幕之外。

几何值走 QSettings（Windows 上是注册表），所以父进程必须持有还原责任。
第 5 步是真重启：另起一个进程只构造窗口，断言几何是从注册表里读回来的。
"""
import inspect
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

GEO_KEYS = ("win/x", "win/y", "win/w", "win/h", "win/maximized")
EXPECT_KEY = "TB_V165_EXPECT"


def _mk_window():
    """造一个不采集的 MainWindow（agg.start 桩掉，避免真起线程）。"""
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg)
    win.show()
    app.processEvents()
    return app, win


def _wipe(settings) -> None:
    for k in GEO_KEYS:
        settings.remove(k)
    settings.sync()


def _sig(win):
    """几何签名：位置取 frameGeometry（move 认这个），尺寸取客户端宽高（resize 认这个）。
    混用 geometry()/frameGeometry() 会让窗口每次启动漂一个边框宽——那正是本轮要修的。"""
    fg = win.frameGeometry()
    return (fg.x(), fg.y(), win.width(), win.height())


def _phase2() -> int:
    """子进程 = 真重启：只构造窗口，断言上一步存的几何原样回来了。"""
    use_temp_data_dir("v165p2")
    from PySide6.QtGui import QGuiApplication

    app, win = _mk_window()
    expect = tuple(int(v) for v in os.environ[EXPECT_KEY].split(","))
    got = _sig(win)
    assert got == expect, f"重启后几何丢了: 实得 {got}，应为 {expect}"
    fg = win.frameGeometry()
    assert any(s.geometry().intersects(fg) for s in QGuiApplication.screens()), fg
    print(f"5) 真重启后几何一致 PASS  {got[0]},{got[1]} {got[2]}x{got[3]}")
    return 0


def _child() -> int:
    use_temp_data_dir("v165")
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QGuiApplication

    app, win = _mk_window()
    s = win._settings
    _wipe(s)

    # ---- 1) 没有存值时不谎称恢复了 ----
    assert win._restore_win_geometry() is False, "无存值却返回 True"
    assert win.geometry().width() >= 800, win.geometry()
    print("1) 无存值走默认尺寸 PASS")

    # ---- 2) 存进去的几何能原样回来（含落到注册表）----
    # 期望值取"应用后的实测几何"：窗口有最小尺寸(885x806)，硬编码会被 Qt 夹掉
    win.resize(912, 624)
    win.move(233, 145)
    app.processEvents()
    saved = _sig(win)
    win._save_win_geometry()
    fresh = QSettings("TrafficBoard", "TrafficBoard")
    assert (fresh.value("win/w"), fresh.value("win/h")) == (saved[2], saved[3]), \
        f"没落到设置里: {fresh.value('win/w')}x{fresh.value('win/h')}"
    win.setGeometry(10, 10, 1000, 700)
    assert win._restore_win_geometry() is True
    assert _sig(win) == saved, f"{_sig(win)} != {saved}"
    print(f"2) 几何往返一致 PASS  {saved[2]}x{saved[3]}")

    # ---- 3) 拔过显示器/分辨率变了：不能把窗口丢到屏外 ----
    win.move(999999, 999999)
    app.processEvents()
    win._save_win_geometry()                      # 存了一份屏外几何
    assert win._restore_win_geometry() is True
    fg = win.frameGeometry()
    assert any(sg.geometry().intersects(fg) for sg in QGuiApplication.screens()), \
        f"窗口落在所有屏幕之外: {fg}"
    print("3) 屏外几何落回屏幕内 PASS")

    # ---- 4) 存值坏掉：不炸、不接管窗口 ----
    _wipe(s)
    s.setValue("win/x", "garbage")
    s.sync()
    assert win._restore_win_geometry() is False, "坏数据不该被当成恢复成功"
    _wipe(s)
    for k, v in (("win/x", 100), ("win/y", 100), ("win/w", 0), ("win/h", 0)):
        s.setValue(k, v)
    s.sync()
    assert win._restore_win_geometry() is False, "0 尺寸不该被恢复"
    assert win.geometry().width() > 0
    print("4) 坏数据/0 尺寸静默降级 PASS")

    # ---- 5) 关窗保存 + 真重启读回 ----
    win.resize(845, 585)
    win.move(120, 90)
    app.processEvents()
    expect = _sig(win)
    _wipe(s)
    win.close()                       # 默认设置：忽略关闭 + 隐藏到托盘，但几何应已保存
    assert not win.isVisible()
    assert win._restore_win_geometry() is True, "closeEvent 没把几何写进设置"
    assert _sig(win) == expect, f"{_sig(win)} != {expect}"
    code = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        env=dict(os.environ, TB_ISOLATED_CHILD="1", TB_V165_PHASE="2",
                 **{EXPECT_KEY: ",".join(str(v) for v in expect)}),
    ).returncode
    assert code == 0, f"重启进程验几何 rc={code}"

    # ---- 6) 最大化：存的是上次的正常矩形，恢复时才最大化 ----
    # 真调 showMaximized() 在离屏下会抛 pywintypes.error(1400)（qframelesswindow 问
    # Win32 要窗口句柄），所以这里桩掉状态与调用，验的是"我这条逻辑"而不是 Qt 行为。
    _wipe(s)
    s.setValue("win/x", 140)
    s.setValue("win/y", 80)
    s.setValue("win/w", 900)
    s.setValue("win/h", 640)
    s.setValue("win/maximized", "0")
    s.sync()
    win.isMaximized = lambda: True
    win.isMinimized = lambda: False
    win._save_win_geometry()
    after = QSettings("TrafficBoard", "TrafficBoard")
    assert after.value("win/maximized") == "1", after.value("win/maximized")
    assert (after.value("win/x"), after.value("win/w")) == (140, 900), \
        f"最大化时把铺屏矩形存进去了: {after.value('win/x')}x{after.value('win/w')}"
    win.showNormal = lambda *a, **k: None
    calls = []
    win.showMaximized = lambda *a, **k: calls.append(1)
    assert win._restore_win_geometry() is True
    assert calls == [1], "存了最大化标记却没最大化"
    print("6) 最大化标记 + 保留正常矩形 PASS")

    # ---- 7) 连续多轮"存→恢复"不许漂移 ----
    # 位置存 frameGeometry、尺寸存客户端宽高：混用 geometry() 与 move() 时每轮偏一个边框宽
    _wipe(s)
    win.isMaximized = lambda: False
    win.isMinimized = lambda: False
    win.resize(960, 700)
    win.move(200, 160)
    app.processEvents()
    base = _sig(win)
    for i in range(3):
        win._save_win_geometry()
        win.setGeometry(0, 0, 1000, 700)
        assert win._restore_win_geometry() is True
        assert _sig(win) == base, f"第 {i + 1} 轮几何漂移: {_sig(win)} != {base}"
    print("7) 连续三轮存/恢复零漂移 PASS")

    # ---- 8) 接线检查：构造与关窗真调了这两个方法 ----
    src_init = inspect.getsource(type(win).__init__)
    src_close = inspect.getsource(type(win).closeEvent)
    assert "_restore_win_geometry()" in src_init, "构造函数没接恢复"
    assert "_save_win_geometry()" in src_close, "closeEvent 没接保存"
    print("8) __init__ 恢复 + closeEvent 保存 已接线 PASS")
    return 0


if __name__ == "__main__":
    if am_child() and os.environ.get("TB_V165_PHASE") == "2":
        try:
            hard_exit(_phase2())
        except Exception:
            import traceback
            traceback.print_exc()
            hard_exit(1)
    elif not am_child() and os.name == "nt":
        sys.exit(run_isolated(str(Path(__file__).resolve())))
    else:
        try:
            code = _child()
        except Exception:
            # 失败也要走 hard_exit：否则 Qt 收尾阶段段错误，rc=139 会把
            # "断言没过" 和 "崩在 teardown" 混成同一个信号。
            import traceback
            traceback.print_exc()
            code = 1
        hard_exit(code)

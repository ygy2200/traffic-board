# -*- coding: utf-8 -*-
"""v1.6.3 R10 验收：提醒开关重启后仍生效，且提醒在窗口前台时也看得见。

两条老 bug 一起钉：
① MainWindow 把 alert_enabled 写死 True，从不读 alert/enabled —— 设置页开关
   显示"关"，重启后提醒照旧弹（设置不落地）；
② 配额/陌生连接提醒只发托盘气泡 —— 用户正盯着窗口时等于没提醒。
"""
import inspect
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))


def _mk_window():
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    win.show()
    app.processEvents()
    return app, win, QSettings


def _board(proc="a.exe", targets=("one.com:443",)):
    from core.aggregator import AppView, Board, ConnView
    conns = [ConnView(proto="tcp", target=t, channel="proxy", node="HK-1",
                      download=10, upload=1, from_clash=True, conn_id=f"c{i}")
             for i, t in enumerate(targets)]
    return Board(apps=[AppView(pid=400, proc_name=proc, display=proc, conns=conns)])


def _child() -> int:
    use_temp_data_dir("v166")
    app, win, QSettings = _mk_window()
    s = win._settings
    toasts = []
    win._toast = lambda kind, title, content: toasts.append((kind, title, content))

    # ---- 1) 开关关掉：重启后行为跟显示一致（一条都不发）----
    s.setValue("alert/enabled", "0")
    s.sync()
    win.worker.stop()                       # 先停掉第一个窗口的采集线程再起第二个
    win.worker.wait(3000)
    _, win, _ = _mk_window()
    toasts.clear()
    win._toast = lambda kind, title, content: toasts.append((kind, title, content))
    assert win.alert_enabled is False, "设置页显示关着，窗口却把提醒当开着"
    win._check_unknown(_board())
    assert toasts == [], f"关了提醒还发: {toasts}"
    print("1) alert/enabled=0 跨重启生效 PASS")

    # ---- 2) 开关打开：陌生连接发 info 回执 ----
    def unthrottle(w):
        w._last_alert_any = 0.0

    s.setValue("alert/enabled", "1")
    s.sync()
    win.alert_enabled = True
    unthrottle(win)
    win._check_unknown(_board(proc="b.exe", targets=("two.com:443",)))
    assert len(toasts) == 1 and toasts[0][0] == "info", toasts
    assert toasts[0][1] == "发现新的网络连接" and "b.exe" in toasts[0][2], toasts
    print("2) 陌生连接提醒走 _toast PASS")

    # ---- 3) 同一对 (进程, 目标) 不重复提醒 ----
    unthrottle(win)
    win._check_unknown(_board(proc="b.exe", targets=("two.com:443",)))
    assert len(toasts) == 1, f"重复提醒: {toasts}"
    print("3) 已记账连接不再刷屏 PASS")

    # ---- 4) 同进程一次两条新连接 → 只提醒一条 ----
    unthrottle(win)
    win._check_unknown(_board(proc="c.exe", targets=("x.com:443", "y.com:443")))
    assert len(toasts) == 2, f"限流失效: {toasts}"
    print("4) 同进程一次只提醒一条 PASS")

    # ---- 5) 首次启动那种"几十条一起陌生"的场面：一批只发一条 ----
    unthrottle(win)
    from core.aggregator import AppView, Board, ConnView
    burst = Board(apps=[AppView(pid=900 + i, proc_name=f"p{i}.exe", display=f"p{i}",
                                conns=[ConnView(proto="tcp", target=f"t{i}.com:443",
                                                channel="proxy", conn_id=f"p{i}")])
                       for i in range(12)])
    win._check_unknown(burst)
    assert len(toasts) == 3, f"一批 12 个陌生进程不该发 12 条: {toasts}"
    print("5) 一批陌生连接只提醒一条（不糊窗口）PASS")

    # ---- 6) 全局每分钟 1 条：60s 内的新批次静默，但照常记账 ----
    win._check_unknown(_board(proc="late.exe", targets=("later.com:443",)))
    assert len(toasts) == 3, f"60s 内又发了一条: {toasts}"
    unthrottle(win)                       # 等价于过了 61 秒
    win._check_unknown(_board(proc="late.exe", targets=("later.com:443",)))
    assert len(toasts) == 3, "被静默的那条已经记过账，不该攒着补发"
    win._check_unknown(_board(proc="fresh.exe", targets=("now.com:443",)))
    assert len(toasts) == 4, f"过了限流窗口还不发: {toasts}"
    print("6) 全局限流生效又确实放行 PASS")

    # ---- 7) 配额提醒走同一通道，文案原样 ----
    win._on_quota_alert("月流量已用尽", "已用 210GB / 上限 200GB")
    assert toasts[-1] == ("warning", "月流量已用尽", "已用 210GB / 上限 200GB"), toasts[-1]
    print("7) 配额提醒走 _toast PASS")

    # ---- 8) 接线检查：两处都真调了 _toast，且开关值来自设置而非写死 ----
    src_alert = inspect.getsource(type(win)._on_quota_alert)
    src_unknown = inspect.getsource(type(win)._check_unknown)
    assert "_toast(" in src_alert and "showMessage" not in src_alert, src_alert
    assert "_toast(" in src_unknown and "showMessage" not in src_unknown, src_unknown
    src_init = inspect.getsource(type(win).__init__)
    assert 'alert/enabled' in src_init, "构造函数没读 alert/enabled"
    print("8) 两处提醒 + 构造函数读值 已接线 PASS")

    # ---- 9) info 样式真渲染（提醒是主动推送，长什么样得有图）----
    from pathlib import Path as _P
    from qfluentwidgets import InfoBar
    from _qt_harness import shoot

    win._toast = type(win)._toast.__get__(win)      # 撤桩，用真实现
    win._toast("info", "发现新的网络连接", "b.exe 首次连接：two.com:443")
    app.processEvents()
    bars = [b for b in win.findChildren(InfoBar) if b.title == "发现新的网络连接"]
    assert bars, "info 提醒没挂到窗口上"
    assert "two.com" in bars[0].content
    shoot(win, _P(__file__).parent / "render_v166_alert_info.png", app, settle_ms=500)
    print("9) info 提醒实渲染 PASS")
    return 0


if __name__ == "__main__":
    if am_child():
        try:
            code = _child()
        except Exception:
            # 失败也要走 hard_exit：否则 Qt 收尾段错误 rc=139 会混淆失败原因
            import traceback
            traceback.print_exc()
            code = 1
        hard_exit(code)
    elif os.name == "nt":
        sys.exit(run_isolated(str(Path(__file__).resolve())))
    else:
        hard_exit(_child())

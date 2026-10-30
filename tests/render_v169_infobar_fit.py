# -*- coding: utf-8 -*-
"""v1.6.3 R13 验收：前台提醒（InfoBar）不许比窗口还宽。

实测改前：qfluentwidgets 的 _adjustText 按「9px 一个字符」估算折行宽度，
中文一个字实际 13~14px，于是同一条陌生连接提醒——
  窗口 700 宽：InfoBar 769 宽、x=-35，左右各被切掉 35px；
  窗口 456 宽（R12 刚把最小尺寸降下来）：InfoBar 699 宽、x=-122，两头各切 122px。
Win11 半屏贴靠就是 683 宽，等于「贴半边屏幕看提醒 → 提醒文字两头看不见」。
这一轮钉：① 各种窗口宽度下 toast 完整落在窗口内；② 修的方式不许是截断，
正文里的进程名/目标地址必须还在；③ 窗口中途改变大小，活着的 toast 要跟着重新贴合。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))

TITLE = "有新的陌生连接"
CONTENT = ("douyin.exe 第一次连接 127.0.0.1:7890（本地代理端口）。\n"
           "如果这是你自己装的软件可以放心忽略，陌生连接提醒只是帮你发现后台偷跑流量的程序。")
MUST_KEEP = ("douyin.exe", "127.0.0.1:7890", "可以放心忽略")


def _settle(app, ms: int = 250) -> None:
    """转真实时间片：InfoBar 的滑入动画与父窗口 resize 后的重排都要时间。"""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _bars(win):
    from qfluentwidgets import InfoBar
    return [b for b in win.findChildren(InfoBar) if b.isVisible()]


def _toast_and_check(app, win, w, h, note=""):
    win.resize(w, h)
    _settle(app)
    win.show()
    _settle(app, 120)
    win._toast("warning", TITLE, CONTENT)
    _settle(app, 400)                 # 等滑入动画落位
    bars = _bars(win)
    assert bars, f"{w}x{h} 窗口里没出现 InfoBar（前台分支没走到？）"
    for b in bars:
        g = b.geometry()
        assert g.x() >= 0, (
            f"{w}x{h} 窗口{note}：提醒左端在窗口外 x={g.x()}（宽 {g.width()}），"
            f"左边文字被切掉 {-g.x()}px")
        assert g.x() + g.width() <= win.width(), (
            f"{w}x{h} 窗口{note}：提醒右端超出窗口 {g.x() + g.width() - win.width()}px"
            f"（x={g.x()} 宽 {g.width()}），右边文字被切掉")
    txt = " ".join(b.contentLabel.text() + b.titleLabel.text() for b in bars)
    flat = txt.replace("\n", "")
    for frag in MUST_KEEP:
        assert frag in flat, f"提醒正文里的「{frag}」在折行后丢了（不许用截断换宽度）"
    return bars


def _child() -> int:
    use_temp_data_dir("infobarfit")
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg)
    win.alert_enabled = False
    win.show()
    _settle(app)

    # ---- 1) 全屏宽度：本来就没问题，钉住别改坏 ----
    bars = _toast_and_check(app, win, 1366, 720)
    print(f"1) 1366x720 提醒宽 {bars[0].width()} 完整在窗口内 PASS")
    for b in bars:
        b.close()
    _settle(app, 200)

    # ---- 2) 默认窗口宽度 ----
    bars = _toast_and_check(app, win, 1000, 700)
    print(f"2) 1000x700 提醒宽 {bars[0].width()} 完整在窗口内 PASS")
    for b in bars:
        b.close()
    _settle(app, 200)

    # ---- 3) Win11 半屏贴靠（1366/2 = 683）：改前 769 宽、x=-35 两头被切 ----
    bars = _toast_and_check(app, win, 683, 700)
    print(f"3) 683x700（半屏贴靠）提醒宽 {bars[0].width()} x={bars[0].x()} 完整在窗口内 PASS")

    # ---- 4) 多条提醒叠着放也要全部在窗口内 ----
    win._toast("success", "已导出", "128 条连接 → C:/Users/y/Downloads/conns.csv")
    _settle(app, 400)
    bars2 = _bars(win)
    assert len(bars2) >= 2, f"两条提醒只剩 {len(bars2)} 条，前一条被挤掉了？"
    for b in bars2:
        assert 0 <= b.x() and b.x() + b.width() <= win.width(), (
            f"多条堆叠时越界：x={b.x()} 宽 {b.width()} 窗口 {win.width()}")
    print(f"4) {len(bars2)} 条提醒同时存在，全部在 683 宽窗口内 PASS")
    for b in bars2:
        b.close()
    _settle(app, 250)

    # ---- 5) 窗口中途变小，活着的提醒要跟着重新贴合 ----
    win.resize(1000, 700)
    _settle(app)
    win._toast("warning", TITLE, CONTENT)
    _settle(app, 400)
    bars3 = _bars(win)
    assert bars3, "1000 宽时没弹出提醒"
    win.resize(560, 640)
    _settle(app, 500)
    bars3 = _bars(win)
    assert bars3, "窗口变小后提醒自己消失了（应该是重新贴合）"
    for b in bars3:
        assert 0 <= b.x() and b.x() + b.width() <= win.width(), (
            f"窗口从 1000 缩到 560 后提醒没重排：x={b.x()} 宽 {b.width()}")
    print(f"5) 1000→560 缩小后活着的提醒重新贴合（宽 {bars3[0].width()}）PASS")

    # ---- 6) 极限小窗口（R12 的 456x510）----
    msh = win.minimumSizeHint()
    bars4 = _toast_and_check(app, win, msh.width(), msh.height(), note="（窗口最小尺寸）")
    print(f"6) 极限 {msh.width()}x{msh.height()} 提醒宽 {bars4[0].width()} 仍在窗口内 PASS")

    # ---- 7) 留图：半屏宽度 + 提醒，肉眼看排版 ----
    from _qt_harness import shoot
    for b in _bars(win):
        b.close()
    _settle(app, 250)
    win.resize(683, 700)
    win.show()
    _settle(app)
    win._toast("warning", TITLE, CONTENT)
    _settle(app, 400)
    shoot(win, Path(__file__).parent / "render_v169_infobar_halfscreen.png", app, settle_ms=400)
    print("7) 半屏宽度带提醒的实渲染 PASS")

    # ---- 8) 窗口隐藏时退回托盘气泡这条路径不能被这次改动带坏 ----
    win.hide()
    _settle(app, 150)
    shown: list = []
    real = win.tray.showMessage

    def spy(title, content, icon=0, msecs=0):
        shown.append((title, content))
        return real(title, content, icon, msecs)

    win.tray.showMessage = spy
    win._toast("warning", TITLE, CONTENT)
    _settle(app, 150)
    assert shown, "窗口不可见时没有退回托盘气泡"
    assert not _bars(win), "窗口都隐藏了还往窗口里弹 InfoBar"
    print(f"8) 隐藏窗口时走托盘气泡（{shown[0][0]}）PASS")
    return 0


if __name__ == "__main__":
    if am_child():
        try:
            code = _child()
        except Exception:
            import traceback
            traceback.print_exc()
            code = 1
        hard_exit(code)
    elif os.name == "nt":
        sys.exit(run_isolated(str(Path(__file__).resolve())))
    else:
        hard_exit(_child())

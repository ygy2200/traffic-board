# -*- coding: utf-8 -*-
"""v1.6.3 R15 验收：设置页窄窗口不许出现横向滚动条。

R12 把窗口最小尺寸降到 456x510，设置页同时改成了页内滚动——但只解决了「竖向」。
实测改前：窗口压到最小 456x510 时设置页滚动区视口 407 宽，
而「月流量上限」那张卡坚持要 550 宽（两个 SpinBox 就要 198 + 142，
加上「上限」「重置日」两个标签、20px 间隔和卡片 16+16 边距），
于是卡片右端超出视口 167px：用户必须横向拖一条滚动条才能看到「重置日」，
而 SpinBox 的上下按钮在卡片右缘外，看着像少了半截。

这一轮钉：① 极限宽度下没有横向滚动条、每张卡都在视口内；
② 修的方式不许是把输入框压到没法用（宽度/按钮可读性有下限）；
③ 宽窗口时这两个输入框仍在一行（不许为了窄屏把常态布局拉成两层）；
④ 改动不碰设置值的读写。
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

MIN_SPIN_W = 110      # SpinBox 低于这个宽度，数字 + GB 后缀 + 上下按钮就挤成一团


def _settle(app, ms: int = 250) -> None:
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _scroll_of(page):
    from PySide6.QtWidgets import QScrollArea
    scrolls = page.findChildren(QScrollArea)
    assert scrolls, "设置页没有滚动区了（R12 的页内滚动被谁拆了？）"
    return scrolls[0]


def _hbar_problem(scroll, cards):
    """返回 (横向可滚动距离, 最宽那张卡的最小宽度, 视口宽)。"""
    from PySide6.QtCore import QPoint
    vp_w = scroll.viewport().width()
    hmax = scroll.horizontalScrollBar().maximum()
    widest = 0
    for c in cards:
        mh = c.minimumSizeHint().width()
        widest = max(widest, mh)
        x = c.mapTo(scroll, QPoint(0, 0)).x()
        assert x >= 0, f"卡片左端在视口外 x={x}（视口 {vp_w}）"
        assert x + c.width() <= vp_w + 2, (
            f"卡片右端超出视口 {x + c.width() - vp_w}px（卡宽 {c.width()} 视口 {vp_w}）")
    return hmax, widest, vp_w


def _child() -> int:
    use_temp_data_dir("settingsfit")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QWidget
    from qfluentwidgets import CardWidget, SpinBox
    from ui.main_window import MainWindow
    from core.aggregator import Aggregator

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    win.alert_enabled = False
    win.show()
    _settle(app)
    page = win.page_settings
    win.switchTo(page)
    _settle(app)
    scroll = _scroll_of(page)
    cards = page.findChildren(CardWidget)
    assert len(cards) >= 7, f"设置页只认出 {len(cards)} 张卡，检查结构是否又变了"

    msh = win.minimumSizeHint()
    win.resize(msh.width(), msh.height())
    _settle(app, 300)

    # ---- 1) 极限宽度：没有横向滚动条，每张卡都在视口内 ----
    hmax, widest, vp_w = _hbar_problem(scroll, cards)
    assert hmax == 0, (
        f"窗口 {win.width()} 宽时设置页要横向拖 {hmax}px 才能看全（视口 {vp_w}，"
        f"最宽的卡最小尺寸 {widest}）——「重置日」输入框在屏幕外")
    print(f"1) 极限 {win.width()}x{win.height()}：视口 {vp_w} 宽，"
          f"最宽的卡 {widest}，横向可滚动 0 PASS")

    # ---- 2) 两个数字输入框仍然够用：可见、宽度有下限、上下按钮在卡内 ----
    for name in ("sp_cap", "sp_reset"):
        sp = getattr(page, name)
        assert isinstance(sp, SpinBox) and sp.isVisibleTo(win), f"{name} 看不见了"
        assert sp.width() >= MIN_SPIN_W, f"{name} 被压到 {sp.width()}px，低于可读下限 {MIN_SPIN_W}"
        card = sp.parentWidget()
        while card is not None and not isinstance(card, CardWidget):
            card = card.parentWidget()
        assert card is not None, f"{name} 不在任何卡片里"
        x_in_card = sp.mapTo(card, QPoint(0, 0)).x()
        assert x_in_card >= 0 and x_in_card + sp.width() <= card.width(), (
            f"{name} 右端出了卡片边界（x={x_in_card} 宽 {sp.width()} 卡宽 {card.width()}），按钮被切")
    print(f"2) 上限/重置日两个输入框各 {page.sp_cap.width()}/{page.sp_reset.width()}px，"
          f"都在卡片边界内 PASS")

    # ---- 3) 极限宽度下控件不打架：两个输入框互不重叠，卡内控件横向都在边界里 ----
    cap, rst = page.sp_cap, page.sp_reset
    g_cap = cap.mapTo(win, QPoint(0, 0))
    g_rst = rst.mapTo(win, QPoint(0, 0))
    r1 = cap.rect().translated(cap.mapTo(win, QPoint(0, 0)))
    r2 = rst.rect().translated(rst.mapTo(win, QPoint(0, 0)))
    assert not r1.intersects(r2), f"上限框与重置日框重叠了：{r1} vs {r2}"
    for card in cards:
        for ch in card.findChildren(QWidget):
            if not ch.isVisibleTo(win):
                continue
            x = ch.mapTo(card, QPoint(0, 0)).x()
            assert -1 <= x and x + ch.width() <= card.width() + 1, (
                f"{ch.metaObject().className()}#{ch.text() if hasattr(ch, 'text') else ''} "
                f"横向超出卡片边界 x={x} w={ch.width()} 卡宽 {card.width()}")
    dy = abs(g_cap.y() - g_rst.y())
    if dy >= 4:      # 分两行时，两个输入框必须左对齐成一列，否则看着像排歪了
        x_cap = cap.mapTo(page, QPoint(0, 0)).x()
        x_rst = rst.mapTo(page, QPoint(0, 0)).x()
        assert abs(x_cap - x_rst) <= 1, f"折行后两个输入框没对齐：x={x_cap} vs {x_rst}"
        print(f"3) 极限宽度折成两行且输入框左对齐（x={x_cap}），"
              f"{len(cards)} 张卡的可见后代控件都在边界内 PASS")
    else:
        print(f"3) 极限宽度下两个输入框同一行，{len(cards)} 张卡的可见后代控件都在边界内 PASS")

    # ---- 4) 宽窗口（用户常态）必须还是一行：不许为窄屏把常态拉成两层 ----
    win.resize(1000, 760)
    _settle(app, 300)
    hmax_w, widest_w, vp_w_w = _hbar_problem(scroll, cards)
    dy_wide = abs(cap.mapTo(page, QPoint(0, 0)).y() - rst.mapTo(page, QPoint(0, 0)).y())
    assert dy_wide < 4, (f"1000 宽窗口里两个输入框被拆成两行（纵向差 {dy_wide}px），"
                         f"常态布局变矮胖了")
    assert hmax_w == 0, f"1000 宽还横向可滚动 {hmax_w}px"
    print(f"4) 1000 宽：视口 {vp_w_w}，两个输入框同一行（纵向差 {dy_wide}px）PASS")

    # ---- 5) 值读写没被布局改动带坏 ----
    page.sp_cap.setValue(320)
    page.sp_reset.setValue(15)
    _settle(app, 150)
    from PySide6.QtCore import QSettings
    s = QSettings("TrafficBoard", "TrafficBoard")
    got_cap = float(s.value("quota/cap_gb", -1))
    got_day = int(s.value("quota/reset_day", -1))
    assert (got_cap, got_day) == (320.0, 15), f"设置没落盘：cap={got_cap} reset_day={got_day}"
    print(f"5) 改成 320GB/15 号后 QSettings 落盘一致 PASS")

    # ---- 6) 极限宽度的实渲染留图 ----
    from _qt_harness import shoot
    win.resize(msh.width(), msh.height())
    _settle(app, 300)
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    _settle(app, 150)
    shoot(win, Path(__file__).parent / "render_v170_settings_minwidth.png", app, settle_ms=300)
    print("6) 极限宽度滚到底的实渲染留图 PASS")
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

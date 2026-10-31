# -*- coding: utf-8 -*-
"""v1.6.3 R12 验收：窗口最小尺寸不再顶死小屏笔记本，且量的是"有数据"的最小尺寸。

改之前实测 minimumSizeHint = 885x806（设置页七张卡把最小尺寸一路顶到窗口），
1366x768 的笔记本可用高度只有 ~720，窗口连"缩到刚好放下"都做不到。
更阴的是：空看板量出来只有 465x510，喂上真实 Board 之后最小宽度又涨回 781，
窗口被 Qt 悄悄反撑到 913——所以 9)~13) 全部在有数据的状态下测量。
这一轮钉四件事：
① 最小尺寸必须塞得进 1366x720（并且留足余量到 800x600），空/有数据都要满足；
② 设置页改成滚动区之后，所有控件还在、还能滚到底见到"关于"卡；
③ 长说明文字必须换行，否则又会把最小宽度顶回去（回归哨兵）；
④ 排行行长名字/长指标、连接明细标题、顶栏状态一律可缩（省略号 + tooltip），
   缩到极限时必须真的停在极限上，不被反撑。
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

# 1366x768 笔记本去掉任务栏后的可用高度（保守取 720）
LAPTOP_W, LAPTOP_H = 1366, 720


def _mk_window():
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    win.show()
    app.processEvents()
    return app, win


def _settle(app, ms: int = 300) -> None:
    """给真实时间片转事件循环。

    离屏下 resize() 之后子件几何不是立刻重排的（qfluentwidgets 的过渡动画也要时间），
    只 processEvents() 一轮就读宽度，读到的可能是上一轮的旧值。
    另一个坑：_qt_harness.shoot() 结尾会 widget.hide()，窗口隐藏期间的 resize() 根本不重排
    布局——实测放大回 1000 后页面还停在 407。所以留图之后的量尺寸前要先 win.show()。
    """
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _child() -> int:
    use_temp_data_dir("smallscreen")
    app, win = _mk_window()
    msh = win.minimumSizeHint()
    w, h = msh.width(), msh.height()

    # ---- 1) 最小尺寸能塞进笔记本（改之前是 885x806，高就超了）----
    assert w <= LAPTOP_W and h <= LAPTOP_H, f"最小尺寸 {w}x{h} 仍放不下 {LAPTOP_W}x{LAPTOP_H}"
    assert w <= 800 and h <= 600, f"没有余量：{w}x{h} 连 800x600 都塞不进"
    print(f"1) 窗口最小尺寸 {w}x{h} 塞得进 1366x720（且留到 800x600 余量）PASS")

    # ---- 2) 真把窗口缩到以前根本不可能的高度，Qt 不再拒绝 ----
    win.resize(900, 620)
    _settle(app)
    assert win.height() == 620, f"缩不到 620，实际 {win.height()}（说明最小高度还卡着）"
    assert win.width() == 900, win.width()
    print("2) 900x620 真能缩下去 PASS")

    # ---- 3) 设置页改成滚动区：控件一个不少，且都在滚动内容里 ----
    page = win.page_settings
    win.switchTo(page)                 # 不切过去整页都是隐藏的，可见性断言没意义
    app.processEvents()
    names = ["sw_autostart", "sw_badge", "sw_topmost", "sw_close_exit",
             "sw_alert", "sw_legacy", "sp_cap", "sp_reset"]
    for n in names:
        ctrl = getattr(page, n)
        assert ctrl.isVisibleTo(win), f"{n} 看不见了"
    from PySide6.QtWidgets import QScrollArea
    scrolls = page.findChildren(QScrollArea)
    assert scrolls, "设置页没有滚动区，最小高度还会顶到窗口"
    content = scrolls[0].widget()
    assert content is not None and page.sw_topmost.isVisibleTo(content), "开关没挂在滚动内容里"
    assert page.minimumSizeHint().height() < 200, page.minimumSizeHint().height()
    print(f"3) 设置页 {len(names)} 个控件齐全，页内最小高度降到 {page.minimumSizeHint().height()} PASS")

    # ---- 4) 缩小时能滚到最后一张卡（关于），内容不是被裁掉而是可滚动 ----
    from PySide6.QtCore import QPoint
    from qfluentwidgets import CaptionLabel, CardWidget

    win.resize(msh.width(), msh.height())      # 压到窗口允许的极限尺寸
    win.switchTo(page)
    _settle(app)
    bar = scrolls[0].verticalScrollBar()
    assert bar.maximum() > 0, "内容比视口高却滚不动"
    about = next(c for c in page.findChildren(CardWidget)
                 if any(l.text().startswith("流量看板 Traffic Board") for l in c.findChildren(CaptionLabel)))
    vp_h = scrolls[0].viewport().height()
    y_top = about.mapTo(scrolls[0], QPoint(0, 0)).y()
    assert y_top > vp_h, f"关于卡本来就在视口里（y={y_top} 视口 {vp_h}），滚到底的断言没意义"
    bar.setValue(bar.maximum())
    app.processEvents()
    assert bar.value() == bar.maximum(), "滚到底没生效"
    y_after = about.mapTo(scrolls[0], QPoint(0, 0)).y()
    assert y_after < vp_h, f"滚到底了关于卡还在视口外（y={y_after} 视口 {vp_h}）"
    print(f"4) 视口 {vp_h}px 时可滚动 {bar.maximum()}px，关于卡从 y={y_top} 滚进视口 y={y_after} PASS")
    bar.setValue(0)
    app.processEvents()

    # ---- 5) 长说明必须换行：否则最小宽度会被一句话顶回去（回归哨兵）----
    caps = [c for c in page.findChildren(CaptionLabel) if len(c.text()) > 18]
    assert caps, "设置页找不到说明文字，检查结构是否又变了"
    no_wrap = [c.text()[:12] for c in caps if not c.wordWrap()]
    assert not no_wrap, f"这些说明没换行，会把最小宽度顶回去: {no_wrap}"
    print(f"5) {len(caps)} 条说明文字全部允许换行 PASS")

    # ---- 6) 帮助/体检页同款滚动结构没被这次改动带坏 ----
    for attr in ("page_health",):
        pg = getattr(win, attr)
        assert pg.findChildren(QScrollArea), f"{attr} 的滚动区没了"
    print("6) 体检页滚动区仍在 PASS")

    # ---- 7) 小窗口 + 设置页真渲染留图 ----
    from _qt_harness import shoot
    win.resize(900, 620)
    win.switchTo(win.page_settings)
    _settle(app)
    assert page.isVisibleTo(win)
    shoot(win, Path(__file__).parent / "render_v168_small_settings.png", app, settle_ms=400)
    print("7) 900x620 设置页实渲染 PASS")

    # ---- 8) 极限尺寸下主页面也没散架（排行 + 明细 + 波形都还在）----
    win.show()                                 # 上一步 shoot() 把窗口隐藏了
    win.resize(msh.width(), msh.height())
    from PySide6.QtWidgets import QWidget
    dash = win.findChild(QWidget, "dashPage")
    win.switchTo(dash)
    _settle(app)
    assert win.width() == msh.width() and win.height() == msh.height(), (win.width(), win.height())
    for name in ("rank", "table", "wave", "search"):
        assert getattr(win, name).isVisibleTo(win), f"缩到极限后 {name} 不见了"
    assert win.table.columnWidth(0) > 0, "极限尺寸下明细表第一列被挤没了"
    shoot(win, Path(__file__).parent / "render_v168_min_dash.png", app, settle_ms=400)
    print(f"8) 极限 {msh.width()}x{msh.height()} 主页面齐全 PASS")

    # ---- 9) 有数据以后最小尺寸不许暴涨（上一版的测量盲区）----
    # 空看板量出来 465x510 一片叫好，真 Board 一来最小宽度变成 781，窗口被 Qt
    # 一路反撑回 913——量最小尺寸必须喂上真实数据才有意义。
    from core.aggregator import AppView, Board, ConnView

    def _board(n_apps=6, n_conns=4):
        apps = []
        for i in range(n_apps):
            conns = [ConnView(proto="tcp", target=f"some-long-host-{i}-{j}.example.com:443",
                              channel="proxy", node="HK-1", download=1000 * (i + 1),
                              upload=100, from_clash=True, conn_id=f"c{i}{j}")
                     for j in range(n_conns)]
            apps.append(AppView(pid=400 + i, proc_name=f"proc{i}.exe",
                                display=f"某个比较长的软件名字{i}", conns=conns))
        return Board(apps=apps, clash_online=True, clash_version="1.19.0")

    win.alert_enabled = False
    win.show()        # shoot() 结尾会 hide()，隐藏的窗口 resize() 不重排子件（实测页面停在 407）
    win.resize(1000, 700)
    _settle(app)
    assert win.width() == 1000, f"窗口没能回到 1000，实际 {win.width()}"
    assert dash.width() > 900, f"窗口 1000 宽但页面还停在 {dash.width()}（离屏几何没重排，测量无意义）"
    assert win.search.width() == 260, (
        f"宽 {win.width()} 窗口下搜索框没回到 260，实际 {win.search.width()}（状态文字 {win.status.width()}）")
    win._on_board(_board())
    _settle(app)
    msh2 = win.minimumSizeHint()
    assert msh2.width() <= 800 and msh2.height() <= 600, f"有数据后最小尺寸 {msh2.width()}x{msh2.height()}"
    assert msh2.width() - w < 80, f"数据把最小宽度撑大了 {msh2.width() - w}px（排行行/标题还在顶宽度）"
    print(f"9) 喂上 6 个进程的 Board 后最小尺寸 {msh2.width()}x{msh2.height()}"
          f"（空看板 {w}x{h}）PASS")

    # ---- 10) 缩到自己的最小值，不许被反撑回去 ----
    win.resize(msh2.width(), msh2.height())
    for _ in range(3):                     # 反复收缩看是否收敛（改之前 465 -> 913 一路涨）
        _settle(app, 200)
        win.resize(win.minimumSizeHint())
        _settle(app, 200)
    assert win.width() == win.minimumSizeHint().width(), (
        f"缩不下去：窗口 {win.width()} 最小 {win.minimumSizeHint().width()}")
    assert win.width() <= msh2.width(), f"收缩过程中被撑大到 {win.width()}"
    print(f"10) 有数据反复缩到极限收敛于 {win.width()}x{win.height()}，不再被反撑 PASS")

    # ---- 11) 极限宽度下长名字走省略号，整段留在悬浮提示里 ----
    from ui.ranklist import RankRow

    rows = win.findChildren(RankRow)
    assert rows, "极限尺寸下排行行没了"
    squeezed = [r for r in rows if r._display != r.lbl_name.text()]
    assert squeezed, f"长名字没省略，行内容仍在顶窗口最小宽度：{rows[0].lbl_name.text()!r}"
    for r in squeezed:
        assert r.lbl_name.text().endswith("…"), r.lbl_name.text()
        assert r.lbl_name.toolTip() == r._display, r.lbl_name.toolTip()
        assert r.lbl_name.width() > 0, "名字标签被挤没了（不是省略号，是看不见）"
    for r in rows:
        assert r.lbl_metric.toolTip() == r._metric or r.lbl_metric.text() == r._metric
    print(f"11) {len(squeezed)}/{len(rows)} 行名字用省略号，整名在 tooltip 里 PASS")

    # ---- 12) 顶栏状态文字在极限宽度下还有可读的宽度 ----
    assert win.status.width() >= 100, f"状态文字被挤到 {win.status.width()}px，等于看不见"
    assert win.search.width() >= 100, f"搜索框被挤到 {win.search.width()}px"
    print(f"12) 极限宽度下状态 {win.status.width()}px / 搜索 {win.search.width()}px 仍可读 PASS")

    # ---- 13) 有数据 + 极限尺寸留图 ----
    shoot(win, Path(__file__).parent / "render_v168_min_dash_data.png", app, settle_ms=400)
    print("13) 有数据极限尺寸实渲染 PASS")
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

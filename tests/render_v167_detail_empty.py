# -*- coding: utf-8 -*-
"""v1.6.3 R11 验收：明细表的三种空态，以及空态下右键不再误伤别的进程。

钉住两件事：
① 选中的进程消失 / 该进程 0 条连接 / 搜索框过滤到 0 条 —— 三种"表里没东西"
   各有各的说法，不再是一格白表或一句不相干的"点击左侧任一软件"；
② 空态时 _current_detail_rows 必须清空。右键菜单按行号取连接，旧实现留着
   上一个进程的行，在提示行上点"断开此代理连接"会真去掐一条已经不显示的活连接。
"""
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
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    win.show()
    win.alert_enabled = False          # 本测试不关心提醒，别让它弹 InfoBar
    app.processEvents()
    return app, win


def _conn(target, cid, ip=""):
    from core.aggregator import ConnView
    return ConnView(proto="tcp", target=target, channel="proxy", node="HK-1",
                    download=10, upload=1, from_clash=True, conn_id=cid,
                    raddr_ip=ip)


def _board(targets=(("github.com:443", "c1"), ("aone.com:443", "c2")),
           proc="chrome.exe", with_conns=True):
    from core.aggregator import AppView, Board
    conns = [_conn(t, c) for t, c in (targets if with_conns else [])]
    return Board(apps=[AppView(pid=400, proc_name=proc, display="Chrome", conns=conns)])


def _cell(win, r, c):
    it = win.table.item(r, c)
    return it.text() if it is not None else ""


def _row_conn(win, row=0):
    """明细表第 row 行上的右键动作会落到哪条连接（None = 这条菜单点不动）。

    注意：不能真调 _detail_menu —— 它内部是 menu.exec() 模态循环，
    离屏下没人点击，测试会永久挂死（实测两次）。取连接这一步已经抽成
    _detail_menu 用的同一个方法，所以这里测的就是产品代码本身。
    """
    from PySide6.QtCore import QPoint
    y = win.table.rowViewportPosition(row) + 2
    return win._detail_row_conn(QPoint(20, y))


def _child() -> int:
    use_temp_data_dir("v167")
    app, win = _mk_window()

    # ---- 1) 从没点过任何软件：老文案，且提示行右键取不到连接 ----
    win._on_board(_board())
    app.processEvents()
    assert win.table.rowCount() == 1, win.table.rowCount()
    assert "点击左侧任一软件" in _cell(win, 0, 0), _cell(win, 0, 0)
    assert _row_conn(win) is None, "没选中任何软件，右键却能拿到一条连接"
    print("1) 未选中时提示行 + 右键无目标 PASS")

    # ---- 2) 选中后有真行，右键拿得到这条连接 ----
    win._on_pick("chrome.exe")
    app.processEvents()
    assert win.table.rowCount() == 2, win.table.rowCount()
    assert _row_conn(win) is not None and _row_conn(win, 1) is not None
    print("2) 选中进程显示真行、右键可用 PASS")

    # ---- 3) 进程消失：说清"已不在列表"，且不再握着上一个进程的行 ----
    from core.aggregator import Board
    win._on_board(Board(apps=[]))
    app.processEvents()
    assert win.table.rowCount() == 1, win.table.rowCount()
    assert "已不在当前列表" in _cell(win, 0, 0), _cell(win, 0, 0)
    assert "点击左侧任一软件" not in _cell(win, 0, 0), "进程没了还让人点左侧，等于没说"
    assert win._current_detail_rows == [], "空态还留着上一个进程的行，右键会误伤"
    assert _row_conn(win) is None, "提示行右键会掐掉一条已经不显示的活连接"
    print("3) 进程消失空态 + 右键无目标 PASS")

    # ---- 4) 该进程 0 条连接：说法要针对"没连接"，不是"进程没了" ----
    win._on_board(_board(with_conns=False))
    app.processEvents()
    assert win.table.rowCount() == 1, win.table.rowCount()
    assert "没有可见连接" in _cell(win, 0, 0), _cell(win, 0, 0)
    assert "已不在当前列表" not in _cell(win, 0, 0), _cell(win, 0, 0)
    assert win._current_detail_rows == []
    print("4) 零连接空态文案 PASS")

    # ---- 5) 搜索过滤到 0 条：把关键词和总数说出来，并给出出路 ----
    win._on_board(_board())
    app.processEvents()
    assert win.table.rowCount() == 2
    win.search.setText("zzz")
    app.processEvents()
    assert win.table.rowCount() == 1, win.table.rowCount()
    tip = _cell(win, 0, 0)
    # R27 起搜索是全局过滤：一条软件都不匹配时直接说全局无结果，
    # 不再拿「Chrome 共 2 条」这种旧选中项的上下文绕人
    assert "zzz" in tip and "清空搜索" in tip, tip
    assert "没有软件或连接匹配" in tip, tip
    assert win._current_detail_rows == []
    print("5) 无匹配空态带关键词与出路 PASS")

    # ---- 6) 清掉搜索必须能回到真行（空态不能粘住）----
    win.search.setText("")
    app.processEvents()
    assert win.table.rowCount() == 2, win.table.rowCount()
    assert _cell(win, 0, 0).startswith("github.com:443"), _cell(win, 0, 0)
    assert len(win._current_detail_rows) == 2
    print("6) 清空搜索恢复真行 PASS")

    # ---- 7) 空态不每秒重建：同一份空看板连刷 5 次，行对象没换 ----
    win.search.setText("zzz")
    app.processEvents()
    first = win.table.item(0, 0)
    for _ in range(5):
        win._on_board(_board())
        app.processEvents()
    assert win.table.item(0, 0) is first, "空态提示行每秒重建，会闪"
    win.search.setText("")
    app.processEvents()

    # ---- 8) 真渲染留图（空态长什么样得有图）----
    from _qt_harness import shoot
    win.search.setText("gitl")
    app.processEvents()
    assert "gitl" in _cell(win, 0, 0), _cell(win, 0, 0)
    shoot(win, Path(__file__).parent / "render_v167_detail_empty.png", app, settle_ms=400)
    print("8) 无匹配空态实渲染 PASS")

    # ---- 9) 帮助页要解释这三句空态（界面文案和帮助不能各说各话）----
    from ui.help_page import _SECTIONS
    faq = "".join(line for _, items in _SECTIONS for line in items)
    for phrase in ("已不在当前列表", "没有可见连接", "没有匹配"):
        assert phrase in faq, f"帮助页没解释空态「{phrase}」"
    assert _cell(win, 0, 0) and "gitl" in _cell(win, 0, 0)
    print("9) 帮助页覆盖三种空态 PASS")
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

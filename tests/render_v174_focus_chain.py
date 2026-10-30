# -*- coding: utf-8 -*-
"""v1.6.4 R20 验收：键盘焦点链闸门（开窗口第一下回车不能把窗口弄没）。

这轮量出来的事实（探针 tmp/probe_focus_chain.py，窗口真 show 之后逐跳走焦点链）：

  1. qfluentwidgets 把标题栏的 最小化→关闭→最大化 排在 Tab 链**最前面**，
     窗口一 show 初始焦点就是 MinimizeButton。键盘用户按第一下回车把窗口收进
     任务栏，第二下（CloseButton，我们的关闭是缩进托盘）看着像程序没了。
  2. 导航页签反过来一个都不在链上：NavigationWidget 继承 QWidget，默认 NoFocus，
     库里也没写 keyPressEvent——键盘完全切不了页，只能用鼠标。
  3. 那三个隐藏标题栏子件（TitleBar / FluentWidgetTitleBar，R13 起挂的遗留）
     是无害的：在全新窗口里精确摘掉它们，焦点链 11 跳一字不差、最小尺寸仍是
     456x510、标题栏照常跟随宽度。之前"隐藏按钮吃掉 Tab"是**在隐藏窗口上量**
     造成的假象——隐藏时所有子件 isVisible() 都是 False，Qt 的跳过逻辑只看
     可见性，于是钻进了从没上过屏的按钮里。

关掉标题栏按钮的焦点没有夺走出路：实测本窗口 HWND 样式含 WS_SYSMENU，系统菜单里
SC_CLOSE/SC_MINIMIZE 都在（tmp/probe_hwnd_style.py），Alt+F4 与 Alt+空格照常工作。

为什么这道闸门必须 show()：Qt 只给可见窗口设焦点，在隐藏窗口上量到的链是假的
（上面第 3 条就是这么来的）。默认跑 offscreen 平台——show() 也不会真弹窗，
而焦点链由 QtWidgets 计算、与平台无关；第 7 步的像素差只问"变没变"，
不问绝对颜色，所以离屏没有中文字形不影响判定。
FOCUS_GATE_NATIVE=1 时改跑 windows 平台核对真实初始焦点（会闪一瞬真窗口）。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
NATIVE = os.environ.get("FOCUS_GATE_NATIVE") == "1"
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if NATIVE else "offscreen")

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QWidget, QTableWidgetItem  # noqa: E402

TITLE_BTNS = ("MinimizeButton", "MaximizeButton", "CloseButton")
TAB_FOCUS = int(Qt.FocusPolicy.TabFocus)


def _child() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    use_temp_data_dir("focusgate")
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

    def settle(ms=250):
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def cls(o):
        return type(o).__name__

    def owner_of(w, stop):
        """这个控件挂在哪个标题栏下面（不在标题栏里就返回 None）。"""
        p = w.parent()
        while p is not None and p is not stop:
            if "itle" in cls(p) and hasattr(p, "closeBtn"):
                return cls(p)
            p = p.parent()
        return None

    def focusables(win):
        return [w for w in win.findChildren(QWidget)
                if int(w.focusPolicy()) != int(Qt.FocusPolicy.NoFocus)
                and w.isEnabled() and w.isVisibleTo(win)]

    def tabbable(win):
        return [w for w in focusables(win) if int(w.focusPolicy()) & TAB_FOCUS]

    def succ_of(win, w):
        """先真把焦点放到 w 上，再按真 Tab 键，看焦点跳到谁身上。

        必须逐件设焦点：Qt 的下一跳是从当前 focusWidget 出发的，不放焦点就量出假链。
        必须发真按键而不是直接调 focusNextPrevChild：表格里 Tab 被 QAbstractItemView
        接管成"下一个单元格"（焦点件不变、只换 currentIndex），键盘用户走的正是
        QWidget::event 里那条按键路径，照按键量才和他看到的行为一致。
        """
        w.setFocus()
        settle(60)
        if app.focusWidget() is not w:
            return None                      # Qt 不肯把焦点给它
        for _ in range(60):
            QTest.keyClick(w, Qt.Key_Tab)
            settle(40)
            nxt = app.focusWidget()
            if nxt is None or not nxt.isWidgetType():
                return None
            if nxt is not w:
                return nxt
        return None

    def build_cycle(head, n):
        order, cur, seen = [], head, set()
        while cur is not None and len(order) <= n:
            if id(cur) in seen:
                break
            seen.add(id(cur))
            order.append(cur)
            cur = succ_of(win, cur)
            if cur is head:
                break
        return order

    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg)
    win.resize(1000, 700)

    # ---- 1. 标题栏三个按钮退出焦点链，判定函数本身要认得出标题栏 ----
    tb = win.titleBar
    for b in (tb.minBtn, tb.maxBtn, tb.closeBtn):
        assert int(b.focusPolicy()) == int(Qt.FocusPolicy.NoFocus), \
            f"{cls(b)} 还在抢焦点（policy={int(b.focusPolicy())}）"
    assert owner_of(tb.minBtn, win) == cls(tb), "owner_of 认不出标题栏，后面的判定不可信"
    assert owner_of(win.search, win) is None, "owner_of 把内容件也算进标题栏了"
    print("1) 标题栏按钮 NoFocus，判定函数认得出归属 PASS")

    # ---- 2. 残留的两个隐藏标题栏：show 之后仍然不在屏上（所以它们不吃 Tab）----
    dead = [t for t in win.findChildren(QWidget)
            if cls(t) in ("TitleBar", "FluentWidgetTitleBar")]
    assert len(dead) == 2, f"预期的残留标题栏数量变了：{[cls(t) for t in dead]}"
    win.show()
    settle(500)
    for t in dead:
        assert not t.isVisible(), f"{cls(t)} 现在竟然上屏了，得重新量焦点链"
    print(f"2) 残留标题栏 {len(dead)} 个，show 后仍不在屏上 PASS")

    # ---- 3. 开窗口后的初始焦点必须落在内容上，而不是窗口按钮 ----
    f0 = app.focusWidget()
    assert f0 is not None and f0.isWidgetType(), "show 之后没有焦点件，Tab 起点无从谈起"
    assert cls(f0) not in TITLE_BTNS, f"初始焦点又落回窗口按钮：{cls(f0)}"
    assert owner_of(f0, win) is None, f"初始焦点在标题栏里：{cls(f0)}"
    print(f"3) 初始焦点={cls(f0)}（内容区，平台={os.environ['QT_QPA_PLATFORM']}）PASS")

    # ---- 4. 第一下回车/空格不许把窗口弄没 ----
    QTest.keyClick(f0, Qt.Key_Return)
    settle(300)
    assert win.isVisible() and not win.isMinimized(), "回车之后窗口最小化了"
    QTest.keyClick(app.focusWidget(), Qt.Key_Space)
    settle(300)
    assert win.isVisible() and not win.isMinimized(), "空格之后窗口最小化了"
    assert win.search.text() == "", "焦点在别处却把按键写进了搜索框"
    print("4) 回车/空格不最小化、不误触搜索框 PASS")

    # ---- 5. Tab 链覆盖所有内容焦点件，且不含窗口按钮 ----
    tabs = tabbable(win)
    names = [cls(w) for w in tabs]
    assert not [n for n in names if n in TITLE_BTNS], f"窗口按钮又进了 Tab 链：{names}"
    assert cls(win.search) in names, "搜索框不在可聚焦清单里，后面的可达性没意义"
    nav_tabs = getattr(win, "_nav_items", [])
    assert len(nav_tabs) >= 2, f"导航页签一个都没接管过来：{nav_tabs}"
    for it in nav_tabs:
        assert it in tabs, f"页签「{it.text()}」还是摸不到"
    chain = build_cycle(f0, len(tabs) + 6)
    in_chain = {id(w) for w in chain}
    missed = [w for w in tabs if id(w) not in in_chain]
    assert not missed, "Tab 链漏掉了：" + ", ".join(
        f"{cls(w)}「{str(getattr(w, 'text', lambda: '')())[:8]}」" for w in missed)
    assert not [w for w in chain if cls(w) in TITLE_BTNS], "链上又混进窗口按钮"
    # 明细表单独钉一条：它是这轮真正的"键盘牢房"——有行时 Tab 只在第 0 行的四列
    # 里绕圈，永远出不来（tmp/probe_tab_walk.py 实测连按 12 次全留在表内）
    win.table.setRowCount(2)
    for r in range(2):
        for c in range(4):
            win.table.setItem(r, c, QTableWidgetItem(f"r{r}c{c}"))
    win.table.setCurrentCell(0, 0)
    win.table.setFocus()
    settle(120)
    for _ in range(3):
        QTest.keyClick(win.table, Qt.Key_Tab)
        settle(120)
    left = app.focusWidget()
    assert left is not win.table, "Tab 还被困在明细表里（setTabKeyNavigation 又开了？）"
    # 帮助页「不用鼠标也能操作」写着顺序是 搜索框 → … → 连接明细 → 页签，那就得按链序量。
    # 页签排在最后是 qfluentwidgets 的装配顺序决定的（内容区先加进 win），不改，但要说实话。
    def pos_of(w):
        return next((i for i, c in enumerate(chain) if c is w), None)

    i_search, i_table = pos_of(win.search), pos_of(win.table)
    assert i_search is not None and i_table is not None, "搜索框/明细表不在链上"
    assert i_search < i_table, f"链序和帮助页不符：搜索框第 {i_search}、明细表第 {i_table}"
    tabs_at = [pos_of(t) for t in nav_tabs]
    assert all(p is not None and p > i_table for p in tabs_at), \
        f"页签没有排在明细表之后（帮助页写错顺序了）：{tabs_at} vs 表格 {i_table}"
    print(f"5) Tab 链 {len(chain)} 跳覆盖全部内容焦点件（含 {len(nav_tabs)} 个页签），"
          f"表格按 3 次 Tab 能走到 {cls(left)}，链序 搜索框#{i_search}→表格#{i_table}"
          f"→页签#{min(tabs_at)}..{max(tabs_at)} PASS")

    # ---- 6. 页签要真能键盘翻页：空格翻页、回车不误伤 ----
    idx0 = win.stackedWidget.currentIndex()
    last = nav_tabs[-1]
    last.setFocus()
    settle(120)
    assert app.focusWidget() is last, "页签接不住焦点，第 5 步是假的"
    QTest.keyClick(last, Qt.Key_Space)
    settle(500)
    idx1 = win.stackedWidget.currentIndex()
    assert idx1 != idx0, f"空格没能翻页：{idx0} -> {idx1}"
    assert win.stackedWidget.currentWidget() is win.page_health, \
        f"翻页翻到了别处：{cls(win.stackedWidget.currentWidget())}"
    cur = app.focusWidget()
    assert cur is last, f"翻页把焦点丢了（现在是 {cls(cur) if cur else None}）"
    QTest.keyClick(cur, Qt.Key_Return)
    settle(400)
    assert app.focusWidget() is last, "回车把焦点弄丢了"
    nav_tabs[0].setFocus()
    settle(120)
    QTest.keyClick(nav_tabs[0], Qt.Key_Space)
    settle(500)
    assert win.stackedWidget.currentWidget() is not win.page_health, \
        "回到第一个页签没能切回去"
    # 帮助页写了"焦点在明细表里 ↑/↓ 换行"，那就得实测（第 5 步已经给表格填了两行）
    assert win.table.rowCount() >= 2, "表格行数不够，方向键这条测不出东西"
    win.table.setFocus()
    win.table.setCurrentCell(0, 0)
    settle(150)
    QTest.keyClick(win.table, Qt.Key_Down)
    settle(150)
    assert win.table.currentRow() == 1, f"↓ 没换行：当前行 {win.table.currentRow()}"
    QTest.keyClick(win.table, Qt.Key_Up)
    settle(150)
    assert win.table.currentRow() == 0, f"↑ 没换行：当前行 {win.table.currentRow()}"
    print(f"6) 空格翻页 {idx0}->{idx1} 落在体检页，回车不丢焦点，切回首页，"
          f"表格里 ↑/↓ 换行 PASS")

    # ---- 7. 焦点必须看得见：页签自绘，聚焦不画东西等于盲操作 ----
    win.search.setFocus()
    settle(150)
    unfocused = last.grab()
    last.setFocus()
    settle(200)
    focused = last.grab()
    assert focused.size() == unfocused.size() and focused.width() > 0, "抓不到页签的图"
    u, f = unfocused.toImage(), focused.toImage()
    diff = total = near_edge = 0
    for yy in range(0, f.height(), 3):
        for xx in range(0, f.width(), 3):
            a, b = f.pixelColor(xx, yy), u.pixelColor(xx, yy)
            total += 1
            if (abs(a.red() - b.red()) + abs(a.green() - b.green())
                    + abs(a.blue() - b.blue())) > 24:
                diff += 1
                # 描边应该贴在边缘，跑到中间去变色的就是别的东西了
                if min(xx, yy, f.width() - 1 - xx, f.height() - 1 - yy) <= 6:
                    near_edge += 1
    assert diff * 6 >= total, f"聚焦后页签几乎没变化（{diff}/{total}），键盘用户看不见焦点"
    assert near_edge * 10 >= diff * 7, f"变化的点不在边框上（{near_edge}/{diff}），焦点框画歪了"
    print(f"7) 聚焦画出了描边（采样 {diff}/{total} 变了，其中贴边 {near_edge}）PASS")

    # ---- 8. 布局没被这套焦点改动顶坏 ----
    # 最小尺寸按平台各钉一个实测值：离屏是 96 DPI 的算法字体，真平台本机 DPR=1.25，
    # 两者本来就差十几个像素（R17 量像素宽度时踩过同一件事）。这里要钉的是
    # "这轮焦点改动没有把窗口顶大"，所以基线必须是同平台的数。
    base = {"offscreen": (456, 510), "windows": (457, 540)}.get(
        os.environ["QT_QPA_PLATFORM"])
    got = (win.minimumSize().width(), win.minimumSize().height())
    assert base is None or got == base, f"最小尺寸变了：{got[0]}x{got[1]}（基线 {base}）"
    assert tb.titleLabel.text() == "流量看板", f"标题栏文字丢了：{tb.titleLabel.text()!r}"
    # 标题栏是从导航栏右边开始铺的（宽度=窗口宽-导航宽），所以不能按"等于窗口宽"判，
    # 真正要钉的是：窗口改了多大，标题栏就跟着改多少，高度不动
    w_before, tb_before = win.width(), tb.width()
    win.resize(700, 600)
    settle(200)
    delta = w_before - 700
    assert tb_before - tb.width() == delta, \
        f"标题栏没跟着窗口走：窗口 -{delta} 而标题栏 -{tb_before - tb.width()}"
    assert tb.height() == 48, f"标题栏高度变了：{tb.height()}"
    assert abs(tb.width() - (win.width() - 46)) <= 2, \
        f"标题栏宽度没接上窗口宽-导航宽：{tb.width()} vs {win.width() - 46}"
    print(f"8) 最小尺寸 {got[0]}x{got[1]}（{os.environ['QT_QPA_PLATFORM']} 基线）/ "
          f"标题文字 / 标题栏跟随宽度 PASS")

    # ---- 9. 反向对照：把窗口按钮改回可聚焦，这道闸门必须叫 ----
    for b in (tb.minBtn, tb.maxBtn, tb.closeBtn):
        b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    back = [w for w in tabbable(win) if cls(w) in TITLE_BTNS]
    assert len(back) == 3, f"变异没被看见：{[cls(w) for w in back]}"
    c2 = build_cycle(back[0], len(tabbable(win)) + 6)
    assert any(cls(w) in TITLE_BTNS for w in c2), "链上抓不到窗口按钮，第 5 步是空判"
    for b in (tb.minBtn, tb.maxBtn, tb.closeBtn):
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    assert not [w for w in tabbable(win) if cls(w) in TITLE_BTNS], "还原失败"
    print("9) 变异对照：恢复可聚焦后 1/5/9 三步都会叫 PASS")

    # ---- 10. 重跑装配仍然成立（幂等，也证明事件过滤器不会重复激活）----
    n = len(win._nav_items)
    win._setup_key_chain()
    assert int(tb.closeBtn.focusPolicy()) == int(Qt.FocusPolicy.NoFocus), \
        "重跑 _setup_key_chain 后标题栏按钮又抢焦点"
    assert len(win._nav_items) == n, "重跑装配把页签数量改了"
    idx = win.stackedWidget.currentIndex()
    win._nav_items[-1].setFocus()
    settle(120)
    QTest.keyClick(win._nav_items[-1], Qt.Key_Space)
    settle(500)
    assert win.stackedWidget.currentIndex() != idx, "装了两次过滤器后空格反而不能翻页了"
    print("10) 重跑装配幂等，空格仍只切一页 PASS")

    # ---- 11. Ctrl 切页：不依赖焦点在哪，左侧高亮跟着走 ----
    # R21 实测的代价（tmp/probe_tab_cost.py，起点=该页第一个可 Tab 的控件）：
#    页签在链里的位置跟着页面走——体检页 1 跳、
    # 历史页 2 跳、帮助页 3 跳、首页搜索框里 5 跳、设置页从滚动区要 15 跳。
    # 链是单进单出的循环（一个控件只能有一个后继），没法给每页都把页签提到最前，
    # 所以给「切页」本身一条不依赖焦点位置的路。加之前这些键实测全是空的。
    n_pages = win.stackedWidget.count()
    assert len(win._page_shortcuts) == n_pages + 2, \
        f"快捷键件数不对：{len(win._page_shortcuts)} 应为 {n_pages}+2"
    panel = win.navigationInterface.panel
    assert hasattr(panel, "_currentRouteKey"), "库换了？导航选中状态没法量了"
    for i in range(n_pages):
        (win._nav_items[0] if i % 2 else win.search).setFocus()
        settle(120)
        QTest.keyClick(app.focusWidget(), Qt.Key_1 + i, Qt.ControlModifier)
        settle(400)
        got = win.stackedWidget.currentIndex()
        assert got == i, f"Ctrl+{i + 1} 没跳到第 {i} 页（现在 {got}）"
        want = win.stackedWidget.widget(i).objectName()
        assert panel._currentRouteKey == want, \
            f"页翻过去了但左侧高亮还指着 {panel._currentRouteKey!r}（应为 {want!r}）"

    win._switch_page(0)
    settle(250)
    fwd = []
    for _ in range(3):
        QTest.keyClick(app.focusWidget(), Qt.Key_Tab, Qt.ControlModifier)
        settle(300)
        fwd.append(win.stackedWidget.currentIndex())
    assert fwd == [1, 2, 3], f"Ctrl+Tab 没依次往前翻：{fwd}"
    bwd = []
    for _ in range(5):
        QTest.keyClick(app.focusWidget(), Qt.Key_Tab,
                       Qt.ControlModifier | Qt.ShiftModifier)
        settle(300)
        bwd.append(win.stackedWidget.currentIndex())
    assert bwd == [2, 1, 0, n_pages - 1, n_pages - 2], \
        f"Ctrl+Shift+Tab 没依次往后翻或没绕回：{bwd}"

    win.search.clear()
    win.search.setText("abc")
    win.search.setFocus()
    settle(150)
    QTest.keyClick(win.search, Qt.Key_1, Qt.ControlModifier)
    settle(300)
    assert win.search.text() == "abc", f"Ctrl+1 往搜索框里插了字符：{win.search.text()!r}"
    assert win.stackedWidget.currentIndex() == 0, "搜索框里按 Ctrl+1 没回首页"

    for sc in win._page_shortcuts:
        sc.setEnabled(False)
    win.table.setFocus()
    settle(120)
    idx_before = win.stackedWidget.currentIndex()
    QTest.keyClick(app.focusWidget(), Qt.Key_3, Qt.ControlModifier)
    settle(350)
    assert win.stackedWidget.currentIndex() == idx_before, \
        "把快捷键关掉后还能翻页——那第 11 步量的是别的东西在翻页"
    for sc in win._page_shortcuts:
        sc.setEnabled(True)
    print(f"11) Ctrl+1..{n_pages} 从任意焦点都翻对页且高亮跟上、Ctrl+Tab 正反面绕回、"
          f"不插字符、关掉快捷键就翻不动 PASS")

    return 0


if __name__ == "__main__":
    if am_child():
        try:
            code = _child()
        except AssertionError as e:
            print(f"FAIL: {e}")
            code = 1
        except Exception:
            import traceback
            traceback.print_exc()
            code = 1
        hard_exit(code)
    elif os.name == "nt":
        sys.exit(run_isolated(str(Path(__file__).resolve())))
    else:
        hard_exit(_child())

# -*- coding: utf-8 -*-
"""R27 验收：搜索要「看得懂、找得到、退得出去」。

改之前实测（tmp/probe_search.py）：
  · 搜软件名「chrome」：排行只剩一行，右侧明细却是 0 条（只按连接目标过滤）；
  · 选着 Chrome 搜「微信」：左边只剩微信，右边仍写「Chrome 的连接（2 条）」；
  · 搜 IP「10.0.0.1」：明细 1 条，标题仍写「2 条」，排行副行仍写「代理 1 · 直连 1」；
  · Esc 不清空、Ctrl+F 不聚焦、回车没反应，排行行 focusPolicy=0。

本闸门按这五条钉：软件名命中→全部连接、目标命中→匹配/共几、选中项自动跟到
第一条结果、Esc/Ctrl+F/回车各就各位、无结果有明确空态。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))


def _child() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator, AppView, Board, ConnView
    from ui.main_window import MainWindow

    use_temp_data_dir("v177")
    app = QApplication.instance() or QApplication([])

    def settle(ms=250):
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    win.resize(1000, 700)
    win.show()
    settle(500)

    chrome = AppView(1, "chrome.exe", "Chrome", [
        ConnView("tcp", "example.com:443", "proxy", upload=100, download=900),
        ConnView("tcp", "10.0.0.1:80", "direct", upload=10, download=20),
    ])
    wechat = AppView(2, "wechat.exe", "微信", [
        ConnView("tcp", "example.org:443", "proxy", upload=5, download=50),
    ])
    win._board = Board(apps=[chrome, wechat], clash_online=True, data_mode="tun")
    win._selected_key = chrome.key
    win._render()
    settle(200)

    def rows():
        return list(win.rank._rows_by_key.keys())

    def search(text, ms=250):
        win.search.setText(text)
        settle(ms)

    def metric(key):
        return win.rank._rows_by_key[key].lbl_metric.text()

    # ---- 1. 搜软件名：明细必须显示该软件的全部连接 ----
    search("chrome")
    assert rows() == ["chrome.exe"], f"软件名搜索的排行不对：{rows()}"
    assert len(win._current_detail_rows) == 2, \
        f"搜软件名后明细只剩 {len(win._current_detail_rows)} 条（应按软件名放行全部连接）"
    assert win.detail_title.text() == "Chrome 的连接（2 条）", \
        f"软件名命中时标题不该写匹配数：{win.detail_title.text()!r}"
    print("1) 搜 chrome：排行 1 行、明细 2/2 条 PASS")

    # ---- 2. 搜 IP：排行与明细都要说清「匹配几条 / 共几条」 ----
    search("10.0.0.1")
    assert rows() == ["chrome.exe"], f"按 IP 搜索的排行不对：{rows()}"
    assert metric("chrome.exe") == "匹配 1 条 · 共 2 条", \
        f"排行副行没写匹配数：{metric('chrome.exe')!r}"
    assert win.detail_title.text() == "Chrome 的连接（匹配 1 / 共 2 条）", \
        f"明细标题没写匹配数：{win.detail_title.text()!r}"
    assert len(win._current_detail_rows) == 1, "按 IP 搜索的明细行数不对"
    print("2) 搜 10.0.0.1：副行与标题均为 匹配1/共2，明细 1 条 PASS")

    # ---- 3. 选中项被过滤掉时，自动跟到第一条结果 ----
    assert win._selected_key == "chrome.exe"
    search("微信")
    assert win._selected_key == "wechat.exe", \
        f"搜微信后选中项还停在 {win._selected_key!r}"
    assert win.detail_title.text() == "微信 的连接（1 条）", \
        f"明细没跟到微信：{win.detail_title.text()!r}"
    assert rows() == ["wechat.exe"], f"搜微信的排行不对：{rows()}"
    print("3) 搜微信：选中项 chrome→wechat，明细同步切换 PASS")

    # ---- 4. 无结果：排行与明细都有明确空态；选中项留着，清搜索能原样回来 ----
    search("zzz-nope")
    assert rows() == [], f"无结果仍有排行：{rows()}"
    assert win.rank._hint is not None and win.rank._hint.text() == "没有匹配的软件", \
        "排行没有「没有匹配的软件」空态"
    assert win._selected_key == "wechat.exe", \
        f"无结果时选中项被清掉了（清搜索后回不到原样）：{win._selected_key!r}"
    assert not win._current_detail_rows
    assert "没有软件或连接匹配" in win.table.item(0, 0).text(), \
        f"明细空态没说清原因：{win.table.item(0, 0).text()!r}"
    win.search.setText("微信")
    settle(300)
    assert win.detail_title.text() == "微信 的连接（1 条）", \
        f"清掉无结果搜索后明细没回到原选中项：{win.detail_title.text()!r}"
    print("4) 无结果：排行空态 + 明细空态 + 选中保留可恢复 PASS")

    # ---- 5. Esc 清空搜索并恢复完整排行；清空按钮同样有效 ----
    win.search.setFocus()
    settle(100)
    QTest.keyClick(win.search, Qt.Key_Escape)
    settle(300)
    assert win.search.text() == "", f"Esc 没清空搜索：{win.search.text()!r}"
    assert len(rows()) == 2, f"Esc 后排行没恢复：{rows()}"
    assert app.focusWidget() is win.search, "Esc 把焦点弄丢了"
    search("chrome")
    assert win.search.clearButton.isVisible(), "有内容时清空按钮应该可见"
    QTest.mouseClick(win.search.clearButton, Qt.MouseButton.LeftButton)
    settle(300)
    assert win.search.text() == "", "点清空按钮没清空"
    assert len(rows()) == 2, f"清空按钮后排行没恢复：{rows()}"
    print("5) Esc 与清空按钮都能清空并恢复完整排行 PASS")

    # ---- 6. Ctrl+F：从设置页回到流量页并聚焦搜索框，已有内容全选 ----
    search("chrome")
    win.switchTo(win.page_settings)
    settle(500)
    win.page_settings.sw_alert.setFocus()
    settle(150)
    QTest.keyClick(app.focusWidget(), Qt.Key_F, Qt.ControlModifier)
    settle(500)
    assert win.stackedWidget.currentIndex() == 0, "Ctrl+F 没回到流量首页"
    assert app.focusWidget() is win.search, \
        f"Ctrl+F 后焦点在 {type(app.focusWidget()).__name__ if app.focusWidget() else None}"
    assert win.search.text() == "chrome", f"Ctrl+F 改了搜索内容：{win.search.text()!r}"
    assert win.search.selectedText() == "chrome", "Ctrl+F 没有全选已有内容"
    print("6) Ctrl+F：回首页 + 聚焦 + 全选，不改内容 PASS")

    # ---- 7. 回车：选中第一条结果并把焦点送进明细 ----
    search("example")           # 两个软件的目标都命中，Chrome 流量更大排第一
    assert set(rows()) == {"chrome.exe", "wechat.exe"}, f"搜 example 的排行不对：{rows()}"
    win.search.setFocus()
    settle(100)
    QTest.keyClick(win.search, Qt.Key_Return)
    settle(300)
    assert win._selected_key == "chrome.exe", f"回车没选中第一条：{win._selected_key!r}"
    assert app.focusWidget() is win.table, \
        f"回车后焦点在 {type(app.focusWidget()).__name__ if app.focusWidget() else None}"
    assert win.table.currentRow() == 0, f"明细没落到第一行：{win.table.currentRow()}"
    print("7) 回车：选中第一条并进入明细第一行 PASS")

    # ---- 8. 无结果时回车不抢焦点、不崩、不动已有选中 ----
    search("zzz-nope")
    win.search.setFocus()
    settle(100)
    QTest.keyClick(win.search, Qt.Key_Return)
    settle(250)
    assert app.focusWidget() is win.search, "无结果回车把焦点挪走了"
    assert win._selected_key == "chrome.exe", \
        f"无结果回车改出了选中项：{win._selected_key!r}"
    print("8) 无结果回车：焦点留在搜索框、选中项不变 PASS")

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

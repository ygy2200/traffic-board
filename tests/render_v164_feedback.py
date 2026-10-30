# -*- coding: utf-8 -*-
"""v1.6.3 R8 验收：右键菜单三类动作必须有可见回执（成功/失败都算）。

背景：打包后 console=False，槽函数里的异常只会消失在黑洞里。导出 CSV / 起 Wireshark /
断开代理连接这三条曾经全靠"运气好没出错"，出错时用户点了没反应。
"""
import inspect
import os
import stat
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, shoot, use_temp_data_dir  # noqa: E402

# 会往注册表写设置（MainWindow 构造读 badge/pos 等），还原统一交父进程
if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))

HERE = Path(__file__).parent


def _child() -> int:
    use_temp_data_dir("v164")
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import InfoBar

    from core.aggregator import Aggregator, AppView, Board, ConnView
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg)
    win.resize(1000, 700)
    win.show()
    app.processEvents()

    board = Board(apps=[
        AppView(pid=400, proc_name="=evil.exe", display="= evil", conns=[
            ConnView(proto="tcp", target="example.com:443", channel="proxy",
                     node="HK-1", download=1234, upload=99, from_clash=True,
                     conn_id="c1", raddr_ip="93.1.1.1", raddr_port=443),
            ConnView(proto="udp", target="1.1.1.1:53", channel="direct",
                     download=0, upload=0),
        ]),
        AppView(pid=401, proc_name="chrome.exe", display="Chrome", conns=[]),
    ])
    win._board = board

    toasts = []
    real_toast = win._toast
    win._toast = lambda kind, title, content: toasts.append((kind, title, content))
    tmpdir = Path(tempfile.gettempdir())

    # ---- 1) 导出成功：回执含行数，文件真写出来 ----
    ok = tmpdir / f"tb-v164-{os.getpid()}-ok.csv"
    win._export_csv(str(ok))
    assert len(toasts) == 1 and toasts[0][0] == "success", toasts
    assert "2 条" in toasts[0][2], toasts
    raw = ok.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "缺 UTF-8 BOM，Excel 打开必乱码"
    text = raw.decode("utf-8-sig")
    assert text.count("\n") == 3, f"表头+2 行，实得 {text!r}"
    assert "'=evil" in text or "'= evil" in text, f"公式注入没转义: {text!r}"
    assert "代理" in text and "直连" in text, text
    print("1) 导出成功回执 + BOM + 转义 PASS")

    # ---- 2) 目录不存在：失败必须可见，且不留半个文件 ----
    bad_dir = tmpdir / f"tb-v164-{os.getpid()}-nope"
    bad = bad_dir / "sub.csv"
    win._export_csv(str(bad))
    assert len(toasts) == 2 and toasts[1][0] == "error", toasts
    assert not bad.exists(), "失败却留下了半个文件"
    print("2) 路径不可写有失败回执 PASS")

    # ---- 3) 文件被占用/只读：说明原因，且绝不截断用户原文件 ----
    keep = tmpdir / f"tb-v164-{os.getpid()}-keep.csv"
    keep.write_text("原有内容\n", encoding="utf-8")
    keep.chmod(stat.S_IREAD)
    try:
        win._export_csv(str(keep))
        assert toasts[-1][0] == "error", toasts
        assert "占用" in toasts[-1][2], f"失败原因没用人话说清: {toasts[-1]}"
        assert keep.read_text(encoding="utf-8") == "原有内容\n", "失败时把用户原文件清空了"
    finally:
        keep.chmod(stat.S_IWRITE | stat.S_IREAD)
    print("3) 占用态回执 + 原文件未被截断 PASS")

    # ---- 4) 空看板也要有回执（"点了没反应"就是没回执）----
    win._board = Board(apps=[])
    empty = tmpdir / f"tb-v164-{os.getpid()}-empty.csv"
    win._export_csv(str(empty))
    assert toasts[-1][0] == "success" and "0 条" in toasts[-1][2], toasts[-1]
    print("4) 空列表导出仍给回执 PASS")

    # ---- 5) 断开代理连接：成功/失败两条路各有回执 ----
    cv = board.apps[0].conns[0]
    win.agg.clash.close_connection = lambda cid: True
    win._close_conn(cv)
    assert toasts[-1][0] == "success", toasts[-1]
    win.agg.clash.close_connection = lambda cid: False
    win._close_conn(cv)
    assert toasts[-1][0] == "warning", toasts[-1]
    print("5) 断开连接成功/失败都有回执 PASS")

    # ---- 6) Wireshark 起不来要说话（缺文件/无权限都是 OSError）----
    win._launch_wireshark(r"C:\tb-nope\Wireshark.exe", "host 93.1.1.1")
    assert toasts[-1][0] == "error" and "Wireshark" in toasts[-1][1], toasts[-1]
    print("6) Wireshark 启动失败有回执 PASS")

    # ---- 7) 接线检查：上面这些方法必须真被右键菜单调用，否则是在测死代码 ----
    src = inspect.getsource(MainWindow._detail_menu)
    for name in ("_export_csv", "_close_conn", "_launch_wireshark"):
        assert name + "(" in src, f"{name} 没接进 _detail_menu"
    print("7) 三条动作都接在 _detail_menu 上 PASS")

    # ---- 8) 真 InfoBar 挂得上去（不打桩；文字断言用属性，离屏截图中文是豆腐块不可读）----
    win._board = board
    win._toast = real_toast
    win._toast("success", "已导出", "2 条连接 → 桌面/连接列表.csv")
    app.processEvents()
    bars = win.findChildren(InfoBar)
    assert len(bars) == 1, f"InfoBar 没挂到主窗口（父窗口传错或 API 变了）: {bars}"
    bar = bars[0]
    assert bar.title == "已导出" and "连接列表.csv" in bar.content, (bar.title, bar.content)
    assert bar.contentLabel.isVisibleTo(win) and bar.isVisibleTo(win), "文字标签没显示出来"
    shoot(win, HERE / "render_v164_infobar.png", app, settle_ms=500)

    # shoot() 会把窗口 hide() —— 正好验第二条通路：窗口收进托盘时退回托盘气泡
    assert not win.isVisible(), "shoot 后窗口应已隐藏（本步前提）"
    bubbles = []
    win.tray.showMessage = lambda *a, **k: bubbles.append(a)
    n_bars = len(win.findChildren(InfoBar))
    win._toast("error", "导出失败", "看不见的窗口也要能说话")
    assert len(bubbles) == 1 and bubbles[0][0] == "导出失败", bubbles
    assert len(win.findChildren(InfoBar)) == n_bars, "窗口没显示却新弹了 InfoBar"
    print("8) 窗口隐藏时回执退回托盘气泡 PASS")

    win.show()
    app.processEvents()
    win._toast("error", "导出失败", "连接列表.csv：目标文件正被占用或没有写入权限")
    app.processEvents()
    titles = {b.title for b in win.findChildren(InfoBar)}
    assert titles == {"已导出", "导出失败"}, titles   # 两条回执同时挂在窗口顶部（会堆叠）
    shoot(win, HERE / "render_v164_infobar_error.png", app, settle_ms=500)
    print("9) 成功/失败 InfoBar 实渲染 + 文案落在窗口上 PASS")
    return 0


if __name__ == "__main__":
    try:
        code = _child()
    except Exception:
        # 失败一定要走 hard_exit：否则 Qt 在解释器收尾阶段段错误，
        # rc 变成 139/127，「断言没过」和「崩在 teardown」就分不出来了。
        import traceback
        traceback.print_exc()
        code = 1
    hard_exit(code)

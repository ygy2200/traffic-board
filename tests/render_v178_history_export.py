# -*- coding: utf-8 -*-
"""R28 验收：历史页能导出 CSV，且在"没数据"时把话说明白。

改之前实测：
  · 历史页没有任何导出入口，30 天数据只能看不能带走；
  · 空态只有一句「积累中…」，不说多久才有一条、也不说软件流量为什么要 Clash；
  · 切到 7 天只是换了柱子，空态文案不跟着走。

本闸门钉八条：CSV 表头/行数/BOM/中文软件名、范围过滤真的生效、成功/失败回执、
空范围不弹框不建文件也不毁旧文件、空态文案按范围说清、导出按钮可 Tab 可空格、
回执在 MainWindow 走 _toast、默认文件名带时间戳。
"""
import csv
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from _qt_harness import am_child, hard_exit, run_isolated, shoot, use_temp_data_dir  # noqa: E402

if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))

HERE = Path(__file__).parent


def _child() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from core.history import HistoryStore
    import ui.history_page as history_ui
    from ui.history_page import HistoryPage, default_csv_path

    use_temp_data_dir("v178")
    app = QApplication.instance() or QApplication([])
    from qfluentwidgets import Theme, setTheme
    setTheme(Theme.DARK)      # 与生产一致；否则离屏截图是浅底白字，看不清

    def settle(ms=200):
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    tmpdir = Path(tempfile.mkdtemp(prefix="tb-v178-"))
    db = str(tmpdir / "history.sqlite")
    store = HistoryStore(db)
    now = int(time.time())
    base = (now // 3600) * 3600
    t1, t2 = base - 2 * 3600, base - 3600
    old = t1 - 8 * 86400
    store.write_tick(t1, {"chrome.exe": (10, 20, 2), "微信": (5, 6, 1)}, 1000, 2000)
    store.write_tick(t2, {"chrome.exe": (30, 40, 3), "=SUM(A1)": (1, 1, 1)}, 3000, 4000)
    store.write_tick(old, {"chrome.exe": (1, 2, 1)}, 7, 8)   # 8 天前，只该出现在 7 天外

    page = HistoryPage(store)
    page.resize(900, 640)
    page.show()
    page.activateWindow()
    settle(400)
    page.refresh()
    settle(150)
    events = []
    page.exported.connect(lambda k, m: events.append((k, m)))

    # ---- 1. 有数据时：图表有柱、排行有软件、总览不是占位符 ----
    assert len(page.chart._bars) == 2, f"24h 桶数不对：{page.chart._bars}"
    rank = page.rank_area.text()
    assert "chrome.exe" in rank and "微信" in rank, f"排行缺软件：{rank!r}"
    for k in ("today", "week", "month"):
        assert not page._sum_labels[k].text().startswith("—"), f"{k} 总览没刷新"
    print("1) 有数据：图表 2 桶 + 排行含两软件 + 总览已刷新 PASS")

    # ---- 2. CSV：表头 / 行数 / BOM / 中文 / 空列 ----
    out = tmpdir / "export.csv"
    n_total, n_app = store.export_csv(now - 86400, str(out))
    assert (n_total, n_app) == (2, 4), f"导出行数不对：{(n_total, n_app)}"
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 缺 UTF-8 BOM，Excel 打开中文会乱码"
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == ["类型", "时间", "软件", "上行字节", "下行字节", "连接数"], rows[0]
    assert len(rows) == 7, f"表头+数据应为 7 行：{len(rows)}"
    assert rows[1][0] == "本机" and rows[1][2] == "" and rows[1][5] == "", rows[1]
    assert any(r[0] == "软件" and r[2] == "微信" for r in rows[1:]), "中文软件名没保住"
    app_row = next(r for r in rows[1:] if r[2] == "微信")
    assert app_row[3:] == ["5", "6", "1"], f"软件行字节/连接数不对：{app_row}"
    evil = next(r for r in rows[1:] if r[2].lstrip("'") == "=SUM(A1)")
    assert evil[2].startswith("'="), f"公式开头的进程名没转义：{evil[2]!r}"
    print("2) CSV 表头/7 行/ BOM / 微信 / 连接数 / 公式转义 PASS")

    # ---- 3. 范围真的过滤：7 天导出不含 8 天前那拍；成功/失败都有回执 ----
    page._set_range("7d")
    settle(100)
    out7 = tmpdir / "export7.csv"
    assert page._export_to(str(out7)) is True
    rows7 = list(csv.reader(out7.read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows7) == 7, f"7d 导出应仍是 7 行（老的 2 行被排除）：{len(rows7)}"
    assert events[-1][0] == "success", events[-1]
    assert "已导出 6 行" in events[-1][1], events[-1][1]
    assert str(out7) in events[-1][1], "回执里没写导到哪"
    page._export_to(str(tmpdir / "no_such_dir" / "x.csv"))
    assert events[-1][0] == "error" and "导出失败" in events[-1][1], events[-1]
    print("3) 7d 范围过滤生效 + 成功/失败回执含行数与路径 PASS")

    # ---- 4. 空库：三处空态都说清来源与节奏；导出不建文件、不毁旧文件 ----
    empty = HistoryPage(HistoryStore(str(tmpdir / "empty.sqlite")))
    empty.resize(900, 640)
    empty.show()
    settle(300)
    empty.refresh()
    settle(100)
    events_e = []
    empty.exported.connect(lambda k, m: events_e.append((k, m)))
    assert empty.chart._bars == []
    assert "24 小时" in empty.chart._empty_text and "1 分钟" in empty.chart._empty_text, \
        empty.chart._empty_text
    assert "Clash" in empty.rank_area.text() and "上方" in empty.rank_area.text(), \
        empty.rank_area.text()
    keep = tmpdir / "keep.csv"
    keep.write_text("KEEP", encoding="utf-8")
    assert empty._export_to(str(keep)) is False
    assert keep.read_text(encoding="utf-8") == "KEEP", "空导出把已有文件毁了"
    assert events_e[-1][0] == "info" and "还没有记录" in events_e[-1][1], events_e[-1]
    assert empty.store.has_data_since(int(time.time()) - 3 * 3600) is False
    assert page.store.has_data_since(int(time.time()) - 3 * 3600) is True
    # 空范围点导出不该先弹保存对话框再告诉你没内容
    class _NoDialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            raise AssertionError("空范围不该弹保存对话框")
    orig_dlg = history_ui.QFileDialog
    history_ui.QFileDialog = _NoDialog
    try:
        empty._on_export()
        settle(100)
    finally:
        history_ui.QFileDialog = orig_dlg
    assert events_e[-1][0] == "info" and "还没有记录" in events_e[-1][1], events_e[-1]
    empty._set_range("7d")
    settle(100)
    assert "7 天" in empty.chart._empty_text, empty.chart._empty_text
    print("4) 空库：24h/7d 文案 + 空导出不建文件也不毁旧文件 PASS")

    # ---- 5. 按钮可 Tab 可空格：焦点从 7d 走到导出，空格真的触发保存 ----
    assert page.btn_export.focusPolicy() == Qt.FocusPolicy.StrongFocus, \
        f"导出按钮不可聚焦：{page.btn_export.focusPolicy()}"
    assert "Excel" in page.btn_export.toolTip(), page.btn_export.toolTip()
    page._set_range("24h")
    page.raise_()
    page.activateWindow()
    settle(250)
    page.btn_7d.setFocus()
    settle(150)
    assert app.focusWidget() is page.btn_7d, \
        f"焦点没落在 7 天按钮：{type(app.focusWidget()).__name__}"
    QTest.keyClick(page.btn_7d, Qt.Key_Tab)
    settle(150)
    assert app.focusWidget() is page.btn_export, \
        f"Tab 没走到导出按钮：{type(app.focusWidget()).__name__}"
    via_dialog = tmpdir / "via_dialog"      # 故意不带 .csv，验证后缀补齐
    class _FakeDialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return (str(via_dialog), "CSV 文件 (*.csv)")
    orig = history_ui.QFileDialog
    history_ui.QFileDialog = _FakeDialog
    try:
        QTest.keyClick(page.btn_export, Qt.Key_Space)
        settle(250)
    finally:
        history_ui.QFileDialog = orig
    assert (tmpdir / "via_dialog.csv").is_file(), "空格没触发导出或没补 .csv 后缀"
    assert events[-1][0] == "success" and "已导出" in events[-1][1], events[-1]
    print("5) Tab 聚焦 + 空格触发 + 自动补 .csv + 成功回执 PASS")

    # ---- 6. 页面回执接到主窗口：走 _toast，而不是自己吞掉 ----
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    seen = []
    win._toast = lambda kind, title, msg: seen.append((kind, title, msg))
    win.page_history.exported.emit("success", "已导出 5 行（本机 2 + 软件 3）")
    assert seen and seen[0][0] == "success" and "已导出" in seen[0][1], seen
    win.page_history.exported.emit("error", "导出失败：磁盘已满")
    assert seen[-1][0] == "error" and "失败" in seen[-1][1], seen[-1]
    win.hide()
    print("6) 历史页回执在 MainWindow 走 _toast（成功/失败都有标题）PASS")

    # ---- 7. 默认文件名带时间戳，落在下载/用户目录 ----
    name = default_csv_path()
    assert name.endswith(".csv") and "流量看板历史-" in Path(name).name, name
    stamp = time.strftime("%Y%m%d", time.localtime())
    assert stamp in Path(name).name, f"默认文件名没带日期：{name}"
    print(f"7) 默认文件名 {Path(name).name} PASS")

    # ---- 8. 真实渲染留图 ----
    shoot(page, HERE / "render_v178_history_full.png", app)
    shoot(empty, HERE / "render_v178_history_empty.png", app)
    print("8) 有数据/空态两版渲染留图 PASS")

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

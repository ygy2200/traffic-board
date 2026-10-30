# -*- coding: utf-8 -*-
"""v1.6.3 R16 验收：界面文字对比度闸门（真实渲染像素，不是声明的颜色）。

为什么这个套件不用 offscreen（其它 render_* 都用）：
① 离屏平台没有中文字形，标签区一个墨点都没有，量出来"没有文字"= 没有缺陷，
   闸门会永远绿灯；② 离屏 grab 的卡片背景是 Qt 默认浅灰 #efefef，
   qfluentwidgets 的主题背景根本没画进去，背景色本身就是错的。
实测 windows 平台下不 show() 也能 grab 到真图（tmp/probe_grab_native.py：
卡片底 0x292929、文字有墨、125% 缩放下位图是逻辑尺寸的 1.25 倍），
所以这里全程不弹窗，只抓位图。

前景色取"离背景最远的那 2% 像素的众数"，不取墨色众数：小字号中文全是抗锯齿边，
墨色众数量到的是灰边——实测把 CaptionLabel 的白字算成 (120,120,120)，
对比度虚低一半。抗锯齿只会把字色往背景方向混，所以最远的像素才是真字色。

阈值用 WCAG AA：正文 4.5:1，大字（≥14pt 或 ≥11pt 加粗）3:1。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # 见文件头：离屏量不出中文

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

MIN_LABELS = 40       # 5 个页面逐屏滚动，少于这个数说明遍历漏了，闸门等于没测
AA_SMALL = 4.5
AA_LARGE = 3.0

app = None
win = None


def _settle(ms=200):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _modal(pixels):
    seen, best, cnt = {}, None, -1
    for p in pixels:
        seen[p] = seen.get(p, 0) + 1
        if seen[p] > cnt:
            best, cnt = p, seen[p]
    return best, cnt


def _measure(img, dpr, w, host):
    """返回 (对比度, 前景, 背景, 墨占比)；标签没完整落在可视区里返回 None。"""
    from PySide6.QtCore import QPoint

    top = w.mapTo(host, QPoint(0, 0))
    x0, y0 = int(top.x() * dpr), int(top.y() * dpr)
    x1, y1 = x0 + int(w.width() * dpr), y0 + int(w.height() * dpr)
    cx0, cy0 = max(x0, 0), max(y0, 0)
    cx1, cy1 = min(x1, img.width()), min(y1, img.height())
    need = (x1 - x0) * (y1 - y0)
    if need <= 0 or (cx1 - cx0) * (cy1 - cy0) < need * 0.95:
        return None                             # 被裁一半的标签量到的是背景
    if cx1 - cx0 < 8 or cy1 - cy0 < 6:
        return None
    cols = []
    for y in range(cy0, cy1):
        for x in range(cx0, cx1):
            p = img.pixel(x, y)
            cols.append(((p >> 16) & 0xFF, (p >> 8) & 0xFF, p & 0xFF))
    bg, _ = _modal(cols)
    ink = [c for c in cols if max(abs(c[i] - bg[i]) for i in range(3)) >= 40]
    if not ink:
        return (1.0, bg, bg, 0.0)               # 与背景同色 = 隐形
    ink.sort(key=lambda c: -abs(_lum(c) - _lum(bg)))
    fg, _ = _modal(ink[:max(1, len(ink) // 50)])
    return (_ratio(fg, bg), fg, bg, len(ink) / len(cols))


def _pages():
    from PySide6.QtWidgets import QWidget

    out = [("流量", win.findChild(QWidget, "dashPage")), ("帮助", win.page_help),
           ("设置", win.page_settings), ("历史", win.page_history),
           ("体检", win.page_health)]
    return [(n, p) for n, p in out if p is not None]


def _audit(pages):
    """逐页、逐屏滚动地量一遍；返回 [(对比度, 需要, 页, 类名, 文字, 前景, 背景, 墨占比)]。"""
    from PySide6.QtWidgets import QLabel

    rows, seen = [], set()
    for name, page in pages:
        win.switchTo(page)
        _settle()
        from PySide6.QtWidgets import QAbstractScrollArea

        areas = [sa for sa in page.findChildren(QAbstractScrollArea)
                 if sa.verticalScrollBar().maximum() > 0]
        if areas:
            step = max(sa.viewport().height() for sa in areas)
            top = max(sa.verticalScrollBar().maximum() for sa in areas)
            stops = list(range(0, top + 1, max(step * 2 // 3, 50))) + [top]
        else:
            stops = [0]
        for pos in stops:
            for sa in areas:
                sa.verticalScrollBar().setValue(pos)
            _settle(120)
            img = page.grab().toImage()
            dpr = img.devicePixelRatio() or 1.0
            for w in page.findChildren(QLabel):
                if id(w) in seen or not w.isVisibleTo(page) or not (w.text() or "").strip():
                    continue
                m = _measure(img, dpr, w, page)
                if m is None:
                    continue
                seen.add(id(w))
                r, fg, bg, cover = m
                f = w.font()
                large = (f.pointSizeF() >= 14) or (f.bold() and f.pointSizeF() >= 11)
                rows.append((r, AA_LARGE if large else AA_SMALL, name, type(w).__name__,
                             w.text().strip().replace("\n", " ")[:26], fg, bg, cover))
    return rows


def _board(online: bool):
    from core.aggregator import AppView, Board, ConnView
    from core.clash_api import ClashState

    apps = []
    for i in range(6):
        conns = [ConnView(proto="tcp", target=f"host-{i}-{j}.example.com:443",
                          channel="proxy", node="HK-1", download=1000 * (i + 1),
                          upload=100, from_clash=True, conn_id=f"c{i}{j}")
                 for j in range(3)]
        apps.append(AppView(pid=400 + i, proc_name=f"proc{i}.exe",
                            display=f"某软件名字{i}", conns=conns))
    win.agg.clash.state = ClashState(online=online, version="1.19.0" if online else "")
    win._on_board(Board(apps=apps, clash_online=online, clash_version="1.19.0"))
    _settle()


def _status_caption() -> str:
    from PySide6.QtWidgets import QLabel

    for w in win.findChildren(QLabel):
        if "Clash 未连接" in (w.text() or "") or "Clash 已连接" in (w.text() or ""):
            return w.text()
    return ""


def _child() -> int:
    global app, win
    use_temp_data_dir("v171")
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from qfluentwidgets import isDarkTheme
    import ui.main_window as mw

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = mw.MainWindow(agg)
    win.alert_enabled = False
    win.resize(1000, 700)
    _settle(350)

    # ---- 1) 量的必须是被真实画出来的深色界面 ----
    assert isDarkTheme(), "本轮闸门按「应用强制深色」的前提量；主题变了要先重看前提"
    _board(online=False)
    pages = _pages()
    assert len(pages) == 5, f"只找到 {len(pages)} 个页面，遍历漏了"
    dash = pages[0][1]
    img = dash.grab().toImage()
    bg, _ = _modal([((img.pixel(x, 3) >> 16) & 0xFF, (img.pixel(x, 3) >> 8) & 0xFF,
                     img.pixel(x, 3) & 0xFF) for x in range(0, img.width(), 7)])
    assert _lum(bg) < 0.2, f"抓到的背景是 {bg}（亮度 {_lum(bg):.2f}），不像深色主题真图"
    print(f"1) 深色主题真实渲染：页面 5 个，抓到的底色 {bg} 亮度 {_lum(bg):.3f} PASS")

    # ---- 2) 全量遍历：数量本身要够，否则闸门是空的 ----
    rows = _audit(pages)
    assert len(rows) >= MIN_LABELS, f"只量到 {len(rows)} 处文字（门槛 {MIN_LABELS}）"
    print(f"2) 逐屏滚动量到 {len(rows)} 处文字（门槛 {MIN_LABELS}）PASS")

    # ---- 3) 不许有"与背景同色"的隐形文字 ----
    ghost = [r for r in rows if r[7] == 0.0]
    assert not ghost, "隐形文字（与背景同色）：\n" + "\n".join(
        f"  {r[2]}/{r[3]} 「{r[4]}」 fg={r[5]} bg={r[6]}" for r in ghost[:8])
    print("3) 与背景同色的隐形文字 0 处 PASS")

    # ---- 4) WCAG AA：正文 4.5:1，大字 3:1 ----
    bad = sorted([r for r in rows if r[0] < r[1]], key=lambda r: r[0])
    assert not bad, f"对比度不达标 {len(bad)} 处：\n" + "\n".join(
        f"  {r[0]:.2f} 需{r[1]} {r[2]}/{r[3]} 「{r[4]}」 fg={r[5]} bg={r[6]}"
        for r in bad[:8])
    lowest = min(rows, key=lambda r: r[0])
    print(f"4) {len(rows)} 处文字对比度全部达标，最低 {lowest[0]:.2f}"
          f"（{lowest[2]}/{lowest[3]} 「{lowest[4]}」）PASS")

    # ---- 5) 本轮修的那一处：顶栏"Clash 未连接"必须 ≥4.5 ----
    txt = _status_caption()
    assert "Clash 未连接" in txt, f"顶栏状态没走到未连接分支：{txt!r}"
    hit = [r for r in rows if "Clash 未连接" in r[4]]
    assert hit, "未连接那行没被量到（被裁掉/文字对不上），第 5 步等于没断言"
    assert hit[0][0] >= AA_SMALL, f"未连接状态文字对比度 {hit[0][0]:.2f} < {AA_SMALL}"
    print(f"5) 「Clash 未连接」那行 {hit[0][0]:.2f}:1（fg={hit[0][5]} bg={hit[0][6]}）PASS")

    # ---- 6) 已连接的绿色那行同样要达标，另一条分支别漏 ----
    _board(online=True)
    txt = _status_caption()
    assert "Clash 已连接" in txt, txt
    rows2 = _audit([pages[0]])
    hit = [r for r in rows2 if "Clash 已连接" in r[4]]
    assert hit, "已连接那行没被量到"
    assert hit[0][0] >= AA_SMALL, f"已连接状态文字对比度 {hit[0][0]:.2f} < {AA_SMALL}"
    print(f"6) 「Clash 已连接」那行 {hit[0][0]:.2f}:1（fg={hit[0][5]}）PASS")

    # ---- 7) 留证据图（不 show 窗口，只抓位图）----
    for page, nm in ((pages[0][1], "dash"), (pages[2][1], "settings")):
        win.switchTo(page)
        _settle()
        out = Path(__file__).parent / f"render_v171_{nm}.png"
        assert page.grab().save(str(out)), out
        print(f"7) {out.name} {out.stat().st_size} bytes")
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

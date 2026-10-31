# -*- coding: utf-8 -*-
"""v1.6.3 R17 验收：提醒条折行不许把路径/URL 从中间劈开。

R13 让 InfoBar 按真实像素折行，但 `_wrap_px` 是逐字符试的——中文那样是对的
（没有空格可找），代价是 ASCII 被殃及：实测 420px 可用宽下
  C:\\Users\\y\\Downloads\\traffic_detail_2026-10-30.csv  (317px)
折成 "...\\Download" + "s\\traffic_detail_2026-10-30.csv"，用户照着抄就少个 s；
  https://github.com/ygy2200/traffic-board/releases/tag/v1.6.3  (377px)
折成 ".../tag/v1.6." + "3 里面…"，版本号被劈成两半。
这一轮钉：① ASCII 词是断行整体，放不下才在分隔符后断；② 数字之间的点不算分隔符；
③ 断不开的东西（60 位没有分隔符的长串）仍要硬拆，不许把行撑爆——那是 R13 的契约；
④ 中文照旧逐字折，宽度契约不许退化。

为什么这个套件也不用 offscreen（和 R16 同一个理由的另一面）：
离屏平台没有 Segoe UI，ASCII 字符宽度量出来是假机的一半多——同一条 URL
实测 840px（离屏）vs 377px（windows 平台真字体）。用假宽度选用例，
「窗口里明明放得下却被断了」这条最关键的断言就永远测不到。

全程不 show()：windows 平台下 show() 会在用户桌面上真弹一个窗口。
`_toast` 的前台分支只看 `isVisible()`，所以把那个判断临时改成真，
让生产调用路径（`_toast` → `_ToastBar(...).show()`）照常走一遍——
父窗口仍是隐藏的，`.show()` 落在隐藏父窗口上不会真的映射窗口。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # 见文件头：离屏的字符宽度是假的

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))

PX = 420                                   # 单元级折行宽度，和真提醒条同量级
PATH = "C:\\Users\\y\\Downloads\\traffic_detail_2026-10-30.csv"
URL = "https://github.com/ygy2200/traffic-board/releases/tag/v1.6.3"
HEX60 = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
SOFT = "/\\-_.:;,?&%="
OVERHEAD = 154                             # 图标 + 内外边距 + 关闭按钮那一圈


def _settle(app, ms=250):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _ascii(ch: str) -> bool:
    """可打印 ASCII（不含空格）：路径、URL、文件名、版本号都落在这一段。"""
    return "!" <= ch <= "~"


def _broken(out):
    """找「从词中间劈开」的断行。

    一行结尾是 ASCII、下一行开头也是 ASCII，说明一个词被拆到了两行——
    只有紧跟分隔符（traffic_ / tag/）才是允许的断点；数字之间的点不算
    （v1.6. | 3 比不断还难读）。
    """
    lines = [ln for ln in out.split("\n") if ln]
    bad = []
    for a, b in zip(lines, lines[1:]):
        if not _ascii(a[-1]) or not _ascii(b[0]):
            continue
        if a[-1] not in SOFT:
            bad.append(("非分隔符处断词", a[-12:], b[:12]))
        elif a[-1] == "." and len(a) >= 2 and a[-2].isdigit() and b[0].isdigit():
            bad.append(("数字之间的点当断点", a[-12:], b[:12]))
    return bad


def _kept(text, out):
    """折行只许插换行、删空格，别的字符一个不许动。"""
    return text.replace(" ", "").replace("\n", "") == out.replace(" ", "").replace("\n", "")


def _over(out, fm, px):
    return [(fm.horizontalAdvance(ln), ln) for ln in out.split("\n")
            if fm.horizontalAdvance(ln) > px]


def _bar_with(win, needle):
    """按正文找那条真提醒条——findChildren 的顺序不保证是弹出顺序。"""
    from ui.main_window import _ToastBar
    for b in win.findChildren(_ToastBar):
        if needle in b.contentLabel.text().replace("\n", ""):
            return b
    raise AssertionError(f"没找到正文含「{needle}」的提醒条")


def _child() -> int:
    use_temp_data_dir("v172")
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow, _ToastBar, _wrap_px

    app = QApplication.instance() or QApplication([])
    agg = Aggregator()
    agg.start = lambda: None
    win = MainWindow(agg, start_worker=False)
    win.alert_enabled = False
    win.resize(456, 510)
    _settle(app)
    # _toast 前台分支的门槛是 isVisible()，隐藏窗口下会退回托盘气泡。
    # 只改这个判断，弹提醒仍然走生产的 _ToastBar(...) 构造路径。
    win.isVisible = lambda: True           # type: ignore[assignment]
    win.isMinimized = lambda: False        # type: ignore[assignment]

    win._toast("info", "取字体", "占位")
    _settle(app, 200)
    seed = win.findChildren(_ToastBar)
    assert seed, "建不出提醒条，量不到真实字体"
    fm = QFontMetrics(seed[-1].contentLabel.font())
    w_path, w_url = fm.horizontalAdvance(PATH), fm.horizontalAdvance(URL)
    assert w_path > 0 and w_url > 0, "字体度量没宽度，测试无意义"
    print(f"0) 用真提醒条 contentLabel 的字体量宽度："
          f"中文 {fm.horizontalAdvance('流量看板')}px/4字，"
          f"路径 {w_path}px，URL {w_url}px")
    # 用例前提：这两个串在 PX=420 下必须「放得下」，否则第 2 步的整块断言就失去意义。
    # 字体/DPI 变了导致前提不成立时，宁可红着报出来，别让它悄悄退化成只查断词。
    assert w_path <= PX and w_url <= PX, (
        f"用例前提不成立：路径 {w_path}px / URL {w_url}px 已超出 PX={PX}，"
        f"第 2 步的「整块在一行」不再是允许的折法")

    # ---- 1) 放得下就不许动 ----
    short = "已导出 1,284 行"
    assert _wrap_px(fm, short, PX) == short, _wrap_px(fm, short, PX)
    assert not _over(short, fm, PX)
    print(f"1) 放得下的文本一字未改 PASS（{fm.horizontalAdvance(short)}px ≤ {PX}px）")

    # ---- 2) 中英混排：放得下的词必须整块在一行，且没有行被撑爆 ----
    cases = {
        "路径+中文": f"已导出 1,284 行到 {PATH} 可以直接用 Excel 打开",
        "URL+中文": f"看板说明见 {URL} 里面有完整变更记录",
        "两段混合": f"第一行 {PATH}\n第二行 {URL}",
    }
    for name, text in cases.items():
        out = _wrap_px(fm, text, PX)
        lines = out.split("\n")
        bad = _broken(out)
        assert not bad, f"{name} 有词被从中间劈开：{bad}\n折出来是：\n{out}"
        assert _kept(text, out), f"{name} 折行把内容改了：\n{out}"
        over = _over(out, fm, PX)
        assert not over, f"{name} 有行超出可用宽度：{over}"
        for token, label in ((PATH, "路径"), (URL, "URL")):
            if token in text:
                assert any(token in ln for ln in lines), \
                    f"{name}：{label} 放得下（{fm.horizontalAdvance(token)}px ≤ {PX}px）却还是断了：\n{out}"
        print(f"2) {name}：路径/URL 整块在一行，最长行 "
              f"{max(fm.horizontalAdvance(l) for l in lines)}px ≤ {PX}px PASS")
        print("   折出来是：\n" + "\n".join(f"     |{l}" for l in lines))

    # ---- 3) 版本号里的点是数字之间的点，不许当断点 ----
    out = _wrap_px(fm, f"看板说明见 {URL} 里面有完整变更记录", 200)
    assert not _broken(out), f"200px 宽下断点位置不对：{_broken(out)}\n{out}"
    assert _kept(f"看板说明见 {URL} 里面有完整变更记录", out), out
    assert not any(ln.endswith("v1.6.") for ln in out.split("\n")), \
        f"版本号被劈成 v1.6. + 3：\n{out}"
    print("3) 挤到 200px 宽必须断行时，v1.6.3 仍在一行里（数字之间的点不断行）PASS")
    print("   折出来是：\n" + "\n".join(f"     |{l}" for l in out.split("\n")))

    # ---- 4) 断不开的东西必须硬拆，不许撑爆行（R13 的宽度契约）----
    out = _wrap_px(fm, f"哈希 {HEX60} 已写入", PX)
    assert not _over(out, fm, PX), f"超长无分隔符串撑爆了行：{_over(out, fm, PX)}"
    assert HEX60 in out.replace("\n", ""), "硬拆把内容弄丢/弄乱了"
    print(f"4) 60 位无分隔符长串被硬拆成 {out.count(chr(10)) + 1} 行，"
          f"没有一行超过 {PX}px，内容一字未丢 PASS")

    # ---- 5) 中文照旧逐字折，一字不改 ----
    zh = "如果这是你自己装的软件可以放心忽略，陌生连接提醒只是帮你发现后台偷跑流量的程序。"
    out = _wrap_px(fm, zh, PX)
    assert "\n" in out, "纯中文没折行"
    assert not _over(out, fm, PX), _over(out, fm, PX)
    assert out.replace("\n", "") == zh, "折行把中文内容改了"
    print(f"5) 纯中文逐字折成 {len(out.split(chr(10)))} 行，字数一字未变 PASS")

    # ---- 6) 真提醒条 @最小窗口宽：条子不超窗，路径只断在分隔符后 ----
    # 456 只是请求值，布局把窗口最小宽顶到 500；提醒条可用宽再扣掉图标/边距/关闭按钮
    # 那一圈，最后一轮贴合时已经比路径本身（317px）还窄——物理上放不进一行。
    # 所以这里的契约不是「整块」，而是「只准断在分隔符后」：R13 的逐字折行把它断在
    # Download|s，用户照抄就少一个 s，那是真丢内容。
    text6 = f"已导出 1,284 行到 {PATH} 可以直接用 Excel 打开"
    win._toast("success", "明细已导出", text6)
    _settle(app, 300)
    bar = _bar_with(win, "已导出")
    assert bar.width() <= win.width(), f"提醒条 {bar.width()} 比窗口 {win.width()} 还宽"
    shown = bar.contentLabel.text()
    assert not _broken(shown), f"最小宽真提醒条把词从中间劈开：{_broken(shown)}\n{shown}"
    assert _kept(text6, shown), f"折行把内容改了：\n{shown}"
    assert not any(ln.endswith("Download") for ln in shown.split("\n")), \
        f"又断回 Download|s 了：\n{shown}"
    print(f"6) 最小宽真提醒条：条宽 {bar.width()} ≤ 窗口 {win.width()}，"
          f"路径只断在分隔符后 PASS")
    print("   折出来是：\n" + "\n".join(f"     |{l}" for l in shown.split("\n")))

    # ---- 7) 真提醒条 @683（Win11 半屏贴靠）：URL 放得下就必须整块 ----
    avail7 = min(600, 683 - 24 - OVERHEAD)
    text7 = f"看板说明见 {URL} 里面有完整变更记录"
    win.resize(683, 700)
    _settle(app)
    win._toast("info", "版本说明", text7)
    _settle(app, 300)
    bar7 = _bar_with(win, "看板说明见")
    assert bar7.width() <= win.width(), f"{bar7.width()} > {win.width()}"
    shown7 = bar7.contentLabel.text()
    assert w_url <= avail7, (
        f"用例前提不成立：683 宽可用 {avail7}px，URL 却要 {w_url}px")
    assert any(URL in ln for ln in shown7.split("\n")), \
        f"683 宽放得下 URL（{w_url}px ≤ {avail7}px）却还是断了：\n{shown7}"
    assert _kept(text7, shown7) and not _broken(shown7), f"内容有改动或劈词：\n{shown7}"
    print(f"7) 683 宽（半屏贴靠）真提醒条：条宽 {bar7.width()} ≤ 窗口 {win.width()}，"
          f"URL 整块在一行内 PASS")
    print("   折出来是：\n" + "\n".join(f"     |{l}" for l in shown7.split("\n")))

    # ---- 8) 窗口从小改到大，活着的提醒重新贴合后词还是完整的 ----
    win.resize(456, 510)
    _settle(app, 300)
    win.resize(1000, 700)
    _settle(app, 400)
    bar8 = _bar_with(win, "看板说明见")
    shown8 = bar8.contentLabel.text()
    assert bar8.width() <= win.width(), f"{bar8.width()} > {win.width()}"
    assert _kept(text7, shown8), f"重新贴合把内容改了：\n{shown8}"
    assert not _broken(shown8), f"重新贴合后有词被劈开：{_broken(shown8)}\n{shown8}"
    print(f"8) 456→1000 重新贴合：条宽 {bar8.width()}，无劈词 PASS")
    print("   折出来是：\n" + "\n".join(f"     |{l}" for l in shown8.split("\n")))

    png = Path(__file__).parent / "render_v172_infobar_words.png"
    if bar8.grab().save(str(png)):
        print(f"9) 留图 {png.name}（{png.stat().st_size} bytes，隐藏窗口下 grab）")
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

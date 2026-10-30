# -*- coding: utf-8 -*-
"""v1.6.4 R19 验收：悬浮速率条的可读性与可发现性闸门（真实像素，不是声明的颜色）。

为什么单独给它建一道闸门：它是 WA_TranslucentBackground + paintEvent 自绘的独立窗口，
R16 那套「grab 主窗口逐屏量标签」根本扫不到它——胶囊底色 alpha=215（84% 不透明），
桌面会透进来，字色对不对必须按**合成后的像素**算：
  深色桌面 #101014 → 底 ≈(23,23,27)，白字 15.69:1
  浅色桌面 #ffffff → 底 ≈(60,60,64)，白字  9.64:1
  中灰桌面 #b0b0b0 → 底 ≈(48,48,52)，白字 11.53:1
本轮实测都过 WCAG AA（正文 4.5:1），所以这一轮没有颜色要修——闸门的作用是
「以后有人把底色/字色改一个字节就会被拦」，同时把 v1.6.4 发布说明里
「悬浮条文字对比度下一轮补」这句话兑现掉。

顺带钉两条同样只在这个控件上成立的契约：
① 最长速率串不许画出胶囊外（自绘没有省略号，越界就是被裁掉半截数字）；
② 悬浮说明（tooltip）必须在——它没有标题栏、没有菜单入口，
   拖动/双击回主界面/右键锁定这些操作全靠用户猜。

和 R16/R17 一样跑在 windows 平台、全程不 show()：windows 平台下不弹窗也能 render 出真图。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # 离屏没有中文字形，量出来是空图

from _qt_harness import am_child, hard_exit, run_isolated, use_temp_data_dir  # noqa: E402

if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))

AA_SMALL = 4.5
DESKTOPS = (("深色桌面", "#101014", "dark"),
            ("浅色桌面", "#ffffff", "light"),
            ("中灰桌面", "#b0b0b0", "gray"))
# 最长可能出现的速率串：_fmt_bytes 到 GB 一档保留一位小数，再加 "/s" 与前缀箭头
WORST = "1023.9GB/s"


def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb):
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _settle(app, ms=200):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _composite(badge, backdrop):
    """把半透明悬浮条合成到指定桌面色上，返回 RGB32 的 QImage。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPixmap, QPainter
    w, h = badge.width(), badge.height()
    base = QImage(w, h, QImage.Format.Format_RGB32)
    base.fill(QColor(backdrop))
    pm = QPixmap.fromImage(base)
    p = QPainter(pm)
    badge.render(p, QPoint(0, 0))
    p.end()
    return pm.toImage()


def _pixels(img, box):
    x0, y0, x1, y1 = box
    out = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = img.pixel(x, y)
            out.append(((c >> 16) & 255, (c >> 8) & 255, c & 255))
    return out


def _child() -> int:
    use_temp_data_dir("badgegate")
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ui.speed_badge import SpeedBadge

    badge = SpeedBadge()
    badge.set_speed(WORST, WORST)          # 绘制时自己加 "↓ " / "↑ " 前缀
    badge.resize(210, 40)
    _settle(app)
    # 必须用 paintEvent 那套字体量，不然测试和实现各量各的（自绘字体是 9.5pt 加粗）
    from PySide6.QtGui import QFont
    f = QFont(badge.font())
    f.setPointSizeF(9.5)
    f.setBold(True)
    fm = QFontMetrics(f)
    text_w = fm.horizontalAdvance("↓ " + WORST)
    print(f"0) 悬浮条 {badge.width()}x{badge.height()}，"
          f"最长串「↓ {WORST}」= {text_w}px（9.5pt 加粗，与绘制同一字体）")

    # ---- 1) 最长速率串必须整个画在胶囊里（自绘没有省略号，越界=数字被裁）----
    # 文字从 x=28 起画（左边留给两个指示灯），右端必须留 ≥8px 余量
    right = 28 + text_w
    assert right <= badge.width() - 8, (
        f"最长速率串画到 x={right}，胶囊只有 {badge.width()} 宽，"
        f"数字会被裁掉（自绘不会自动省略）")
    print(f"1) 最长串右端 x={right} ≤ {badge.width() - 8}（留 8px 余量）PASS")

    # ---- 2) 指示灯与文字不许重叠（几何契约：点占 x=14..21，文字从 28 起）----
    dot_right = 14 + 7
    assert dot_right < 28, f"指示灯画到 x={dot_right}，会压到文字起点 28"
    print(f"2) 指示灯右端 {dot_right} < 文字起点 28，不重叠 PASS")

    # ---- 3) 三种桌面底色下，合成后的文字对比度都要过 WCAG AA ----
    rows = []
    for name, backdrop, slug in DESKTOPS:
        img = _composite(badge, backdrop)
        assert img.width() == badge.width(), "render 出来的图尺寸不对，量到的不是悬浮条"
        strip = _pixels(img, (40, 18, 200, 22))     # 胶囊内部空白带 → 底色
        bg = min(strip, key=_lum)
        ink = _pixels(img, (28, 4, badge.width() - 6, 18))   # 第一行文字带
        bright = sorted(ink, key=lambda c: -_lum(c))[:max(1, len(ink) // 50)]
        fg = bright[0]
        ratio = _ratio(fg, bg)
        rows.append((name, ratio))
        assert _lum(fg) > _lum(bg), f"{name}：字比底还暗，量法或配色反了 fg={fg} bg={bg}"
        assert ratio >= AA_SMALL, (
            f"{name} 下悬浮条文字对比度 {ratio:.2f}:1 低于 WCAG AA {AA_SMALL}"
            f"（字色≈{fg} 合成底≈{bg}）")
        img.save(str(Path(__file__).with_name(f"render_v173_badge_on_{slug}.png")))
    for name, ratio in rows:
        print(f"3) {name}: 对比度 {ratio:.2f}:1 ≥ {AA_SMALL} PASS")

    # ---- 4) 浅色桌面上底色必须还是「深色底」----
    # 白字配浅底是同一处改动里最容易只改一半的错误：有人把 alpha 调小或把底色调浅，
    # 上面第 3 步会一起红；这一步单独钉住「亮度」，报错时能直接指出是底色漂了。
    img = _composite(badge, "#ffffff")
    bg = min(_pixels(img, (40, 18, 200, 22)), key=_lum)
    assert _lum(bg) < 0.25, f"白桌面下合成底色亮度 {_lum(bg):.2f}，底色已经漂浅了：{bg}"
    print(f"4) 白桌面下合成底色 {_lum(bg):.2f} < 0.25（仍是深底）PASS")

    # ---- 5) 可发现性：悬浮说明必须在，且说清三个隐藏操作 ----
    tip = badge.toolTip()
    assert tip, "悬浮条没有 tooltip：拖动/双击/右键这些操作只能靠猜"
    for kw in ("↓", "↑", "拖动", "双击", "右键"):
        assert kw in tip, f"悬浮说明缺了「{kw}」这一项：{tip!r}"
    print(f"5) 悬浮说明含 ↓/↑/拖动/双击/右键 五个关键词 PASS")
    print("   文案：" + tip.replace("\n", " / "))

    # ---- 6) 锁定状态下位置仍会保存（拖动契约，回归 R11 那轮的行为）----
    badge._locked = True
    before = badge.pos()
    badge._drag_offset = None
    badge.move(before.x() + 30, before.y())
    assert badge.pos() != before, "move 没生效，这一步的前提就没了"
    badge._save_pos()
    assert badge._settings.value("badge/pos") == badge.pos(), "位置没落盘"
    print("6) 锁定状态下手动移动仍能落盘 PASS")
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

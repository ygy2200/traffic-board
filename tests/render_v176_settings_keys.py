# -*- coding: utf-8 -*-
"""R26 验收：设置页的开关必须能用键盘走完「找到 → 拨动 → 离开」。

改之前实测（tmp/probe_switch_keys.py / tmp/probe_settings_keys.py）：
  · 六个 SwitchButton 全是 focusPolicy=0，Tab 链里根本没有它们；
  · 硬 setFocus 进去后按空格，状态不变（库没写 keyPressEvent）；
  · 从「上限」数字框按 Tab，先在 SpinBox/SpinButton 里绕 6 跳，再撞上
    导航页签和滚动区——页内滚动的箭头按钮也占停靠位；
  · 带文字的开关一拨，标签就变成 On/Off（描述文字消失）；
  · 页内没有 Esc 出口，只能再按十几次 Tab 去摸页签。

本闸门要钉的就是这五件事的修复结果：顺序、可按、可读、不抢焦点、能回首页。
用 windows 平台跑：焦点与像素都要真窗口，全程不 show 的离屏窗口上量焦点是假的
（R20 的教训）。窗口会闪一下，由 _qt_harness 隔离注册表。
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

SWITCHES = ("sw_autostart", "sw_badge", "sw_topmost",
            "sw_close_exit", "sw_alert", "sw_legacy")


def _child() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    use_temp_data_dir("v176")
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow

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
    page = win.page_settings
    sws = [getattr(page, n) for n in SWITCHES]

    # ---- 1. 六个开关都能被 Tab 认领（policy 不再是 0）----
    for name, sw in zip(SWITCHES, sws):
        assert int(sw.focusPolicy()) == int(Qt.FocusPolicy.StrongFocus), \
            f"{name} 还是摸不到（policy={int(sw.focusPolicy())}）"
    print("1) 六个开关 focusPolicy=StrongFocus PASS")

    # ---- 2. 滚动区与它的滚动条/箭头不占停靠位 ----
    from PySide6.QtWidgets import QAbstractScrollArea
    for w in page.scroll.findChildren(QAbstractScrollArea):
        assert int(w.focusPolicy()) == int(Qt.FocusPolicy.NoFocus), \
            f"滚动区 {type(w).__name__} 还在抢焦点"
    for sb in (page.scroll.verticalScrollBar(), page.scroll.horizontalScrollBar()):
        assert int(sb.focusPolicy()) == int(Qt.FocusPolicy.NoFocus), "滚动条还在 Tab 链上"
        for child in sb.findChildren(QWidget):
            assert int(child.focusPolicy()) == int(Qt.FocusPolicy.NoFocus), \
                f"滚动条子件 {type(child).__name__} 还在 Tab 链上"
    print("2) 滚动区/滚动条/箭头全部 NoFocus PASS")

    # ---- 3. 切到设置页后，Tab 按卡片阅读顺序走过六个开关 ----
    win.switchTo(page)
    settle(600)
    assert win.stackedWidget.currentWidget() is page, "没切到设置页"
    sws[0].setFocus()
    settle(150)
    assert app.focusWidget() is sws[0], "第一个开关接不住焦点"
    walked = []
    for _ in range(5):
        QTest.keyClick(app.focusWidget(), Qt.Key_Tab)
        settle(100)
        walked.append(app.focusWidget())
    assert walked == sws[1:], \
        "Tab 顺序不对：" + " → ".join(type(w).__name__ if w else "None" for w in walked)
    QTest.keyClick(app.focusWidget(), Qt.Key_Tab)
    settle(120)
    nxt = app.focusWidget()
    assert nxt is page.sp_cap or page.sp_cap.isAncestorOf(nxt), \
        f"最后一个开关之后没进数字框，而是 {type(nxt).__name__ if nxt else None}"
    print("3) Tab 顺序：" + " → ".join(SWITCHES) + " → 月流量上限 PASS")

    # ---- 4. 空格/回车都能拨动，且 checkedChanged 真的发 ----
    sw = page.sw_alert
    fired = []
    sw.checkedChanged.connect(fired.append)
    before = sw.isChecked()
    QTest.keyClick(sw, Qt.Key_Space)
    settle(150)
    assert sw.isChecked() != before, "空格没拨动开关"
    assert fired and fired[-1] == sw.isChecked(), "空格拨了状态却没发 checkedChanged"
    QTest.keyClick(sw, Qt.Key_Return)
    settle(150)
    assert sw.isChecked() == before, "回车没拨回去"
    assert len(fired) == 2, f"两次按键只发了 {len(fired)} 次信号"
    assert win.alert_enabled == before, "开关状态没有同步进窗口的提醒开关"
    print(f"4) 空格/回车各拨一次（{len(fired)} 次信号），alert_enabled 同步 PASS")

    # ---- 5. 带文字的开关拨动后描述不能变成 On/Off ----
    label_sw = page.sw_close_exit
    want = "直接退出程序（不留后台）"
    assert label_sw.getText() == want, f"初始标签就不对：{label_sw.getText()!r}"
    QTest.keyClick(label_sw, Qt.Key_Space)
    settle(150)
    assert label_sw.getText() == want, f"拨动后标签变成了 {label_sw.getText()!r}"
    QTest.keyClick(label_sw, Qt.Key_Space)
    settle(150)
    assert label_sw.getText() == want, "拨回来标签又变了"
    print(f"5) 带文字开关拨动后仍是「{want}」PASS")

    # ---- 6. 聚焦要看得见：开关自绘，没描边就是盲操作 ----
    page.sw_badge.setFocus()
    settle(200)
    unfocused = page.sw_alert.grab()
    page.sw_alert.setFocus()
    settle(250)
    focused = page.sw_alert.grab()
    assert focused.width() > 0 and focused.size() == unfocused.size(), "抓不到开关的图"
    u, f = unfocused.toImage(), focused.toImage()
    ring = page.sw_alert.indicator.geometry().united(
        page.sw_alert.label.geometry()).adjusted(-3, -2, 3, 2)
    dpr = f.devicePixelRatio()          # grab() 给的是设备像素，geometries 是逻辑像素
    l, t = ring.left() * dpr, ring.top() * dpr
    r, b = (ring.left() + ring.width()) * dpr, (ring.top() + ring.height()) * dpr
    diff = near = outside = total = 0
    for yy in range(f.height()):
        for xx in range(f.width()):
            a, bb = f.pixelColor(xx, yy), u.pixelColor(xx, yy)
            total += 1
            if (abs(a.red() - bb.red()) + abs(a.green() - bb.green())
                    + abs(a.blue() - bb.blue())) > 24:
                diff += 1
                if not (l - 3 <= xx <= r + 3 and t - 3 <= yy <= b + 3):
                    outside += 1
                if min(abs(xx - l), abs(xx - r), abs(yy - t), abs(yy - b)) <= 4:
                    near += 1
    assert diff >= 12, f"聚焦后开关没有可见变化（{diff}/{total}）"
    assert outside * 20 <= diff, f"变化跑到开关内容之外了（{outside}/{diff}）"
    assert near * 2 >= diff, f"变化不在描边上（{near}/{diff}），焦点框画歪了"
    print(f"6) 聚焦描边可见（{diff}/{total} 像素变化，贴边 {near}，越界 {outside}）PASS")

    # ---- 7. Esc 回首页；Ctrl+Tab 仍能换页 ----
    idx = win.stackedWidget.currentIndex()
    QTest.keyClick(app.focusWidget(), Qt.Key_Escape)
    settle(500)
    assert win.stackedWidget.currentIndex() == 0, \
        f"Esc 没回首页（{idx} -> {win.stackedWidget.currentIndex()}）"
    assert win.stackedWidget.currentWidget() is not page, "Esc 之后还停在设置页"

    win.switchTo(page)
    settle(500)
    page.sw_alert.setFocus()
    settle(150)
    QTest.keyClick(page.sw_alert, Qt.Key_Tab, Qt.ControlModifier)
    settle(500)
    assert win.stackedWidget.currentIndex() != 2, "Ctrl+Tab 在设置页失效了"
    QTest.keyClick(app.focusWidget(), Qt.Key_Tab,
                   Qt.ControlModifier | Qt.ShiftModifier)
    settle(500)
    assert win.stackedWidget.currentWidget() is page, "Ctrl+Shift+Tab 没切回设置页"
    print("7) Esc 回首页、Ctrl+Tab/Ctrl+Shift+Tab 仍可换页 PASS")

    # ---- 8. 反向对照：把开关改回 NoFocus，Tab 顺序断言必须叫 ----
    page.sw_topmost.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    page.sw_badge.setFocus()
    settle(150)
    QTest.keyClick(app.focusWidget(), Qt.Key_Tab)
    settle(150)
    assert app.focusWidget() is not page.sw_topmost, "改回 NoFocus 后 Tab 仍然停下了，第 3 步是空判"
    page.sw_topmost.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    page.sw_badge.setFocus()
    settle(150)
    QTest.keyClick(app.focusWidget(), Qt.Key_Tab)
    settle(150)
    assert app.focusWidget() is page.sw_topmost, "还原 StrongFocus 后 Tab 又不到它了"
    print("8) 变异对照：NoFocus 时跳过、还原后停下 PASS")

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

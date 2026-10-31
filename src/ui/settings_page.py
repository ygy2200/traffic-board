# -*- coding: utf-8 -*-
"""设置页：开机自启、陌生连接提醒、悬浮速率条（显示/固定图层）、关闭行为、关于。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractScrollArea, QGridLayout, QHBoxLayout, QFrame, QVBoxLayout, QWidget,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, ScrollArea, SpinBox, SwitchButton,
    SubtitleLabel,
)

from core.tools import get_autostart, set_autostart
from core.version import VERSION


def _exe_path() -> str:
    return sys.executable if getattr(sys, "frozen", False) else ""


class KeySwitch(SwitchButton):
    """能 Tab 到、能空格/回车拨、聚焦看得见的开关。

    qfluentwidgets 的 SwitchButton 默认 NoFocus（实测 focusPolicy=0），键盘用户
    既到不了，硬 setFocus 后按空格也不改状态；它又是自绘控件，没有焦点描边分支。
    另外带文字的开关一拨就把标签换成 On/Off（库里 onText 默认 'On'），
    描述文字直接消失——这里把 on/off 文字钉成同一句。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        txt = self.getText()
        if txt not in (self.tr("Off"), self.tr("On")):
            self.setOnText(txt)
            self.setOffText(txt)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggleChecked()
            e.accept()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if not self.hasFocus():
            return
        # 开关控件被布局拉满整行（实测 871x22），描边只圈住指示器+文字，
        # 否则键盘焦点看起来像选中了整张卡
        r = self.indicator.geometry().united(self.label.geometry()).adjusted(-3, -2, 3, 2)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(120, 190, 255, 235), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 5, 5)


class _FlowFields(QWidget):
    """一排「标签 + 控件」：放得下就保持一行，放不下自动折成多行。

    判断依据取滚动区视口宽度，而不是本控件自己的宽度——横向滚动条一旦冒出来，
    布局会把这个控件撑到它自己要求的最小宽度，于是「放得下」永远成立，
    滚动条再也收不掉。实测就是这么锁死的：窗口 456 宽时视口只有 407，
    而「月流量上限」那张卡坚持要 550，横向多出 167px。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._g = QGridLayout(self)
        self._g.setContentsMargins(0, 0, 0, 0)
        self._g.setHorizontalSpacing(12)
        self._g.setVerticalSpacing(8)
        self._cells: list = []
        self._stacked = False

    def addField(self, labelText: str, widget: QWidget) -> None:
        cell = QWidget(self)
        label = BodyLabel(labelText, cell)
        h = QHBoxLayout(cell)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(label)
        h.addWidget(widget)
        # 富余量给尾部弹簧：否则多出来的宽度会被标签吃掉，
        # 折行时两行的输入框起点就错开了（实测 66 vs 136）
        h.addStretch(1)
        self._cells.append((cell, label, widget))
        self._place(self._stacked)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    def _need(self) -> int:
        return (sum(c.minimumSizeHint().width() for c, _, _ in self._cells)
                + self._g.horizontalSpacing() * max(len(self._cells) - 1, 0))

    def _reflow(self) -> None:
        sa = self.parentWidget()
        while sa is not None and not isinstance(sa, QAbstractScrollArea):
            sa = sa.parentWidget()
        inner = sa.widget() if sa is not None else None
        if sa is None or inner is None:
            return                      # 不在滚动区里：没有「视口」可言，保持一行
        left = self.mapTo(inner, QPoint(0, 0)).x()
        avail = sa.viewport().width() - 2 * left
        stacked = avail < self._need()
        if stacked != self._stacked:
            self._place(stacked)

    def _place(self, stacked: bool) -> None:
        self._stacked = stacked
        # 折行时把标签占位对齐，否则两行的输入框会一前一后错开
        lab_w = max((lab.sizeHint().width() for _, lab, _ in self._cells), default=0)
        for i, (cell, label, _) in enumerate(self._cells):
            label.setMinimumWidth(lab_w if stacked else 0)
            if stacked:
                self._g.addWidget(cell, i, 0)
            else:
                self._g.addWidget(cell, 0, i)
        cols = 1 if stacked else len(self._cells)
        for c in range(0, len(self._cells) + 2):
            self._g.setColumnStretch(c, 1 if c == cols else 0)


class SettingsPage(QWidget):
    def __init__(self, agg=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._settings = QSettings("TrafficBoard", "TrafficBoard")
        self._agg = agg
        self._loading = True
        # 整页放进滚动区：七张卡的最小高度原本会一路顶到窗口，
        # 1366x768 的笔记本上窗口最小高度 806 已经大于可用高度，底部直接看不到。
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.enableTransparentBackground()
        inner = QWidget(self.scroll)
        self.scroll.setWidget(inner)
        v.addWidget(self.scroll, 1)

        iv = QVBoxLayout(inner)
        iv.setContentsMargins(24, 20, 24, 20)
        iv.setSpacing(10)
        iv.addWidget(SubtitleLabel("设置"))
        hint = CaptionLabel("键盘：Tab 切换控件 · 空格/回车 拨动开关 · Esc 回首页")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgb(160,160,160);")
        iv.addWidget(hint)

        # ---- 开机自启 ----
        card1 = CardWidget(inner)
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(16, 12, 16, 12)
        c1.addWidget(BodyLabel("开机自动启动"))
        note = CaptionLabel("写入当前用户的启动项（注册表 HKCU Run），关闭即完全移除。")
        note.setWordWrap(True)
        note.setStyleSheet("color: rgb(160,160,160);")
        c1.addWidget(note)
        self.sw_autostart = KeySwitch(card1)
        self.sw_autostart.setChecked(get_autostart(_exe_path()))
        self.sw_autostart.checkedChanged.connect(self._on_autostart)
        c1.addWidget(self.sw_autostart)
        iv.addWidget(card1)

        # ---- 悬浮速率条 ----
        card2 = CardWidget(inner)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 12, 16, 12)
        c2.addWidget(BodyLabel("悬浮速率条"))
        self.sw_badge = KeySwitch("显示悬浮速率条", card2)
        self.sw_badge.setChecked(self._settings.value("badge/enabled", "1") in ("1", "true", True))
        self.sw_badge.checkedChanged.connect(self._on_badge)
        c2.addWidget(self.sw_badge)
        self.sw_topmost = KeySwitch("固定图层（始终置顶）", card2)
        self.sw_topmost.setChecked(self._settings.value("badge/topmost", "1") in ("1", "true", True))
        self.sw_topmost.checkedChanged.connect(self._on_topmost)
        c2.addWidget(self.sw_topmost)
        note2 = CaptionLabel("固定图层让悬浮条始终显示在其他窗口之上，不被遮挡。"
                             "悬浮条可拖动，右键它有锁定位置/显示主界面/退出。")
        note2.setWordWrap(True)
        note2.setStyleSheet("color: rgb(160,160,160);")
        c2.addWidget(note2)
        iv.addWidget(card2)

        # ---- 关闭行为 ----
        card3 = CardWidget(inner)
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(16, 12, 16, 12)
        c3.addWidget(BodyLabel("关闭主窗口时"))
        self.sw_close_exit = KeySwitch("直接退出程序（不留后台）", card3)
        self.sw_close_exit.setChecked(self._settings.value("close/exit", "0") in ("1", "true", True))
        self.sw_close_exit.checkedChanged.connect(self._on_close_exit)
        c3.addWidget(self.sw_close_exit)
        note3 = CaptionLabel("关闭此开关：点 × 会最小化到托盘（后台保持监测），从托盘菜单才能退出。")
        note3.setWordWrap(True)
        note3.setStyleSheet("color: rgb(160,160,160);")
        c3.addWidget(note3)
        iv.addWidget(card3)

        # ---- 陌生连接提醒 ----
        card4 = CardWidget(inner)
        c4 = QVBoxLayout(card4)
        c4.setContentsMargins(16, 12, 16, 12)
        c4.addWidget(BodyLabel("陌生连接提醒"))
        self.sw_alert = KeySwitch(card4)
        self.sw_alert.setChecked(self._settings.value("alert/enabled", "1") in ("1", "true", True))
        self.sw_alert.checkedChanged.connect(self._on_alert)
        c4.addWidget(self.sw_alert)
        note4 = CaptionLabel("某软件第一次连接某个目标时提醒一次（同一软件每小时最多一条，"
                             "窗口在前台时显示在窗口顶部，否则走托盘）。")
        note4.setWordWrap(True)
        note4.setStyleSheet("color: rgb(160,160,160);")
        c4.addWidget(note4)
        iv.addWidget(card4)

        # ---- 数据源 ----
        card_ds = CardWidget(inner)
        cds = QVBoxLayout(card_ds)
        cds.setContentsMargins(16, 12, 16, 12)
        cds.addWidget(BodyLabel("数据源"))
        self.sw_legacy = KeySwitch("强制传统模式（排查用）", card_ds)
        self.sw_legacy.setChecked(
            self._settings.value("forceLegacyMode", False, type=bool))
        self.sw_legacy.checkedChanged.connect(self._on_force_legacy)
        cds.addWidget(self.sw_legacy)
        note_ds = CaptionLabel(
            "默认：TUN 开启时直接使用 Clash 内核的连接数据（更快、UDP 明细更全）。\n"
            "勾选后始终使用系统连接表扫描（旧路径，数据不变但更耗资源）。")
        note_ds.setWordWrap(True)
        note_ds.setStyleSheet("color: rgb(160,160,160);")
        cds.addWidget(note_ds)
        iv.addWidget(card_ds)

        # ---- 月流量上限 ----
        card_q = CardWidget(inner)
        cq = QVBoxLayout(card_q)
        cq.setContentsMargins(16, 12, 16, 12)
        cq.addWidget(BodyLabel("月流量上限"))
        note_q = CaptionLabel(
            "按计费周期统计「经代理的流量」（不含校园网等本地流量）。"
            "用到 80% 时提醒一次，超限再提醒一次；同一周期不重复打扰。0 = 不限。")
        note_q.setStyleSheet("color: rgb(160,160,160);")
        note_q.setWordWrap(True)
        cq.addWidget(note_q)
        row_q = _FlowFields(card_q)
        self.sp_cap = SpinBox(card_q)
        self.sp_cap.setRange(0, 100000)
        self.sp_cap.setSuffix(" GB")
        self.sp_cap.setValue(int(float(self._settings.value("quota/cap_gb", 0.0) or 0.0)))
        self.sp_cap.valueChanged.connect(self._on_quota)
        row_q.addField("上限", self.sp_cap)
        self.sp_reset = SpinBox(card_q)
        self.sp_reset.setRange(1, 28)
        self.sp_reset.setSuffix(" 号")
        self.sp_reset.setValue(int(self._settings.value("quota/reset_day", 1) or 1))
        self.sp_reset.valueChanged.connect(self._on_quota)
        row_q.addField("重置日", self.sp_reset)
        cq.addWidget(row_q)
        iv.addWidget(card_q)

    # ---- 关于 ----
        card5 = CardWidget(inner)
        c5 = QVBoxLayout(card5)
        c5.setContentsMargins(16, 12, 16, 12)
        c5.addWidget(BodyLabel("关于"))
        about = CaptionLabel(f"流量看板 Traffic Board v{VERSION} · 开源：github.com/ygy2200/traffic-board\n"
                             "数据全部在本机采集与显示，不上传任何内容。")
        about.setWordWrap(True)
        about.setStyleSheet("color: rgb(170,170,170);")
        c5.addWidget(about)
        iv.addWidget(card5)
        iv.addStretch(1)
        self._loading = False
        self._init_keyboard()

    # ---- 键盘 ----
    def _init_keyboard(self) -> None:
        """把 Tab 链钉成卡片的阅读顺序，并给滚动区一条「不抢焦点」的规矩。

        实测默认链序：从滚动区按 Tab 会先撞滚动条箭头，再跳导航页签，六个开关
        一次都到不了；页内也没有任何回首页的出口。滚动条本身只是鼠标替代品，
        键盘用户用方向键/PageUp 就能滚，没必要占停靠位。
        """
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for sb in (self.scroll.verticalScrollBar(), self.scroll.horizontalScrollBar()):
            sb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            for child in sb.findChildren(QWidget):
                child.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.apply_tab_order()
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc.activated.connect(self._back_home)

    def apply_tab_order(self) -> None:
        """主窗口把所有页装进 stackedWidget 后要再调一次：
        页被重新 parent 时 Qt 会重排链，只在构造期设一遍不一定留得住。"""
        chain = [self.sw_autostart, self.sw_badge, self.sw_topmost,
                 self.sw_close_exit, self.sw_alert, self.sw_legacy,
                 self.sp_cap, self.sp_reset]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

    def _back_home(self) -> None:
        win = self.window()
        if hasattr(win, "go_home"):
            win.go_home()

    # ---- 回调 ----
    def _on_autostart(self, checked: bool) -> None:
        exe = _exe_path()
        if not exe:
            self.sw_autostart.setChecked(False)
            return
        set_autostart(checked, exe)

    def _on_badge(self, checked: bool) -> None:
        self._settings.setValue("badge/enabled", "1" if checked else "0")
        win = self.window()
        if hasattr(win, "set_badge_visible"):
            win.set_badge_visible(checked)

    def _on_topmost(self, checked: bool) -> None:
        self._settings.setValue("badge/topmost", "1" if checked else "0")
        win = self.window()
        if hasattr(win, "set_badge_topmost"):
            win.set_badge_topmost(checked)

    def _on_close_exit(self, checked: bool) -> None:
        self._settings.setValue("close/exit", "1" if checked else "0")
        win = self.window()
        if hasattr(win, "set_exit_on_close"):
            win.set_exit_on_close(checked)

    def _on_alert(self, checked: bool) -> None:
        self._settings.setValue("alert/enabled", "1" if checked else "0")
        win = self.window()
        if hasattr(win, "alert_enabled"):
            win.alert_enabled = checked

    def _on_force_legacy(self, checked: bool) -> None:
        self._settings.setValue("forceLegacyMode", "1" if checked else "0")
        if self._agg is not None:
            self._agg.set_force_legacy(checked)

    def _on_quota(self) -> None:
        if getattr(self, "_loading", False):
            return
        cap = float(self.sp_cap.value())
        reset = int(self.sp_reset.value())
        self._settings.setValue("quota/cap_gb", cap)
        self._settings.setValue("quota/reset_day", reset)
        win = self.window()
        if hasattr(win, "set_quota"):
            win.set_quota(cap, reset)

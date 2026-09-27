# -*- coding: utf-8 -*-
"""设置页：开机自启、陌生连接提醒、悬浮速率条（显示/固定图层）、关闭行为、关于。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, SwitchButton, SubtitleLabel,
)

from core.tools import get_autostart, set_autostart


def _exe_path() -> str:
    return sys.executable if getattr(sys, "frozen", False) else ""


class SettingsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._settings = QSettings("TrafficBoard", "TrafficBoard")
        self._loading = True
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        v.addWidget(SubtitleLabel("设置"))

        # ---- 开机自启 ----
        card1 = CardWidget(self)
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(16, 12, 16, 12)
        c1.addWidget(BodyLabel("开机自动启动"))
        note = CaptionLabel("写入当前用户的启动项（注册表 HKCU Run），关闭即完全移除。")
        note.setStyleSheet("color: rgb(160,160,160);")
        c1.addWidget(note)
        self.sw_autostart = SwitchButton(card1)
        self.sw_autostart.setChecked(get_autostart(_exe_path()))
        self.sw_autostart.checkedChanged.connect(self._on_autostart)
        c1.addWidget(self.sw_autostart)
        v.addWidget(card1)

        # ---- 悬浮速率条 ----
        card2 = CardWidget(self)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 12, 16, 12)
        c2.addWidget(BodyLabel("悬浮速率条"))
        self.sw_badge = SwitchButton("显示悬浮速率条", card2)
        self.sw_badge.setChecked(self._settings.value("badge/enabled", "1") in ("1", "true", True))
        self.sw_badge.checkedChanged.connect(self._on_badge)
        c2.addWidget(self.sw_badge)
        self.sw_topmost = SwitchButton("固定图层（始终显示在最前，不被窗口遮挡）", card2)
        self.sw_topmost.setChecked(self._settings.value("badge/topmost", "1") in ("1", "true", True))
        self.sw_topmost.checkedChanged.connect(self._on_topmost)
        c2.addWidget(self.sw_topmost)
        note2 = CaptionLabel("悬浮条可拖动，右键它有锁定位置/显示主界面/退出。")
        note2.setStyleSheet("color: rgb(160,160,160);")
        c2.addWidget(note2)
        v.addWidget(card2)

        # ---- 关闭行为 ----
        card3 = CardWidget(self)
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(16, 12, 16, 12)
        c3.addWidget(BodyLabel("关闭主窗口时"))
        self.sw_close_exit = SwitchButton("直接退出程序（不留后台）", card3)
        self.sw_close_exit.setChecked(self._settings.value("close/exit", "0") in ("1", "true", True))
        self.sw_close_exit.checkedChanged.connect(self._on_close_exit)
        c3.addWidget(self.sw_close_exit)
        note3 = CaptionLabel("关闭此开关：点 × 会最小化到托盘（后台保持监测），从托盘菜单才能退出。")
        note3.setStyleSheet("color: rgb(160,160,160);")
        c3.addWidget(note3)
        v.addWidget(card3)

        # ---- 陌生连接提醒 ----
        card4 = CardWidget(self)
        c4 = QVBoxLayout(card4)
        c4.setContentsMargins(16, 12, 16, 12)
        c4.addWidget(BodyLabel("陌生连接提醒"))
        self.sw_alert = SwitchButton(card4)
        self.sw_alert.setChecked(self._settings.value("alert/enabled", "1") in ("1", "true", True))
        self.sw_alert.checkedChanged.connect(self._on_alert)
        c4.addWidget(self.sw_alert)
        note4 = CaptionLabel("某软件第一次连接某个目标时弹托盘通知（同一软件每小时最多提醒一次）。")
        note4.setStyleSheet("color: rgb(160,160,160);")
        c4.addWidget(note4)
        v.addWidget(card4)

        # ---- 关于 ----
        card5 = CardWidget(self)
        c5 = QVBoxLayout(card5)
        c5.setContentsMargins(16, 12, 16, 12)
        c5.addWidget(BodyLabel("关于"))
        about = CaptionLabel("流量看板 Traffic Board v1.5 · 开源：github.com/ygy2200/traffic-board\n"
                             "数据全部在本机采集与显示，不上传任何内容。")
        about.setStyleSheet("color: rgb(170,170,170);")
        c5.addWidget(about)
        v.addWidget(card5)
        v.addStretch(1)
        self._loading = False

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

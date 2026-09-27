# -*- coding: utf-8 -*-
"""设置页：开机自启开关、陌生连接提醒开关、关于。"""
from __future__ import annotations

import sys

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

        # ---- 陌生连接提醒 ----
        card2 = CardWidget(self)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 12, 16, 12)
        c2.addWidget(BodyLabel("陌生连接提醒"))
        note2 = CaptionLabel("某软件第一次连接某个目标时弹托盘通知（同一软件每小时最多提醒一次）。")
        note2.setStyleSheet("color: rgb(160,160,160);")
        c2.addWidget(note2)
        self.sw_alert = SwitchButton(card2)
        self.sw_alert.setChecked(True)
        self.sw_alert.checkedChanged.connect(self._on_alert)
        c2.addWidget(self.sw_alert)
        v.addWidget(card2)

        # ---- 关于 ----
        card3 = CardWidget(self)
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(16, 12, 16, 12)
        c3.addWidget(BodyLabel("关于"))
        about = CaptionLabel("流量看板 Traffic Board v1.5 · 开源：github.com/ygy2200/traffic-board\n"
                             "数据全部在本机采集与显示，不上传任何内容。")
        about.setStyleSheet("color: rgb(170,170,170);")
        c3.addWidget(about)
        v.addWidget(card3)
        v.addStretch(1)

    def _on_autostart(self, checked: bool) -> None:
        exe = _exe_path()
        if not exe:
            self.sw_autostart.setChecked(False)
            return
        set_autostart(checked, exe)

    def _on_alert(self, checked: bool) -> None:
        # 批次 D：陌生连接提醒开关（MainWindow 读取此状态）
        MainWindowRef = self.window()
        if hasattr(MainWindowRef, "alert_enabled"):
            MainWindowRef.alert_enabled = checked

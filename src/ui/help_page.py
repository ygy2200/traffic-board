# -*- coding: utf-8 -*-
"""使用帮助页：名词解释 + 使用教程（全中文通俗）。"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget, QFrame

from qfluentwidgets import BodyLabel, CaptionLabel, ScrollArea, SubtitleLabel, StrongBodyLabel


_SECTIONS: list[tuple[str, list[str]]] = [
    ("这个软件是干什么的", [
        "流量看板实时显示：电脑上每个软件正在访问哪里、走了代理还是直连、用了多少流量。",
    ]),
    ("界面怎么读（三步上手）", [
        "① 顶部波形图：绿色是下载、蓝色是上传，山越高速度越快。这是全机真实流量（直连 + 代理都算在内）。",
        "② 左侧软件排行：一个软件一行，条越长占用越多。点击任意一行，右侧立刻显示它的连接明细。",
        "③ 右侧连接明细：每行是一条连接，看「正在访问」列就知道它连去了哪，「渠道」列告诉你走的是代理还是直连。",
    ]),
    ("名词解释", [
        "代理：流量先经过代理服务器（如你的 Clash 节点）再出去，常用于访问国外网站。",
        "直连：不走代理，直接从你的网络出去，国内网站和游戏一般是直连。",
        "本地/内网：只在你电脑内部或家庭/校园网内部的通信，不出外网，通常不用管。",
        "节点：代理服务的出口，名字由你的机场/服务商起，比如「香港01」。",
        "Clash：一类代理工具的内核（FlClash、Clash Verge 等都属于）。检测到它在线时，看板会额外显示节点明细和代理流量。",
        "↓ 下载 / ↑ 上传：↓ 是从网络收到的数据，↑ 是发出去的数据。B、KB、MB 是数据大小，/s 表示每秒。",
    ]),
    ("常见问题", [
        "为什么有的软件没有流量数字？——Windows 系统不提供\"哪个软件直连用了多少\"的数据，只有经过代理的流量有精确数字（来自 Clash）。",
        "为什么 Clash 没开也能用？——看板的核心数据来自系统本身；Clash 在线只是增强：多显示节点名字和代理流量。",
        "波形图的数值多久变一次？——曲线每 0.25 秒更新一次，下方的速率数字每 1 秒刷新一次，方便阅读。",
        "想一直盯着某个软件？——点它那一行选中，右侧明细会持续刷新；再点一次别的行就切换。",
    ]),
]


class HelpPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("helpPage")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.enableTransparentBackground()
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(24, 20, 24, 20)
        iv.setSpacing(6)

        iv.addWidget(SubtitleLabel("使用帮助"))
        iv.addSpacing(4)
        for title, lines in _SECTIONS:
            iv.addWidget(StrongBodyLabel(title))
            for line in lines:
                lbl = BodyLabel(line)
                lbl.setWordWrap(True)
                iv.addWidget(lbl)
            iv.addSpacing(8)
        tip = CaptionLabel("提示：数据只在本机采集与显示，不上传任何内容。")
        iv.addWidget(tip)
        iv.addStretch(1)

        self.scroll.setWidget(inner)
        v.addWidget(self.scroll, 1)

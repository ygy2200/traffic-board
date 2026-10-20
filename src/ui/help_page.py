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
        "② 左侧软件排行：一个软件一行，条越长占用越多。点排行标题旁的「按速率」可以切换成"
        "「现在谁在跑量」视图——数值列变成实时速度，副行显示累计流量；再点一次切回累计视图。",
        "③ 右侧连接明细：每行是一条连接，看「正在访问」列就知道它连去了哪，「渠道」列告诉你走的是代理还是直连。",
        "④ 顶栏最右边显示当前数据源（TUN 单源 / 传统双源）和本次运行的累计流量。",
    ]),
    ("各页面都是干什么的", [
        "流量：主界面，上面三样。",
        "历史：今日 / 本周 / 本月流量总览，加 24 小时 / 7 天柱状图和软件流量回看。",
        "体检：一键检查网络环境（代理生效没有、节点通不通、TUN 和系统代理有没有冲突等），"
        "有问题会给出修复建议。拿不准「我的代理到底开没开」时，先看这一页最上面的「代理总状态」。",
        "设置：开机自启、悬浮速率条、关闭行为、陌生连接提醒、数据源、月流量上限。",
    ]),
    ("名词解释", [
        "代理：流量先经过代理服务器（如你的 Clash 节点）再出去，常用于访问国外网站。",
        "直连：不走代理，直接从你的网络出去，国内网站和游戏一般是直连。",
        "本地/内网：只在你电脑内部或家庭/校园网内部的通信，不出外网，通常不用管。",
        "节点：代理服务的出口，名字由你的机场/服务商起，比如「香港01」。",
        "Clash：一类代理工具的内核（FlClash、Clash Verge 等都属于）。检测到它在线时，看板会额外显示节点明细和代理流量。",
        "TUN：一种让全部网络流量（不只是肯走代理的软件）都经过内核的模式，数据更全；"
        "开了 TUN 就不必再开系统代理，两个一起开反而会让 Microsoft Store 等应用断网。",
        "↓ 下载 / ↑ 上传：↓ 是从网络收到的数据，↑ 是发出去的数据。B、KB、MB 是数据大小，/s 表示每秒。",
    ]),
    ("常见问题", [
        "为什么有的软件没有流量数字？——Windows 系统不提供\"哪个软件直连用了多少\"的数据，只有经过代理的流量有精确数字（来自 Clash）。",
        "为什么 Clash 没开也能用？——看板的核心数据来自系统本身；Clash 在线只是增强：多显示节点名字和代理流量。",
        "波形图的数值多久变一次？——曲线每 0.25 秒更新一次，下方的速率数字每 1 秒刷新一次，方便阅读。",
        "想一直盯着某个软件？——点它那一行选中，右侧明细会持续刷新；再点一次别的行就切换。",
        "开了 TUN 但软件全都叫「未知进程」？——内核的 find-process-mode 被关掉了，体检页的"
        "「进程归因」一项会告诉你，按里面的建议改配置即可。",
        "月流量上限怎么用？——设置里填套餐流量和重置日，用到 80% 会提醒一次，超限再提醒一次；"
        "只统计经代理的流量，校园网等本地流量不算。",
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

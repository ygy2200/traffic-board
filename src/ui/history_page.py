# -*- coding: utf-8 -*-
"""历史页：全机流量柱状图（24 小时/7 天）+ 软件代理流量回看排行。"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget, QSizePolicy

from qfluentwidgets import (
    BodyLabel, CaptionLabel, PillPushButton, SubtitleLabel, CardWidget,
)

from core.history import HistoryStore


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


class BarChart(QWidget):
    """双系列柱状图（绿=下载 蓝=上传），无第三方依赖。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bars: list = []   # [(label, down, up)]
        self.setMinimumHeight(220)

    def set_data(self, bars: list) -> None:
        self._bars = bars
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad_l, pad_b, pad_t = 8, 22, 14
        plot_h = h - pad_b - pad_t
        if not self._bars:
            p.setPen(QColor(140, 140, 140))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无历史数据（软件运行时自动积累）")
            return
        peak = max(max(d for _, d, _ in self._bars), max(u for _, _, u in self._bars), 1.0)
        # 网格 + Y 刻度
        p.setPen(QPen(QColor(255, 255, 255, 16), 1))
        for i in range(1, 4):
            p.drawLine(pad_l, int(pad_t + plot_h * i / 4), w - 50, int(pad_t + plot_h * i / 4))
        p.setPen(QColor(150, 150, 150))
        f = QFont(self.font()); f.setPointSizeF(7.5)
        p.setFont(f)
        p.drawText(w - 46, pad_t + 10, _fmt(peak))
        p.drawText(w - 46, int(pad_t + plot_h / 2), _fmt(peak / 2))

        n = len(self._bars)
        slot = (w - pad_l - 58) / n
        bar_w = max(3.0, min(26.0, slot * 0.62))
        half = bar_w / 2
        f_label = QFont(self.font()); f_label.setPointSizeF(7.5)
        for i, (label, down, up) in enumerate(self._bars):
            cx = pad_l + slot * i + slot / 2
            base = pad_t + plot_h
            hd = (down / peak) * (plot_h - 4)
            hu = (up / peak) * (plot_h - 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(76, 175, 80, 200))
            p.drawRoundedRect(QRectF(cx - half - 1, base - hd, half, hd), 2, 2)
            p.setBrush(QColor(33, 150, 243, 200))
            p.drawRoundedRect(QRectF(cx + 1, base - hu, half, hu), 2, 2)
            p.setPen(QColor(150, 150, 150))
            p.setFont(f_label)
            p.drawText(int(cx - slot / 2), h - 8, int(slot * 2), 14,
                       Qt.AlignmentFlag.AlignCenter, label)


class HistoryPage(QWidget):
    def __init__(self, store: HistoryStore, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.store = store
        self._range: str = "24h"
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("历史流量"))
        top.addStretch(1)
        self.btn_24h = PillPushButton("最近 24 小时")
        self.btn_7d = PillPushButton("最近 7 天")
        self.btn_24h.setCheckable(True)
        self.btn_7d.setCheckable(True)
        self.btn_24h.setChecked(True)
        self.btn_24h.clicked.connect(lambda: self._set_range("24h"))
        self.btn_7d.clicked.connect(lambda: self._set_range("7d"))
        top.addWidget(self.btn_24h)
        top.addWidget(self.btn_7d)
        v.addLayout(top)

        self.chart = BarChart(self)
        self.chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        card = CardWidget(self)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 12, 12, 12)
        cv.addWidget(self.chart)
        legend = CaptionLabel("绿色 = 下载    蓝色 = 上传（全机真实流量）")
        legend.setStyleSheet("color: rgb(160,160,160);")
        cv.addWidget(legend)
        v.addWidget(card, 2)

        v.addWidget(BodyLabel("软件代理流量回看（Top 10）"))
        self.rank_card = CardWidget(self)
        rv = QVBoxLayout(self.rank_card)
        rv.setContentsMargins(12, 8, 12, 8)
        self.rank_area = QLabel("积累中…（软件运行时每分钟记录一次）")
        self.rank_area.setStyleSheet("color: rgb(200,200,200); padding: 4px;")
        self.rank_area.setWordWrap(True)
        rv.addWidget(self.rank_area)
        v.addWidget(self.rank_card, 1)

        self._timer = None  # 由 MainWindow 驱动刷新

    def _set_range(self, r: str) -> None:
        self._range = r
        self.btn_24h.setChecked(r == "24h")
        self.btn_7d.setChecked(r == "7d")
        self.refresh()

    def refresh(self) -> None:
        now = time.time()
        if self._range == "24h":
            since, bucket = now - 86400, 3600
            fmt_ts = lambda ts: time.strftime("%H:00", time.localtime(ts))
        else:
            since, bucket = now - 7 * 86400, 86400
            fmt_ts = lambda ts: time.strftime("%m-%d", time.localtime(ts))
        rows = self.store.query_total_buckets(since, bucket)
        bars = [(fmt_ts(ts), down, up) for ts, up, down in rows]
        self.chart.set_data(bars)

        rank = self.store.query_app_rank(since, top=10)
        if rank:
            lines = []
            for app, up, down, conns in rank:
                lines.append(f"· {app}    ↓{_fmt(down)}  ↑{_fmt(up)}")
            self.rank_area.setText("\n".join(lines))
        else:
            self.rank_area.setText("积累中…（软件运行时每分钟记录一次；代理流量需要 Clash 在线）")

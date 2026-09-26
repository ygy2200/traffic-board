# -*- coding: utf-8 -*-
"""软件排行列表 v2：大字号、高对比、透明背景（贴 Fluent 深色底）。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLabel, QFrame, QScrollArea, QVBoxLayout, QWidget


def _fmt_bytes(n: int) -> str:
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


class RankRow(QWidget):
    clicked_pid = Signal(int)

    HEIGHT = 54

    def __init__(self, app, share: float, metric: str, selected: bool, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._share = max(0.03, share)
        self._metric = metric
        self._selected = selected
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked_pid.emit(self._app.pid)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()

        # 选中底色
        if self._selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 22))
            p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), 8, 8)

        # 比例条：放在下半行作为"底衬"，不与主文字重叠
        bar_y, bar_h = h - 14, 5
        bar_w = (w - 16) * min(1.0, self._share)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(QRectF(8, bar_y, w - 16, bar_h), 2.5, 2.5)
        p.setBrush(QColor(33, 150, 243, 200))
        p.drawRoundedRect(QRectF(8, bar_y, bar_w, bar_h), 2.5, 2.5)

        # 主文字：软件名（大、白）
        p.setPen(QColor(245, 245, 245))
        f = QFont(self.font()); f.setPointSizeF(11.5); f.setBold(True)
        p.setFont(f)
        p.drawText(10, 26, self._app.display)

        # 渠道文字（中灰但够亮）
        p.setPen(QColor(178, 178, 178))
        f2 = QFont(self.font()); f2.setPointSizeF(9)
        p.setFont(f2)
        p.drawText(10, h - 19, self._metric)

        # 右侧流量数字（大号、亮）
        p.setPen(QColor(255, 255, 255))
        f3 = QFont(self.font()); f3.setPointSizeF(10.5); f3.setBold(True)
        p.setFont(f3)
        a = self._app
        val = (f"↓{_fmt_bytes(a.download)} ↑{_fmt_bytes(a.upload)}"
               if (a.download or a.upload) else f"{len(a.conns)} 条")
        p.drawText(QRectF(0, 2, w - 12, h - 8),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, val)


class RankList(QWidget):
    row_clicked = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.container = QWidget()
        self.v = QVBoxLayout(self.container)
        self.v.setContentsMargins(0, 0, 8, 0)
        self.v.setSpacing(2)
        self.v.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.container)
        v.addWidget(self.scroll)
        self._rows = []

    def render(self, apps, metric_mode: str, selected_pid, keyword: str) -> None:
        kw = keyword.strip().lower()
        apps = [a for a in apps if not kw or kw in a.display.lower() or kw in a.proc_name.lower()
                or any(kw in c.target.lower() for c in a.conns)]
        if metric_mode == "bytes":
            apps = sorted(apps, key=lambda a: -(a.download + a.upload))
            shares = self._shares(apps, key=lambda a: a.download + a.upload)
        else:
            apps = sorted(apps, key=lambda a: -len(a.conns))
            shares = self._shares(apps, key=lambda a: len(a.conns))

        for row in self._rows:
            self.v.removeWidget(row)
            row.deleteLater()
        self._rows = []
        for a in apps:
            metric = f"代理 {a.proxy_count} · 直连 {a.direct_count}" if a.proxy_count \
                else f"连接 {len(a.conns)}"
            row = RankRow(a, shares.get(a.pid, 0.03), metric, selected=(selected_pid == a.pid))
            row.clicked_pid.connect(self.row_clicked.emit)
            self.v.insertWidget(self.v.count() - 1, row)
            self._rows.append(row)
        if not apps:
            hint = QLabel("没有匹配的软件")
            hint.setStyleSheet("color: gray; padding: 12px;")
            self.v.insertWidget(0, hint)
            self._rows.append(hint)

    @staticmethod
    def _shares(apps, key) -> dict:
        vals = {a.pid: key(a) for a in apps}
        total = sum(vals.values())
        if total <= 0:
            return {pid: 0.03 for pid in vals}
        return {pid: v / total for pid, v in vals.items()}

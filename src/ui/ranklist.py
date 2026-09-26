# -*- coding: utf-8 -*-
"""软件排行列表 v3：QLabel 布局（杜绝文字重叠）+ 底部比例条。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QFrame, QScrollArea, QVBoxLayout, QWidget,
)


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

    def __init__(self, app, share: float, metric: str, selected: bool, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._share = max(0.03, share)
        self._selected = selected
        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(1)
        self.lbl_name = QLabel(app.display)
        f = QFont(); f.setPointSizeF(11); f.setBold(True)
        self.lbl_name.setFont(f)
        self.lbl_metric = QLabel(metric)
        f2 = QFont(); f2.setPointSizeF(8.5)
        self.lbl_metric.setFont(f2)
        self.lbl_metric.setStyleSheet("color: rgb(170,170,170);")
        left.addWidget(self.lbl_name)
        left.addWidget(self.lbl_metric)
        layout.addLayout(left, 1)

        a = app
        val = (f"↓{_fmt_bytes(a.download)} ↑{_fmt_bytes(a.upload)}"
               if (a.download or a.upload) else f"{len(a.conns)} 条")
        self.lbl_value = QLabel(val)
        f3 = QFont(); f3.setPointSizeF(10); f3.setBold(True)
        self.lbl_value.setFont(f3)
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.lbl_value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked_pid.emit(self._app.pid)

    def paintEvent(self, event) -> None:  # noqa: N802
        """只画：选中底 + 底部比例条。文字全部交给 QLabel，不会重叠。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 22))
            p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), 8, 8)
        bar_y, bar_h = h - 7, 4
        bar_w = (w - 24) * min(1.0, self._share)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawRoundedRect(QRectF(12, bar_y, w - 24, bar_h), 2, 2)
        p.setBrush(QColor(33, 150, 243, 210))
        p.drawRoundedRect(QRectF(12, bar_y, bar_w, bar_h), 2, 2)


class RankList(QWidget):
    row_clicked = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.container = QWidget()
        self.v = QVBoxLayout(self.container)
        self.v.setContentsMargins(0, 0, 8, 0)
        self.v.setSpacing(3)
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

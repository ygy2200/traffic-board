# -*- coding: utf-8 -*-
"""实时流量波形图（QPainter 自绘面积图，不引新依赖）。"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont
from PySide6.QtWidgets import QWidget


class WaveGraph(QWidget):
    """双色面积波形。set_mode 决定单位标签；push_sample 每秒喂一次。"""

    GREEN = QColor(76, 175, 80)     # 下载
    BLUE = QColor(33, 150, 243)     # 上传

    def __init__(self, parent=None, points: int = 60) -> None:
        super().__init__(parent)
        self._down: deque[float] = deque(maxlen=points)
        self._up: deque[float] = deque(maxlen=points)
        self._points = points
        self._mode_label = "字节/秒"
        self.setMinimumHeight(130)

    def set_mode_label(self, text: str) -> None:
        self._mode_label = text

    def push_sample(self, down: float, up: float) -> None:
        self._down.append(max(0.0, float(down)))
        self._up.append(max(0.0, float(up)))
        self.update()  # 触发重绘

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bg = self.palette().color(self.backgroundRole())
        p.fillRect(self.rect(), bg)

        # 网格横线
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for i in range(1, 4):
            y = h * i / 4
            p.drawLine(0, int(y), w, int(y))

        peak = max(max(self._down, default=0.0), max(self._up, default=0.0), 1.0)
        self._draw_series(p, w, h, self._down, self.GREEN, peak)
        self._draw_series(p, w, h, self._up, self.BLUE, peak)

        # 角标（模式说明）
        p.setPen(QColor(140, 140, 140))
        f = QFont(self.font()); f.setPointSizeF(8)
        p.setFont(f)
        p.drawText(8, 16, f"{self._mode_label} · 峰值 {_fmt(peak)}")

    def _draw_series(self, p: QPainter, w: float, h: float, data: deque, color: QColor, peak: float) -> None:
        if len(data) < 2:
            return
        path = QPainterPath()
        n = len(data)
        step = w / (self._points - 1)
        x0 = w - (n - 1) * step  # 数据从右侧往左排（最新在右）
        path.moveTo(x0, h)
        for i, v in enumerate(data):
            x = x0 + i * step
            y = h - (v / peak) * (h - 22) - 2
            path.lineTo(QPointF(x, y))
        path.lineTo(x0 + (n - 1) * step, h)
        path.closeSubpath()
        fill = QColor(color)
        fill.setAlpha(70)
        p.setPen(QPen(color, 1.6))
        p.setBrush(fill)
        p.drawPath(path)


def _fmt(v: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f}{unit}"
        v /= 1024.0
    return f"{v:.1f}GB"

# -*- coding: utf-8 -*-
"""实时流量波形图 v2：样条平滑 + 指数平滑 + 峰值缓变（修复"假"感）。"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


def _fmt(v: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f}{unit}" if unit == "B" else f"{v:.1f}{unit}"
        v /= 1024.0
    return f"{v:.1f}GB"


class WaveGraph(QWidget):
    """双色面积波形。

    平滑三板斧：
    1. 数据入列前做指数移动平均（EMA），消除秒间抖动
    2. Catmull-Rom 样条连线（代替直线），曲线圆润
    3. 峰值缓变：瞬时上升立即跟随、缓慢回落，避免整条曲线反复缩放
    """

    GREEN = QColor(76, 175, 80)
    BLUE = QColor(33, 150, 243)

    def __init__(self, parent=None, points: int = 60) -> None:
        super().__init__(parent)
        self._down: deque[float] = deque(maxlen=points)
        self._up: deque[float] = deque(maxlen=points)
        self._points = points
        self._mode_label = "字节/秒"
        self._ema_down = 0.0
        self._ema_up = 0.0
        self._peak = 0.0            # 峰值缓存（缓变）
        self.setMinimumHeight(150)

    def set_mode_label(self, text: str) -> None:
        self._mode_label = text

    def push_sample(self, down: float, up: float) -> None:
        # EMA：a=0.45 新值权重，兼顾响应与平滑
        self._ema_down = self._ema_down * 0.55 + float(down) * 0.45
        self._ema_up = self._ema_up * 0.55 + float(up) * 0.45
        self._down.append(max(0.0, self._ema_down))
        self._up.append(max(0.0, self._ema_up))
        cur_peak = max(max(self._down), max(self._up), 1.0)
        # 峰值缓变：超过则立即抬升；低于则以每秒 8% 缓慢回落
        self._peak = cur_peak if cur_peak > self._peak else max(cur_peak, self._peak * 0.92)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 网格
        p.setPen(QPen(QColor(255, 255, 255, 16), 1))
        for i in range(1, 4):
            y = int(h * i / 4)
            p.drawLine(0, y, w, y)

        peak = max(self._peak, 1.0)
        self._draw_series(p, w, h, self._down, self.GREEN, peak)
        self._draw_series(p, w, h, self._up, self.BLUE, peak)

        p.setPen(QColor(135, 135, 135))
        f = QFont(self.font()); f.setPointSizeF(8)
        p.setFont(f)
        p.drawText(8, 16, f"{self._mode_label} · 峰值 {_fmt(peak)}")

    def _draw_series(self, p: QPainter, w: float, h: float, data: deque, color: QColor, peak: float) -> None:
        if len(data) < 2:
            return
        n = len(data)
        step = w / (self._points - 1)
        x0 = w - (n - 1) * step
        pts = [(x0 + i * step, h - (v / peak) * (h - 26) - 2) for i, v in enumerate(data)]

        path = QPainterPath()
        path.moveTo(pts[0][0], h)
        path.lineTo(pts[0][0], pts[0][1])
        # Catmull-Rom → 三次贝塞尔平滑
        for i in range(len(pts) - 1):
            x0_, y0_ = pts[i - 1] if i > 0 else pts[i]
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            x3, y3 = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
            c1 = (x1 + (x2 - x0_) / 6.0, y1 + (y2 - y0_) / 6.0)
            c2 = (x2 - (x3 - x1) / 6.0, y2 - (y3 - y1) / 6.0)
            path.cubicTo(c1[0], c1[1], c2[0], c2[1], x2, y2)
        path.lineTo(pts[-1][0], h)
        path.closeSubpath()

        fill = QColor(color); fill.setAlpha(64)
        p.setPen(QPen(color, 2.0))
        p.setBrush(fill)
        p.drawPath(path)

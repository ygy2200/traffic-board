# -*- coding: utf-8 -*-
"""实时流量波形 v3：4Hz 采样 + Y 轴刻度 + 渐变面积 + 样条平滑。

"假"感根治：
- 采样从 1Hz 提到 4Hz（在 PollWorker 线程做轻量 Clash API 采样），突发不再是一根根竖针
- 数据 EMA 平滑（4Hz 下 a=0.3）
- Catmull-Rom 样条连线
- 峰值缓变（瞬时升 / 每步回落 1.5%）
- 右侧 Y 轴刻度（峰值、半峰值）直接读数，不再"看不出多大"
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


def _fmt_rate(v: float) -> str:
    v = float(v)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f}{unit}/s" if unit == "B" else f"{v:.1f}{unit}/s"
        v /= 1024.0
    return f"{v:.1f}GB/s"


class WaveGraph(QWidget):
    GREEN = QColor(76, 175, 80)
    BLUE = QColor(33, 150, 243)

    def __init__(self, parent=None, points: int = 240) -> None:
        super().__init__(parent)
        # 不预填：配合动态点距，曲线从第一帧就全宽拉伸，随采样加密滚动
        self._down: deque[float] = deque(maxlen=points)
        self._up: deque[float] = deque(maxlen=points)
        self._points = points          # 240 点 ≈ 60 秒 @4Hz
        self._mode_label = "字节/秒"
        self._ema_down = 0.0
        self._ema_up = 0.0
        self._peak = 0.0
        self.setMinimumHeight(160)

    def set_mode_label(self, text: str) -> None:
        self._mode_label = text

    def push_sample(self, down: float, up: float) -> None:
        self._ema_down = self._ema_down * 0.7 + float(down) * 0.3
        self._ema_up = self._ema_up * 0.7 + float(up) * 0.3
        self._down.append(max(0.0, self._ema_down))
        self._up.append(max(0.0, self._ema_up))
        cur_peak = max(max(self._down), max(self._up), 1.0)
        self._peak = cur_peak if cur_peak > self._peak else max(cur_peak, self._peak * 0.985)
        self.update()

    def peak(self) -> float:
        return self._peak

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 横向网格（4 分）
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for i in range(1, 4):
            p.drawLine(0, int(h * i / 4), w, int(h * i / 4))

        peak = max(self._peak, 1.0)
        self._draw_series(p, w, h, self._down, self.GREEN, peak)
        self._draw_series(p, w, h, self._up, self.BLUE, peak)

        # Y 轴刻度（右侧读数）
        p.setPen(QColor(150, 150, 150))
        f = QFont(self.font()); f.setPointSizeF(8)
        p.setFont(f)
        p.drawText(w - 70, 16, _fmt_rate(peak))
        p.drawText(w - 70, int(h / 2) - 4, _fmt_rate(peak / 2))
        p.drawText(w - 70, h - 6, "0")
        # 左上角模式
        p.setPen(QColor(135, 135, 135))
        p.drawText(8, 16, self._mode_label)

    def _draw_series(self, p: QPainter, w: float, h: float, data: deque, color: QColor, peak: float) -> None:
        if len(data) < 2:
            return
        n = len(data)
        # 关键：按当前实际点数铺满全宽（点少时拉伸显示，点满后自然滚动）
        step = w / max(1, self._points - 1) if n >= self._points else w / max(1, n - 1)
        x0 = w - (n - 1) * step if n < self._points else 0.0
        pts = [(x0 + i * step, h - (v / peak) * (h - 24) - 2) for i, v in enumerate(data)]

        path = QPainterPath()
        path.moveTo(pts[0][0], h)
        path.lineTo(pts[0][0], pts[0][1])
        for i in range(len(pts) - 1):
            px, py = pts[i - 1] if i > 0 else pts[i]
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            nx, ny = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
            c1x = x1 + (x2 - px) / 6.0; c1y = y1 + (y2 - py) / 6.0
            c2x = x2 - (nx - x1) / 6.0; c2y = y2 - (ny - y1) / 6.0
            path.cubicTo(c1x, c1y, c2x, c2y, x2, y2)
        path.lineTo(pts[-1][0], h)
        path.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        top = QColor(color); top.setAlpha(90)
        bottom = QColor(color); bottom.setAlpha(10)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        p.setPen(QPen(color, 1.8))
        p.setBrush(grad)
        p.drawPath(path)

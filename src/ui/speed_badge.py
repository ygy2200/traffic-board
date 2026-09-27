# -*- coding: utf-8 -*-
"""悬浮速率条：置顶无边框小窗，实时显示全机上下行速度，可拖动、双击回主界面。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget, QMenu


class SpeedBadge(QWidget):
    double_clicked = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(210, 40)
        self._down = "—"
        self._up = "—"
        self._drag_pos: QPoint | None = None
        self._dark = True

    def set_speed(self, down_txt: str, up_txt: str) -> None:
        if self._down != down_txt or self._up != up_txt:
            self._down = down_txt
            self._up = up_txt
            self.update()

    # ---- 拖动 ----
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            act_show = menu.addAction("显示主界面")
            menu.addSeparator()
            act_quit = menu.addAction("退出")
            chosen = menu.exec(event.globalPosition().toPoint())
            if chosen is act_show:
                self.double_clicked.emit()
            elif chosen is act_quit:
                self.quit_requested.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.double_clicked.emit()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, 10, 10)
        p.fillPath(path, QColor(24, 24, 28, 215))
        p.setPen(QColor(70, 130, 200, 120))
        p.drawPath(path)
        # 绿点=下载 蓝点=上传
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(76, 175, 80))
        p.drawEllipse(14, h // 2 - 10, 7, 7)
        p.setBrush(QColor(33, 150, 243))
        p.drawEllipse(14, h // 2 + 3, 7, 7)
        p.setPen(QColor(240, 240, 240))
        f = QFont(self.font()); f.setPointSizeF(9.5); f.setBold(True)
        p.setFont(f)
        p.drawText(28, h // 2 - 6, f"↓ {self._down}")
        p.drawText(28, h - 6, f"↑ {self._up}")

# -*- coding: utf-8 -*-
"""悬浮速率条 v2：默认贴任务栏左下角，位置记忆，可锁定/拖动，双击回主界面。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QSettings, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtGui import QGuiApplication
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
        self._drag_offset: QPoint | None = None
        self._locked = False
        self._dark = True
        self._settings = QSettings("TrafficBoard", "TrafficBoard")
        self._restore_or_default_pos()

    # ---- 位置 ----
    def _default_pos(self) -> QPoint:
        """任务栏左下角（可用区域底部左侧，略留边距）。"""
        screen = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(screen.left() + 8, screen.bottom() - self.height() - 4)

    def _restore_or_default_pos(self) -> None:
        try:
            pos = self._settings.value("badge/pos")
            if pos is not None:
                pos = QPoint(int(pos.x()), int(pos.y()))
        except Exception:
            pos = None
        self._locked = self._settings.value("badge/locked", "0") in ("1", "true", True)
        if pos is None:
            pos = self._default_pos()
        else:
            # 位置合法性：必须落在某个屏幕内（分辨率变化后防丢）
            inside = False
            for scr in QGuiApplication.screens():
                if scr.availableGeometry().contains(pos):
                    inside = True
                    break
            if not inside:
                pos = self._default_pos()
        self.move(pos)

    def _save_pos(self) -> None:
        self._settings.setValue("badge/pos", self.pos())

    # ---- 交互 ----
    def set_topmost(self, on: bool) -> None:
        """切换置顶（固定图层）。需要重新 show 才能生效。"""
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def set_speed(self, down_txt: str, up_txt: str) -> None:
        if self._down != down_txt or self._up != up_txt:
            self._down = down_txt
            self._up = up_txt
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            act_show = menu.addAction("显示主界面")
            act_lock = menu.addAction("锁定位置")
            act_lock.setCheckable(True)
            act_lock.setChecked(self._locked)
            menu.addSeparator()
            act_quit = menu.addAction("退出")
            chosen = menu.exec(event.globalPosition().toPoint())
            if chosen is act_show:
                self.double_clicked.emit()
            elif chosen is act_quit:
                self.quit_requested.emit()
            elif chosen is act_lock:
                self._locked = act_lock.isChecked()
                self._settings.setValue("badge/locked", "1" if self._locked else "0")
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._locked:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_pos()

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

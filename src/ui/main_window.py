# -*- coding: utf-8 -*-
"""流量看板主界面（Fluent 风格）。

轮询在后台 QThread，主线程只渲染（qfluentwidgets 耗时操作禁主线程）。
退出用 os._exit 兜底（qfluentwidgets item view 退出挂起的已知坑）。
"""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QThread, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QVBoxLayout, QWidget, QFrame, QSizePolicy,
)

from qfluentwidgets import (
    BodyLabel, CardWidget, CaptionLabel, FluentWindow, FluentIcon,
    InfoBadge, NavigationItemPosition, SearchLineEdit, StrongBodyLabel,
    SubtitleLabel, TableWidget, TransparentToolButton, themeColor,
)

from core.aggregator import Aggregator, Board, CHANNEL_LABEL


class PollWorker(QThread):
    """后台轮询聚合器，每秒产出一版 Board。"""

    board_ready = Signal(object)

    def __init__(self, agg: Aggregator, interval_ms: int = 1000) -> None:
        super().__init__()
        self._agg = agg
        self._interval = interval_ms
        self._running = True

    def run(self) -> None:
        while self._running:
            t0 = time.time()
            try:
                board = self._agg.build()
                self.board_ready.emit(board)
            except Exception:
                pass
            elapsed = time.time() - t0
            time.sleep(max(0.2, self._interval / 1000.0 - elapsed))

    def stop(self) -> None:
        self._running = False


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


class AppCard(CardWidget):
    """一个软件一张卡：名称 + 渠道计数 + 展开的连接行。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(16, 12, 16, 12)
        self._v.setSpacing(4)
        self._head = QWidget()
        h = QHBoxLayout(self._head)
        h.setContentsMargins(0, 0, 0, 0)
        self.lbl_name = StrongBodyLabel(self._head)
        self.lbl_meta = CaptionLabel(self._head)
        self.lbl_meta.setTextColor(QColor(128, 128, 128), QColor(160, 160, 160))
        h.addWidget(self.lbl_name)
        h.addSpacing(8)
        h.addWidget(self.lbl_meta)
        h.addStretch(1)
        self._v.addWidget(self._head)
        self._rows: list[QWidget] = []

    def update_data(self, app) -> None:
        self.lbl_name.setText(app.display)
        counts = f"共 {len(app.conns)} 条"
        if app.proxy_count:
            counts += f" · 代理 {app.proxy_count}"
        if app.direct_count:
            counts += f" · 直连 {app.direct_count}"
        total = app.upload + app.download
        if total:
            counts += f" · ↓{_fmt_bytes(app.download)} ↑{_fmt_bytes(app.upload)}"
        self.lbl_meta.setText(counts)
        self._clear_rows()
        for cv in app.conns[:6]:
            row = QWidget(self)
            rh = QHBoxLayout(row)
            rh.setContentsMargins(16, 1, 0, 1)
            rh.setSpacing(8)
            badge = InfoBadge.custom(f"{CHANNEL_LABEL[cv.channel]}", "#f0f0f0", "#f0f0f0")
            if cv.channel == "proxy":
                badge = InfoBadge.custom("代理", "#e8f1ff", "#e8f1ff")
                badge.setStyleSheet(f"color:{themeColor().name()};"
                                    f"border:1px solid {themeColor().name()};border-radius:8px;"
                                    "padding:1px 8px;font-size:12px;background:transparent;")
            elif cv.channel == "direct":
                badge.setStyleSheet("color:#2e7d32;border:1px solid #2e7d32;border-radius:8px;"
                                    "padding:1px 8px;font-size:12px;background:transparent;")
            else:
                badge.setStyleSheet("color:#9e9e9e;border:1px solid #bdbdbd;border-radius:8px;"
                                    "padding:1px 8px;font-size:12px;background:transparent;")
            node = f" · {cv.node}" if cv.node else ""
            updown = f"   ↓{_fmt_bytes(cv.download)} ↑{_fmt_bytes(cv.upload)}" if (cv.upload or cv.download) else ""
            lbl = BodyLabel(f"{cv.target}{node}{updown}", row)
            lbl.setTextColor(QColor(60, 60, 60), QColor(200, 200, 200))
            f = lbl.font(); f.setPointSizeF(8.5); lbl.setFont(f)
            rh.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
            rh.addWidget(lbl, 1, Qt.AlignmentFlag.AlignVCenter)
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._rows.append(row)
            self._v.addWidget(row)
        more = len(app.conns) - 6
        if more > 0:
            extra = CaptionLabel(f"… 还有 {more} 条连接", self)
            extra.setTextColor(QColor(150, 150, 150), QColor(150, 150, 150))
            extra.setContentsMargins(16, 0, 0, 0)
            self._rows.append(extra)
            self._v.addWidget(extra)

    def _clear_rows(self) -> None:
        for w in self._rows:
            self._v.removeWidget(w)
            w.deleteLater()
        self._rows = []


class OverviewPage(QWidget):
    """总览页：搜索 + 软件卡片流。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("搜索软件 / 域名 / IP …")
        self.search.setFixedWidth(360)
        v.addWidget(self.search)
        from qfluentwidgets import ScrollArea
        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inner = QWidget()
        self.inner_v = QVBoxLayout(self.inner)
        self.inner_v.setContentsMargins(0, 0, 8, 0)
        self.inner_v.setSpacing(8)
        self.inner_v.addStretch(1)
        self.scroll.setWidget(self.inner)
        self.scroll.enableTransparentBackground()
        v.addWidget(self.scroll, 1)
        self._cards: list[AppCard] = []

    def render(self, board: Board, keyword: str) -> None:
        kw = keyword.strip().lower()
        apps = [a for a in board.apps
                if not kw or kw in a.display.lower() or kw in a.proc_name.lower()
                or any(kw in c.target.lower() for c in a.conns)]
        # 复用卡片，减少重建抖动
        while len(self._cards) < len(apps):
            card = AppCard(self.inner)
            self.inner_v.insertWidget(self.inner_v.count() - 1, card)
            self._cards.append(card)
        for i, card in enumerate(self._cards):
            if i < len(apps):
                card.update_data(apps[i])
                card.setVisible(True)
            else:
                card.setVisible(False)
        self.inner.setUpdatesEnabled(False)
        self.inner.setUpdatesEnabled(True)


class DetailPage(QWidget):
    """连接明细页：全量表格。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        self.table = TableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["软件", "目标", "渠道", "节点", "上行", "下行"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        v.addWidget(self.table, 1)

    def render(self, board: Board, keyword: str) -> None:
        kw = keyword.strip().lower()
        rows = []
        for a in board.apps:
            for cv in a.conns:
                line = f"{a.display} {cv.target} {cv.node}".lower()
                if kw and kw not in line:
                    continue
                rows.append((a, cv))
        self.table.setRowCount(len(rows))
        for i, (a, cv) in enumerate(rows):
            label = CHANNEL_LABEL[cv.channel] + (f"·{cv.node}" if cv.node else "")
            vals = [a.display, cv.target, label, cv.node or "—",
                    _fmt_bytes(cv.upload) if cv.upload else "—",
                    _fmt_bytes(cv.download) if cv.download else "—"]
            for j, val in enumerate(vals):
                from PySide6.QtWidgets import QTableWidgetItem
                it = self.table.setItem(i, j, QTableWidgetItem(str(val)))
                if it is not None and j == 2:
                    if cv.channel == "proxy":
                        it.setForeground(QColor(themeColor().name()))
                    elif cv.channel == "direct":
                        it.setForeground(QColor("#2e7d32"))


class MainWindow(FluentWindow):
    def __init__(self, agg: Aggregator) -> None:
        super().__init__()
        self.setWindowTitle("流量看板")
        self.resize(960, 680)
        self.agg = agg

        self.page_over = OverviewPage()
        self.page_over.setObjectName("overview")
        self.page_detail = DetailPage()
        self.page_detail.setObjectName("detail")
        self.addSubInterface(self.page_over, FluentIcon.GLOBE, "总览")
        self.addSubInterface(self.page_detail, FluentIcon.IOT, "连接明细")

        self._board = Board()
        self.worker = PollWorker(agg)
        self.worker.board_ready.connect(self._on_board, Qt.ConnectionType.QueuedConnection)
        self.worker.start()

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._render)
        self._tick.start(1000)
        self.page_over.search.textChanged.connect(lambda _: self._render())

    def _on_board(self, board) -> None:
        self._board = board

    def _render(self) -> None:
        kw = self.page_over.search.text()
        self.page_over.render(self._board, kw)
        self.page_detail.render(self._board, kw)
        self._update_status()

    def _update_status(self) -> None:
        b = self._board
        if b.clash_online:
            tip = f"Clash 已连接 v{b.clash_version} · DNS 映射 {b.dns_count} 条"
        else:
            tip = f"Clash 未连接（仅系统视角） · DNS 映射 {b.dns_count} 条"
        if b.sys_partial:
            tip += " · [建议管理员运行以显示全部进程]"
        self.setWindowTitle(f"流量看板 — {tip}")

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.worker.stop()
            self.agg.stop()
        finally:
            event.accept()
            os._exit(0)  # qfluentwidgets 退出挂起兜底


def run_app() -> int:
    app = QApplication(sys.argv)
    agg = Aggregator()
    w = MainWindow(agg)
    w.show()
    code = app.exec()
    os._exit(code)

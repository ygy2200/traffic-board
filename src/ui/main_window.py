# -*- coding: utf-8 -*-
"""流量看板 v1.2 主窗口：波形 + 软件排行 + 点击下钻明细（GlassWire 式三段布局，深色主题）。"""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout,
    QWidget, QSplitter,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, FluentWindow, FluentIcon, SearchLineEdit,
    SubtitleLabel, TableWidget, Theme, setTheme,
)

from core.aggregator import Aggregator, Board
from core.domains_cn import annotate
from ui.wavegraph import WaveGraph
from ui.ranklist import RankList, _fmt_bytes


class PollWorker(QThread):
    """后台复合循环：250ms 轻量波形采样（Clash 总量差分），每 4 轮做一次重量级面板刷新。"""
    board_ready = Signal(object)
    wave_ready = Signal(float, float)   # down B/s, up B/s
    offline_tick = Signal(int)          # 离线时发连接数

    def __init__(self, agg: Aggregator, wave_ms: int = 250, board_ticks: int = 4) -> None:
        super().__init__()
        self._agg = agg
        self._wave_ms = wave_ms
        self._board_ticks = board_ticks
        self._running = True

    def run(self) -> None:
        tick = 0
        last = None
        last_t = time.time()
        while self._running:
            loop_t0 = time.time()
            try:
                clash = self._agg.clash.snapshot()
                if clash.online:
                    cur = (clash.upload_total, clash.download_total)
                    if last is not None:
                        dt = max(0.05, time.time() - last_t)
                        self.wave_ready.emit((cur[1] - last[1]) / dt, (cur[0] - last[0]) / dt)
                    last = cur
                    last_t = time.time()
                else:
                    last = None
            except Exception:
                pass
            tick += 1
            if tick % self._board_ticks == 0 or last is None:
                try:
                    board = self._agg.build()
                    self.board_ready.emit(board)
                    if last is None:
                        self.offline_tick.emit(sum(len(a.conns) for a in board.apps))
                except Exception:
                    pass
            rest = self._wave_ms / 1000.0 - (time.time() - loop_t0)
            if rest > 0:
                time.sleep(rest)

    def stop(self) -> None:
        self._running = False


def _channel(cv) -> str:
    if cv.channel == "proxy":
        return f"代理 · {cv.node}" if cv.node else "代理"
    if cv.channel == "direct":
        return "直连"
    return "本地/内网"


class MainWindow(FluentWindow):
    def __init__(self, agg: Aggregator) -> None:
        super().__init__()
        setTheme(Theme.DARK)
        self.setWindowTitle("流量看板")
        self.resize(1000, 700)
        self.agg = agg
        self._board = Board()
        self._selected_pid = None
        self._last_bytes = None
        self._metric_mode = "bytes"

        page = QWidget()
        page.setObjectName("dashPage")
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)

        # ---- 顶栏 ----
        top = QHBoxLayout()
        title = BodyLabel("流量看板")
        f = title.font(); f.setPointSizeF(13); f.setBold(True); title.setFont(f)
        top.addWidget(title)
        top.addSpacing(8)
        self.search = SearchLineEdit(page)
        self.search.setPlaceholderText("搜软件 / 网站 / IP")
        self.search.setFixedWidth(260)
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search)
        top.addStretch(1)
        self.status = CaptionLabel(page)
        top.addWidget(self.status)
        v.addLayout(top)

        # ---- 波形区 ----
        self.wave = WaveGraph(page)
        v.addWidget(self.wave)
        self.rate = CaptionLabel(page)
        v.addWidget(self.rate)

        # ---- 左右分栏 ----
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        lv.addWidget(SubtitleLabel("软件排行"))
        self.rank = RankList(left)
        lv.addWidget(self.rank, 1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        self.detail_title = SubtitleLabel("连接明细")
        rv.addWidget(self.detail_title)
        self.table = TableWidget(right)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["正在访问", "渠道", "下载", "上传"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        rv.addWidget(self.table, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)

        self.addSubInterface(page, FluentIcon.GLOBE, "流量")

        self.rank.row_clicked.connect(self._on_pick)
        self.search.textChanged.connect(self._render)
        self.worker = PollWorker(agg)
        self.worker.board_ready.connect(self._on_board, Qt.ConnectionType.QueuedConnection)
        self.worker.wave_ready.connect(self._on_wave, Qt.ConnectionType.QueuedConnection)
        self.worker.offline_tick.connect(self._on_offline, Qt.ConnectionType.QueuedConnection)
        self.worker.start()

    def _on_wave(self, down_bps: float, up_bps: float) -> None:
        self.wave.set_mode_label("字节/秒（Clash 数据 · 4Hz 采样）")
        self.wave.push_sample(down_bps, up_bps)
        self.rate.setText(f"当前速率：  ↓ {_fmt_bytes(down_bps)}/s    ↑ {_fmt_bytes(up_bps)}/s")
        self._metric_mode = "bytes"
        self.status.setText(f"● Clash 已连接 v{self.agg.clash.snapshot().version}")
        self.status.setTextColor(QColor("#4caf50"), QColor("#4caf50"))

    def _on_offline(self, conn_count: int) -> None:
        self.wave.set_mode_label("连接数（Clash 离线，降级模式）")
        self.wave.push_sample(conn_count, 0)
        self.rate.setText(f"当前活动连接：{conn_count} 条（Clash 未连接，字节流量不可用）")
        self._metric_mode = "count"
        self.status.setText("● Clash 未连接")
        self.status.setTextColor(QColor(150, 150, 150), QColor(120, 120, 120))

    # ---- 数据流 ----
    def _on_board(self, board: Board) -> None:
        self._board = board
        clash = self.agg.clash.snapshot()
        if clash.online:
            self.status.setText(f"● Clash 已连接 v{clash.version}")
            self.status.setTextColor(QColor("#4caf50"), QColor("#4caf50"))
        else:
            self.status.setText("● Clash 未连接")
            self.status.setTextColor(QColor(150, 150, 150), QColor(120, 120, 120))
        self._render()

    def _on_pick(self, pid: int) -> None:
        self._selected_pid = None if self._selected_pid == pid else pid
        self._render()

    def _render(self) -> None:
        b = self._board
        self.rank.render(b.apps, self._metric_mode, self._selected_pid, self.search.text())

        app = next((a for a in b.apps if a.pid == self._selected_pid), None)
        kw = self.search.text().strip().lower()
        if app is None:
            self.detail_title.setText("连接明细")
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem("点击左侧任一软件，查看它的连接明细"))
            for j in range(1, 4):
                self.table.setItem(0, j, QTableWidgetItem(""))
            return

        self.detail_title.setText(f"{app.display} 的连接（{len(app.conns)} 条）")
        rows = [cv for cv in app.conns if not kw or kw in cv.target.lower()]
        self.table.setRowCount(len(rows))
        for i, cv in enumerate(rows):
            vals = [annotate(cv.target), _channel(cv), _fmt_bytes(cv.download), _fmt_bytes(cv.upload)]
            for j, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if j == 1:
                    if cv.channel == "proxy":
                        item.setForeground(QColor("#64b5f6"))
                    elif cv.channel == "direct":
                        item.setForeground(QColor("#81c784"))
                    else:
                        item.setForeground(QColor(140, 140, 140))
                self.table.setItem(i, j, item)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.worker.stop()
            self.agg.stop()
        finally:
            event.accept()
            os._exit(0)


def run_app() -> int:
    app = QApplication(sys.argv)
    agg = Aggregator()
    w = MainWindow(agg)
    w.show()
    code = app.exec()
    os._exit(code)

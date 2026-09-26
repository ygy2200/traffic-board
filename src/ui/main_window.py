# -*- coding: utf-8 -*-
"""流量看板 v1.2 主窗口：波形 + 软件排行 + 点击下钻明细（GlassWire 式三段布局，深色主题）。"""
from __future__ import annotations

import os
import sys
import time

import psutil
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout,
    QWidget, QSplitter, QSizePolicy,
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
    """后台复合循环：250ms 双采样（全机网卡流量 + Clash 代理总量），每 4 轮一次面板刷新。"""
    board_ready = Signal(object)
    wave_ready = Signal(float, float, float, float, bool)  # 全机down/up B/s, 代理down/up B/s, clash在线

    def __init__(self, agg: Aggregator, wave_ms: int = 250, board_ticks: int = 4) -> None:
        super().__init__()
        self._agg = agg
        self._wave_ms = wave_ms
        self._board_ticks = board_ticks
        self._running = True

    def run(self) -> None:
        tick = 0
        last_nic = None
        last_nic_t = 0.0
        last_clash = None
        while self._running:
            loop_t0 = time.time()
            try:
                nics = psutil.net_io_counters(pernic=True)
                dn = sum(v.bytes_recv for k, v in nics.items() if not k.lower().startswith("lo"))
                up = sum(v.bytes_sent for k, v in nics.items() if not k.lower().startswith("lo"))
                if last_nic is not None:
                    dt = time.time() - last_nic_t
                    if dt >= 0.1:
                        down_bps = max(0.0, (dn - last_nic[0]) / dt)
                        up_bps = max(0.0, (up - last_nic[1]) / dt)
                        clash = self._agg.clash.snapshot()
                        pd = pu = 0.0
                        online = clash.online
                        if online and last_clash is not None:
                            pd = max(0.0, (clash.download_total - last_clash[1]) / dt)
                            pu = max(0.0, (clash.upload_total - last_clash[0]) / dt)
                        self.wave_ready.emit(down_bps, up_bps, pd, pu, online)
                        last_nic_t = time.time()
                    else:
                        last_nic_t = t0
                else:
                    last_nic_t = time.time()
                last_nic = (dn, up)
                if clash.online:
                    last_clash = (clash.upload_total, clash.download_total)
            except Exception:
                pass
            tick += 1
            if tick % self._board_ticks == 0:
                try:
                    self.board_ready.emit(self._agg.build())
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
        self._detail_key = None
        self._detail_vals = []
        self._detail_title_text = ""

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

        # ---- 波形区（强制全宽）----
        self.wave = WaveGraph(page)
        self.wave.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.wave.setMinimumHeight(170)
        v.addWidget(self.wave)
        self.rate = CaptionLabel(page)
        v.addWidget(self.rate)

        # ---- 左右分栏（显式宽度分配，防止塌缩）----
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        self.rank_title = SubtitleLabel("软件排行（按连接数）")
        lv.addWidget(self.rank_title)
        self.rank = RankList(left)
        lv.addWidget(self.rank, 1)
        left.setMinimumWidth(300)

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
        split.setSizes([430, 530])
        v.addWidget(split, 1)

        self.addSubInterface(page, FluentIcon.GLOBE, "流量")

        self.rank.row_clicked.connect(self._on_pick)
        self.search.textChanged.connect(self._render)
        self.worker = PollWorker(agg)
        self.worker.board_ready.connect(self._on_board, Qt.ConnectionType.QueuedConnection)
        self.worker.wave_ready.connect(self._on_wave, Qt.ConnectionType.QueuedConnection)
        self.worker.start()

    def _on_wave(self, down_bps: float, up_bps: float, pd: float, pu: float, online: bool) -> None:
        """全机真实流量波形（直连+代理都含），代理量作数字标注。"""
        self.wave.set_mode_label("全机真实流量 · 字节/秒（含直连与代理）")
        self.wave.push_sample(down_bps, up_bps)
        proxy_txt = f"    ·    经代理 ↓ {_fmt_bytes(pd)}/s  ↑ {_fmt_bytes(pu)}/s" if online else ""
        self.rate.setText(f"全机  ↓ {_fmt_bytes(down_bps)}/s    ↑ {_fmt_bytes(up_bps)}/s{proxy_txt}")
        self._metric_mode = "bytes" if online else "count"
        self.rank_title.setText("软件排行（按代理流量）" if online else "软件排行（按连接数）")

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
        # 点击即选中（不做 toggle：误触两次会显得"没反应"）
        self._selected_pid = pid
        self._render()

    def _render(self) -> None:
        b = self._board
        self.rank.render(b.apps, self._metric_mode, self._selected_pid, self.search.text())
        self._render_detail()

    def _render_detail(self) -> None:
        """明细表增量更新：结构不变时只改文本（不再每秒新建 item）。"""
        b = self._board
        app = next((a for a in b.apps if a.pid == self._selected_pid), None)
        kw = self.search.text().strip().lower()
        if app is None:
            if self._detail_key != ("none", ""):
                self._detail_key = ("none", "")
                self.detail_title.setText("连接明细")
                self.table.setRowCount(1)
                self.table.setItem(0, 0, QTableWidgetItem("点击左侧任一软件，查看它的连接明细"))
                for j in range(1, 4):
                    self.table.setItem(0, j, QTableWidgetItem(""))
            return

        rows = [cv for cv in app.conns if not kw or kw in cv.target.lower()]
        title = f"{app.display} 的连接（{len(app.conns)} 条）"
        key = (app.pid, tuple((cv.target, cv.channel) for cv in rows))
        struct_changed = key != self._detail_key
        if self._detail_title_text != title:
            self.detail_title.setText(title)
            self._detail_title_text = title
        if len(rows) != self.table.rowCount():
            self.table.setRowCount(len(rows))
            struct_changed = True

        for i, cv in enumerate(rows):
            vals = [annotate(cv.target), _channel(cv), _fmt_bytes(cv.download), _fmt_bytes(cv.upload)]
            old_vals = self._detail_vals[i] if struct_changed is False and i < len(self._detail_vals) else None
            for j, val in enumerate(vals):
                if old_vals is not None and old_vals[j] == val:
                    continue  # 文本未变，跳过
                item = QTableWidgetItem(str(val))
                if j == 1:
                    if cv.channel == "proxy":
                        item.setForeground(QColor("#64b5f6"))
                    elif cv.channel == "direct":
                        item.setForeground(QColor("#81c784"))
                    else:
                        item.setForeground(QColor(140, 140, 140))
                self.table.setItem(i, j, item)
        self._detail_key = key
        self._detail_vals = [list(_detail_row_vals(cv)) for cv in rows]


def _detail_row_vals(cv):
    return [annotate(cv.target), _channel(cv), _fmt_bytes(cv.download), _fmt_bytes(cv.upload)]

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

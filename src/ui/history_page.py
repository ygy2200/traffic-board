# -*- coding: utf-8 -*-
"""历史页：流量总览（今日/本周/本月）+ 全机流量柱状图（24 小时/7 天）+ 软件回看排行。"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget, QSizePolicy,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, PillPushButton, PushButton, SubtitleLabel, CardWidget,
)

from core.history import HistoryStore


def default_csv_path(now: float | None = None) -> str:
    """默认导出到「下载」或用户目录，文件名带时间，避免连续导出互相覆盖。"""
    ts = time.strftime("%Y%m%d-%H%M", time.localtime(time.time() if now is None else now))
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    return str(base / f"流量看板历史-{ts}.csv")


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


class BarChart(QWidget):
    """双系列柱状图（绿=下载 蓝=上传），无第三方依赖。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bars: list = []   # [(label, down, up)]
        self._empty_text = "暂无历史数据（程序运行满 1 分钟后开始统计）"
        self.setMinimumHeight(220)

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text

    def set_data(self, bars: list) -> None:
        self._bars = bars
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad_l, pad_b, pad_t = 8, 22, 14
        plot_h = h - pad_b - pad_t
        if not self._bars:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self._empty_text)
            return
        peak = max(max(d for _, d, _ in self._bars), max(u for _, _, u in self._bars), 1.0)
        # 网格 + Y 刻度
        p.setPen(QPen(QColor(255, 255, 255, 16), 1))
        for i in range(1, 4):
            p.drawLine(pad_l, int(pad_t + plot_h * i / 4), w - 50, int(pad_t + plot_h * i / 4))
        p.setPen(QColor(170, 170, 170))
        f = QFont(self.font()); f.setPointSizeF(7.5)
        p.setFont(f)
        p.drawText(w - 46, pad_t + 10, _fmt(peak))
        p.drawText(w - 46, int(pad_t + plot_h / 2), _fmt(peak / 2))

        n = len(self._bars)
        slot = (w - pad_l - 58) / n
        bar_w = max(3.0, min(26.0, slot * 0.62))
        half = bar_w / 2
        f_label = QFont(self.font()); f_label.setPointSizeF(7.5)
        for i, (label, down, up) in enumerate(self._bars):
            cx = pad_l + slot * i + slot / 2
            base = pad_t + plot_h
            hd = (down / peak) * (plot_h - 4)
            hu = (up / peak) * (plot_h - 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(76, 175, 80, 200))
            p.drawRoundedRect(QRectF(cx - half - 1, base - hd, half, hd), 2, 2)
            p.setBrush(QColor(33, 150, 243, 200))
            p.drawRoundedRect(QRectF(cx + 1, base - hu, half, hu), 2, 2)
            p.setPen(QColor(170, 170, 170))
            p.setFont(f_label)
            p.drawText(int(cx - slot / 2), h - 8, int(slot * 2), 14,
                       Qt.AlignmentFlag.AlignCenter, label)


class HistoryPage(QWidget):
    # ("success" | "error" | "info", 一句话说明)：反馈走窗口 InfoBar/托盘气泡，
    # 不占页面纵向空间——历史页的图表已经够高，再加一行说明会把最小窗口顶到 532。
    exported = Signal(str, str)

    def __init__(self, store: HistoryStore, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.store = store
        self._range: str = "24h"
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("历史流量"))
        top.addStretch(1)
        self.btn_24h = PillPushButton("最近 24 小时")
        self.btn_7d = PillPushButton("最近 7 天")
        self.btn_24h.setCheckable(True)
        self.btn_7d.setCheckable(True)
        self.btn_24h.setChecked(True)
        self.btn_24h.clicked.connect(lambda: self._set_range("24h"))
        self.btn_7d.clicked.connect(lambda: self._set_range("7d"))
        top.addWidget(self.btn_24h)
        top.addWidget(self.btn_7d)
        v.addLayout(top)

        # ---- 总览卡片：今日 / 本周 / 本月（R7，对标同类产品的日/月总量视图）----
        self.summary_card = CardWidget(self)
        sv = QHBoxLayout(self.summary_card)
        sv.setContentsMargins(12, 10, 12, 10)
        sv.setSpacing(8)
        self._sum_labels: dict = {}
        for key, title in (("today", "今日"), ("week", "本周"), ("month", "本月")):
            tile = QVBoxLayout()
            tile.setSpacing(1)
            t = BodyLabel(title)
            t.setStyleSheet("color: rgb(180,180,180);")
            val = BodyLabel("—")
            f = val.font(); f.setPointSizeF(13); f.setBold(True)
            val.setFont(f)
            tile.addWidget(t)
            tile.addWidget(val)
            sv.addLayout(tile, 1)
            self._sum_labels[key] = val
        self.btn_export = PushButton("导出 CSV")
        self.btn_export.setToolTip(
            "导出当前范围的本机分钟流量与软件代理流量（Excel 可直接打开）")
        self.btn_export.clicked.connect(self._on_export)
        sv.addWidget(self.btn_export, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(self.summary_card)
        QWidget.setTabOrder(self.btn_7d, self.btn_export)

        self.chart = BarChart(self)
        self.chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        card = CardWidget(self)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 12, 12, 12)
        cv.addWidget(self.chart)
        legend = CaptionLabel("绿色 = 下载    蓝色 = 上传（全机真实流量）")
        legend.setStyleSheet("color: rgb(160,160,160);")
        cv.addWidget(legend)
        v.addWidget(card, 2)

        v.addWidget(BodyLabel("软件代理流量回看（Top 10）"))
        self.rank_card = CardWidget(self)
        rv = QVBoxLayout(self.rank_card)
        rv.setContentsMargins(12, 8, 12, 8)
        self.rank_area = QLabel("积累中…（软件运行时每分钟记录一次）")
        self.rank_area.setStyleSheet("color: rgb(200,200,200); padding: 4px;")
        self.rank_area.setWordWrap(True)
        rv.addWidget(self.rank_area)
        v.addWidget(self.rank_card, 1)

        self._timer = None  # 由 MainWindow 驱动刷新

    def _set_range(self, r: str) -> None:
        self._range = r
        self.btn_24h.setChecked(r == "24h")
        self.btn_7d.setChecked(r == "7d")
        self.refresh()

    def _range_since(self, now: float) -> int:
        return int(now - (86400 if self._range == "24h" else 7 * 86400))

    def _on_export(self) -> None:
        if not self.store.has_data_since(self._range_since(time.time())):
            self.exported.emit(
                "info", "这个范围还没有记录可导出（每满 1 分钟记一条，先运行一会儿再试）")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出历史流量", default_csv_path(), "CSV 文件 (*.csv)")
        if path:
            self._export_to(path)

    def _export_to(self, path: str) -> bool:
        """真正写文件并通过 exported 汇报；空范围不建文件，返回 False。"""
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            n_total, n_app = self.store.export_csv(self._range_since(time.time()), path)
        except OSError as e:
            self.exported.emit("error", f"导出失败：{e}")
            return False
        if not n_total and not n_app:
            self.exported.emit(
                "info", "这个范围还没有记录可导出（每满 1 分钟记一条，先运行一会儿再试）")
            return False
        self.exported.emit(
            "success", f"已导出 {n_total + n_app} 行（本机 {n_total} + 软件 {n_app}）→ {path}")
        return True

    def refresh(self) -> None:
        now = time.time()
        self._refresh_summary(now)
        since = self._range_since(now)
        if self._range == "24h":
            bucket = 3600
            fmt_ts = lambda ts: time.strftime("%H:00", time.localtime(ts))
            self.chart.set_empty_text(
                "最近 24 小时还没有记录（每满 1 分钟记一条，运行满 1 分钟后出现）")
        else:
            bucket = 86400
            fmt_ts = lambda ts: time.strftime("%m-%d", time.localtime(ts))
            self.chart.set_empty_text("最近 7 天还没有记录（每满 1 分钟记一条）")
        rows = self.store.query_total_buckets(since, bucket)
        bars = [(fmt_ts(ts), down, up) for ts, up, down in rows]
        self.chart.set_data(bars)

        rank = self.store.query_app_rank(since, top=10)
        if rank:
            lines = []
            for app, up, down, conns in rank:
                lines.append(f"· {app}    ↓{_fmt(down)}  ↑{_fmt(up)}")
            self.rank_area.setText("\n".join(lines))
        else:
            self.rank_area.setText(
                "还没有软件流量记录：本机流量见上方柱状图；软件代理流量需要 Clash 在线才会记录。")

    def _refresh_summary(self, now: float) -> None:
        """今日（本地 0 点起）/ 本周（周一 0 点起）/ 本月（自然月 1 号起）。"""
        lt = time.localtime(now)
        day_start = now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
        week_start = day_start - lt.tm_wday * 86400          # tm_wday: Monday=0
        month_start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
        for key, since in (("today", day_start), ("week", week_start),
                           ("month", month_start)):
            up, down = self.store.query_total_since(int(since))
            self._sum_labels[key].setText(f"↓{_fmt(down)}  ↑{_fmt(up)}")

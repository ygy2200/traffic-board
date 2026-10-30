# -*- coding: utf-8 -*-
"""软件排行列表 v4：增量更新（消除每秒全量重建导致的滚动卡顿）。

核心改动：行 widget 按应用键（proc_name，v1.6 起兼容 TUN 单源无 pid）键控复用，
每秒只 setText 变化的字段；顺序未变时不做任何布局操作；差集行才创建/销毁。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QFrame, QSizePolicy,
                               QVBoxLayout, QWidget)
from qfluentwidgets import ScrollArea, isDarkTheme


def _fmt_bytes(n: int) -> str:
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _fmt_rate(v: float) -> str:
    """B/s → '1.2MB/s'（速率榜行内显示用）。"""
    v = float(v or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f}{unit}/s" if unit == "B" else f"{v:.1f}{unit}/s"
        v /= 1024.0
    return f"{v:.1f}GB/s"


class RankRow(QWidget):
    clicked_key = Signal(str)

    def __init__(self, app, share: float, metric: str, selected: bool,
                 value: str = "", parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._display = app.display
        self._metric = metric
        self._share = max(0.03, share)
        self._selected = selected
        self._dark = isDarkTheme()   # 构造时缓存，不再每帧查询
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
        self.lbl_name.setStyleSheet("color: rgb(245,245,245); background: transparent;")
        # 名字可以缩到任意宽度（不够就省略号）：否则一行长名字会把整个看板的最小宽度顶死
        self.lbl_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_name.setToolTip(app.display)
        self.lbl_metric = QLabel(metric)
        f2 = QFont(); f2.setPointSizeF(8.5)
        self.lbl_metric.setFont(f2)
        self.lbl_metric.setStyleSheet("color: rgb(175,175,175); background: transparent;")
        self.lbl_metric.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_metric.setToolTip(metric)
        left.addWidget(self.lbl_name)
        left.addWidget(self.lbl_metric)
        layout.addLayout(left, 1)

        self.lbl_value = QLabel(value)
        f3 = QFont(); f3.setPointSizeF(10); f3.setBold(True)
        self.lbl_value.setFont(f3)
        self.lbl_value.setStyleSheet("color: rgb(255,255,255); background: transparent;")
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.lbl_value)

        # 关键：所有子 QLabel 对鼠标透明，点击必然命中 RankRow 自身（修复"点了没反应"）
        for lbl in (self.lbl_name, self.lbl_metric, self.lbl_value):
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        for lbl in (self.lbl_name, self.lbl_metric):
            lbl.installEventFilter(self)

    def key(self) -> str:
        return self._app.key

    def update_data(self, app, share: float, metric: str, selected: bool,
                    value: str = "") -> None:
        """秒级增量更新：只改真正变化的字段。"""
        self._app = app
        if self._share != share:
            self._share = share
            self.update()
        if self._selected != selected:
            self._selected = selected
            self.update()
        if self._display != app.display:
            self._set_display(app.display)
        if self._metric != metric:
            self._set_metric(metric)
        if self.lbl_value.text() != value:
            self.lbl_value.setText(value)

    def _set_display(self, display: str) -> None:
        self._display = display
        self.lbl_name.setToolTip(display)
        self._elide(self.lbl_name)

    def _set_metric(self, metric: str) -> None:
        self._metric = metric
        self.lbl_metric.setToolTip(metric)
        self._elide(self.lbl_metric)

    def _elide(self, lbl: QLabel) -> None:
        """放不下就省略号，整段文字在悬浮提示里——否则行里的长文本会顶死整个看板的最小宽度。"""
        full = self._display if lbl is self.lbl_name else self._metric
        avail = lbl.width()
        txt = full if avail <= 0 else QFontMetrics(lbl.font()).elidedText(
            full, Qt.TextElideMode.ElideRight, avail)
        if lbl.text() != txt:
            lbl.setText(txt)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 子标签的最终宽度由行布局在本行 resize 之后才定下来，只看本行的 resizeEvent 会拿到旧值
        if event.type() == QEvent.Type.Resize and obj in (self.lbl_name, self.lbl_metric):
            self._elide(obj)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide(self.lbl_name)
        self._elide(self.lbl_metric)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        row_bg = QColor(43, 43, 43) if self._dark else QColor(248, 248, 248)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(row_bg)
        p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), 8, 8)
        if self._selected:
            p.setPen(QPen(QColor(33, 150, 243, 200), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), 8, 8)
        bar_y, bar_h = h - 8, 4
        bar_w = (w - 24) * min(1.0, self._share)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26) if self._dark else QColor(0, 0, 0, 26))
        p.drawRoundedRect(QRectF(12, bar_y, w - 24, bar_h), 2, 2)
        p.setBrush(QColor(33, 150, 243, 210))
        p.drawRoundedRect(QRectF(12, bar_y, bar_w, bar_h), 2, 2)


class RankList(QWidget):
    row_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.container = QWidget()
        self.v = QVBoxLayout(self.container)
        self.v.setContentsMargins(0, 0, 8, 0)
        self.v.setSpacing(3)
        self.v.addStretch(1)
        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.enableTransparentBackground()
        self.scroll.setWidget(self.container)
        v.addWidget(self.scroll)
        self._rows_by_key: dict[str, RankRow] = {}
        self._hint: QLabel | None = None
        # 点击处理装在 viewport 的事件过滤器上：真机鼠标事件必经 viewport，
        # 不依赖子类 mousePressEvent 的分发行为
        self.scroll.viewport().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.scroll.viewport() and event.type() == event.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = self.container.mapFrom(self.scroll.viewport(), event.position().toPoint())
                w = self.container.childAt(pos)
                while w is not None and not isinstance(w, RankRow):
                    w = w.parentWidget()
                if isinstance(w, RankRow):
                    self.row_clicked.emit(w.key())
                    return True
        return super().eventFilter(obj, event)

    def render(self, apps, metric_mode: str, selected_key, keyword: str,
               sort_mode: str = "cum") -> None:
        """渲染排行。sort_mode: 'cum'=按累计流量（默认）| 'rate'=按当前速率。

        两种模式的列语义互补（R5 新增，对标同类产品的"谁在跑量"视图）：
        - cum：数值=本次运行累计字节，副行=代理/直连连接数
        - rate：数值=当前速率（EMA 平滑），副行=累计字节 + 连接数
        """
        kw = keyword.strip().lower()
        apps = [a for a in apps if not kw or kw in a.display.lower() or kw in a.proc_name.lower()
                or any(kw in c.target.lower() for c in a.conns)]
        # 速率榜仅在 Clash 在线（有字节数据源）时生效；离线时速率全 0，
        # 强行按速率排只会得到乱序，回落连接数排序
        rate_mode = sort_mode == "rate" and metric_mode == "bytes"
        if metric_mode == "bytes" and not rate_mode:
            apps = sorted(apps, key=lambda a: -(a.download + a.upload))
            shares = self._shares(apps, key=lambda a: a.download + a.upload)
        elif rate_mode:
            apps = sorted(apps, key=lambda a: -(a.rate_down + a.rate_up))
            shares = self._shares(apps, key=lambda a: a.rate_down + a.rate_up)
        else:
            apps = sorted(apps, key=lambda a: -len(a.conns))
            shares = self._shares(apps, key=lambda a: len(a.conns))

        # ---- 差集：删除消失的行 ----
        keep = {a.key for a in apps}
        for k in [x for x in self._rows_by_key if x not in keep]:
            row = self._rows_by_key.pop(k)
            self.v.removeWidget(row)
            row.deleteLater()
        self._set_hint(None)

        # ---- 复用/创建 + 增量更新 ----
        target_widgets = []
        for a in apps:
            if rate_mode:
                metric = (f"累计 ↓{_fmt_bytes(a.download)} ↑{_fmt_bytes(a.upload)}"
                          f" · 代理 {a.proxy_count} · 直连 {a.direct_count}")
                if a.rate_down or a.rate_up:
                    value = f"↓{_fmt_rate(a.rate_down)} ↑{_fmt_rate(a.rate_up)}"
                elif a.download or a.upload:
                    value = f"累计 ↓{_fmt_bytes(a.download)}"
                else:
                    value = f"{len(a.conns)} 条"
            else:
                metric = f"代理 {a.proxy_count} · 直连 {a.direct_count}" if a.proxy_count \
                    else f"连接 {len(a.conns)}"
                value = (f"↓{_fmt_bytes(a.download)} ↑{_fmt_bytes(a.upload)}"
                         if (a.download or a.upload) else f"{len(a.conns)} 条")
            row = self._rows_by_key.get(a.key)
            if row is None:
                row = RankRow(a, shares.get(a.key, 0.03), metric, selected=(selected_key == a.key),
                              value=value)
                row.clicked_key.connect(self.row_clicked.emit)
                self._rows_by_key[a.key] = row
            else:
                row.update_data(a, shares.get(a.key, 0.03), metric,
                                selected=(selected_key == a.key), value=value)
            target_widgets.append(row)

        # ---- 顺序：仅在目标顺序与当前不同时重排（避免每秒布局抖动）----
        current = [self.v.itemAt(i).widget() for i in range(self.v.count())
                   if self.v.itemAt(i).widget() is not None]
        if current != target_widgets:
            for w in target_widgets:
                self.v.removeWidget(w)
            for i, w in enumerate(target_widgets):
                self.v.insertWidget(i, w)

        if not apps:
            self._set_hint("没有匹配的软件")

    def _set_hint(self, text: str | None) -> None:
        if self._hint is not None:
            if text is None:
                self.v.removeWidget(self._hint)
                self._hint.deleteLater()
                self._hint = None
            else:
                self._hint.setText(text)
            return
        if text is not None:
            self._hint = QLabel(text)
            self._hint.setStyleSheet("color: gray; padding: 12px; background: transparent;")
            self.v.insertWidget(0, self._hint)

    @staticmethod
    def _shares(apps, key) -> dict:
        vals = {a.key: key(a) for a in apps}
        total = sum(vals.values())
        if total <= 0:
            return {k: 0.03 for k in vals}
        return {k: v / total for k, v in vals.items()}

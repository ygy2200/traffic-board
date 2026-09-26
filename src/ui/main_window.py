# -*- coding: utf-8 -*-
"""流量看板主界面 v2：紧凑树形表。

- 一行一个软件（收纳），点击展开看它的连接明细
- 连接目标自动加中文注释（谷歌 (google.com)）
- 低价值的本地/UDP 端口连接合并为一行，不再刷屏
- 轮询在后台 QThread（qfluentwidgets 耗时操作禁主线程）；退出 os._exit 兜底
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QVBoxLayout, QWidget, QHeaderView,
)

from qfluentwidgets import (
    BodyLabel, FluentWindow, FluentIcon, SearchLineEdit, TreeWidget,
    CaptionLabel, themeColor,
)

from core.aggregator import Aggregator, Board, CHANNEL_LABEL
from core.domains_cn import annotate


class PollWorker(QThread):
    board_ready = Signal(object)

    def __init__(self, agg: Aggregator, interval_ms: int = 1000) -> None:
        super().__init__()
        self._agg = agg
        self._interval = interval_ms
        self._running = True

    def run(self) -> None:
        import time
        while self._running:
            try:
                self.board_ready.emit(self._agg.build())
            except Exception:
                pass
            time.sleep(self._interval / 1000.0)

    def stop(self) -> None:
        self._running = False


def _fmt_bytes(n: int) -> str:
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _channel_text(cv) -> str:
    if cv.channel == "proxy":
        return f"代理 · {cv.node}" if cv.node else "代理"
    if cv.channel == "direct":
        return "直连"
    return "本地/内网"


class MainWindow(FluentWindow):
    def __init__(self, agg: Aggregator) -> None:
        super().__init__()
        self.setWindowTitle("流量看板")
        self.resize(900, 640)
        self.agg = agg
        self._board = Board()
        self._expanded: set[int] = set()   # 用户点开的软件 pid，跨刷新保留

        page = QWidget()
        page.setObjectName("treePage")
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)

        # ---- 顶部一行：标题 + 搜索 + 状态 ----
        top = QHBoxLayout()
        title = BodyLabel("流量看板")
        f = title.font(); f.setPointSizeF(13); f.setBold(True); title.setFont(f)
        top.addWidget(title)
        top.addSpacing(6)
        self.search = SearchLineEdit(page)
        self.search.setPlaceholderText("搜软件 / 网站 / IP，回车展开结果")
        self.search.setFixedWidth(280)
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search, 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch(1)
        self.status_label = CaptionLabel(page)
        top.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(top)

        # ---- 提示行 ----
        self.hint = CaptionLabel(page)
        self.hint.setText("点击软件名展开它的连接明细；代理=蓝色，直连=绿色，灰色为无信息量的本地连接")
        self.hint.setTextColor(QColor(130, 130, 130), QColor(120, 120, 120))
        v.addWidget(self.hint)

        # ---- 树形表 ----
        self.tree = TreeWidget(page)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["软件 / 正在访问", "渠道", "下载", "上传"])
        self.tree.setRootIsDecorated(True)
        self.tree.setEditTriggers(self.tree.EditTrigger.NoEditTriggers)
        self.tree.setBorderVisible(True)
        self.tree.setBorderRadius(8)
        self.tree.setWordWrap(False)
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.itemCollapsed.connect(self._on_collapse)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.tree, 1)

        self.addSubInterface(page, FluentIcon.GLOBE, "流量")

        self.worker = PollWorker(agg)
        self.worker.board_ready.connect(self._on_board, Qt.ConnectionType.QueuedConnection)
        self.worker.start()
        self.search.textChanged.connect(self._render)

    # ---- 事件 ----
    def _on_expand(self, item):
        pid = item.data(0, Qt.ItemDataRole.UserRole)
        if pid is not None:
            self._expanded.add(pid)

    def _on_collapse(self, item):
        pid = item.data(0, Qt.ItemDataRole.UserRole)
        if pid is not None:
            self._expanded.discard(pid)

    def _on_board(self, board) -> None:
        self._board = board
        self._render()

    def _render(self) -> None:
        b = self._board
        kw = self.search.text().strip().lower()

        # 状态角标
        if b.clash_online:
            self.status_label.setText(f"● Clash 已连接 v{b.clash_version}")
            self.status_label.setTextColor(QColor("#2e7d32"), QColor("#4caf50"))
        else:
            self.status_label.setText("● Clash 未连接（纯系统视角，功能正常）")
            self.status_label.setTextColor(QColor(150, 150, 150), QColor(120, 120, 120))

        apps = [a for a in b.apps
                if not kw or kw in a.display.lower() or kw in a.proc_name.lower()
                or any(kw in c.target.lower() for c in a.conns)]

        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        gray = QColor(140, 140, 140)
        green = QColor("#2e7d32")
        blue = QColor(themeColor().name())

        for a in apps:
            head = QApplication.translate("tb", "") or ""
            summary_parts = []
            if a.proxy_count:
                summary_parts.append(f"代理 {a.proxy_count}")
            if a.direct_count:
                summary_parts.append(f"直连 {a.direct_count}")
            summary = " · ".join(summary_parts) if summary_parts else "无对外连接"

            parent = self._mk_row(
                f"{a.display}  ({a.proc_name})",
                summary,
                _fmt_bytes(a.download) if a.download else "—",
                _fmt_bytes(a.upload) if a.upload else "—",
                bold=True,
            )
            parent.setData(0, Qt.ItemDataRole.UserRole, a.pid)

            local_count = 0
            shown = 0
            for cv in a.conns:
                if cv.channel == "local":
                    local_count += 1
                    continue
                node_txt = _channel_text(cv)
                child = self._mk_row(annotate(cv.target), node_txt,
                                     _fmt_bytes(cv.download), _fmt_bytes(cv.upload))
                self._color_item(child, 1, blue if cv.channel == "proxy" else green)
                parent.addChild(child)
                shown += 1
                if shown >= 40:  # 单软件防护：极端情况不无限铺
                    more = self._mk_row("… 连接过多，已省略其余", "", "", "")
                    more.setDisabled(True)
                    parent.addChild(more)
                    break
            if local_count:
                local_row = self._mk_row(f"本地/内网连接 {local_count} 条（无外网流量，已收纳）",
                                         "本地", "—", "—")
                self._color_item(local_row, 0, gray)
                self._color_item(local_row, 1, gray)
                parent.addChild(local_row)
            if not a.conns:
                empty = self._mk_row("暂无活动连接", "", "", "")
                self._color_item(empty, 0, gray)
                parent.addChild(empty)

            self.tree.addTopLevelItem(parent)
            # 搜索时自动展开命中项；否则恢复用户手动展开的状态
            if kw:
                parent.setExpanded(True)
            elif a.pid in self._expanded:
                parent.setExpanded(True)
        self.tree.setUpdatesEnabled(True)

    @staticmethod
    def _mk_row(c0: str, c1: str, c2: str, c3: str, bold: bool = False):
        from PySide6.QtWidgets import QTreeWidgetItem
        it = QTreeWidgetItem([c0, c1, c2, c3])
        if bold:
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
        return it

    @staticmethod
    def _color_item(item, col: int, color: QColor) -> None:
        item.setForeground(col, color)

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

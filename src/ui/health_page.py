# -*- coding: utf-8 -*-
"""网络体检页：10 项检查可视化（fail/warn 置顶 + 证据展开 + 修复指引）。

体检是一次性动作：进页自动跑一次 + 手动按钮。引擎在 QThread 里执行
（耗时操作禁主线程，qfw 铁律），整体自带 14s 截止；离开页面不中断，
晚到的报告若页面已隐藏则忽略渲染。worker 用完即弃（deleteLater 防堆积）。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, FluentIcon,
                            PrimaryPushButton, ScrollArea, SubtitleLabel)

from core.health import SEV_FAIL, SEV_INFO, SEV_OK, SEV_WARN, HealthEngine, HealthReport

_SEV_COLOR = {SEV_FAIL: "#e53935", SEV_WARN: "#fb8c00",
              SEV_OK: "#43a047", SEV_INFO: "#90a4af"}
_SEV_TEXT = {SEV_FAIL: "严重", SEV_WARN: "警告", SEV_OK: "正常", SEV_INFO: "提示"}
_BANNER_BG = {SEV_FAIL: "rgb(122,36,36)", SEV_WARN: "rgb(122,84,24)",
              SEV_OK: "rgb(36,92,48)", SEV_INFO: "rgb(64,64,64)"}


class CheckWorker(QThread):
    report_ready = Signal(object)

    def __init__(self, agg, parent=None) -> None:
        super().__init__(parent)
        self._agg = agg

    def run(self) -> None:  # noqa: N802
        rep = HealthEngine(self._agg).run_all()
        self.report_ready.emit(rep)


class HealthPage(QWidget):
    def __init__(self, agg, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("healthPage")
        self._agg = agg
        self._worker = None
        self._auto_done = False
        self._last_report = None
        self._row_widgets: list = []

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("网络体检"))
        top.addStretch(1)
        self.lbl_status = CaptionLabel("进入页面自动体检一次")
        self.lbl_status.setWordWrap(True)   # 不换行会把整页最小宽度顶到状态文字那么长（R12）
        self.lbl_status.setStyleSheet("color: rgb(160,160,160);")
        top.addWidget(self.lbl_status)
        self.btn_copy = QPushButton("复制报告")
        self.btn_copy.setFlat(True)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet("color: rgb(150,150,150); border: none;")
        self.btn_copy.clicked.connect(self._copy_report)
        self.btn_copy.hide()
        top.addWidget(self.btn_copy)
        self.btn_run = PrimaryPushButton("开始体检")
        self.btn_run.setIcon(FluentIcon.PLAY)
        self.btn_run.clicked.connect(self._start)
        top.addWidget(self.btn_run)
        v.addLayout(top)

        self.banner = BodyLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("border-radius: 8px; padding: 10px 14px;"
                                  "color: white; background: rgb(64,64,64);")
        self.banner.hide()
        v.addWidget(self.banner)

        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.enableTransparentBackground()
        self.container = QWidget()
        self.cards_v = QVBoxLayout(self.container)
        self.cards_v.setContentsMargins(0, 0, 8, 0)
        self.cards_v.setSpacing(6)
        self.cards_v.addStretch(1)
        self.scroll.setWidget(self.container)
        v.addWidget(self.scroll, 1)

        self.lbl_mode = CaptionLabel("")
        self.lbl_mode.setWordWrap(True)     # 同上：数据源那行很长，不换行会顶住窗口最小宽度
        self.lbl_mode.setStyleSheet("color: rgb(140,140,140);")
        v.addWidget(self.lbl_mode)

    # ---- 生命周期 ----
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self._auto_done and self._worker is None:
            self._auto_done = True
            self._start()

    def _start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_run.setEnabled(False)
        self.btn_copy.hide()
        self.lbl_status.setText("体检中…（最长 14 秒）")
        self.banner.hide()
        self._worker = CheckWorker(self._agg)
        self._worker.report_ready.connect(self._on_report)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self) -> None:
        self.btn_run.setEnabled(True)
        self.lbl_status.setText(f"上次体检：{time.strftime('%H:%M:%S')}")
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def set_mode(self, data_mode: str, tun_state: str) -> None:
        """由主窗口每秒喂当前数据源模式。"""
        mode_text = ("数据源：TUN 直连单源（全部流量覆盖，含直连应用）" if data_mode == "tun"
                     else "数据源：传统双源（排行覆盖走代理的流量；开 TUN 可覆盖全部）")
        self.lbl_mode.setText(f"{mode_text} · TUN 判定={tun_state}")

    def _copy_report(self) -> None:
        if self._last_report is None:
            return
        lines = [f"流量看板网络体检 {time.strftime('%Y-%m-%d %H:%M:%S')}  最差={self._last_report.worst()}"]
        for it in self._last_report.items:
            lines.append(f"[{it.severity}] {it.name}: {it.detail}")
            if it.fix:
                lines.append(f"    建议：{it.fix}")
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(lines))
        self.btn_copy.setText("已复制 ✓")

    # ---- 渲染 ----
    def _on_report(self, rep) -> None:
        if not isinstance(rep, HealthReport):
            return
        self._last_report = rep
        if not self.isVisible():
            return   # 离开页面后晚到的报告：忽略（引擎已自带截止）
        self._render(rep)

    def _render(self, rep: HealthReport) -> None:
        self.btn_copy.show()
        # 清空旧卡片
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets = []

        worst = rep.worst()
        n_fail = sum(1 for it in rep.items if it.severity == SEV_FAIL)
        n_warn = sum(1 for it in rep.items if it.severity == SEV_WARN)
        if n_fail:
            first = next(it for it in rep.items if it.severity == SEV_FAIL)
            self.banner.setText(f"发现 {n_fail} 个严重问题：{first.detail}"
                                + (f"　→　{first.fix}" if first.fix else ""))
        elif n_warn:
            first = next(it for it in rep.items if it.severity == SEV_WARN)
            self.banner.setText(f"{n_warn} 项警告：{first.detail}")
        else:
            self.banner.setText("网络环境健康，未发现问题。")
        self.banner.setStyleSheet(f"border-radius: 8px; padding: 10px 14px;"
                                  f"color: white; background: {_BANNER_BG[worst]};")
        self.banner.show()

        for it in rep.items:
            self._row_widgets.append(self._make_card(it))
        # 逐个插入（stretch 保持在末尾）
        for i, w in enumerate(self._row_widgets):
            self.cards_v.insertWidget(i, w)

    def _make_card(self, it) -> QWidget:
        card = CardWidget(self)
        c = QVBoxLayout(card)
        c.setContentsMargins(14, 10, 14, 10)
        c.setSpacing(4)

        row = QHBoxLayout()
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"border-radius: 6px; background: {_SEV_COLOR[it.severity]};")
        name = BodyLabel(f"{it.name}　{_SEV_TEXT[it.severity]}")
        name.setStyleSheet(f"color: {_SEV_COLOR[it.severity]};")
        detail = CaptionLabel(it.detail)
        detail.setWordWrap(True)
        detail.setStyleSheet("color: rgb(200,200,200);")
        row.addWidget(dot)
        row.addWidget(name)
        row.addStretch(1)
        ev_text = None
        if it.evidence:
            ev_text = CaptionLabel(it.evidence)
            ev_text.setWordWrap(True)
            ev_text.setStyleSheet("color: rgb(150,150,150);")
            ev_text.hide()
            ev_btn = QPushButton("详情")
            ev_btn.setFlat(True)
            ev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ev_btn.setStyleSheet("color: rgb(150,150,150); border: none;")
            ev_btn.clicked.connect(
                lambda: ev_text.setVisible(not ev_text.isVisible()))
            row.addWidget(ev_btn)
        c.addLayout(row)
        c.addWidget(detail)

        if it.fix:
            fix = CaptionLabel(f"建议：{it.fix}")
            fix.setWordWrap(True)
            fix.setStyleSheet("color: rgb(255,213,79);")
            c.addWidget(fix)

        if ev_text is not None:
            c.addWidget(ev_text)

        return card

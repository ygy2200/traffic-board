# -*- coding: utf-8 -*-
"""流量看板 v1.2 主窗口：波形 + 软件排行 + 点击下钻明细（GlassWire 式三段布局，深色主题）。"""
from __future__ import annotations

import os
import sys
import time

import psutil
from PySide6.QtCore import QSettings, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout,
    QWidget, QSplitter, QSizePolicy,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, FluentWindow, FluentIcon, SearchLineEdit,
    SubtitleLabel, TableWidget, Theme, setTheme,
)

from core.aggregator import Aggregator, Board, CHANNEL_LABEL
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
        self._acc_down = 0.0     # 全机字节累计（60 秒落库）
        self._acc_up = 0.0
        self._acc_n = 0
        self._last_app_total: dict = {}   # 软件代理字节累计快照（差分用）
        self._tick = 0

    def run(self) -> None:
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
                        self._acc_down += down_bps * dt
                        self._acc_up += up_bps * dt
                        clash = self._agg.clash.snapshot()
                        pd = pu = 0.0
                        online = clash.online
                        if online and last_clash is not None:
                            pd = max(0.0, (clash.download_total - last_clash[1]) / dt)
                            pu = max(0.0, (clash.upload_total - last_clash[0]) / dt)
                        self.wave_ready.emit(down_bps, up_bps, pd, pu, online)
                        last_nic_t = time.time()
                        if online:
                            last_clash = (clash.upload_total, clash.download_total)
                else:
                    last_nic_t = time.time()
                last_nic = (dn, up)
            except Exception:
                pass
            self._tick += 1
            if self._tick % self._board_ticks == 0:
                try:
                    self.board_ready.emit(self._agg.build())
                except Exception:
                    pass
            # 每 60 秒历史落库
            if self._tick % 240 == 0:
                self._flush_history()
            rest = self._wave_ms / 1000.0 - (time.time() - loop_t0)
            if rest > 0:
                time.sleep(rest)

    def _flush_history(self) -> None:
        try:
            self._agg.known.flush()
            clash = self._agg.clash.snapshot()
            per_app: dict = {}
            if clash.online:
                cur: dict = {}
                for c in clash.conns:
                    proc = c.process or "未知进程"
                    u, d = cur.get(proc, (0, 0))
                    cur[proc] = (u + c.upload, d + c.download)
                for proc, (u, d) in cur.items():
                    lu, ld = self._last_app_total.get(proc, (0, 0))
                    per_app[proc] = (max(0, u - lu), max(0, d - ld), 1)
                self._last_app_total = cur
            self._agg.history.write_tick(int(time.time()), per_app,
                                         int(self._acc_up), int(self._acc_down))
        except Exception:
            pass
        self._acc_down = 0.0
        self._acc_up = 0.0

    def stop(self) -> None:
        self._running = False


def _channel(cv) -> str:
    if cv.channel == "proxy":
        return f"代理 · {cv.node}" if cv.node else "代理"
    if cv.channel == "direct":
        return "直连"
    return "本地/内网"


def _hard_exit(code: int = 0) -> None:
    """Windows 硬杀自进程。os._exit 在 Windows 走 CRT exit()，仍执行 DLL
    detach 等运行时清理，会撞上采样线程残留的 Qt 线程存储导致段错误
    （实测约 1/3 复现）；TerminateProcess 跳过一切清理，立即终止。"""
    try:
        import ctypes
        ctypes.windll.kernel32.TerminateProcess(
            ctypes.windll.kernel32.GetCurrentProcess(), code)
    except Exception:
        os._exit(code)


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
        self._alert_ts: dict = {}   # 每进程上次提醒时间（陌生连接限流）

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
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._detail_menu)
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

        # ---- 帮助页 ----
        from ui.help_page import HelpPage
        self.page_help = HelpPage()
        self.addSubInterface(self.page_help, FluentIcon.QUESTION, "帮助")

        # ---- 设置页 ----
        from ui.settings_page import SettingsPage
        self.page_settings = SettingsPage()
        self.addSubInterface(self.page_settings, FluentIcon.SETTING, "设置")

        # ---- 历史页 ----
        from ui.history_page import HistoryPage
        self.page_history = HistoryPage(self.agg.history)
        self.addSubInterface(self.page_history, FluentIcon.HISTORY, "历史")
        self._hist_timer = QTimer(self)
        self._hist_timer.timeout.connect(self.page_history.refresh)
        self._hist_timer.start(60000)

        self._wave_buf: list = []
        self._current_detail_rows: list = []
        self._tray_exit = False
        self.alert_enabled = True
        self._settings = QSettings("TrafficBoard", "TrafficBoard")
        self.exit_on_close = self._settings.value("close/exit", "0") in ("1", "true", True)
        self.badge_enabled = self._settings.value("badge/enabled", "1") in ("1", "true", True)
        self.badge_topmost = self._settings.value("badge/topmost", "1") in ("1", "true", True)
        self.worker = PollWorker(agg)
        self.worker.board_ready.connect(self._on_board, Qt.ConnectionType.QueuedConnection)
        self.worker.wave_ready.connect(self._on_wave, Qt.ConnectionType.QueuedConnection)
        self.worker.start()

        # ---- 托盘 + 悬浮速率条 ----
        self._init_tray()
        self._init_badge()

    # ---- 托盘 ----
    def _init_tray(self) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon, QMenu
        from PySide6.QtGui import QIcon
        from core.tools import app_icon_path
        icon_path = app_icon_path()
        self.tray = QSystemTrayIcon(QIcon(icon_path) if icon_path else self.windowIcon(), self)
        menu = QMenu()
        act_show = menu.addAction("显示主界面")
        act_help = menu.addAction("使用帮助")
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_show.triggered.connect(self._show_main)
        act_help.triggered.connect(lambda: (self.show(), self.switchTo(self.page_help)))
        act_quit.triggered.connect(self._quit_app)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("流量看板")
        self.tray.activated.connect(
            lambda reason: self._show_main() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def _init_badge(self) -> None:
        from ui.speed_badge import SpeedBadge
        self.badge = SpeedBadge()
        self.badge.double_clicked.connect(self._show_main)
        self.badge.quit_requested.connect(self._quit_app)
        self.badge.move(100, 100)
        self.badge.set_topmost(self.badge_topmost)
        if not self.badge_enabled:
            self.badge.hide()
        else:
            self.badge.show()

    # ---- 设置联动（SettingsPage 经 window() 调用）----
    def set_badge_visible(self, on: bool) -> None:
        self.badge_enabled = on
        if on:
            self.badge.show()
        else:
            self.badge.hide()

    def set_badge_topmost(self, on: bool) -> None:
        self.badge_topmost = on
        self.badge.set_topmost(on)

    def set_exit_on_close(self, on: bool) -> None:
        self.exit_on_close = on

    def _show_main(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_app(self) -> None:
        self._tray_exit = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        # 关窗行为由设置决定：默认最小化到托盘；开启"直接退出"或托盘菜单退出时真退
        if not self._tray_exit and not self.exit_on_close:
            from PySide6.QtWidgets import QSystemTrayIcon
            event.ignore()
            self.hide()
            self.tray.showMessage("流量看板", "已最小化到托盘，双击托盘图标可重新打开。",
                                  QSystemTrayIcon.MessageIcon.Information, 2000)
            return
        try:
            self.tray.hide()
            self.badge.close()
            self.worker.stop()
            if not self.worker.wait(5000):
                # 采样线程还活着（psutil 偶发慢查询/写库卡住）：
                # 此时关 SQLite 会与它并发写同一连接导致崩溃，直接终止进程
                _hard_exit(0)
            self.agg.stop()
        finally:
            event.accept()
            _hard_exit(0)

    def _detail_menu(self, pos) -> None:
        """明细表右键菜单：复制 / Wireshark 抓包 / 断开代理连接 / 导出 CSV。"""
        from PySide6.QtWidgets import QMenu, QApplication, QFileDialog
        from core.tools import find_wireshark, export_connections_csv
        import subprocess as _sp

        row = self.table.rowAt(int(pos.y()))
        cv = self._current_detail_rows[row] if 0 <= row < len(self._current_detail_rows) else None
        # 抓包目标：优先 IP，无 IP（Clash 只有域名的连接）用域名过滤
        ws_target = ""
        ws_port = cv.raddr_port if cv else 0
        if cv is not None:
            if cv.raddr_ip:
                ws_target = cv.raddr_ip
            else:
                host_part = cv.target.split(":")[0] if cv.target else ""
                if host_part and "/" not in host_part and "（" not in host_part:
                    ws_target = host_part
        menu = QMenu(self)
        ws_path = find_wireshark()
        act_copy = menu.addAction("复制目标地址")
        act_copy.setEnabled(cv is not None)
        # 抓包可用性：有目标且非本机回环（127.0.0.1:7890 代理入口行抓出来全是回环加密流，无意义）
        is_loopback_entry = bool(ws_target) and (ws_target.startswith("127.0.0.1") or ws_target == "::1")
        act_ws = menu.addAction("用 Wireshark 抓这条连接")
        act_ws.setEnabled(cv is not None and bool(ws_path) and bool(ws_target) and not is_loopback_entry)
        if cv is not None and is_loopback_entry:
            hint = menu.addAction("（代理入口连接：抓它只会看到回环加密流）")
            hint.setEnabled(False)
        act_kill = menu.addAction("断开此代理连接")
        act_kill.setEnabled(cv is not None and bool(cv.conn_id))
        menu.addSeparator()
        act_csv = menu.addAction("导出全部连接为 CSV…")
        chosen = menu.exec(self.table.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_copy and cv is not None:
            QApplication.clipboard().setText(cv.target)
        elif chosen is act_ws and cv is not None:
            if cv.raddr_ip:
                filt = f"host {ws_target} and port {ws_port}" if ws_port else f"host {ws_target}"
            else:
                filt = f"host {ws_target}"
            _sp.Popen([ws_path, "-f", filt])
        elif chosen is act_kill and cv is not None:
            self.agg.clash.close_connection(cv.conn_id)
        elif chosen is act_csv:
            path, _ = QFileDialog.getSaveFileName(self, "导出连接列表",
                                                  os.path.expanduser("~/Desktop/连接列表.csv"),
                                                  "CSV (*.csv)")
            if not path:
                return
            all_rows = []
            for a in self._board.apps:
                for c in a.conns:
                    all_rows.append((a.display, a.proc_name, c.target, CHANNEL_LABEL[c.channel],
                                     c.node, _fmt_bytes(c.download), _fmt_bytes(c.upload)))
            export_connections_csv(path, all_rows)

    def _on_wave(self, down_bps: float, up_bps: float, pd: float, pu: float, online: bool) -> None:
        """全机真实流量波形（直连+代理都含），代理量作数字标注。"""
        self.wave.set_mode_label("全机真实流量 · 字节/秒（含直连与代理）")
        self.wave.push_sample(down_bps, up_bps)
        # 速率数字防跳：攒 4 个采样（1 秒）取均值后刷新一次显示
        self._wave_buf.append((down_bps, up_bps, pd, pu))
        if len(self._wave_buf) < 4:
            return
        vals = [sum(col) / len(self._wave_buf) for col in zip(*self._wave_buf)]
        self._wave_buf = []
        down_avg, up_avg, pd_avg, pu_avg = vals
        proxy_txt = f"    ·    经代理 ↓ {_fmt_bytes(pd_avg)}/s  ↑ {_fmt_bytes(pu_avg)}/s" if online else ""
        self.rate.setText(f"全机  ↓ {_fmt_bytes(down_avg)}/s    ↑ {_fmt_bytes(up_avg)}/s{proxy_txt}")
        if self.badge_enabled:
            self.badge.set_speed(_fmt_bytes(down_avg) + "/s", _fmt_bytes(up_avg) + "/s")
        self._metric_mode = "bytes" if online else "count"
        self.rank_title.setText("软件排行（代理流量 · 本次运行累计）" if online else "软件排行（按连接数）")

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
        self._check_unknown(board)
        self._render()

    def _check_unknown(self, board: Board) -> None:
        """陌生连接检测：新 (进程, 目标) 对弹托盘通知（同进程每小时限 1 条）。"""
        if not getattr(self, "alert_enabled", True):
            return
        from PySide6.QtWidgets import QSystemTrayIcon
        pairs = set()
        for a in board.apps:
            for c in a.conns:
                if c.channel == "proxy":
                    pairs.add((a.proc_name, c.target))
        if not pairs:
            return
        new = self.agg.known.filter_new(pairs)
        if not new:
            return
        now = time.time()
        announced = set()
        for proc, target, _first_seen in new:
            last = self._alert_ts.get(proc, 0)
            if now - last < 3600 or proc in announced:
                continue
            announced.add(proc)
            self._alert_ts[proc] = now
            self.tray.showMessage("发现新的网络连接",
                                  f"{proc} 首次连接：{target}",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)

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
        self._current_detail_rows = rows
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


def run_app() -> int:
    app = QApplication(sys.argv)
    agg = Aggregator()
    w = MainWindow(agg)
    w.show()
    code = app.exec()
    _hard_exit(code)

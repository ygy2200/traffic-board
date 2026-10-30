# -*- coding: utf-8 -*-
"""流量看板 v1.2 主窗口：波形 + 软件排行 + 点击下钻明细（GlassWire 式三段布局，深色主题）。"""
from __future__ import annotations

import os
import sys
import time

import psutil
from PySide6.QtCore import QSettings, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QPushButton, QTableWidgetItem,
    QVBoxLayout, QWidget, QSplitter, QSizePolicy,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, FluentWindow, FluentIcon, InfoBar, SearchLineEdit,
    SubtitleLabel, TableWidget, Theme, setTheme,
)

from core.aggregator import Aggregator, Board, CHANNEL_LABEL
from core.domains_cn import annotate
from core.nics import physical_nics
from ui.wavegraph import WaveGraph
from ui.ranklist import RankList, _fmt_bytes


_WIDE_START = 0x2E80        # 这个码位之后基本是中日韩字与全角标点，可逐字断行
_SOFT_BREAK = "/\\-_.:;,?&%="  # 路径/URL/文件名里允许紧跟其后断行的分隔符


def _is_wide(ch: str) -> bool:
    return ord(ch) >= _WIDE_START


def _units(para: str) -> list:
    """切成断行单元：中日韩一个字一个单元，连续的 ASCII 是一整块。

    分成 ASCII 整块是关键——按字符断会把 C:\\...\\Downloads 折成
    "...Download" + "s\\traffic_detail..."，用户照着抄就少个 s。
    """
    out: list = []
    cur = ""
    for ch in para:
        if ch == " " or _is_wide(ch):
            if cur:
                out.append(cur)
                cur = ""
            out.append(ch)
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _split_long(fm: QFontMetrics, unit: str, px: int) -> list:
    """单个单元比行还宽（长路径、长 URL）：先在分隔符后断，断不开才逐字。"""
    pieces, cur = [], ""
    for i, ch in enumerate(unit):
        cur += ch
        # 数字之间的点不断：v1.6.3 断成 "v1.6." + "3" 比不断还难读
        if ch in _SOFT_BREAK and not (ch == "." and i + 1 < len(unit)
                                      and unit[i + 1].isdigit()):
            pieces.append(cur)
            cur = ""
    if cur:
        pieces.append(cur)

    merged: list = []
    line = ""
    for p in pieces:
        if line and fm.horizontalAdvance(line + p) > px:
            merged.append(line)
            line = p
        else:
            line += p
    if line:
        merged.append(line)

    out: list = []
    for m in merged:
        while fm.horizontalAdvance(m) > px and len(m) > 1:
            k = len(m)
            while k > 1 and fm.horizontalAdvance(m[:k]) > px:
                k -= 1
            out.append(m[:k])
            m = m[k:]
        if m:                       # 正好整除时别多吐一个空行
            out.append(m)
    return out


def _wrap_px(fm: QFontMetrics, text: str, px: int) -> str:
    """按真实像素宽度折行：中文逐字可断，ASCII 词（路径/URL/文件名）保持完整。"""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for unit in _units(para):
            if unit == " ":
                if cur:
                    cur += " "      # 行尾空格最后 rstrip 掉，不占宽度
                continue
            if fm.horizontalAdvance(unit) > px:
                for piece in _split_long(fm, unit, px):
                    if cur and fm.horizontalAdvance(cur + piece) > px:
                        lines.append(cur.rstrip())
                        cur = piece
                    else:
                        cur += piece
                continue
            if cur and fm.horizontalAdvance(cur + unit) > px:
                lines.append(cur.rstrip())
                cur = unit
            else:
                cur += unit
        lines.append(cur.rstrip())
    return "\n".join(lines)


class _ToastBar(InfoBar):
    """宽度永远不超过父窗口的前台提醒条。

    qfluentwidgets 的 _adjustText 按「9px 一个字符」估算折行，中文一个字实际 13~14px，
    于是一条普通提醒在 683 宽（Win11 半屏贴靠）的窗口里会撑到 769 宽、x=-43，
    左右两头各被切掉几十像素——用户看到的是提醒本身缺字。
    """

    def _adjustText(self) -> None:
        p = self.parent()
        if p is None:
            return super()._adjustText()
        limit = max(200, p.width() - 24)        # 整条提醒允许的宽度上限
        avail = max(150, min(600, limit - 154))  # 154 = 图标 + 内外边距 + 关闭按钮那一圈
        fm_title = QFontMetrics(self.titleLabel.font())
        fm_content = QFontMetrics(self.contentLabel.font())
        for _ in range(3):
            self.titleLabel.setText(_wrap_px(fm_title, self.title, avail))
            self.contentLabel.setText(_wrap_px(fm_content, self.content, avail))
            self.adjustSize()
            if self.width() <= limit:
                break
            # 样式表字体比 font() 拿到的大时一轮折不准，按超出的量再收一次
            avail = max(150, avail - (self.width() - limit))


class PollWorker(QThread):
    """后台复合循环：250ms 双采样（全机网卡流量 + Clash 代理总量），每 4 轮一次面板刷新。"""
    board_ready = Signal(object)
    wave_ready = Signal(float, float, float, float, bool)  # 全机down/up B/s, 代理down/up B/s, clash在线
    quota_alert = Signal(str, str)                          # (标题, 正文) 月流量上限告警（R7）

    def __init__(self, agg: Aggregator, wave_ms: int = 250, board_ticks: int = 4,
                 quota_cap_gb: float = 0.0, quota_reset_day: int = 1) -> None:
        super().__init__()
        self._agg = agg
        self._wave_ms = wave_ms
        self._board_ticks = board_ticks
        self._quota_cap_gb = float(quota_cap_gb or 0.0)
        self._quota_reset_day = int(quota_reset_day or 1)
        from core.quota import QuotaState
        self._quota_state = QuotaState()
        self._running = True
        self._acc_down = 0.0     # 全机字节累计（60 秒落库）
        self._acc_up = 0.0
        self._acc_n = 0
        self._sess_down = 0.0    # 本次运行全机累计（永不重置，状态栏展示）
        self._sess_up = 0.0
        self._tick = 0

    def run(self) -> None:
        last_nic = None
        last_nic_t = 0.0
        last_clash = None
        monitor_nics = None
        last_nic_refresh = 0.0
        while self._running:
            loop_t0 = time.time()
            try:
                # 网卡集合定期刷新（跟随 TUN 开关/网卡插拔）；TUN 虚拟网卡必须排除，
                # 否则流量在虚拟网卡+物理网卡双重计数，全机速率虚高约 2 倍（R3 修复）
                if monitor_nics is None or time.time() - last_nic_refresh > 10:
                    monitor_nics = physical_nics(psutil.net_if_stats())
                    last_nic_refresh = time.time()
                nics = psutil.net_io_counters(pernic=True)
                dn = sum(v.bytes_recv for k, v in nics.items() if k in monitor_nics)
                up = sum(v.bytes_sent for k, v in nics.items() if k in monitor_nics)
                if last_nic is not None:
                    dt = time.time() - last_nic_t
                    if dt >= 0.1:
                        down_bps = max(0.0, (dn - last_nic[0]) / dt)
                        up_bps = max(0.0, (up - last_nic[1]) / dt)
                        self._acc_down += down_bps * dt
                        self._acc_up += up_bps * dt
                        self._sess_down += down_bps * dt
                        self._sess_up += up_bps * dt
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
                    board = self._agg.build()
                    board.session_up = int(self._sess_up)
                    board.session_down = int(self._sess_down)
                    self.board_ready.emit(board)
                except Exception:
                    pass
            # 每 60 秒历史落库
            if self._tick % 240 == 0:
                self._flush_history()
                self._check_quota()
            rest = self._wave_ms / 1000.0 - (time.time() - loop_t0)
            if rest > 0:
                time.sleep(rest)

    def set_quota(self, cap_gb: float, reset_day: int) -> None:
        """设置页改月流量上限（下分钟落库时生效）。"""
        self._quota_cap_gb = float(cap_gb or 0.0)
        self._quota_reset_day = int(reset_day or 1)

    def _check_quota(self) -> None:
        """每分钟落库后检查套餐周期用量（R7：80% 预警 / 100% 超限，同周期同档不重复）。"""
        if self._quota_cap_gb <= 0:
            return
        try:
            from core.quota import month_window, next_alert
            now = time.time()
            start, _ = month_window(now, self._quota_reset_day)
            up, down = self._agg.history.query_proxy_total_since(int(start))
            alert = next_alert(self._quota_state, up + down,
                               int(self._quota_cap_gb * (1024 ** 3)),
                               now, self._quota_reset_day)
            if alert:
                level, msg = alert
                self.quota_alert.emit(
                    "流量接近套餐上限" if level == 1 else "流量已超套餐上限", msg)
        except Exception:
            pass

    def _flush_history(self) -> None:
        try:
            self._agg.known.flush()
            # 历史按软件差分改用 Aggregator 的累计增量（与实时排行同源）：
            # 旧实现按活跃连接快照求和做差，连接在两次落库间关闭会丢尾字节
            per_app: dict = {}
            for proc, (du, dd) in self._agg.proc_cum_deltas().items():
                per_app[proc] = (du, dd, 1)
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
    """Windows 硬杀自进程。os._exit 走 CRT exit()，仍执行 DLL detach 等运行时
    清理，会撞上采样线程残留的 Qt 线程存储导致段错误；TerminateProcess 跳过一切
    清理，立即终止。

    句柄必须按 c_void_p 传：不带 argtypes 时 ctypes 把伪句柄 -1 截成 0xFFFFFFFF，
    TerminateProcess 只返回 ERROR_INVALID_HANDLE(6) 而不抛异常——原写法因此从未
    真正终止过进程，退出总是落到解释器拆除阶段崩（实测退出码 0xC0000005，8 次中 6 次）。

    刷流必须在 TerminateProcess 之前：管道/重定向下 stdout 是块缓冲的，
    硬杀会把最后一段输出连同缓冲区一起丢掉（曾让冒烟脚本"静默成功"无从判断）。
    """
    import ctypes
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        if kernel32.TerminateProcess(ctypes.c_void_p(-1), code & 0xFFFFFFFF):
            return
    except Exception:
        pass
    os._exit(code)


class MainWindow(FluentWindow):
    def __init__(self, agg: Aggregator) -> None:
        super().__init__()
        setTheme(Theme.DARK)
        self.setWindowTitle("流量看板")
        self.resize(1000, 700)
        self.agg = agg
        self._board = Board()
        # 启动恢复"强制传统模式"开关（设置页持久化）
        if QSettings("TrafficBoard", "TrafficBoard").value(
                "forceLegacyMode", False, type=bool):
            agg.set_force_legacy(True)
        self._selected_key = ""
        self._last_bytes = None
        self._metric_mode = "bytes"
        self._rank_sort = "cum"                 # 排行依据：cum=累计 | rate=当前速率（R5）
        if QSettings("TrafficBoard", "TrafficBoard").value(
                "rank/sort", "cum") == "rate":
            self._rank_sort = "rate"
        self._detail_key = None
        self._detail_vals = []
        self._detail_title_text = ""
        self._alert_ts: dict = {}   # 每进程上次提醒时间（陌生连接限流）
        self._last_alert_any = 0.0  # 全局上次提醒时间（首启空库时防糊屏）

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
        # 顶栏挤的时候搜索框先让路（固定 260 会在窄窗口把状态文字压成看不见）
        self.search.setMinimumWidth(120)
        self.search.setMaximumWidth(260)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search)
        top.addStretch(1)
        self.status = CaptionLabel(page)
        # 状态文本长（● Clash 已连接 v… · TUN · 本次 …），不换行会把窗口最小宽度顶到 348+
        self.status.setWordWrap(True)
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
        title_row = QHBoxLayout()
        self.rank_title = SubtitleLabel("软件排行（代理流量 · 本次运行累计）")
        title_row.addWidget(self.rank_title)
        title_row.addStretch(1)
        self.btn_sort = QPushButton("按速率", left)
        self.btn_sort.setFlat(True)
        self.btn_sort.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sort.setToolTip("切换排行依据：累计流量 ↔ 当前速率")
        self.btn_sort.setStyleSheet("color: rgb(150,150,150); border: none; padding: 2px 6px;")
        self.btn_sort.clicked.connect(self._toggle_sort)
        title_row.addWidget(self.btn_sort)
        if self._rank_sort == "rate":   # 持久化恢复：按钮文案与标题同步
            self.btn_sort.setText("按累计")
            self.rank_title.setText("软件排行（当前速率 · 经代理）")
        lv.addLayout(title_row)
        self.rank = RankList(left)
        lv.addWidget(self.rank, 1)
        left.setMinimumWidth(300)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        self.detail_title = SubtitleLabel("连接明细")
        # 空态提示语很长（如"该进程当前没有可见连接…"），不换行会顶死右栏最小宽度
        self.detail_title.setWordWrap(True)
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
        self.page_settings = SettingsPage(self.agg)
        self.addSubInterface(self.page_settings, FluentIcon.SETTING, "设置")

        # ---- 历史页 ----
        from ui.history_page import HistoryPage
        self.page_history = HistoryPage(self.agg.history)
        self.addSubInterface(self.page_history, FluentIcon.HISTORY, "历史")
        self._hist_timer = QTimer(self)
        self._hist_timer.timeout.connect(self.page_history.refresh)
        self._hist_timer.start(60000)

        # ---- 体检页 ----
        from ui.health_page import HealthPage
        self.page_health = HealthPage(self.agg)
        self.addSubInterface(self.page_health, FluentIcon.HEART, "体检")

        self._wave_buf: list = []
        self._current_detail_rows: list = []
        self._tray_exit = False
        self._settings = QSettings("TrafficBoard", "TrafficBoard")
        # 提醒开关必须从设置读：写死 True 会让设置页显示"关"、重启后照旧弹
        self.alert_enabled = self._settings.value("alert/enabled", "1") in ("1", "true", True)
        self.exit_on_close = self._settings.value("close/exit", "0") in ("1", "true", True)
        self.badge_enabled = self._settings.value("badge/enabled", "1") in ("1", "true", True)
        self.badge_topmost = self._settings.value("badge/topmost", "1") in ("1", "true", True)
        # 月流量上限（R7：0=不限；reset_day=计费重置日）
        self.quota_cap_gb = float(self._settings.value("quota/cap_gb", 0.0) or 0.0)
        self.quota_reset_day = int(self._settings.value("quota/reset_day", 1) or 1)
        self._restore_win_geometry()          # 没有存值就保持上面的 resize(1000, 700)
        self.worker = PollWorker(agg, quota_cap_gb=self.quota_cap_gb,
                                 quota_reset_day=self.quota_reset_day)
        self.worker.board_ready.connect(self._on_board, Qt.ConnectionType.QueuedConnection)
        self.worker.wave_ready.connect(self._on_wave, Qt.ConnectionType.QueuedConnection)
        self.worker.quota_alert.connect(self._on_quota_alert,
                                        Qt.ConnectionType.QueuedConnection)
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
        # 位置由 SpeedBadge 自己从 badge/pos 恢复（含屏幕合法性校验）：
        # 这里不要再 move，否则用户拖好的悬浮条每次启动都被拽回同一个点。
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

    def set_quota(self, cap_gb: float, reset_day: int) -> None:
        """设置页改月流量上限：持久化 + 转交后台线程。"""
        self.quota_cap_gb = float(cap_gb or 0.0)
        self.quota_reset_day = int(reset_day or 1)
        self._settings.setValue("quota/cap_gb", self.quota_cap_gb)
        self._settings.setValue("quota/reset_day", self.quota_reset_day)
        self.worker.set_quota(self.quota_cap_gb, self.quota_reset_day)

    def _on_quota_alert(self, title: str, msg: str) -> None:
        self._toast("warning", title, msg)

    def _show_main(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_app(self) -> None:
        self._tray_exit = True
        self.close()

    def _save_win_geometry(self) -> None:
        """窗口大小/位置落设置，下次启动回到用户放的地方。
        位置存 frameGeometry（含边框，move() 认这个），尺寸存客户端宽高
        （resize() 认这个）——两个空间混用会让窗口每次启动沿对角线漂一个边框宽。
        sync() 必须调：退出走 TerminateProcess，QSettings 析构不会跑。"""
        try:
            s = self._settings
            if self.isMaximized() or self.isMinimized():
                # 此刻的 geometry 是"铺满屏幕"，存进去会污染下次启动的正常尺寸：
                # 只更新标记，位置尺寸沿用上一次的正常值。
                s.setValue("win/maximized", "1" if self.isMaximized() else "0")
            else:
                fg, g = self.frameGeometry(), self.geometry()
                s.setValue("win/x", fg.x())
                s.setValue("win/y", fg.y())
                s.setValue("win/w", g.width())
                s.setValue("win/h", g.height())
                s.setValue("win/maximized", "0")
            s.sync()
        except Exception:
            pass      # 存不进去只是丢一次体验，不能让关窗本身失败

    def _restore_win_geometry(self) -> bool:
        """返回是否真按存值恢复了（无存值/坏值 → False，保持默认尺寸）。"""
        from PySide6.QtGui import QGuiApplication
        s = self._settings
        try:
            x, y = int(s.value("win/x")), int(s.value("win/y"))
            w, h = int(s.value("win/w")), int(s.value("win/h"))
        except (TypeError, ValueError):
            return False
        if w < 200 or h < 200:          # 0/负数/半截数据：宁可回默认
            return False
        self.resize(w, h)
        self.move(x, y)
        # 显示器拔掉/分辨率变了：Qt 不一定兜得住，这里保证窗口回到某块屏幕内
        fg = self.frameGeometry()
        screens = QGuiApplication.screens()
        if screens and not any(sg.geometry().intersects(fg) for sg in screens):
            avail = (QGuiApplication.primaryScreen() or screens[0]).availableGeometry()
            self.move(avail.center().x() - self.width() // 2,
                      avail.center().y() - self.height() // 2)
        if s.value("win/maximized", "0") in ("1", "true", True):
            # 本窗口是 qframelesswindow 无边框窗，它的 showMaximized() 会去问 Win32
            # GetWindowPlacement——句柄无效时直接抛 pywintypes.error(1400)（离屏实测）。
            # 启动阶段绝不能因为它炸，最大化丢了只是体验，兜住。
            try:
                self.showMaximized()
            except Exception:
                pass
        return True

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_win_geometry()
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

    def _detail_row_conn(self, pos):
        """右键位置对应的那条连接；空态、越界、点在表头下一律 None。

        空态必须返回 None：明细表空时第 0 行是一句提示，而 _current_detail_rows
        可能还留着上一个进程的行 —— 按行号直取会让"断开此代理连接"
        掐掉一条已经不显示给用户的活连接。
        """
        row = self.table.rowAt(int(pos.y()))
        if 0 <= row < len(self._current_detail_rows):
            return self._current_detail_rows[row]
        return None

    def _detail_menu(self, pos) -> None:
        """明细表右键菜单：复制 / Wireshark 抓包 / 断开代理连接 / 导出 CSV。"""
        from PySide6.QtWidgets import QMenu, QApplication, QFileDialog
        from core.tools import find_wireshark

        cv = self._detail_row_conn(pos)
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
            if cv.raddr_ip and ws_port:
                filt = f"host {ws_target} and port {ws_port}"
            else:
                filt = f"host {ws_target}"
            self._launch_wireshark(ws_path, filt)
        elif chosen is act_kill and cv is not None:
            self._close_conn(cv)
        elif chosen is act_csv:
            path, _ = QFileDialog.getSaveFileName(self, "导出连接列表",
                                                  os.path.join(os.path.expanduser("~"), "Desktop",
                                                              "连接列表.csv"),
                                                  "CSV (*.csv)")
            if path:
                self._export_csv(path)

    # ------------------------------------------------------------ 动作与回执
    def _toast(self, kind: str, title: str, content: str) -> None:
        """干完一件事必须说一声：前台用 InfoBar，窗口收进托盘时退回托盘气泡。
        打包后 console=False，不吭声的失败和用户听到的「没反应」是同一件事。"""
        if self.isVisible() and not self.isMinimized():
            from qfluentwidgets import InfoBarIcon, InfoBarPosition
            icon = {"success": InfoBarIcon.SUCCESS, "info": InfoBarIcon.INFORMATION,
                    "warning": InfoBarIcon.WARNING, "error": InfoBarIcon.ERROR}[kind]
            # 全用关键字：qfluentwidgets 各版本 success() 的形参顺序不一样
            # （本机这版是 title, content, orient, isClosable, duration, position, parent），
            # 按位置传会把 InfoBarPosition 塞进 isClosable，直接 TypeError。
            _ToastBar(icon, title, content, duration=5000,
                      position=InfoBarPosition.TOP, parent=self,
                      isClosable=True).show()
            return
        from PySide6.QtWidgets import QSystemTrayIcon
        icon = {"success": QSystemTrayIcon.MessageIcon.Information,
                "info": QSystemTrayIcon.MessageIcon.Information,
                "warning": QSystemTrayIcon.MessageIcon.Warning,
                "error": QSystemTrayIcon.MessageIcon.Critical}[kind]
        self.tray.showMessage(title, content, icon, 6000)

    def _csv_rows(self) -> list:
        rows = []
        for a in self._board.apps:
            for c in a.conns:
                rows.append((a.display, a.proc_name, c.target, CHANNEL_LABEL[c.channel],
                             c.node, _fmt_bytes(c.download), _fmt_bytes(c.upload)))
        return rows

    def _export_csv(self, path: str) -> None:
        from core.tools import export_connections_csv
        try:
            n = export_connections_csv(path, self._csv_rows())
        except OSError as e:
            why = ("目标文件正被占用或没有写入权限（同名文件在 Excel 里开着最常见）"
                   if isinstance(e, PermissionError) else (e.strerror or str(e)))
            self._toast("error", "导出失败", f"{os.path.basename(path)}：{why}")
            return
        self._toast("success", "已导出", f"{n} 条连接 → {path}")

    def _close_conn(self, cv) -> None:
        if self.agg.clash.close_connection(cv.conn_id):
            self._toast("success", "已断开", f"{cv.target} 这条代理连接已关闭")
        else:
            self._toast("warning", "没能断开",
                        f"{cv.target}：代理可能已离线，或这条连接已经结束")

    def _launch_wireshark(self, exe: str, filt: str) -> None:
        import subprocess as _sp
        try:
            _sp.Popen([exe, "-f", filt])
        except OSError as e:
            self._toast("error", "Wireshark 没起来", f"{os.path.basename(exe)}：{e.strerror or e}")

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
        self._update_rank_title(online)

    # ---- 数据流 ----
    def _update_rank_title(self, online: bool) -> None:
        """排行标题跟随「Clash 在线状态 × 排序依据」（R5：双模式语义要写在明处）。"""
        if online and self._rank_sort == "rate":
            self.rank_title.setText("软件排行（当前速率 · 经代理）")
        elif online:
            self.rank_title.setText("软件排行（代理流量 · 本次运行累计）")
        else:
            self.rank_title.setText("软件排行（按连接数）")

    def _toggle_sort(self) -> None:
        """排行依据切换：累计流量 ↔ 当前速率（R5：同类产品的核心视图）。"""
        self._rank_sort = "rate" if self._rank_sort == "cum" else "cum"
        self.btn_sort.setText("按累计" if self._rank_sort == "rate" else "按速率")
        QSettings("TrafficBoard", "TrafficBoard").setValue("rank/sort", self._rank_sort)
        self._update_rank_title(self.agg.clash.snapshot().online)
        self._render()

    def _on_board(self, board: Board) -> None:
        self._board = board
        clash = self.agg.clash.snapshot()
        mode_tag = "TUN 单源" if board.data_mode == "tun" else "传统双源"
        sess = f"会话 ↓{_fmt_bytes(board.session_down)} ↑{_fmt_bytes(board.session_up)}"
        if clash.online:
            self.status.setText(f"● Clash 已连接 v{clash.version} · {mode_tag} · {sess}")
            self.status.setTextColor(QColor("#4caf50"), QColor("#4caf50"))
        else:
            self.status.setText(f"● Clash 未连接 · {mode_tag} · {sess}")
            # 原来深色下用 (120,120,120)，量到 3.78:1（卡片底 #1e1e1e），
            # 是全部界面文字里唯一低于 WCAG AA 4.5 的一处——这行是"代理没生效"
            # 的关键提示，不该比正文更含糊。150 灰量到 5.63:1。
            self.status.setTextColor(QColor(96, 96, 96), QColor(150, 150, 150))
        self._check_unknown(board)
        self._render()

    def _check_unknown(self, board: Board) -> None:
        """陌生连接检测：新 (进程, 目标) 对提醒一次。
        限流两层——同进程每小时 1 条 + 全局每分钟最多 1 条。
        全局那层是给首次启动兜底的：那时 known 库是空的，几十条连接会被同时
        判成"陌生"，不拦就会用提醒把用户自己的窗口糊住（连接仍照常记账，只是不弹）。"""
        if not getattr(self, "alert_enabled", True):
            return
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
        if now - self._last_alert_any < 60:
            return
        for proc, target, _first_seen in new:
            if now - self._alert_ts.get(proc, 0) < 3600:
                continue
            self._alert_ts[proc] = now
            self._last_alert_any = now
            self._toast("info", "发现新的网络连接", f"{proc} 首次连接：{target}")
            break

    def _on_pick(self, key: str) -> None:
        # 点击即选中（不做 toggle：误触两次会显得"没反应"）
        self._selected_key = key
        self._render()

    def _render(self) -> None:
        b = self._board
        self.rank.render(b.apps, self._metric_mode, self._selected_key,
                         self.search.text(), sort_mode=self._rank_sort)
        self._render_detail()
        self.page_health.set_mode(b.data_mode, b.tun_state)

    def _detail_placeholder(self, title: str, tip: str) -> None:
        """明细表空态：一行灰字说清"为什么是空的"。

        顺手把 _current_detail_rows 清空——右键菜单按行号取连接，
        留着上一个进程的行会让提示行也能"断开连接"。
        """
        self._current_detail_rows = []
        self._detail_vals = []
        key = ("placeholder", title, tip)
        if key == self._detail_key:
            return
        self._detail_key = key
        self.detail_title.setText(title)
        self._detail_title_text = title
        self.table.setRowCount(1)
        item = QTableWidgetItem(tip)
        item.setForeground(QColor(140, 140, 140))
        self.table.setItem(0, 0, item)
        for j in range(1, 4):
            self.table.setItem(0, j, QTableWidgetItem(""))

    def _render_detail(self) -> None:
        """明细表增量更新：结构不变时只改文本（不再每秒新建 item）。"""
        b = self._board
        app = next((a for a in b.apps if a.key == self._selected_key), None)
        kw = self.search.text().strip().lower()
        if app is None:
            if not self._selected_key:
                self._detail_placeholder("连接明细", "点击左侧任一软件，查看它的连接明细")
            else:
                self._detail_placeholder(
                    "连接明细",
                    f"{self._selected_key} 已不在当前列表（进程结束或本次没采到它），"
                    "点击左侧其他软件继续看")
            return

        title = f"{app.display} 的连接（{len(app.conns)} 条）"
        rows = [cv for cv in app.conns if not kw or kw in cv.target.lower()]
        if not rows:
            if kw:
                tip = (f"没有匹配「{self.search.text().strip()}」的连接"
                       f"（共 {len(app.conns)} 条，清空搜索框看全部）")
            else:
                tip = "该进程当前没有可见连接（连接已断开，或它的流量没走被采集的通道）"
            self._detail_placeholder(title, tip)
            return
        self._current_detail_rows = rows
        key = (app.key, tuple((cv.target, cv.channel) for cv in rows))
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
    from core.instance import claim, notify_running_instance
    if notify_running_instance():
        # 已有实例在跑（多半缩在托盘里）：激活它，本进程安静退出
        return 0
    agg = Aggregator()
    w = MainWindow(agg)
    state, srv = claim(w._show_main)
    if state == "taken":
        return 0                      # 并发启动里另一实例先抢到
    w._instance_srv = srv             # 持住引用：server 被 GC 管道就断
    w.show()
    code = app.exec()
    _hard_exit(code)

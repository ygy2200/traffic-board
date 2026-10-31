"""v1.6.1 离屏渲染验收：软件排行双模式（累计/速率）+ 切换按钮 + 状态栏数据源徽标。"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, shoot, use_temp_data_dir  # noqa: E402

# 本脚本会写用户设置（rank/sort）、也会打开历史库：设置快照/还原交给父进程用
# reg.exe 在进程外做，数据目录改到临时目录，绝不碰用户真实数据。
if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))
use_temp_data_dir("v161")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.aggregator import AppView, Board, ConnView  # noqa: E402
from ui.ranklist import RankList  # noqa: E402

HERE = Path(__file__).parent


def _app(name, display, conns, up, down, rate_up, rate_down, proxy=1, direct=0):
    a = AppView(pid=0, proc_name=name, display=display)
    a.conns = [ConnView(proto="tcp", target=f"t{i}.example.com", channel="proxy",
                        node="香港01", upload=10, download=20) for i in range(conns)]
    a.upload, a.download = up, down
    a.rate_up, a.rate_down = rate_up, rate_down
    a._proxy_count = proxy
    a._direct_count = direct
    return a


def _fake_board():
    b = Board(clash_online=True, clash_version="1.10.0", data_mode="tun", tun_state="on")
    b.apps = [
        _app("chrome.exe", "Chrome 浏览器", 12, 5 * 1024 * 1024, 120 * 1024 * 1024,
             12 * 1024, 3.5 * 1024 * 1024, proxy=8, direct=4),      # 累计最大
        _app("steam.exe", "Steam", 6, 900 * 1024 * 1024, 60 * 1024 * 1024,
             2.5 * 1024 * 1024, 8 * 1024, proxy=6),                  # 当前速率最大
        _app("wechat.exe", "微信", 9, 30 * 1024 * 1024, 300 * 1024 * 1024,
             800, 4 * 1024, proxy=9),
        _app("cs2.exe", "CS2", 3, 2 * 1024 * 1024, 40 * 1024 * 1024,
             0.0, 0.0, proxy=3),                                     # 已停流
    ]
    b.session_up = 800 * 1024 * 1024
    b.session_down = 21 * 1024 * 1024 * 1024
    return b


def _shoot(widget, path):
    shoot(widget, path, app)


app = QApplication.instance() or QApplication([])
b = _fake_board()

# ---- 排行累计模式 ----
rk = RankList()
rk.resize(430, 560)
rk.render(b.apps, "bytes", "", "", sort_mode="cum")
_shoot(rk, HERE / "render_v161_rank_cum.png")

# ---- 排行速率模式（同一数据，排序与数值列全部翻转）----
rk.render(b.apps, "bytes", "", "", sort_mode="rate")
_shoot(rk, HERE / "render_v161_rank_rate.png")

# ---- 排序逻辑断言（离屏渲染之外的数值验收）----
def _order(rk: RankList):
    """读布局实际显示序（_rows_by_key 是创建序，不是显示序）。"""
    return [rk.v.itemAt(i).widget().lbl_name.text()
            for i in range(rk.v.count()) if rk.v.itemAt(i).widget() is not None]

rk2 = RankList()
rk2.render(b.apps, "bytes", "", "", sort_mode="cum")
order_cum = _order(rk2)
rk2.render(b.apps, "bytes", "", "", sort_mode="rate")
order_rate = _order(rk2)
# 累计模式：下载+上传 —— Steam 960MB > 微信 330MB > Chrome 125MB > CS2 42MB
assert order_cum[0].startswith("Steam"), f"累计榜首异常: {order_cum}"
# 速率模式：Chrome 3.5MB/s > Steam 2.5MB/s > 微信 > CS2(0)
assert order_rate[0].startswith("Chrome"), f"速率榜首异常: {order_rate}"
assert order_cum != order_rate, "两种模式排序必须不同"
# 停流的 CS2 在速率模式排最后
assert order_rate[-1].startswith("CS2"), f"停流软件未沉底: {order_rate}"
# 速率模式下数值列显示 /s
steam_row = rk2._rows_by_key["steam.exe"]
assert "/s" in steam_row.lbl_value.text(), steam_row.lbl_value.text()
# 离线时速率模式自动回落连接数（metric_mode=count）
rk2.render(b.apps, "count", "", "", sort_mode="rate")
assert "/s" not in rk2._rows_by_key["steam.exe"].lbl_value.text()
print("排序翻转 + 数值列语义 + 离线回落 PASS")

# ---- 主窗口：切换按钮 + 状态栏徽标（无头，不起真实轮询）----
from PySide6.QtCore import QSettings  # noqa: E402

_qs = QSettings("TrafficBoard", "TrafficBoard")
_qs.remove("rank/sort")          # 从默认状态开始测，不读用户既有偏好（还原由父进程负责）

from core.aggregator import Aggregator  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

agg = Aggregator()
agg.start = lambda: None            # 隔离：不拉真实采集线程
agg.clash.state.__dict__["online"] = True
win = MainWindow(agg, start_worker=False)
win.resize(980, 700)
assert win.btn_sort.text() == "按速率"
win._toggle_sort()
assert win.btn_sort.text() == "按累计" and win._rank_sort == "rate"
assert "当前速率" in win.rank_title.text()
win._toggle_sort()
assert win.btn_sort.text() == "按速率" and win._rank_sort == "cum"
# 状态栏：数据源徽标 + 会话累计
win._on_board(b)
assert "TUN 单源" in win.status.text(), win.status.text()
assert "会话" in win.status.text() and "21.0GB" in win.status.text(), win.status.text()
win.show()
win.repaint()
_shoot(win, HERE / "render_v161_main.png")
win.close()
app.processEvents()
# rank/sort 的还原不在这儿做：同进程里 MainWindow 自己也有 QSettings 实例，
# 实例间缓存会互相覆盖（实测留下过脏值），统一由父进程按注册表快照还原。
print("render_v161 全部通过")
hard_exit(0)

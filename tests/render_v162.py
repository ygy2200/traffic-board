"""v1.6.1 离屏渲染验收：历史页总览卡片（今日/本周/本月）+ 设置页月流量上限。"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, shoot, use_temp_data_dir  # noqa: E402

# 设置页会往 QSettings 写 quota/cap_gb 等：还原交给父进程按注册表快照做（进程内还原不可信）
if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))
use_temp_data_dir("v162")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.history import HistoryStore  # noqa: E402
from ui.history_page import HistoryPage  # noqa: E402
from ui.settings_page import SettingsPage  # noqa: E402

HERE = Path(__file__).parent


def main() -> int:
    app = QApplication.instance() or QApplication([])

    # ---- 历史页：灌两拍真数据，验证总览三瓦片 ----
    db = os.path.join(tempfile.gettempdir(), "tb_render_quota.sqlite")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(db + s)
        except OSError:
            pass
    h = HistoryStore(db)
    now = int(time.time())
    h.write_tick(now - 120, {"chrome.exe": (100, 900, 1)}, 5000, 50000)
    h.write_tick(now - 60, {"steam.exe": (200, 300, 1)}, 2000, 18000)
    h.close()

    page = HistoryPage(HistoryStore(db))
    page.resize(700, 640)
    page.refresh()
    # 今日瓦片应显示两拍总和（↓68KB ↑7KB 量级，具体值随单位换算）
    today_txt = page._sum_labels["today"].text()
    assert "↓" in today_txt and "↑" in today_txt and today_txt != "—", today_txt
    week_txt = page._sum_labels["week"].text()
    month_txt = page._sum_labels["month"].text()
    assert week_txt != "—" and month_txt != "—"
    # 本周 >= 今日（含今日）、本月 >= 本周（同月）
    def _parse(t):
        import re
        m = re.findall(r"([\d.]+)(B|KB|MB|GB)", t)
        return sum(float(v) * (1024 ** ["B", "KB", "MB", "GB"].index(u)) for v, u in m)
    assert _parse(week_txt) >= _parse(today_txt)
    assert _parse(month_txt) >= _parse(week_txt)
    out = HERE / "render_v162_history_summary.png"
    shoot(page, out, app)
    print(f"总览瓦片: 今日={today_txt} 本周={week_txt} 本月={month_txt}")

    # ---- 设置页：月流量上限卡片（SpinBox 默认值 + 改动回调落 QSettings）----
    qs = QSettings("TrafficBoard", "TrafficBoard")
    qs.remove("quota/cap_gb")
    qs.remove("quota/reset_day")

    sp = SettingsPage()
    assert sp.sp_cap.value() == 0 and sp.sp_reset.value() == 1
    sp.sp_cap.setValue(200)
    sp.sp_reset.setValue(10)
    assert qs.value("quota/cap_gb") in (200.0, "200"), qs.value("quota/cap_gb")
    assert qs.value("quota/reset_day") in (10, "10"), qs.value("quota/reset_day")
    sp.resize(640, 760)
    out2 = HERE / "render_v162_settings_quota.png"
    shoot(sp, out2, app)

    # quota/* 的还原不在这里做：SettingsPage 自己也有 QSettings 实例，
    # 进程内还原会被实例缓存覆盖，统一由父进程按注册表快照还原。
    print("render_v162 全部通过")
    return 0


if __name__ == "__main__":
    hard_exit(main())

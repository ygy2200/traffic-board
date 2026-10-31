# -*- coding: utf-8 -*-
"""v1.6 离屏渲染验收：体检页三态（正常/警告/严重）+ 主窗口完整冒烟。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _qt_harness import am_child, hard_exit, run_isolated, shoot, use_temp_data_dir  # noqa: E402

# 本脚本后半段用真实 Aggregator（会拉起采样线程并往历史库落库）：数据目录换临时目录，
# 设置的快照/还原交给父进程用 reg.exe 在进程外做
if not am_child() and os.name == "nt":
    sys.exit(run_isolated(str(Path(__file__).resolve())))
use_temp_data_dir("v16h")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

import core.health as health  # noqa: E402
from core.health import (  # noqa: E402
    SEV_FAIL, SEV_INFO, SEV_OK, SEV_WARN, HealthItem, HealthReport)
from core.tun_detect import TunState  # noqa: E402
from ui.health_page import HealthPage  # noqa: E402

HERE = Path(__file__).parent


class _FakeAgg:
    def __init__(self, tun=TunState.OFF):
        self.tun = SimpleNamespace(snapshot=lambda: tun)
        self.clash = SimpleNamespace(snapshot=lambda: SimpleNamespace(
            online=False, port=0, version="", conns=[]))


def _real_fail_item() -> HealthItem:
    """用真引擎生成互斥 FAIL 项（不是手捏的假数据）。"""
    old = health._reg_proxy_enabled
    try:
        health._reg_proxy_enabled = lambda: True
        return health._check_proxy_mutex(_FakeAgg(tun=TunState.ON))
    finally:
        health._reg_proxy_enabled = old


def main() -> int:
    app = QApplication.instance() or QApplication([])
    page = HealthPage(_FakeAgg())
    page._auto_done = True          # 跳过 showEvent 自动体检（验收用注入报告）
    page.resize(660, 780)

    ok = HealthReport(items=[
        HealthItem("mutex", "TUN×系统代理互斥", SEV_OK, "无冲突（TUN 与系统代理未并存）"),
        HealthItem("egress", "出境连通", SEV_OK, "境外可达（0.5s，经当前生效的代理链路）"),
        HealthItem("tun", "TUN 模式", SEV_OK, "TUN 接管生效（fake-ip DNS 行为探测命中）"),
        HealthItem("api", "mihomo API 在线", SEV_OK, "外部控制器 127.0.0.1:9090 可用（1.10.0），当前 112 条连接"),
        HealthItem("vnic", "加速器共存", SEV_INFO, "未检测到游戏加速器进程"),
        HealthItem("dnsbl", "系统 DNS 解析", SEV_INFO, "解析被接管（返回 fake-ip 198.18.0.8）"),
    ])
    warn = HealthReport(items=[
        HealthItem("tun", "TUN 模式", SEV_WARN,
                   "内核自报 TUN 开启但行为探测不符——看板保守使用传统模式",
                   evidence="GET /configs tun.enable=true；fake-ip 探测返回 31.13.92.37",
                   fix="重开一次 FlClash 的 TUN 开关；若持续，反馈给开发者"),
        HealthItem("mutex", "TUN×系统代理互斥", SEV_OK, "无冲突"),
        HealthItem("egress", "出境连通", SEV_OK, "境外可达（0.6s）"),
    ])
    fail = HealthReport(items=[
        _real_fail_item(),
        HealthItem("egress", "出境连通", SEV_OK, "境外可达（0.6s，经当前生效的代理链路）"),
        HealthItem("vnic", "加速器共存", SEV_WARN,
                   "TUN 与游戏加速器（leigod.exe）同时运行——两者都会改路由，加速表现未验证",
                   fix="玩需要加速器的游戏前先实测；异常时关闭 TUN 或加速器其一"),
    ])

    def _shoot(name: str, rep: HealthReport) -> None:
        page._render(rep)
        # shoot() 里会转圈处理事件（离屏下不处理事件 layout 不会重算），
        # 但不再嵌套 app.exec()——实测那条路有丢 quit 导致无声挂死的风险
        shoot(page, HERE / f"render_v16_health_{name}.png", app)

    for name, rep in (("ok", ok), ("warn", warn), ("fail", fail)):
        _shoot(name, rep)

    # ---- 主窗口完整冒烟（真实 agg：拉起线程、跑一帧、退出）----
    from core.aggregator import Aggregator
    from ui.main_window import MainWindow
    agg = Aggregator()
    win = MainWindow(agg, start_worker=False)
    win.resize(980, 720)
    win.show()
    win.repaint()
    b = agg.build()
    print(f"主窗口冒烟 PASS  mode={b.data_mode} tun={b.tun_state} 软件={len(b.apps)}")
    agg.stop()
    win.close()
    app.processEvents()
    print("render_v16_health 全部通过")
    return 0


if __name__ == "__main__":
    hard_exit(main())

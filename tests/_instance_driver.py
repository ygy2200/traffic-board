# -*- coding: utf-8 -*-
"""单实例测试的子进程驱动：占住给定实例名若干秒，激活时打 ACTIVATED。

用法：python tests/_instance_driver.py <name> [duration_sec]
输出协议（每行一条，父进程按行判定）：
  CLAIM mine|taken|degraded     claim() 的结果
  ACTIVATED                     收到第二个实例的连接
  EXIT                          正常结束
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.instance import claim  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _qt_harness import hard_exit  # noqa: E402


def main() -> int:
    name = sys.argv[1]
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    app = QApplication([])
    hits = []

    def _show() -> None:
        hits.append(1)
        print("ACTIVATED", flush=True)

    state, _srv = claim(_show, name)
    print(f"CLAIM {state}", flush=True)
    QTimer.singleShot(int(duration * 1000), app.quit)
    app.exec()
    print("EXIT", flush=True)
    return 0


if __name__ == "__main__":
    hard_exit(main())

# -*- coding: utf-8 -*-
"""对抗测试：离屏套件不许再拉起真实 PollWorker。

为什么值得钉：MainWindow 默认会起 PollWorker，250ms 一次读本机网卡、Clash API，
每秒 build 一次排行榜。渲染套件原本只 stub 了 agg.start，没管 MainWindow 自己起的
worker——同一份代码，用户开着 FlClash 和没开，排行榜行数/文字长度不同，像素闸门
时红时绿。R24 给 MainWindow 加了 start_worker 开关，这道闸门防止以后新套件漏传。
"""
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main_window_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if name == "MainWindow":
            yield node


def test_all_render_suites_pass_start_worker_false():
    calls = 0
    bad = []
    for p in sorted((ROOT / "tests").glob("render_*.py")):
        for node in main_window_calls(p):
            calls += 1
            kw = {k.arg: k.value for k in node.keywords}
            v = kw.get("start_worker")
            if not (isinstance(v, ast.Constant) and v.value is False):
                bad.append(f"{p.name}:{node.lineno}")
    assert calls >= 10, f"只找到 {calls} 处 MainWindow 构造，闸门可能是空的"
    assert not bad, "这些构造没写 start_worker=False，会拉起真实采集线程：" + ", ".join(bad)


def test_main_window_has_explicit_start_worker_seam():
    src = (ROOT / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "def __init__(self, agg: Aggregator, start_worker: bool = True)" in src, \
        "MainWindow 的 start_worker 默认值变了：生产默认必须仍然是启动采集"
    assert "if start_worker:\n            self.worker.start()" in src, \
        "worker.start() 没有受 start_worker 控制"


def main():
    test_all_render_suites_pass_start_worker_false()
    print("  PASS test_all_render_suites_pass_start_worker_false")
    test_main_window_has_explicit_start_worker_seam()
    print("  PASS test_main_window_has_explicit_start_worker_seam")
    print("adv_test_isolation 全部通过（2 例）")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _qt_harness import hard_exit
    hard_exit(main())

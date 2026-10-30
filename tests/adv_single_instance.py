# -*- coding: utf-8 -*-
"""单实例行为端到端测试：真子进程 + 真命名管道 + 真命名互斥量。

覆盖：无实例时不误报、有实例时能激活、并发启动只有一个 mine、
拿不到管道时降级而不是拒绝启动。
"""
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.instance import claim, notify_running_instance  # noqa: E402
from _qt_harness import hard_exit  # noqa: E402

DRIVER = str(ROOT / "tests" / "_instance_driver.py")


def _spawn(name: str, duration: float):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.Popen([sys.executable, DRIVER, name, str(duration)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", env=env)
    q: queue.Queue = queue.Queue()

    def _pump():
        for line in p.stdout:
            q.put(line.strip())
        q.put(None)

    threading.Thread(target=_pump, daemon=True).start()
    return p, q


def _lines(q, timeout: float):
    """收集 timeout 秒内到达的输出行（None 表示子进程已结束）。"""
    out = []
    end = time.time() + timeout
    while time.time() < end:
        try:
            item = q.get(timeout=max(0.05, end - time.time()))
        except queue.Empty:
            break
        if item is None:
            break
        out.append(item)
    return out


def _wait_for(q, prefix: str, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            item = q.get(timeout=max(0.05, end - time.time()))
        except queue.Empty:
            return False
        if item is None:
            return False
        if item.startswith(prefix):
            return True
    return False


def _kill(p):
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        pass


def test_no_instance_is_not_reported():
    name = f"TrafficBoard-instance-test-{os.getpid()}-absent"
    t0 = time.time()
    assert notify_running_instance(name) is False
    elapsed = time.time() - t0
    assert elapsed < 2.0, f"无实例时探测耗了 {elapsed:.1f}s（启动会跟着变慢）"
    print("无实例不误报（探测 <2s）PASS")


def test_running_instance_gets_activated():
    name = f"TrafficBoard-instance-test-{os.getpid()}-active"
    p, q = _spawn(name, 12.0)
    try:
        assert _wait_for(q, "CLAIM mine", 30), "子实例没拿到首实例身份"
        assert notify_running_instance(name) is True, "第二个进程没连上在跑的实例"
        assert _wait_for(q, "ACTIVATED", 5), "在跑的实例没收到激活请求"
        assert p.poll() is None, "被激活的实例自己退了"
    finally:
        _kill(p)
    print("在跑实例被二次启动激活且不自退 PASS")


def test_claim_returns_taken_when_running():
    name = f"TrafficBoard-instance-test-{os.getpid()}-claim"
    p, q = _spawn(name, 12.0)
    try:
        assert _wait_for(q, "CLAIM mine", 30)
        hits = []
        state, srv = claim(lambda: hits.append(1), name)
        assert state == "taken", f"已有实例时 claim 返回了 {state}"
        assert srv is None
        assert _wait_for(q, "ACTIVATED", 5), "claim 的 taken 路径没把激活送到实例"
    finally:
        _kill(p)
    print("claim 在已有实例时返回 taken 并激活对方 PASS")


def test_concurrent_start_only_one_wins():
    name = f"TrafficBoard-instance-test-{os.getpid()}-race"
    a, qa = _spawn(name, 14.0)
    b, qb = _spawn(name, 14.0)
    try:
        la, lb = _wait_line_pair(qa, qb, 35)
        assert la and lb, f"两个实例输出不齐: {la!r} {lb!r}"
        wins = [x for x in (la, lb) if x == "CLAIM mine"]
        assert len(wins) == 1, f"并发启动的归属错了: {la!r} / {lb!r}"
        loser = lb if la == "CLAIM mine" else la
        assert loser == "CLAIM taken", f"输家没走 taken 路径: {loser!r}"
    finally:
        _kill(a)
        _kill(b)
    print("并发启动互斥量仲裁：恰好一个 mine、另一个 taken PASS")


def _wait_line_pair(qa, qb, timeout):
    end = time.time() + timeout
    la = lb = None
    while time.time() < end and not (la and lb):
        for q, cur in ((qa, "la"), (qb, "lb")):
            if cur == "la" and la:
                continue
            if cur == "lb" and lb:
                continue
            try:
                item = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                continue
            if item.startswith("CLAIM"):
                if cur == "la":
                    la = item
                else:
                    lb = item
    return la, lb


def test_degraded_when_pipe_cannot_listen():
    """QLocalServer 起不来时不许拒绝启动（降级路径）。"""
    import PySide6.QtNetwork as net
    name = f"TrafficBoard-instance-test-{os.getpid()}-degraded"
    real = net.QLocalServer
    calls = {"n": 0}

    class _Broken(real):
        def listen(self, *a, **k):
            calls["n"] += 1
            return False

    net.QLocalServer = _Broken
    try:
        state, srv = claim(lambda: None, name)
    finally:
        net.QLocalServer = real
    assert state == "degraded" and srv is None, (state, srv)
    assert calls["n"] == 1
    print("管道起不来时降级启动（不拒绝）PASS")


if __name__ == "__main__":
    app = QApplication([])          # noqa: F841  QLocalSocket/Server 需要 QCoreApplication 存在
    test_no_instance_is_not_reported()
    test_running_instance_gets_activated()
    test_claim_returns_taken_when_running()
    test_concurrent_start_only_one_wins()
    test_degraded_when_pipe_cannot_listen()
    print("adv_single_instance 全部通过")
    hard_exit(0)

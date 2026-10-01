# -*- coding: utf-8 -*-
"""对抗测试：退出竞态（2026-10-01 退出报错修复回归）。

攻击面：
1. known 库：filter_new(主线程) / flush(worker) / close(退出) 三方并发
2. history 库：write_tick(worker) / close(退出) 并发 + 重建路径并发
3. PollWorker.wait 超时分支前提：线程卡死时 wait 必须返回 False
"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "\\src"))

_tmp = tempfile.mkdtemp(prefix="tb_adv_exit_")
os.environ["LOCALAPPDATA"] = _tmp   # 隔离 KnownPeers/HistoryStore 默认库路径

from core.known import KnownPeers      # noqa: E402
from core.history import HistoryStore  # noqa: E402


def test_known_concurrent():
    """退出时主线程 close() 撞上 worker flush()：修复前会在已关闭连接上 executemany。"""
    for round_no in range(30):
        k = KnownPeers()
        stop = threading.Event()
        errors = []

        def writer(tid):
            i = 0
            while not stop.is_set():
                k.filter_new({(f"p{tid}.exe", f"target{i}.com:443")})
                i += 1
                time.sleep(0.001)

        def flusher():
            while not stop.is_set():
                k.flush()
                time.sleep(0.001)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(3)]
        threads.append(threading.Thread(target=flusher))
        for t in threads:
            t.start()
        time.sleep(0.15)
        k.close()          # 与 flush/writer 并发的退出路径
        stop.set()
        for t in threads:
            t.join()
        k.flush()          # close 后不得复活连接/崩溃
        errors.extend([e for e in [] if e])
        assert k._conn is None, f"round{round_no}: close 后连接未置空"
    print("known 三方并发 close/flush/filter_new PASS (30 轮)")


def test_history_concurrent_close():
    """worker write_tick 与退出 close() 并发：锁外取 conn 引用在修复前会拿到已关闭连接。"""
    db = os.path.join(_tmp, "hist_cc.sqlite")
    h = HistoryStore(db)
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            h.write_tick(int(time.time()) + i, {"a.exe": (i, i, 1)}, i, i)
            i += 1
            time.sleep(0.001)

    t = threading.Thread(target=writer)
    t.start()
    time.sleep(0.15)
    h.close()              # 与 write_tick 并发
    stop.set()
    t.join()
    h.write_tick(int(time.time()), {"a.exe": (1, 1, 1)}, 1, 1)   # close 后静默降级
    h.close()              # 幂等
    assert h._conn is None
    print("history write_tick/close 并发 PASS")


def test_history_rebuild_vs_close():
    """损坏库触发重建路径（worker）与主线程 close() 并发：不得死锁、不得复活。"""
    db = os.path.join(_tmp, "hist_rebuild.sqlite")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(db + s)
        except OSError:
            pass
    h = HistoryStore(db)
    h.write_tick(int(time.time()), {}, 1, 1)
    h.close()
    open(db, "wb").write(b"corrupt" * 100)   # 下次 write_tick 走重建

    done = threading.Event()

    def closer():
        time.sleep(0.01)
        h.close()
        done.set()

    t = threading.Thread(target=closer)
    t.start()
    h.write_tick(int(time.time()), {"a.exe": (1, 1, 1)}, 1, 1)   # 并发触发 _init_db 重建
    t.join(timeout=5)
    assert done.is_set(), "并发 close 死锁"
    h.write_tick(int(time.time()), {}, 1, 1)   # 重建后仍可用或静默降级，不崩溃
    print("history 重建与 close 并发 PASS")


def test_pollworker_wait_timeout():
    """修复前提：worker 卡死时 wait(短超时) 返回 False——closeEvent 新分支据此跳过关库。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QTimer
    from PySide6.QtCore import QThread
    from ui.main_window import PollWorker
    from core.aggregator import Aggregator
    import psutil

    app = QCoreApplication.instance() or QCoreApplication([])
    agg = Aggregator()
    w = PollWorker(agg)

    real_net_conn = psutil.net_connections

    def slow_net_conn(*a, **kw):
        time.sleep(3)          # 模拟 psutil 偶发卡死（杀软挂钩/进程暴涨）
        return real_net_conn(*a, **kw)

    psutil.net_connections = slow_net_conn
    try:
        w.start()
        time.sleep(1.5)        # 等 worker 进入 build() → poll() 卡住
        w.stop()
        t0 = time.time()
        ok = w.wait(500)
        elapsed = time.time() - t0
        assert ok is False, "线程卡死时 wait 却返回 True，分支前提不成立"
        assert elapsed < 2.0, f"wait 未按超时返回（耗时 {elapsed:.1f}s）"
        w.wait(10000)          # 收尾清理
    finally:
        psutil.net_connections = real_net_conn
        agg.stop()
    print("PollWorker 卡死时 wait 超时返回 False PASS")


def test_backend_threads_joined_on_stop():
    """退出崩溃根因回归：dns/clash 的 stop() 必须让后台线程真正退出。

    修复前：stop 只 set event / kill 子进程，reader 线程还卡在管道读上，
    主线程紧接的 os._exit 终止例程会杀掉持锁线程 → 段错误（实测 40% 复现）。
    """
    from core.dns_cache import DnsCacheReader
    from core.clash_api import ClashApiPoller

    r = DnsCacheReader(interval_sec=0.2)
    r.start()
    time.sleep(1.0)
    t0 = time.time()
    r.stop()
    assert not r._thread.is_alive(), "dns reader 线程 stop 后仍存活"
    assert time.time() - t0 < 3, f"dns stop 耗时过长 {time.time() - t0:.1f}s"
    r.stop()   # 幂等
    print("dns reader 线程 stop 即终止 PASS")

    p = ClashApiPoller()
    p.start()
    time.sleep(1.0)
    t0 = time.time()
    p.stop()
    assert not p._thread.is_alive(), "clash poller 线程 stop 后仍存活"
    assert time.time() - t0 < 4, f"clash stop 耗时过长 {time.time() - t0:.1f}s"
    p.stop()   # 幂等
    print("clash poller 线程 stop 即终止 PASS")


if __name__ == "__main__":
    test_known_concurrent()
    test_history_concurrent_close()
    test_history_rebuild_vs_close()
    test_pollworker_wait_timeout()
    test_backend_threads_joined_on_stop()
    print("adv_exit 全部通过")

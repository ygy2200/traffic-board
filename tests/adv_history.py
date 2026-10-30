# -*- coding: utf-8 -*-
"""对抗测试：历史库损坏自愈、CSV 公式注入、自启注册表。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "\\src"))

from core.history import HistoryStore  # noqa: E402
from core.tools import _csv_safe, export_connections_csv  # noqa: E402


def test_corrupt_db_selfheal():
    db = os.path.join(tempfile.gettempdir(), "tb_adv_hist.sqlite")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(db + s)
        except OSError:
            pass
    h = HistoryStore(db)
    h.write_tick(int(time.time()), {"a.exe": (1, 1, 1)}, 10, 10)
    h.close()
    open(db, "wb").write(b"garbage not sqlite" * 20)
    h2 = HistoryStore(db)
    h2.write_tick(int(time.time()), {"a.exe": (2, 2, 1)}, 20, 20)
    assert h2.query_total_buckets(time.time() - 3600, 3600), "自愈后读回失败"
    h2.close()
    print("损坏库自愈 PASS")


def test_csv_injection():
    assert _csv_safe("=cmd|' /c calc'!A0").startswith("'")
    assert _csv_safe("+sum(A1:A9)").startswith("'")
    assert _csv_safe("-2").startswith("'")
    assert _csv_safe("@cmd").startswith("'")
    assert _csv_safe("normal.com") == "normal.com"
    path = os.path.join(tempfile.gettempdir(), "tb_adv.csv")
    export_connections_csv(path, [("x", "x.exe", "=HYPERLINK(evil)", "代理", "n", "1", "2")])
    content = open(path, encoding="utf-8-sig").read()
    assert "'=HYPERLINK" in content
    os.remove(path)
    print("CSV 公式注入防护 PASS")


def test_negative_and_huge():
    db = os.path.join(tempfile.gettempdir(), "tb_adv_hist2.sqlite")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(db + s)
        except OSError:
            pass
    h = HistoryStore(db)
    # 负数/超大值不崩溃（差分回退保护）
    h.write_tick(int(time.time()), {"a.exe": (-5, 2**60, 1)}, -999, 2**62)
    rows = h.query_total_buckets(time.time() - 3600, 3600)
    assert rows and rows[0][1] >= 0 and rows[0][2] >= 0
    h.close()
    os.remove(db)
    print("负数/超大值防护 PASS")


def test_total_and_proxy_queries():
    """R7：总览/配额查询——全机总量与代理分量（本地流量不计入套餐）。"""
    db = os.path.join(tempfile.gettempdir(), "tb_adv_hist3.sqlite")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(db + s)
        except OSError:
            pass
    h = HistoryStore(db)
    now = int(time.time())
    h.write_tick(now - 30, {"app.exe": (100, 900, 1)}, 5000, 50000)
    h.write_tick(now - 10, {"app.exe": (50, 100, 1)}, 1000, 9000)
    # 全机总量 = 两拍之和
    up, down = h.query_total_since(now - 3600)
    assert (up, down) == (6000, 59000), (up, down)
    # 代理分量只含 per_min（app.exe 两拍）
    pup, pdown = h.query_proxy_total_since(now - 3600)
    assert (pup, pdown) == (150, 1000), (pup, pdown)
    # 时间窗之外查不到
    assert h.query_total_since(now + 60) == (0, 0)
    assert h.query_proxy_total_since(now + 60) == (0, 0)
    h.close()
    os.remove(db)
    print("总览/配额查询 PASS（代理分量不含本地流量）")


def _fresh_db(name):
    db = os.path.join(tempfile.gettempdir(), name)
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(db + s)
        except OSError:
            pass
    return db


def test_busy_must_not_delete_history():
    """R3 核心：另一个连接持写锁（双开实例、OneDrive 漫游的 LOCALAPPDATA）时
    commit 报 "database is locked"。它是 DatabaseError 的子类，旧代码一律按
    "库损坏"处理 → 删掉 history.sqlite，用户 30 天历史就这么没了。"""
    import sqlite3
    db = _fresh_db("tb_adv_busy.sqlite")
    h = HistoryStore(db)
    now = int(time.time())
    h.write_tick(now - 120, {"a.exe": (10, 20, 1)}, 100, 200)
    h.write_tick(now - 60, {"a.exe": (10, 20, 1)}, 100, 200)
    up_before, down_before = h.query_total_since(now - 3600)
    assert (up_before, down_before) == (200, 400), (up_before, down_before)
    h._conn.execute("PRAGMA busy_timeout=200")   # 生产是 15s，测试里等不起

    blocker = sqlite3.connect(db)
    blocker.execute("BEGIN IMMEDIATE")            # 抢走写锁
    try:
        h.write_tick(now, {"a.exe": (10, 20, 1)}, 100, 200)
    finally:
        blocker.commit()
        blocker.close()

    assert os.path.exists(db), "BUSY 被误判成损坏，历史库被删除了"
    assert h.query_total_since(now - 3600) == (up_before, down_before), "既有历史丢失"
    # 锁放开后必须能自愈继续写，而不是永久失忆
    h.write_tick(now + 60, {"a.exe": (10, 20, 1)}, 100, 200)
    up_after, _d = h.query_total_since(now - 3600)
    assert up_after >= up_before, (up_after, up_before)
    h.close()
    try:
        os.remove(db)
    except OSError as e:
        raise AssertionError("close 后句柄仍被占用：自愈路径泄漏了旧连接 -> %s" % e)
    print("BUSY 不删库 PASS（历史完好、能自愈、无句柄泄漏）")


def test_readers_survive_none_conn():
    """连接降级为 None 时四个查询必须返回空，而不是抛 AttributeError
    （主窗口 60s 定时器撞上它 = 历史页永久空白且无提示）。"""
    db = _fresh_db("tb_adv_none.sqlite")
    h = HistoryStore(db)
    now = int(time.time())
    h.write_tick(now, {"a.exe": (1, 2, 3)}, 10, 20)
    h.close()
    h._conn = None
    assert h.query_total_buckets(now - 3600, 3600) == []
    assert h.query_total_since(now - 3600) == (0, 0)
    assert h.query_proxy_total_since(now - 3600) == (0, 0)
    assert h.query_app_rank(now - 3600) == []
    os.remove(db)
    print("读函数 None 安全 PASS")


def test_u64_clamp_and_overflow():
    """psutil 计数器是 u64，坏读数超过 SQLite INTEGER 上限时旧代码抛
    OverflowError（不属于 DatabaseError）→ 写路径永久打死。"""
    from core.history import _u64, _MAX_BYTES
    assert _u64(2 ** 70) == _MAX_BYTES and _u64(-5) == 0
    assert _u64(None) == 0 and _u64("abc") == 0 and _u64(1.7) == 1
    db = _fresh_db("tb_adv_ovf.sqlite")
    h = HistoryStore(db)
    now = int(time.time())
    h.write_tick(now, {"a.exe": (2 ** 70, -9, 5)}, 2 ** 70, 10)
    up, down = h.query_total_since(now - 60)
    assert up == _MAX_BYTES and down == 10, (up, down)
    # 夹紧之后还能继续写，且聚合不溢出（没夹紧时 SUM 会整表报错返回空）
    h.write_tick(now + 60, {"a.exe": (1, 1, 1)}, 5, 5)
    assert h.query_total_since(now - 60)[0] == _MAX_BYTES + 5
    h.close()
    os.remove(db)
    print("u64 夹紧 PASS")


if __name__ == "__main__":
    test_corrupt_db_selfheal()
    test_csv_injection()
    test_negative_and_huge()
    test_total_and_proxy_queries()
    test_busy_must_not_delete_history()
    test_readers_survive_none_conn()
    test_u64_clamp_and_overflow()
    print("adv_history 全部通过")

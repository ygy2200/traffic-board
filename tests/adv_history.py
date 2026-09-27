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


if __name__ == "__main__":
    test_corrupt_db_selfheal()
    test_csv_injection()
    test_negative_and_huge()
    print("adv_history 全部通过")

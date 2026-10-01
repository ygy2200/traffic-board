# -*- coding: utf-8 -*-
"""历史流量存储（SQLite，单写者，损坏自动重建）。

表：
- totals_min(ts, up, down)      全机网卡每分钟字节
- per_min(ts, app, up, down, conns)  代理流量按软件每分钟（近似：活动连接累计差分）
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time


def default_db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "TrafficBoard", "history.sqlite")


class HistoryStore:
    def __init__(self, db_path: str = "") -> None:
        self.db_path = db_path or default_db_path()
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            # 必须先关句柄，否则 Windows 下文件被占用、删除会失败（重建卡死）
            conn.close()
            raise
        return conn

    def _init_db(self) -> None:
        # 可能从 worker 线程(write_tick 重建路径)与主线程(退出 close)并发调用，整体持锁
        with self._lock:
            ddl = ("CREATE TABLE IF NOT EXISTS totals_min("
                   " ts INTEGER PRIMARY KEY, up INTEGER, down INTEGER);"
                   "CREATE TABLE IF NOT EXISTS per_min("
                   " ts INTEGER, app TEXT, up INTEGER, down INTEGER, conns INTEGER,"
                   " PRIMARY KEY(ts, app));"
                   "CREATE INDEX IF NOT EXISTS idx_per_app ON per_min(app, ts);")
            try:
                self._conn = self._connect()
                self._conn.executescript(ddl)
                self._conn.commit()
            except sqlite3.DatabaseError:
                # 库损坏：连 WAL/SHM 一起删除重建（防递归，重建失败则降级为无历史）
                try:
                    if self._conn:
                        self._conn.close()
                except Exception:
                    pass
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.remove(self.db_path + suffix)
                    except OSError:
                        pass
                try:
                    self._conn = self._connect()
                    self._conn.executescript(ddl)
                    self._conn.commit()
                except sqlite3.DatabaseError:
                    self._conn = None

    def write_tick(self, ts: int, per_app: dict, total_up: int, total_down: int) -> None:
        """每分钟落库一次。per_app: {app: (up, down, conns)}。"""
        try:
            with self._lock:
                conn = self._conn   # 锁内取引用，防止与 close() 竞态拿到已关闭连接
                if conn is None:
                    return
                conn.execute("INSERT OR REPLACE INTO totals_min(ts, up, down) VALUES(?,?,?)",
                             (int(ts), int(max(0, total_up)), int(max(0, total_down))))
                conn.executemany(
                    "INSERT OR REPLACE INTO per_min(ts, app, up, down, conns) VALUES(?,?,?,?,?)",
                    [(int(ts), app, int(max(0, up)), int(max(0, down)), int(conns))
                     for app, (up, down, conns) in per_app.items()])
                # 保留 30 天
                cutoff = int(time.time()) - 30 * 86400
                conn.execute("DELETE FROM totals_min WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM per_min WHERE ts < ?", (cutoff,))
                conn.commit()
        except sqlite3.DatabaseError:
            self._init_db()

    def query_total_buckets(self, since_ts: int, bucket_sec: int) -> list:
        """返回 [(bucket_ts, up, down)] 按桶聚合。"""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT (ts/?) * ? AS b, SUM(up), SUM(down) FROM totals_min"
                    " WHERE ts >= ? GROUP BY b ORDER BY b",
                    (bucket_sec, bucket_sec, int(since_ts)))
                return cur.fetchall()
        except sqlite3.DatabaseError:
            return []

    def query_app_rank(self, since_ts: int, top: int = 10) -> list:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT app, SUM(up), SUM(down), MAX(conns) FROM per_min"
                    " WHERE ts >= ? GROUP BY app ORDER BY SUM(up)+SUM(down) DESC LIMIT ?",
                    (int(since_ts), int(top)))
                return cur.fetchall()
        except sqlite3.DatabaseError:
            return []

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

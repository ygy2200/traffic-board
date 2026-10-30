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


# 一分钟 1 PB 物理上不可能（100Gbps 线速约 0.75 GB/min），超过即坏读数。
# 不夹紧到 int64 上限是因为 SUM() 会在查询端溢出：存进来的值必须留出聚合余量。
_MAX_BYTES = 10 ** 15


def _u64(v) -> int:
    """负数归零、上限夹紧。psutil 的网卡计数器是 u64，坏读数能超过 SQLite
    INTEGER 上限；OverflowError 不属于 DatabaseError，一旦冒出会把整条写路径打死。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return 0 if n < 0 else (n if n <= _MAX_BYTES else _MAX_BYTES)


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
            # 双开/另一个进程持有写锁时先等而不是立刻报错（报错会被误判成损坏）
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            # 必须先关句柄，否则 Windows 下文件被占用、删除会失败（重建卡死）
            conn.close()
            raise
        return conn

    @staticmethod
    def _is_corrupt(exc: BaseException) -> bool:
        """只有真正的库损坏才允许删库重建。

        OperationalError 是 DatabaseError 的子类，"database is locked" /
        "disk I/O error" / "unable to open database file" 都从这里进来——
        按损坏处理会把用户 30 天历史连文件一起删掉。"""
        msg = str(exc).lower()
        return ("not a database" in msg or "malformed" in msg
                or "database disk image is faulty" in msg
                or ("corrupt" in msg and "sqlite" in msg))

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
                if self._conn is None:
                    self._conn = self._connect()
                self._conn.executescript(ddl)
                self._conn.commit()
            except sqlite3.DatabaseError as e:
                # 替换连接前必须关掉旧的：BUSY 自愈路径也会走这里，
                # 旧句柄不关就是 Windows 下"文件被占用"的泄漏源
                old, self._conn = self._conn, None
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass
                if not self._is_corrupt(e):
                    # BUSY / 打不开文件：保留数据，下次 write_tick 再试
                    return
                # 库损坏：连 WAL/SHM 一起删除重建（防递归，重建失败则降级为无历史）
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
        retry = False
        try:
            with self._lock:
                conn = self._conn   # 锁内取引用，防止与 close() 竞态拿到已关闭连接
                if conn is None:
                    retry = True    # 上次 BUSY 留下的空档：锁外重建，别永久失忆
                else:
                    conn.execute("INSERT OR REPLACE INTO totals_min(ts, up, down) VALUES(?,?,?)",
                                 (int(ts), _u64(total_up), _u64(total_down)))
                    conn.executemany(
                        "INSERT OR REPLACE INTO per_min(ts, app, up, down, conns) VALUES(?,?,?,?,?)",
                        [(int(ts), app, _u64(up), _u64(down), _u64(conns))
                         for app, (up, down, conns) in per_app.items()])
                    # 保留 30 天
                    cutoff = int(time.time()) - 30 * 86400
                    conn.execute("DELETE FROM totals_min WHERE ts < ?", (cutoff,))
                    conn.execute("DELETE FROM per_min WHERE ts < ?", (cutoff,))
                    conn.commit()
        except (sqlite3.DatabaseError, OverflowError, OSError, ValueError):
            retry = True
            try:
                if self._conn:
                    self._conn.rollback()   # 不留半开事务：否则锁一直挂着，另一实例永远等
            except Exception:
                pass
        if retry:
            self._init_db()

    def _read(self, sql: str, args: tuple, many: bool):
        """读路径统一入口：连接降级为 None（BUSY 后）时返回空结果。

        四个查询以前都直接 self._conn.execute，None 时抛 AttributeError——
        主窗口 60 秒定时器撞上它，历史页就永久空白且无任何提示。"""
        try:
            with self._lock:
                if self._conn is None:
                    return [] if many else None
                cur = self._conn.execute(sql, args)
                return cur.fetchall() if many else cur.fetchone()
        except sqlite3.DatabaseError:
            return [] if many else None

    def query_total_buckets(self, since_ts: int, bucket_sec: int) -> list:
        """返回 [(bucket_ts, up, down)] 按桶聚合。"""
        return self._read(
            "SELECT (ts/?) * ? AS b, SUM(up), SUM(down) FROM totals_min"
            " WHERE ts >= ? GROUP BY b ORDER BY b",
            (bucket_sec, bucket_sec, int(since_ts)), many=True)

    def query_total_since(self, since_ts: int) -> tuple:
        """返回 (up, down) 自 since_ts 起的全机字节总和（今日/本周/本月总览用）。"""
        row = self._read(
            "SELECT SUM(up), SUM(down) FROM totals_min WHERE ts >= ?",
            (int(since_ts),), many=False)
        if row and row[0] is not None:
            return int(row[0] or 0), int(row[1] or 0)
        return 0, 0

    def query_proxy_total_since(self, since_ts: int) -> tuple:
        """返回 (up, down) 自 since_ts 起的代理字节总和（套餐上限计量用——
        计量经代理的流量，不含校园网内网本地流量，避免内网传输误报超限）。"""
        row = self._read(
            "SELECT SUM(up), SUM(down) FROM per_min WHERE ts >= ?",
            (int(since_ts),), many=False)
        if row and row[0] is not None:
            return int(row[0] or 0), int(row[1] or 0)
        return 0, 0

    def query_app_rank(self, since_ts: int, top: int = 10) -> list:
        return self._read(
            "SELECT app, SUM(up), SUM(down), MAX(conns) FROM per_min"
            " WHERE ts >= ? GROUP BY app ORDER BY SUM(up)+SUM(down) DESC LIMIT ?",
            (int(since_ts), int(top)), many=True)

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

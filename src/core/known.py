# -*- coding: utf-8 -*-
"""陌生连接检测：已见 (进程, 目标) 对持久化，新对触发提醒。

内存 set 判重（每秒比对零 SQL），入库批量节流（60 秒或退出时刷写）。
库损坏自动重建（同 history 的句柄关闭模式）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time


class KnownPeers:
    def __init__(self, db_path: str = "") -> None:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        self.db_path = db_path or os.path.join(base, "TrafficBoard", "known.sqlite")
        self._seen: set = set()
        self._pending: list = []
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()   # filter_new(主线程)/flush、close(worker 与主线程) 跨线程
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def _init_db(self) -> None:
        ddl = ("CREATE TABLE IF NOT EXISTS known("
               " proc TEXT, target TEXT, first_seen INTEGER,"
               " PRIMARY KEY(proc, target));")
        try:
            self._conn = self._connect()
            self._conn.executescript(ddl)
            self._conn.commit()
            cur = self._conn.execute("SELECT proc, target FROM known")
            self._seen = {(p, t) for p, t in cur.fetchall()}
        except sqlite3.DatabaseError:
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
                self._seen = set()
            except sqlite3.DatabaseError:
                self._conn = None
                self._seen = set()

    def filter_new(self, pairs: set) -> list:
        """返回其中从未见过的 (proc, target) 列表，并标记为已见（待落库）。"""
        new = []
        with self._lock:
            for pair in pairs:
                if pair not in self._seen:
                    self._seen.add(pair)
                    new.append((pair[0], pair[1], int(time.time())))
                    self._pending.append(pair)
        return new

    def flush(self) -> None:
        """批量落库（60 秒节流/退出时）。"""
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._pending or self._conn is None:
            self._pending = []
            return
        try:
            now = int(time.time())
            self._conn.executemany(
                "INSERT OR IGNORE INTO known(proc, target, first_seen) VALUES(?,?,?)",
                [(p, t, now) for p, t in self._pending])
            self._conn.commit()
        except sqlite3.DatabaseError:
            pass
        self._pending = []

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

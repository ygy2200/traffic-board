# -*- coding: utf-8 -*-
"""串行跑全部测试，并且**让退出码说真话**。

为什么不用 shell 的 `for f in tests/adv_*.py; do python "$f" | tail -3; done`：
管道最后一段是 tail，循环里拿到的 rc 永远是 tail 的 0——测试本身 rc=1 也会显示全绿。
2026-10-31 就发生过一次：adv_release_tools 解码崩了，外层却报 rc=0。

    python scripts/test_all.py             # adv + render
    python scripts/test_all.py --adv       # 只跑对抗测试
    python scripts/test_all.py --render    # 只跑离屏/像素闸门
    python scripts/test_all.py --list      # 只列出会跑哪些

任何一套 rc != 0，整体 rc=1，并打印那套输出的末尾。
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# 控制台可能是 GBK：子进程输出里带 emoji 时 print 会抛 UnicodeEncodeError，
# 把「测试失败」伪装成 runner 崩溃，末尾那段最有用的输出反而看不到。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


def suites(kind: str) -> list[Path]:
    pats = {"adv": "adv_*.py", "render": "render_*.py"}
    return sorted(p for p in TESTS.glob(pats[kind]))


def run_one(path: Path, timeout: int) -> tuple[int, str, float]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t0 = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, str(path)], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out, time.perf_counter() - t0
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
        return 124, out + f"\nTIMEOUT {timeout}s", time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adv", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--match", default="", help="只跑文件名包含该子串的套件")
    ap.add_argument("--timeout", type=int, default=900, help="单套超时秒数")
    a = ap.parse_args()
    kinds = ["adv", "render"] if not (a.adv or a.render) else \
            [k for k in ("adv", "render") if getattr(a, k)]
    picked = [p for k in kinds for p in suites(k) if a.match in p.name]
    if a.list:
        for p in picked:
            print(p.relative_to(ROOT).as_posix())
        return 0

    fails: list[tuple[Path, int, str]] = []
    t0 = time.perf_counter()
    for i, p in enumerate(picked, 1):
        rc, out, dt = run_one(p, a.timeout)
        print(f"[{i:02d}/{len(picked)}] rc={rc} {dt:6.1f}s {p.relative_to(ROOT).as_posix()}")
        if rc != 0:
            fails.append((p, rc, out))
            tail = "\n".join(out.strip().splitlines()[-12:])
            print("---- 失败输出末尾 ----\n" + tail + "\n----------------------")
    total = time.perf_counter() - t0
    print(f"\n{len(picked) - len(fails)}/{len(picked)} 套通过，用时 {total:.1f}s")
    if fails:
        print("失败清单：")
        for p, rc, _ in fails:
            print(f"  rc={rc} {p.relative_to(ROOT).as_posix()}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""端到端退出路径测试：真实子进程跑生产 closeEvent，断言退出码=0 且不挂死。

背景：2026-10-01 用户报告"有时候退出会报错"（实测 40% 复现 SIGSEGV），修复靠
TerminateProcess 跳过 CRT 清理（v1.5.4）。adv_exit.py 只在单元层测并发关库，
没有任何测试验证"进程真的以 0 退出"，所以本文件补这一层——多轮重复正是为了
抓间歇性（flaky）而不是逻辑分支。

LOCALAPPDATA 指向临时目录：QSettings 与 known/history 库全部隔离，不碰用户配置。
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "_exit_driver.py"
ROUNDS = 8
TIMEOUT = 90


def _run(mode: str, rounds: int = ROUNDS):
    tmp = tempfile.mkdtemp(prefix="tb_proc_exit_")
    env = dict(os.environ)
    env["LOCALAPPDATA"] = tmp
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    results = []
    for _ in range(rounds):
        start = time.time()
        try:
            p = subprocess.run([sys.executable, str(DRIVER), mode, "3"],
                               capture_output=True, timeout=TIMEOUT, env=env)
            rc, out, err, hung = p.returncode, p.stdout.decode("utf-8", "replace"), \
                p.stderr.decode("utf-8", "replace"), False
        except subprocess.TimeoutExpired as e:
            rc = "HUNG>%ds" % TIMEOUT
            out = (e.stdout or b"").decode("utf-8", "replace")
            err = (e.stderr or b"").decode("utf-8", "replace")
            hung = True
        results.append((rc, time.time() - start, out, err, hung))
    return results, tmp


def main() -> int:
    failures = []
    for mode in ("quit", "close", "hide"):
        results, _tmp = _run(mode)
        rcs = [r[0] for r in results]
        bad = [r for r in results if r[0] != 0]
        hangs = [r for r in results if r[4]]
        slow = max(r[1] for r in results)
        print(f"[{mode}] rc={rcs}  worst_elapsed={slow:.1f}s")
        if mode == "hide":
            alive = sum(1 for r in results if "ALIVE_AFTER_CLOSE" in r[2])
            print(f"[hide] 关窗后进程存活证据: {alive}/{len(results)}")
            if alive != len(results):
                failures.append(f"hide: {len(results) - alive} 次关窗后进程未存活（默认应是最小化）")
        if bad:
            failures.append(f"{mode}: {len(bad)}/{len(results)} 次退出码非 0")
            first = bad[0]
            tail = (first[3] or first[2])[-600:]
            print(f"  首个非零样本 rc={first[0]} elapsed={first[1]:.1f}s\n  {tail}")
        if hangs:
            failures.append(f"{mode}: {len(hangs)} 次挂死超过 {TIMEOUT}s")

    print()
    if failures:
        print("adv_process_exit 失败：")
        for f in failures:
            print("  -", f)
        return 1
    print(f"adv_process_exit 全部通过（3 模式 × {ROUNDS} 轮 = {3 * ROUNDS} 次真实退出，全部 rc=0）")
    return 0


if __name__ == "__main__":
    from _qt_harness import hard_exit
    hard_exit(main())

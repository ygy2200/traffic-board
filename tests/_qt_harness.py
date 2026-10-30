# -*- coding: utf-8 -*-
"""离屏渲染/冒烟脚本的统一收尾。

生产 closeEvent 用 TerminateProcess 跳过 CRT 清理（见 ui/main_window._hard_exit）。
测试脚本若走 Python 正常退出，解释器拆除阶段会撞上仍存活的 Qt 对象：
实测退出码 0xC0000409（fail-fast）或挂死，导致"打印全部通过"与退出码不一致。
收尾统一走与生产相同的硬终止路径，退出码才是可信信号。

真实退出路径（closeEvent → worker.wait → agg.stop → TerminateProcess）由
tests/adv_process_exit.py 端到端覆盖，本模块只负责渲染类脚本的收尾。

隔离设置：Qt 测试要写 QSettings（悬浮条位置、排序偏好…），而同一进程里被测控件
自己也 new 了 QSettings 实例——实例间缓存会互相覆盖，进程内"还原"实测不可靠
（曾在注册表留下 @Point(999999 999999)）。所以走 run_isolated()：父进程用 reg.exe
快照整棵设置树 → 子进程随便写 → 父进程用 reg.exe 精确还原并回读比对。
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time


REG_ROOT = r"HKCU\Software\TrafficBoard\TrafficBoard"
_VALUE_RE = re.compile(r"^\s+(?P<name>\S+)\s+(?P<type>REG_[A-Z]+)\s+(?P<data>.*)$")


def use_temp_data_dir(tag: str = "") -> str:
    """把 SQLite 数据目录换成本进程专属临时目录。

    实测：渲染冒烟起真实 worker 线程，会往 %LOCALAPPDATA%\\TrafficBoard\\history.sqlite
    每分钟落库——那正是用户正在运行的实例在写的同一个库（现场看到测试进程打开
    生产库的 -wal/-shm）。测试不该往用户 30 天历史里写假数据。
    """
    d = tempfile.mkdtemp(prefix="tb-test-%s%s-" % (tag or "", os.getpid()))
    os.environ["TB_DATA_DIR"] = d
    return d


def shoot(widget, path, app, settle_ms: int = 400) -> None:
    """离屏截图。不用嵌套 exec()。

    旧写法 QTimer.singleShot + app.exec() + _snap 里 app.quit() 有丢退出请求的风险：
    py-spy 现场是主线程停在 app.exec()、三张 PNG 早已落盘、CPU 归零，挂 19 分钟不自报。
    改成有限时间转圈处理事件，最坏情况是截图断言失败，不会无声卡死。
    """
    widget.show()
    deadline = time.monotonic() + settle_ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    widget.repaint()
    assert widget.grab().save(str(path)), f"保存失败: {path}"
    print(f"{path.name}  {path.stat().st_size} bytes")
    widget.hide()


def _reg_tree() -> dict:
    """整棵设置树 {键: {值名: (类型, 数据)}}——主窗口会写多组，只快照一组不够。"""
    r = subprocess.run(["reg", "query", REG_ROOT, "/s"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    if r.returncode != 0:
        return {}
    tree, cur = {}, None
    for line in (r.stdout or "").splitlines():
        if line.startswith("HKEY_"):
            cur = line.strip()
            tree[cur] = {}
            continue
        m = _VALUE_RE.match(line)
        if m and cur:
            name = m.group("name")
            if name.startswith("("):
                # 这是键的"默认值"（(默认)/(Default)）：QSettings 从不写它，
                # 而 reg add 一旦碰过这个键就会把它实体化出来——不能进快照，否则还原必假报
                continue
            tree[cur][name] = (m.group("type"), m.group("data").rstrip())
    return tree


def am_child() -> bool:
    """当前进程是不是 run_isolated 派出的子进程（可随便写设置，还原由父进程负责）。"""
    return os.environ.get("TB_ISOLATED_CHILD") == "1"


def run_isolated(child: str) -> int:
    """快照 → 跑 child → 还原 → 回读比对。返回 child 退出码（还原失败一定非 0）。"""
    def reg(*args):
        return subprocess.run(["reg", *args], capture_output=True,
                              text=True, encoding="gbk", errors="replace")

    before = _reg_tree()
    rc = subprocess.run([sys.executable, child],
                        env=dict(os.environ, TB_ISOLATED_CHILD="1")).returncode
    now = _reg_tree()
    for key, vals in before.items():
        if key not in now:
            reg("add", key, "/f")                   # 键被整个删掉时才重建（碰它会实体化默认值）
        for name, (vtype, data) in vals.items():
            reg("add", key, "/v", name, "/t", vtype, "/d", data, "/f")
    for key in now:
        for name in now[key]:
            if name not in before.get(key, {}):
                reg("delete", key, "/v", name, "/f")
    for key in now:
        if key not in before:
            reg("delete", key, "/f")
    after = _reg_tree()
    if after != before:
        print("设置未还原: before=%s\nafter=%s" % (before, after))
        return rc or 1
    print("设置已按注册表原样还原（%d 键 / %d 值）"
          % (len(before), sum(len(v) for v in before.values())))
    return rc


def hard_exit(code: int = 0) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        if kernel32.TerminateProcess(ctypes.c_void_p(-1), code & 0xFFFFFFFF):
            return
    except Exception:
        pass
    os._exit(code)

# -*- coding: utf-8 -*-
"""网络体检引擎：固化 2026-10-03 FlClash TUN 排障经验的 10 项检查。

设计约束：
- 一次性动作（手动触发/进页），不做常驻轮询；线程池并发，单项内部自带超时，
  整体 14s 截止（超时的项降级为 warn，不抛异常穿透）；
- 所有检查对畸形输入免疫：注册表读失败/乱码输出/子进程超时 → 明确的 severity；
- 互斥检查（tun×系统代理）是招牌项：TUN 与系统代理并存时 UWP 应用全挂
  （0x80072EFD，2026-10-03 Microsoft Store 瘫痪事故），必须 FAIL + 修复指引。
"""
from __future__ import annotations

import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List
from urllib.parse import quote

from .tun_detect import TunState, fakeip_probe

SEV_OK, SEV_INFO, SEV_WARN, SEV_FAIL = "ok", "info", "warn", "fail"
_SEV_ORDER = {SEV_FAIL: 0, SEV_WARN: 1, SEV_INFO: 2, SEV_OK: 3}

_STORE_MUTEX_NOTE = ("2026-10-03 实锤：FlClash 开 TUN 且系统代理并存时，"
                     "UWP 应用（Microsoft Store 等）被 Windows 禁止访问 127.0.0.1 "
                     "代理，全部请求 0x80072EFD 失败。")


@dataclass
class HealthItem:
    id: str
    name: str
    severity: str          # ok / info / warn / fail
    detail: str            # 一句话结论
    evidence: str = ""     # 展开可见的原始证据
    fix: str = ""          # fail/warn 时的修复指引


@dataclass
class HealthReport:
    items: List[HealthItem] = field(default_factory=list)
    ts: float = 0.0
    duration: float = 0.0

    def get(self, item_id: str) -> "HealthItem | None":
        for it in self.items:
            if it.id == item_id:
                return it
        return None

    def worst(self) -> str:
        if not self.items:
            return SEV_INFO
        return min((it.severity for it in self.items),
                   key=lambda s: _SEV_ORDER.get(s, 99))

    def sorted_items(self) -> List[HealthItem]:
        """fail/warn 置顶，其余按固定 id 顺序。"""
        return sorted(self.items,
                      key=lambda it: (_SEV_ORDER.get(it.severity, 99), it.id))


# ---------- 工具 ----------

def _reg_proxy_enabled() -> bool:
    """读 HKCU 系统代理开关；读不到（键缺失/损坏）按 False 处理。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Internet Settings") as k:
            v, _ = winreg.QueryValueEx(k, "ProxyEnable")
            return bool(v)
    except Exception:
        return False


def _reg_proxy_server() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Internet Settings") as k:
            v, _ = winreg.QueryValueEx(k, "ProxyServer")
            return str(v or "")
    except Exception:
        return ""


def _reg_proxy_port() -> int:
    """从注册表 ProxyServer 解析端口（'127.0.0.1:7890' / 'http=127.0.0.1:7890;https=…'）。
    解析不到返回 0。"""
    raw = _reg_proxy_server()
    if not raw:
        return 0
    part = raw.split("=")[-1].strip()     # 取 'http=…' 的值段
    tail = part.rsplit(":", 1)[-1].strip()
    try:
        p = int(tail)
        return p if 0 < p <= 65535 else 0
    except ValueError:
        return 0


def _api_mixed_port(agg, timeout: float = 2.0) -> int:
    """从 mihomo /configs 读 mixed-port（回退 port/socks-port）。读不到返回 0。"""
    st = agg.clash.snapshot()
    if not st.online or not st.port:
        return 0
    try:
        s = _direct_session()
        r = s.get(f"http://127.0.0.1:{st.port}/configs", timeout=timeout)
        data = r.json() if r.ok else None
        if isinstance(data, dict):
            for key in ("mixed-port", "port", "socks-port"):
                v = data.get(key)
                if isinstance(v, int) and 0 < v <= 65535:
                    return v
    except Exception:
        pass
    return 0


def _resolve_proxy_port(agg) -> int:
    """本机代理入口端口发现（R4 修复：旧代码写死 7890，用户 mixed-port
    不同（如 7897）或 TUN-only 无 mixed-port 时必误报）。
    优先级：系统代理注册表 > mihomo /configs > 常见端口 7890。"""
    return _reg_proxy_port() or _api_mixed_port(agg) or 7890


def _port_listening(port: int, timeout: float = 0.6) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        return ok
    except Exception:
        return False


def _direct_session() -> "requests.Session":
    """不走环境变量代理的会话（体检测的是网络层本身）。
    requests 懒加载（R6）：导入 ~277ms，只服务体检线程。"""
    import requests
    s = requests.Session()
    s.trust_env = False
    return s


def _run_cmd(args: List[str], timeout: float = 4.0) -> str:
    """子进程跑命令，GBK 解码（Windows 中文控制台输出）；任何异常返回空串。"""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or b"").decode("gbk", errors="replace")
    except Exception:
        return ""


# ---------- 检查项（每项一个函数，入参=Aggregator）----------

def _proxy_egress_ok(port: int = 7890, timeout: float = 3.0) -> bool:
    """走本机代理测境外可达（用户实际翻墙路径）。"""
    try:
        s = _direct_session()
        r = s.get("https://www.google.com/generate_204", timeout=timeout,
                  proxies={"http": f"http://127.0.0.1:{port}",
                           "https": f"http://127.0.0.1:{port}"})
        return r.status_code == 204
    except Exception:
        return False


def _direct_egress_ok(timeout: float = 3.0) -> bool:
    """不走任何代理直测境外（TUN 模式下流量已被内核接管，再走 7890 反而误判）。"""
    try:
        r = _direct_session().get("https://www.google.com/generate_204", timeout=timeout)
        return r.status_code == 204
    except Exception:
        return False


def _check_overall(agg) -> HealthItem:
    """总状态（用户视角第一问：我的代理生效了吗？）。

    系统代理开 + 节点出站失败 = "代理已开启但境外不可达"（fail，引导换节点）；
    这与"没开代理"是两回事，2026-10-04 用户反馈"我开代理他说我没开"的根因
    就是旧版体检没有这一项，黑话淹没结论。

    R4 修复：端口不再写死 7890（按注册表/API 发现）；TUN 模式下直测出站，
    不再经 7890（TUN-only 用户没有 7890 监听，旧代码必报假 FAIL）。"""
    tun_on = agg.tun.snapshot() == TunState.ON
    proxy_on = _reg_proxy_enabled()
    if tun_on:
        if _direct_egress_ok():
            return HealthItem("overall", "代理总状态", SEV_OK,
                              "TUN 模式生效中，境外可达 ✓")
        return HealthItem("overall", "代理总状态", SEV_FAIL,
                          "TUN 已开启但境外不可达——多半是当前节点问题"
                          "（看下方「节点可用性」选一个能用的）",
                          fix="在 FlClash 里切换到体检列出的可用节点")
    if proxy_on:
        port = _resolve_proxy_port(agg)
        listening = _port_listening(port)
        if not listening:
            return HealthItem("overall", "代理总状态", SEV_FAIL,
                              f"系统代理开着但 {port} 端口无人监听——代理软件可能刚退出",
                              evidence=f"ProxyEnable=1；127.0.0.1:{port} 连接失败",
                              fix="重新打开 FlClash（或关闭系统代理开关恢复直连）")
        if _proxy_egress_ok(port):
            return HealthItem("overall", "代理总状态", SEV_OK,
                              f"系统代理模式生效中（{port}），境外可达 ✓")
        return HealthItem("overall", "代理总状态", SEV_FAIL,
                          f"代理已开启（{port}），但境外出站不通——多半是当前节点问题"
                          "（国内直连正常）（看下方「节点可用性」选一个能用的）",
                          evidence=f"走 127.0.0.1:{port} 访问 google 超时；"
                                   "这不是「没开代理」，是节点不可用",
                          fix="在 FlClash 里切换到体检列出的可用节点")
    return HealthItem("overall", "代理总状态", SEV_INFO,
                      "当前未开启任何代理（直连网络）——看板照常工作",
                      fix="需要翻墙时打开 FlClash（系统代理或 TUN 任一）")


def _pick_main_group(proxies: dict) -> dict:
    """从 /proxies 载荷选主策略组（节点最多的 Selector/URLTest/Fallback 组）。

    畸形输入免疫：非 dict/空表返回 {}。"""
    if not isinstance(proxies, dict):
        return {}
    groups = [v for k, v in proxies.items()
              if isinstance(v, dict)
              and v.get("type") in ("Selector", "URLTest", "Fallback")
              and isinstance(k, str) and k != "GLOBAL"]
    if not groups:
        return {}
    return max(groups, key=lambda g: len(g.get("all") or []))


def _check_nodes(agg) -> HealthItem:
    """节点可用性：实测当前节点 + 备选节点延迟（mihomo delay API）。

    补全"发现节点不通 → 知道该切哪个"的闭环（2026-10-04 复盘：旧版体检
    只会说"节点问题"，用户仍要去 FlClash 里盲试）。只读 API，不改选中节点。"""
    st = agg.clash.snapshot()
    if not st.online:
        return HealthItem("nodes", "节点可用性", SEV_INFO, "API 离线，跳过节点探测")
    port = st.port or 9090
    base = f"http://127.0.0.1:{port}"
    try:
        s = _direct_session()   # R4：禁环境代理变量，防 http_proxy 瘫痪本机 API 调用
        r = s.get(f"{base}/proxies", timeout=2.5)
        proxies = (r.json() or {}).get("proxies") or {}
    except Exception:
        return HealthItem("nodes", "节点可用性", SEV_WARN,
                          "无法读取节点列表（API 无应答）")
    main = _pick_main_group(proxies)
    if not main:
        return HealthItem("nodes", "节点可用性", SEV_INFO, "未发现策略组（无订阅？）")
    now = main.get("now") or ""
    pool = [n for n in (main.get("all") or [])
            if isinstance(n, str) and n not in ("DIRECT", "REJECT") and n != now][:6]
    test_list = ([now] if now else []) + pool
    if not test_list:
        return HealthItem("nodes", "节点可用性", SEV_INFO, "该分组无可选节点")

    def _delay(name: str):
        try:
            u = (f"{base}/proxies/{quote(name, safe='')}/delay"
                 f"?timeout=2500&url={quote('https://www.google.com/generate_204', safe='')}")
            rr = _direct_session().get(u, timeout=4)
            return name, int((rr.json() or {}).get("delay") or 0)
        except Exception:
            return name, 0

    results: Dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for name, d in ex.map(_delay, test_list):
            results[name] = d

    def _s(n: str) -> str:
        short = n[:24]
        return f"{short}({results[n]}ms)" if results.get(n) else f"{short}(不通)"

    alive = sorted(((n, d) for n, d in results.items() if d > 0), key=lambda x: x[1])
    alive_txt = "、".join(_s(n) for n, _ in alive[:4]) or "无"
    ev = f"当前节点：{_s(now) if now else '(未知)'}；实测 {len(results)} 个，可用 {len(alive)} 个"

    if now and results.get(now, 0) > 0:
        return HealthItem("nodes", "节点可用性", SEV_OK,
                          f"当前节点可用：{_s(now)}；备用可用：{alive_txt}", evidence=ev)
    if alive:
        return HealthItem("nodes", "节点可用性", SEV_FAIL,
                          f"当前节点不可用！可用备选：{alive_txt}",
                          evidence=ev,
                          fix="在 FlClash 里切换到上面列出的可用节点之一")
    return HealthItem("nodes", "节点可用性", SEV_FAIL,
                      f"实测 {len(results)} 个节点全部不可用（机场故障或出境网络中断）",
                      evidence=ev,
                      fix="去机场官网检查订阅状态；或切手机热点后重测")


def _check_api_online(agg) -> HealthItem:
    st = agg.clash.snapshot()
    if st.online:
        return HealthItem("api", "mihomo API 在线", SEV_OK,
                          f"外部控制器 127.0.0.1:{st.port} 可用（{st.version}），"
                          f"当前 {len(st.conns)} 条连接",
                          evidence=f"version={st.version} port={st.port} "
                                   f"conns={len(st.conns)}")
    return HealthItem("api", "mihomo API 在线", SEV_WARN,
                      "未发现 mihomo 外部控制器，看板降级为传统采集模式",
                      fix="打开 FlClash 后自动恢复；若确认已开，检查其「外部控制器」设置")


def _check_tun(agg) -> HealthItem:
    st = agg.tun.snapshot()
    if st is TunState.ON:
        return HealthItem("tun", "TUN 模式", SEV_OK,
                          "TUN 接管生效（fake-ip DNS 行为探测命中）")
    if st is TunState.UNKNOWN:
        return HealthItem("tun", "TUN 模式", SEV_WARN,
                          "内核自报 TUN 开启但行为探测不符——看板保守使用传统模式",
                          fix="重开一次 FlClash 的 TUN 开关；若持续，反馈给开发者")
    return HealthItem("tun", "TUN 模式", SEV_INFO,
                      "TUN 未启用（用系统代理时不需要 TUN，属正常选择）")


def _check_proxy_mutex(agg) -> HealthItem:
    """招牌项：TUN×系统代理互斥（UWP 瘫痪事故的根因）。

    TUN 状态 UNKNOWN（API 自报开但行为证据没跟上）时同样告警：
    存疑比放过安全——若 TUN 实际在运行，后果相同。"""
    tun_st = agg.tun.snapshot()
    tun_on = tun_st == TunState.ON
    tun_suspect = tun_st == TunState.UNKNOWN
    proxy_on = _reg_proxy_enabled()
    server = _reg_proxy_server()
    if proxy_on and (tun_on or tun_suspect):
        if tun_on:
            return HealthItem("mutex", "TUN×系统代理互斥", SEV_FAIL,
                              "TUN 与系统代理同时开启——UWP 应用（Microsoft Store 等）会全部断网",
                              evidence=f"ProxyEnable=1 指向 {server or '127.0.0.1:?'}，"
                                       f"且 TUN 判定 ON。{_STORE_MUTEX_NOTE}",
                              fix="在 FlClash 界面关闭「系统代理」开关，只留 TUN"
                                  "（浏览器等桌面应用不受影响，流量仍被 TUN 接管）")
        return HealthItem("mutex", "TUN×系统代理互斥", SEV_WARN,
                          "TUN 状态存疑且系统代理开启——若 TUN 实际在运行，UWP 应用会断网",
                          evidence=f"ProxyEnable=1 指向 {server or '127.0.0.1:?'}。"
                                   f"{_STORE_MUTEX_NOTE}",
                          fix="建议在 FlClash 界面关闭「系统代理」开关（只留 TUN）")
    if proxy_on and not tun_on:
        return HealthItem("mutex", "TUN×系统代理互斥", SEV_INFO,
                          f"系统代理模式（{server or '127.0.0.1:7890'}）——"
                          "UWP 应用不支持走系统代理属正常现象")
    return HealthItem("mutex", "TUN×系统代理互斥", SEV_OK,
                      "无冲突（TUN 与系统代理未并存）")


def _check_uwp_loopback(agg) -> HealthItem:
    out = _run_cmd(["CheckNetIsolation", "LoopbackExempt", "-s"])
    if not out:
        return HealthItem("uwp", "UWP loopback 豁免", SEV_INFO,
                          "无法读取豁免清单（需权限），跳过")
    has_entries = "S-1-15" in out          # 豁免条目含 AppContainer SID
    proxy_on = _reg_proxy_enabled()
    if proxy_on and not has_entries:
        return HealthItem("uwp", "UWP loopback 豁免", SEV_WARN,
                          "系统代理模式下 UWP 应用无法连接本机代理（豁免清单为空）",
                          evidence=out[-400:])
    if has_entries:
        return HealthItem("uwp", "UWP loopback 豁免", SEV_INFO,
                          "存在豁免条目", evidence=out[-400:])
    return HealthItem("uwp", "UWP loopback 豁免", SEV_OK,
                      "豁免清单为空（TUN 模式下不需要豁免）")


def _check_fakeip(agg) -> HealthItem:
    probe = fakeip_probe()
    if probe == "fake":
        return HealthItem("fakeip", "DNS 劫持（fake-ip）", SEV_OK,
                          "全系统 DNS 正被 mihomo 接管（fake-ip 198.18.0.0/15）")
    if probe == "real":
        tun_on = agg.tun.snapshot() == TunState.ON
        if tun_on:
            return HealthItem("fakeip", "DNS 劫持（fake-ip）", SEV_WARN,
                              "TUN 判定 ON 但 DNS 行为探测未被劫持——数据源可能已回退传统模式")
        return HealthItem("fakeip", "DNS 劫持（fake-ip）", SEV_INFO,
                          "DNS 未被劫持（TUN 未开时的正常现象）",
                          evidence="www.google.com 查询返回真实/污染地址")
    return HealthItem("fakeip", "DNS 劫持（fake-ip）", SEV_WARN,
                      "DNS 探测超时（无网络或 UDP 53 被特殊处理）")


def _check_egress(agg) -> HealthItem:
    """直连出境（网络层事实，诊断用信息）：墙内环境不通是常态，不是故障——
    "代理路径"的可达性由总状态项（overall）负责判定。"""
    t0 = time.time()
    try:
        r = _direct_session().get("https://www.google.com/generate_204", timeout=3)
        return HealthItem("egress", "直连出境（诊断）", SEV_INFO,
                          f"直连境外可达（{time.time()-t0:.1f}s，未经代理）")
    except Exception:
        return HealthItem("egress", "直连出境（诊断）", SEV_INFO,
                          "直连境外不可达（境内网络常态，不影响代理翻墙）",
                          evidence="此为网络层诊断信息；代理路径是否可用看「代理总状态」")


def _check_proxy_port(agg) -> HealthItem:
    """代理入口端口与 mihomo 内核活性（端口按注册表/API 发现，不再写死 7890；
    测国内站点——境外可达性归总状态项）。"""
    port = _resolve_proxy_port(agg)
    if not _port_listening(port):
        proxy_on = _reg_proxy_enabled()
        tun_on = agg.tun.snapshot() == TunState.ON
        if proxy_on and not tun_on:
            return HealthItem("pport", f"代理端口 {port}", SEV_FAIL,
                              f"系统代理指向 {port} 但端口无人监听——浏览器会全部断网",
                              fix="重新打开 FlClash，或关闭系统代理开关")
        return HealthItem("pport", f"代理端口 {port}", SEV_INFO,
                          f"{port} 未监听（TUN 模式或未开代理时属正常）")
    try:
        t0 = time.time()
        s = _direct_session()
        r = s.get("https://www.baidu.com", timeout=3,
                  proxies={"http": f"http://127.0.0.1:{port}",
                           "https": f"http://127.0.0.1:{port}"})
        if r.status_code == 200:
            return HealthItem("pport", f"代理端口 {port}", SEV_OK,
                              f"mihomo 内核工作正常（{time.time()-t0:.1f}s 国内应答）")
        return HealthItem("pport", f"代理端口 {port}", SEV_WARN,
                          f"{port} 可连但内核应答异常（HTTP {r.status_code}）")
    except Exception as e:
        return HealthItem("pport", f"代理端口 {port}", SEV_WARN,
                          f"{port} 可连但内核无应答",
                          evidence=f"{e.__class__.__name__}"[:80])


def _check_proc_attr(agg) -> HealthItem:
    """进程归因健康（R4 新增，研究实锤风险）：
    mihomo 的 find-process-mode=off 时 /connections 不返回 process 字段，
    TUN 单源模式下整个看板会变成「未知进程」且旧版无任何提示。
    三站调研一致指出该配置决定 TUN 下归因有无（clashopen/clashsurge/
    clashknow，2026-10）。行为检测：TUN 生效 + 有连接 + 全部无进程名。"""
    tun_on = agg.tun.snapshot() == TunState.ON
    st = agg.clash.snapshot()
    if not (tun_on and st.online):
        return HealthItem("procattr", "进程归因", SEV_INFO,
                          "当前非 TUN 单源模式，进程名来自系统连接表，不受该配置影响")
    if not st.conns:
        return HealthItem("procattr", "进程归因", SEV_INFO,
                          "当前无活动连接，无法采样进程名（有流量后再看）")
    named = sum(1 for c in st.conns if c.process)
    if named:
        return HealthItem("procattr", "进程归因", SEV_OK,
                          f"{named}/{len(st.conns)} 条连接带进程名，软件排行归因正常")
    return HealthItem("procattr", "进程归因", SEV_FAIL,
                      "内核未返回任何进程名——软件排行会全部显示「未知进程」",
                      evidence="TUN 生效且连接存在，但 process 字段全为空。"
                               "mihomo 的 find-process-mode=off 会关闭 TUN 进程匹配",
                      fix="在 FlClash 配置文件中设置 find-process-mode: strict"
                          "（或 always），保存后重启内核")


def _check_dns_baseline(agg) -> HealthItem:
    """系统解析器视角（与 fakeip 探测互补：它走系统 DNS 配置）。"""
    try:
        infos = socket.getaddrinfo("www.google.com", 443, socket.AF_INET)
        ip = infos[0][4][0] if infos else ""
        try:
            import ipaddress
            fake = ipaddress.ip_address(ip) in ipaddress.ip_network("198.18.0.0/15")
        except ValueError:
            fake = False
        if fake:
            return HealthItem("dnsbl", "系统 DNS 解析", SEV_OK,
                              f"解析被接管（返回 fake-ip {ip}）")
        return HealthItem("dnsbl", "系统 DNS 解析", SEV_INFO,
                          f"解析结果 {ip}（境外域名经校园网直连解析，"
                          "结果可能被污染，属环境常态）")
    except Exception as e:
        return HealthItem("dnsbl", "系统 DNS 解析", SEV_WARN,
                          "解析失败（DNS 服务器不可达或超时）",
                          evidence=f"{e.__class__.__name__}"[:80])


def _check_vnic_conflict(agg) -> HealthItem:
    tun_on = agg.tun.snapshot() == TunState.ON
    boosters = []
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            try:
                n = (p.info.get("name") or "").lower()
                if n.startswith(("leigod", "uu", "netuu")) and n:
                    boosters.append(n)
            except Exception:
                continue
    except Exception:
        pass
    boosters = sorted(set(boosters))
    if tun_on and boosters:
        return HealthItem("vnic", "加速器共存", SEV_WARN,
                          f"TUN 与游戏加速器（{', '.join(boosters)}）同时运行——"
                          "两者都会改路由，加速表现未验证",
                          fix="玩需要加速器的游戏前先实测；异常时关闭 TUN 或加速器其一")
    if boosters:
        return HealthItem("vnic", "加速器共存", SEV_INFO,
                          f"检测到加速器进程（{', '.join(boosters)}），当前与 TUN 无冲突")
    return HealthItem("vnic", "加速器共存", SEV_OK, "未检测到游戏加速器进程")


def _check_routes(agg) -> HealthItem:
    out = _run_cmd(["route", "print", "-4"])
    defaults = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "0.0.0.0":
            defaults.append(f"→ {parts[2]}  经 {parts[3]}  跃点 {parts[4]}")
    ev = "\n".join(defaults[-5:]) or "(无默认路由)"
    if not defaults:
        return HealthItem("route", "默认路由", SEV_WARN,
                          "未发现 IPv4 默认路由（可能断网）")
    tun_takeover = any(p.startswith("→ 198.18.") for p in defaults)
    if tun_takeover:
        return HealthItem("route", "默认路由", SEV_OK,
                          "TUN 以跃点 0 接管默认路由（物理路由保留备用）",
                          evidence=ev)
    return HealthItem("route", "默认路由", SEV_INFO,
                      f"{len(defaults)} 条默认路由（常规网络环境）", evidence=ev)


_CHECKS: Dict[str, Callable] = {
    "overall": _check_overall,
    "nodes": _check_nodes,
    "api": _check_api_online,
    "tun": _check_tun,
    "mutex": _check_proxy_mutex,
    "uwp": _check_uwp_loopback,
    "fakeip": _check_fakeip,
    "egress": _check_egress,
    "pport": _check_proxy_port,
    "dnsbl": _check_dns_baseline,
    "vnic": _check_vnic_conflict,
    "route": _check_routes,
    "procattr": _check_proc_attr,
}


class HealthEngine:
    """并发执行全部检查项；整体 14s 截止，超时项降级为 warn。"""

    def __init__(self, agg) -> None:
        self._agg = agg

    def run_all(self, deadline_s: float = 14.0) -> HealthReport:
        t0 = time.time()
        report = HealthReport(ts=t0)
        try:
            self._agg.start()   # 幂等：确保采集线程已拉起（体检可先于主界面 build 运行，
            # 否则 ClashApiPoller 线程未启动 → "未发现外部控制器"时序误报）
        except Exception:
            pass
        _wait_for_api(self._agg)
        deadline = time.time() + deadline_s
        ex = ThreadPoolExecutor(max_workers=6)
        try:
            futs = {ex.submit(fn, self._agg): cid for cid, fn in _CHECKS.items()}
            for fut, cid in futs.items():
                remain = deadline - time.time()
                try:
                    report.items.append(fut.result(timeout=max(0.3, remain)))
                except Exception as e:  # noqa: BLE001 超时/异常都归档为 warn
                    name = _CHECKS_META.get(cid, {}).get("name", cid)
                    report.items.append(HealthItem(
                        cid, name, SEV_WARN,
                        f"体检项执行超时或异常（{e.__class__.__name__}），已跳过"))
        finally:
            # 不等慢线程：超时项在后台自然结束（体检不阻塞 UI/退出；
            # 看板退出走 os._exit 兜底路径，不受此影响）
            ex.shutdown(wait=False, cancel_futures=True)
        report.duration = time.time() - t0
        report.items = report.sorted_items()
        return report


_CHECKS_META = {
    "overall": {"name": "代理总状态"}, "nodes": {"name": "节点可用性"},
    "api": {"name": "mihomo API 在线"},
    "tun": {"name": "TUN 模式"},
    "mutex": {"name": "TUN×系统代理互斥"}, "uwp": {"name": "UWP loopback 豁免"},
    "fakeip": {"name": "DNS 劫持（fake-ip）"}, "egress": {"name": "直连出境（诊断）"},
    "pport": {"name": "代理端口"}, "dnsbl": {"name": "系统 DNS 解析"},
    "vnic": {"name": "加速器共存"}, "route": {"name": "默认路由"},
    "procattr": {"name": "进程归因"},
}


def _wait_for_api(agg, timeout: float = 2.5) -> None:
    """等 ClashApiPoller 完成首次端口探测（新建实例立即体检时避免时序误报
    "未发现外部控制器"——2026-10-04 用户反馈复盘项）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if agg.clash.snapshot().online:
            return
        time.sleep(0.2)

if __name__ == "__main__":
    # 自测：对真实本机跑一轮体检
    from core.aggregator import Aggregator
    agg = Aggregator()
    eng = HealthEngine(agg)
    t0 = time.time()
    rep = eng.run_all()
    print(f"体检完成 {time.time()-t0:.1f}s 最差项={rep.worst()}")
    for it in rep.items:
        print(f"  [{it.severity:4}] {it.name}: {it.detail}")
    agg.stop()

# -*- coding: utf-8 -*-
"""物理网卡甄别：「全机流量」波形/历史只应计物理网卡。

为什么需要：TUN 模式下流量路径为 app → wintun 虚拟网卡 → mihomo → 物理网卡，
字节在虚拟网卡与物理网卡**各被计一次**（上传下行都是）。早期版本对
「所有非 lo 网卡」求和，TUN 开启后全机速率与历史总量虚高约 2 倍
（2026-10-20 R3 复盘实测确认该路径）。

判定策略（分层过滤 + 逐级兜底，全部为纯函数可单测）：
1. 名字命中虚拟网卡特征 → 排除。特征分两层防误伤：
   - 长子串（loopback/wintun/vethernet/pseudo/蓝牙…）：整名包含即中
   - 短词（lo/tap/tun/tunnel）：按分词精确匹配，防 "Adapter" 中 tap、
     "Local Area Connection" 中 lo 被误杀
2. 未启用（isup=False）的网卡 → 排除（本身不跑流量，排除后集合更稳）
3. 已启用但链路速率 speed==0 → 排除（虚拟适配器不报链路速率；物理网卡几乎都报）
4. 兜底：若过滤后集合为空（极端环境：唯一物理网卡不报速率等），
   逐级放宽回「全部已启用非回环」→「全部非回环」，绝不给用户看 0 流量。
"""
from __future__ import annotations

import re
from typing import Dict, Set

# 长子串特征：出现在网卡名任意位置即判虚拟（这些串本身足够特异）
_VIRTUAL_SUBSTR = (
    "loopback", "wintun", "wireguard", "openvpn", "vethernet", "pseudo",
    "bluetooth", "蓝牙", "virtualbox", "vmware", "vmnet", "hyper-v",
    "docker", "zerotier", "tailscale", "hamachi", "softether",
    "npcap", "winpcap",
)
# 短词特征：仅当分词后精确相等才判虚拟（防误伤）
_VIRTUAL_TOKENS = {"lo", "tap", "tun", "tunnel"}
_TOKEN_SPLIT = re.compile(r"[\s\-_*#:：、,，/\\()（）]+")


def _tokens(name: str):
    return [t for t in _TOKEN_SPLIT.split((name or "").strip().lower()) if t]


def _is_virtual_name(name: str) -> bool:
    low = (name or "").strip().lower()
    if not low:
        return True                      # 空名字不可信，按虚拟排除
    if any(part in low for part in _VIRTUAL_SUBSTR):
        return True
    return any(t in _VIRTUAL_TOKENS for t in _tokens(low))


def physical_nics(stats: Dict[str, object]) -> Set[str]:
    """从 psutil.net_if_stats() 的映射选出计入「全机流量」的网卡名集合。

    stats: {nic_name: 含 isup/speed 属性的对象}（duck-typed；测试可传
    简单命名空间）。任何畸形输入（None/缺字段）都不会抛异常。
    """
    if not isinstance(stats, dict) or not stats:
        return set()

    def _up(nic: str) -> bool:
        return bool(getattr(stats.get(nic), "isup", False))

    def _speed(nic: str) -> int:
        try:
            return int(getattr(stats.get(nic), "speed", 0) or 0)
        except (TypeError, ValueError):
            return 0

    named_ok = {n for n in stats if not _is_virtual_name(n)}
    if not named_ok:                     # 兜底 1：全是虚拟名 → 用全部
        named_ok = set(stats)
    up_ok = {n for n in named_ok if _up(n)}
    if not up_ok:                        # 兜底 2：没有已启用网卡 → 回到名字过滤集
        up_ok = named_ok
    speed_ok = {n for n in up_ok if _speed(n) > 0}
    if not speed_ok:                     # 兜底 3：无人报速率 → 用已启用集
        speed_ok = up_ok
    return speed_ok


if __name__ == "__main__":
    # 自测：构造三类拓扑
    from types import SimpleNamespace as NS

    def st(isup=True, speed=1000):
        return NS(isup=isup, speed=speed, mtu=1500, duplex=2)

    phys_only = physical_nics({"以太网": st(), "WLAN": st(isup=False)})
    assert phys_only == {"以太网"}, phys_only
    with_tun = physical_nics({"以太网": st(), "Wintun": st(speed=0),
                              "Loopback Pseudo-Interface 1": st(speed=1073),
                              "TAP-Windows Adapter": st(speed=0)})
    assert with_tun == {"以太网"}, with_tun
    # 防误伤：含 tap/lo 子串的物理名不得被排除
    tricky = physical_nics({"Ethernet Adapter": st(), "Local Area Connection": st(),
                            "以太网": st()})
    assert tricky == {"Ethernet Adapter", "Local Area Connection", "以太网"}, tricky
    only_virtual_up = physical_nics({"Wintun": st(speed=0), "Tap": st(speed=0)})
    assert only_virtual_up == {"Wintun", "Tap"}, only_virtual_up  # 兜底不返回空
    assert physical_nics({}) == set()
    assert physical_nics(None) == set()
    print("nics 自测通过")

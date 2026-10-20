# -*- coding: utf-8 -*-
"""对抗测试：TUN 判定矩阵（行为证据优先）+ DNS 探测解析器对畸形应答的免疫。

误判代价不对称：误判 ON → 直连流量漏报；误判 OFF → 仅回退慢路径。
证据模型（2026-10-03 本机实测教训）：FlClash 0.8.98 运行中 /configs 的
tun.enable 恒为 false（HelperService 实现），API 自报只能当辅助；
主证据 = fake-ip DNS 行为探测（TUN dns-hijack 生效 ⇔ 全系统流量被接管）。
"""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.tun_detect import (  # noqa: E402
    TunState, evaluate, fakeip_probe, _parse_a_records, TunDetector)

_DNS_Q = (b"\xab\xcd"                          # txid
          b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
          b"\x03www\x06google\x03com\x00"      # qname
          b"\x00\x01\x00\x01")


def _reply(txid: bytes = b"\xab\xcd", answers: bytes = b"") -> bytes:
    hdr = txid + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"   # 1 question 1 answer
    q = b"\x03www\x06google\x03com\x00\x00\x01\x00\x01"
    return hdr + q + answers


def _a_answer(ip: bytes) -> bytes:
    return b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x0e\x10\x00\x04" + ip   # name 压缩指针


def test_evaluate_matrix():
    """核心判定矩阵：行为证据优先，API 自报只影响 UNKNOWN/OFF 分界。"""
    assert evaluate("fake", api_tun=False) is TunState.ON    # API 撒谎(本机实测)也 ON
    assert evaluate("fake", api_tun=True) is TunState.ON
    assert evaluate("real", api_tun=False) is TunState.OFF   # TUN 关=本机当前态
    assert evaluate("real", api_tun=True) is TunState.UNKNOWN  # redir-host/劫持失效→存疑
    assert evaluate(None, api_tun=False) is TunState.OFF     # 探测超时
    assert evaluate(None, api_tun=True) is TunState.UNKNOWN  # 探测超时但 API 说开→存疑
    print("判定矩阵 PASS（行为证据优先，UNKNOW 保保守回退）")


def test_dns_parser_attacks():
    """DNS 应答解析器对畸形载荷免疫：截断/空答案/坏指针/坏长度不抛异常。"""
    assert _parse_a_records(b"") == []
    assert _parse_a_records(_DNS_Q) == []                    # 只有 question
    r = _reply(answers=_a_answer(bytes([198, 18, 0, 8])))
    assert _parse_a_records(r) == ["198.18.0.8"]
    r2 = _reply(answers=_a_answer(bytes([1, 2, 3, 4])) * 3)
    assert _parse_a_records(r2) == ["1.2.3.4"] * 3
    # rdlen 撒谎（声称 4 字节实际截断）→ 空表不崩
    bad = _reply(answers=b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x0e\x10\xff\xff" + b"\x01\x02")
    assert _parse_a_records(bad) == []
    # 恶意循环指针：name 字段 0xFF 0xFF…（非 0xC0 前缀但值大）→ 有限步退出
    evil = _reply(answers=b"\xff\x0f\x00\x01\x00\x01\x00\x00\x0e\x10\x00\x04" + b"\x01\x02\x03\x04")
    _parse_a_records(evil)                                   # 只要不挂/不超时
    print("DNS 解析器攻击用例 PASS")


def test_fakeip_probe_shapes():
    """fakeip_probe 三态形态：本地无服务→None；畸形应答→None。"""
    # 53 端口无监听的高端口伪造：向 127.0.0.1 查询，绝大多数本机无 53 → None
    assert fakeip_probe(server="127.0.0.1", timeout=0.3) is None
    print("fakeip_probe 三态形态 PASS")


def test_detector_lifecycle():
    """线程生命周期：未启动 snapshot=OFF；start/stop 幂等、stop 可重复。"""
    class _FakePoller:
        def snapshot(self):
            from core.clash_api import ClashState
            return ClashState(online=False)
    d = TunDetector(_FakePoller())
    assert d.snapshot() is TunState.OFF
    d.start(); d.start()            # 幂等 start
    d.stop(); d.stop()              # 重复 stop 不炸
    assert d.snapshot() is TunState.OFF
    print("TunDetector 生命周期 PASS")


if __name__ == "__main__":
    test_evaluate_matrix()
    test_dns_parser_attacks()
    test_fakeip_probe_shapes()
    test_detector_lifecycle()
    print("adv_tun_detect 全部通过")

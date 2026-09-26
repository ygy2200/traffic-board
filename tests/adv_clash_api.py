# -*- coding: utf-8 -*-
"""对抗测试：Clash API 数据解析对畸形载荷的免疫。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.clash_api import parse_connections_payload  # noqa: E402


def test_attacks():
    # 全空 / 非字典
    s = parse_connections_payload({})
    assert s.online and s.conns == []
    s = parse_connections_payload(None)  # type: ignore
    assert not s.online  # 非字典直接回离线空态
    s = parse_connections_payload([1, 2, 3])  # type: ignore
    assert not s.online
    # 畸形字段类型
    evil = {
        "uploadTotal": "abc", "downloadTotal": None,
        "connections": [
            None,                                   # 非字典条目
            {"metadata": None},                     # metadata 空
            {"metadata": "evil", "chains": "not-list", "upload": "12x"},
            {"metadata": {"process": None, "host": 123, "destinationPort": "443",
                          "destinationIP": "1.2.3.4", "network": ["tcp"]},
             "chains": [None, 123, "香港01"], "download": {"a": 1}},
            {"metadata": {"process": "x" * 5000, "destinationPort": "99999999999999999999"}},
        ],
    }
    s = parse_connections_payload(evil, "1.10.0")
    assert len(s.conns) == 4  # None 条目被跳过
    c = s.conns[2]
    assert c.host == "123" and c.dest_port == 443      # 数字 host 转 str，端口字符串转 int
    assert c.network == "['tcp']"                       # 网络字段畸形仅影响展示
    assert c.chains == [None, 123, "香港01"]            # chains 原样保留，展示层兜底
    assert c.upload == 0 and c.download == 0            # 非数字字节归 0
    assert len(s.conns[3].process) == 5000              # 超长进程名不截断不崩
    assert s.conns[3].dest_port == 0                    # 溢出端口归 0
    print("Clash 载荷攻击用例 PASS")


def test_node_of_semantics():
    from core.aggregator import Aggregator
    # 实测 mihomo chains 顺序：chains[0]=出口节点，其后为策略组
    assert Aggregator._node_of(["HK-01", "MyAirportGroup"]) == "HK-01"
    assert Aggregator._node_of(["DIRECT"]) == ""
    assert Aggregator._node_of([]) == ""
    assert Aggregator._node_of(None) == ""  # type: ignore
    assert Aggregator._node_of([None, "", "REJECT", "US-01"]) == "US-01"
    print("节点提取语义 PASS")


if __name__ == "__main__":
    test_attacks()
    test_node_of_semantics()
    print("adv_clash_api 全部通过")

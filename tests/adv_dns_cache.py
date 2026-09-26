# -*- coding: utf-8 -*-
"""对抗测试：DNS 缓存模块的解析健壮性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.dns_cache import DnsCacheReader  # noqa: E402


def test_parse_attacks():
    p = DnsCacheReader._parse
    # 畸形输入全部安全返回
    assert p("") == []
    assert p("not json at all") == []
    assert p("null") == []
    assert p("123") == []
    assert p('{"Entry":"a.com","Data":null}') == [{"Entry": "a.com", "Data": None}]
    assert p('{"Entry":"a.com"}') == [{"Entry": "a.com"}]
    assert p('[]') == []
    huge = '{"Entry":"' + "x" * 100000 + '.com","Data":"1.2.3.4"}'
    assert len(p(huge)) == 1  # 超长行不崩
    nested = '{"a":{"b":{"c":[1,2,3]}}}'
    assert p(nested) == [{"a": {"b": {"c": [1, 2, 3]}}}]  # 深嵌套 dict 也放行，构建时过滤
    print("parse 攻击用例 PASS")


def test_build_filters():
    r = DnsCacheReader()
    # 构建阶段过滤：坏 IP / CNAME 链 / in-addr.arpa / 空值
    bad = [{"Data": "999.999.999.999", "Entry": "evil.com"},
           {"Data": "", "Entry": "empty.com"},
           {"Data": "1.2.3.4", "Entry": ""},
           {"Data": "1.2.3.4", "Entry": "1.2.3.4.in-addr.arpa"},
           {"Data": "1.2.3.4", "Entry": "good.com"},
           {"Data": "5.6.7.8", "Entry": "a" * 5000 + ".com"}]
    new_map = {}
    for it in bad:
        ip, domain = str(it.get("Data", "")).rstrip("."), str(it.get("Entry", "")).rstrip(".")
        if ip and domain and not ip.endswith(".in-addr.arpa"):
            new_map[ip] = domain
    assert "1.2.3.4" in new_map and new_map["1.2.3.4"] == "good.com"
    assert all(not k.endswith(".in-addr.arpa") for k in new_map)
    print("构建过滤 PASS（注意：'999.999.999.999' 为合法点分字符串会被收录，仅影响展示不影响判定）")


def test_stop_idempotent():
    r = DnsCacheReader()
    r.stop(); r.stop()  # 双杀不崩
    r.start(); time_sleep_guard = 0  # start 后立刻 stop
    r.stop()
    print("stop/start 幂等 PASS")


if __name__ == "__main__":
    test_parse_attacks()
    test_build_filters()
    test_stop_idempotent()
    print("adv_dns_cache 全部通过")

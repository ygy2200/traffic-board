# -*- coding: utf-8 -*-
"""对抗测试：控制器密钥发现与 401 语义（R2 回归锁）。

攻击面：
1. config.yaml 解析：带引号/不带引号、注释掉、子树同名键（缩进行不能算）
2. external-controller 三种写法解析端口
3. 401 ≠ 端口没人监听：auth_failed 必须为真，且不带密钥时永不 online
4. 自动发现：401 后读配置拿密钥 → 同一轮探测内立刻成功
5. 密钥来源变更后刷新（用户在 FlClash 里改了 secret 且已重启看板配置）
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "\\src")

from core import clash_api  # noqa: E402
from core.clash_api import ClashApiPoller  # noqa: E402
from core.flconfig import parse_config, _as_port  # noqa: E402

SECRET = "abcdefghijkl0123456789ABCDEFGH"


# ---- 1/2: 解析 ----
def test_parse_secret_quoting():
    cfg = parse_config('secret: "%s"\nexternal-controller: "127.0.0.1:9090"\n' % SECRET)
    assert cfg.secret == SECRET and cfg.controller_port == 9090


def test_parse_secret_single_quote_and_bare():
    assert parse_config("secret: 'abc-123'").secret == "abc-123"
    assert parse_config("secret: abc-123").secret == "abc-123"


def test_parse_skips_commented_out():
    assert parse_config("secret:   # 用户没设密钥").secret == ""


def test_parse_ignores_nested_same_key():
    """缩进行的 secret 属于子树（如 profile 里的字段），不能当控制器密钥。"""
    text = "dns:\n  secret: nested-should-be-ignored\nsecret: top-level-ok\n"
    assert parse_config(text).secret == "top-level-ok"


def test_parse_ports():
    cfg = parse_config("mixed-port: 7890\nexternal-controller: ':9097'\n")
    assert cfg.mixed_port == 7890 and cfg.controller_port == 9097
    assert _as_port("0.0.0.0:0") == 0 and _as_port("70000") == 0 and _as_port("19090") == 19090


# ---- 3/4/5: 401 语义与自动发现 ----
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        want = self.headers.get("Authorization") or ""
        if want != f"Bearer {SECRET}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"message":"Unauthorized"}')
            return
        if self.path == "/version":
            body = '{"version":"v1.19.32","meta":"mihomo"}'
        elif self.path == "/connections":
            body = '{"connections":[{"id":"1","chains":["PROXY"],"upload":100,"download":200,' \
                   '"metadata":{"network":"tcp","host":"ex.com","process":"a.exe",' \
                   '"destinationPort":443}}],"upload_total":100,' \
                   '"download_total":200}'
        else:
            body = "{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_401_is_not_port_dead_and_auth_flag():
    """没有密钥来源时：401 端口不应被当成"没人监听"，auth_failed=True 且 offline。"""
    srv = _serve()
    port = srv.server_address[1]
    old_ports, old_read = clash_api._CANDIDATE_PORTS, clash_api.ClashApiPoller._discover_secret
    clash_api._CANDIDATE_PORTS = [port]
    clash_api.ClashApiPoller._discover_secret = lambda self: False
    try:
        p = ClashApiPoller()
        p.start()
        time.sleep(1.0)
        st = p.snapshot()
        assert st.online is False
        assert p._auth_failed is True, "401 被误判成端口不存在"
        p.stop()
    finally:
        clash_api._CANDIDATE_PORTS = old_ports
        clash_api.ClashApiPoller._discover_secret = old_read
        srv.shutdown()


def test_auto_discovery_revives_bytes():
    """401 → 读配置拿密钥 → 同一轮探测成功，字节数不再是 0。"""
    srv = _serve()
    port = srv.server_address[1]
    old_ports, old_read = clash_api._CANDIDATE_PORTS, clash_api.ClashApiPoller._discover_secret

    class Cfg:
        secret = SECRET
    discovered = {"n": 0}

    def fake_discover(self):
        discovered["n"] += 1
        self.secret = Cfg.secret
        self.secret_source = "config"
        return True

    clash_api._CANDIDATE_PORTS = [port]
    clash_api.ClashApiPoller._discover_secret = fake_discover
    try:
        p = ClashApiPoller()
        p.start()
        deadline = time.time() + 8
        while time.time() < deadline and not p.snapshot().online:
            time.sleep(0.2)
        st = p.snapshot()
        assert st.online is True, "自动发现密钥后仍离线"
        assert p.secret_source == "config" and discovered["n"] == 1
        assert st.auth_failed is False
        assert len(st.conns) == 1 and st.conns[0].download == 200, st.conns
        p.stop()
    finally:
        clash_api._CANDIDATE_PORTS = old_ports
        clash_api.ClashApiPoller._discover_secret = old_read
        srv.shutdown()


def test_secret_not_read_when_already_present():
    """手工传了密钥就不该去读别人的配置文件（避免覆盖用户显式设置）。"""
    srv = _serve()
    port = srv.server_address[1]
    old_ports, old_read = clash_api._CANDIDATE_PORTS, clash_api.ClashApiPoller._discover_secret
    calls = {"n": 0}

    def spy(self):
        calls["n"] += 1
        return False

    clash_api._CANDIDATE_PORTS = [port]
    clash_api.ClashApiPoller._discover_secret = spy
    try:
        p = ClashApiPoller(secret=SECRET)
        p.start()
        deadline = time.time() + 8
        while time.time() < deadline and not p.snapshot().online:
            time.sleep(0.2)
        assert p.snapshot().online is True
        assert p.secret_source == "manual"
        assert calls["n"] == 0, "已有密钥仍去读配置"
        p.stop()
    finally:
        clash_api._CANDIDATE_PORTS = old_ports
        clash_api.ClashApiPoller._discover_secret = old_read
        srv.shutdown()


def test_discover_only_on_change():
    """配置里的密钥与当前相同（改了但没生效）不返回 True，避免无限重试。"""
    p = ClashApiPoller(secret=SECRET)
    import core.flconfig as flconfig
    real_read = flconfig.read
    flconfig.read = lambda: type("C", (), {"secret": SECRET})()
    try:
        assert p._discover_secret() is False
        flconfig.read = lambda: type("C", (), {"secret": "rotated-new"})()
        assert p._discover_secret() is True and p.secret == "rotated-new"
    finally:
        flconfig.read = real_read


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("  PASS", fn.__name__)
    print(f"adv_secret 全部通过（{len(fns)} 例）")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _qt_harness import hard_exit
    hard_exit(main())

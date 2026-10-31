# -*- coding: utf-8 -*-
"""对抗测试：发布链自己的判据（R23 回归锁）。

为什么这道闸门值得存在：v1.6.4 发布时 GitHub 那一侧核对得很足，本机入口（桌面 .lnk）却整个忘了，
静默停在 1.6.3 两个版本——README 里明明写着那条不变量。文档提醒挡不住漏，判据得有测试。

覆盖面：
1. tag / 版本号 / 发布说明归档三者的一致性
2. 占位符扫描必须在拿 token 之前（否则发半份说明出去）
3. digest 前缀比较（'sha256:xxx' vs 'xxx'，v1.6.3 打印过假阴性）
4. zip 只数文件条目（v1.6.4 的「295 条目」被记成多收了 29 个依赖）
5. 产物版本身份要从 exe 里读（PYZ 压缩，二进制 grep 无效）
6. 桌面入口必须指向 out/<当前版本>
7. 附件可见性判据（Releases 页附件是懒加载的，tag 页 HTML 搜不到 zip 名）
"""
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT / "scripts"))

import tbbuild  # noqa: E402


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """子进程输出按 UTF-8 解，不随控制台代码页漂移。

    踩过的坑：测试进程带 PYTHONIOENCODING=utf-8 时子进程输出 UTF-8，但 text=True
    默认按 GBK 解码，解码线程抛 UnicodeDecodeError 后 r.stdout 变成 None，测试报的是
    TypeError 而不是子进程真正的结论。编码写死，闸门才量的是产品不是环境。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


# ---- 1: 一致性 ----
def test_tag_forms():
    assert tbbuild.check_tag("v1.6.5") is None
    for bad in ("1.6.5", "v1.6", "vv1.6.5", "v1.6.5.1", "v1.6-beta", "", None):
        assert tbbuild.check_tag(bad) is not None, f"{bad!r} 竟然算合法"


def test_repo_version_and_notes_archived():
    """版本号唯一的来源是 src/core/version.py；当前版本的发布说明必须已经进库归档。"""
    ver = tbbuild.repo_version()
    assert tbbuild.check_tag(f"v{ver}") is None, ver
    p = tbbuild.notes_path(f"v{ver}")
    assert p.is_file(), (
        f"{ver} 的发布说明没归档到 {p.relative_to(ROOT).as_posix()}——"
        "GitHub 上那份公开文本本机没有留档，下次无从对照")


# ---- 2: 占位符顺序 ----
def test_holes_and_stamp():
    body = "SHA256：`@SHA@`\n还有 @TODO@ 没填"
    assert set(tbbuild.find_holes(body)) == {"@SHA@", "@TODO@"}
    new, n = tbbuild.stamp_sha(body, "ab12cd")
    assert n == 1 and "@SHA@" not in new and tbbuild.find_holes(new) == ["@TODO@"]
    assert tbbuild.stamp_sha(new, "ab12cd")[1] == 0, "没有占位符时不该谎报替换了 1 处"


def test_release_refuses_holes_before_token():
    """带占位符的说明必须在**拿 token 之前**被拒：脚本一旦打印过 token 长度就说明已经取过凭据。"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# 标题\n\n压缩包 SHA256：`@SHA@`\n")
        notes = Path(f.name)
    try:
        r = _run([sys.executable, str(ROOT / "scripts" / "release.py"),
                  "--tag", "v9.9.9", "--notes", str(notes),
                  "--zip", str(Path(tempfile.gettempdir()) / "不存在的.zip")], timeout=120)
        out = r.stdout + r.stderr
        assert r.returncode != 0, "占位符没填完竟然放行"
        assert "占位符" in out, out[-400:]
        assert "token len=" not in out, "先拿了 token 才发现占位符——顺序反了"
    finally:
        notes.unlink(missing_ok=True)


# ---- 3: digest ----
def test_digest_prefix_and_case():
    assert tbbuild.digest_matches("sha256:ABCD1234", "abcd1234")
    assert tbbuild.digest_matches("abcd1234", "abcd1234")
    assert not tbbuild.digest_matches("", "abcd1234"), "GitHub 还没算出摘要时不能算通过"
    assert not tbbuild.digest_matches("sha256:deadbeef", "abcd1234")
    assert not tbbuild.digest_matches("sha256:", "abcd1234")


# ---- 4: zip 计数口径 ----
def test_zip_file_names_skips_dirs():
    d = Path(tempfile.mkdtemp())
    a, b = d / "with_dirs.zip", d / "files_only.zip"
    payload = b"TB"
    with zipfile.ZipFile(a, "w") as z:
        z.writestr("TrafficBoard/", "")
        z.writestr("TrafficBoard/_internal/", "")
        z.writestr("TrafficBoard/a.exe", payload)
        z.writestr("TrafficBoard/b.dll", payload)
    with zipfile.ZipFile(b, "w") as z:
        z.write(a, "TrafficBoard/a.exe")
        z.write(a, "TrafficBoard/b.dll")
    assert tbbuild.zip_file_names(a) == ["TrafficBoard/a.exe", "TrafficBoard/b.dll"]
    assert len(tbbuild.zip_file_names(a)) == len(tbbuild.zip_file_names(b)), \
        "同样内容因是否写目录条目而数出不同个数——这就是 v1.6.4 那个假警报"


# ---- 5: 产物版本身份 ----
def test_exe_version_reads_from_pyz():
    exe = ROOT / "out" / tbbuild.repo_version() / "TrafficBoard" / "TrafficBoard.exe"
    if not exe.is_file():
        print("  SKIP test_exe_version_reads_from_pyz（本机没有该产物，构建后才有）")
        return
    got = tbbuild.exe_version(exe)
    assert got == tbbuild.repo_version(), f"exe 里是 {got}，仓库里是 {tbbuild.repo_version()}"


def test_exe_version_fails_loudly_on_junk():
    d = Path(tempfile.mkdtemp())
    junk = d / "not_an_exe.exe"
    junk.write_bytes(b"MZ" + b"\x00" * 4096)
    try:
        got = tbbuild.exe_version(junk)
    except Exception as e:
        assert isinstance(e, SystemExit) or type(e).__name__.endswith("Error"), type(e)
        return
    raise AssertionError(f"对一个假 exe 竟然读出了版本号 {got!r}")



# ---- 6: 本机入口 ----
def test_link_target_matches_form():
    ok = r"C:\x\traffic-board\out\1.6.5\TrafficBoard\TrafficBoard.exe"
    assert tbbuild.link_target_matches(ok, "1.6.5")
    assert not tbbuild.link_target_matches(ok, "1.6.4"), "旧版本目录不该被判成当前版本"
    assert not tbbuild.link_target_matches(r"C:\x\out\1.6.5\TrafficBoard.exe", "1.6.5")
    assert not tbbuild.link_target_matches("", "1.6.5")
    assert tbbuild.link_target_matches(ok.upper().replace("C:", "c:"), "1.6.5"), "大小写要容忍"


def test_ps_utf8_roundtrip():
    """PowerShell 与 Python 之间必须能原样传中文（桌面路径就是中文的）。"""
    got = tbbuild.ps("Write-Output '流量看板 → 1.6.5'", {})
    assert got == "流量看板 → 1.6.5", repr(got)


def test_desktop_link_points_at_current_version():
    """真机核对：桌面上那个 .lnk 必须指向当前版本的产物（忘了它就会静默落后，v1.6.4 已经发生过）。"""
    links = tbbuild.desktop_links()
    if not links:
        print("  SKIP test_desktop_link_points_at_current_version（这台机器桌面没有 TrafficBoard 快捷方式）")
        return
    ver = tbbuild.repo_version()
    for lnk in links:
        target = tbbuild.read_link(lnk)
        assert tbbuild.link_target_matches(target, ver), (
            f"{lnk.name} 指向 {target}，而当前版本是 {ver}——"
            "跑 python scripts/package.py --link-fix 修正")


# ---- 7: 附件可见性 ----
def test_asset_fragment_url_and_listing():
    u = tbbuild.asset_fragment_url("o/r", "v1.6.5")
    assert u == "https://github.com/o/r/releases/expanded_assets/v1.6.5"
    frag = '<a href="/o/r/releases/download/v1.6.5/TrafficBoard-v1.6.5-win64.zip">x</a> 55.8 MB'
    assert tbbuild.asset_listed(frag, "TrafficBoard-v1.6.5-win64.zip")
    assert not tbbuild.asset_listed(frag, "TrafficBoard-v1.6.4-win64.zip")
    assert not tbbuild.asset_listed("", "TrafficBoard-v1.6.5-win64.zip")


def test_release_notes_default_resolves_to_archive():
    """--notes 不再必填：默认就是 docs/releases/<版本>.md，缺了要报出那个路径（而不是 argparse 的必填错）。"""
    ver = tbbuild.repo_version()
    assert tbbuild.resolve_notes(f"v{ver}", None) == tbbuild.notes_path(f"v{ver}")
    assert tbbuild.resolve_notes(f"v{ver}", Path("tmp/x.md")) == Path("tmp/x.md")
    assert tbbuild.notes_path(f"v{ver}").is_file() \
        and tbbuild.find_holes(tbbuild.notes_path(f"v{ver}").read_text(encoding="utf-8")) == [], \
        "归档正文里不该留着没填的占位符"
    r = _run([sys.executable, str(ROOT / "scripts" / "release.py"), "--tag", "v9.9.9"],
             timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode != 0, "9.9.9 根本没有这份发布说明，不该往下走"
    assert "arguments are required" not in out, f"--notes 还是必填：{out[-300:]}"
    assert "docs" in out and "9.9.9.md" in out, f"报错里要看得见默认路径：{out[-300:]}"



def test_package_check_reports_the_invariants():
    """package.py --check 要把四条不变量都打出来；当前版本已打包时它必须报「就绪」（rc=0）。"""
    r = _run([sys.executable, str(ROOT / "scripts" / "package.py"), "--check"], timeout=240)
    out = r.stdout + r.stderr
    for key in ("仓库版本", "发布说明", "exe 内 VERSION", "桌面入口"):
        assert key in out, f"报告里少了「{key}」这条：{out[-400:]}"
    built = (ROOT / "out" / tbbuild.repo_version() / "TrafficBoard" / "TrafficBoard.exe").is_file()
    if not built:
        print("  SKIP rc 判定（这一版还没打包）")
        return
    assert r.returncode == 0, f"产物都在，说明本机这一版没收口：{out[-600:]}"
    assert "就绪" in out


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("  PASS", fn.__name__)
    print(f"adv_release_tools 全部通过（{len(fns)} 例）")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _qt_harness import hard_exit
    hard_exit(main())

# -*- coding: utf-8 -*-
"""发布链里那些「判断」——集中一处，好让 tests/adv_release_tools.py 能真的钉住它们。

为什么单独一个模块：v1.6.4 那次发布把「桌面快捷方式指向 out/<版本号>」这条写在 README 里的
不变量整个忘了，静默落后两个版本；README 的提醒已经被证明挡不住漏。凡是我们愿意反复
校验的判据就必须写成函数、有测试，而不是躺在某次手敲命令的记忆里。

这里只有纯逻辑和单点副作用（读 .lnk、从 exe 里读版本号），网络那半边在 scripts/release.py。
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG_RE = re.compile(r"v\d+\.\d+\.\d+")
HOLE_RE = re.compile(r"@[A-Z]+@")
VER_CONST_RE = re.compile(r"\d+\.\d+\.\d+")


def check_tag(tag: str) -> str | None:
    """合法返回 None，否则返回一句人话的错误。"""
    if not TAG_RE.fullmatch(tag or ""):
        return f"--tag 要形如 v1.6.5，收到 {tag!r}"
    return None


def repo_version() -> str:
    """src/core/version.py 里的 VERSION——全仓库版本号的唯一来源。"""
    text = (ROOT / "src" / "core" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("src/core/version.py 里找不到 VERSION = \"x.y.z\"")
    return m.group(1)


def notes_path(tag: str) -> Path:
    """发布说明的家：docs/releases/v1.6.5.md（v1.6.5 起进版本库，之前只躺在 gitignore 的 tmp/）。

    文件名带那个 v，跟 tag 一致——去掉 v 会跟 tag 差一个字符，两处口径迟早对不上。
    """
    return ROOT / "docs" / "releases" / f"{tag}.md"



def resolve_notes(tag: str, notes: Path | None) -> Path:
    """没给 --notes 就用归档里那份——少一个手敲参数，也逼着发布说明必须进版本库。"""
    return Path(notes) if notes else notes_path(tag)



def find_holes(body: str) -> list[str]:
    return HOLE_RE.findall(body or "")


def stamp_sha(body: str, sha: str) -> tuple[str, int]:
    """把 @SHA@ 换成真实摘要；返回新文本与替换次数。"""
    return body.replace("@SHA@", sha), body.count("@SHA@")


def digest_matches(digest: str, sha: str) -> bool:
    """GitHub 的 asset digest 带 'sha256:' 前缀，直接 == 本地 hex 永远为假（v1.6.3 就打印过假阴性）。"""
    if not digest:
        return False
    return digest.split(":", 1)[-1].lower() == (sha or "").lower()


def zip_file_names(zip_path: Path) -> list[str]:
    """只数文件条目。v1.6.4 那份 zip「295 个条目」里 29 个是目录条目，被当时记成多收了依赖。"""
    with zipfile.ZipFile(zip_path) as z:
        return [n for n in z.namelist() if not n.endswith("/")]


def zip_has_exe(zip_path: Path) -> bool:
    """发布 zip 的顶层必须是 TrafficBoard/TrafficBoard.exe——别把空壳或错目录发出去。"""
    return "TrafficBoard/TrafficBoard.exe" in zip_file_names(zip_path)


def exe_version(exe: Path) -> str:
    """从打包产物里把 core.version 读出来。

    二进制 grep 量不到：PYZ 是 zlib 压缩的，盘面上搜 '1.6.5' 命中 0 次，
    所以「exe 里到底是哪个版本」只有走 PyInstaller 的 archive reader 才算证据。
    """
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    car = CArchiveReader(str(exe))
    pyz_name = next((n for n in car.toc if "PYZ" in n), None)
    if pyz_name is None:
        raise SystemExit(f"{exe.name} 里没有 PYZ 归档，读不到 core.version")
    with tempfile.TemporaryDirectory(prefix="tb_pyz_") as td:
        p = Path(td) / "app.pyz"
        p.write_bytes(car.extract(pyz_name))
        code = ZlibArchiveReader(str(p)).extract("core.version")
    consts = [c for c in getattr(code, "co_consts", ()) if isinstance(c, str)]
    for c in consts:
        if VER_CONST_RE.fullmatch(c):
            return c
    raise SystemExit(f"exe 里 core.version 没有版本号形状的常量，只读到 {consts}")


def asset_fragment_url(repo: str, tag: str) -> str:
    """Releases 页的附件列表是前端懒加载的：这段 HTML 才是用户真正看见的那张附件表。"""
    return f"https://github.com/{repo}/releases/expanded_assets/{tag}"


def asset_listed(fragment_html: str, zip_name: str) -> bool:
    return bool(zip_name) and zip_name in (fragment_html or "")


def link_target_matches(target: str, version: str) -> bool:
    """桌面入口的不变量：必须指向 out/<version>/TrafficBoard/TrafficBoard.exe。"""
    if not target:
        return False
    tail = f"out/{version}/TrafficBoard/TrafficBoard.exe"
    norm = target.replace("\\", "/").lower()
    return norm.endswith(tail.lower())


def ps(script: str, env_extra: dict[str, str], check: bool = True) -> str:
    """PowerShell 与 Python 之间显式走 UTF-8。

    默认按控制台代码页解码：中文桌面路径在 GBK/UTF-8 混用时会让 decode 线程崩掉，
    stdout 变成 None，报错落在 `.strip()` 上而不是真正的原因。两侧都写死编码。
    """
    import os
    env = dict(os.environ, **env_extra)
    prelude = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    p = subprocess.run(["powershell", "-NoProfile", "-Command", prelude + script],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip()[:200]
        raise SystemExit(f"powershell 失败（rc={p.returncode}）：{detail}")
    return (p.stdout or "").strip()


def desktop_links() -> list[Path]:
    """桌面上所有指向 TrafficBoard.exe 的 .lnk（正常就一个「流量看板.lnk」）。"""
    script = (
        "$s=New-Object -ComObject WScript.Shell;"
        "Get-ChildItem ([Environment]::GetFolderPath('Desktop')) -Filter *.lnk | ForEach-Object {"
        "$t=$s.CreateShortcut($_.FullName).TargetPath;"
        "if($t -like '*TrafficBoard.exe'){Write-Output $_.FullName}}"
    )
    out = ps(script, {})
    return [Path(l) for l in out.splitlines() if l.strip()]


def read_link(lnk: Path) -> str:
    # 路径走环境变量传进去：命令行里拼中文路径既难读又是注入面。
    script = "$s=New-Object -ComObject WScript.Shell;" \
             "$s.CreateShortcut($env:TB_LNK).TargetPath | Write-Output"
    return ps(script, {"TB_LNK": str(lnk)})


def write_link(lnk: Path, target: str) -> None:
    script = ("$s=New-Object -ComObject WScript.Shell;"
              "$o=$s.CreateShortcut($env:TB_LNK);"
              "$o.TargetPath=$env:TB_TARGET;"
              "$o.WorkingDirectory=Split-Path $env:TB_TARGET;"
              "$o.Save()")
    ps(script, {"TB_LNK": str(lnk), "TB_TARGET": target})

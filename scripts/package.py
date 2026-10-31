# -*- coding: utf-8 -*-
"""一条命令把本机这一版准备好：打包 → 拷 out/<版本> → 打 zip → 填 SHA → 自证版本 → 核对桌面入口。

为什么要有这个脚本：v1.6.4 那次的发布链是四步手敲命令 + 一次「记得要把桌面快捷方式也改掉」。
GitHub 那一侧核对得很足，本机入口却静默停在旧版两个版本，用户双击图标打开的还是老程序。
手敲的步骤会漏，判据只有写进脚本才算存在。

    python scripts/package.py --check      # 只报告现状，不动任何文件
    python scripts/package.py              # 打包并收口本机这一版
    python scripts/package.py --link-fix   # 顺手把桌面 .lnk 指到新版本产物
    python scripts/package.py --publish    # 全部本地检查过了再交给 scripts/release.py 外发

外发那半边（建 Release / 传附件 / digest / 公开页 / 附件列表）在 scripts/release.py。
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tbbuild  # noqa: E402

ROOT = tbbuild.ROOT
SPEC = ROOT / "TrafficBoard.spec"


def _run(cmd: list[str], tail: int = 6) -> int:
    print("+ " + " ".join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], cwd=str(ROOT))
    if p.returncode != 0:
        return p.returncode
    return 0


def running_from(path_prefix: Path) -> list[str]:
    """正在运行的 TrafficBoard.exe 里落在 path_prefix 之下的那些路径。

    只查不改：这个脚本绝不代用户杀进程（那是另一条铁律），撞见占用就停下来让人先关。
    """
    script = ("Get-Process TrafficBoard -ErrorAction SilentlyContinue | "
              "ForEach-Object { try { Write-Output $_.MainModule.FileName } catch {} }")
    out = tbbuild.ps(script, {}, check=False)
    hit = [l.strip() for l in out.splitlines() if l.strip()]
    low = str(path_prefix).replace("\\", "/").lower()
    return [x for x in hit if x.replace("\\", "/").lower().startswith(low)]


def report(ver: str, tag: str, notes: Path, zip_path: Path, exe: Path) -> list[str]:
    """把现状打成一行一行的事实，返回问题列表（空列表 == 本机这一版已经就绪）。"""
    problems: list[str] = []

    def line(k: str, v: str, bad: bool = False) -> None:
        print(f"  {'! ' if bad else '  '}{k}: {v}")
        if bad:
            problems.append(f"{k}: {v}")

    line("仓库版本", ver)
    line("发布说明", f"{notes.relative_to(ROOT).as_posix()} 存在={notes.is_file()}",
         not notes.is_file())
    if notes.is_file():
        holes = tbbuild.find_holes(notes.read_text(encoding="utf-8"))
        line("未填占位符", str(holes or "无"), bool(holes))
    line("exe 产物", f"{exe.relative_to(ROOT).as_posix()} 存在={exe.is_file()}", not exe.is_file())
    if exe.is_file():
        try:
            got = tbbuild.exe_version(exe)
        except SystemExit as e:
            got = f"读不出（{e}）"
        line("exe 内 VERSION", f"{got}（与仓库一致={got == ver}）", got != ver)
    line("发布 zip", f"{zip_path.name} 存在={zip_path.is_file()}", not zip_path.is_file())
    if zip_path.is_file():
        data = zip_path.read_bytes()
        line("zip 大小/sha256", f"{len(data)} 字节 {hashlib.sha256(data).hexdigest()[:16]}…")
        line("zip 文件条目", str(len(tbbuild.zip_file_names(zip_path))))
    links = tbbuild.desktop_links()
    if not links:
        line("桌面入口", "这台机器桌面没有 TrafficBoard 快捷方式（跳过）")
    for lnk in links:
        target = tbbuild.read_link(lnk)
        ok = tbbuild.link_target_matches(target, ver)
        line("桌面入口", f"{lnk.name} → {target} 对齐={ok}", not ok)
    busy = running_from(ROOT / "out")
    if busy:
        line("正在运行的实例", " ".join(busy) + "（重打包前要你自己关掉，脚本不代杀）", True)
    return problems


def build(ver: str, tag: str, notes: Path, zip_path: Path, out_dir: Path,
          link_fix: bool, force: bool) -> int:
    exe = out_dir / "TrafficBoard" / "TrafficBoard.exe"
    busy = running_from(out_dir)
    if busy:
        print(f"！{out_dir.name} 里有实例在跑：{' '.join(busy)}——请先自己退出，脚本不代杀进程")
        return 1
    if out_dir.exists():
        if not force:
            print(f"！{out_dir.relative_to(ROOT)} 已存在——要重打同一版本加 --force（旧目录会被改名保留）")
            return 1
        discard = ROOT / "out" / "_discard" / f"{ver}-{time.strftime('%Y%m%d-%H%M%S')}"
        discard.parent.mkdir(parents=True, exist_ok=True)
        out_dir.rename(discard)
        print(f"旧产物改名保留在 {discard.relative_to(ROOT)}")

    rc = _run([sys.executable, "-m", "PyInstaller", "--noconfirm",
               "--distpath", "dist", "--workpath", "build", SPEC.name])
    if rc:
        return rc

    dist = ROOT / "dist" / "TrafficBoard"
    if not (dist / "TrafficBoard.exe").is_file():
        print("！dist/TrafficBoard/TrafficBoard.exe 不存在，构建没出东西")
        return 1
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist, out_dir / "TrafficBoard")
    print(f"拷到 {out_dir.relative_to(ROOT)}/TrafficBoard")

    got = tbbuild.exe_version(exe)
    if got != ver:
        print(f"！exe 里的 VERSION 是 {got}，仓库里是 {ver}——这一版别发，先查构建用的是哪份源码")
        return 1
    print(f"产物自证：exe 内 core.version = {got}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((out_dir / "TrafficBoard").rglob("*")):
            if p.is_file():
                arc = str(Path("TrafficBoard") / p.relative_to(out_dir / "TrafficBoard"))
                z.write(p, arc.replace("\\", "/"))
    raw = zip_path.read_bytes()
    sha_hex = hashlib.sha256(raw).hexdigest()
    print(f"zip：{zip_path.name} 文件条目={len(tbbuild.zip_file_names(zip_path))} "
          f"字节={len(raw)} sha256={sha_hex}")

    if notes.is_file():
        body = notes.read_text(encoding="utf-8")
        if "@SHA@" in body:
            new, n = tbbuild.stamp_sha(body, sha_hex)
            notes.write_text(new, encoding="utf-8")
            print(f"发布说明里 @SHA@ 已填 {n} 处（这份文件改了要记得提交）")
        else:
            holes = tbbuild.find_holes(body)
            if holes:
                print(f"！发布说明里还有 {holes}，这个脚本只认 @SHA@，其余得你自己写")
                return 1
    else:
        print(f"！缺发布说明 {notes.relative_to(ROOT)}——先写好正文再打包")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告现状，不构建不写文件")
    ap.add_argument("--link-fix", action="store_true", help="把桌面 .lnk 指到本次 out/ 产物")
    ap.add_argument("--publish", action="store_true", help="本地检查全过后接着跑 scripts/release.py")
    ap.add_argument("--force", action="store_true", help="允许重打同一版本（旧目录改名保留）")
    a = ap.parse_args()

    ver = tbbuild.repo_version()
    tag = f"v{ver}"
    notes = tbbuild.notes_path(tag)
    zip_path = ROOT / f"TrafficBoard-{tag}-win64.zip"
    out_dir = ROOT / "out" / ver
    exe = out_dir / "TrafficBoard" / "TrafficBoard.exe"

    if not a.check:
        rc = build(ver, tag, notes, zip_path, out_dir, a.link_fix, a.force)
        if rc:
            print("打包阶段没走完，先解决上面报的问题")
            return rc

    if a.link_fix and exe.is_file():
        for lnk in tbbuild.desktop_links():
            target = tbbuild.read_link(lnk)
            if tbbuild.link_target_matches(target, ver):
                continue
            tbbuild.write_link(lnk, str(exe))
            after = tbbuild.read_link(lnk)
            print(f"桌面入口改指：{lnk.name} {target} → {after}")

    print(f"现状核对 {tag}：")
    problems = report(ver, tag, notes, zip_path, exe)
    if problems:
        print("本机这一版还没就绪：")
        for p in problems:
            print("  -", p)
        return 1
    print("本机这一版就绪。外发：python scripts/release.py --tag " + tag)
    if a.publish:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "release.py"),
                               "--tag", tag], cwd=str(ROOT)).returncode
    return 0


if __name__ == "__main__":
    rc = main()
    sys.exit(rc if isinstance(rc, int) else (print(rc) or 1))

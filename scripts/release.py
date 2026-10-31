# -*- coding: utf-8 -*-
"""发一个版本：建 Release → 上传 zip → digest 核对 → 公开 Releases 页 + 附件列表都看得见。

以前每轮都 sed 复制一份 publishXXX.py（build/ 下躺着 8 份），复制的代价不是脏文件，
是下一次发布会顺手漏掉某一步验证。这里把六步钉成一个脚本：

    python scripts/release.py --tag v1.6.5          # 发布说明默认取 docs/releases/v1.6.5.md

zip 默认取仓库根目录下 TrafficBoard-<tag>-win64.zip。token 只从 git credential helper 拿，
全程只打印长度和后 4 位，绝不落盘、绝不回显。
第 5 步是"发布了"的定义本身：API 里有 release、tag 推上去、日志说了都不算，
公开页面 https://github.com/<repo>/releases 的 HTML 里能看见这个版本号才算。
第 6 步是这轮补上的：附件列表在页面上是前端懒加载的，tag 页 HTML 里搜不到 zip 名，
所以「版本可见」不等于「用户点得到那个包」——要另外抓 expanded_assets 片段确认列出了 zip。
"""
import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tbbuild  # noqa: E402

ROOT = tbbuild.ROOT
REPO = "ygy2200/traffic-board"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def token() -> str:
    p = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, check=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise SystemExit("credential helper 没给出 token")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="形如 v1.6.5")
    ap.add_argument("--notes", type=Path, default=None,
                    help="Release 正文（markdown），默认 docs/releases/<版本>.md")
    ap.add_argument("--zip", type=Path, default=None)
    ap.add_argument("--title", default=None, help="Release 标题，默认 Traffic Board <tag>")
    ap.add_argument("--dry-run", action="store_true",
                    help="只做本地预检并打印发布计划：不取 token、不联网、不建 Release")
    a = ap.parse_args()

    err = tbbuild.check_tag(a.tag)
    if err:
        return err
    notes = tbbuild.resolve_notes(a.tag, a.notes)
    if not notes.is_file():
        return f"发布说明不存在：{notes}（新版本要先在 docs/releases/ 写好正文）"
    body = notes.read_text(encoding="utf-8")
    holes = tbbuild.find_holes(body)
    if holes:
        return f"发布说明里还有没填的占位符 {holes}——先补完再发"
    zip_path = a.zip or ROOT / f"TrafficBoard-{a.tag}-win64.zip"
    if not zip_path.is_file():
        return f"zip 不存在：{zip_path}（先跑 python scripts/package.py 打包）"
    ver = a.tag.lstrip("v")
    if tbbuild.repo_version() != ver:
        return (f"要发的是 {ver}，但 src/core/version.py 写的是 {tbbuild.repo_version()}"
                "——版本号和发布对象对不上")
    if not tbbuild.zip_has_exe(zip_path):
        return f"zip 里没有 TrafficBoard/TrafficBoard.exe：{zip_path.name} 不是可用的发布包"
    exe = ROOT / "out" / ver / "TrafficBoard" / "TrafficBoard.exe"
    if exe.is_file():
        got = tbbuild.exe_version(exe)
        if got != ver:
            return f"out/{ver} 里的 exe 自证版本是 {got}，与 tag {a.tag} 对不上"
        exe_msg = f"out/{ver} 的 exe 自证 {got}"
    else:
        exe_msg = f"没有 out/{ver} 的 exe 可比对（只校验 zip 成员）"

    data = zip_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    print(f"0) 本地包 {zip_path.name} size={len(data)} sha256={sha} 文件条目={len(tbbuild.zip_file_names(zip_path))}")
    print(f"   预检：{exe_msg}；zip 成员含 TrafficBoard/TrafficBoard.exe")
    if a.dry_run:
        print("DRY-RUN：本地预检全过，接下来会做但不执行：")
        print(f"   1) 取 git credential（只打印长度/后4位）  2) POST /releases tag={a.tag}")
        print(f"   3) 上传 {zip_path.name}  4) digest 与本地 sha 核对")
        print(f"   5) 公开 /releases 页可见 {a.tag}  6) expanded_assets 列出 zip")
        return 0

    tok = token()
    print(f"token len={len(tok)} tail={tok[-4:]}")        # 只报长度和后 4 位
    s = requests.Session()
    s.headers["Authorization"] = "Bearer " + tok
    s.proxies = PROXY

    r = s.post(f"https://api.github.com/repos/{REPO}/releases",
               json={"tag_name": a.tag, "name": a.title or f"Traffic Board {a.tag}",
                     "body": body, "draft": False, "prerelease": False}, timeout=60)
    if r.status_code in (200, 201):
        rel = r.json()
        print(f"2) 建 Release：HTTP {r.status_code} id={rel['id']} url={rel['html_url']}")
    elif r.status_code == 422 and "already exists" in r.text:
        rel = s.get(f"https://api.github.com/repos/{REPO}/releases/tags/{a.tag}",
                    timeout=60).json()
        print(f"2) Release 已存在，沿用 id={rel['id']} url={rel['html_url']}")
    else:
        print(r.text[:400])
        return "2) 建 Release 失败"

    up = s.post(f"https://uploads.github.com/repos/{REPO}/releases/{rel['id']}/assets",
                params={"name": zip_path.name},
                headers={"Content-Type": "application/zip"}, data=data, timeout=900)
    if up.status_code not in (200, 201):
        print(up.text[:400])
        return "3) 上传附件失败"
    asset = up.json()
    print(f"3) 上传附件：HTTP {up.status_code} {asset['name']} size={asset['size']} "
          f"-> {asset['browser_download_url']}")

    ok = False
    for attempt in range(6):
        j = s.get(f"https://api.github.com/repos/{REPO}/releases/tags/{a.tag}",
                  timeout=60).json()
        hit = next((x for x in j.get("assets", []) if x["name"] == zip_path.name), None)
        dg = (hit or {}).get("digest") or ""
        ok = tbbuild.digest_matches(dg, sha)
        print(f"4) digest 核对（第 {attempt + 1} 次）：{dg[:20] or 'GitHub 还没算好'} "
              f"{'MATCH' if ok else '!=' if dg else '…'}")
        if ok:
            break
        time.sleep(5)
    if not ok:
        return "4) digest 对不上，别把这版当已发布"

    html = s.get(f"https://github.com/{REPO}/releases", timeout=60,
                 headers={"User-Agent": "tb-publish"}).text
    seen = a.tag in html
    print(f"5) 公开页面 github.com/{REPO}/releases 里能否看见 {a.tag}：{seen}"
          f"（HTML {len(html)} 字节）")
    if not seen:
        return "5) API 说发好了但公开页面还看不见——先别报「已发布」"

    frag = s.get(tbbuild.asset_fragment_url(REPO, a.tag), timeout=60,
                 headers={"User-Agent": "tb-publish"})
    listed = tbbuild.asset_listed(frag.text, zip_path.name)
    print(f"6) 附件列表（页面上懒加载的那段 HTML，{frag.status_code}、{len(frag.text)} 字节）"
          f"里能否看见 {zip_path.name}：{listed}")
    if not listed:
        return "6) 版本看得见但附件列表里没有这个包——用户点不到，别报「已发布」"
    print(f"发布收口：{rel['html_url']}")
    return 0



if __name__ == "__main__":
    rc = main()
    sys.exit(rc if isinstance(rc, int) else (print(rc) or 1))

# -*- coding: utf-8 -*-
"""发一个版本：建 Release → 上传 zip → digest 核对 → 最后按**公开 Releases 页面**收口。

以前每轮都 sed 复制一份 publishXXX.py（build/ 下躺着 8 份），复制的代价不是脏文件，
是下一次发布会顺手漏掉某一步验证。这里把四步钉成一个脚本：

    python scripts/release.py --tag v1.6.5 --notes tmp/release-notes-v1.6.5.md

zip 默认取仓库根目录下 TrafficBoard-<tag>-win64.zip。token 只从 git credential helper 拿，
全程只打印长度和后 4 位，绝不落盘、绝不回显。
第 4 步是"发布了"的定义本身：API 里有 release、tag 推上去、日志说了都不算，
公开页面 https://github.com/<repo>/releases 的 HTML 里能看见这个版本号才算。
"""
import argparse
import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
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
    ap.add_argument("--notes", required=True, type=Path, help="Release 正文（markdown）")
    ap.add_argument("--zip", type=Path, default=None)
    ap.add_argument("--title", default=None, help="Release 标题，默认 Traffic Board <tag>")
    a = ap.parse_args()

    if not re.fullmatch(r"v\d+\.\d+\.\d+", a.tag):
        return f"--tag 要形如 v1.6.5，收到 {a.tag!r}"
    zip_path = a.zip or ROOT / f"TrafficBoard-{a.tag}-win64.zip"
    if not zip_path.is_file():
        return f"zip 不存在：{zip_path}（先打包再发布）"
    if not a.notes.is_file():
        return f"发布说明不存在：{a.notes}"
    body = a.notes.read_text(encoding="utf-8")
    holes = re.findall(r"@[A-Z]+@", body)
    if holes:
        return f"发布说明里还有没填的占位符 {holes}——先补完再发"

    tok = token()
    print(f"token len={len(tok)} tail={tok[-4:]}")        # 只报长度和后 4 位
    s = requests.Session()
    s.headers["Authorization"] = "Bearer " + tok
    s.proxies = PROXY

    data = zip_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    print(f"1) 本地包 {zip_path.name} size={len(data)} sha256={sha}")

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
        ok = dg.split(":", 1)[-1] == sha
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
    print(f"发布收口：{rel['html_url']}")
    return 0


if __name__ == "__main__":
    rc = main()
    sys.exit(rc if isinstance(rc, int) else (print(rc) or 1))

# -*- coding: utf-8 -*-
"""把一轮迭代写进 docs/iteration-ledger.jsonl——轮次编号只由台账决定。

为什么要有它：100 轮长跑里最容易坏的不是代码，是「上一轮到底做到哪」。轮次靠记忆编号，
迟早重复或跳号；手写 JSON 又容易漏字段。这里把字段校验、连续编号、单行 JSON 钉在一处。

    python scripts/log_round.py --json tmp/r23.json
    cat tmp/r23.json | python scripts/log_round.py
    python scripts/log_round.py --check        # 只校验现有台账

条目字段：theme / review / direction / action / evidence / verdict / leftover / next。
action/leftover/next 可以是字符串或字符串数组。
"""
import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "iteration-ledger.jsonl"
REQUIRED = ("theme", "review", "direction", "action", "evidence", "verdict",
            "leftover", "next")


def read_rounds(path: Path = LEDGER) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"台账第 {i} 行不是合法 JSON：{e}")
    return out


def validate(entry: dict, rounds: list[dict], force: bool = False) -> dict:
    for k in REQUIRED:
        v = entry.get(k)
        if v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v):
            raise SystemExit(f"台账字段「{k}」缺失或为空")
    for k in ("action", "leftover", "next"):
        v = entry[k]
        entry[k] = [v] if isinstance(v, str) else list(v)
    entry.setdefault("date", _dt.date.today().isoformat())
    entry.setdefault("agent", "qoder")
    want = (rounds[-1]["round"] + 1) if rounds else 1
    got = entry.get("round", want)
    if got != want and not force:
        raise SystemExit(f"轮次不连续：台账最后是 R{rounds[-1]['round'] if rounds else 0}，"
                         f"这条写的是 R{got}（要改旧轮次加 --force）")
    entry["round"] = got
    return entry


def append_round(entry: dict, path: Path = LEDGER, force: bool = False) -> int:
    rounds = read_rounds(path)
    entry = validate(dict(entry), rounds, force)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry["round"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, help="条目 JSON 文件；省略则从 stdin 读")
    ap.add_argument("--check", action="store_true", help="只校验现有台账")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    rounds = read_rounds()
    if a.check:
        for i, e in enumerate(rounds, 1):
            if e.get("round") != i:
                print(f"第 {i} 条写的是 R{e.get('round')}")
                return 1
        print(f"台账 {len(rounds)} 条，R1..R{rounds[-1]['round'] if rounds else 0} 连续")
        return 0
    raw = a.json.read_text(encoding="utf-8") if a.json else sys.stdin.read()
    entry = json.loads(raw)
    n = append_round(entry, force=a.force)
    print(f"R{n} 已写入（台账 {len(rounds) + 1} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

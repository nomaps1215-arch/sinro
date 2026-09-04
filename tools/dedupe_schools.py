#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""同じ学校が2件入っているのを1件にまとめる。

    python tools/dedupe_schools.py            # 重複を表示するだけ
    python tools/dedupe_schools.py --apply    # 1件にまとめる

私学連合会の一覧は、賢明学院を全日制と通信制課程で2行に分けて載せている。
学校としては1つなので、URL のパスが浅いほう（本体のページ）を残す。

取り込みツール側でも同じ処理をしているが、データが古い状態に巻き戻ると
重複が復活する。qa_check が「school id が重複しています」と言ったらこれを流す。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"


def depth(s: dict) -> int:
    path = urllib.parse.urlsplit(s.get("website") or "").path
    return len([p for p in path.split("/") if p])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    groups = defaultdict(list)
    for s in doc["schools"]:
        groups[s["name"]].append(s)

    drop = set()
    for name, items in groups.items():
        if len(items) < 2:
            continue
        keep = min(items, key=depth)
        print(f"{name}")
        print(f"   残す  {keep.get('website')}")
        for s in items:
            if s is not keep:
                drop.add(id(s))
                print(f"   削除  {s.get('website')}")

    if not drop:
        print("重複はありません。")
        return 0

    before = len(doc["schools"])
    if args.apply:
        doc["schools"] = [s for s in doc["schools"] if id(s) not in drop]
        write_json(SCHOOLS, doc)
        print(f"\n{before} 校 -> {len(doc['schools'])} 校")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print(f"\n{len(drop)} 件が重複しています。--apply で 1件にまとめます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

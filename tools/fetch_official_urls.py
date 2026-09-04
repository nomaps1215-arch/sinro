#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大阪府の「公立高校ホームページ一覧」から、府立高校の公式URLを正確に取得する。

府立高校のURLは www.osaka-c.ed.jp/<slug>/ に統一されておらず、
www2/www3 配下だったり独自ドメイン（tennoji-hs.jp など）だったりする。
推測では当たらないので、大阪府が公開している一覧ページを一次ソースにする。

    python tools/fetch_official_urls.py           # 取得して差分を表示するだけ
    python tools/fetch_official_urls.py --apply   # schools.json の website を更新

私立高校はこの一覧に載らないので、tools/find_websites.py で探す。

一次ソース: https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/index.html
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
INDEX = "https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/index.html"
UA = "koukou-search/1.0 (personal study tool)"
SLEEP_SEC = 1.5

RE_AREA = re.compile(r'href="([^"]*(?:_area|tei)\.html)"')
RE_ANCHOR = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]{2,40}?)</a>')
RE_FOUNDER = re.compile(r"^(大阪府立|大阪市立|府立|私立|市立)")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def normalize(s: str) -> str:
    s = s.strip().replace(" ", "").replace("　", "")
    s = RE_FOUNDER.sub("", s)
    return s.replace("ヶ", "ケ").replace("が", "ケ")


def collect() -> dict[str, str]:
    print(f"一覧ページを取得: {INDEX}")
    idx = get(INDEX)
    areas = sorted(set(RE_AREA.findall(idx)))
    print(f"  地区別ページ {len(areas)} 件")
    time.sleep(SLEEP_SEC)

    table: dict[str, str] = {}
    for a in areas:
        url = urllib.parse.urljoin(INDEX, a)
        try:
            page = get(url)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {a} の取得に失敗: {e}")
            time.sleep(SLEEP_SEC)
            continue
        n = 0
        for href, name in RE_ANCHOR.findall(page):
            if not name.endswith(("高等学校", "中学校")):
                continue
            table.setdefault(normalize(name), href.replace("&amp;", "&"))
            n += 1
        print(f"  {a.rsplit('/', 1)[-1]}: {n} 校")
        time.sleep(SLEEP_SEC)
    return table


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    table = collect()
    print(f"\n一覧から {len(table)} 校のURLを取得\n")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    changed = same = missing = 0
    for s in doc["schools"]:
        if s["type"] != "public":
            continue
        url = table.get(normalize(s["name"]))
        if not url:
            # 現役の府立高校はすべてこの一覧に載る。載っていない＝廃校・統合の可能性が高い。
            print(f"   × {s['name']}: 一覧に見つからず（廃校・統合の可能性。"
                  f"メモリアルページ https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/memo.html で確認すること）")
            missing += 1
            continue
        if url == s.get("website"):
            same += 1
            continue
        print(f"   → {s['shortName']}: {s.get('website')}\n        {url}")
        if args.apply:
            s["website"] = url
            s["websiteSource"] = "大阪府 公立高校ホームページ一覧"
            s["linkOk"] = True
        changed += 1

    print(f"\n変更 {changed} / 変更なし {same} / 一覧になし {missing}")
    if args.apply:
        SCHOOLS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("schools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

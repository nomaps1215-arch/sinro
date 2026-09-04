#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""高校の偏差値の目安を、公開されている一覧ページから取り込む。

    python tools/fetch_deviation.py            # 取得して差分を表示するだけ
    python tools/fetch_deviation.py --apply    # data/schools.json に反映

■ このデータの性質（重要）
高校の偏差値に公的なデータは存在しない。ここで取り込むのは民間サイトが
模試結果から独自に算出した推定値で、公式発表ではないし、模試会社が違えば
3〜5は平気でずれる。だから取り込んだ値には estimated: true を付け、
画面には「（想定）」と添えて出す。出願の判断には使えない。

■ 取り扱い
このアプリは個人利用のみという前提で取り込んでいる。
取得したデータを再配布したり、商用に使ったりしないこと。
GitHub Pages などで公開する場合は、この値を外すか各自で確認し直すこと。

■ 相手のサーバーへの配慮
robots.txt を確認したうえで取得する。一覧は1ページで完結するので
リクエストは1回だけ。むやみに繰り返し叩かないこと。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
import sys
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
CACHE = ROOT / "tools" / ".cache"

SOURCE_NAME = "みんなの高校情報"
SOURCE_URL = "https://www.minkou.jp/hischool/exam/osaka/deviation/"
UA = "koukou-search/1.0 (personal study tool; respects robots.txt)"

# <td class="tx-ac tx-wb">75</td> ... <li><a>北野高等学校</a><span>（文理学科/公立）</span></li>
RE_BLOCK = re.compile(
    r'<td[^>]*class="tx-ac tx-wb"[^>]*>\s*(\d{2})\s*</td>(.*?)</tr>', re.S)
RE_ITEM = re.compile(
    r'<a[^>]*href="/hischool/school/[^"]*"[^>]*>([^<]+)</a>\s*<span>\s*[（(]([^）)]*)[）)]', re.S)

RE_FOUNDER = re.compile(r"^(大阪府立|大阪市立|府立|私立|市立|町立|村立|組合立)")
RE_SUFFIX = re.compile(r"(高等学校|高校|中学校|中等教育学校)")


def key(name: str) -> str:
    s = name.strip().replace(" ", "").replace("　", "")
    s = RE_FOUNDER.sub("", s)
    s = RE_SUFFIX.sub("", s)
    return s.replace("ヶ", "ケ").replace("が", "ケ").replace("國", "国").replace("學", "学")


def robots_allows(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:  # noqa: BLE001 — robots.txt が無いサイトは許可扱い
        return True
    return rp.can_fetch(UA, url)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def parse(html: str) -> dict[str, list[dict]]:
    """校名キー → [{course, deviation, founder}] を作る。"""
    out: dict[str, list[dict]] = {}
    for dev, body in RE_BLOCK.findall(html):
        for name, meta in RE_ITEM.findall(body):
            parts = [p.strip() for p in meta.split("/")]
            course = parts[0] if parts else "普通科"
            founder = parts[-1] if len(parts) > 1 else ""
            out.setdefault(key(name), []).append({
                "name": name.strip(),
                "course": course,
                "deviation": int(dev),
                "founder": founder,
            })
    return out


def pick_for_course(our_course: str, entries: list[dict]):
    """学科名がいちばん近い候補を選ぶ。合うものが無ければ最も高い値を返す。"""
    ours = re.sub(r"[（(].*", "", our_course).strip()
    for e in entries:                                   # 完全一致
        if e["course"] == our_course or e["course"] == ours:
            return e
    for e in entries:                                   # 含む・含まれる
        if ours and (ours in e["course"] or e["course"] in ours):
            return e
    return max(entries, key=lambda e: e["deviation"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cache", action="store_true", help="前回取得したHTMLを使う")
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / "deviation.html"

    if args.cache and cached.exists():
        print(f"キャッシュを使用: {cached.name}")
        html = cached.read_text(encoding="utf-8")
    else:
        if not robots_allows(SOURCE_URL):
            print(f"robots.txt で取得が許可されていません: {SOURCE_URL}")
            return 1
        print(f"robots.txt を確認しました。取得します: {SOURCE_URL}")
        html = fetch(SOURCE_URL)
        cached.write_text(html, encoding="utf-8")

    table = parse(html)
    total = sum(len(v) for v in table.values())
    print(f"{len(table)} 校 / のべ {total} 学科の偏差値を読み取りました\n")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    today = dt.date.today().isoformat()
    filled = replaced = nohit = 0
    nohit_names = []

    for s in doc["schools"]:
        entries = table.get(key(s["name"]))
        if not entries:
            nohit += 1
            nohit_names.append(s["name"])
            continue
        had = any(c.get("deviation") is not None for c in s["courses"])

        if s["type"] == "private" and len(s["courses"]) == 1 and s["courses"][0]["name"] == "普通科":
            # 私立の学科は一覧に載っていないので「普通科」で埋めてある。
            # 取得元はコース別に持っているので、そちらに置き換えるほうが情報量が多い。
            courses = [{"name": e["course"], "deviation": e["deviation"], "estimated": True}
                       for e in sorted(entries, key=lambda e: -e["deviation"])]
        else:
            courses = []
            for c in s["courses"]:
                e = pick_for_course(c["name"], entries)
                courses.append({"name": c["name"], "deviation": e["deviation"], "estimated": True})

        if args.apply:
            s["courses"] = courses
            s["deviationSource"] = SOURCE_NAME
            s["deviationSourceUrl"] = SOURCE_URL
            s["deviationFetchedAt"] = today
        if had:
            replaced += 1
        else:
            filled += 1

    print(f"新たに埋まった {filled} 校 / 既存の値を置き換えた {replaced} 校 / 一覧に無い {nohit} 校")
    if nohit_names:
        print("\n一覧に無く、偏差値が付かない学校:")
        for n in nohit_names[:40]:
            print("   - " + n)
        if len(nohit_names) > 40:
            print(f"   ... ほか {len(nohit_names) - 40} 校")

    if args.apply:
        doc["meta"].setdefault("dataPolicy", []).append(
            f"偏差値は{SOURCE_NAME}（{SOURCE_URL}）の掲載値。模試結果からの推定であり公式発表ではない。"
            "個人利用の範囲で参照しており、再配布・商用利用はしない。"
        )
        write_json(SCHOOLS, doc)
        print("\nschools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("\n--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

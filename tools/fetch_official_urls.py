#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公式の学校一覧から、各高校の公式サイトURLを正確に取得する。

URLを校名のローマ字から推測しても当たらない。府立高校は www.osaka-c.ed.jp/<slug>/ に
統一されておらず www2/www3 配下や独自ドメイン（天王寺高校は tennoji-hs.jp）が混在し、
私立高校に至っては規則性がない。だから一覧ページを一次ソースにする。

    python tools/fetch_official_urls.py           # 取得して差分を表示するだけ
    python tools/fetch_official_urls.py --apply   # schools.json の website を更新

一次ソース:
  公立 https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/index.html （大阪府）
  私立 https://www.osaka-shigaku.gr.jp/school/index.html （大阪私立中学校高等学校連合会）

府立高校がこの一覧に無い場合は廃校の可能性が高い。必ずメモリアルページで確認すること。
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json, write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
INDEX = "https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/index.html"
PRIVATE_INDEX = "https://www.osaka-shigaku.gr.jp/school/index.html"
MEMORIAL = "https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/memo.html"
UA = "koukou-search/1.0 (personal study tool)"
SLEEP_SEC = 1.5

RE_AREA = re.compile(r'href="([^"]*(?:_area|tei)\.html)"')
RE_ANCHOR = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]{2,40}?)</a>')
RE_ANCHOR_ANY = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.{0,80}?)</a>', re.S)
RE_FOUNDER = re.compile(r"^(大阪府立|大阪市立|府立|私立|市立)")
RE_SUFFIX = re.compile(r"(高等学校|高校|中学校)")
RE_PAREN = re.compile(r"[（(].*?[）)]")


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
    return s.replace("ヶ", "ケ").replace("が", "ケ").replace("國", "国").replace("學", "学")


def depth(url: str) -> int:
    """URL のパスの深さ。トップページほど小さい。"""
    return len([p for p in urllib.parse.urlsplit(url).path.split("/") if p])


def core(s: str) -> str:
    """「大阪学芸高等学校」→「大阪学芸」。私立の一覧は校名のみの短い表記になっている。"""
    return RE_SUFFIX.sub("", normalize(RE_PAREN.sub("", s)))


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


def collect_private() -> dict[str, str]:
    """大阪私立中学校高等学校連合会の加盟校一覧から 校名→URL を作る。

    一覧の表記は「大阪学芸」のように校名のみ。中等教育課程などは
    「大阪学芸（中等教育）」と括弧付きで併記されるので、括弧無しを優先する。
    """
    print(f"私立の一覧ページを取得: {PRIVATE_INDEX}")
    page = get(PRIVATE_INDEX)
    table: dict[str, str] = {}
    n = 0
    for href, raw in RE_ANCHOR_ANY.findall(page):
        if "osaka-shigaku.gr.jp" in href:
            continue
        name = re.sub(r"<[^>]+>", "", raw).strip()
        if not name or len(name) > 30:
            continue
        key = core(name)
        if not key:
            continue
        # 同じ校名が複数並ぶことがある（賢明学院は全日制と通信制課程が同じ表記で2行ある）。
        # 学校のトップページを採るため、パスの浅いURLを優先する。
        prev = table.get(key)
        if prev is None or depth(href) < depth(prev):
            table[key] = href
        n += 1
    print(f"  {n} 校のリンクを取得")
    return table


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    table = collect()
    print(f"\n公立の一覧から {len(table)} 校のURLを取得")
    time.sleep(SLEEP_SEC)
    try:
        priv = collect_private()
    except Exception as e:  # noqa: BLE001 — 公立側だけでも処理を続ける
        print(f"  ! 私立の一覧を取得できませんでした: {e}")
        priv = {}
    print(f"私立の一覧から {len(priv)} 校のURLを取得\n")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    changed = same = missing = 0
    for s in doc["schools"]:
        if s["type"] == "public":
            url = table.get(normalize(s["name"]))
            src = "大阪府 公立高校ホームページ一覧"
        else:
            url = priv.get(core(s["name"]))
            src = "大阪私立中学校高等学校連合会 加盟校一覧"
        if not url:
            if s["type"] == "public":
                # 現役の府立高校はすべてこの一覧に載る。載っていない＝廃校・統合の可能性が高い。
                print(f"   × {s['name']}: 公立の一覧に見つからず（廃校・統合の可能性。"
                      f"{MEMORIAL} で確認すること）")
            else:
                print(f"   × {s['name']}: 私立の一覧に見つからず（校名の表記ゆれか、連合会に未加盟）")
            missing += 1
            continue
        if url == s.get("website"):
            same += 1
            continue
        print(f"   → {s['shortName']}: {s.get('website')}\n        {url}")
        if args.apply:
            s["website"] = url
            s["websiteSource"] = src
            s["linkOk"] = True
        changed += 1

    print(f"\n変更 {changed} / 変更なし {same} / 一覧になし {missing}")
    if args.apply:
        write_json(SCHOOLS, doc)
        print("schools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

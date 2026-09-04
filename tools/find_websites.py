#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公式サイトのURLが間違っている学校について、候補URLを総当たりして正しいものを探す。

schools.json の id にローマ字が入っている（例 pref-tennoji → tennoji）ので、
大阪府立学校でよく使われるURLパターンを組み立てて順に叩く。
ページのタイトルに校名（またはその一部）が含まれていれば採用する。

    python tools/find_websites.py                 # 現在のURLが死んでいる学校だけ探す
    python tools/find_websites.py --all           # 全校について探し直す
    python tools/find_websites.py --apply         # 見つかったURLを schools.json に書き込む
    python tools/find_websites.py --only pref-tennoji

1リクエストにつき1.5秒待つ。相手のサーバーに負荷をかけないこと。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json, write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
UA = "koukou-search/1.0 (personal study tool)"
SLEEP_SEC = 1.5
TIMEOUT = 15

RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
RE_TAG = re.compile(r"<[^>]+>")
RE_CHARSET = re.compile(rb"charset=[\"']?\s*([\w\-]+)", re.I)


def slugs(school: dict) -> list[str]:
    base = re.sub(r"^(pref|priv)-", "", school["id"])
    out = [base]
    if "-" in base:
        out.append(base.replace("-", ""))
        out.append(base.split("-")[0])
    return list(dict.fromkeys(out))


def candidates(school: dict) -> list[str]:
    urls = []
    for s in slugs(school):
        if school["type"] == "public":
            # 大阪府立学校の標準ドメインを最優先に試す
            urls += [
                f"https://www.osaka-c.ed.jp/{s}/",
                f"https://{s}.ed.jp/",
                f"https://www.{s}.ed.jp/",
            ]
        else:
            urls += [
                f"https://www.{s}.ed.jp/",
                f"https://{s}.ed.jp/",
                f"https://www.{s}.ac.jp/",
            ]
    return list(dict.fromkeys(urls))


def fetch_title(url: str) -> tuple[int, str] | None:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "ja"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            raw = res.read(200000)
            if res.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            enc = None
            m = re.search(r"charset=([\w\-]+)", res.headers.get("Content-Type", ""), re.I)
            if m:
                enc = m.group(1)
            if not enc:
                m2 = RE_CHARSET.search(raw[:4096])
                if m2:
                    enc = m2.group(1).decode("ascii", "ignore")
            html = None
            for cand in [enc, "utf-8", "cp932", "euc-jp"]:
                if not cand:
                    continue
                try:
                    html = raw.decode(cand)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if html is None:
                html = raw.decode("utf-8", "replace")
            t = RE_TITLE.search(html)
            title = re.sub(r"\s+", " ", RE_TAG.sub("", t.group(1))).strip() if t else ""
            return res.status, title
    except Exception:  # noqa: BLE001 — 候補を順に試すので個々の失敗は想定内
        return None


def title_matches(school: dict, title: str) -> bool:
    if not title:
        return False
    keys = [school["shortName"], school["name"].replace("大阪府立", "").replace("高等学校", "")]
    return any(k and k in title for k in keys)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="現在のURLが生きている学校も探し直す")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    targets = doc["schools"]
    if args.only:
        targets = [s for s in targets if s["id"] == args.only]
    elif not args.all:
        targets = [s for s in targets if s.get("linkOk") is not True]

    print(f"対象 {len(targets)} 校\n")
    found = 0
    unresolved = []

    for i, s in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {s['name']}")
        hit = None
        for url in candidates(s):
            r = fetch_title(url)
            time.sleep(SLEEP_SEC)
            if not r:
                continue
            status, title = r
            if status == 200 and title_matches(s, title):
                hit = (url, title)
                break
            if status == 200:
                print(f"      到達したが校名不一致: {url}  「{title[:40]}」")
        if hit:
            url, title = hit
            mark = "=" if url == s.get("website") else "→"
            print(f"   {mark} {url}   「{title[:50]}」")
            if args.apply:
                s["website"] = url
                s["linkOk"] = True
            found += 1
        else:
            print("   × 見つからず")
            unresolved.append(s["name"])

    print(f"\n確定 {found} / {len(targets)}")
    if unresolved:
        print("手で調べる必要があるもの:")
        for n in unresolved:
            print("   - " + n)

    if args.apply:
        write_json(SCHOOLS, doc)
        print("\nschools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("\n--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

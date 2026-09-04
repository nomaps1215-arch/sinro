#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""各高校の公式サイトを巡回して、リンクの生死・男女比・制服の情報を拾う。

    python tools/update_schools.py             # 巡回して tools/update_report.json に書くだけ
    python tools/update_schools.py --apply     # 確度の高い項目を schools.json に反映
    python tools/update_schools.py --only pref-mikunigaoka   # 1校だけ

方針:
  - robots.txt を必ず確認し、拒否されているURLは取得しない。
  - 1リクエストごとに待機を入れる（相手サーバーに負荷をかけない）。
  - 男女比・制服は「確実に読み取れたときだけ」書き込む。読めなければ null のまま残し、
    候補は report に記録して人間が確認できるようにする。推測で埋めない。

外部ライブラリ不要（標準ライブラリのみ）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
REPORT = ROOT / "tools" / "update_report.json"

UA = "koukou-search/1.0 (personal study tool; contact: local user)"
SLEEP_SEC = 2.0
TIMEOUT = 20

# 男女の在籍数。全角数字・カンマ・「名/人」の揺れを吸収する。
NUM = r"[0-9０-９,，]{1,6}"
RE_RATIO = [
    re.compile(rf"男子\s*[:：]?\s*({NUM})\s*[名人].{{0,30}}?女子\s*[:：]?\s*({NUM})\s*[名人]", re.S),
    re.compile(rf"男\s*[:：]?\s*({NUM})\s*[名人].{{0,20}}?女\s*[:：]?\s*({NUM})\s*[名人]", re.S),
]
UNIFORM_WORDS = ["ブレザー", "セーラー服", "セーラー", "学ラン", "詰襟", "制服なし", "私服", "標準服"]

RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
RE_META_CHARSET = re.compile(rb"charset=[\"']?\s*([\w\-]+)", re.I)
RE_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
RE_ANY_TAG = re.compile(r"<[^>]+>")


def zen2han(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９，", "0123456789,")).replace(",", "")


def fetch(url: str) -> tuple[int, str] | tuple[int, None]:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "ja"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        raw = res.read()
        if res.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        enc = None
        ct = res.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w\-]+)", ct, re.I)
        if m:
            enc = m.group(1)
        if not enc:
            m2 = RE_META_CHARSET.search(raw[:4096])
            if m2:
                enc = m2.group(1).decode("ascii", "ignore")
        for cand in [enc, "utf-8", "cp932", "euc-jp"]:
            if not cand:
                continue
            try:
                return res.status, raw.decode(cand)
            except (UnicodeDecodeError, LookupError):
                continue
        return res.status, raw.decode("utf-8", "replace")


def robots_allows(url: str, cache: dict) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    rp = cache.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
        except Exception:  # noqa: BLE001 — robots.txt が無いサイトは許可扱い
            rp = None
        cache[base] = rp
        time.sleep(SLEEP_SEC)
    if rp is None:
        return True
    return rp.can_fetch(UA, url)


def to_text(html: str) -> str:
    html = RE_TAG.sub(" ", html)
    html = RE_ANY_TAG.sub(" ", html)
    return re.sub(r"\s+", " ", html)


def find_ratio(text: str):
    for rx in RE_RATIO:
        m = rx.search(text)
        if not m:
            continue
        try:
            male = int(zen2han(m.group(1)))
            female = int(zen2han(m.group(2)))
        except ValueError:
            continue
        total = male + female
        # 高校1校の在籍数としてありえない値は捨てる
        if not (60 <= total <= 3000):
            continue
        return {
            "male": round(male * 100 / total),
            "female": round(female * 100 / total),
            "raw": {"male": male, "female": female},
            "evidence": m.group(0)[:120],
        }
    return None


def find_uniform(text: str):
    hits = [w for w in UNIFORM_WORDS if w in text]
    return hits or None


def process(school: dict, robots_cache: dict) -> dict:
    entry = {"id": school["id"], "name": school["name"], "website": school.get("website")}
    url = school.get("website")
    if not url:
        entry["status"] = "no-url"
        return entry
    if not robots_allows(url, robots_cache):
        entry["status"] = "robots-disallowed"
        return entry
    try:
        status, html = fetch(url)
    except urllib.error.HTTPError as e:
        entry["status"] = f"http-{e.code}"
        return entry
    except Exception as e:  # noqa: BLE001
        entry["status"] = "error"
        entry["error"] = str(e)
        return entry

    entry["status"] = f"ok-{status}"
    m = RE_TITLE.search(html or "")
    if m:
        entry["title"] = re.sub(r"\s+", " ", RE_ANY_TAG.sub("", m.group(1))).strip()[:120]
    text = to_text(html or "")
    entry["genderRatioCandidate"] = find_ratio(text)
    entry["uniformKeywords"] = find_uniform(text)
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="確度の高い項目を schools.json に書き込む")
    ap.add_argument("--only", help="school id を1つだけ指定")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    schools = doc["schools"]
    if args.only:
        schools = [s for s in schools if s["id"] == args.only]
        if not schools:
            print(f"該当なし: {args.only}")
            return 1

    today = dt.date.today().isoformat()
    robots_cache: dict = {}
    report = []
    applied = 0

    for i, s in enumerate(schools, 1):
        print(f"[{i}/{len(schools)}] {s['name']} ...", end=" ", flush=True)
        e = process(s, robots_cache)
        report.append(e)
        print(e["status"])

        if args.apply and e["status"].startswith("ok"):
            s["updatedAt"] = today
            s["linkOk"] = True
            cand = e.get("genderRatioCandidate")
            if cand and not s.get("genderRatio"):
                s["genderRatio"] = {"male": cand["male"], "female": cand["female"]}
                s["genderRatioSource"] = e["website"]
                applied += 1
            kw = e.get("uniformKeywords")
            if kw and not s.get("uniform") and len(kw) == 1:
                # キーワードが1つだけ = 曖昧さが無いときに限り採用する
                s["uniform"] = {"type": kw[0], "note": "公式サイトから自動取得"}
                applied += 1
        elif args.apply:
            s["linkOk"] = False
        time.sleep(SLEEP_SEC)

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nレポート: {REPORT.relative_to(ROOT)}")

    ok = sum(1 for e in report if e["status"].startswith("ok"))
    print(f"到達 {ok} / {len(report)} 件")
    bad = [e for e in report if not e["status"].startswith("ok")]
    if bad:
        print("到達できなかったURL（schools.json の website を直す必要あり）:")
        for e in bad:
            print(f"  - {e['name']}: {e['status']}  {e.get('website')}")

    if args.apply:
        SCHOOLS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"schools.json を更新（自動反映 {applied} 項目）")
        print("→ 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("（--apply を付けると schools.json に反映します）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

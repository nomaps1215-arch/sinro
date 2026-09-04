#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""各高校の公式サイトから、修学旅行の行き先を拾う。

    python tools/fetch_trip.py            # 調べてレポートを出すだけ
    python tools/fetch_trip.py --apply    # data/schools.json に反映
    python tools/fetch_trip.py --only pref-mikunigaoka

■ このデータについて
修学旅行の行き先に公的な一覧は存在しない。各校のサイトの行事案内やブログに
書かれているだけなので、拾えるかどうかは学校のサイトの作り次第。
取れなかった学校は null のままにして、画面には何も出さない。推測で埋めない。

■ 拾いかた
  1. トップページを取得（robots.txt を確認し、1リクエストごとに2秒待つ）
  2. 「修学旅行」「行事」「スクールライフ」などのリンクを最大3ページまで辿る
  3. 「修学旅行」の前後60字以内に行き先らしい地名があれば、その組み合わせを採る
  4. 同じ地名が複数回出たものを優先し、年度が書いてあれば一緒に記録する

行き先の候補は下の DESTINATIONS にある地名だけ。ここに無い行き先は拾えない。
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
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
REPORT = ROOT / "tools" / "trip_report.json"
UA = "koukou-search/1.0 (personal study tool; respects robots.txt)"
SLEEP_SEC = 2.0
TIMEOUT = 20
MAX_PAGES = 4

# 高校の修学旅行でよくある行き先。長いものから先に照合する。
DESTINATIONS = [
    "シンガポール", "マレーシア", "オーストラリア", "ニュージーランド", "ハワイ",
    "カナダ", "アメリカ", "イギリス", "フィンランド", "ベトナム", "カンボジア",
    "タイ", "台湾", "韓国", "グアム", "セブ島", "沖縄本島", "沖縄", "石垣島",
    "宮古島", "北海道", "長崎", "広島", "屋久島", "鹿児島", "熊本", "福岡",
    "九州", "東北", "北陸", "信州", "長野", "山形", "新潟", "東京", "京都",
    "奈良", "四国", "淡路島", "伊勢", "志摩",
]
RE_DEST = re.compile("|".join(re.escape(d) for d in DESTINATIONS))
RE_TRIP = re.compile(r"修学旅行")
RE_YEAR = re.compile(r"(20\d{2})年|令和(\d{1,2})年")

RE_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
RE_ANY_TAG = re.compile(r"<[^>]+>")
RE_META_CHARSET = re.compile(rb"charset=[\"']?\s*([\w\-]+)", re.I)
RE_LINK = re.compile(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
RE_INTEREST = re.compile(r"修学旅行|行事|スクールライフ|school\s*life|学校生活|ブログ|blog|活動", re.I)


def http_get(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        raw = res.read(600_000)
        if res.headers.get("Content-Encoding") == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:  # noqa: BLE001 — 途中で切った gzip は諦める
                return ""
        enc = None
        m = re.search(r"charset=([\w\-]+)", res.headers.get("Content-Type", ""), re.I)
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
            return raw.decode(cand)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def robots_allows(url: str, cache: dict) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
        except Exception:  # noqa: BLE001 — robots.txt が無いサイトは許可扱い
            rp = None
        cache[base] = rp
        time.sleep(SLEEP_SEC)
    rp = cache[base]
    return True if rp is None else rp.can_fetch(UA, url)


def to_text(html: str) -> str:
    html = RE_TAG.sub(" ", html)
    html = RE_ANY_TAG.sub(" ", html)
    return re.sub(r"[\s　]+", " ", html)


def interesting_links(base: str, html: str) -> list[str]:
    out = []
    for href, label in RE_LINK.findall(html):
        label = RE_ANY_TAG.sub("", label).strip()
        if not RE_INTEREST.search(href + " " + label):
            continue
        u = urllib.parse.urljoin(base, href)
        if not u.startswith("http") or u.rstrip("/") == base.rstrip("/"):
            continue
        # 別ドメインには出ない
        if urllib.parse.urlsplit(u).netloc != urllib.parse.urlsplit(base).netloc:
            continue
        if u not in out:
            out.append(u)
    # 「修学旅行」を含むリンクを優先する
    out.sort(key=lambda u: 0 if "修学旅行" in urllib.parse.unquote(u) else 1)
    return out[: MAX_PAGES - 1]


def find_destination(text: str):
    """「修学旅行」の近くにある地名を数え、いちばん多いものを返す。"""
    hits = Counter()
    evidence = {}
    for m in RE_TRIP.finditer(text):
        window = text[max(0, m.start() - 60): m.end() + 60]
        for d in RE_DEST.findall(window):
            hits[d] += 1
            evidence.setdefault(d, window.strip())
    if not hits:
        return None
    dest, n = hits.most_common(1)[0]
    year = None
    ym = RE_YEAR.search(evidence[dest])
    if ym:
        year = ym.group(1) if ym.group(1) else str(2018 + int(ym.group(2)))
    return {"destination": dest, "count": n, "year": year, "evidence": evidence[dest][:160]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    schools = doc["schools"]
    if args.only:
        schools = [s for s in schools if s["id"] == args.only]

    robots_cache: dict = {}
    report, found = [], 0

    for i, s in enumerate(schools, 1):
        url = s.get("website")
        print(f"[{i}/{len(schools)}] {s['name']}", end=" ", flush=True)
        if not url:
            print("公式サイト未登録")
            continue
        if not robots_allows(url, robots_cache):
            print("robots.txt で拒否")
            continue

        pages, texts = [url], []
        try:
            html = http_get(url)
            texts.append(to_text(html))
            pages += interesting_links(url, html)
        except Exception as e:  # noqa: BLE001
            print(f"取得できず（{e}）")
            time.sleep(SLEEP_SEC)
            continue
        time.sleep(SLEEP_SEC)

        for page in pages[1:]:
            if not robots_allows(page, robots_cache):
                continue
            try:
                texts.append(to_text(http_get(page)))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SLEEP_SEC)

        hit = find_destination(" ".join(texts))
        report.append({"id": s["id"], "name": s["name"], "pages": len(texts), "hit": hit})
        if hit:
            found += 1
            print(f"→ {hit['destination']}" + (f"（{hit['year']}年）" if hit["year"] else ""))
            if args.apply:
                s["schoolTrip"] = {
                    "destination": hit["destination"],
                    "year": hit["year"],
                    "source": url,
                    "evidence": hit["evidence"],
                    "fetchedAt": dt.date.today().isoformat(),
                }
        else:
            print("記載を見つけられず")

    write_json(REPORT, report)
    print(f"\n行き先が分かった学校 {found} / {len(schools)}")
    print(f"レポート: {REPORT.relative_to(ROOT)}")
    if args.apply:
        write_json(SCHOOLS, doc)
        print("schools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

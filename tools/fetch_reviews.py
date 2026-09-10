#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""クチコミの「項目別評価点」を取り込み、良い点・気になる点の材料にする。

    python tools/fetch_reviews.py            # 取得して結果を表示するだけ
    python tools/fetch_reviews.py --apply    # data/schools.json に反映
    python tools/fetch_reviews.py --only priv-seikyo-gakuen

■ 本文は取らない
クチコミの本文は投稿者の著作物なので、転載しない。取り込むのは
「校則 2.97」「部活 3.97」のような**集計された点数**と件数だけ。
点数は事実であって表現ではないので、出典を示したうえで扱える。

画面に出す「良い点・気になる点」は、この点数から
評価の高い項目・低い項目を並べて、こちらの言葉で組み立てる（js/app.js）。
つまり誰かの文章を写しているわけではない。

■ 点数の読み方には限界がある
数十人の在校生・卒業生が付けた点数の平均でしかない。母数が少ない学校では
数人の印象で大きく動く。件数も一緒に出して、判断できるようにする。

■ 相手のサーバーへの配慮
robots.txt を確認し、1校1リクエスト、2秒待つ。
学校IDは偏差値一覧のページから拾えるので、検索はしない。
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402
import fetch_deviation as D  # noqa: E402  校名の照合ロジックを共有する

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
CACHE = ROOT / "tools" / ".cache"
REPORT = ROOT / "tools" / "review_report.json"

UA = "koukou-search/1.0 (personal study tool; respects robots.txt)"
SLEEP_SEC = 2.0
TIMEOUT = 25
SOURCE_NAME = "みんなの高校情報"
LIST_URL = "https://www.minkou.jp/hischool/exam/osaka/deviation/"
REVIEW_URL = "https://www.minkou.jp/hischool/school/review/{sid}/"

# 拾う項目。画面ではこの並びで出す。
ASPECTS = ["校則", "いじめの少なさ", "部活", "進学実績", "施設", "制服", "イベント"]
RE_TAG = re.compile(r"<[^>]+>")


def http_get(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        raw = res.read()
        if res.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
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


def school_ids() -> dict[str, str]:
    """偏差値一覧のページから 校名キー → 学校ID を作る。"""
    cached = CACHE / "deviation.html"
    html = cached.read_text(encoding="utf-8") if cached.exists() else http_get(LIST_URL)
    out = {}
    for sid, name in re.findall(
            r'<a[^>]*href="/hischool/school/deviation/(\d+)/"[^>]*>([^<]+)</a>', html):
        out.setdefault(D.key(name.strip()), sid)
    return out


def parse_review(html: str) -> dict | None:
    text = re.sub(r"\s+", " ", RE_TAG.sub(" ", html))
    scores = {}
    for a in ASPECTS:
        m = re.search(re.escape(a) + r"[^0-9]{0,40}([0-5][.．]\d{1,2})", text)
        if m:
            scores[a] = float(m.group(1).replace("．", "."))
    if not scores:
        return None
    m = re.search(r"口コミ[^0-9]{0,10}(\d{1,4})\s*件", text)
    count = int(m.group(1)) if m else None
    overall = round(sum(scores.values()) / len(scores), 2)
    return {"scores": scores, "count": count, "overall": overall}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    ids = school_ids()
    print(f"学校IDを {len(ids)} 件把握しました\n")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    targets = doc["schools"]
    if args.only:
        targets = [s for s in targets if s["id"] == args.only]

    robots_cache: dict = {}
    report, found = [], 0
    today = dt.date.today().isoformat()

    for i, s in enumerate(targets, 1):
        sid = ids.get(D.key(s["name"]))
        print(f"[{i}/{len(targets)}] {s['name']}", end=" ", flush=True)
        if not sid:
            print("一覧に無し")
            continue
        url = REVIEW_URL.format(sid=sid)
        if not robots_allows(url, robots_cache):
            print("robots.txt で拒否")
            continue
        try:
            data = parse_review(http_get(url))
        except Exception as e:  # noqa: BLE001
            print(f"取得できず（{e}）")
            time.sleep(SLEEP_SEC)
            continue
        time.sleep(SLEEP_SEC)

        report.append({"id": s["id"], "name": s["name"], "sid": sid, "data": data})
        if not data:
            print("評価点を読み取れず")
            continue
        found += 1
        top = max(data["scores"].items(), key=lambda kv: kv[1])
        print(f"→ {data['count']}件 / 最高 {top[0]}{top[1]}")
        if args.apply:
            s["reviews"] = {
                "scores": data["scores"],
                "count": data["count"],
                "overall": data["overall"],
                "source": SOURCE_NAME,
                "url": url,
                "fetchedAt": today,
            }

    write_json(REPORT, report)
    print(f"\n評価点が取れた学校 {found} / {len(targets)}")
    if args.apply:
        write_json(SCHOOLS, doc)
        print("schools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

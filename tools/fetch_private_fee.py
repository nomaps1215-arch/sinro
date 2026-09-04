#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""私立高校の入学金を、各校の公式サイトから拾う。

    python tools/fetch_private_fee.py            # 調べてレポートを出すだけ
    python tools/fetch_private_fee.py --apply    # data/schools.json に反映
    python tools/fetch_private_fee.py --only priv-seikyo-gakuen

■ なぜ巡回するのか
公立の入学料は課程ごとに一律で、大阪府が額を公表している（tools/set_admission_fee.py）。
私立は学校ごとに違い、**学校別の額をまとめた機械可読な公的一覧が無い**。
大阪私立中学校高等学校連合会が毎年まとめて公表してはいるが、報道発表の形で
出るだけで、サイト上にデータとして置かれていない。
そのため各校のサイトを見に行くしかない。

■ 拾えないものは拾わない
募集要項がPDFだけの学校は読み取れない。金額が画像になっている学校も読めない。
その場合は null のままにして、画面には「募集要項で確認」と出す。
平均額（令和8年度で約23万円）で埋めることはしない。学校ごとに十数万円違う。

■ 相手のサーバーへの配慮
robots.txt を確認し、1リクエストごとに2秒待つ。1校あたり最大4ページまで。
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

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
REPORT = ROOT / "tools" / "private_fee_report.json"
UA = "koukou-search/1.0 (personal study tool; respects robots.txt)"
SLEEP_SEC = 2.0
TIMEOUT = 20
MAX_PAGES = 4

# 高校の入学金としてありえる範囲。これを外れた数値は別の費用とみなして捨てる。
MIN_FEE, MAX_FEE = 50_000, 500_000

RE_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
RE_ANY_TAG = re.compile(r"<[^>]+>")
RE_META_CHARSET = re.compile(rb"charset=[\"']?\s*([\w\-]+)", re.I)
RE_LINK = re.compile(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
RE_INTEREST = re.compile(
    r"学費|納付金|費用|募集要項|入試|入学|受験|nyushi|nyugaku|admission|exam|fee", re.I)

# 「入学金 200,000円」「入学金（入学時） 200,000 円」などを拾う。
NUM = r"[0-9０-９][0-9０-９,，]{2,8}"
RE_FEE = re.compile(rf"入学金[^0-9０-９]{{0,24}}({NUM})\s*円")
# 「入学金 20万円」の表記
RE_FEE_MAN = re.compile(rf"入学金[^0-9０-９]{{0,24}}({NUM})\s*万円")


def zen2han(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９，", "0123456789,")).replace(",", "")


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
        if not u.startswith("http") or u.lower().endswith(".pdf"):
            continue
        if urllib.parse.urlsplit(u).netloc != urllib.parse.urlsplit(base).netloc:
            continue
        if u.rstrip("/") != base.rstrip("/") and u not in out:
            out.append(u)
    # 「学費」「納付金」を含むリンクを先に見る
    out.sort(key=lambda u: 0 if re.search(r"学費|納付金|gakuhi|fee", urllib.parse.unquote(u), re.I) else 1)
    return out[: MAX_PAGES - 1]


def find_fee(text: str):
    """「入学金 ◯◯円」を探す。ありえない額は捨てる。"""
    cands = []
    for m in RE_FEE.finditer(text):
        try:
            v = int(zen2han(m.group(1)))
        except ValueError:
            continue
        if MIN_FEE <= v <= MAX_FEE:
            cands.append((v, m.group(0)[:80]))
    for m in RE_FEE_MAN.finditer(text):
        try:
            v = int(zen2han(m.group(1))) * 10_000
        except ValueError:
            continue
        if MIN_FEE <= v <= MAX_FEE:
            cands.append((v, m.group(0)[:80]))
    if not cands:
        return None
    # 同じ額が複数出るのが普通。いちばん多く出た額を採る。
    counts: dict[int, int] = {}
    for v, _ in cands:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    evidence = next(e for v, e in cands if v == best)
    return {"amount": best, "evidence": evidence, "candidates": sorted(counts)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    targets = [s for s in doc["schools"] if s["type"] == "private"]
    if args.only:
        targets = [s for s in doc["schools"] if s["id"] == args.only]

    robots_cache: dict = {}
    report, found = [], 0

    for i, s in enumerate(targets, 1):
        url = s.get("website")
        print(f"[{i}/{len(targets)}] {s['name']}", end=" ", flush=True)
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

        hit = find_fee(" ".join(texts))
        report.append({"id": s["id"], "name": s["name"], "pages": len(texts), "hit": hit})
        if hit:
            found += 1
            print(f"→ {hit['amount']:,}円")
            if args.apply:
                s["admissionFee"] = {
                    "amount": hit["amount"],
                    "note": "公式サイトの記載から取得",
                    "source": s["name"] + " 公式サイト",
                    "sourceUrl": url,
                    "evidence": hit["evidence"],
                    "fetchedAt": dt.date.today().isoformat(),
                }
        else:
            print("記載を見つけられず")

    write_json(REPORT, report)
    print(f"\n入学金が分かった学校 {found} / {len(targets)}")
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

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""座標が無い学校について、公式サイトから所在地を読み取って座標を求める。

公式一覧には住所が載っていない学校があり、OpenStreetMap にも見つからないと
通学時間を計算できないまま残ってしまう。その穴を埋めるためのツール。

    python tools/fetch_addresses.py            # 調べて結果を表示するだけ
    python tools/fetch_addresses.py --apply    # data/schools.json に反映
    python tools/fetch_addresses.py --all      # 座標がある学校も住所だけ取り直す

やっていること:
  1. 公式サイトを取得（robots.txt を確認し、1リクエストごとに待つ）
  2. トップに住所が無ければ「アクセス」「交通」「学校概要」などのページも見る。
     リダイレクト用のページだった場合は転送先を追う。
  3. 「〒xxx-xxxx 大阪府◯◯市…」の形を探して住所の候補を集める
  4. 国土地理院の住所検索APIで座標にする
  5. 逆ジオコーダで市区町村を引き直し、住所と一致するものだけ採用する

4と5の両方を通ったものだけ書き込む。ひとつでも合わなければ人手に回す。

府立高校のサイトは共通テンプレートを使っていて、フッターが
「〒xxx-xxxx 住所を入れてください」のまま埋まっていない学校がある。
この手の未入力の文字列は住所として拾わないようにしている。
"""
from __future__ import annotations

import argparse
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
UA = "koukou-search/1.0 (personal study tool; respects robots.txt)"
SLEEP_SEC = 2.0
TIMEOUT = 25

GSI_SEARCH = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="
GSI_REVERSE = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={lat}&lon={lng}"
MUNI_JS = "https://maps.gsi.go.jp/js/muni.js"

BOUNDS = {"lat": (34.2, 35.1), "lng": (135.1, 135.8)}

RE_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
RE_ANY_TAG = re.compile(r"<[^>]+>")
RE_META_CHARSET = re.compile(rb"charset=[\"']?\s*([\w\-]+)", re.I)

# 〒591-8025 大阪府堺市北区長曽根町1179-1 のような並びを拾う。
# 「大阪府」が省略されることも多いので、市区町村から始まる形も許す。
RE_ZIP_ADDR = re.compile(
    r"〒?\s*\d{3}\s*[-−ー–]?\s*\d{4}\s*((?:大阪府)?[^\s<>「」【】]{4,50})")
RE_PLAIN_ADDR = re.compile(
    r"((?:大阪府)?(?:大阪市[^\s<>]{0,4}区|堺市[^\s<>]{0,3}区|[^\s<>]{2,8}[市町村])[^\s<>「」【】,、]{3,40}\d[^\s<>「」【】,、]{0,12})")


def http_get(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        raw = res.read()
        if res.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
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


# 共通テンプレートの未入力プレースホルダを住所と誤認しないための除外語
RE_PLACEHOLDER = re.compile(r"入れてください|ください|xxx|XXX|000-0000|サンプル|example")

RE_META_REFRESH = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\'][^"\']*url=([^"\'>\s]+)', re.I)
RE_LINK = re.compile(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
RE_ACCESS = re.compile(r"アクセス|交通|所在地|学校概要|学校案内|access|about", re.I)


def follow_pages(url: str, html: str, robots_cache: dict) -> list[str]:
    """トップ以外にも見るべきページのURLを返す（多くても3件）。"""
    out = []
    m = RE_META_REFRESH.search(html)
    if m:
        out.append(urllib.parse.urljoin(url, m.group(1).strip()))
    # 本文が極端に短いページはリダイレクト用の踏み台なので、最初のリンクを追う
    text = to_text(html)
    if len(text) < 400:
        for href, _ in RE_LINK.findall(html):
            nxt = urllib.parse.urljoin(url, href)
            if nxt.rstrip("/") != url.rstrip("/"):
                out.append(nxt)
                break
    for href, label in RE_LINK.findall(html):
        label = RE_ANY_TAG.sub("", label).strip()
        if RE_ACCESS.search(href) or RE_ACCESS.search(label):
            out.append(urllib.parse.urljoin(url, href))
    seen, uniq = set(), []
    for u in out:
        if u in seen or not u.startswith("http"):
            continue
        seen.add(u)
        uniq.append(u)
    return uniq[:3]


def normalize_addr(a: str) -> str:
    a = a.strip().strip("　 ,、")
    a = a.translate(str.maketrans("０１２３４５６７８９－−ー", "0123456789---"))
    if not a.startswith("大阪府"):
        a = "大阪府" + a
    return a


def address_candidates(text: str) -> list[str]:
    out = []
    for rx in (RE_ZIP_ADDR, RE_PLAIN_ADDR):
        for m in rx.finditer(text):
            raw = m.group(1)
            if RE_PLACEHOLDER.search(raw):
                continue          # テンプレートの未入力欄
            a = normalize_addr(raw)
            if len(a) < 9 or a in out:
                continue
            out.append(a)
    return out[:8]


def get_json(url: str, tries: int = 4):
    """国土地理院のAPIは短時間に叩くと落ちることがあるので、待ちながらやり直す。"""
    delay = 3
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(delay)
            delay *= 2
    raise last


def geocode(address: str):
    items = get_json(GSI_SEARCH + urllib.parse.quote(address))
    if not items:
        return None
    lng, lat = items[0]["geometry"]["coordinates"][:2]
    if not (BOUNDS["lat"][0] <= lat <= BOUNDS["lat"][1]):
        return None
    if not (BOUNDS["lng"][0] <= lng <= BOUNDS["lng"][1]):
        return None
    return round(lat, 6), round(lng, 6)


def load_muni() -> dict:
    js = http_get(MUNI_JS)
    table = {}
    for m in re.finditer(r"GSI\.MUNI_ARRAY\[\"?(\d+)\"?\]\s*=\s*'([^']*)'", js):
        parts = m.group(2).split(",")
        if len(parts) >= 4:
            table[m.group(1).zfill(5)] = parts[1] + parts[3]
    return table


def reverse_city(lat: float, lng: float, muni: dict):
    doc = get_json(GSI_REVERSE.format(lat=lat, lng=lng))
    code = str((doc.get("results") or {}).get("muniCd") or "").zfill(5)
    return muni.get(code)


def squash(x: str) -> str:
    return x.replace("　", "").replace(" ", "").replace("大阪府", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all", action="store_true", help="座標がある学校も住所を取り直す")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    targets = [s for s in doc["schools"]
               if args.all or s.get("lat") is None or s.get("lng") is None]
    print(f"対象 {len(targets)} 校\n")
    if not targets:
        print("座標が無い学校はありません。")
        return 0

    muni = load_muni()
    print(f"市区町村コード表 {len(muni)} 件\n")
    robots_cache: dict = {}
    fixed, failed = 0, []

    for i, s in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {s['name']}")
        url = s.get("website")
        if not url:
            print("   公式サイトが未登録")
            failed.append(s["name"])
            continue
        if not robots_allows(url, robots_cache):
            print("   robots.txt で拒否されているため取得しない")
            failed.append(s["name"])
            continue
        # トップページと、そこから辿れるアクセス系ページの本文を集める
        pages, texts = [url], []
        seen_pages = set()
        while pages:
            page = pages.pop(0)
            if page in seen_pages or len(seen_pages) >= 4:
                continue
            seen_pages.add(page)
            if not robots_allows(page, robots_cache):
                continue
            try:
                html = http_get(page)
            except Exception as e:  # noqa: BLE001
                print(f"   取得できません（{page}）: {e}")
                time.sleep(SLEEP_SEC)
                continue
            time.sleep(SLEEP_SEC)
            texts.append(to_text(html))
            if page == url:
                pages.extend(follow_pages(page, html, robots_cache))

        if not texts:
            failed.append(s["name"])
            continue
        text = " ".join(texts)

        hit = None
        for addr in address_candidates(text):
            try:
                point = geocode(addr)
            except Exception as e:  # noqa: BLE001
                print(f"   × {addr} → 住所検索に失敗（{e}）")
                point = None
            time.sleep(1.5)
            if not point:
                continue
            try:
                city = reverse_city(*point, muni)
            except Exception:  # noqa: BLE001
                city = None      # 逆引きできないときは検証なしで採る（下でその旨を残す）
            time.sleep(1.5)
            if city is None:
                print(f"   ? {addr} → 逆引きできず、市区町村の照合は省略")
                hit = (addr, point, None)
                break
            # 住所の市区町村と、座標を引き直した市区町村が一致するものだけ採る
            if squash(city) in squash(addr):
                hit = (addr, point, city)
                break
            print(f"   × {addr} → 座標は「{city}」で不一致")

        if not hit:
            print("   住所を確定できませんでした")
            failed.append(s["name"])
            continue

        addr, (lat, lng), city = hit
        print(f"   ○ {addr}\n     {lat}, {lng}（{city or '照合なし'}）")
        if args.apply:
            s["address"] = addr
            if city:
                s["city"] = city.replace("大阪府", "").replace("　", "")
            s["lat"], s["lng"] = lat, lng
            s["coordSource"] = "official-site+gsi"
            if city is None:
                s["dataWarnings"] = (s.get("dataWarnings") or []) + [
                    "座標は公式サイトの住所から求めた値で、市区町村の照合ができていない。"
                ]
        fixed += 1

    print(f"\n確定 {fixed} / 失敗 {len(failed)}")
    for n in failed:
        print("   - " + n)

    if args.apply and fixed:
        write_json(SCHOOLS, doc)
        print("\nschools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py と python tools/qa_check.py を実行してください。")
    elif not args.apply:
        print("\n--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

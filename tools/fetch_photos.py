#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""各高校の写真を Wikimedia Commons から集める。

    python tools/fetch_photos.py            # 調べてレポートを出すだけ
    python tools/fetch_photos.py --apply    # data/schools.json に反映
    python tools/fetch_photos.py --only pref-mikunigaoka

■ なぜ学校の公式サイトから取らないのか
学校サイトの写真はその学校の著作物で、こちらのアプリに並べて出すのは
再配布にあたる。Commons の画像は投稿者が再利用を許す条件で公開したものなので、
出典と作者と license を添えれば載せられる。そこだけを使う。

■ 拾いかた
日本語版 Wikipedia の学校の記事に使われている画像を採る。Commons の全文検索は
「大阪府立各学校入学試験問題答案集（明治41年）」のような無関係な古書PDFまで
引っかかるので使わない。記事に載っている画像は人が選んだものなので確度が高い。

■ 載せないもの
  - Commons 以外にあるファイル（日本語版ローカルの画像は自由ライセンスとは限らない）
  - 幅400px未満（アイコンやスタブ画像）
  - ライセンスが下の ALLOWED に無いもの
  - Restrictions が付いているもの（肖像権など、利用に条件があるもの）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
REPORT = ROOT / "tools" / "photo_report.json"

# Wikimedia は連絡先の分かる User-Agent を求めている
UA = "sinro-highschool-search/1.0 (https://github.com/nomaps1215-arch/sinro)"
JA = "ja.wikipedia.org"
COMMONS = "commons.wikimedia.org"
SLEEP_SEC = 0.5
BATCH = 40
MAX_PHOTOS = 4
THUMB_W = 640
MIN_WIDTH = 400

# 記事に必ず出てくる飾りの画像や、内容と関係の無いファイルを外す
SKIP = re.compile(
    r"(flag|logo|icon|commons|wiki|ambox|question|disambig|symbol|locator|"
    r"map of|osm|stub|edit-|blackboard|crystal|nuvola|emblem)", re.I)
IS_PHOTO = re.compile(r"\.(jpe?g|png)$", re.I)
RE_TAG = re.compile(r"<[^>]+>")

# 表示して問題のないライセンスだけを通す
ALLOWED = re.compile(
    r"^(public domain|cc0|cc[ -]by([ -]sa)?([ -][\d.]+)?|"
    r"pd[ -]|attribution|gfdl.*cc[ -]by[ -]sa)", re.I)


def api(host: str, **params) -> dict:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read())


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def article_images(names: list[str]) -> dict[str, list[str]]:
    """学校名 -> その記事に使われている画像ファイル名。記事が無ければ空。"""
    found: dict[str, list[str]] = {}
    for group in chunks(names, BATCH):
        data = api(JA, action="query", prop="images|pageimages",
                   titles="|".join(group), redirects=1, imlimit=50, piprop="name")
        query = data.get("query", {})
        # リダイレクトと正規化で題名が変わるので、元の名前に戻せるようにする
        back = {}
        for kind in ("redirects", "normalized"):
            for m in query.get(kind, []):
                back[m["to"]] = back.get(m["from"], m["from"])
        for page in query.get("pages", []):
            if page.get("missing"):
                continue
            title = page.get("title", "")
            origin = back.get(title, title)
            files = [i["title"] for i in page.get("images", [])
                     if IS_PHOTO.search(i["title"]) and not SKIP.search(i["title"])]
            lead = page.get("pageimage")
            if lead:
                # 記事の代表画像を先頭に持ってくる
                files.sort(key=lambda f: 0 if f.split(":", 1)[-1].replace("_", " ")
                           == lead.replace("_", " ") else 1)
            if files:
                found[origin] = files[:MAX_PHOTOS + 2]
        time.sleep(SLEEP_SEC)
    return found


def to_commons_title(title: str) -> str:
    """日本語版は「ファイル:」、Commons は「File:」。名前空間を付け替える。"""
    return "File:" + title.split(":", 1)[-1]


def file_info(titles: list[str]) -> dict[str, dict]:
    """Commons のファイル情報（URL・大きさ・ライセンス・作者）をまとめて引く。"""
    out: dict[str, dict] = {}
    for group in chunks([to_commons_title(t) for t in titles], BATCH):
        data = api(COMMONS, action="query", titles="|".join(group), prop="imageinfo",
                   iiprop="url|size|extmetadata", iiurlwidth=THUMB_W)
        for page in data.get("query", {}).get("pages", []):
            if page.get("missing") or not page.get("imageinfo"):
                continue      # Commons に無い＝日本語版ローカルの画像
            info = page["imageinfo"][0]
            meta = info.get("extmetadata", {})
            get = lambda k: RE_TAG.sub("", str(meta.get(k, {}).get("value", ""))).strip()  # noqa: E731
            out[page["title"]] = {
                "thumb": info.get("thumburl") or info.get("url"),
                "full": info.get("url"),
                "width": info.get("width"),
                "height": info.get("height"),
                "page": info.get("descriptionurl"),
                "license": get("LicenseShortName"),
                "licenseUrl": meta.get("LicenseUrl", {}).get("value", ""),
                "author": get("Artist")[:60],
                "restrictions": get("Restrictions"),
            }
        time.sleep(SLEEP_SEC)
    return out


def usable(info: dict) -> bool:
    if not info.get("thumb") or not info.get("license"):
        return False
    if (info.get("width") or 0) < MIN_WIDTH:
        return False
    if info.get("restrictions"):
        return False
    return bool(ALLOWED.match(info["license"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="data/schools.json に反映する")
    ap.add_argument("--only", help="school id を1つだけ指定")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    schools = doc["schools"]
    if args.only:
        schools = [s for s in schools if s["id"] == args.only]
        if not schools:
            print(f"該当なし: {args.only}")
            return 1

    # 記事名は正式名で引く。改称した学校は旧名でも試す。
    wanted: dict[str, list[str]] = {}
    for s in schools:
        cands = [s["name"]]
        if s.get("formerName"):
            cands.append(s["formerName"])
        wanted[s["id"]] = cands

    titles = sorted({t for v in wanted.values() for t in v})
    print(f"Wikipedia を照会：{len(titles)} 件")
    by_title = article_images(titles)

    files = sorted({f for v in by_title.values() for f in v})
    print(f"画像ファイルの情報を照会：{len(files)} 件")
    info = file_info(files)

    report, applied = [], 0
    today = dt.date.today().isoformat()
    for s in schools:
        photos = []
        seen = set()
        for title in wanted[s["id"]]:
            for f in by_title.get(title, []):
                meta = info.get(to_commons_title(f))
                if not meta or not usable(meta) or meta["full"] in seen:
                    continue
                seen.add(meta["full"])
                photos.append({
                    "thumb": meta["thumb"],
                    "full": meta["full"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "author": meta["author"] or "不明",
                    "license": meta["license"],
                    "licenseUrl": meta["licenseUrl"],
                    "page": meta["page"],
                })
                if len(photos) >= MAX_PHOTOS:
                    break
            if len(photos) >= MAX_PHOTOS:
                break
        report.append({"id": s["id"], "name": s["name"], "count": len(photos)})
        if photos:
            applied += 1
            if args.apply:
                s["photos"] = photos
                s["photoSource"] = "Wikimedia Commons"
                s["photoFetchedAt"] = today
        elif args.apply:
            s.pop("photos", None)
            s.pop("photoSource", None)
            s.pop("photoFetchedAt", None)

    write_json(REPORT, report)
    total = sum(r["count"] for r in report)
    print(f"\n写真が見つかった学校 {applied} / {len(schools)}（のべ {total} 枚）")
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

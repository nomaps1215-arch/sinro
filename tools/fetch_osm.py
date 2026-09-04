#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenStreetMap（Overpass API）から駅と高校の実座標を取得して data/*.json を補正する。

国土地理院の住所検索APIは大阪駅・天王寺では±130m の精度だったが、
泉北ニュータウンや岬町の一部で数km外す誤マッチがあったため、
座標の一次ソースは OSM に置く。Overpass は無料・APIキー不要。

    python tools/fetch_osm.py             # 取得して差分を表示するだけ
    python tools/fetch_osm.py --apply     # lines.json / schools.json に反映
    python tools/fetch_osm.py --cache     # 前回取得した生データを再利用（通信しない）

照合の考え方:
  駅  … 駅名が完全一致する候補のうち、現在の座標に最も近いものを採用する。
  高校… 大阪府内で校名はほぼ一意なので、距離ではなく名前の一意性で判定する。
        OSM 側は「府立◯◯高等学校」「◯◯中学校高等学校」のような表記なので、
        設置者の接頭辞と「高等学校/中学校」を落とした識別部分で突き合わせる。
  大きく動いたもの・OSMに無いものは tools/coord_review.json に出して目視確認に回す。

出典: © OpenStreetMap contributors (ODbL)
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / "tools" / ".cache"
ENDPOINT = "https://overpass-api.de/api/interpreter"
UA = "koukou-search/1.0 (personal study tool)"

MAX_MOVE_STATION_M = 9000   # 駅名は府内でほぼ一意なので広めに許容
REVIEW_MOVE_M = 2000        # これを超えて動いた学校は目視確認に回す

Q_STATIONS = """
[out:json][timeout:180];
area["name"="大阪府"]["admin_level"="4"]->.a;
(
  node(area.a)["railway"="station"];
  way(area.a)["railway"="station"];
);
out center tags;
"""

Q_SCHOOLS = """
[out:json][timeout:180];
area["name"="大阪府"]["admin_level"="4"]->.a;
(
  nwr(area.a)["amenity"="school"]["name"~"高等学校|高校"];
  nwr(area.a)["amenity"="college"]["name"~"高等学校"];
);
out center tags;
"""


def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def overpass(query: str, cache_name: str, use_cache: bool) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / cache_name
    if use_cache and path.exists():
        print(f"  キャッシュを使用: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=240) as res:
        doc = json.loads(res.read().decode("utf-8"))
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"  取得 {len(doc.get('elements', []))} 件 -> {path.name}")
    return doc


def elements_to_points(doc: dict) -> list[dict]:
    out = []
    for e in doc.get("elements", []):
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lng = e.get("lon") or (e.get("center") or {}).get("lon")
        name = (e.get("tags") or {}).get("name")
        if lat is None or lng is None or not name:
            continue
        out.append({"name": name, "lat": lat, "lng": lng, "tags": e.get("tags", {})})
    return out


# OSM 側の駅名が別表記になっているもの
STATION_ALIAS = {"あびこ": "我孫子"}

# OSM の学校名は「府立◯◯高等学校」のように設置者が接頭辞になっている
RE_FOUNDER = re.compile(r"^(大阪府立|大阪市立|府立|私立|市立|町立|村立|組合立)")
RE_SUFFIX = re.compile(r"(高等学校|高校|中学校|中等教育学校|・)")


def normalize(s: str) -> str:
    """表記ゆれの吸収。設置者の接頭辞、「ケ/ヶ/が」、駅の接尾語、空白を揃える。"""
    s = s.strip().replace(" ", "").replace("　", "")
    s = re.sub(r"駅$", "", s)
    s = RE_FOUNDER.sub("", s)
    s = s.replace("ヶ", "ケ").replace("ガ", "ケ").replace("が", "ケ")
    s = s.replace("ノ", "の").replace("之", "の")
    return STATION_ALIAS.get(s, s)


def core_token(name: str) -> str:
    """「大阪府立富田林高等学校」→「富田林」。学校名の識別部分だけを取り出す。"""
    return RE_SUFFIX.sub("", normalize(name)).strip()


def build_pool(points: list[dict]) -> dict:
    pool: dict = {}
    for p in points:
        pool.setdefault(normalize(p["name"]), []).append(p)
    return pool


def match_station(name: str, lat: float, lng: float, pool: dict):
    cands = pool.get(normalize(name), [])
    if not cands:
        return None, None
    scored = sorted(((haversine_m(lat, lng, c["lat"], c["lng"]), c) for c in cands), key=lambda x: x[0])
    d, c = scored[0]
    if d > MAX_MOVE_STATION_M:
        return None, d
    return c, d


def match_school(school: dict, points: list[dict]):
    """戻り値: (採用する点 or None, 移動距離 or None, 判定理由)"""
    token = core_token(school["name"])
    exact = [p for p in points if normalize(p["name"]) == normalize(school["name"])]
    if exact:
        cands = exact
    elif len(token) < 2:
        # 「鳳」「岬」のような一文字の校名で部分一致に落とすと誤爆するため、完全一致だけを使う
        return None, None, "完全一致なし（校名が1文字のため部分一致は行わない）"
    else:
        cands = [p for p in points if token in core_token(p["name"])]
    if not cands:
        return None, None, "OSMに該当なし"

    scored = sorted(
        ((haversine_m(school["lat"], school["lng"], p["lat"], p["lng"]), p) for p in cands),
        key=lambda x: x[0],
    )
    d, p = scored[0]
    reason = "一意に一致" if len(cands) == 1 else f"候補{len(cands)}件から最寄りを採用"
    return p, d, reason


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cache", action="store_true", help="通信せずキャッシュを使う")
    args = ap.parse_args()

    print("駅データを取得中 ...")
    st_pool = build_pool(elements_to_points(overpass(Q_STATIONS, "osm_stations.json", args.cache)))
    print("高校データを取得中 ...")
    sc_points = elements_to_points(overpass(Q_SCHOOLS, "osm_schools.json", args.cache))

    lines_doc = json.loads((DATA / "lines.json").read_text(encoding="utf-8"))
    schools_doc = json.loads((DATA / "schools.json").read_text(encoding="utf-8"))

    # ---- 駅 ----
    hit = miss = 0
    misses = []
    for line in lines_doc["lines"]:
        for st in line["stations"]:
            c, d = match_station(st["name"], st["lat"], st["lng"], st_pool)
            if c is None:
                miss += 1
                misses.append(f"{line['name']} {st['name']}")
                continue
            st["lat"] = round(c["lat"], 6)
            st["lng"] = round(c["lng"], 6)
            hit += 1
    print(f"\n駅: 一致 {hit} / 未一致 {miss}")
    for m in misses:
        print("   未一致 " + m)

    # ---- 高校 ----
    shit = smiss = 0
    review = []
    for s in schools_doc["schools"]:
        p, d, reason = match_school(s, sc_points)
        if p is None:
            smiss += 1
            review.append({
                "name": s["name"], "issue": reason,
                "address": s["address"], "coordSource": s.get("coordSource"),
            })
            continue
        s["lat"] = round(p["lat"], 6)
        s["lng"] = round(p["lng"], 6)
        s["coordSource"] = "osm"
        s["osmName"] = p["name"]
        shit += 1
        if d > REVIEW_MOVE_M:
            review.append({
                "name": s["name"],
                "issue": f"{reason} / 座標が {d:.0f}m 移動。住所か校名のどちらかが要確認",
                "osmName": p["name"], "address": s["address"],
            })

    print(f"高校: 一致 {shit} / 未一致 {smiss}")
    if review:
        print("\n■ 目視確認が必要なもの")
        for r in review:
            tail = f"  [OSM: {r['osmName']}]" if r.get("osmName") else ""
            print(f"   - {r['name']}: {r['issue']}{tail}")
        write_json(ROOT / "tools" / "coord_review.json", review)
        print("   -> tools/coord_review.json に書き出しました")

    if not args.apply:
        print("\n--apply を付けると data/*.json に書き込みます。")
        return 0

    lines_doc["meta"]["coordSource"] = "osm"
    schools_doc["meta"]["coordSource"] = "osm"
    write_json(DATA / "lines.json", lines_doc)
    write_json(DATA / "schools.json", schools_doc)
    print("\ndata/lines.json, data/schools.json を更新しました。")
    print("-> 続けて python tools/build_bundle.py を実行してください。")
    print("出典: (c) OpenStreetMap contributors (ODbL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

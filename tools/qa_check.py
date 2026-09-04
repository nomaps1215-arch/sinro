#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""データの妥当性チェック。座標がおかしい学校を炙り出す。

    python tools/qa_check.py             # オフラインのチェックのみ
    python tools/qa_check.py --reverse   # 座標→住所の逆引きで住所との矛盾も調べる

見ているもの:
  - 各校の最寄り駅と直線距離（大阪府南部で駅から3km以上離れる高校は少ないので、
    大きく外れているレコードは座標か住所が疑わしい）
  - --reverse: 国土地理院の逆ジオコーダで座標から市区町村名を引き、
    schools.json の住所と食い違うレコードを検出する
  - JSON の必須項目の欠落と偏差値の異常値
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FAR_M = 3000

REVERSE_URL = (
    "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={lat}&lon={lng}"
)
MUNI_URL = "https://maps.gsi.go.jp/js/muni.js"
UA = "koukou-search/1.0 (personal study tool)"


def load_muni_table() -> dict:
    """国土地理院の市区町村コード表（muni.js）を読み、コード→「府県名 市区町村名」に変換する。"""
    req = urllib.request.Request(MUNI_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        js = res.read().decode("utf-8")
    table = {}
    # GSIMUNI[27141] = '27,大阪府,27141,堺市北区'; の形
    for m in re.finditer(r"GSI\.MUNI_ARRAY\[\"?(\d+)\"?\]\s*=\s*'([^']*)'", js):
        parts = m.group(2).split(",")
        if len(parts) >= 4:
            table[m.group(1).zfill(5)] = parts[1] + parts[3]
    return table


def reverse_city(lat: float, lng: float, muni: dict) -> str | None:
    req = urllib.request.Request(REVERSE_URL.format(lat=lat, lng=lng), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as res:
        doc = json.loads(res.read().decode("utf-8"))
    r = doc.get("results") or {}
    code = str(r.get("muniCd") or "").zfill(5)
    return muni.get(code)


def haversine_m(a, b, c, d):
    R = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reverse", action="store_true", help="座標を住所に逆引きして住所と照合する")
    args = ap.parse_args()

    lines = json.loads((DATA / "lines.json").read_text(encoding="utf-8"))
    schools = json.loads((DATA / "schools.json").read_text(encoding="utf-8"))

    stations = []
    for ln in lines["lines"]:
        for st in ln["stations"]:
            stations.append((st["name"], ln["name"], st["lat"], st["lng"]))

    print(f"駅 {len(stations)} / 高校 {len(schools['schools'])}\n")

    problems = []
    rows = []
    for s in schools["schools"]:
        if s.get("lat") is None or s.get("lng") is None:
            continue
        near = min(stations, key=lambda t: haversine_m(s["lat"], s["lng"], t[2], t[3]))
        d = haversine_m(s["lat"], s["lng"], near[2], near[3])
        rows.append((d, s, near))
        if d > FAR_M:
            problems.append(f"{s['name']}: 最寄り駅まで {d/1000:.1f}km（{near[0]}）  座標源={s.get('coordSource')}  {s['address']}")

    rows.sort(key=lambda r: -r[0])
    print("■ 最寄り駅が遠い順（上位15件）")
    for d, s, near in rows[:15]:
        mark = "!" if d > FAR_M else " "
        print(f" {mark} {s['shortName']:<12} {d:>6.0f}m  {near[0]}（{near[1]}）  [{s.get('coordSource')}]")

    # 必須項目。address と city は公立の一覧に載っていないので必須にしない。
    # 偏差値は公的データが無いため null が正常。範囲外の値だけを弾く。
    required = ["id", "name", "type", "gender", "courses", "website"]
    for s in schools["schools"]:
        for k in required:
            if k not in s or s[k] in (None, "", []):
                problems.append(f"{s.get('name')}: 必須項目 {k} が空")
        if s.get("lat") is None or s.get("lng") is None:
            problems.append(f"{s['name']}: 座標が無く通学時間を計算できない")
        for c in s.get("courses", []):
            dv = c.get("deviation")
            if dv is not None and (not isinstance(dv, int) or not (25 <= dv <= 80)):
                problems.append(f"{s['name']}: 偏差値が異常 {c}")

    ids = [s["id"] for s in schools["schools"]]
    if len(ids) != len(set(ids)):
        problems.append("school id が重複しています")

    # 座標→住所の逆引きで、登録住所との食い違いを検出する
    if args.reverse:
        print("\n■ 座標と住所の照合（国土地理院 逆ジオコーダ）")
        muni = load_muni_table()
        print(f"   市区町村コード表 {len(muni)} 件を取得")
        for s in schools["schools"]:
            try:
                city = reverse_city(s["lat"], s["lng"], muni)
            except Exception as e:  # noqa: BLE001
                print(f"   ? {s['shortName']}: 逆引き失敗 {e}")
                time.sleep(1)
                continue
            if not city:
                print(f"   ? {s['shortName']}: 市区町村を特定できず")
            else:
                # GSI 側は「大阪市　阿倍野区」のように全角スペースが入る。
                # 住所側は「泉南郡岬町」のように郡名が付く。どちらも吸収して包含判定する。
                def squash(x: str) -> str:
                    return x.replace("　", "").replace(" ", "").replace("大阪府", "")

                addr = squash(s["address"])
                key = squash(city)
                if key not in addr:
                    problems.append(
                        f"{s['name']}: 座標は「{city}」だが住所は「{s['address']}」（不一致）"
                    )
                    print(f"   ! {s['shortName']}: 座標={city} / 住所={s['address']}")
            time.sleep(1)

    print("\n■ 要確認")
    if problems:
        for p in problems:
            print("   - " + p)
    else:
        print("   なし")

    # 未取得項目の集計
    n = len(schools["schools"])
    no_ratio = sum(1 for s in schools["schools"] if not s.get("genderRatio"))
    no_uni = sum(1 for s in schools["schools"] if not s.get("uniform"))
    no_upd = sum(1 for s in schools["schools"] if not s.get("updatedAt"))
    print(f"\n■ 未取得の項目\n   男女比 {no_ratio}/{n}　制服 {no_uni}/{n}　公式サイト未巡回 {no_upd}/{n}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

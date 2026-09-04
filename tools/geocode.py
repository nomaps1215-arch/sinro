#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""schools.json の住所から緯度経度を取り直す（国土地理院 住所検索API・無料/キー不要）。

初期データの lat/lng は手入力の概算値（coordSource="approx"）。
このスクリプトを一度流すと公的な測地成果ベースの座標に置き換わり、
通学時間の精度がそのぶん上がる。

    python tools/geocode.py            # approx のものだけ更新
    python tools/geocode.py --force    # 全件更新
    python tools/geocode.py --dry-run  # 書き込まずに結果だけ表示

API: https://msearch.gsi.go.jp/address-search/AddressSearch?q=<住所>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
ENDPOINT = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="
UA = "koukou-search/1.0 (personal study tool)"
SLEEP_SEC = 1.0  # 相手サーバーへの配慮。短くしないこと。

# 大阪府の範囲。ここから外れた結果は誤ヒットとみなす。
BOUNDS = {"lat": (34.2, 35.1), "lng": (135.1, 135.8)}


def geocode(address: str) -> tuple[float, float] | None:
    url = ENDPOINT + urllib.parse.quote(address)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as res:
        items = json.loads(res.read().decode("utf-8"))
    if not items:
        return None
    lng, lat = items[0]["geometry"]["coordinates"][:2]
    if not (BOUNDS["lat"][0] <= lat <= BOUNDS["lat"][1]):
        return None
    if not (BOUNDS["lng"][0] <= lng <= BOUNDS["lng"][1]):
        return None
    return round(lat, 6), round(lng, 6)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="coordSource に関係なく全件更新")
    ap.add_argument("--dry-run", action="store_true", help="ファイルを書き換えない")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    targets = [
        s for s in doc["schools"]
        if args.force or s.get("coordSource") != "gsi"
    ]
    print(f"対象 {len(targets)} 校 / 全 {len(doc['schools'])} 校")

    updated = failed = 0
    for s in targets:
        try:
            hit = geocode(s["address"])
        except Exception as e:  # noqa: BLE001 — 1件の失敗で全体を止めない
            print(f"  ! {s['name']}: 通信エラー {e}")
            failed += 1
            time.sleep(SLEEP_SEC)
            continue

        if hit is None:
            print(f"  ? {s['name']}: 住所を特定できず（{s['address']}）")
            failed += 1
        else:
            lat, lng = hit
            moved_m = _rough_m(s["lat"], s["lng"], lat, lng)
            s["lat"], s["lng"] = lat, lng
            s["coordSource"] = "gsi"
            print(f"  o {s['name']}: {lat}, {lng}  （概算値から {moved_m:.0f}m 移動）")
            updated += 1
        time.sleep(SLEEP_SEC)

    print(f"\n更新 {updated} 件 / 失敗 {failed} 件")
    if args.dry_run:
        print("--dry-run のため書き込みません。")
        return 0
    if updated:
        SCHOOLS.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"書き込み: {SCHOOLS.relative_to(ROOT)}")
        print("→ 続けて python tools/build_bundle.py を実行してください。")
    return 0


def _rough_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math

    dlat = (lat2 - lat1) * 111_000
    dlng = (lng2 - lng1) * 111_000 * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlng)


if __name__ == "__main__":
    sys.exit(main())

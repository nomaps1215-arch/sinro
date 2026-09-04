#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenStreetMap の路線リレーションから、大阪府全域の路線・駅データを生成する。

駅の並び順を手で書くのは量が多すぎて間違えるので、OSM の route リレーション
（停車駅が順番どおりに入っている）から自動で組み立てる。

    python tools/fetch_osm_lines.py            # 生成して差分を表示するだけ
    python tools/fetch_osm_lines.py --apply    # data/lines.json に反映
    python tools/fetch_osm_lines.py --cache    # 前回の取得結果を使う（通信しない）

取捨選択の方針:
  - 新幹線と特急列車（のぞみ、はるか、ラピート等）は通学に使わないので除外する。
    これらを入れると停車駅の少ない経路が最短として選ばれ、所要時間が過小になる。
  - 快速・急行の系統も除外し、停車駅がいちばん多い系統（＝各駅停車）を採用する。
    所要時間は路線ごとの実効速度で調整しているので、停車駅は多い方が経路として正しい。
  - 同じ路線で上り下りの2本があるときは、駅数の多い方を採る。

既に data/lines.json にある路線は、実測ダイヤに合わせて速度を較正済みなので
その設定（avgSpeedKmh / waitMin / throughTo）を引き継ぐ。

出典: (c) OpenStreetMap contributors (ODbL)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json, write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LINES = DATA / "lines.json"
CACHE = ROOT / "tools" / ".cache"
ENDPOINT = "https://overpass-api.de/api/interpreter"
UA = "koukou-search/1.0 (personal study tool)"

Q_ROUTES = """
[out:json][timeout:300];
area["name"="大阪府"]["admin_level"="4"]->.a;
relation(area.a)["type"="route"]["route"~"^(train|subway|monorail|light_rail)$"];
out body;
"""

# 通学に使わない列車。これらを残すと停車駅の少ない経路が選ばれて所要時間が過小になる。
EXCLUDE_NAME = re.compile(
    r"新幹線|のぞみ|ひかり|こだま|みずほ|さくら|つばめ|はるか|ラピート|サンダーバード|"
    r"こうのとり|はまかぜ|くろしお|ひのとり|アーバンライナー|らくラクはりま|"
    r"まほろば|びわこエクスプレス|特急|ライナー|貨物|回送"
)
# 系統名の飾りを落として路線名にまとめるための表現
STRIP_PATTERNS = [
    re.compile(r"\s*[（(][^（(）)]*[）)]\s*"),          # (大正=>門真南) など
    re.compile(r"\s*(普通|各駅停車|快速|区間快速|新快速|急行|区間急行|準急|区間準急|通勤快速|直通快速|区急|快急)\s*"),
    re.compile(r"\s*[:：].*$"),                        # サンダーバード: 大阪 -> 敦賀
    re.compile(r"\s*(上り|下り|内回り|外回り)\s*"),
]
# 停車駅の少ない系統は除外する（各駅停車だけを残す）
SKIP_SERVICE = re.compile(r"快速|急行|準急|区急|快急|ライナー")

# OSM の路線名は事業者の正式名だったり誤記だったりするので、通りのよい名前に揃える。
# 既存の較正済み定義と同じ名前にすることで、速度設定が引き継がれる。
RENAME = {
    "Osaka Metor谷町線": "Osaka Metro谷町線",          # OSM 側の誤記
    "南海電気鉄道高野線": "南海高野線",
    "南海電気鉄道泉北線": "泉北高速鉄道",
    "南海電気鉄道汐見橋線": "南海汐見橋線",
    "近畿日本鉄道南大阪線": "近鉄南大阪線",
    "近畿日本鉄道長野線": "近鉄長野線",
    "近畿日本鉄道大阪線": "近鉄大阪線",
    "近畿日本鉄道奈良線": "近鉄奈良線",
    "近畿日本鉄道難波線": "近鉄難波線",
    "近畿日本鉄道けいはんな線": "近鉄けいはんな線",
    "近畿日本鉄道信貴線": "近鉄信貴線",
    "近畿日本鉄道道明寺線": "近鉄道明寺線",
    "阪急電鉄宝塚本線": "阪急宝塚線",
    "阪急電鉄神戸本線": "阪急神戸線",
    "阪急神戸本線": "阪急神戸線",
    "阪急電鉄京都本線": "阪急京都線",
    "阪急京都本線": "阪急京都線",
    "阪急電鉄箕面線": "阪急箕面線",
    "阪神電気鉄道本線": "阪神本線",
    "阪神電気鉄道阪神なんば線": "阪神なんば線",
    "京阪電気鉄道交野線": "京阪交野線",
    "京阪電気鉄道京阪本線": "京阪本線",
    "京阪電気鉄道本線": "京阪本線",
    "JR関西空港線 シャトル": "JR関西空港線",
}

DEFAULT_SPEED = {"subway": 32, "train": 45, "monorail": 30, "light_rail": 18}
DEFAULT_WAIT = {"subway": 3, "train": 5, "monorail": 5, "light_rail": 8}

# 新しい書き方（PTv2）は stop、古い書き方（PTv1）は station や役割なしで駅を並べている。
STOP_ROLES = ("stop", "stop_entry_only", "stop_exit_only")
FALLBACK_ROLES = ("", "station", "halt", "forward:stop", "backward:stop")


def overpass(query: str, name: str, use_cache: bool) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if use_cache and path.exists():
        print(f"  キャッシュを使用: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))
    # Overpass は混んでいると 429 / 504 を返す。待ち時間を伸ばしながら数回やり直す。
    delay = 10
    for attempt in range(1, 6):
        req = urllib.request.Request(
            ENDPOINT, data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
            headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=300) as res:
                doc = json.loads(res.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == 5:
                raise
            print(f"  {name}: HTTP {e.code}。{delay}秒待って再試行（{attempt}/5）")
            time.sleep(delay)
            delay *= 2
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"  取得 {len(doc.get('elements', []))} 件 -> {path.name}")
    return doc


def fetch_nodes(ids: list[int], use_cache: bool) -> dict[int, dict]:
    """停車駅ノードの名前と座標を、まとめて引く。"""
    out: dict[int, dict] = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = f"[out:json][timeout:180];node(id:{','.join(map(str, chunk))});out body;"
        doc = overpass(q, f"osm_nodes_{i // 400}.json", use_cache)
        for e in doc.get("elements", []):
            out[e["id"]] = e
        if not use_cache:
            time.sleep(1.5)
    return out


def canonical(name: str) -> str:
    s = name
    for rx in STRIP_PATTERNS:
        s = rx.sub("", s)
    s = s.strip()
    return RENAME.get(s, s)


def stop_refs(rel: dict) -> list[int]:
    """リレーションから停車駅ノードを順番どおりに取り出す。

    新しい書き方では role="stop" が付くが、古い書き方の路線（京阪本線など）は
    role が空のまま駅ノードが並んでいる。前者が取れないときだけ後者に落とす。
    """
    members = rel.get("members", [])
    stops = [m["ref"] for m in members if m.get("type") == "node" and m.get("role") in STOP_ROLES]
    if len(stops) >= 3:
        return stops
    return [m["ref"] for m in members if m.get("type") == "node" and m.get("role") in FALLBACK_ROLES]


def line_id(name: str, used: set) -> str:
    base = re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower()
    if not base:
        base = "line-" + str(len(used) + 1)
    cand, i = base, 2
    while cand in used:
        cand, i = f"{base}-{i}", i + 1
    used.add(cand)
    return cand


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cache", action="store_true")
    args = ap.parse_args()

    print("路線リレーションを取得中 ...")
    doc = overpass(Q_ROUTES, "osm_routes.json", args.cache)
    rels = doc.get("elements", [])
    print(f"  route リレーション {len(rels)} 件")

    # 使う系統だけに絞る
    cands = []
    for r in rels:
        t = r.get("tags", {})
        name = t.get("name") or t.get("name:ja") or ""
        if not name or EXCLUDE_NAME.search(name):
            continue
        if SKIP_SERVICE.search(name):
            continue
        if t.get("service") in ("high_speed", "long_distance"):
            continue
        stops = stop_refs(r)
        if len(stops) < 3:
            continue
        cands.append({
            "name": canonical(name),
            "route": t.get("route", "train"),
            "operator": t.get("operator") or t.get("network") or "",
            "colour": t.get("colour") or "#888888",
            "stops": stops,
        })
    print(f"  通学に使える系統 {len(cands)} 件")

    # 同じ路線名で複数あるときは停車駅がいちばん多いものを採る
    best: dict[str, dict] = {}
    for c in cands:
        prev = best.get(c["name"])
        if prev is None or len(c["stops"]) > len(prev["stops"]):
            best[c["name"]] = c
    print(f"  路線数 {len(best)}")

    print("\n停車駅の座標を取得中 ...")
    node_ids = [n for c in best.values() for n in c["stops"]]
    nodes = fetch_nodes(node_ids, args.cache)
    print(f"  駅ノード {len(nodes)} 件")

    # 既存の設定（手で較正した速度など）を引き継ぐ
    old = json.loads(LINES.read_text(encoding="utf-8"))
    old_by_name = {l["name"]: l for l in old["lines"]}

    used_ids: set = set()
    out_lines = []
    for name, c in sorted(best.items()):
        stations = []
        seen_prev = None
        for nid in c["stops"]:
            n = nodes.get(nid)
            if not n:
                continue
            nm = (n.get("tags") or {}).get("name")
            if not nm or nm == seen_prev:
                continue
            stations.append({"name": nm, "lat": round(n["lat"], 6), "lng": round(n["lon"], 6)})
            seen_prev = nm
        if len(stations) < 3:
            continue
        prev = old_by_name.get(name)
        # 駅は多いほうを採る。OSM のほうが延伸区間まで持っていることが多い。
        use_osm_stations = not prev or len(stations) >= len(prev["stations"])
        rec = {
            "id": prev["id"] if prev else line_id(name, used_ids),
            "name": name,
            "operator": (prev or {}).get("operator") or c["operator"],
            "color": (prev or {}).get("color") or c["colour"],
            "avgSpeedKmh": (prev or {}).get("avgSpeedKmh") or DEFAULT_SPEED.get(c["route"], 45),
            "waitMin": (prev or {}).get("waitMin") or DEFAULT_WAIT.get(c["route"], 5),
            "stations": stations if use_osm_stations else prev["stations"],
            "speedCalibrated": bool(prev),
        }
        if prev and prev.get("throughTo"):
            rec["throughTo"] = prev["throughTo"]
        if prev:
            used_ids.add(prev["id"])
        out_lines.append(rec)

    kept = sum(1 for l in out_lines if l["speedCalibrated"])
    total_st = sum(len(l["stations"]) for l in out_lines)
    print(f"\n路線 {len(out_lines)}（うち速度較正済み {kept}） / 駅のべ {total_st}")
    print("\n新しく入る路線:")
    for l in out_lines:
        if not l["speedCalibrated"]:
            print(f"   {l['name']}（{len(l['stations'])}駅, {l['avgSpeedKmh']}km/h・既定値）")

    missing = [n for n in old_by_name if n not in {l["name"] for l in out_lines}]
    if missing:
        print("\nOSM から作れず、既存の定義をそのまま残す路線:")
        for n in missing:
            print("   " + n)
        out_lines.extend(old_by_name[n] for n in missing)

    if not args.apply:
        print("\n--apply を付けると data/lines.json に反映します。")
        return 0

    old["lines"] = out_lines
    old["meta"]["coordSource"] = "osm"
    old["meta"]["notes"] = [
        "路線と駅の並び順は OpenStreetMap の route リレーションから自動生成している"
        "（tools/fetch_osm_lines.py）。手で並べ替えないこと。",
        "avgSpeedKmh は実効速度。speedCalibrated が true の路線は実測ダイヤに合わせて調整済み、"
        "false の路線は種別ごとの既定値のままなので、使ううちに合わせ込む。",
        "新幹線と特急は除外している。入れると停車駅の少ない経路が選ばれて所要時間が過小になる。",
        "乗換は transit.js が駅間の距離から自動生成する（既定 500m 以内）。",
    ]
    write_json(LINES, old)
    print("\ndata/lines.json を更新しました。")
    print("-> 続けて python tools/build_bundle.py と python tools/qa_check.py を実行してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

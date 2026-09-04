#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大阪府内の高校を公式一覧から丸ごと取り込んで data/schools.json を作り直す。

手入力では抜けが多すぎるので、名簿は必ず公式の一覧から作る。

    python tools/import_all_schools.py            # 差分を表示するだけ
    python tools/import_all_schools.py --apply    # data/schools.json を書き換える
    python tools/import_all_schools.py --cache    # OSM はキャッシュを使う

取ってくるもの:
  公立（大阪府「公立高校ホームページ一覧」）
      校名 / 公式URL / 学科 / 全日制か定時制通信制か
  私立（大阪私立中学校高等学校連合会「加盟校一覧」）
      校名 / 公式URL / 所在地 / 男女別（アイコン01=男子校 02=女子校 03=共学）
  座標
      OpenStreetMap の学校ポイントを校名で照合。無いものは住所から国土地理院APIで引く。

取ってこないもの（自動で取れないので、既存の値を引き継ぐか null のままにする）:
  偏差値   … 公的データが存在しない。既に手で入れたものだけ残す
  男女比   … 公式サイトに載っている学校だけ update_schools.py が拾う
  制服     … 同上

既に data/schools.json にある学校は、校名で突き合わせて
偏差値・制服・男女比・警告文・座標の手直しを引き継ぐ。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402
import fetch_osm as O  # noqa: E402  座標の照合ロジックを再利用する

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCHOOLS = DATA / "schools.json"
CACHE = ROOT / "tools" / ".cache"

PUBLIC_INDEX = "https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/index.html"
PRIVATE_INDEX = "https://www.osaka-shigaku.gr.jp/school/index.html"
GSI = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="
UA = "koukou-search/1.0 (personal study tool)"
SLEEP_SEC = 1.5

RE_AREA = re.compile(r'href="([^"]*(?:_area|tei)\.html)"')
RE_TAG = re.compile(r"<[^>]+>")
RE_FOUNDER = re.compile(r"^(大阪府立|大阪市立|府立|私立|市立|町立|村立|組合立)")
RE_SUFFIX = re.compile(r"(高等学校|高校|中学校|中等教育学校|・)")

# 私学連合会の一覧で使われている男女別アイコン。既知の男子校・女子校で検証済み。
#   01 = 大阪星光学院・清風・明星 → 男子校
#   02 = 四天王寺・大阪女学院・金蘭会・梅花 → 女子校
#   03 = 清風南海・浪速・清教学園 → 共学
GENDER_ICON = {"01": "boys", "02": "girls", "03": "coed"}


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def strip(s: str) -> str:
    return re.sub(r"\s+", " ", RE_TAG.sub(" ", s)).strip()


def key(name: str) -> str:
    """照合キー。設置者の接頭辞と「高等学校」を落とし、表記ゆれを揃える。"""
    s = name.strip().replace(" ", "").replace("　", "")
    s = RE_FOUNDER.sub("", s)
    s = RE_SUFFIX.sub("", s)
    return s.replace("ヶ", "ケ").replace("が", "ケ").replace("國", "国").replace("學", "学")


def slug_from_url(url: str, used: set) -> str:
    """公式URLから ASCII の id を作る。assets のファイル名にも使うので ASCII に限る。"""
    parts = urllib.parse.urlsplit(url)
    seg = [p for p in parts.path.split("/") if p and not p.endswith((".html", ".php", ".htm"))]
    base = seg[-1] if seg else parts.netloc
    base = re.sub(r"^(www\d*\.)", "", base)
    base = re.sub(r"\.(ed|ac|or|co|lg)?\.?jp$|\.com$|\.net$", "", base)
    base = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")
    base = base or "school"
    cand = base
    i = 2
    while cand in used:
        cand = f"{base}-{i}"
        i += 1
    used.add(cand)
    return cand


# ---------------------------------------------------------------- 公立
def collect_public() -> list[dict]:
    print(f"公立の一覧を取得: {PUBLIC_INDEX}")
    idx = get(PUBLIC_INDEX)
    areas = sorted(set(RE_AREA.findall(idx)))
    time.sleep(SLEEP_SEC)

    out = []
    for a in areas:
        url = urllib.parse.urljoin(PUBLIC_INDEX, a)
        division = "定時制・通信制" if a.endswith("tei.html") else "全日制"
        try:
            page = get(url)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {a}: {e}")
            time.sleep(SLEEP_SEC)
            continue

        # 学校名リンクの位置で本文を切り、その間のテキストを学科として拾う
        anchors = list(re.finditer(
            r'<td[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]{2,40}?)</a>', page))
        n = 0
        for i, m in enumerate(anchors):
            name = strip(m.group(2))
            if not name.endswith("高等学校"):
                continue
            end = anchors[i + 1].start() if i + 1 < len(anchors) else len(page)
            # 表の終わりや次の見出しをまたぐと、別の地区の見出し語まで学科として拾ってしまう
            chunk = page[m.end():end]
            cut = re.search(r"</table>|<th[\s>]|高等学校名|学科名|地区にある", chunk)
            if cut:
                chunk = chunk[:cut.start()]
            body = strip(chunk)
            courses = [c for c in re.split(r"[、,]|</td>", body) if c.strip()]
            courses = [re.sub(r"【.*?】", "", c).strip() for c in courses]
            courses = [c for c in courses
                       if 1 < len(c) < 30
                       and not re.search(r"学校名|学科等|一覧|課程$|^\d+$", c)
                       and not c.endswith(("市", "町", "村", "区"))]
            out.append({
                "name": "大阪府立" + name if not name.startswith("大阪") else name,
                "listName": name,
                "website": m.group(1),
                "courseNames": courses[:8] or ["普通科"],
                "division": division,
                "type": "public",
                "gender": "coed",  # 大阪府立高校はすべて共学
                "address": None,
            })
            n += 1
        print(f"  {a.rsplit('/', 1)[-1]}: {n} 校")
        time.sleep(SLEEP_SEC)

    # 同じ学校が複数の地区ページに出ることはないが、念のため重複を落とす
    seen, uniq = set(), []
    for s in out:
        if key(s["name"]) in seen:
            continue
        seen.add(key(s["name"]))
        uniq.append(s)
    return uniq


# ---------------------------------------------------------------- 私立
RE_PRIV_BLOCK = re.compile(
    r'<div class="slboxl"><a href="([^"]+)"[^>]*>(.*?)</a></div>'
    r'.*?<strong>所在地</strong>(.*?)</div>(.*?)(?=<div class="slboxl"|\Z)',
    re.S,
)


def collect_private() -> list[dict]:
    print(f"私立の一覧を取得: {PRIVATE_INDEX}")
    page = get(PRIVATE_INDEX)
    out = []
    for href, rawname, rawaddr, tail in RE_PRIV_BLOCK.findall(page):
        name = strip(rawname)
        if not name or len(name) > 30:
            continue
        addr = strip(rawaddr)
        icons = re.findall(r"ichiran_ico(\d+)\.png", tail)
        gender = next((GENDER_ICON[i] for i in icons if i in GENDER_ICON), None)
        if gender is None:
            print(f"  ? {name}: 男女別アイコンを判定できず（icons={icons}）")
            gender = "coed"
        if addr and not addr.startswith("大阪府"):
            addr = "大阪府" + addr
        out.append({
            "name": name if name.endswith("高等学校") else name + "高等学校",
            "listName": name,
            "website": href,
            "courseNames": ["普通科"],
            "division": "全日制",
            "type": "private",
            "gender": gender,
            "address": addr or None,
        })
    # 同じ校名が2行あることがある（賢明学院は全日制と通信制課程で2行）。
    # 学校としては1つなので、URLのパスが浅いほう＝本体のページを残す。
    best: dict[str, dict] = {}
    for s in out:
        k = key(s["name"])
        prev = best.get(k)
        depth = len([p for p in urllib.parse.urlsplit(s["website"]).path.split("/") if p])
        if prev is None or depth < prev["_depth"]:
            s["_depth"] = depth
            best[k] = s
    out = list(best.values())
    for s in out:
        s.pop("_depth", None)
    print(f"  {len(out)} 校")
    return out


# ---------------------------------------------------------------- 座標
def geocode(address: str):
    req = urllib.request.Request(GSI + urllib.parse.quote(address), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as res:
        items = json.loads(res.read().decode("utf-8"))
    if not items:
        return None
    lng, lat = items[0]["geometry"]["coordinates"][:2]
    if not (34.2 <= lat <= 35.1 and 135.1 <= lng <= 135.8):
        return None
    return round(lat, 6), round(lng, 6)


def attach_coords(schools: list[dict], use_cache: bool) -> tuple[int, int, int]:
    doc = O.overpass(O.Q_SCHOOLS, "osm_schools.json", use_cache)
    pts = O.elements_to_points(doc)
    pool: dict[str, list] = {}
    for p in pts:
        pool.setdefault(key(p["name"]), []).append(p)

    from_osm = from_gsi = none = 0
    for s in schools:
        cands = pool.get(key(s["name"]))
        if cands:
            p = cands[0]
            s["lat"], s["lng"] = round(p["lat"], 6), round(p["lng"], 6)
            s["coordSource"] = "osm"
            s["osmName"] = p["name"]
            from_osm += 1
            continue
        if s.get("address"):
            try:
                hit = geocode(s["address"])
            except Exception:  # noqa: BLE001
                hit = None
            time.sleep(1.0)
            if hit:
                s["lat"], s["lng"] = hit
                s["coordSource"] = "gsi"
                from_gsi += 1
                continue
        s["lat"] = s["lng"] = None
        s["coordSource"] = None
        none += 1
    return from_osm, from_gsi, none


# ---------------------------------------------------------------- 統合
CARRY = ["genderRatio", "genderRatioSource", "uniform", "uniformImage",
         "dataWarnings", "notes", "formerName", "verified"]


def merge(imported: list[dict], existing: list[dict]) -> tuple[list[dict], dict]:
    old = {key(s["name"]): s for s in existing}
    for s in existing:
        if s.get("formerName"):
            old.setdefault(key(s["formerName"]), s)

    used_ids = set()
    matched: set[int] = set()  # 旧校名の別名キーが残るので、実体で照合済みを記録する
    stats = {"kept": 0, "new": 0, "dropped": 0, "deviation": 0}
    out = []
    for s in imported:
        prev = old.pop(key(s["name"]), None)
        if prev is not None:
            matched.add(id(prev))
        rec = {
            "id": prev["id"] if prev else None,
            "name": s["name"],
            "shortName": s["listName"].replace("高等学校", "") or s["name"],
            "type": s["type"],
            "gender": s["gender"],
            "division": s["division"],
            "city": None,
            "address": s.get("address") or (prev or {}).get("address"),
            "lat": s.get("lat"), "lng": s.get("lng"),
            "coordSource": s.get("coordSource"),
            "courses": [],
            "genderRatio": None,
            "uniform": None,
            "website": s["website"],
            "websiteSource": "公式一覧",
            "updatedAt": None,
            "verified": False,
        }
        if prev:
            rec["id"] = prev["id"]
            used_ids.add(prev["id"])
            for k in CARRY:
                if prev.get(k) not in (None, [], ""):
                    rec[k] = prev[k]
            # 座標は OSM が取れていればそちらを優先し、取れなければ以前の値を使う
            if rec["lat"] is None and prev.get("lat") is not None:
                rec["lat"], rec["lng"] = prev["lat"], prev["lng"]
                rec["coordSource"] = prev.get("coordSource")
            # 偏差値は手入力なので、学科名が一致するものに引き継ぐ
            devs = {c["name"]: c.get("deviation") for c in prev.get("courses", [])}
            single = list(devs.values())[0] if len(devs) == 1 else None
            for cn in s["courseNames"]:
                d = devs.get(cn, single if len(s["courseNames"]) == 1 else None)
                rec["courses"].append({"name": cn, "deviation": d})
            if any(c["deviation"] is not None for c in rec["courses"]):
                stats["deviation"] += 1
            stats["kept"] += 1
        else:
            rec["courses"] = [{"name": cn, "deviation": None} for cn in s["courseNames"]]
            stats["new"] += 1
        out.append(rec)

    # id の割り当て（既存のものはそのまま、新規はURLから作る）
    for rec, s in zip(out, imported):
        if rec["id"] is None:
            prefix = "pref-" if rec["type"] == "public" else "priv-"
            rec["id"] = prefix + slug_from_url(s["website"], used_ids)

    leftover = {id(s): s for s in old.values() if id(s) not in matched}
    stats["dropped"] = len(leftover)
    stats["droppedNames"] = [s["name"] for s in leftover.values()]
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cache", action="store_true", help="OSM はキャッシュを使う")
    args = ap.parse_args()

    imported = collect_public() + collect_private()
    print(f"\n一覧から合計 {len(imported)} 校\n")

    print("座標を付与中 ...")
    a, b, c = attach_coords(imported, args.cache)
    print(f"  OSM {a} / 住所から {b} / 座標なし {c}")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    merged, stats = merge(imported, doc["schools"])

    no_coord = [s for s in merged if s["lat"] is None]
    print(f"\n既存を引き継いだ {stats['kept']} / 新規 {stats['new']} / 偏差値あり {stats['deviation']}")
    print(f"座標が無く通学時間を計算できない学校 {len(no_coord)} 校")
    for s in no_coord[:20]:
        print(f"   - {s['name']}")
    if stats["dropped"]:
        print(f"\n公式一覧に無くなった学校 {stats['dropped']} 校（廃校・校名変更の可能性）:")
        for n in stats["droppedNames"]:
            print(f"   - {n}")

    if not args.apply:
        print("\n--apply を付けると data/schools.json を書き換えます。")
        return 0

    doc["schools"] = merged
    doc["meta"]["source"] = {
        "公立": PUBLIC_INDEX,
        "私立": PRIVATE_INDEX,
        "座標": "OpenStreetMap (c) OpenStreetMap contributors, ODbL / 国土地理院 住所検索API",
    }
    write_json(SCHOOLS, doc)
    print(f"\ndata/schools.json を {len(merged)} 校で更新しました。")
    print("-> 続けて python tools/build_bundle.py と python tools/qa_check.py を実行してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大阪府が公表している「学校別在籍者数」から、学校ごとの男女比を作る。

    python tools/fetch_gender_ratio.py            # 取得して結果を表示するだけ
    python tools/fetch_gender_ratio.py --apply    # data/schools.json に反映

一次ソース:
    https://www.pref.osaka.lg.jp/o180040/kotogakko/chigai/index.html
    「データで見る府立高校」の 学校別在籍者数（Excel）

Excel には 学校名 / 学科 / 学年ごとの男女 / 総男・総女・総計 が入っている。
学科ごとに行が分かれているので、学校単位で合計して比を出す。
公表された実数なので推定ではない。

■ 対象は府立高校だけ
このファイルに私立は入っていない（147校ぶん）。私立の学校別在籍者数を
まとめた公的な一覧は見当たらないため、私立は未取得のままにする。
公式サイトを巡回する tools/update_schools.py が拾えれば、そちらで埋まる。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402
from fetch_capacity import read_xlsx  # noqa: E402  xlsx の読み取りを再利用する

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
UA = "koukou-search/1.0 (personal study tool)"
SOURCE_PAGE = "https://www.pref.osaka.lg.jp/o180040/kotogakko/chigai/index.html"

# 新しい年度から順に試す。年度が変わったら先頭に足す。
CANDIDATES = [
    ("令和8年度", "https://www.pref.osaka.lg.jp/documents/35613/r08_zaiseki_2.xlsx"),
    ("令和7年度", "https://www.pref.osaka.lg.jp/documents/35613/r07_zaiseki.xlsx"),
    ("令和6年度", "https://www.pref.osaka.lg.jp/documents/35613/r06_zaiseki_2.xlsx"),
]

# 列の位置。0=区分 1=学校名 2=学科 …… 12=総男 13=総女 14=総計
COL_NAME, COL_MALE, COL_FEMALE = 1, 12, 13

RE_RUBY = re.compile(r"[ァ-ヶー]+$")
RE_FOUNDER = re.compile(r"^(大阪府立|大阪市立|府立|市立|私立)")
RE_SUFFIX = re.compile(r"(高等学校|高校)")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def key(name: str) -> str:
    s = RE_RUBY.sub("", name.strip()).replace(" ", "").replace("　", "")
    s = RE_FOUNDER.sub("", s)
    s = RE_SUFFIX.sub("", s)
    return s.replace("ヶ", "ケ").replace("が", "ケ")


def num(v: str):
    v = (v or "").strip().replace(",", "")
    try:
        return int(float(v))
    except ValueError:
        return None      # 「―」など、募集していない学年


def parse(rows: list[list[str]]) -> dict[str, dict]:
    """学校ごとに男女の在籍数を合計する。学科ごとに行が分かれているため足し合わせる。"""
    out: dict[str, dict] = {}
    for r in rows:
        if len(r) <= COL_FEMALE:
            continue
        name = (r[COL_NAME] or "").strip()
        # 学校名が空の行は節の合計。「計」だけの行も混ざるので弾く。
        if not name or "計" in name or not name.startswith(("府立", "市立")):
            continue
        m, f = num(r[COL_MALE]), num(r[COL_FEMALE])
        if m is None or f is None:
            continue
        e = out.setdefault(key(name), {"name": name, "male": 0, "female": 0})
        e["male"] += m
        e["female"] += f
    # 在籍0の学校は比を出せない
    return {k: v for k, v in out.items() if v["male"] + v["female"] > 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    label = url = None
    rows = None
    for lb, u in CANDIDATES:
        try:
            rows = read_xlsx(get(u))
            label, url = lb, u
            print(f"{lb}: {u}")
            break
        except Exception as e:  # noqa: BLE001
            print(f"  {lb} は取得できず（{e}）")
    if rows is None:
        print("在籍者数の Excel を取得できませんでした。CANDIDATES を更新してください。")
        return 1

    table = parse(rows)
    print(f"{len(table)} 校ぶんの男女別在籍者数を読み取りました\n")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    by_key = {key(s["name"]): s for s in doc["schools"]}
    today = dt.date.today().isoformat()

    matched = 0
    missing = []
    for k, v in table.items():
        s = by_key.get(k)
        if not s:
            missing.append(v["name"])
            continue
        total = v["male"] + v["female"]
        male = round(v["male"] * 100 / total)
        if args.apply:
            s["genderRatio"] = {"male": male, "female": 100 - male}
            s["genderRatioDetail"] = {
                "male": v["male"], "female": v["female"], "total": total, "year": label,
            }
            s["genderRatioSource"] = f"大阪府 学校別在籍者数（{label}）"
            s["genderRatioSourceUrl"] = SOURCE_PAGE
            s["genderRatioFetchedAt"] = today
        matched += 1

    print(f"schools.json と照合：一致 {matched} / 未登録 {len(missing)}")
    if missing:
        print("  schools.json に無い学校:")
        for n in missing[:20]:
            print("    - " + n)

    priv = sum(1 for s in doc["schools"] if s["type"] == "private")
    print(f"\n私立 {priv} 校はこのファイルに含まれないため未取得のまま")

    if args.apply:
        write_json(SCHOOLS, doc)
        print("\nschools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("\n--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

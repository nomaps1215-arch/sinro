#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大阪府が公表している入学者選抜の志願者数から、前年度の定員割れを判定する。

    python tools/fetch_capacity.py            # 取得して結果を表示するだけ
    python tools/fetch_capacity.py --apply    # data/schools.json に反映

一次ソース:
    https://www.pref.osaka.lg.jp/o180040/kotogakko/gakuji-g3/r08_shigansha.html
    「一般入学者選抜（全日制の課程）の志願者数」の最終締切数（Excel）

Excel には学校ごとに 募集人員(A) と 学校全体の志願者数(B)、競争率(B/A) が入っている。
B < A なら定員割れ。府が出している実数なので、推定ではない。

私立高校にはこれに相当する公表データが無いため、判定できない（バッジは付かない）。

外部ライブラリは使わない。xlsx は zip + XML なので標準ライブラリだけで読める。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json, write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
UA = "koukou-search/1.0 (personal study tool)"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# 年度ごとの志願者数ページ。新しい順に試して、最初に読めたものを使う。
YEAR_PAGES = [
    ("令和8年度", "https://www.pref.osaka.lg.jp/o180040/kotogakko/gakuji-g3/r08_shigansha.html"),
    ("令和7年度", "https://www.pref.osaka.lg.jp/o180040/kotogakko/gakuji-g3/r07_shigansya.html"),
    ("令和6年度", "https://www.pref.osaka.lg.jp/o180040/kotogakko/gakuji-g3/r06_shigansya.html"),
]
RE_XLSX = re.compile(r'href="([^"]*ippan[_a-z]*sigansya[_0-9]*\.xlsx)"', re.I)
# 校名に付いているふりがな（末尾のカタカナ）を落とす。「桜宮サクラノミヤ」→「桜宮」
RE_RUBY = re.compile(r"[ァ-ヶー]+$")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def find_latest_xlsx() -> tuple[str, str]:
    for label, page in YEAR_PAGES:
        try:
            html = get(page).decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"  {label}: ページを開けません（{e}）")
            continue
        hits = RE_XLSX.findall(html)
        if not hits:
            print(f"  {label}: 志願者数の Excel が見つかりません")
            continue
        # 締切日が後のもの（ファイル名の数字が大きいもの）が最終値
        hits.sort()
        url = urllib.parse.urljoin(page, hits[-1])
        print(f"  {label}: {url}")
        return label, url
    raise SystemExit("志願者数の Excel を見つけられませんでした。YEAR_PAGES を更新してください。")


RE_COL = re.compile(r"^([A-Z]+)")


def col_index(ref: str) -> int:
    """セル参照 "E7" → 列番号 4（0起点）。"""
    m = RE_COL.match(ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(raw: bytes) -> list[list[str]]:
    """xlsx を「列位置を保ったまま」読む。

    xlsx は空セルの <c> 要素そのものを省略するので、出てきた順に詰めると
    列がずれる。セル参照 r="E7" から本来の列番号を求めて配置する。
    """
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))

    def value(c):
        t = c.get("t")
        v = c.find(f"{NS}v")
        if t == "s":
            return shared[int(v.text)] if v is not None else ""
        if t == "inlineStr":
            return "".join(x.text or "" for x in c.iter(f"{NS}t"))
        return v.text if v is not None else ""

    rows: list[list[str]] = []
    for name in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n)):
        root = ET.fromstring(z.read(name))
        for r in root.iter(f"{NS}row"):
            cells = r.findall(f"{NS}c")
            if not cells:
                rows.append([])
                continue
            width = max(col_index(c.get("r")) for c in cells) + 1
            row = [""] * width
            for c in cells:
                row[col_index(c.get("r"))] = value(c)
            rows.append(row)
    return rows


def num(s: str):
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse(rows: list[list[str]]) -> dict[str, dict]:
    """学校ごとに 募集人員の合計と、学校全体の志願者数を取り出す。

    表は「１ 普通科」「３ 文理探究科」のように節に分かれていて、節の終わりに
    「合計」行が入る。学校名の欄が空の行は前の学校の続き（第2学科）なので、
    直前の学校に足していくが、合計行や節見出しでは対象を切らないと
    その学校に全体の合計が乗ってしまう。
    """
    out: dict[str, dict] = {}
    cur = None
    for r in rows:
        if len(r) < 11:
            cur = None
            continue
        first = (r[0] or "").strip()
        name = (r[1] or "").strip()
        if first and not first.endswith("立"):
            # 「合計」「３ 全日制の課程…」「高等学校名」など。学校の並びが切れる。
            cur = None
            continue
        if first.endswith("立") and name:
            cur = RE_RUBY.sub("", name).strip()
            if not cur:
                continue
            out.setdefault(cur, {"founder": first, "capacity": 0.0, "applicants": None})
        if cur is None:
            continue
        a = num(r[4])            # 募集人員（Ａ）
        b = num(r[9])            # 学校全体の志願者数（Ｂ）
        if a:
            out[cur]["capacity"] += a
        if b and out[cur]["applicants"] is None:
            out[cur]["applicants"] = b
    return {k: v for k, v in out.items()
            if v["capacity"] > 0 and v["applicants"] is not None}


def school_key(name: str) -> str:
    s = name.replace("大阪府立", "").replace("大阪市立", "").replace("府立", "").replace("市立", "")
    s = s.replace("高等学校", "").replace(" ", "").replace("　", "")
    return s.replace("ヶ", "ケ").replace("が", "ケ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    print("志願者数の Excel を探しています ...")
    label, url = find_latest_xlsx()
    stats = parse(read_xlsx(get(url)))
    print(f"\n{label}：{len(stats)} 校分の募集人員と志願者数を読み取りました")

    under = {k: v for k, v in stats.items() if v["applicants"] < v["capacity"]}
    print(f"うち定員割れ {len(under)} 校\n")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    by_key = {school_key(s["name"]): s for s in doc["schools"]}

    matched = 0
    missing_names = []
    for name, v in stats.items():
        s = by_key.get(school_key(name))
        if not s:
            missing_names.append(f"{v['founder']}{name}")
            continue
        matched += 1
        ratio = round(v["applicants"] / v["capacity"], 2)
        if args.apply:
            s["lastYearCapacity"] = int(v["capacity"])
            s["lastYearApplicants"] = int(v["applicants"])
            s["lastYearRatio"] = ratio
            s["lastYearUnderCapacity"] = v["applicants"] < v["capacity"]
            s["lastYearLabel"] = label
            if s["lastYearUnderCapacity"]:
                s["lastYearUnderCapacityNote"] = (
                    f"{label}の一般入学者選抜で、志願者数 {int(v['applicants'])}人が"
                    f"募集人員 {int(v['capacity'])}人に届きませんでした（倍率 {ratio}）。"
                )

    print(f"schools.json と照合：一致 {matched} / 未登録 {len(missing_names)}")
    if missing_names:
        print("  府の一覧に載らない市立高校など、schools.json に無い学校:")
        for n in missing_names:
            print("    - " + n)
    print("\n定員割れだった学校（倍率の低い順）:")
    for name, v in sorted(under.items(), key=lambda kv: kv[1]["applicants"] / kv[1]["capacity"]):
        mark = " " if school_key(name) in by_key else "×"
        print(f"  {mark} {name:<12} {int(v['applicants']):>4}人 / 募集 {int(v['capacity']):>4}人"
              f"  倍率 {v['applicants'] / v['capacity']:.2f}")

    if args.apply:
        write_json(SCHOOLS, doc)
        print("\nschools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("\n--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""入学金（入学料）を登録する。

    python tools/set_admission_fee.py            # 変更内容を表示するだけ
    python tools/set_admission_fee.py --apply    # data/schools.json に反映

■ 公立
大阪府立高等学校の入学料は課程ごとに一律で決まっている。学校ごとに違わないので、
課程から機械的に入れられる。出典は大阪府の「府立高等学校の授業料と就学支援金について」。
    https://www.pref.osaka.lg.jp/o180140/kyoishisetsu/furitukoukou/index.html
    全日制 5,650円 ／ 定時制 2,100円

■ 私立
学校ごとに違い、**学校別の額をまとめた公的な一覧は存在しない**。
大阪府私学課も学校別の額は公表しておらず、各校の募集要項（多くはPDF）にしかない。
そのため私立は null のままにして、画面には「募集要項で確認」と出し、
公式サイトへ誘導する。推測で額を入れない。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"

SOURCE = "大阪府 府立高等学校の授業料と就学支援金について"
SOURCE_URL = "https://www.pref.osaka.lg.jp/o180140/kyoishisetsu/furitukoukou/index.html"
# 課程ごとの入学料（円）
PUBLIC_FEE = {"全日制": 5650, "定時制・通信制": 2100}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    filled = skipped = 0

    for s in doc["schools"]:
        if s["type"] != "public":
            skipped += 1
            continue
        fee = PUBLIC_FEE.get(s.get("division") or "全日制")
        if fee is None:
            skipped += 1
            continue
        if args.apply:
            s["admissionFee"] = {
                "amount": fee,
                "note": (s.get("division") or "全日制") + "の入学料。大阪府立高校は一律。",
                "source": SOURCE,
                "sourceUrl": SOURCE_URL,
            }
        filled += 1

    print(f"公立 {filled} 校に入学料を登録")
    print(f"私立など {skipped} 校は学校ごとに異なり公的な一覧が無いため未登録のまま")
    for div, fee in PUBLIC_FEE.items():
        print(f"   {div}: {fee:,}円")

    if args.apply:
        write_json(SCHOOLS, doc)
        print("\nschools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("\n--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

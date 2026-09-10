#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""data/*.json をまとめて data/bundle.js を生成する。

index.html をローカルファイル（file://）のまま開いても動くようにするための仕組み。
ブラウザは file:// から fetch() で JSON を読めないので、<script> で読める形に変換する。

    python tools/build_bundle.py
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "bundle.js"
INDEX = ROOT / "index.html"

# index.html の <script src="data/bundle.js?v=...">
RE_BUNDLE_REF = re.compile(r"(data/bundle\.js\?v=)([^\"']*)")


def main() -> None:
    payload = {
        "lines": json.loads((DATA / "lines.json").read_text(encoding="utf-8")),
        "schools": json.loads((DATA / "schools.json").read_text(encoding="utf-8")),
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(
        "/* 自動生成ファイル。編集しないこと。\n"
        "   data/*.json を直したら python tools/build_bundle.py を実行して再生成する。 */\n"
        "window.HS_DATA = " + body + ";\n",
        encoding="utf-8",
    )
    # データが変わったら ?v= を中身のハッシュに差し替える。
    # 12時間ごとの自動更新をブラウザのキャッシュが握りつぶさないようにするため、
    # 手で番号を上げなくても新しいデータが届くようにしておく。
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    html = INDEX.read_text(encoding="utf-8")
    new_html, n = RE_BUNDLE_REF.subn(lambda m: m.group(1) + digest, html)
    if n and new_html != html:
        INDEX.write_text(new_html, encoding="utf-8")
        print(f"index.html の data/bundle.js?v= を {digest} にしました")

    n_lines = len(payload["lines"]["lines"])
    n_st = sum(len(l["stations"]) for l in payload["lines"]["lines"])
    n_sc = len(payload["schools"]["schools"])
    print(f"生成: {OUT.relative_to(ROOT)}  路線{n_lines} / 駅{n_st} / 高校{n_sc}")


if __name__ == "__main__":
    main()

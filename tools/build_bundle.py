#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""data/*.json をまとめて data/bundle.js を生成する。

index.html をローカルファイル（file://）のまま開いても動くようにするための仕組み。
ブラウザは file:// から fetch() で JSON を読めないので、<script> で読める形に変換する。

    python tools/build_bundle.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "bundle.js"


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
    n_lines = len(payload["lines"]["lines"])
    n_st = sum(len(l["stations"]) for l in payload["lines"]["lines"])
    n_sc = len(payload["schools"]["schools"])
    print(f"生成: {OUT.relative_to(ROOT)}  路線{n_lines} / 駅{n_st} / 高校{n_sc}")


if __name__ == "__main__":
    main()

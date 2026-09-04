#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""index.html と CSS・JS・データを1枚のHTMLにまとめる。

「1ファイルしか置けない場所」で配布するための形。アプリの中身は変えず、
外部ファイルの参照をその場に埋め込むだけ。

    python tools/build_webapp.py
      -> dist/sinro.html             Artifact 用（<head> を持たない断片）
      -> dist/sinro-standalone.html  単体で開ける完全なHTML

Artifact は公開時に <!DOCTYPE> と <head>（charset を含む）を付けるので、
そちら向けの出力にはこれらを入れない。ただし断片のままローカルで開くと
文字コードが判定できず日本語が化けるため、単体版も一緒に出す。
GitHub Pages などに自分で置く場合は単体版を使う。

Google Fonts だけは外部参照のまま残す（Artifact の CSP で許可されている）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "sinro.html"
STANDALONE = ROOT / "dist" / "sinro-standalone.html"

SCRIPTS = ["js/transit.js", "js/uniform-art.js", "data/bundle.js", "js/app.js"]

RE_BODY = re.compile(r"<body[^>]*>(.*)</body>", re.S | re.I)
RE_SCRIPT_TAG = re.compile(r"<script\b[^>]*></script>\s*", re.I)
RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
RE_FONT_LINK = re.compile(r'<link[^>]+href="(https://fonts\.googleapis\.com/[^"]+)"[^>]*>', re.I)


def main() -> int:
    html = (ROOT / "index.html").read_text(encoding="utf-8")

    title = RE_TITLE.search(html)
    title = title.group(1).strip() if title else "SINRO"

    fonts = RE_FONT_LINK.search(html)
    font_import = f"@import url('{fonts.group(1)}');\n" if fonts else ""

    body = RE_BODY.search(html)
    if not body:
        print("index.html の <body> を読み取れませんでした")
        return 1
    body_html = RE_SCRIPT_TAG.sub("", body.group(1)).strip()

    css = (ROOT / "css" / "style.css").read_text(encoding="utf-8")

    parts = [
        f"<title>{title}</title>",
        "<style>\n" + font_import + css + "\n</style>",
        body_html,
    ]
    for rel in SCRIPTS:
        src = (ROOT / rel).read_text(encoding="utf-8")
        parts.append(f"<!-- {rel} -->\n<script>\n{src}\n</script>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fragment = "\n\n".join(parts) + "\n"
    write_text(OUT, fragment)

    standalone = (
        "<!DOCTYPE html>\n<html lang=\"ja\">\n<head>\n"
        "<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        "<meta name=\"theme-color\" content=\"#1b1a17\">\n"
        f"</head>\n<body>\n{fragment}</body>\n</html>\n"
    )
    write_text(STANDALONE, standalone)

    for f in (OUT, STANDALONE):
        print(f"生成: {f.relative_to(ROOT)}  {f.stat().st_size / 1024:.0f}KB")
    print(f"タイトル: {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

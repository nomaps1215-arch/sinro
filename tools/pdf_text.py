# -*- coding: utf-8 -*-
"""PDF から文字を座標つきで取り出す、必要最小限の実装。

外部ライブラリを増やさない方針なので、標準ライブラリだけで書いている。
汎用のPDFライブラリではない。**表になっているPDFを読むためだけのもの**で、
次の前提を置いている。

  - 本文の stream が Flate（zlib）で圧縮されている
  - 文字が Type0（CID）フォントで、/ToUnicode CMap が付いている
  - 文字の位置が Tm 行列で指定されている（TD/Td による相対移動は追わない）

この前提を外れるPDFでは、文字が取れないか歯抜けになる。読めなければ
空を返すだけで、それらしい値をでっち上げることはしない。

使いかた:
    from pdf_text import pages_text
    for rows in pages_text(pdf_bytes):        # ページごと
        for y, cells in rows:                 # 上から順の行
            print(y, cells)                   # cells は (x, 文字列) を左から
"""
from __future__ import annotations

import re
import zlib

RE_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
RE_BFCHAR = re.compile(r"beginbfchar(.*?)endbfchar", re.S)
RE_BFRANGE = re.compile(r"beginbfrange(.*?)endbfrange", re.S)
RE_PAIR = re.compile(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
RE_TRIPLE = re.compile(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
RE_HEX = re.compile(r"<([0-9A-Fa-f]+)>")

# フォント切替 / 文字の位置 / 文字そのもの（配列形式と単体形式）
RE_TOKEN = re.compile(
    r"/(?P<font>F\d+)\s+[\d.]+\s+Tf"
    r"|(?P<tm>[\d.-]+\s+[\d.-]+\s+[\d.-]+\s+[\d.-]+\s+(?P<x>[\d.-]+)\s+(?P<y>[\d.-]+)\s+Tm)"
    r"|\[(?P<arr>[^\]]*)\]\s*TJ"
    r"|<(?P<one>[0-9A-Fa-f]+)>\s*Tj"
)

ROW_TOLERANCE = 4.0   # この範囲に収まる y は同じ行とみなす


def _utf16_chars(hexstr: str) -> str:
    """ToUnicode の値。UTF-16BE で、1文字が2桁×2以上のことがある。"""
    try:
        return bytes.fromhex(hexstr).decode("utf-16-be", "ignore")
    except ValueError:
        return ""


def parse_cmap(data: bytes) -> dict[int, str]:
    """/ToUnicode CMap を「文字コード -> 文字」の辞書にする。"""
    text = data.decode("latin-1")
    table: dict[int, str] = {}
    for block in RE_BFCHAR.findall(text):
        for src, dst in RE_PAIR.findall(block):
            table[int(src, 16)] = _utf16_chars(dst)
    for block in RE_BFRANGE.findall(text):
        for lo, hi, dst in RE_TRIPLE.findall(block):
            base = int(dst, 16)
            for step, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                table[code] = chr(base + step)
    return table


def _streams(pdf: bytes) -> list[bytes]:
    """stream を取り出す。圧縮されているものは展開する。"""
    out = []
    for raw in RE_STREAM.findall(pdf):
        body = raw.strip(b"\r\n")
        try:
            out.append(zlib.decompress(body))
        except zlib.error:
            out.append(raw)      # CMap は非圧縮で入っていることが多い
    return out


def pages_text(pdf: bytes) -> list[list[tuple[float, list[tuple[float, str]]]]]:
    """ページごとに、行（y座標順）と、その行の文字列（x座標順）を返す。"""
    streams = _streams(pdf)

    cmaps: list[dict[int, str]] = []
    contents: list[bytes] = []
    for s in streams:
        if b"beginbfchar" in s or b"beginbfrange" in s:
            cmaps.append(parse_cmap(s))
        elif b" Tf" in s and (b"TJ" in s or b"Tj" in s):
            contents.append(s)
    if not cmaps:
        return []

    # フォント名は F1, F2, ... の順に現れる。CMap も同じ順に並んでいる前提。
    by_font = {f"F{i + 1}": c for i, c in enumerate(cmaps)}

    pages = []
    for content in contents:
        text = content.decode("latin-1")
        font, x, y = "F1", 0.0, 0.0
        items: list[tuple[float, float, str]] = []
        for m in RE_TOKEN.finditer(text):
            if m.group("font"):
                font = m.group("font")
                continue
            if m.group("tm"):
                x, y = float(m.group("x")), float(m.group("y"))
                continue
            source = m.group("arr") if m.group("arr") is not None else "<" + m.group("one") + ">"
            table = by_font.get(font) or cmaps[0]
            chars = []
            for chunk in RE_HEX.findall(source):
                for i in range(0, len(chunk), 4):
                    chars.append(table.get(int(chunk[i:i + 4], 16), ""))
            s = "".join(chars)
            if s.strip():
                items.append((y, x, s))

        rows: list[tuple[float, list[tuple[float, str]]]] = []
        for yy, xx, s in sorted(items):
            if rows and abs(rows[-1][0] - yy) <= ROW_TOLERANCE:
                rows[-1][1].append((xx, s))
            else:
                rows.append((yy, [(xx, s)]))
        for _, cells in rows:
            cells.sort()
        pages.append(rows)
    return pages

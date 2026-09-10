#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""私立高校の入学金を、大阪私立中学校高等学校連合会の公表PDFから取り込む。

    python tools/fetch_private_fee_pdf.py            # 読み取ってレポートを出すだけ
    python tools/fetch_private_fee_pdf.py --apply    # data/schools.json に反映

■ 出どころ
連合会が毎年11月に「私立高等学校新入生徒 納付金等調」を公表している。
学校別に入学金等・授業料等・合計が載った一次資料。掲載ページは年度ごとに
変わるので、ニュース一覧から今年度のPDFを探して使う。

    https://www.osaka-shigaku.gr.jp/news/

■ 各校サイトを巡回しないのはなぜか
以前は募集要項を巡回して拾おうとしていた（tools/fetch_private_fee.py）。
PDFや画像で書かれている学校が多く、確実に読めない。この一覧は同じ団体が
同じ様式でまとめた公表値なので、こちらの方が確かで、相手サーバーにも優しい。

■ 埋めないもの
PDFに載っていない学校（通信制・単位制は別のPDF、府外校など）は null のまま。
平均額で埋めることはしない。学校ごとに十数万円違う。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf_text import pages_text  # noqa: E402
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
REPORT = ROOT / "tools" / "private_fee_report.json"

NEWS = "https://www.osaka-shigaku.gr.jp/news/"
UA = "koukou-search/1.0 (personal study tool)"
TIMEOUT = 30

# 高等学校ぶんのPDF。_j は中学校、_tsushin は通信制・単位制なので使わない。
RE_PDF = re.compile(r'href=["\']([^"\']*noufu_h\.pdf)["\']', re.I)
# 通信制・単位制は別のPDF。入学金だけは同じように公表されている。
RE_PDF_TSUSHIN = re.compile(r"""href=["']([^"']*noufu_tsushin\.pdf)["']""", re.I)
# 通信制の表で金額が入っている列のx座標。備考欄にも金額が出てくるので、
# 列の位置で切らないと「教育関連諸費45,000円」まで拾ってしまう。
TSUSHIN_X = (180.0, 265.0)
# 「320,000591,000911,000」のようにセルがくっついて出ることがあるので、
# 3桁区切りの形に厳密に合わせて切り出す。
RE_MONEY = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+")
RE_SURVEY = re.compile(r"(令和\s*[0-9０-９]{1,2}\s*年\s*[0-9０-９]{1,2}\s*月)\s*調")

# 校名を照合するための正規化。連合会の表は略称、schools.json は正式名。
DROP = re.compile(r"高等学校|高校|中学校|学校法人|\s|　")
OLD_NEW = str.maketrans({
    "國": "国", "學": "学", "藝": "芸", "眞": "真", "濵": "浜", "曾": "曽",
    "德": "徳", "澤": "沢", "齋": "斎", "嶋": "島", "栁": "柳", "髙": "高",
})


# 全角英数を半角に（「ＰＬ学園」→「PL学園」）
ZEN2HAN = str.maketrans({chr(0xFF01 + i): chr(0x21 + i) for i in range(94)})


def norm(name: str) -> str:
    return DROP.sub("", name).translate(OLD_NEW).translate(ZEN2HAN)


def aliases(name: str) -> list[str]:
    """照合に使う名前の候補。「上宮・上宮学園高等学校」は「上宮」でも引けるようにする。"""
    base = norm(name)
    out = [base]
    if "・" in base:
        out += [p for p in base.split("・") if p]
    return out


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return res.read()


def find_pdf_url(html: str) -> str:
    m = RE_PDF.search(html)
    if not m:
        raise RuntimeError(f"{NEWS} に noufu_h.pdf のリンクが見つかりません")
    return urllib.parse.urljoin(NEWS, m.group(1))


def parse_tsushin(pdf: bytes) -> list[dict]:
    """通信制・単位制の表から (校名, 入学金) を拾う。

    授業料は1単位あたりの額なので、全日制と並べられない。入学金だけ採る。
    """
    out = []
    for rows in pages_text(pdf):
        for _, cells in rows:
            money = []
            for x, s in cells:
                if not (TSUSHIN_X[0] <= x <= TSUSHIN_X[1]):
                    continue
                if RE_MONEY.sub("", s).strip():
                    continue          # 金額以外が混じるセルは列がずれている
                money += [int(v.replace(",", "")) for v in RE_MONEY.findall(s)]
            if not money:
                continue
            name = re.sub(r"[※＊*\s　]+$", "", cells[0][1]).strip()
            if not name or RE_MONEY.search(name):
                continue
            out.append({"name": name, "entry": money[0], "tuition": None, "total": None})
    return out


def parse_rows(pdf: bytes) -> tuple[list[dict], str | None]:
    """PDFの表から (校名, 入学金, 授業料, 合計) を拾う。"""
    out: list[dict] = []
    survey = None
    for rows in pages_text(pdf):
        for _, cells in rows:
            line = "".join(s for _, s in cells)
            if survey is None:
                m = RE_SURVEY.search(line)
                if m:
                    survey = m.group(1).replace(" ", "")

            # 1行に金額が3つ（入学金・授業料・合計）並んでいる行だけを表の行とみなす。
            # セルが1つにくっついて出ることがあるので、行全体から数字を拾う。
            money = [int(x.replace(",", "")) for x in RE_MONEY.findall(line)]
            money = [v for v in money if 10_000 <= v <= 3_000_000]
            if len(money) < 3:
                continue
            name = RE_MONEY.split(cells[0][1])[0].strip() if cells else ""
            if not name:
                continue
            name = re.sub(r"[※＊*\d\s　]+$", "", name)
            entry, tuition, total = money[0], money[1], money[2]
            # 合計が合わない行は表の読み違いなので採らない
            if entry + tuition != total:
                continue
            out.append({"name": name, "entry": entry, "tuition": tuition, "total": total})
    return out, survey


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="data/schools.json に反映する")
    args = ap.parse_args()

    html = http_get(NEWS).decode("utf-8", "replace")
    url = find_pdf_url(html)
    print(f"全日制のPDF: {url}")
    rows, survey = parse_rows(http_get(url))
    print(f"  {len(rows)} 校" + (f"（{survey}調べ）" if survey else ""))
    if not rows:
        print("読み取れませんでした。PDFの作りが変わった可能性があります。")
        return 1

    # 通信制・単位制は別のPDF。全日制で採れた学校には上書きしない。
    url_t, rows_t = None, []
    m = RE_PDF_TSUSHIN.search(html)
    if m:
        url_t = urllib.parse.urljoin(NEWS, m.group(1))
        print(f"通信制のPDF: {url_t}")
        rows_t = parse_tsushin(http_get(url_t))
        print(f"  {len(rows_t)} 校（入学金のみ）")

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    index: dict[str, list[dict]] = {}
    for s in doc["schools"]:
        if s.get("type") == "private":
            for a in aliases(s["name"]):
                index.setdefault(a, []).append(s)

    matched, ambiguous, unmatched = 0, [], []
    today = dt.date.today().isoformat()
    done: set[int] = set()
    for r in [dict(x, _src=url) for x in rows] + [dict(x, _src=url_t) for x in rows_t]:
        key = norm(r["name"])
        hits = index.get(key)
        if not hits:
            # 表の略称が正式名の一部になっている場合（「清風南海」→「清風南海高等学校」）
            hits = [s for k, v in index.items() if k.startswith(key) or key.startswith(k) for s in v]
            hits = list({id(s): s for s in hits}.values())
        if not hits:
            unmatched.append(r["name"])
            continue
        if len(hits) > 1:
            ambiguous.append((r["name"], [s["name"] for s in hits]))
            continue
        target = hits[0]
        if id(target) in done:
            continue          # 全日制で採れている学校は通信制の額で上書きしない
        done.add(id(target))
        matched += 1
        if args.apply:
            fee = {
                "amount": r["entry"],
                "note": "入学金等（施設費などを含む場合がある）",
                "source": r["_src"],
                "sourceName": "大阪私立中学校高等学校連合会「私立高等学校新入生徒 納付金等調」",
                "survey": survey,
                "fetchedAt": today,
            }
            if r["tuition"] is not None:
                fee["tuitionPerYear"] = r["tuition"]
            target["admissionFee"] = fee

    write_json(REPORT, {"url": url, "urlTsushin": url_t, "survey": survey,
                        "rows": rows, "rowsTsushin": rows_t,
                        "unmatched": unmatched, "ambiguous": ambiguous})
    print(f"schools.json と結びついた学校 {matched} 校")
    if ambiguous:
        print(f"同名候補が複数（未反映）{len(ambiguous)} 件: {[a[0] for a in ambiguous][:8]}")
    if unmatched:
        print(f"照合できなかった校名 {len(unmatched)} 件: {unmatched[:12]}")
    print(f"レポート: {REPORT.relative_to(ROOT)}")

    if args.apply:
        write_json(SCHOOLS, doc)
        print("schools.json を更新しました。")
        print("-> 続けて python tools/build_bundle.py を実行してください。")
    else:
        print("--apply を付けると schools.json に反映します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

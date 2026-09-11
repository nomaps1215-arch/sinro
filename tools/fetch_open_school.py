#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""各高校の公式サイトから、オープンスクール・学校説明会の日程と申込先を拾う。

    python tools/fetch_open_school.py             # 巡回してレポートを出すだけ
    python tools/fetch_open_school.py --apply     # data/schools.json に反映
    python tools/fetch_open_school.py --only pref-mikunigaoka
    python tools/fetch_open_school.py --limit 10  # 先頭10校だけ（動作確認用）

■ このデータについて
オープンスクールの日程に公的な一覧は無い。各校サイトの「入試情報」「中学生のみなさんへ」
あたりに書いてあるだけなので、拾えるかどうかはサイトの作り次第。
拾えなかった学校は openSchool を持たせない（画面には何も出ない）。推測で埋めない。

■ 日付を信用する条件（誤読を出さないための線引き）
受験生が予定を組むのに使う情報なので、少しでも怪しい日付は捨てる。

  - 曜日の記載があり、計算した曜日と一致する           → 採用
  - 曜日の記載があるが一致しない                       → 捨てる（書式の読み違い）
  - 曜日が無いが「令和8年」「2026年」と年が書いてある  → 採用
  - 曜日も年も無い（「10月11日」だけ）                 → 捨てる

■ 申込の要否
日付の前後にある「要予約」「事前申込」「申込不要」などの語から判定する。
どちらとも取れる場合は null（＝不明）にして、画面には「公式サイトで確認」と出す。

■ 消えた情報の扱い
巡回できた学校は openSchool を毎回まるごと作り直す。日程が終わったりページから
消えたりしたものは自然に消える。逆に**サイトへ到達できなかった学校は前回の値を残す**。
一時的な通信エラーで全校のデータが吹き飛ぶのを防ぐため。

外部ライブラリ不要（標準ライブラリのみ）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "data" / "schools.json"
REPORT = ROOT / "tools" / "open_school_report.json"

UA = "koukou-search/1.0 (personal study tool; respects robots.txt)"
SLEEP_SEC = 2.0
TIMEOUT = 20
MAX_PAGES = 4          # トップ＋辿る3ページ
MAX_EVENTS = 6         # 1校あたりカードに出す上限
WINDOW = 70            # 行事名と日付が同じ話題だとみなす文字数

# ---- 行事の呼び名 -------------------------------------------------------
# 長いものを先に置く（「学校説明会」を「説明会」より先に拾わせる）
EVENT_WORDS = [
    "オープンスクール", "オープンキャンパス", "オープンハイスクール",
    "学校説明会", "入試説明会", "進学説明会", "入学説明会",
    "体験入学", "入学体験", "授業体験", "クラブ体験", "部活動体験",
    "学校見学会", "見学説明会", "個別相談会", "入試相談会",
    "プレテスト", "オープンデー", "説明会",
]
RE_EVENT = re.compile("|".join(re.escape(w) for w in EVENT_WORDS))

# ---- 日付 ---------------------------------------------------------------
RE_DATE = re.compile(
    r"(?:"
    # 「2026.09.20」「2026/9/20」形式。年が入っているので区切りが点でも取り違えない。
    r"(?P<fy>20[0-9]{2})\s*[.．/／-]\s*(?P<fm>[0-9０-９]{1,2})\s*[.．/／-]\s*(?P<fd>[0-9０-９]{1,2})"
    r"|"
    # 「令和8年10月11日」「10月11日」「10/11」形式
    r"(?:令和\s*(?P<r>[0-9０-９]{1,2})\s*年度?\s*)?"
    r"(?:(?P<y>20[0-9]{2})\s*年\s*)?"
    r"(?P<m>[0-9０-９]{1,2})\s*[月/／]\s*(?P<d>[0-9０-９]{1,2})\s*日?"
    r")"
    r"(?:\s*[（(]\s*(?P<w>[月火水木金土日])\s*[)）])?"
)


def date_parts(m: re.Match):
    """RE_DATE のどちらの書き方でマッチしたかを吸収して (月, 日, 年) を返す。"""
    if m.group("fy"):
        return m.group("fm"), m.group("fd"), m.group("fy")
    return m.group("m"), m.group("d"), m.group("y")
WEEKDAYS = "月火水木金土日"  # datetime.weekday() は月曜=0
# 「10/24(土)、11/7(土)」のように並記された日付をひとまとまりとして扱うための、
# 日付と日付のあいだに入ってよい文字。これ以外が挟まれば別の話題とみなす。
RE_DATE_GLUE = re.compile(r"^[\s　、,，・/／()（）\[\]&＆と及びおよびまたは~〜ー－-]{0,8}$")
# 「①1月16日（土） 午前の部／10：00～ ②2月13日（土）」のように、番号を振って
# 複数回ぶんの日程を並べる書き方。この印があるあいだは同じ行事の続きとみなす。
RE_ENUM = re.compile(r"[①-⑳❶-❿]|第\s*[0-9０-９一二三四五六七八九十]{1,3}\s*回|[(（][0-9０-９]{1,2}[)）]")
# 説明会ではない学校行事の名前。日付のすぐ隣にこれがあれば、その日付は
# その行事のものなので説明会に結びつけない。狭山高校の入試日程表で
# 「合格発表 令和９年３月１８日（木） 学校説明会 …」の3月18日を
# 説明会の日として拾ってしまっていた。
RE_OTHER_EVENT = re.compile(
    r"合格発表|出願|願書|学力検査|入学試験|入学式|卒業式|始業式|終業式|修了式|"
    r"考査|定期試験|実力テスト|模試|文化祭|体育祭|運動会|球技大会|遠足|代休|"
    r"休業|締切|締め切り|講習|面談|懇談|振替|検定|開始|発表"
)
# 申込を受け付ける「期間」の日付。行事の日ではないので拾わない。
# 山田高校の「第１期申し込み期間：9月1日（月）～ 9月11日（金）16時」を
# 見学会の日として登録してしまっていた。
RE_PERIOD = re.compile(
    r"申込期間|申し込み期間|申込み期間|受付期間|申込期限|受付期限|応募期間|"
    r"エントリー期間|受付開始|申込開始|受付は|期間："
)
PERIOD_BEFORE = 40      # 日付の前のどこまでを「期間の見出し」とみなすか
ADJACENT = 14      # 「すぐ隣」とみなす文字数

# ---- 申込の要否 ---------------------------------------------------------
RE_NEEDED = re.compile(
    r"要予約|要申込|要申し込み|要事前|事前予約|事前申込|事前申し込み|予約制|申込制|"
    r"完全予約|予約が必要|申込が必要|申し込みが必要|お申し込みください|お申込みください|"
    r"申込フォーム|申し込みフォーム|予約フォーム|Web申込|WEB申込"
)
RE_NOT_NEEDED = re.compile(
    r"申込不要|申し込み不要|予約不要|事前申込は不要|事前予約は不要|"
    r"当日参加|当日受付|自由参加|直接お越し|申込は必要ありません"
)

# ---- 申込リンク ---------------------------------------------------------
# 「予約」だけを手がかりにすると「大学予約奨学金のお知らせ」まで拾ってしまうので、
# 申込を意味する語のまとまりで見る。
RE_APPLY_TEXT = re.compile(
    r"申込|申し込|お申込|参加予約|来校予約|見学予約|予約フォーム|予約はこちら|エントリー"
)
# href 側の手がかり。"information" が "form" を含むので、区切り文字で挟んで見る。
RE_APPLY_HREF = re.compile(
    r"(^|[/_.?=-])(forms?|entry|apply|mousikomi|moushikomi|yoyaku|reserve)([/_.?=-]|$)", re.I
)
# 学校が使いがちな外部フォーム。ここに載っている先だけ別ドメインでも許可する。
FORM_HOSTS = re.compile(
    r"(^|\.)(mirai-compass\.(net|jp)|e-shiharai\.net|forms\.gle|docs\.google\.com|"
    r"logoform\.jp|form\.run|kokuchpro\.com|questant\.jp|formzu\.net|form\.os7\.biz|"
    r"shinsei\.pref\.osaka\.lg\.jp|reserva\.be|airrsv\.net|coubic\.com)$", re.I
)
# 「申込」でも入試の出願は別物なので弾く
RE_APPLY_NG = re.compile(
    r"出願|願書|インターネット出願|合否|入学手続|奨学金|卒業生|同窓会|証明書|"
    r"寄付|寄附|求人|採用|保護者会|アンケート|問い合わせ|問合せ|お問合|資料請求", re.I
)

# ---- 辿るリンク ---------------------------------------------------------
RE_INTEREST = re.compile(
    r"オープンスクール|オープンキャンパス|説明会|体験入学|学校見学|見学会|"
    r"中学生|受験生|入学希望|入試情報|入試|受験|イベント|行事予定|"
    r"open|setsumei|taiken|kengaku|nyushi|nyuushi|exam|admission|event", re.I
)

RE_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
RE_ANY_TAG = re.compile(r"<[^>]+>")
RE_META_CHARSET = re.compile(rb"charset=[\"']?\s*([\w\-]+)", re.I)
# href はシングルクォートで書かれているサイトも多い（東大谷は104箇所がそれだった）。
RE_LINK = re.compile(r"""<a[^>]+href=["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)


def zen2han(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def same_site(a: str, b: str) -> bool:
    """www の有無だけの違いは同じサイトとみなす。

    東大谷は www.higashiohtani.ac.jp から higashiohtani.ac.jp へ転送していて、
    厳密にホスト名を比べるとサイト内リンクを1本も辿れなくなる。
    """
    strip = lambda h: h.lower().removeprefix("www.")  # noqa: E731
    return strip(urllib.parse.urlsplit(a).netloc) == strip(urllib.parse.urlsplit(b).netloc)


def as_base(url: str) -> str:
    """相対リンクの基準にできる形へ直す。

    守口東は .../moriguchihigashi/ から .../moriguchihigashi（末尾スラッシュ無し）へ
    転送する。そのまま基準にすると "junior.html" が1階層上の /junior.html になり、
    中学生向けページに辿り着けなくなる。最後の要素に "." が無ければフォルダとみなす。
    """
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    last = path.rsplit("/", 1)[-1]
    if last and "." not in last:
        path += "/"
    return urllib.parse.urlunsplit(parts._replace(path=path))


def http_get(url: str) -> tuple[str, str]:
    """本文と、転送されたあとの最終URLを返す。リンクの解決には最終URLを使う。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        final = res.geturl() or url
        raw = res.read(800_000)
        if res.headers.get("Content-Encoding") == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:  # noqa: BLE001 — 途中で切った gzip は諦める
                return "", final
        enc = None
        m = re.search(r"charset=([\w\-]+)", res.headers.get("Content-Type", ""), re.I)
        if m:
            enc = m.group(1)
        if not enc:
            m2 = RE_META_CHARSET.search(raw[:4096])
            if m2:
                enc = m2.group(1).decode("ascii", "ignore")
    for cand in [enc, "utf-8", "cp932", "euc-jp"]:
        if not cand:
            continue
        try:
            return raw.decode(cand), final
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), final


def robots_allows(url: str, cache: dict) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
        except Exception:  # noqa: BLE001 — robots.txt が無いサイトは許可扱い
            rp = None
        cache[base] = rp
        time.sleep(SLEEP_SEC)
    rp = cache[base]
    return True if rp is None else rp.can_fetch(UA, url)


def to_text(html: str) -> str:
    html = RE_TAG.sub(" ", html)
    html = RE_ANY_TAG.sub(" ", html)
    return re.sub(r"[\s　]+", " ", html)


def interesting_links(base: str, html: str) -> list[str]:
    """入試・説明会まわりのページへのリンクを、確度の高い順に返す。"""
    scored: list[tuple[int, str]] = []
    seen = set()
    for href, label in RE_LINK.findall(html):
        label = RE_ANY_TAG.sub("", label).strip()
        hay = urllib.parse.unquote(href) + " " + label
        if not RE_INTEREST.search(hay):
            continue
        u = urllib.parse.urljoin(base, href.split("#")[0])
        if not u.startswith("http") or u.rstrip("/") == base.rstrip("/"):
            continue
        if not same_site(u, base):
            continue      # 辿るのは同じサイトの中だけ
        if u in seen:
            continue
        seen.add(u)
        # 行事名そのものが書かれているリンクを最優先する
        score = 0 if RE_EVENT.search(hay) else (1 if re.search(r"中学生|受験生|入試", hay) else 2)
        scored.append((score, u))
    scored.sort(key=lambda t: t[0])
    return [u for _, u in scored[: MAX_PAGES - 1]]


def resolve_year(month: int, day: int, weekday: str | None,
                 era: str | None, year: str | None, today: dt.date):
    """年が書かれていない日付の年を決める。曜日が合わなければ捨てる。

    返り値は (date, 確度) か None。
    """
    fixed = None
    if era:
        fixed = 2018 + int(zen2han(era))     # 令和1年 = 2019
    elif year:
        fixed = int(year)

    if not fixed and not weekday:
        # 「10月11日」とだけ書かれていると、今年のことか去年の残骸か区別できない。
        # 予定を組むのに使う情報なので、当てずっぽうで年を決めずに捨てる。
        return None

    candidates = [fixed] if fixed else [today.year, today.year + 1]
    for y in candidates:
        try:
            d = dt.date(y, month, day)
        except ValueError:
            continue
        if weekday and WEEKDAYS[d.weekday()] != weekday:
            continue          # 曜日が合わない年は違う
        if not fixed and d < today - dt.timedelta(days=14):
            continue          # 年の指定が無いなら、過ぎた日付ではなく来年とみなす
        return d, ("high" if weekday else "mid")
    return None


def reservation_of(window: str):
    need = len(RE_NEEDED.findall(window))
    free = len(RE_NOT_NEEDED.findall(window))
    if free and not need:
        return "none"
    if need and not free:
        return "required"
    return None               # 両方あるか、どちらも無い＝分からない


def find_events(text: str, source: str, today: dt.date) -> list[dict]:
    """行事名の近くにある日付だけを拾う。ページ内の他の日付は無視する。

    日付ごとに「いちばん近くにある行事名」を割り当てる。行事名ごとに走査すると、
    同じ日付が「オープンスクール」でも「学校説明会」でも拾われて重複するため。

    年間行事予定表を説明会の日程と読み違えないよう、行事名との間に別の日付が
    挟まっている場合は結びつけない。「9月11日 代休 9月12日 3年模試 … 9月19日
    学校説明会」のような表で、9月11日まで説明会にされるのを防ぐ。
    """
    keywords = [(m.start(), m.end(), m.group(0)) for m in RE_EVENT.finditer(text)]
    if not keywords:
        return []

    # 日付をまとまり（クラスタ）にする。「10/24(土)、11/7(土)」のように区切り文字だけで
    # 並んだ日付は同じ行事の複数回だが、あいだに別の語が入るなら別の話題。
    clusters: list[list] = []
    for m in RE_DATE.finditer(text):
        if clusters and RE_DATE_GLUE.match(text[clusters[-1][-1].end(): m.start()]):
            clusters[-1].append(m)
        else:
            clusters.append([m])

    # 先に「これからの日付」に解決できるものだけ残す。お知らせの投稿日のような
    # 過ぎた日付を残しておくと、次の判定で邪魔になる。
    live = []
    for cluster in clusters:
        dates = []
        for dm in cluster:
            raw_m, raw_d, raw_y = date_parts(dm)
            try:
                month = int(zen2han(raw_m))
                day = int(zen2han(raw_d))
            except (ValueError, TypeError):
                continue
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            got = resolve_year(month, day, dm.group("w"), dm.group("r"), raw_y, today)
            if not got:
                continue
            date, conf = got
            if today <= date <= today + dt.timedelta(days=400):
                dates.append((date, conf))
        if dates:
            live.append({"start": cluster[0].start(), "end": cluster[-1].end(), "dates": dates})

    # 行事名から見て日付が前にあるか後ろにあるかは、ページごとに揃っていることが多い。
    #   「オープンキャンパス 9月20日（日） 個別相談会 10月10日（土）」＝後ろ
    #   「9月19日（土） 学校説明会 9月25日（金） 生徒指導週間」＝前（年間行事予定表）
    # 片側にしか候補が無い行事名でどちらの並びかを数え、迷ったときはその向きに従う。
    dominant = None

    def candidates_for(ks: int, ke: int, check_own: bool = True) -> list:
        out = []
        for i, c in enumerate(live):
            if ks <= c["start"] <= ke:
                dist, side = 0, 0
            elif c["end"] <= ks:
                dist, side = ks - c["end"], -1
            else:
                dist, side = c["start"] - ke, 1
            if dist > WINDOW:
                continue
            lo, hi = (c["end"], ks) if side < 0 else (ke, c["start"])
            if any(lo <= o["start"] < hi for j, o in enumerate(live) if j != i):
                continue
            # 行事名と日付のあいだに別の学校行事の名前があれば、その日付は
            # そちらのものなので使わない。
            if RE_OTHER_EVENT.search(text[lo:hi]):
                continue
            # 「申込期間：9月1日～9月11日」のような受付期間の日付を弾く。
            # 期間の見出しは必ず日付の前に来るので、向きによらず前だけ見る。
            if RE_PERIOD.search(text[max(0, c["start"] - PERIOD_BEFORE): c["start"]]):
                continue
            # その日付自身の見出しが別の行事なら、この行事のものではない。
            # 見出しが日付の前に来るか後ろに来るかはページごとに揃っているので、
            # 多数決で分かった向きの側を見る。
            #   「合格発表 3月18日 学校説明会 …」        見出しは日付の前
            #   「… プレテスト 9月11日(金) 文化祭 …」    見出しは日付の後ろ
            # 向きが分からないページでは、どちらに見出しがあっても捨てる。
            before = text[max(0, c["start"] - ADJACENT): c["start"]]
            after = text[c["end"]: c["end"] + ADJACENT]
            own = [before] if dominant == 1 else [after] if dominant == -1 else [before, after]
            # 並びの向きを数えている最中はこの判定を使わない。
            # 向きが未定のうちは両側を見てしまい、数える材料まで消えてしまう。
            if check_own and any(RE_OTHER_EVENT.search(x) for x in own):
                continue
            out.append((dist, side, c))
        out.sort(key=lambda x: x[0])
        return out

    votes = {-1: 0, 1: 0}
    for ks, ke, _ in keywords:
        cands = candidates_for(ks, ke, check_own=False)
        sides = {c[1] for c in cands}
        if len(cands) == 1 or len(sides) == 1:
            votes[cands[0][1]] = votes.get(cands[0][1], 0) + 1 if cands else 0
    if votes[-1] != votes[1]:
        dominant = -1 if votes[-1] > votes[1] else 1

    by_date: dict[str, tuple[int, dict]] = {}
    for ks, ke, word in keywords:
        # 行事名との間に別の日付が挟まっていたら、行事予定表の行が並んでいるだけなので
        # 結びつけない。「9月11日 代休 9月12日 3年模試 … 9月19日 学校説明会」で
        # 9月11日まで説明会にされるのを防ぐ。
        cands = candidates_for(ks, ke)
        if not cands:
            continue
        # 行事名の前と後ろに同じくらい近い日付があると、どちらの行のものか決められない。
        # ページの並びが分かっていればそれに従い、分からなければ捨てる。
        # 間違った日付を出すくらいなら、何も出さない方がよい。
        if len(cands) >= 2 and cands[0][1] != cands[1][1] and cands[1][0] - cands[0][0] < 8:
            picked = [c for c in cands if c[1] == dominant] if dominant else []
            if not picked:
                continue
            cands = picked
        dist, side, c = cands[0]

        # 番号を振って続けて並べてある日程は、同じ行事の別の回なので一緒に拾う。
        chain = [c]
        idx = live.index(c)
        step = 1 if side >= 0 else -1
        while 0 <= idx + step < len(live) and len(chain) < 5:
            nxt = live[idx + step]
            lo, hi = (live[idx]["end"], nxt["start"]) if step > 0 else (nxt["end"], live[idx]["start"])
            gap = text[lo:hi]
            # 間に別の行事名があれば、そこから先はその行事のもの
            if len(gap) > 60 or RE_EVENT.search(gap) or not RE_ENUM.search(gap):
                break
            chain.append(nxt)
            idx += step

        lo = min(min(x["start"] for x in chain), ks)
        hi = max(max(x["end"] for x in chain), ke)
        window = text[max(0, lo - 30): hi + WINDOW]
        reservation = reservation_of(window)
        for date, conf in [d for x in chain for d in x["dates"]]:
            ev = {
                "date": date.isoformat(),
                "label": word,
                "reservation": reservation,
                "confidence": conf,
                "source": source,
                "evidence": window.strip()[:160],
            }
            cur = by_date.get(ev["date"])
            if cur is None or dist < cur[0]:
                by_date[ev["date"]] = (dist, ev)
    return [e for _, e in by_date.values()]


def find_apply_url(base: str, html: str) -> dict | None:
    """申込フォームらしいリンクを1つ選ぶ。入試の出願ページは除く。"""
    best = None
    for href, label in RE_LINK.findall(html):
        label = re.sub(r"[\s　]+", " ", RE_ANY_TAG.sub("", label)).strip()
        hay = urllib.parse.unquote(href) + " " + label
        if RE_APPLY_NG.search(hay):
            continue
        u = urllib.parse.urljoin(base, href.split("#")[0])
        if not u.startswith("http"):
            continue
        host = urllib.parse.urlsplit(u).netloc
        external_form = bool(FORM_HOSTS.search(host))
        if not same_site(u, base) and not external_form:
            continue
        by_text = bool(RE_APPLY_TEXT.search(hay))
        by_href = bool(RE_APPLY_HREF.search(urllib.parse.urlsplit(u).path + "?" + (
            urllib.parse.urlsplit(u).query or "")))
        if not (external_form or by_text or by_href):
            continue
        # 外部の申込システム > ラベルに「申込」 > URLだけが手がかり
        score = 0 if external_form else (1 if by_text else 2)
        # リンクの文字が申込と関係ない（画像リンクなど）ときは、当てにせず一般名にする
        name = label[:40] if RE_APPLY_TEXT.search(label) else "申込フォーム"
        if best is None or score < best[0]:
            best = (score, {"url": u, "label": name})
    return best[1] if best else None


def crawl(school: dict, robots_cache: dict, today: dt.date, max_pages: int) -> dict:
    """1校ぶん巡回する。reached=False のときは既存データを消さない。"""
    url = school.get("website")
    res = {"id": school["id"], "name": school["name"], "website": url,
           "reached": False, "pages": 0, "events": [], "apply": None, "note": ""}
    if not url:
        res["note"] = "公式サイト未登録"
        return res
    if not robots_allows(url, robots_cache):
        res["note"] = "robots.txt で拒否"
        return res

    try:
        top, final = http_get(url)   # 転送されていたら、以降は転送先を起点にする
        url = as_base(final)
    except Exception as e:  # noqa: BLE001
        res["note"] = f"取得できず（{e}）"
        time.sleep(SLEEP_SEC)
        return res
    time.sleep(SLEEP_SEC)

    res["reached"] = True
    pages = [(url, top)]
    for u in interesting_links(url, top)[: max_pages - 1]:
        if not robots_allows(u, robots_cache):
            continue
        try:
            html, final = http_get(u)
            pages.append((as_base(final), html))
        except Exception:  # noqa: BLE001 — 個別ページの失敗は無視してよい
            pass
        time.sleep(SLEEP_SEC)
    res["pages"] = len(pages)

    events: list[dict] = []
    apply_hit = None
    for page_url, html in pages:
        text = to_text(html)
        hits = find_events(text, page_url, today)
        events += hits
        # 申込リンクは、行事の話が載っているページから優先して拾う
        if hits and apply_hit is None:
            apply_hit = find_apply_url(page_url, html)
    if apply_hit is None and events:
        for page_url, html in pages:
            apply_hit = find_apply_url(page_url, html)
            if apply_hit:
                break

    # 複数ページに同じ日程が載っていることがあるので、日付でまとめる
    merged: dict[str, dict] = {}
    for e in events:
        cur = merged.get(e["date"])
        if cur is None or (cur["confidence"] == "mid" and e["confidence"] == "high"):
            merged[e["date"]] = e
        elif cur.get("reservation") is None and e.get("reservation"):
            cur["reservation"] = e["reservation"]
    out = sorted(merged.values(), key=lambda e: e["date"])
    res["events"] = out[:MAX_EVENTS]
    res["apply"] = apply_hit
    return res


def to_record(res: dict, today: dt.date) -> dict | None:
    """schools.json に入れる形にする。載せるものが無ければ None。"""
    if not res["events"] and not res["apply"]:
        return None
    votes = [e["reservation"] for e in res["events"] if e["reservation"]]
    reservation = None
    if votes and len(set(votes)) == 1:
        reservation = votes[0]
    elif res["apply"] and not votes:
        reservation = "required"      # 申込フォームがあるなら申込は要る
    rec = {
        "events": [
            {k: e[k] for k in ("date", "label", "reservation", "confidence", "source")}
            for e in res["events"]
        ],
        "reservation": reservation,
        "source": res["events"][0]["source"] if res["events"] else res["website"],
        "checkedAt": today.isoformat(),
    }
    if res["apply"]:
        rec["applyUrl"] = res["apply"]["url"]
        rec["applyLabel"] = res["apply"]["label"]
    if res["events"]:
        rec["evidence"] = res["events"][0]["evidence"]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="data/schools.json に反映する")
    ap.add_argument("--only", help="school id を1つだけ指定")
    ap.add_argument("--limit", type=int, help="先頭 N 校だけ巡回する")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    args = ap.parse_args()

    doc = json.loads(SCHOOLS.read_text(encoding="utf-8"))
    schools = doc["schools"]
    if args.only:
        schools = [s for s in schools if s["id"] == args.only]
        if not schools:
            print(f"該当なし: {args.only}")
            return 1
    if args.limit:
        schools = schools[: args.limit]

    today = dt.date.today()
    robots_cache: dict = {}
    report, found, kept, dropped = [], 0, 0, 0

    for i, s in enumerate(schools, 1):
        print(f"[{i}/{len(schools)}] {s['name']}", end=" ", flush=True)
        res = crawl(s, robots_cache, today, args.max_pages)
        report.append(res)

        if not res["reached"]:
            print(res["note"] or "到達できず")
            if s.get("openSchool"):
                kept += 1     # 前回の値はそのまま残す
            continue

        rec = to_record(res, today)
        if rec:
            found += 1
            head = rec["events"][0]["date"] if rec["events"] else "日程なし"
            print(f"→ {len(rec['events'])}件（{head}）" + ("＋申込リンク" if rec.get("applyUrl") else ""))
        else:
            if s.get("openSchool"):
                dropped += 1
            print("記載を見つけられず")

        if args.apply:
            if rec:
                s["openSchool"] = rec
            else:
                s.pop("openSchool", None)

    write_json(REPORT, report)
    print(f"\n日程または申込先が分かった学校 {found} / {len(schools)}")
    if kept:
        print(f"到達できず前回の値を残した学校 {kept} 校")
    if dropped:
        print(f"記載が消えたので削除した学校 {dropped} 校")
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

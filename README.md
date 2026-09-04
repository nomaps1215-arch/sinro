# SINRO - OSAKA HIGH SCHOOL -

大阪の高校進学情報アプリ。正式名称は **SINRO**。

大阪府在住の中学生が、**最寄り駅からの通学時間**と**偏差値**から進学先の高校（公立・私立）を
探すためのアプリ。スマホで見ることを前提にしている。

外部APIもサーバーも使わない静的サイトなので、`index.html` をブラウザで開くだけで動く。

| | |
|---|---|
| 収録校 | **256校**（公立146・私立110） |
| 収録路線 | **51路線 962駅**（大阪府全域） |
| 偏差値 | 221校に登録済み（すべて「想定値」） |
| 定員割れ | 令和8年度入試の公表実数から55校を判定 |

---

## 使い方

```bash
python tools/build_bundle.py
```

を一度実行したあと、`index.html` をブラウザで開く。

ローカルサーバー経由で見たい場合（キャッシュの問題が起きにくい）:

```bash
python -m http.server 8765
```

### 画面の構成

- **検索画面** … 学校名検索、公立/私立の切り替え（公・私のボタン）、現在と目標の偏差値、結果一覧
- **設定画面**（右上の歯車） … 性別、最寄り駅、通学手段と上限時間、課程、並び順

設定は端末に保存されるので、次に開いたときも同じ条件から始まる。

### カードの見かた

- 左端のくさび … 公立 / 私立
- 右上の朱印 … 昨年（令和8年度）の入試で**定員割れ**だった学校
- 「通学ルート」ボタン … 乗換を含む経路の内訳と、他の手段での所要時間
- カードをタップ … 学科別の偏差値と判定、特徴のまとめ、公式サイトと地図

---

## 公開先

<https://nomaps1215-arch.github.io/sinro/>

GitHub Pages（main ブランチの root）で配信している。`git push` すれば数分で反映される。
ビルド作業は要らない。ただし **`data/bundle.js` を作り直して push しないとデータは変わらない**。

```bash
python tools/build_bundle.py      # data/*.json を直したら
git add -A; git commit -m "..."; git push
```

CSS や JS を直したときは `index.html` の `?v=` の数字を1つ増やすこと。
増やし忘れると、一度見た人のブラウザに古いファイルが残る。

スマホでは、ブラウザの共有メニューから「ホーム画面に追加」するとアプリのように起動する。

---

## 別のパソコンで作業を続けるとき

```bash
git clone https://github.com/nomaps1215-arch/sinro.git && cd sinro
```

必要なのは **Python 3.10以上** だけ（外部ライブラリ不要）。Node.js は開発時のテストにのみ使う。

---

## フォルダ構成

```
index.html            画面（検索画面と設定画面）
css/style.css         スタイル
js/transit.js         通学時間の概算エンジン（このファイルだけで完結）
tools/build_webapp.py       1枚のHTMLにまとめる
js/app.js             画面の組み立てと絞り込み
data/lines.json       路線と駅                      ← 自動生成。手で並べ替えない
data/schools.json     高校のデータ                  ← 自動生成＋手直し
data/bundle.js        上の2つを結合した自動生成物    ← 編集しない
dist/                 配布用に1枚にまとめたHTML（自動生成）

tools/safe_write.py         JSONの原子的な書き出し（全ツールが使う）
tools/build_bundle.py       data/*.json → data/bundle.js
tools/import_all_schools.py 公式一覧から全高校を取り込む（名簿の作り直し）
tools/fetch_official_urls.py 公式サイトURLを一覧から取得
tools/fetch_osm_lines.py    OSMの路線リレーションから路線・駅を生成
tools/fetch_osm.py          OSMから学校・駅の座標を取得
tools/fetch_capacity.py     大阪府の志願者数から定員割れを判定
tools/fetch_deviation.py    偏差値の目安を取り込む
tools/update_schools.py     公式サイトを巡回して男女比・制服を取得
tools/find_websites.py      私立の公式URLを候補総当たりで探す
tools/geocode.py            国土地理院APIで住所から座標（補助）
tools/qa_check.py           データの妥当性チェック
```

**データを編集したら必ず `python tools/build_bundle.py` を実行する。**
CSS や JS を直したら `index.html` の `?v=` の数字を1つ増やす（ブラウザのキャッシュ対策）。

---

## データの出どころと信頼度

| 項目 | 出典 | 信頼度 |
|------|------|--------|
| 校名・公式サイト・学科 | [大阪府 公立高校ホームページ一覧](https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/index.html) | 一次ソース。高い |
| 私立の校名・所在地・男女別 | [大阪私立中学校高等学校連合会 加盟校一覧](https://www.osaka-shigaku.gr.jp/school/index.html) | 一次ソース。高い |
| 定員割れ | [大阪府 入学者選抜の志願者数](https://www.pref.osaka.lg.jp/o180040/kotogakko/gakuji-g3/r08_shigansha.html) | 公表実数。高い |
| 駅・高校の座標 | OpenStreetMap（© OpenStreetMap contributors, ODbL） | 良好。3校のみ住所からの推定 |
| **偏差値** | **みんなの高校情報の掲載値** | **模試結果からの推定。公式発表ではない** |
| 男女比 | 公式サイトからの自動取得 | 掲載がある学校のみ。大半は未取得 |

### 偏差値について（重要）

**高校の偏差値に公的なデータは存在しない。** 民間の模試会社がそれぞれ独自に算出しているもので、
模試が違えば3〜5はずれる。このアプリの数値は「だいたいこのあたり」を掴むためのもので、
出願の判断に使える精度はない。だから画面には必ず「想定値」と添えて出している。

**この偏差値データは個人利用の前提で取り込んでいる。再配布や商用利用はしないこと。**
GitHub Pages などで公開する場合は、この値を外すか、各自で確認し直すこと。

---

## データを更新する

### 名簿を作り直す（学校が増減したとき）

```bash
python tools/import_all_schools.py --apply
python tools/build_bundle.py
python tools/qa_check.py
```

公式一覧から全校を取り直し、既存の偏差値・制服・男女比・警告文は引き継ぐ。

**府の一覧に載っていない府立高校は廃校の可能性が高い。**
実際、初期データに入れていた泉鳥取高等学校は廃校で、
[メモリアルページ](https://www.pref.osaka.lg.jp/o180040/kotogakko/hp/memo.html)で確認して削除した。

### 定員割れを更新する（毎年3月以降）

```bash
python tools/fetch_capacity.py --apply
python tools/build_bundle.py
```

大阪府の志願者数Excelから、募集人員と志願者数を読んで判定する。
新年度のページが増えたら `tools/fetch_capacity.py` の `YEAR_PAGES` の先頭に追加する。

### 偏差値を更新する

```bash
python tools/fetch_deviation.py --apply
python tools/build_bundle.py
```

一覧は1ページで完結するのでリクエストは1回だけ。robots.txt を確認したうえで取得する。

### 路線・座標を更新する

```bash
python tools/fetch_osm_lines.py --apply   # 路線と駅
python tools/fetch_osm.py --apply         # 学校と駅の座標
python tools/build_bundle.py
```

Overpass は混雑時に 429 を返すので、待ち時間を伸ばしながら5回まで再試行する。

### 公式サイトURLを更新する

```bash
python tools/fetch_official_urls.py --apply
python tools/build_bundle.py
```

府立高校のURLは `www.osaka-c.ed.jp/<校名>/` に統一されておらず、`www2`/`www3` 配下や
独自ドメイン（天王寺高校は `tennoji-hs.jp`）が混在しているので、推測では当たらない。

### 妥当性を確認する

```bash
python tools/qa_check.py             # オフラインのチェック
python tools/qa_check.py --reverse   # 座標を住所に逆引きして照合（約4分）
```

---

## 通学時間の計算方法と、その限界

`js/transit.js` が、駅の座標と路線の並び順だけから所要時間を推定している。
有料の経路検索APIを使っていないので、時刻表に基づく正確な検索ではない。

- 駅間の所要時間 = 直線距離 × 1.10 ÷ 路線ごとの実効速度
- 乗換は「駅どうしが500m以内」なら自動で接続。乗換時間＋待ち時間を加算
- 直通運転のある路線（泉北高速↔南海高野線など）は乗換ではなく2分の接続として扱う
- 徒歩80m/分・自転車240m/分。直線距離に1.25〜1.30倍の迂回係数をかける
- **バスは路線データを持っていないため、直線距離からの粗い概算**

実測ダイヤとの照合では、難波→岸和田で実際26分に対し推定約35分など、
**長距離ほど数分〜10分ほど多めに出る**傾向がある。安全側の見積もりとして使い、
実際の受験校選びでは必ず乗換案内で確認すること。

`avgSpeedKmh` を実測ダイヤに合わせて調整したのは10路線だけで（`speedCalibrated: true`）、
残りは種別ごとの既定値のまま。使いながら合わせ込むとよい。

将来もっと正確にしたい場合は `HSTransit.route()` の中身だけを
Google Maps Directions API などに差し替えれば、画面側は変更不要。

---

## 既知の不足

- **市立高校3校が未収録**（東大阪市立日新、堺市立堺、岸和田市立産業）。府の一覧にも
  私学連合会の一覧にも載らないため。
- **座標が無い4校**（東大阪みらい工科、夕陽丘、教育センター附属、貝塚）は通学時間を計算できない。
- **偏差値が無い35校**。工科高校など、取得元の一覧に載っていない学校。
- **住所と座標が食い違う3校**（阪南、成美、近畿大学泉州）。画面に警告が出る。
- **阪堺電気軌道・バス路線が未収録。**

---

## ライセンスと出典

- 駅・高校の座標: © OpenStreetMap contributors（[ODbL](https://www.openstreetmap.org/copyright)）
- 校名・URL・学科・志願者数: 大阪府、大阪私立中学校高等学校連合会
- 住所検索の補助: 国土地理院 地名検索API
- 偏差値: みんなの高校情報（個人利用の範囲で参照）

**このアプリは個人利用のみを想定している。商用利用はしない。**

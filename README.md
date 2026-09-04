# 大阪 高校さがし

大阪府堺市在住の中学生が、**最寄り駅からの通学時間**と**偏差値**から進学先の高校（公立・私立）を
探すための Web アプリ。対象範囲は大阪市から大阪府最南端の岬町まで。

外部APIもサーバーも使わない静的サイトなので、`index.html` をブラウザで開くだけで動く。

---

## 使い方

```bash
python tools/build_bundle.py
```

を一度実行したあと、`index.html` をダブルクリックしてブラウザで開く。

ローカルサーバーで見たい場合（推奨・キャッシュの問題が起きにくい）:

```bash
python -m http.server 8765
```

ブラウザで <http://localhost:8765> を開く。

---

## 別のパソコンで作業を続けるとき

このフォルダは Git リポジトリになっている。

```bash
git clone <リポジトリURL> && cd 高校受験
```

必要なのは **Python 3.10以上** だけ（外部ライブラリ不要）。Node.js は開発時のテストにのみ使う。
GitHub にまだ上げていない場合の手順は「GitHub に置く」の節を参照。

---

## できること

| # | 機能 | 実装状況 |
|---|------|----------|
| 1 | 性別を選び、最寄り駅から徒歩・自転車・バス・電車で通える高校を時間指定で絞り込む | 完了 |
| 2 | 現在の偏差値と目標偏差値から、安全圏／合格圏／実力相応／挑戦圏を学科ごとに判定 | 完了 |
| 3 | 偏差値・男女比・制服の表示 | 表示は完了。男女比と制服はデータ取得が道半ば（後述） |
| 4 | 公式サイトからの情報の自動取得・更新 | 巡回とリンク検査は完了。抽出は保守的な自動＋目視確認 |
| 5 | 大阪市〜大阪府最南部（岬町）をカバー | 10路線177駅・51校を収録 |

---

## フォルダ構成

```
index.html            画面
css/style.css         スタイル
js/transit.js         通学時間の概算エンジン（このファイルだけで完結）
js/app.js             画面の組み立てと絞り込み
data/lines.json       路線と駅（座標・実効速度・待ち時間・直通関係）  ← 手で編集してよい
data/schools.json     高校のデータ                                    ← 手で編集してよい
data/bundle.js        上の2つを結合した自動生成ファイル                ← 編集しない
tools/build_bundle.py data/*.json → data/bundle.js
tools/fetch_osm.py    OpenStreetMap から駅・高校の座標を取得
tools/geocode.py      国土地理院APIで住所から座標を取得（補助）
tools/update_schools.py 公式サイトを巡回してリンク検査・男女比・制服を取得
tools/qa_check.py     データの妥当性チェック
```

**データを編集したら必ず `python tools/build_bundle.py` を実行する。**
これを忘れると画面に反映されない。

---

## 通学時間の計算方法と、その限界

`js/transit.js` が、駅の座標と路線の並び順だけから所要時間を推定している。
有料の経路検索APIを使っていないので、時刻表に基づく正確な検索ではない。

- 駅間の所要時間 = 直線距離 × 1.10 ÷ 路線ごとの実効速度（急行・快速込みで較正済み）
- 乗換は「駅どうしが500m以内」なら自動で接続。乗換時間＋待ち時間を加算
- 直通運転のある路線（泉北高速↔南海高野線、近鉄南大阪線↔長野線、JR阪和線↔関西空港線／大阪環状線）は
  乗換ではなく2分の接続として扱う
- 徒歩80m/分・自転車240m/分。直線距離に1.25〜1.30倍の迂回係数をかける
- **バスは路線データを持っていないため、直線距離からの粗い概算**

実測ダイヤとの照合では、難波→岸和田で実際26分に対し推定約35分など、
**長距離ほど数分〜10分ほど多めに出る**傾向がある。安全側の見積もりとして使い、
実際の受験校選びでは必ず乗換案内で確認すること。

将来もっと正確にしたい場合は `HSTransit.route()` の中身だけを
Google Maps Directions API などに差し替えれば、画面側は変更不要。

---

## データの出どころと信頼度

| 項目 | 出典 | 信頼度 |
|------|------|--------|
| 駅の座標 | OpenStreetMap（© OpenStreetMap contributors, ODbL） | 177駅すべて名前一致で取得。高い |
| 高校の座標 | OpenStreetMap | 51校中48校が一致。残り3校は住所からの推定値 |
| 高校の住所・校名・種別・共学/男子/女子 | 手入力 | 未検証。公式サイトで確認が必要 |
| **偏差値** | **民間模試の一般的な目安をもとにした参考値** | **公式発表ではない。塾・模試の最新資料で必ず確認すること** |
| 男女比 | 公式サイトから自動取得（未実行） | 現在51校中48校が未取得 |
| 制服 | 一部のみ手入力 | 現在51校中44校が未取得 |

`verified: false` のレコードは画面に「未検証」バッジが出る。
座標の確度が低い4校には警告文が出る（近畿大学泉州、貝塚南、初芝立命館、大阪暁光）。

### 偏差値について

高校の偏差値には公的なオープンデータが存在しない。民間模試会社がそれぞれ独自に算出しており、
模試によって数値が3〜5違うのは普通のこと。このアプリの数値は「だいたいこのあたり」を掴むためのもので、
出願判断に使える精度はない。

---

## データを更新する

### 座標を取り直す（OpenStreetMap）

```bash
python tools/fetch_osm.py            # 差分の確認だけ
python tools/fetch_osm.py --apply    # data/*.json に反映
python tools/build_bundle.py
```

大きく座標が動いたものと、地図に見つからなかったものは `tools/coord_review.json` に出る。

### 公式サイトを巡回する

```bash
python tools/update_schools.py           # 巡回してレポートを出すだけ
python tools/update_schools.py --apply   # 確実に読み取れた項目だけ反映
python tools/build_bundle.py
```

robots.txt を確認し、1リクエストにつき2秒待つ。相手のサーバーに迷惑をかけない設計にしてあるので、
待ち時間を短くしないこと。結果は `tools/update_report.json` に出る。
**リンク切れの学校がここで分かるので、まずこれを一度流して `website` を直すとよい。**

男女比と制服は、公式サイトに明確な記載があるときだけ自動で入る。読み取れないものは
`null`（未取得）のまま残る。推測で埋めない方針。

### データの健全性を確認する

```bash
python tools/qa_check.py
```

最寄り駅から3km以上離れている高校、必須項目の欠落、偏差値の異常値を検出する。

---

## 高校や駅を追加する

`data/schools.json` の `schools` 配列に1件足すだけ。

```json
{
  "id": "pref-xxxx",
  "name": "大阪府立◯◯高等学校",
  "shortName": "◯◯",
  "type": "public",
  "gender": "coed",
  "city": "◯◯市",
  "address": "大阪府◯◯市◯◯1-2-3",
  "lat": 34.5, "lng": 135.5, "coordSource": "approx",
  "courses": [{ "name": "普通科", "deviation": 55 }],
  "genderRatio": null,
  "uniform": null,
  "website": "https://example.ed.jp/",
  "updatedAt": null,
  "verified": false
}
```

- `type` は `public` / `private`
- `gender` は `coed`（共学）/ `boys`（男子校）/ `girls`（女子校）
- 座標は適当でよい。追加後に `python tools/fetch_osm.py --apply` を流せば実座標に直る
- 駅を足すときは `data/lines.json` の該当路線の `stations` に、**路線の並び順どおりの位置に**挿入する

未収録の路線: 阪堺電気軌道、水間鉄道、各社の路線バス。

---

## GitHub に置く

```bash
git remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git
git branch -M main
git push -u origin main
```

GitHub Pages（Settings → Pages → Branch: main / root）を有効にすると、
スマホからも見られる URL が発行される。

---

## ライセンスと出典

- 駅・高校の座標: © OpenStreetMap contributors（[ODbL](https://www.openstreetmap.org/copyright)）
- 住所検索の補助: 国土地理院 地名検索API

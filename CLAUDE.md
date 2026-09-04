# このプロジェクトで作業するときの約束事

大阪府（大阪市〜岬町）の高校を、通学時間と偏差値から探す静的Webアプリ。
詳しい仕様と手順は README.md に書いてある。まずそれを読むこと。

## 環境

- Windows / PowerShell。**PowerShell 5.1 なので `&&` `||` は使えない**（`; if ($?) { }` を使う）
- Python 3.13。**外部ライブラリを増やさない**（標準ライブラリだけで書く）
- Node.js はエンジンの動作確認にだけ使う。アプリ本体は Node に依存しない

### PowerShell で日本語ファイルを触るときの注意

`Get-Content -Raw` / `Set-Content` は UTF-8 のファイルを壊す（読み込みで文字化け、書き込みで BOM 付与）。
JSON や日本語を含むファイルを PowerShell から書き換えないこと。Python か Edit ツールを使う。
どうしても必要なら `[System.IO.File]::ReadAllText` と
`[System.IO.File]::WriteAllText($p, $t, (New-Object System.Text.UTF8Encoding($false)))` を使う。

## 変更したら必ずやること

```bash
python tools/build_bundle.py   # data/*.json を編集したら毎回
python tools/qa_check.py       # データを触ったら毎回
```

`data/bundle.js` は自動生成物。直接編集しない。

## データの扱いかた（重要）

**推測で数値を埋めない。** このアプリは受験生が進学先を決めるのに使う。

- 分からない項目は `null` にして「未取得」と表示する。それらしい値を入れない
- 偏差値は公的データが存在しない参考値。UI とドキュメントで必ずそう明示する
- 出典が変わったら `coordSource` などのフィールドを更新する
- 確度が低いレコードには `dataWarnings` を付ける（画面に警告が出る）
- 座標は OpenStreetMap を一次ソースにする。国土地理院の住所検索APIは
  泉北ニュータウンや岬町で数km外す誤マッチが確認されているので、単独では使わない
- **府立高校の公式URLは推測しない。** 大阪府の「公立高校ホームページ一覧」から取る
  （`tools/fetch_official_urls.py`）。`www.osaka-c.ed.jp/<校名>/` に統一されていない
- **府立高校を追加したら必ず `fetch_official_urls.py` を流す。** 府の一覧に無い学校は
  廃校の可能性が高い（泉鳥取高校が実際にそうだった）。閉校した学校を候補に出さないこと

## 通学時間エンジンを触るとき

`js/transit.js` の `route()` が唯一の公開API。ここを差し替えれば経路検索APIに移行できるので、
この境界を壊さないこと。

速度係数（`avgSpeedKmh`）を変えたら、実測ダイヤと照合して妥当性を確認すること。
現状は「難波→岸和田 実際26分に対し推定約35分」程度に、長距離で多めに出る。

## 外部サイトへのアクセス

`tools/update_schools.py` は各高校の公式サイトを巡回する。
robots.txt の確認と1リクエスト2秒の待機を必ず維持すること。短くしない。

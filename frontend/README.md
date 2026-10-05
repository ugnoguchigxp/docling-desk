# Docling Desk frontend

React 19・TypeScript strict・Vite・TanStack Queryを使う。表はAG Grid React/Community 35.2.1の閲覧専用表示。通常起動はFastAPIのみで、配布用の生成物は `../src/docling_desk/resources/static/frontend/` に保存する。

```sh
pnpm install --frozen-lockfile
pnpm dev
```

Node.js 22.12以降（検証環境: 24.11.1）、pnpm 10.24.0を使う。開発画面は127.0.0.1:5173、FastAPIは127.0.0.1:8765。API・文書・既存CSSはHostを保つプロキシで取得し、Origin制限を維持する。Viteはポートが使用中なら停止する。

```sh
pnpm typecheck
pnpm lint
pnpm test:run
pnpm build
pnpm export:static
pnpm test:e2e
pnpm test:e2e:dev
pnpm test:visual
```

`pnpm test:e2e:synthetic` は空のデータから合成試料だけを使います。文字位置とサムネイルの回帰は `a` の32桁、表の印刷は `b` の32桁です。保存済み実資料の確認は別コマンド `pnpm test:e2e:saved` で、`UI_SAVED_DOCUMENTS=1` と `pnpm visual:prepare` が作った非公開キャッシュが必要です。その資料は再配布しません。`UI_SYNTHETIC=1` のままでは実行しません。

`build` はdistへ出力し、`export:static` が配信用ディレクトリだけを更新する。新資産を先に配置してから入口HTMLを置換し、表示中の画面が遅延取得できるよう古いハッシュ付き資産を保持する。配布ライセンスは `static/frontend/licenses.txt` に出力する。表のコードは初めて表タブを開くときに読み込む。文書内のHTML・CSS・フォント・SVG・画像はViteの処理対象にしない。

ブラウザー検証は8876の専用サーバーと `.cache/frontend-migration-data-8876/` を自動起動する。開発プロキシ検証は8877/5187と専用コピーを使う。8765の通常サーバーを停止しない。翻訳・解説・アップロードの検証は固定応答を使い、LLMへ送信しない。

初回の検証用原本は `.cache/frontend-migration-source/data/` に固定する。存在しない環境では `pnpm visual:prepare` が保存資料から初回コピーを作る。`visual:baseline` は旧画面を2回撮って再現性を確認する。`test:visual` は同じ旧画面と `/ui/` の画像・矩形・文字の行位置・描画方式・資産ハッシュを比較し、証拠を `../qa/frontend-migration/layout/` へ保存する。移行後の画像で旧画面の基準を上書きしない。

共通部品の利用方針と台帳は `src/components/README.md` を参照。旧UIは `/legacy/`。FastAPI起動時に `DOCLING_LEGACY_UI=1` を指定すれば通常の入口を旧UIへ戻せる。保存データを過去の状態へ戻す必要はない。

ExcelのFit計測の修正は新旧のiframe bridgeで共有する。修正前・後の原寸表示も全シートで比較する。PDFの言語通知漏れも旧画面に反映し、保存済み訳文を同じ条件で表示する。

文書レイアウトの画素比較は、同じ全画面画像からPPTXの表示領域、PDFのページ表示領域、Excelのシート表示領域を取り出して行う。領域の位置・寸法は新旧で一致を要求し、拡縮や画素のマスクを使わない。全画面の画像と差分も保存する。サムネイルは縮小描画の色が旧画面内でも変動するため、表示完了を待ち、配置、選択状態、参照画像のSHA-256を比較する。親画面と文書内部の矩形・文字の行位置、CSS・SVG・画像の参照とハッシュも比較する。

画素の色差は旧画面の繰り返し撮影で観測したものだけを記録する。差が残る場合は同じ条件の旧画面を追加撮影し、同じ画素・色チャンネルで再現した差だけを記録する。Reactの差分から許容値を決めない。通常の操作画面は別に12条件で全画面比較する。PDFのツールバーSVGが旧画面同士でも微小な色差を持つことは、`native-svg-calibration.json` と旧画面2枚に記録している。文書表示領域の比較には、このSVGの色差を適用しない。


最終集計はリポジトリのルートから `.venv/bin/python qa/frontend-migration/verify.py` で確認する。116条件の移行ビルドの証拠と、並行変更を含む現在の操作確認を区別して記録する。長い比較の途中に配布内容が変わる場合は、専用サーバーの `DOCLING_UI_QA_BUNDLE` に固定した配布ディレクトリを指定できる。`UI_CASES` に条件名のJSON配列を渡すと、その条件だけを再検証する。再検証前の結果を保存し、未検証の条件を合格扱いにしない。

コードレビュー後の現在の検証は `qa/frontend-review/` に保存する。`verification.json` が現在の修正と検証範囲の集計で、移行時の `qa/frontend-migration/` は履歴として保持する。レビューの比較は固定した旧ビルドを専用サーバーの `DOCLING_UI_QA_BASELINE` で配信し、`UI_BASELINE_PATH=/review-baseline/` と別の `UI_LAYOUT_OUT`、`UI_REPORT_DIRECTORY` を指定して移行時の証拠を上書きしない。

レビュー後の最終集計は、リポジトリのルートから `.venv/bin/python qa/frontend-review/verify.py` で確認する。

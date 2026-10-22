# ブラウザー・Python型チェックの追跡検証（2026-10-22）

対象は前回のセキュリティ更新後のcheckout。既存の未コミット変更を保持し、本番環境の変更、サービス停止、データ削除、commit/pushは行っていない。

## 結論

ブラウザー7件の原因は、既存の画面・配布物とテストの参照先の不一致、iframeの描画反映待ち、図編集のツール未選択だった。Node.js更新による生成物の回帰は確認されなかった。修正はE2E 3ファイルと、前回追加したTLS検証スクリプトの2件に限定した。製品コード、API、認証、プロンプト、依存版、型検査の対象・規則は変更していない。

ユーザーの範囲指定: 「今回追加した2件を修正し、既存診断は根拠付きで切り分ける」。既存の自社コード・外部Docling・過去QAの型診断を一括改修していない。全体の型チェックが成功したという意味ではない。

## Node.js更新との比較

- 更新前ソースをHEADから専用コピーへ復元し、開始時点のユーザー変更4件を重ねた。元checkoutやvenvを巻き戻していない。依存物は同じnode_modulesを使用し、旧版による外部パッケージ取得は行っていない。
- 旧Node.js 24.11.1で作った配布物でも、印刷範囲・CSS順序・表の復旧ボタン・TeX・図編集の5件が同じ理由で失敗。クリックに依存する印刷2件は旧版でも新版でも成否が変わった。
- 別の作業パスで作った配布物にはパス由来のチャンク差が出るため、それをNodeの差とは扱わなかった。同じ作業パス、同じソース・node_modulesで24.11.1→24.22.1を順に実行し、生成物の全325ファイルがファイル名・SHA-256とも一致することを確認した。
- 比較結果: `.cache/security-followup/node-same-path-comparison.json`。実ブラウザーは同じChrome、Pythonは同じvenvを使用。Brotli/TLS/DNSの修正版指定を戻す理由は見つからなかった。

## ブラウザー7件の原因と修正

| 失敗 | 観測・原因 | 修正と維持した検査 |
|---|---|---|
| lazy imagesの印刷 | CSSでiframeのpointer-eventsが無効でも、Chromeの別描画面に反映前だとクリックがiframeへ届く | メニューを開いた後にも既存のwaitForViewerSurfaceを使用。画像待ち中の印刷禁止・読込後の許可を維持 |
| 印刷シェルの再試行 | 同じクリックの取りこぼし。診断12回中5回でトップ文書のpointer/clickイベントなし、activeElementがiframe | 同じ共通待機処理。シェル失敗表示と再試行後の印刷許可を維持。修正後は2件×10回の20件連続成功 |
| 印刷範囲synthetic-glyphs | 文書側の翻訳エラーとダイアログ側の範囲エラーでrole=alertが2件あり、page全体の単数locatorが失敗 | ダイアログ内のalertへ限定。範囲0のエラーと印刷不可、正常範囲の印刷を維持 |
| bundle/CSS/Origin | 既存の配布物にpreload-helper CSSがあり、期待する2ファイルと不一致 | helper・DocumentViewer・indexの3ファイルの順序を明示。先頭6 CSS、Origin拒否/許可、legacy検査を維持 |
| 欠落Tables chunk | 画面のボタン名は「もう一度読み込む」、テストは「再読み込み」を参照 | 現在の復旧ボタン名へ追従。chunk取得失敗、エラー表示、原本/一覧へ戻れることを維持 |
| TeXの組版/ソース切替 | PDFページは既にWebPとテキスト層。旧SVGを期待して失敗 | 実際のページ画像の表示・ロード完了・naturalWidth>0を確認。本文Hello、2ページ、ページ移動、ソース検索、表示復帰、サムネイルの検査を維持 |
| 図形描画・保存・再読込 | 作成ダイアログ終了直後はエディターにフォーカスがなく、裸のrキーで矩形が選択されない | 表示される矩形ツールのlabelをクリックし、radioが選択済みであることを先に検査。ドラッグ・文字入力・保存後と再読込後の矩形/文字の期待値は維持 |

図形ツールでは、native radioへの直接クリックは上のアイコンに遮られ、Space操作でも選択が変わらなかった。DOMでlabelとアイコンの関係を確認し、利用者が押すlabel経由で選択できることを観測して修正した。force操作、任意時間sleep、retries、タイムアウトの延長、skip、保存データの注入は追加していない。

追加の通し検証で翻訳範囲のチェック操作が1件失敗した。クリックイベントの計測では、描画待ちなし6回中5回で入力へclickが届かず、待機あり6回中0回だった。翻訳ダイアログを開いてiframeの操作を無効化した後にも同じwaitForViewerSurfaceを使用し、チェック操作・POST件数・選択単位・force・pacingの検査を維持した。修正後の同試験は10回連続成功。未確認のまま全体成功とはせず、全体を再検証した。

## 型チェックの切り分け

前回追加したscripts/verify_tls.pyについて、requestsが未導入ならNoneとなる分岐の型をprobe関数内でも検証し、urllib.errorを明示importした。requests未導入時にmainでスキップする既存動作は保持している。

| 比較 | 結果 |
|---|---|
| 更新前ソースの自社範囲（src/packages/integrations/scripts/tests） | 333診断 |
| セキュリティ更新後・今回修正前の同範囲 | 335診断。行番号を除いた診断文の比較で追加はTLSスクリプトの2件のみ、既存診断の消失なし |
| 全体チェック・今回修正前 | 1,654診断 |
| 全体チェック・今回修正後 | 1,652診断。追加0、解消はTLSスクリプトの2件のみ |
| TLSスクリプト単独、警告もエラー扱い | 成功 |

残る1,652診断の内訳: 外部Docling 1,280、過去のqa 39、src 110、tests 222、integrations 1。前回の「1,654」は診断件数であり、全件が今回導入した不具合という意味ではなかった。

自社範囲の更新前後比較は同じvenv・同じty 0.0.84で実行した。外部Doclingは固定commit・作業ツリーとも変更なし。Doclingを別パスへコピーして全体比較するとeditable installとの型の同一性が変わり診断数も変わったため、その生の件数を回帰判定には使っていない。全体の前後比較は元パスのログ、更新前の自社範囲比較は位置情報を正規化した診断文を根拠にしている。

根拠: `.cache/security-followup/type-comparison.json`、`types-final-comparison.json`、`types-baseline-owned.log`、`types-full-before.log`、`types-full-after.log`。既存分を隠すexclude/ignoreや検査対象の縮小は追加していない。

## 最終検証

| 検証 | 結果 | 主なログ（.cache/security-followup/） |
|---|---|---|
| 合成ブラウザー全体 | **94成功・0失敗**（wiki/printing/operations/diagram、Node 24.22.1、専用18876ポート） | browser-final-two.log、browser-final-two/browser-results.json |
| 元の7件の個別検証 | 全件成功。最後に全体でも確認 | browser-after.log、browser-after-two.log、diagram-after.log |
| 印刷の反復 | 2件×10回=20成功 | print-repeat.log |
| 翻訳の反復 | 10回成功 | translation-repeat.log |
| frontend単体 | 57ファイル・417成功 | frontend-unit-final.log |
| frontend build/typecheck/lint/format | 成功。型確認の対象にはe2eも含まれる | frontend-build-final.log、frontend-lint-final.log、frontend-format-final.log |
| Python関連テスト | 82成功 | python-targeted-final.log |
| Python lint | 全体Ruff成功 | 実行結果を会話に記録 |
| 今回の型修正 | ty 0.0.84でverify_tls.pyを警告もエラー扱いにして成功 | types-final-comparison.json |
| Python全体の型チェック | **失敗のまま、既存1,652診断**。対象や規則を緩和せず、今回増えた2件だけ解消 | types-full-after.log |
| TLS検証スクリプト | Python 3.12.13、OpenSSL 3.6.3、certifi 2026.10.19。25項目成功（正常TLS接続と問題CA拒否） | tls-after.jsonl |
| Nodeの同一パス比較 | 24.11.1/24.22.1の全325ファイルが同一 | node-same-path-comparison.json |
| 差分点検 | 空白エラーなし。既存の変更保持。型検査の無視・skip・検証無効化なし | start-hashes.json、start.patch |

初回の全体実行は93成功・翻訳チェック1失敗。その新しい失敗も診断してから修正し、最終全体を再実行した。途中の成功を最終結果へ流用していない。

Pythonの製品コード・依存物・配布資源は今回変更していないため、前回完了したPython全体試験やDocker再構築を繰り返す代わりに、変更したヘルパーの型・実通信と関連Python検査を行った。本番・実API認証・課金通信は対象外。

追試例（リポジトリルート）:

```sh
.venv/bin/ty check scripts/verify_tls.py --error-on-warning
.venv/bin/ty check --output-format concise  # 既存1,652診断により非ゼロ終了
UI_SYNTHETIC=1 UI_QA_PORT=18876 DOCLING_ENV_FILE=/dev/null \
  mise exec node@24.22.1 -- pnpm --dir frontend test:e2e:synthetic
mise exec node@24.22.1 -- pnpm --dir frontend build
```

今回の実行にはレポートと失敗時成果物の保存先も.cache/security-followup/以下へ指定した。独立したQAプロセスだけを使い、既存の稼働サービスを停止していない。

## 保持・残作業

開始時の全変更ファイルをハッシュ付きで控え、今回編集したTLS検証スクリプトと報告書への追記を除き、既存の変更が同一内容で残っていることを確認した。ユーザーのWiki/検索変更4件を変更していない。

指定範囲の型修正は完了。既存1,652診断は残る。Bun内蔵CAの問題、未実施の本番反映、実API資格情報を伴う通信経路など、前回報告の残作業も継続する。今回の検証成功をそれらの対策完了へ広げない。

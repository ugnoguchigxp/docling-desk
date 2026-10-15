# 外部文書基盤の検証記録

2026年10月14日から15日にかけて、独立ナレッジAPIのMarkdown編集、MCP、見たまま編集、検索と原文閲覧を検証した。変更前コミットは `1b27e5ac2d2f66970286a02515a99d49d5688f76`。実環境の資料・設定やEumenes・LARM本体は変更していない。

## 変更前のベースライン

| 検査 | 結果 |
| --- | --- |
| knowledge-api既存試験 | 60成功、0失敗 |
| frontend既存試験 | 170成功、1時間切れ。Libraryのツールバー操作試験が5000msで時間切れ |
| 同じLibrary試験の単独実行 | 9成功。全体並行実行時の失敗は消去せずベースラインへ残す |
| Python既存全体試験 | 663成功、4スキップ、1失敗。`test_isolated_http_path_records_samples_without_external_calls` が既存WikiのimportからHTTP 500を受けた |

ベースラインPythonの失敗は独立APIの編集経路に属さず、既存Wiki・評価用コードは変更していない。根拠は `baseline-python.log`、利用経路は `src/docling_desk/evaluation/benchmark.py` のimport_corpus。担当外の調査・修正が必要な場合は別作業とする。

## 原文保持の試作と採用方式

Plate 53.3.15、Markdown 53.3.15、React 19.3.0で試作した。全文再シリアライズを保存経路にせず、remarkの構文位置で原文を分割し、対応するブロックだけをPlateで編集する。無編集のブロックと未知の記法は原文のまま保持する。

初期の試作でBOMによる構文位置のずれを確認し、先頭BOMを分離して元の位置へ対応させた。API試験ではBun 1.4.3がSQLiteへ先頭BOMのある文字列をbindするとBOMを落とすことを再現した。下書き本文はUTF-8のBLOBで保存する。これは原文保持のための入出力処理であり、意味判断の追加ではない。

4件の試作・回帰試験で、BOM・CRLF・コード・Mermaid・数式・独自ブロック・参照リンク・MDX・日本語・絵文字の保持、実Plate操作、undo、保護対象と古い原文の拒否、textarea編集時の未変更CRLF保持を確認した。

## 実装時の部分と結合の試験（レビュー前）

| 検査 | 結果と範囲 |
| --- | --- |
| knowledge-api全体 | 78成功。追加の編集15件と、実プロセスSIGKILLの3件を含む |
| 型・lint・OpenAPI一致 | APIとfrontendで成功。公開契約を保存ファイルと照合 |
| frontend全体 | 175成功。既存171件と原文保持4件 |
| Python関連 | gateway、MCP、Open WebUI、processor、配布単体を含む47件成功 |
| 配布の既存単体 | entrypointsと資産選別の5件成功 |
| 原文と保存状態 | 下書き保存で抽出0回、キュー満杯でも確定成功、ハッシュと実ファイルの一致 |
| 部分編集 | 0件・複数一致・原文不一致・UTF-8途中・重複範囲で本文とETagを変更しない |
| 競合 | 同じ下書きETagの後発更新、同じ文書版からの別下書きの後発確定を拒否 |
| 再送と再起動 | 保存直後の結果照会、同じキーの再送、DBの再オープンで文書と版を重複しない |
| 異常終了 | original_written、register_before_commit、editing_savedでSIGKILLを注入。再送後の文書・版・ジョブが各1件、BOMとSHA-256を保持 |
| 権限とジョブ | 処理を明示的なbarrierで止めてscope変更。旧runがsupersededとなり索引を公開しない。旧scopeでは本文取得を拒否 |
| Embedding | fixture Providerの失敗でも本文とFTSを保持。identity変更後の旧ベクトルを除外し、再抽出なしのreindexで再生成 |
| 添付 | 下書き・確定版・次の下書きへ安定IDを保持。所属不一致、権限外、削除後の取得を拒否 |

## 実HTTPとMCPとブラウザーによる通し受入

再現入口は `DOCLING_ENV_FILE=/dev/null .venv/bin/python qa/external-document-platform/acceptance.py`。その前にfrontendのbuild・exportを行う。

一時領域へ、実PDF・Word・Markdown・合成PNGを作成した。API、実Docling processor、gatewayを別プロセスで起動し、MCP SDKの実Streamable HTTP接続とPlaywrightのChromiumを使う。資格情報は一時ファイルに0600で保存し、ツール引数やブラウザーへ渡さない。終了時にそのプロセスと一時領域を削除する。

次の流れが成功した。

1. PDF・Word・Markdownを既存multipart APIへ登録し、実Doclingで抽出した。
2. MCPで検索と根拠取得を行い、原本版・根拠版・retrievalを確認した。
3. MCPで新規Markdown下書きを作り、同じキーの再送、HTTPからの取得、厳密一致による部分編集を行った。
4. MCPでPNGを登録し、安定参照を本文へ追加した。ブラウザーの保護ブロック内で認可された画像を取得した。
5. 接続コードを承認し、人がPlateの見たまま編集で本文を変更した。自動保存のサーバー応答だけを失わせ、同じ操作で再送した。
6. 確定のサーバー応答だけを失わせ、同じ操作で再送した。MCPの古いETagからの後発更新を拒否した。
7. 保存原文のBOM・CRLF・独自記法と実SHA-256、添付バイトを照合し、FTS反映を確認した。
8. 検索の原文範囲を使い、文書単位の閲覧認証後に原文を開いた。MDXを実行せず、エスケープした原文を表示した。
9. 処理を休止して別の下書き・保存版を作り、APIを再起動した。同じキーで確定結果を再取得し、処理再開後に索引を作成した。保存版は1件だった。
10. 改版後の旧引用をMCPと既存viewerセッションで拒否した。read-only、権限外の文書、偽装した利用者情報によるMCP操作も拒否した。

実行結果は [acceptance-results.json](acceptance-results.json)。画面画像はローカルの `editor.png`。実LLM・実LARMは使用しておらず、MCPツール選択のLLM品質を実証したものではない。

## 配布の確認

`verify-distribution.py` はsdistからwheelを構築し、編集HTMLと到達するJS・CSS、エディタのライセンス、OpenAPIを検査する。チェックアウト外で展開したgatewayからeditorを開き、資産取得とMCP引数契約を確認した。結果は [distribution-results.json](distribution-results.json)。editor.htmlも資産選別の起点に追加し、API契約をMANIFESTとwheelのresourcesへ含めた。

既存の `scripts/verify_package.py` は新しいsdistとwheelの構築後、既存デスクトップ画面のTestClientがHost検査で `Invalid host header` となり停止した。関連する既存Host設定と既存画面は変更せず、新しいgatewayの配布経路を別の検証入口で確認した。既存全体の配布probeが成功したとは報告しない。

## 未検証と外部側の作業

Docker CLIは利用できるがデーモンのソケットがなく、コンテナのbuild・実行は行えなかった。gatewayのDockerfileとCompose overlayは準備済みで、Composeのconfig検証は成功。実コンテナの確認は残る。

実Embedding・実LLM・実LARM、実アカウントと本番配備は未検証。LARMの正式なservice・Secret・profile・起動停止契約の接続、Eumenesの認証・Artifact・公開操作、外部媒体への変換・投稿は担当外である。

## 差分と設計原則

保存は既存Store・トランザクション・改版・冪等性・永続ジョブを利用し、MCPとeditorはHTTPの同じ業務操作へ転送する。原文位置の解析、UTF-8範囲・厳密一致の検査、権限と版、URIとMIMEの検査は契約の強制に限定した。文章の意味理解や推敲を、語句別の条件分岐・固定応答で代替していない。

原文・版の保存先は独立APIのみ。既存Wikiのデータ移行・統合、Eumenes・LARM本体の変更、本番配備は行っていない。通常ログへ本文・長期トークン・署名鍵を出さず、接続URLの短期資格情報はアクセスログで伏せる。

## 計画に照らした修正・再検証

2026年10月15日の [実装レビュー](review.md) で、認可・同時要求・応答喪失・容量境界・添付・検索範囲と正本ハッシュを追加検査した。範囲内の問題を修正し、API90件、frontend178件、今回再実行したPython関連42件が成功。実HTTP/MCP・実ブラウザー・実Doclingの通し受入と配布も再実行して成功した。最新の結果JSONには新規作成の応答喪失、待機中の追加入力保持、viewerの添付取得を含む。レビュー前の成功件数は履歴として上記に残す。

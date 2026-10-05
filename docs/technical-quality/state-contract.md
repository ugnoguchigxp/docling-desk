# 状態と成果物の契約

原本、抽出成果物、検索チャンク、出典は別のものです。再抽出、索引更新、印刷は原本を書き換えません。

| もの | 正本 | 識別 | 失効 |
| --- | --- | --- | --- |
| ローカル資料 | `content/documents/<job>/original.*` と `content/manifests/library.json` | job id、原本バイト | 原本・成果物・実行状態・サムネイルを物理削除。ごみ箱はない |
| 抽出成果物 | `derived/documents/<job>/` の `document.json`、`rag.jsonl`、`rag-index.jsonl` とmanifest | `runtime/documents/<job>/job.json` の状態 `success` または `partial`、成果物ハッシュ | 失敗した再抽出は公開済みの成功版を消さない |
| Wiki | `content/wiki/revisions/` のMarkdown・CSVと `content/manifests/wiki.json` | 記事id、論理パス、公開版、本文SHA-256をrevisionにする | manifestで削除を公開し、索引から除外。公開済み本文と削除状態は保持する |
| 検索チャンク | `runtime/knowledge/local.sqlite` の chunks と FTS | `記事id:revision先頭:節:分割` | revisionがsourcesと一致する行だけ検索する |
| 親文脈 | contexts | 記事id、revision、本文ハッシュ | 断片ごとには複製しない |
| 意味ベクトル | vectors | 接続先・モデル版・次元・本文ハッシュ | 別プロファイルのベクトルは混ぜない |
| 外部API資料 | `.knowledge-api` の revisions | source id、revision id、SHA-256 | 別collection、project、regionへは返さない |
| OCR operation | APIの台帳またはローカルJSON | run、source revision、version | `submitting` のまま落ちた要求は受付不明。自動再送しない |
| 印刷HTML | 抽出HTMLからその場で作る受動文書 | ページ範囲 | イベント属性、外部URL、iframeは除去する |

ローカルWikiと外部APIは、同じ言葉を使っていても保存領域もDBも別にします。検索語のNFKC・casefold、出典の再照合、表の射影は各経路で同じ境界入力を使って確認します。PythonとTypeScriptのDBは統合しません。

ローカルのパスは `DOCLING_DATA_DIR` からの相対パスです。SQLiteはBlobへ保存せず、Wikiの検索索引はコンテンツ正本から再構築します。manifest公開後にDB更新が失敗しても、次の読み取りまたは起動で索引を復元します。embeddingや実行履歴は正本ファイルだけから復元されません。[保存構成と移行](../content-storage.md)を参照してください。

代表的な変更は、OCR profileの追加と検索結果の項目追加です。profileを増やすときはローカル台帳、API契約、画面の選択を一緒に変えます。検索結果の項目を増やすときはOpenAPI、`knowledge-api/src/contracts.ts`、`frontend/src/lib/contracts.ts` と、保存済み結果の再照合を一緒に変えます。未知フィールドは受け取り側で拒否します。

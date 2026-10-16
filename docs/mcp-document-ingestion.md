# MCPによる資料の取り込みと処理管理

既存ナレッジAPIの原本登録・改版・処理ジョブをMCPから操作する。ファイル本体はホストがHTTPでgatewayへ送り、モデルは受け渡しIDを使って登録・差し替えを指示する。保存先は独立ナレッジAPIであり、デスクトップ画面の資料・Wikiとは別である。

## 追加する5つの操作

| MCPツール | 主な業務引数 | 結果 |
| --- | --- | --- |
| `import_document` | `transfer_id`、`idempotency_key` | 文書ID・原本版・ジョブID |
| `replace_document_source` | `transfer_id`、`source_id`、`etag`、`idempotency_key` | 同じ文書ID・新しい原本版・ジョブID |
| `get_document_status` | `source_id` | 現在版、`source_etag`、FTS・意味索引の準備状態等 |
| `reextract_document` | `source_id`、`etag`、`ocr_provider` | 原本の再抽出を受け付けたジョブID |
| `reindex_document` | `source_id`、`etag` | 保存済み抽出から索引を更新するジョブID |

状態確認にはread、その他にはwriteが必要。認証された利用者と許可範囲はgatewayとナレッジAPIが検査し、モデル引数では指定しない。追加5ツールは構造化した結果を返す。既存のジョブ・検索ツールは、構造化結果がなければJSON本文を読む。

## ホストからファイルを転送する

通常のgatewayは18768番ポート。利用者別`mcp_token`をBearerとして送る。ホスト用`connector_token`と混同しない。`file`はバイナリ、`metadata`はJSON文字列、`ocr_provider`は任意のフォーム項目である。

```sh
curl --fail --header "Authorization: Bearer $DOCLING_MCP_TOKEN" \
  --form 'file=@/absolute/path/report.pdf' \
  --form 'metadata={"title":"報告書","collection_id":"documents","source_kind":"document","language":"ja"}' \
  --form 'ocr_provider=local' \
  'http://127.0.0.1:18768/api/v1/document-transfers'
```

応答には`transfer_id`、ファイル名、バイト数、SHA-256、`expires_at`、`operation`、`state`、任意の結果がある。モデルにはこの参照情報を渡し、原本バイナリやホストのローカルパスをMCP引数へ入れない。転送しただけでは文書を登録しない。

`metadata`の保存先は`list_document_scopes`から選ぶ。`SourceMetadata`の必須項目はtitle・collection_id。source_kind省略時はdocument、language省略時はjaとなる。案件や地域がある場合はproject_id・regionも渡す。完全な型は`tool.import_document`ガイドのschemas→transfer_http→multipartのmetadata.contentSchemaで取得できる。既存APIと同じく対応形式はPDF・PPTX・XLSX・DOCX・Markdown・テキスト。原本の検証と変換は既存API・processorが行う。

## MCPから登録する

次は`tools/call`に渡すparamsの形。`TRANSFER_ID`はホストの転送応答から取得し、キーは初回送信前に記録する。

```json
{
  "name": "import_document",
  "arguments": {
    "arguments": {
      "transfer_id": "TRANSFER_ID",
      "idempotency_key": "first-import-key"
    }
  }
}
```

追加5ツールは既存文書ツールと同じく、MCPの業務引数を内側の`arguments`へ入れる。一方、既存`POST /host/tools/{name}`を使う場合は、ホストが認証した`user_id`と業務引数を`HostCall.arguments`へ直接渡す。MCP例の二重の包みをそのままホストAPIへ送らない。

登録応答の`job_id`を既存`get_document_job`で追跡する。`get_document_status`の`fts_ready`が全文検索、`semantic_ready`が意味索引の準備を表す。登録の受付と検索準備完了を区別する。意味索引の設定がなくても、FTSが準備できれば本文検索へ進める。

## 原本を差し替える

先に`get_document_status`で現在の`source_etag`を取得する。metadata全量は元の登録記録、または`get_document`のmetadataから取得する。原本差し替えで管理情報は変更せず、external_keyがあればそれも保持する。

ホストの転送に`source_id`と`etag`を追加する。

```sh
curl --fail --header "Authorization: Bearer $DOCLING_MCP_TOKEN" \
  --form 'file=@/absolute/path/report-v2.pdf' \
  --form 'metadata={"title":"報告書","collection_id":"documents","source_kind":"document","language":"ja"}' \
  --form "source_id=$SOURCE_ID" --form "etag=$SOURCE_ETAG" \
  'http://127.0.0.1:18768/api/v1/document-transfers'
```

この転送は対象と基準版に結び付く。`replace_document_source`へ転送応答のID、同じ`source_id`・`etag`、今回の再送キーを渡す。古い版は412、転送時と異なる対象・基準版・操作は409となる。新しい原本版とジョブIDを受け取り、処理状態を追跡する。

## 再抽出と再索引

`get_document_status`の`source_etag`を`etag`に指定する。`reextract_document`は原本を抽出し直し、`ocr_provider`をlocal・azure_read・disabledから明示する。Azure OCRを選ぶ場合は既存の外部送信・認証・課金設定を使う。モデルからProvider URLや資格情報を指定しない。

`reindex_document`は保存済み抽出本文を使うため、OCRやOffice変換を繰り返さない。抽出未準備では409 `extraction_not_ready`となる。両操作とも受付後の`job_id`で結果を確認する。再抽出中に公開済み根拠が残る場合があるため、文書のreadyだけでジョブ完了を判断しない。

この2操作には既存APIの冪等性キー契約がない。応答喪失時は自動再実行せず、把握しているジョブと文書状態、ホストの実行記録から次の操作を判断する。今回、API・DBへ別の再送管理機構は追加していない。

## 転送の結果確認と保持

ホストは同じ利用者別Bearerで`GET /api/v1/document-transfers/{transfer_id}`を取得できる。

| state | 意味 |
| --- | --- |
| `staged` | バイトを保持し、MCPの登録操作は未開始 |
| `in_progress` | 登録操作の応答を待っている |
| `unconfirmed` | 操作キーを記録したが成功結果を確認できていない |
| `completed` | 登録結果を保持している |
| `expired` | 未完了の転送バイトの期限が切れた |

転送バイトは1時間保持し、成功後は直ちに削除する。結果と再送に必要な記録は7日保持する。期限切れの物理清掃は次の転送・結果確認・取り込み要求で行う。実行中のバイトは清掃しない。gateway終了中の清掃は行わない。

応答喪失時は同じtransfer_id・キーで再送する。gatewayを再起動しても転送と結果は保持する。異常終了した実行予約が残った場合は最大120秒待つ。期限切れの転送や`unconfirmed`を未実行と決めつけない。必要な場合は同じファイル名・内容・metadata・OCR設定を再転送し、元のキーでAPIの再送照合を行う。7日を超えた不明な操作を新しいキーで自動再実行しない。

成功結果の再取得も、現在の利用者、write権限、保存先、文書の存在を確認する。別利用者や資格情報変更後の古い転送は取得できない。既存`get_document_operation`はMarkdown編集用であり、このバイナリ転送には使わない。

## 運用と上限

gatewayは既存どおり1プロセスで動かす。既定の一時保存先は私有gateway設定ファイルと同じディレクトリ内の`document-transfers/`。別の保存先はgateway設定の`transfer_dir`に絶対パスを指定する。再起動・コンテナ再作成で復旧する場合は、この領域を保持する。

保存ディレクトリは所有者だけが読める権限を要求し、新規ディレクトリは0700、ファイルは0600で作る。既存の共有ディレクトリを自動でchmodしない。設定先が私有でない場合は503となる。DBと確定原本は既存ナレッジAPIの領域に保存し、gatewayの転送領域を正本にしない。

1ファイル50 MiB、multipart全体51 MiB、保持する未完了ファイルと転送中要求は合計6件まで、転送記録は10000件までとする。通常のMCP・編集JSON要求の2 MiB上限は変更しない。転送上限は413、受付上限は429、空き容量等の保存失敗は507を返す。既存ナレッジAPIの変換キュー・空き容量制限も別に適用する。

## ガイドと受入試験

`get_tool_guide`で`workflow.import-and-read`、または`tool.import_document`等を取得する。HTTP・MCP・ホストAPIの形と、追加操作の入出力スキーマを確認できる。[転送HTTP契約](../contracts/document-transfers.openapi.json)も配布資源へ同梱する。

```sh
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest \
  tests/test_document_ingestion.py tests/test_document_ingestion_live.py -q
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m docling_desk.operations.tool_guides
```

通し試験はBunと`knowledge-api`の依存を導入した環境で、隔離したSQLite・原本領域、実際のPython processorとナレッジAPI、実Streamable HTTPクライアントを使う。Azure・Embedding・回答生成の外部サービスは呼ばない。実Eumenes固有のファイル添付UIとの接続は、ホストがこの転送契約に対応した段階で確認する。

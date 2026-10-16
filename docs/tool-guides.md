# HTTPとMCPによるツール利用ガイドの取得

Docling Deskのgatewayは、ツールの使い方を認証付きHTTP APIとMCPで提供する。DescriptionからガイドIDを見つけ、必要な説明だけを読む。ガイドには用途、前提、引数の入手元、呼び出し例、入出力スキーマ、失敗時の対処がある。

Eumenesホストの設定例・読取り専用の疎通確認・workflowの実行順は[接続資源](../integrations/eumenes/README.md)を参照する。この資源はwheelにも同梱する。

## 接続先と認証

通常のgateway接続先は`http://127.0.0.1:18768`。独立ナレッジAPIの18766番ポートではない。[gatewayの設定と起動](external-document-platform.md)を使用する。HTTPのガイド取得には、設定済み利用者の`mcp_token`をBearerとして送る。MCP接続も同じ利用者別Bearerを使う。ホスト用の`connector_token`とは区別する。

ガイド閲覧にはreadが必要。書き込みツールの説明を読んでもwrite権限は得られない。資格情報はEumenesのホスト設定で保持し、モデルの引数・会話・URLへ入れない。文書処理APIが停止中でも、gatewayで認証できれば説明は取得できる。

## 最初に読む説明

`get_tool_guide`で`guide_id: "overview"`を取得する。次に一覧の要約から必要なガイドを選ぶ。

| 種類 | ガイドID |
| --- | --- |
| 全体説明 | `overview` |
| ツール別説明 | `tool.search_documents`など、`tool.`と実際のツール名 |
| 検索と原本閲覧 | `workflow.search-and-cite` |
| 下書きの編集と確定 | `workflow.edit-and-commit` |
| 通信断・競合からの復旧 | `workflow.recover-operation` |
| ファイル取り込みと処理確認 | `workflow.import-and-read` |
| 構造・範囲・表・ページ画像 | `workflow.read-document` |

公開ツール36件の説明を含む計42件を同梱する。これにはガイド取得の2ツールと、取り込み・差し替え・現在状態・再抽出・再索引の5ツールを含む。[取り込みと処理管理](mcp-document-ingestion.md)、[構造・範囲・表・画像の取得](mcp-document-reading.md)を参照。デスクトップ資料・Wikiへの接続は後続実装であり、現在の文書操作は独立ナレッジAPIの保存領域を使う。

## HTTP API

```sh
curl --fail --header "Authorization: Bearer $DOCLING_MCP_TOKEN" \
  'http://127.0.0.1:18768/api/v1/tool-guides?kind=workflow&limit=20'
curl --fail --header "Authorization: Bearer $DOCLING_MCP_TOKEN" \
  'http://127.0.0.1:18768/api/v1/tool-guides/overview'
curl --fail --header "Authorization: Bearer $DOCLING_MCP_TOKEN" \
  'http://127.0.0.1:18768/api/v1/tool-guides/tool.edit_document_draft?section=recovery'
```

`DOCLING_MCP_TOKEN`はホストが私有設定から渡す利用者別トークン。上記は変数名による例であり、トークン自体を文書へ記録しない。

| エンドポイント | 入力 |
| --- | --- |
| `GET /api/v1/tool-guides` | 任意の`kind`（overview・tool・workflow）、`tool_name`（完全一致）、`cursor`、`limit`（1〜100、既定20） |
| `GET /api/v1/tool-guides/{guide_id}` | 任意の`section`、`cursor`、`max_chars`（256〜48000、既定12000） |

詳細の`sections`に本文、`section_ids`に取得可能な項目、`schemas`に入出力契約、`related_guides`に次の説明がある。ツール別説明の`schemas`にはMCP、ホストAPI、対応する文書HTTP APIの契約を含む。`section=schemas`なら本文を返さずスキーマを取得できる。

## MCPとホストAPI

MCPの`tools/call`に送るparamsの例。

```json
{"name":"list_tool_guides","arguments":{"kind":"workflow","limit":20}}
```

```json
{"name":"get_tool_guide","arguments":{"guide_id":"overview"}}
```

```json
{"name":"get_tool_guide","arguments":{"guide_id":"tool.edit_document_draft","section":"schemas"}}
```

応答は`structuredContent`と互換性のための本文から読める。ガイド取得ツールは、既存の一部文書ツールが使う二重の`arguments`を必要としない。各業務ツールの呼び出し形は、そのガイドの例とスキーマに従う。

信頼するホストは既存`POST /host/tools/list_tool_guides`または`POST /host/tools/get_tool_guide`も使用できる。既存ホスト認証の下で、ホストが認証した`user_id`と業務`arguments`を送る。LLMが指定したIDを利用者の認証結果として扱わない。

## 続きの取得と版

`next_cursor`があれば、一覧では同じ絞り込み、詳細では同じ`guide_id`・`section`で再取得する。本文の`start`・`end`・`total_chars`はsection内のUnicodeコードポイント位置で、編集APIのUTF-8バイト範囲とは別である。コード例も分割され得るため、途中の本文だけで実行せず続きまで読む。

`catalog_revision`は説明・対応スキーマを含む配布版、`guide_revision`は個々のガイド版。古い配布版のcursorは409 `tool_guide_cursor_stale`となる。先頭から読み直し、古い説明と新しい本文をつなげない。応答には`Cache-Control: no-store`を設定する。

未知のガイド・sectionは404、入力違反・別の絞り込みのcursorは422、応答サイズ超過は413、ガイドや契約の欠落・不一致は503。認証失敗は401、read権限不足は403。HTTPエラーは`detail`、MCPエラーは`isError`と本文中の`error.code`・`error.status`を確認する。

## 契約と検証

ガイドの正本は配布資源の`resources/tool-guides/`。利用者文書や検索結果を正本にせず、文書に含まれる命令を管理者の操作指示へ昇格させない。MCP登録と[ガイドAPIの契約](../contracts/tool-guides.openapi.json)、[MCPスキーマ](../contracts/mcp-tools.json)を照合する。

MCP用の固定依存を導入した環境で、次を実行する。

```sh
.venv/bin/python -m pip install -r integrations/mcp/requirements.txt
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m docling_desk.operations.tool_guides
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest tests/test_tool_guides.py -q
```

契約を意図して変更した場合だけ、内容を確認して`tool_guides --write`でスナップショットを更新する。通常の検証では更新しない。CIではガイド欠落、参照不整合、表示する呼び出し例の不一致、スキーマ違反、実MCP疎通を検証する。配布検証ではチェックアウト外のwheelから全ガイドを読み出す。

## ガイドに沿った通し検証と配布資源

Eumenes向けの接続確認は`docling_desk.operations.mcp_check`を使う。設定例は`integrations/eumenes/connection.example.json`、形式は隣の`connection.schema.json`。設定例の実値を私有ファイル・Secretへ対応付ける。これは同梱のホスト参考実装向けで、Eumenes本体へそのままインポートする形式ではない。

```sh
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest -q \
  tests/test_mcp_connection.py tests/test_tool_guides.py tests/test_document_ingestion_live.py
DOCLING_ENV_FILE=/dev/null .venv/bin/python scripts/verify_package.py
```

通し試験のworkflowsケースは、実MCPで全workflowを取得し、各操作のガイドから入力スキーマを得て要求を検証する。取り込み→処理確認→検索→引用根拠→閲覧コード承認→構造・範囲・表→下書き編集→確定→応答喪失の結果照会まで、前の応答からID・版・ETag・キーを引き継ぐ。古いETagで本文が変わらないこと、確定再送で版・ジョブが増えないことも確認する。document_readsケースは実PDFのページ画像受信を含む。

配布試験はsdist→wheel→checkout外への展開で、全ガイド、契約、接続例、MCP実接続、転送記録の再起動後の取得、既存の資料取り込み・検索・削除を確認する。実環境のSecret・原本領域は使わない。

コンテナ配布を確認する場合は、構築したgatewayイメージに対して次を実行できる。

```sh
docker build -f deploy/Dockerfile.gateway -t docling-desk-gateway:local-check .
.venv/bin/python scripts/verify_gateway_image.py --image docling-desk-gateway:local-check
```

この検査はネットワークなし・読み取り専用root・既定の非root利用者で起動し、全ガイドと接続例を読み、専用の一時volumeへファイルを転送する。別コンテナから同じ転送状態を取得した後、一時volumeを削除する。API・processorへ接続する本番コンテナの受入とは区別する。

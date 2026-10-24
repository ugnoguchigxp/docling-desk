# HTTPとMCPによる文書編集の接続手順

独立ナレッジAPIの文書を、HTTP、MCP、ブラウザーの編集画面から扱える。Markdown原文と確定版は既存APIのSQLite・原本領域へ保存し、下書きは同じSQLiteへUTF-8のバイト列で保存する。既存デスクトップ画面とWikiの保存領域は変更しない。

原文を保持する見たまま編集は段落・見出し・強調・リスト・リンクの編集を基本とし、未対応の構文を保護ブロックで表示する。frontmatter、コード、Mermaid、数式、HTML・MDX、独自ブロック、参照リンクなどはソース編集で変更できる。HTML・MDX・コードは実行しない。編集部品はPlateだが、APIにPlate JSONは送らず、Markdown原文だけを送る。

## 起動と認証設定

APIとprocessorの基本設定は [knowledge-api.md](knowledge-api.md)、閲覧仲介は [viewer-embedding.md](viewer-embedding.md) を使用する。APIの入口は通常18766、gatewayの入口は18768。モデル資源を使うのはprocessorと設定済みProviderであり、gatewayは原本領域・SQLite・モデルの資格情報を持たない。

ローカルの準備は次の順で行う。

```sh
cd knowledge-api
bun install --frozen-lockfile
bun run setup:local
cd ..
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
pnpm --dir frontend export:static
uv pip install --python .venv/bin/python -r integrations/mcp/requirements.txt
```

[clients.example.json](../integrations/documents/clients.example.json) の2クライアントを、既存の私有 `clients.json` に追記する。閲覧用はreadだけ、編集用はread・writeを割り当てる。トークンと署名鍵はそれぞれ別の乱数へ置き換え、既存クライアントを上書きしない。

[gateway.example.json](../integrations/documents/gateway.example.json) をGit管理外へコピーし、API登録と対応する資格情報・issuer・kidを設定する。APIに登録した範囲の部分集合を利用者ごとに設定し、利用者別のMCP Bearerを別々に発行する。書き込み利用者には `actions: [read, write]` と `api_client: editor` を設定する。省略時は従来どおりreadだけである。

例の `local-user` は付属CLIの確認用IDに合わせてある。本番ではホストが認証した安定した利用者IDへ置き換える。actorとscopeはホストの認証結果から決定し、モデルのツール引数やブラウザーの申告から権限を作らない。長期トークンと署名鍵はサーバーのSecretまたはアクセス権を制限したファイルに置き、本文・モデル文脈・ブラウザーへ渡さない。

```sh
VIEWER_CONFIG_FILE=/absolute/private/path/gateway.json \
  .venv/bin/python -m uvicorn docling_desk.viewer.gateway:app_factory --factory \
  --host 127.0.0.1 --port 18768 --workers 1 --no-access-log
```

APIへは既存のBearerとHS256 actor JWTを使用する。`aud=docling-desk-api`、登録済みkid、最大300秒の期限、登録範囲の部分集合という規則は共通である。gatewayは要求ごとに設定を読み直し、MCPは同じトークンの利用者を呼び出し前後に再確認する。APIのコンテナへ環境変数として渡したクライアント設定を変更した場合は、同期・再作成が必要となる。

## HTTPの編集契約

正式な入出力は [OpenAPI](../contracts/knowledge-api.openapi.json)。更新では対象のETagを `If-Match`、作成・編集・確定・添付では呼び出し元が記録したキーを `Idempotency-Key` へ渡す。再送は同じキー・同じ要求を使う。

| 操作 | 入口 |
| --- | --- |
| 許可範囲とaction | `GET /api/v1/scopes` |
| 認可された一覧 | `GET /api/v1/sources?offset=0&limit=20` |
| 原文と現在版 | `GET /api/v1/sources/{source_id}/body?revision={source_revision}` |
| Markdownの直接確定 | `POST /api/v1/markdown-sources` |
| メタデータ更新 | `PATCH /api/v1/sources/{source_id}/metadata` |
| 下書き作成と一覧 | `POST /api/v1/drafts`、`GET /api/v1/drafts` |
| 下書き取得 | `GET /api/v1/drafts/{draft_id}` |
| 全文更新と部分編集 | `PUT /api/v1/drafts/{draft_id}`、`PATCH /api/v1/drafts/{draft_id}` |
| 確定 | `POST /api/v1/drafts/{draft_id}/commit` |
| 結果確認 | `POST /api/v1/operations/lookup` |
| 添付登録と下書きからの取得 | `POST /api/v1/drafts/{draft_id}/attachments`、`GET /api/v1/drafts/{draft_id}/attachments/{attachment_id}` |
| 確定版の添付一覧と取得 | `GET /api/v1/sources/{source_id}/attachments`、`GET /api/v1/sources/{source_id}/attachments/{attachment_id}` |
| 大きな添付のバイナリ取得 | `GET /api/v1/sources/{source_id}/attachments/{attachment_id}/content` |
| 検索と根拠、状態と再試行 | 既存search・context・jobs・retry・reindex |

Markdownのbodyは原文文字列。APIのtitleは管理メタデータであり、frontmatterのtitleを自動で書き換えない。PDF・Officeのbody取得は `content_kind=extracted_text`、Markdownは `original_markdown` として区別する。PDF・Officeの原本をMarkdownへ上書きする操作は提供しない。

新規下書きの要求例は次のとおり。初回送信前に冪等性キーを記録する。

```json
{"body":"# 記事\n\n本文。\n","metadata":{"title":"記事","collection_id":"documents","source_kind":"wiki","language":"ja"}}
```

既存文書から作る場合は、取得した `source_id`・`source_revision`・文書ETagを `source_etag` に指定する。bodyを省略すると、その保存版のMarkdownと添付の参照を引き継ぐ。

下書きの取得応答には `draft_id`、`source_id`、基準の保存版とETag、body、metadata、attachments、version、etag、sha256、stateがある。PUTは `body`、任意のmetadata・添付ID一覧を送る。更新後のETagを次の要求へ渡し、以前のETagを使い回さない。

部分編集は次の形で送る。`base_sha256` は取得した下書きのハッシュである。

```json
{"base_sha256":"取得したsha256","edits":[{"expected":"完全一致する原文","replacement":"変更後の文"}]}
```

expectedが0件または複数一致なら変更しない。範囲を指定する場合は `start`・`end` を追加し、0始まりのUTF-8バイト範囲 `[start, end)` と、その範囲に完全一致するexpectedを指定する。JavaScriptの文字列添字やUTF-16のオフセットをそのまま渡さない。複数編集は同じ基準本文に対して全件検証し、重複・原文不一致が一つでもあれば全件を適用しない。

確定には下書きETagをIf-Match、基準文書のETagをbodyの `source_etag` に指定する。新規文書では `source_etag: null` を明示する。下書きが更新済みでも、基準文書が改版済みでも拒否する。確定済み下書きの再編集は、新しい下書きとして開始する。

確定応答の文書ID、原本版、source_etag、sha256、draft_id、draft_version、job_idは保存操作の結果である。検索は別の `status` で確認する。本文保存を成功したと報告した後、抽出やEmbeddingの失敗でそれを取り消さない。

| エラー | 扱い |
| --- | --- |
| 428 | ETagまたは冪等性キーが必要 |
| 412 | 基準が古い。入力を保持し、最新状態を取得して明示的に再編集する |
| 409 | 原文不一致・複数一致・本文ハッシュ不一致・冪等性衝突など。codeで区別する |
| 401・403・404 | 認証・許可範囲・対象の存在を確認。範囲外の本文は返さない |
| 413・422 | サイズ・入力・UTF-8・編集範囲などを修正する |
| 429・503 | 同じ操作のキーを保持し、結果照会または待機後の再送を行う |

付属CLIでの確認には `knowledge-api/scripts/client.ts` を使える。actorは確認用の `local-user` 固定で、設定ファイルの先頭クライアントを使う。編集用クライアントだけを入れた私有の確認設定を `KNOWLEDGE_CLIENTS_FILE` で指定する。

```sh
cd knowledge-api
bun scripts/client.ts POST /api/v1/drafts draft-create.json --idempotency CREATE_KEY
bun scripts/client.ts GET /api/v1/drafts/DRAFT_ID
bun scripts/client.ts PATCH /api/v1/drafts/DRAFT_ID draft-edit.json --etag '取得した下書きETag' --idempotency EDIT_KEY
bun scripts/client.ts POST /api/v1/drafts/DRAFT_ID/commit commit.json --etag '更新後の下書きETag' --idempotency COMMIT_KEY
```

## 通信断後の結果確認

成功した操作はclient・actor・操作種別・対象・キーごとに7日間再取得できる。プロセス再起動でも消えない。応答だけを失った場合は、同じ要求を再送するか、lookupへ次を送る。

```json
{"operation":"commit","target_id":"DRAFT_ID","key":"COMMIT_KEY"}
```

operationはcreate・draft・update・edit・commit・metadata・attachment。新規文書のcreateと新規下書きのdraftはtarget_idを省略し、既存文書からのdraftではsource_id、下書き更新・編集・確定・添付ではdraft_id、metadataではsource_idを指定する。

同じHTTP/MCP操作として確認するには、同じclientとactorが必要である。別の利用者は同じ文書を編集できても、他者の冪等性キーの結果は確認できない。現在の許可範囲・削除状態は再確認する。保持期限外の `operation_unknown` を未実行と判断し、新しいキーで自動再送しない。文書と版を確認してから次の操作を決める。

## MCPの接続例

Eumenesホスト向けの設定例・導入手順・読取り専用の接続確認は[接続資源](../integrations/eumenes/README.md)を参照する。

ツールの前提・手順・呼び出し例は、同じgatewayの[利用ガイドAPIとMCP](tool-guides.md)から取得できる。接続後に`get_tool_guide`へ`guide_id: "overview"`を渡す。HTTPでは利用者別MCP Bearerで`GET /api/v1/tool-guides`と`GET /api/v1/tool-guides/{guide_id}`を使用する。

ファイルの登録・差し替えと再抽出・再索引は[MCPによる取り込みと処理管理](mcp-document-ingestion.md)を参照。ホストがファイルを転送し、MCPには利用者と用途に結び付いたtransfer_idを渡す。

Streamable HTTPの接続先は `public_url + /mcp/`。利用者別BearerはホストのMCP接続設定へ置き、ツール引数へ渡さない。ツールのargumentsは公開OpenAPIから作った項目情報を持ち、実際の認可・更新は同じHTTP APIで検査する。

```python
import json
from pathlib import Path
from uuid import uuid4
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# このファイルはホストの私有設定。利用者はホストの認証結果から決める。
config = json.loads(Path("/absolute/private/path/gateway.json").read_text())
user = "local-user"
async with streamablehttp_client(
    config["public_url"] + "/mcp/",
    headers={"Authorization": "Bearer " + config["users"][user]["mcp_token"]},
) as (read, write, _):
    async with ClientSession(read, write) as mcp:
        await mcp.initialize()
        await mcp.call_tool("list_document_scopes", {})
        create_key = str(uuid4())  # ホストが初回送信前に記録し、再送で再利用する。
        result = await mcp.call_tool(
            "create_document_draft",
            {
                "arguments": {
                    "body": "# 記事\n\n本文。\n",
                    "metadata": {
                        "title": "記事",
                        "collection_id": "documents",
                        "source_kind": "wiki",
                    },
                    "idempotency_key": create_key,
                }
            },
        )
        if result.isError:
            raise RuntimeError("下書き作成を確認できないため、結果照会へ進む")
        draft = result.structuredContent or json.loads(result.content[0].text)
        # 続く更新にはdraft_id、etag、sha256を使う。
```

検索・根拠・閲覧の既存ツール名は維持する。追加はlist_documents、get_document、create_markdown_document、update_document_metadata、list_document_drafts、create_document_draft、get_document_draft、update_document_draft、edit_document_draft、commit_document_draft、get_document_operation、添付の登録・一覧・取得、ジョブ取得・再試行。追加文書ツールは `arguments` に業務引数をまとめ、ETagはetag、冪等性キーはidempotency_keyで指定する。metadata更新だけはHTTPと同じmetadataの各フィールドをarguments直下へ指定する。

## 編集と閲覧の埋め込み

編集入口は `/editor?draft_id=DRAFT_ID`、保存版から始める場合は `/editor?source_id=SOURCE_ID`。新規作成と一覧選択は `/editor?scope=` に、URLエンコードした許可scopeのJSONを指定する。scopeの指定だけで認証されることはない。

画面が示す8文字の接続コードを、認証済みMCPの `connect_document_editor(code)`、または信頼するホストの `/host/tools/connect_document_editor` で承認する。編集用承認は利用者と対象を検証し、read-only接続では拒否する。コードは3分、編集セッションは5分。セッションをモデル文脈、URL履歴、localStorageへ保存しない。接続後は対象下書きへ固定され、別文書を編集するには再接続する。

編集画面は下書きだけを自動保存し、保存版の確定は明示操作にする。人とAIの競合では先の変更を上書きせず、入力を保持して最新下書きへの切替を案内する。応答喪失時の再試行は元の要求とキーを使う。結果が未確認の間は別の保存操作を始めず、下書き更新の再送結果で待機中の追加入力を上書きしない。一般的な通信エラーでは入力を消さず、認可失効時は表示中の本文を除く。

閲覧は既存の `request_document_view` と `connect_document_view` を利用する。文書ID、原本版、根拠版、任意のlocationだけを履歴へ保存し、一時セッションは保存しない。Markdownの `location.original_range` は原文SHA-256とUTF-8範囲を持つ。位置が確実に対応付けられない場合は文書先頭から開く。古い原本版・根拠版は拒否し、新しい検索結果から開く。

iframeには長期API tokenや署名鍵を渡さない。Eumenesは返された閲覧入口と参照を使用でき、編集Artifactでは同じ下書きIDを使う。接続承認の利用者IDはEumenesが認証したIDに限定する。iframeが表示されたことやOriginだけを認証成功として扱わない。

## 添付の参照と上限

添付は不変のIDとSHA-256を持ち、Markdownには `knowledge-attachment:UUID` を保存する。差し替えは新しいIDとし、旧版のファイルを上書きしない。下書きから確定版、次の下書きへ同じ参照を引き継ぐ。本文と添付の所属・認可を両方検査し、取得時にも保存サイズとSHA-256を照合する。下書きへの添付は最大100件で、重複IDを拒否する。

JSON要求・応答は2 MiB以下。本文保存時は結果照会・本文再取得の応答にも収まることを検査し、上限超過なら保存前に413を返す。MCPと編集画面の添付内容取得は1 MiB以下で、base64はJSON制限に含める。HTTPのmultipart登録はfile一つだけで最大50 MiB、確定版の大きな添付はcontent入口からバイナリ取得する。画像プレビューは認可されたPNG・JPEG・GIF・WebPだけを取得し、一時的な表示データをMarkdownへ書き戻さない。閲覧画面にも現在の確定版の添付一覧と画像を表示し、短期viewerセッションで同じ所属・認可を検査して取得する。HTML・SVGなどは文書内で実行せず、取得時はダウンロードとして扱う。

## 処理休止とLARMでの運用

APIと永続領域は、重い処理の停止と分けて管理する。`KNOWLEDGE_CONTROL_TOKEN` をSecretで設定した場合、非公開の `/internal/v1/processing` に管理Bearerを付けてGET・PUTできる。PUTのbodyは `{"enabled":false}` またはtrue。pauseは新しい処理を止め、実行中の処理は完了を待つ。GETのrunningが0になってからprocessorを止める。管理tokenは文章編集のMCPへ公開しない。

休止状態はDBへ保存する。再起動後も休止を維持し、trueで未開始ジョブを再開する。通常停止で中断した処理はfailed/interruptedと明示retry、異常終了したローカル処理は既存リースの期限後の回復を維持する。外部課金や受領不明の要求を無条件に再送しない。画面を閉じてもジョブを取り消さない。

processor停止中でも、保存済み本文・下書き・Markdown確定は利用できる。確定時に抽出キューが満杯でも本文を保持し、検索反映を待機させる。readyはDB・設定・ディスクと、processing_enabled・markdown_editing・extractionなどのcapabilityを返す。Embedding接続の設定があることと、実通信の成功は別である。

gatewayのコンテナ用には [Dockerfile.gateway](../deploy/Dockerfile.gateway) と [compose.gateway.yml](../deploy/compose.gateway.yml) を用意した。[コンテナ用設定例](../integrations/documents/gateway.container.example.json)では転送先を専用volumeへ指定する。gateway.jsonのapi_urlを非公開の `http://api:18766`、public_urlをブラウザーから届く入口に設定し、必要な内部Hostをmcp_hostsへ明示する。

```sh
docker compose -f deploy/compose.knowledge.yml -f deploy/compose.gateway.yml --profile gateway up -d --build
docker compose -f deploy/compose.knowledge.yml -f deploy/compose.gateway.yml --profile gateway stop
```

gatewayは初期版では1プロセスで動かす。Secretファイルは設定したcontainer UIDが読める所有者と権限にする。必要に応じて `KNOWLEDGE_GATEWAY_UID`・`KNOWLEDGE_GATEWAY_GID` を指定する。再起動で短期セッションは失効するが、DBに保存した下書き・版・操作結果・ジョブは残る。通常停止でvolumeを削除しない。

バックアップは既存の手順でAPIとprocessorを止め、stateとartifactsを同じ時点で保存する。下書き・操作結果・ジョブはSQLite、添付はartifactsのsources内に含まれる。起動時は登録済みファイルを保持し、1時間以上経過した未登録の版・添付だけを整理する。起動時にDBをschema version 2へ更新するため、更新前にバックアップし、古いAPIへ戻す場合は対応するstateとartifactsを一緒に復元する。

Embeddingは既存の保存設定でURL・profile・次元数を指定する。接続先・モデル版・次元数・処理仕様が変わると旧ベクトルを検索に混ぜず、reindexで再生成する。Embedding未設定・失敗でも利用可能なFTSとeffective_mode・degraded_reasonsを返す。LARMのサービス登録、Secret供給、正式profile取得、資源の起動停止APIへの接続はLARM側の作業であり、この変更で正式契約を推測したり本番配備したりしていない。

## 検証と外部側の残作業

[実装レビュー](../qa/external-document-platform/review.md) に計画照合・修正と最新の再検証結果を記載する。[検証記録](../qa/external-document-platform/verification.md) に、既存ベースライン、追加試験、実HTTP/MCP・ブラウザー・実Doclingによる通し試験、異常終了と復旧を記録する。再現入口は次のとおり。

```sh
cd knowledge-api
bun run typecheck && bun run lint && bun run contract:check && bun test
cd ..
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend test:run
pnpm --dir frontend build && pnpm --dir frontend export:static
DOCLING_ENV_FILE=/dev/null .venv/bin/python qa/external-document-platform/acceptance.py
```

通し試験は新しい一時領域、合成PDF・Word・Markdownと画像、専用資格情報を使い、終了時にそのプロセス・領域を片付ける。実LLM・実LARMは使用しない。

Eumenes側には、認証済み利用者とactor・scopeの対応、HTTP/MCP接続、Artifactへのviewer・editor配置、短期接続承認、競合の再編集、LARMのLLMによる回答生成、外部公開操作が残る。LARM側にはサービス登録、永続領域とSecret、ヘルス監視、処理休止・再開、正式Provider接続と実運用試験が残る。外部ブログへのMarkdown変換・添付公開・投稿は対象外である。

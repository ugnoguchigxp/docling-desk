# Docling Desk APIの起動と利用

このプロジェクトに、外部アプリのサーバーから使えるナレッジAPIを追加した。TypeScriptのAPI・SQLite索引と、既存Doclingを呼ぶPython処理サービスを別コンテナで動かす。既存デモの画面・`data/`・資料内解説用の索引とは保存先が独立している。

APIのDBには一つのAPIプロセスだけが書き込める。別プロセスはスキーマ初期化前に拒否され、期限付きリースが失効しても稼働中のWriterから所有権を奪えない。[SQLiteの書き込みプロセス](sqlite-writer.md) に詳細を記載した。

登録、差し替え、削除、検索、親文脈取得、ジョブ確認、任意の回答生成を実装した。検索はFTS・semantic・hybridを選べる。Embeddingと回答生成は管理者が設定するHTTP gateway経由で有効にする。未設定でも登録とFTS検索は利用できる。WikiのMarkdownは登録できるが、Wiki編集画面、利用先アプリ/従来システムとの接続、既存資料の移行、Azure OCRの新APIへの接続はこの実装の対象外。

正式な入出力は [OpenAPI契約](../contracts/knowledge-api.openapi.json) に保存してある。稼働中の `GET /api/v1/openapi.json` でも同じ契約を取得できる。設計方針は [API作成計画書](../knowledge-api-implementation-plan.md) を参照。

## Dockerで起動する

Bun 1.4.2、Docker、Docker Compose 2.24以降を用意し、リポジトリのルートで実行する。Doclingのサブモジュールを初期化しておく。ビルド時は既存Dockerfileのモデル・依存取得を行う。文書処理時のモデルはコンテナ内にあるものを使う。

```sh
git submodule update --init --recursive
cd knowledge-api
bun install --frozen-lockfile
bun run setup:local
cd ..
docker compose -f deploy/compose.knowledge.yml up -d --build --wait
curl --fail http://127.0.0.1:18766/health/ready
```

`setup:local` は `.knowledge-api/` に権限を制限した設定ファイルを作る。既存ファイルは上書きしない。`clients.json` がクライアント設定の正本、`runtime.env` がローカル実行用、`container.env` がAPIコンテナ用、`worker.env` が処理コンテナ用である。実際のトークンや署名鍵を画面に出力しない。これらのファイルはGit管理対象外。

Azure OCRは、VM上のリポジトリ直下の`.env`へ`DOCLING_AZURE_OCR_ENDPOINT`と`AZURE_DOCUMENT_INTELLIGENCE_API_KEY`の2項目を設定すれば処理コンテナへ渡ります。`.env`はAPIコンテナやイメージへ含めません。処理コンテナは`.env`、`worker.env`の順で読み、同じ項目は`worker.env`を優先します。マネージドIDを使う場合はキーを設定せず、ユーザー割り当てIDの場合だけ`DOCLING_AZURE_OCR_CLIENT_ID`も設定します。設定同期は、OCR設定やAPIの月間送信上限など手動追記した項目を保持します。任意の`.env`の読み込みには[Compose 2.24以降の`env_file.required`](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)を使っています。

APIの入口は `127.0.0.1:18766`。処理サービスにはホスト公開ポートがない。原本・処理結果は `knowledge-artifacts`、SQLiteは `knowledge-state` の名前付きボリュームに保存する。設定ファイル内のbase64は暗号化ではなく、コンテナへの受け渡し形式である。VMではファイルのアクセス権とSecretの管理を行う。

```sh
docker compose -f deploy/compose.knowledge.yml ps
docker compose -f deploy/compose.knowledge.yml stop
docker compose -f deploy/compose.knowledge.yml start --wait
# コンテナだけを削除する。保存ボリュームは残る。
docker compose -f deploy/compose.knowledge.yml down
```

通常の停止に `down -v` を使うと資料とDBも削除される。APIプロセスはDBごとに1つで運用する。異常終了直後の再起動は、古いプロセスの所有期限が切れるまで最大30秒待つ場合がある。バックアップはAPI・処理サービスを止めて、SQLiteと原本の両ボリュームを同じ時点で取得する。

## メインアプリからの認証

ブラウザーから直接呼ばず、利用先アプリのサーバーが利用者を認証・認可した上でAPIへ問い合わせる。Originの付いた要求は拒否する。検索範囲をブラウザーの申告だけから決めない。

通常の `actor` クライアントは、次の2つを要求する。

| ヘッダー | 内容 |
|---|---|
| `Authorization` | `Bearer <clients.jsonのクライアントトークン>` |
| `X-Knowledge-Actor` | メインアプリが署名した利用者・権限付きJWT |

JWTヘッダーは `alg=HS256`、`typ=JWT`、登録済みの `kid`。署名鍵はAPIとメインアプリのサーバー間で共有する。本文は次の形で、時刻はUnix秒、有効期間は最大300秒。

名称変更後のJWTの `aud` は `docling-desk-api` です。外部アプリから署名する場合も、この値に揃えてください。

```json
{
  "iss": "local-main",
  "aud": "docling-desk-api",
  "client_id": "local-main",
  "sub": "利用者の安定したID",
  "iat": 1791000000,
  "exp": 1791000300,
  "scopes": [
    {"collection_id": "wiki"},
    {"collection_id": "assessment", "project_id": "demo", "region": "JP"}
  ]
}
```

JWTで許可する範囲は、そのクライアントに登録された範囲の部分集合に限る。`collection_id`・`project_id`・`region` は完全一致で、ワイルドカードはない。`actions` はクライアントごとに `read`・`write`・`answer` を設定する。サービス間の定期取り込み専用に `batch` モードも用意した。batchはトークンだけで、登録済み範囲をそのまま使うため、用途に応じて狭い範囲で別クライアントを作る。

初期設定の `local-main` はローカル確認用。利用先のアプリごとに別のトークン・署名鍵・issuerを割り当てる。設定を変更した後は、次の操作でコンテナ用設定を生成し直す。ローカル実行のAPIは `clients.json` を要求ごとに読み、鍵・トークン・権限の変更を再確認する。コンテナは起動時の設定を使うので再作成が必要。

```sh
cd knowledge-api
bun scripts/sync-container-config.ts
cd ..
docker compose -f deploy/compose.knowledge.yml up -d --force-recreate --wait
```

コードを更新した場合は、再起動だけではイメージ内の実装は更新されません。VMで最新コードを取得してから、次の操作でAPI・処理コンテナを再ビルド・再作成します。`.env`や`worker.env`の変更だけの場合もコンテナの再作成が必要です。

```sh
docker compose -f deploy/compose.knowledge.yml up -d --build --force-recreate --wait
```

Azure VMでも同じCompose構成を使える。別VMから使う際は、VM内の入口の前にHTTPSの認証付きAPIを通すリバースプロキシを配置し、ネットワークで接続元を限定する。同じVMに別Composeで置くメインアプリには、共通の非公開ネットワークやホスト側のプロキシからAPIへの経路を用意する。この変更だけでクラウドへの配置は行っていない。

## 資料を登録して検索する

以下はリポジトリルートを起点とするローカル確認例。資格情報をコマンドに貼らず、付属クライアントが `.knowledge-api/clients.json` の先頭クライアントを読み、署名して送る。別接続先は `KNOWLEDGE_API_URL`、別クライアント設定は `KNOWLEDGE_CLIENTS_FILE` で指定できる。

`knowledge-api/metadata.json` の例：

```json
{"title":"在庫連携の確認メモ","collection_id":"wiki","source_kind":"wiki","language":"ja"}
```

`knowledge-api/search.json` の例：

```json
{"query":"在庫連携","scope":{"collection_ids":["wiki"]},"mode":"text","limit":10,"timeout_ms":3000}
```

```sh
cd knowledge-api
bun scripts/client.ts POST /api/v1/sources metadata.json --upload /absolute/path/notes.md
# 登録応答のjob_idで状態を確認し、fts_readyになったら検索する。
bun scripts/client.ts GET /api/v1/jobs/JOB_ID
bun scripts/client.ts POST /api/v1/search search.json
```

HTTPでの登録は `multipart/form-data`、フィールドは `file` とJSON文字列の `metadata` の2つ。`Idempotency-Key` が必須で、同じキー・利用者・内容なら同じ登録結果を7日間返す。付属クライアントはキーを自動生成する。再送時は `--idempotency SAME_KEY` で最初と同じキーを渡す。内容を変えて同じキーを使うと409。

アセスメントの登録には `collection_id=assessment` と案件・地域の両方を指定する。検索にも案件・地域が必要。Wikiと同時に検索する例：

```json
{
  "query": "在庫照合の判断根拠",
  "scope": {"collection_ids":["wiki","assessment"],"project_id":"demo","region":"JP"},
  "mode": "hybrid",
  "filter": {"source_kinds":["wiki","document"],"languages":["ja"],"include_past_revisions":false}
}
```

検索応答には候補、順位、短い本文、出典位置、原本URL、資料・原本版・根拠版・親文脈のIDを返す。FTSは日本語trigramと短語の補完検索、semanticは許可範囲のベクトルに対するcosine検索、hybridは双方の順位をRRFでまとめる。検索対象の制限を候補選定より先に行う。

`status`、`effective_mode`、`degraded_reasons`、`index_state` を確認する。Embedding未設定・未完了・障害時にはFTSへ切り替え、部分結果であることを明示する。索引待ちは503、検索時間超過で結果なしは504、処理済みで一致なしは200と空配列になる。削除中・権限外の本文は返さない。同じ検索本文を持つ別資料も、それぞれの出典候補を保持する。短語の補完検索は許可範囲を走査し、本文・検索語の幅と大文字小文字をそろえて照合する。

メインアプリのエージェントは、検索結果のIDを使って `POST /api/v1/context` に親文脈を求める。検索用の短い本文だけで回答を作らない。

```json
{
  "retrieval_id": "検索応答のretrieval_id",
  "references": [{
    "source_id": "候補のsource_id",
    "source_revision": "候補のsource_revision",
    "evidence_revision": "候補のevidence_revision",
    "context_id": "候補のcontext_id"
  }],
  "max_chars": 80000
}
```

検索で返した参照だけを取得できる。検索IDは10分間有効で、利用者・クライアント・権限設定に結び付く。更新済みの参照は409なので再検索する。複数参照は一括で認可・版を確認し、上限超過は413。本文を無言で切り詰めない。表の構造、Doclingの要素参照、座標、ページ・スライド・シート・行範囲を保持する。ページ不明のWordやテキストに仮のページ番号は付けない。

原本URLも同じ認証が必要。メインアプリは引用元の取得を自身のサーバー経由で仲介し、ブラウザーへ署名鍵やクライアントトークンを渡さない。

## 更新・削除・再索引

| 操作 | API | 主な条件 |
|---|---|---|
| 現在の版・状態・ETag | `GET /api/v1/sources/{id}` | 読み取り権限 |
| 原本 | `GET /api/v1/sources/{id}/content` | `revision`で版指定可能。過去版は `include_past_revisions=true` |
| 差し替え | `POST /api/v1/sources/{id}/revisions` | multipart、`If-Match`、`Idempotency-Key` |
| 削除 | `DELETE /api/v1/sources/{id}` | `If-Match` |
| 再索引 | `POST /api/v1/sources/{id}/reindex` | `If-Match`、抽出済みの現在版 |
| 状態確認 | `GET /api/v1/jobs/{id}` | 資料の読み取り権限 |
| 失敗した処理を再開 | `POST /api/v1/jobs/{id}/retry` | 書き込み権限、現在版の抽出／削除ジョブ |

付属クライアントは資料取得時にETagを標準エラーへ表示する。差し替え・削除・再索引では `--etag '取得したETag'` を指定する。ETagなしは428、古いETagは412。差し替えのmetadataは登録時と同じ内容を渡す。今回のAPIは本文ファイルの差し替えであり、タイトル変更・移動・権限変更用の編集APIは未追加。

差し替え時に現在版を切り替え、旧版を通常検索と古い参照から直ちに外す。新しい原本は不変の別版として保存し、処理キューが抽出→FTS→Embeddingを進める。FTSが先に使えるようになり、Embedding失敗でもFTSは残す。再索引は保存した抽出本文を使い、同じ本文・権限範囲・接続先・モデル・次元数・処理仕様のEmbeddingを再利用する。OCRやOffice変換を繰り返さない。

削除時はまずDB上で無効化する。この時点で検索、根拠、原本、保存済み回答に使えなくなる。原本・全過去版・処理結果・FTS・未使用ベクトルの物理削除はジョブで行う。物理削除失敗は状態を確認してretryする。処理途中で更新・削除されても、遅れて届く旧ジョブの結果は公開しない。

旧版は明示した過去版検索でのみ扱い、`is_current=false` を返す。旧版の自動保存期限は初期実装では未設定で、資料削除時にまとめて消す。保存容量に応じた期限の導入は別途必要。

未開始ジョブは再起動後も残る。異常終了したローカル抽出はリース期限切れ後に再開する。通常停止で実行中の処理を中断した場合は `failed/interrupted` になり、明示的にretryする。回答生成の中断は再課金を避けるため自動再送しない。

## Embedding・回答生成の接続

API呼び出し元がProviderのURLやモデルを変更することはできない。管理者が次を設定する。Dockerでは、Git管理外の `.knowledge-api/providers.env` 等をComposeの `--env-file` に指定する。APIコンテナへだけ渡し、処理コンテナへ資格情報は渡さない。

| 環境変数 | 内容 |
|---|---|
| `KNOWLEDGE_EMBEDDING_URL` | Embedding gatewayの完全なHTTPS URL |
| `KNOWLEDGE_EMBEDDING_PROFILE` | モデル版を含む、gatewayが確認・返却するprofile |
| `KNOWLEDGE_EMBEDDING_DIMENSIONS` | ベクトルの次元数、1〜8192 |
| `KNOWLEDGE_ANSWER_URL` | 回答gatewayの完全なHTTPS URL |
| `KNOWLEDGE_PROVIDER_TOKEN` | gatewayのBearerトークン |

URLへの資格情報埋め込み・query・fragmentを禁止する。開発用localhostだけHTTPを許可する。接続先・profile・次元数を変えると旧ベクトルを検索に使わないため、対象資料をreindexする。profileには可変エイリアスだけを使わず、実際のモデルと版を含める。

```sh
docker compose --env-file .knowledge-api/providers.env -f deploy/compose.knowledge.yml up -d --force-recreate --wait
```

Embedding gatewayのHTTP契約：

```json
{"profile":"configured-model-v1","input":["本文1","本文2"]}
```

```json
{"profile":"configured-model-v1","vectors":[[0.1,0.9],[0.8,0.2]]}
```

入力順・件数・次元数・有限値・非ゼロを検証して正規化する。1バッチ最大16件で、次元数が大きい場合は応答サイズに収まる件数へ減らす（8192次元では8件）。本文1件24,000 UTF-8 bytes以下、1回90秒を上限にする。実際のモデルのトークン上限はgateway側でも確認する。JSONのprofileが設定と違う場合はエラー。モデル会社のAPIへそのまま送る形式ではないため、Azure OpenAI等へ接続するgatewayが必要。

回答gatewayには `{profile:"default", instruction, question, evidence}` を送る。`instruction` はAPI側の固定指示、`evidence` は根拠ID・本文・出典位置・表を含む構造化データ。資料内の指示は信頼せず、ツール実行経路は設けていない。gatewayは次のJSONを返す。

```json
{"answer":"根拠に基づく回答","citation_ids":["渡されたevidence_id"],"unknowns":["資料から確認できない点"]}
```

任意の `POST /api/v1/answers` は `{query,scope,profile:"default",timeout_ms:180000}` を受け、202と `answer_id/job_id` を返す。`GET /api/v1/answers/{id}` で結果を取得する。検索・親文脈取得・生成・引用ID確認・再認可の順で処理する。根拠なしは `insufficient_evidence`、渡していない引用IDは失敗、未設定は503。結果取得時にも資料の更新・削除・権限を再確認する。保存結果は30日で期限切れになる。JWTが待機中に失効した場合も生成を失敗させる。

実際のモデルを使った回答品質、性能、料金、Azure上の通信は未検証。HTTP契約と異常応答の検証にはローカルgateway、検索・回答の整合性検証には決定的なテストProviderを使っている。

## 制限と検証

原本は50 MiB以下。処理workerのページ上限は `DOCLING_MAX_PAGES` で指定でき、既定では固定制限を設けません。JSON要求は2 MiB以下、検索は最大50件、根拠は20参照・本文合計80,000文字・JSON 2 MiB以下。抽出の待機／実行は合計3件、検索の同時実行は8件、回答の待機／実行は3件。クライアントごとの1分枠は読み取り600、書き込み60、回答要求12。上限時は429と再試行可能な状態を返す。

原本の検査は登録前、抽出の公開は完全成功後に行う。API・処理サービスとも空き容量を確認する。`/health/ready` はDB・設定・ディスク、`capabilities.source_management` は処理サービス、`semantic`・`answers` は接続設定の有無を表す。ready応答だけで外部モデルへの実通信が成功したとは扱わない。

```sh
cd knowledge-api
bun run typecheck
bun run lint
bun run contract:check
bun test
bun run test:integration
bun run test:docker
cd ..
PYTHONPATH=. .venv/bin/pytest tests -q
```

Docker検証は専用Composeプロジェクト・一時資格情報・合成資料を作り、終了時にその検証用コンテナとボリュームを削除する。普段の資料を対象にしない。画像はビルドキャッシュとして残る。検証結果と未確認範囲は [検証記録](../qa/knowledge-api/verification.md) に記載する。

SQLiteと完全走査のsemantic検索は単一VMの初期運用向け。大規模データの応答時間、ANN索引への移行、複数APIプロセス、キューの公平性、利用者画面、実際のモデル品質は別途測定・実装する。

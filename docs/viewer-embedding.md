# 文書ビューアーの埋め込みとOpen WebUI連携

PDF、PPTX、XLSX、DOCX、Markdown、テキストを、資料一覧から独立した閲覧部品として開けます。Open WebUIでは検索結果の引用サイドパネルと、閲覧ツールのRich UIに表示します。初期版の接続確認はOpen WebUI **0.11.4**、標準の `allow-scripts allow-downloads` sandboxで行いました。同一Originやフォームの許可設定を追加する必要はありません。

## チャットでの操作

1. `list_document_scopes`で利用者の検索範囲を確認し、`search_documents`で検索します。
2. `get_document_context`で検索結果の親文脈と表を取得し、出典付きで回答します。検索時の `retrieval_id` と4つの参照項目を保持してください。
3. 引用を押すか、`request_document_view`で資料を開きます。閲覧画面が8文字の接続コードを表示します。
4. 「接続コードをチャット入力へ」を押して送信すると、`connect_document_view`がその利用者の権限で接続を承認します。承認された画面だけが資料を取得できます。
5. 資料を選択し「この箇所について質問」を押すと、資料の版、位置、選択した本文をチャット入力に渡します。入力を確認して送信してください。コピーが許可されないsandboxではコピー用の画面から手動でコピーできます。

接続コードは3分、閲覧セッションは5分で失効します。同じ発行元が未承認の接続を占有できる数には上限があり、上限に達すると `viewer_issuer_limited` と `Retry-After` を返します。信頼するproxyを設定した場合だけ、そのproxyが付けた `X-Forwarded-For` の末尾を発行元に使います。先頭の値では上限を回避できません。再表示、チャットのコピー、期限切れでは再接続してください。自動的に元の利用者の権限で再接続する動作はありません。資料が更新された場合は新しい検索結果から開きます。出典位置のない文書はその旨を表示し、先頭から開きます。

## 構成

| 部分 | 配置と役割 |
| --- | --- |
| 共通表示 | `frontend/src/viewer/DocumentViewer.tsx`。通常画面と埋め込みが同じ形式別表示を利用します |
| 資料取得先 | `frontend/src/viewer/data-source.tsx`。インスタンスごとの取得先を渡し、全体の通信設定を書き換えません |
| 通常画面の操作 | `frontend/src/features/Viewer.tsx`。翻訳・解説・印刷・原文保存を共通表示の外側に配置します |
| 専用入口 | `frontend/embed.html` と `frontend/src/embed/`。一覧、アップロード、Wikiの読み込みを持ちません |
| 認証仲介 | `viewer_gateway.py`。接続コードと文書単位の閲覧セッションを扱います |
| 閲覧API | `POST /api/v1/viewer`。資料・原本版・根拠版を検証し、確定した処理試行のmanifestまたは資産を返します |
| 非公開の描画 | `knowledge_worker.py` と `viewer_render.py`。既存の描画処理と保存済み生成物を再利用します |
| MCP | `viewer_mcp.py`。同じ仲介サービス内でStreamable HTTPのツールを公開します |
| Open WebUI | `integrations/open-webui/docling_desk_tools.py`。信頼できる `__user__` を仲介し、検索結果を引用、閲覧要求をRich UIへ変換します |

初期版はiframeごとに一つのビューアーを使います。通常画面へのReact組み込みもできますが、一つのReactルートへの複数配置には対応していません。既存の形式別CSSとDOMのIDを維持しているためです。

標準Artifactsの任意のHTMLに資料を複製する方式や、Open WebUI本体のfork、MCP Appsは追加していません。ネイティブToolのRich UIと引用の埋め込みURLを、初期版の表示経路としています。通常のMCP接続だけでOpen WebUIの引用イベントやRich UIが自動生成されるものではありません。

## 導入手順

既存の独立ナレッジAPIと文書処理サービスを先に設定します。手順は `docs/knowledge-api.md` を参照してください。既存デモの `data/` とナレッジAPIの資料保存領域は別です。デモのjob IDをsource IDとして使わないでください。

### 1. 表示部品と依存関係を用意する

リポジトリのルートで実行します。

```sh
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
pnpm --dir frontend export:static
uv pip install --python .venv/bin/python -r integrations/mcp/requirements.txt
```

新しいPythonの閲覧処理とBun APIを使うため、独立ナレッジAPIと文書処理サービスも更新して再起動します。MCPの追加依存は閲覧仲介サービスだけに必要です。文書処理サービスには不要です。

### 2. 認証設定を作る

`integrations/viewer/gateway.example.json`を、Git管理外の設定ファイルへコピーします。各プレースホルダーを置き換え、ファイル権限を600にしてください。資格情報は用途ごとに生成した別の乱数を使用します。Open WebUIの利用者IDは管理画面または管理用APIで確認したIDを使い、メールアドレスから推測しません。

- `api_url`：仲介サーバーから到達できる独立ナレッジAPI。
- `public_url`：利用者のブラウザーから到達できる閲覧仲介のOrigin。パス、クエリ、ユーザー情報は付けません。
- `client_id`、`issuer`、`kid`、`api_token`、`signing_key`：ナレッジAPIに登録するactorクライアントと一致させます。
- `connector_token`：Open WebUIのネイティブToolと仲介間の資格情報。
- `users`：Open WebUIの利用者IDごとに、許可するcollection/project/regionを指定します。該当項目のない利用者は拒否されます。
- `mcp_token`：MCPを直接使う場合の利用者別Bearer。省略できます。異なる利用者に同じトークンを割り当てる設定は拒否されます。
- `mcp_hosts`：ブラウザー向けURLと異なる内部ホスト名でMCPへ接続する場合のHost許可。Dockerの `host.docker.internal:18768` などを明示します。

`integrations/viewer/knowledge-client.example.json`のactor登録を、ナレッジAPIの既存clients設定に追加します。既存項目を上書きしないでください。クライアントは `mode: actor`、`actions: [read]` とし、登録するscopeは利用者に割り当てる範囲を含めます。projectとregionも両方の設定で一致させます。利用者のscopeを越える要求はナレッジAPIでも拒否されます。

設定ファイルは要求ごとに読み直します。利用者の削除、scope・資格情報の変更は既存セッションを拒否します。公開URLやMCPのHost許可を変更した場合は仲介サービスも再起動してください。

### 3. 仲介サービスを起動する

```sh
VIEWER_CONFIG_FILE=/absolute/private/path/gateway.json \
  .venv/bin/python -m uvicorn docling_desk.viewer.gateway:app_factory --factory \
  --host 127.0.0.1 --port 18768 --workers 1 --no-access-log
```

一つのプロセスでMCPと閲覧を実行してください。接続コードと閲覧セッションはメモリー内で管理するため、再起動で失効します。複数workerや分散配置では同じ状態を共有できません。複数workerを使う場合には外部のセッションストアを実装してから変更します。

HTTPSのOpen WebUIから接続する本番環境では、`public_url`もHTTPSにして仲介へ転送します。公開側のリバースプロキシでも `/viewer/session/` と `/viewer/challenges/` のURLを記録しないでください。アプリのアクセスログにはこれらの一時資格情報を伏せる処理があります。参照と版を含むURLの保存も、組織の通常のログ方針に従ってください。

Docker内のToolは、利用者PCの `localhost` へ接続できません。Toolの接続先にはコンテナから到達するホスト名を使います。ブラウザー向けの `public_url` と分けて設定してください。Dockerからホスト上のサービスへ接続する場合は、その経路から到達できるインターフェースで仲介を待ち受けます。

### 4. Open WebUIにToolを登録する

管理者としてWorkspaceのToolsへ `integrations/open-webui/docling_desk_tools.py` を登録します。管理者Valveの `GATEWAY_URL` にOpen WebUIサーバーから到達する仲介URL、`CONNECTOR_TOKEN` に同じconnector資格情報を設定します。通常利用者の設定やモデルの指示には資格情報を入れません。

対象チャットでこのToolを選択し、モデルのTool CallingをNativeにして利用します。表示や権限の検証は、実際に使うモデルでも行ってください。保存済みのコードやHTMLをLLMに生成させる必要はありません。

MCPで使う場合の接続先は `public_url + /mcp/` です。利用者ごとのBearerをStreamable HTTPクライアントに設定します。Open WebUIの全利用者が一つのMCP資格情報を共有する設定では、個々の利用者の権限を表現できません。その場合は利用者を仲介するネイティブToolを使ってください。

### 5. 最初の資料を用意する

新しく成功した抽出結果は、元の版と確定した処理試行を結び付けて公開します。導入前の抽出結果にはこの対応がないため、`preview_unavailable`になります。必要な資料だけ既存の `reextract` 操作で対応を作ってください。閲覧要求では抽出、OCR、embeddingを再実行しません。

埋め込み内の翻訳・解説生成、原文保存、印刷は初期版では提供していません。通常画面では従来の機能を使えます。Officeの描画品質と変換方式は既存ビューアーと同じで、Office編集や数式再計算は行いません。

## 認証と保存される情報

履歴に保存するものは契約版1の資料参照と初期位置です。`source_id`、`source_revision`、`evidence_revision`、任意の `location.kind/number` を持ちます。PDFのページ、PPTXのスライド、XLSXのシートは1から始まり、Wordと本文は `document: 1` です。XLSXの位置はシート名から推測せず、解析結果の版に属する番号を使います。セル範囲の強調表示、本文アンカー、座標の強調表示は追加対応です。

iframeにはCookieやナレッジAPIのBearerを渡しません。承認前のブラウザーは資料情報を取得できず、ランダムなチャレンジの結果だけを問い合わせます。承認後に一度だけ受け取るセッションは、当該利用者・当該参照・5分の期限に限定します。セッションをモデル文脈、チャット、localStorageへ保存しません。閲覧資産のURLにもセッションが必要ですが、これらは実行中の画面でだけ生成します。

各資産はサーバー仲介とナレッジAPIで再確認し、読み取り途中の資料更新・削除・権限変更でも返しません。ブラウザーは15秒ごとにもmanifestを確認し、失効を確認したら本文を取り除きます。すでに表示した内容を受信前に戻すことはできません。新しい通信は直ちに拒否されます。

CORSはCookieを伴わないsandboxの `Origin: null` に対応します。Originを認証の代わりには使わず、文書単位のセッションを必須にしています。MCP、ブラウザー、ネイティブToolの利用者対応は、それぞれ利用者別Bearer、接続承認、注入された `__user__` から確定します。モデル引数には利用者IDを公開していません。

## 検証記録と契約

実行結果、対象版、画面、再現用スクリプトは `qa/viewer-embedding/` にあります。ナレッジAPIの公開契約は `contracts/knowledge-api.openapi.json`、閲覧とホスト通信の契約は `contracts/viewer-embedding.schema.json` です。合成資料を使用し、モデルはローカルの固定応答にして外部LLMを呼んでいません。実環境のアカウント、資料、設定には変更を加えていません。

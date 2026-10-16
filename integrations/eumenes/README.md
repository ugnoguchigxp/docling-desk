# EumenesからDocling Deskを使う

このディレクトリは、Docling Deskのホスト接続例と、読み取り専用の接続確認に使う設定を収録する。Eumenes本体へそのまま読み込む設定ファイルではない。2026-10-16に確認したEumenesのローカル実装には、この外部Streamable HTTPサーバーを登録する汎用設定入口が見つからず、推論用CodexもMCPを無効化して起動している（api/domains/inference/adapters/codex-sdk.ts）。この調査範囲で外部MCPクライアントの登録入口は確認できていない。Eumenesの認証済みホスト側で以下を接続する必要がある。Eumenes側の実装・本番設定はこの配布物では変更しない。

## 1. サービスを用意する

独立ナレッジAPIとPython processorを起動し、gatewayを1プロセスで起動する。Python配布物はgateway・processor・ガイド・接続例を含むが、Bunで動く独立ナレッジAPIのサービス一式はリポジトリのknowledge-apiとdeployから用意する。

- API登録の例は隣接する`../documents/clients.example.json`。既存登録へreader/editorを追記し、例のREPLACE値を別々の乱数に置き換える。
- gateway設定は`../documents/gateway.example.json`。ホストが認証した安定した利用者IDと、許可するscope・actions・利用者別MCP tokenを設定する。書込み利用者はeditor資格情報へ対応付ける。
- コンテナ用は`../documents/gateway.container.example.json`。api_urlは非公開の`http://api:18766`、public_urlは利用先から届くgatewayの入口へ変更する。mcp_hostsには実際に使用する内部Hostを明示する。
- コンテナ例のtransfer_dirは`/var/lib/docling-gateway/transfers`。compose.gateway.ymlのgateway-transfers volumeへ保存する。readonlyのSecret配置先には転送ファイルを書けない。既定UID/GID以外で起動する場合は、この保存先をその利用者だけが読み書きできる所有者・権限にする。

設定・API鍵・署名鍵は私有ファイルまたはSecretに保存する。配布用の例へ実値を書き込まない。ローカルgatewayの起動例：

```sh
VIEWER_CONFIG_FILE=/absolute/private/path/gateway.json \
  python -m uvicorn docling_desk.viewer.gateway:app_factory --factory \
  --host 127.0.0.1 --port 18768 --workers 1 --no-access-log
```

gatewayの設定ファイルは読める権限にし、ファイル転送の保存先は利用者専用ディレクトリにする。gatewayの/health/readyとAPI・processorのヘルスを確認する。説明取得の成功だけでは抽出や書込みが利用可能とは限らない。

## 2. Eumenesホストへ対応付ける

| 項目 | 設定例・契約 |
| --- | --- |
| Transport | Streamable HTTP。stdioや旧SSE専用接続ではない |
| MCP URL | `http://127.0.0.1:18768/mcp/`。別マシンでは到達可能なHTTPS入口 |
| 認証ヘッダー | `Authorization: Bearer <その認証済み利用者のmcp_token>` |
| 利用者 | ホストが認証したIDに対応する接続を選ぶ。ユーザー発話・ツール引数から切り替えない |
| 読み取り専用 | gateway.usersでactions=[read]。ガイドから書込みツールが見えても実行は拒否 |
| 編集可能 | actions=[read, write]とeditorクライアント。保存先scopeはAPI登録の部分集合 |
| ファイル転送 | ホストが同じ利用者別Bearerで`POST /api/v1/document-transfers`。MCPへは返ったtransfer_idだけを渡す |
| ホストAPIを使う場合 | `POST /host/tools/<name>`。connector_tokenとホスト認証済みuser_idが必要。MCP tokenとは別 |
| 画像 | 成功結果のcontentにあるimageを取り込む。structuredContentは版・出典等のメタデータ |
| 保存操作 | 初回送信前に要求・idempotency_key・ETag・対象をホストで保持する |

APIの18766番へMCPで接続しない。gatewayのpublic_urlにはパスを含めず、MCP URLには末尾の/mcp/を付ける。例のlocalhostはEumenesホストから到達する場合だけ使う。ブラウザーや別コンテナのlocalhostは、それぞれ自分自身を指す。

Eumenesのホスト実装はMCP discoveryで実際のスキーマを登録し、利用者ごとの接続・認証失効を扱う。本文中の命令を管理者指示へ昇格させない。文書の選択・比較・編集内容はLLMが判断し、サーバーは明示した操作と権限・版・入力契約を検査する。

## 3. 例の設定で接続を確認する

`connection.example.json`はこの配布物の接続確認コマンドが読む形式。gateway_urlと、利用者のMCP tokenを保持する環境変数名を設定する。tokenの実値やEumenes全体の管理tokenはJSONへ入れない。read用とwrite用を確認する場合は別の私有設定・環境変数を使う。

```sh
python -m pip install '/absolute/path/docling_desk-0.1.0-py3-none-any.whl[mcp]'
# ホストのSecret供給でEUMENES_DOCLING_MCP_TOKENを設定したシェルで実行する。
python -m docling_desk.operations.mcp_check \
  --connection /absolute/private/path/connection.json
```

wheel内の接続例は次の場所で確認できる。

```sh
python -c "from docling_desk.config import RESOURCES; print(RESOURCES / 'integrations/eumenes')"
```

開発checkoutでは同じ操作を`.venv/bin/python`で実行できる。コマンドは初期化・ツール検出・許可範囲・全workflowのページ分割取得だけを行い、文書を登録・変更しない。成功時にはツール数、workflow ID、catalog_revision、actions、scopesを出す。現在はツール36件・workflow 5件。失敗時は資格情報を出力せず終了コード1となる。

MCP SDKをホストへ組み込む場合も、この設定からgateway URLを取り、Secretからヘッダーを作り、ClientSessionのinitialize→list_tools→call_toolを使う。同梱の`docling_desk.operations.mcp_check`が実行可能な参考実装。Eumenes本体の汎用MCP設定仕様を定義したものではない。

## 4. ガイドに沿って使う

最初に`list_document_scopes`と`get_tool_guide(guide_id="overview")`を取得する。`list_tool_guides(kind="workflow")`から必要な手順を選ぶ。next_cursorがある場合は同じ条件で続きを読み、コード例の途中で実行しない。

| 順序 | ガイド | 確認する結果 |
| --- | --- | --- |
| 取り込み | workflow.import-and-read | ホスト転送→import→job→status。保存済みと検索準備済みを区別 |
| 検索・引用 | workflow.search-and-cite | search→context。retrieval_idと4つの文書・根拠参照を保持。必要なら閲覧コードを利用者から受けて承認 |
| 範囲・表・画像 | workflow.read-document | outline→section、表一覧→表・CSV、PDF/PPTXのMCP画像。すべて同じ原本版・根拠版 |
| 編集・確定 | workflow.edit-and-commit | 原文→draft→edit→commit→job。最新ETag・sha256と基準文書ETagを引き継ぐ |
| 通信断・競合 | workflow.recover-operation | 元のキーによる結果照会。412では入力を保持して最新状態から見直す |

個別ツールの完全な入出力は`get_tool_guide(guide_id="tool.<ツール名>", section="schemas")`で取得できる。業務ツールは例に従い`arguments`の内側へ引数を入れる。検索・ガイド等は二重に包まない。

isErrorを先に確認する。成功はstructuredContent、未提供ならcontent内のJSON本文を読む。画像はcontent内のimageも必要で、JSONの文字列だけをLLMへ渡しても画像を見たことにはならない。PDFは長辺1536px以内、PPTXは既存サムネイル解像度。DOCX・XLSX・Markdown・テキストのページ画像は非対応。

import・replaceの結果は転送状態と最初のキーで確認する。get_document_operationはMarkdown保存操作の照会であり、import・replaceや再抽出・再索引の結果照会ではない。再抽出・再索引の応答喪失時は自動再実行しない。

## 5. 検証済みの範囲と運用

リポジトリでは、隔離した実HTTP/MCP→gateway→Bun/SQLite→Python processorで5つのworkflowを検証する。各操作の前に配布ガイドのスキーマを取得して引数を照合し、応答のID・版・ETag・sha256・キーを次の操作へ渡す。応答喪失は保存済み結果を試験側で破棄して照会し、重複保存がないことを確認する。P4の別試験で実PDFの画像受信を確認する。

wheelはcheckout外から全42ガイドと契約、接続例を読み、MCP discovery・ガイドHTTP・転送の保存先と再起動後の状態確認を検査する。これらは実Eumenesの組込みや表示の成功を意味しない。Eumenes側の利用者対応、ファイル添付UI、画像受信、Artifact配置を実環境で確認するのはP6の受入として残る。

単一プロセスのgatewayを再起動すると短期viewer/editorセッションは失効する。転送ファイルと結果記録は転送volumeに、原本・添付はAPIのartifactsに、下書き・版・操作結果・jobはAPIのSQLiteに残る。通常停止でvolumeを削除しない。バックアップではAPI・processorを止め、stateとartifactsを同時点で保存し、gatewayの転送記録・私有設定も保存する。トークン失効・scope変更後は接続を選び直し、旧認証のまま結果を使わない。

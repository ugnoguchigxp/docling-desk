# 閲覧連携の検証手順

対象はmacOS、Brave 154.1.96.60、Bun 1.4.2、プロジェクトのPython環境とフロントエンド依存関係。Open WebUIは0.11.4-slimをdigestで固定する。既存のOpen WebUI、資料保存先、利用者の設定を使わない。ポート18866–18868、18880–18881が空いた状態で実行する。

初回用の手順である。同じ環境を作り直す場合は、`.cache/viewer-embedding`を別名で退避してから始める。Open WebUIのコンテナを作り直すとアカウントも変わるため、以前のaccounts.jsonだけを再利用しない。

## 準備と起動

リポジトリのルートで依存関係と表示用ビルドを準備する。

```sh
export UI_BROWSER_EXECUTABLE='/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'
uv pip install --python .venv/bin/python -r integrations/mcp/requirements.txt
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
pnpm --dir frontend export:static
.venv/bin/python qa/viewer-embedding/prepare.py
docker compose -f qa/viewer-embedding/compose.yaml up -d
```

ブラウザー検証を実行する各ターミナルでも、この `UI_BROWSER_EXECUTABLE` を設定する。通常のプロファイルを使用せず、各試験が独立した一時プロファイルを作成して終了時に閉じる。他の環境では使用するブラウザーの実行ファイルを明示する。

別々のターミナルで次の二つを起動しておく。

```sh
.venv/bin/python qa/viewer-embedding/model.py
```

```sh
.venv/bin/python qa/viewer-embedding/start.py
```

model.pyは固定応答のローカルモデルで、外部LLMへ接続しない。start.pyは隔離したナレッジAPI、文書処理、閲覧仲介を起動し、AliceとBobの試験用アカウントを作り、ネイティブToolを登録する。API設定、署名鍵、MCP資格情報とアカウント情報はGit管理外の`.cache/viewer-embedding`に権限600で保存する。

準備済みのPDF、PPTX、XLSXはリポジトリの合成fixtureを利用する。DOCXとMarkdownはprepare.pyで実際に変換し、TXTはアップロード後の実際のworker処理で変換する。fixture APIはOffice変換を毎回起動せず、変換後の成果物を確定した処理試行へ配置する。認証、版管理、公開トランザクション、閲覧APIと資産の描画には実装コードを使用する。この検証は全Office形式の新規変換品質を評価するものではない。

## 実行

サービス起動完了後、別のターミナルで順番に実行する。

```sh
.venv/bin/python qa/viewer-embedding/seed.py
.venv/bin/python qa/viewer-embedding/mcp-check.py
node qa/viewer-embedding/formats.mjs
node qa/viewer-embedding/selection.mjs
node qa/viewer-embedding/browser.mjs
node qa/viewer-embedding/instances.mjs
```

instances.mjsは試験用Aliceのscopeだけを一時変更し、表示中の本文が消えることを確認した後、設定を元に戻す。他の試験と同時に実行しない。

| ファイル | 確認内容 |
| --- | --- |
| mcp-check.py | 実際のStreamable HTTP接続、ツール一覧、検索、根拠、閲覧、接続承認、無効な位置・版・資格情報、権限外 |
| formats.mjs | 6形式の初期位置、表示タブ、復帰後の本文、ズームとFit、1280/800/390px、再表示と他利用者の拒否 |
| selection.mjs | 本文の実選択、資料参照付きの質問、コピーの代替画面、別Windowの通知拒否 |
| browser.mjs | 実際のOpen WebUIでのXLSX/PDFの引用パネル、PDFのRich UI、入力への引き継ぎ、再読込、別利用者へのチャットコピー |
| instances.mjs | 同じ文書の複数配置での位置・ズームの分離、正しい参照を装う別Windowの通知拒否、表示後の権限変更 |

結果のJSONと合成資料の画面はこのディレクトリに保存する。生のセッションURL、API資格情報、アカウント情報は検証記録に入れない。設定更新後にToolを再登録したい場合はupdate-tool.pyを使う。

## 通常画面の回帰確認

Pythonは本プロジェクトのtests/を対象にする。別途チェックアウトしたDocling本体や過去のQAスナップショットの試験は収集しない。

```sh
.venv/bin/python -m pytest tests -q
pnpm --dir frontend lint
pnpm --dir frontend test:run
pnpm --dir frontend build
pnpm --dir frontend export:static
bun run --cwd knowledge-api test
bun run --cwd knowledge-api typecheck
bun run --cwd knowledge-api lint
bun run --cwd knowledge-api contract:check
```

ブラウザー回帰には既存の保存済みレイアウトfixtureも必要である。通常のfrontendの準備手順で`.cache/frontend-migration-source/data`を用意してから、出力先を試験専用にする。

```sh
mkdir -p .cache/viewer-embedding/printing
cp -R qa/print-preview/fixtures .cache/viewer-embedding/printing/
cd frontend
UI_QA_PORT=18997 \
UI_REPORT_DIRECTORY=../qa/viewer-embedding/final \
PRINT_QA_DIRECTORY=../.cache/viewer-embedding/printing \
pnpm exec playwright test --project=wiki --project=printing --project=operations \
  --output ../.cache/viewer-embedding/final-playwright
```

UI_SYNTHETIC=1のデータには、複雑な保存済みSVGスライドや保存済みExcelを使う試験のIDがない。その設定だけで上記の全試験を実行すると、該当fixtureの不足で失敗する。閲覧連携そのものの6形式の試験は合成資料だけで完結する。

実際に変換した合成Word文書を使い、通常画面のマウス操作と印刷を確認する場合は、別のターミナルで専用の試験サーバーを起動する。

```sh
UI_SYNTHETIC=1 DOCLING_UI_QA_PORT=18984 .venv/bin/python qa/frontend-migration/server.py
```

起動後にリポジトリのルートで `node qa/viewer-embedding/normal-word.mjs` を実行する。変換済みのDOCXをそのサーバー専用のcacheに配置し、実際のHTTP配信で表示・印刷プレビュー・閉じる操作を3回確認する。通信の置き換えは行わない。終了後はこの試験サーバーも停止する。

## 終了

start.pyとmodel.pyのターミナルを停止し、試験用コンテナを終了する。

```sh
docker compose -f qa/viewer-embedding/compose.yaml down
```

試験サービスの終了で接続コードと閲覧セッションは失効する。秘密情報を含むcacheは共有しない。

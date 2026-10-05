# 運用手順

2026年10月5日更新。ここにある手順が現行の操作です。README内のテスト件数は、その変更を入れた時点の記録であり、今の全体結果ではありません。

## 導入

```sh
git clone --recurse-submodules https://github.com/ugnoguchigxp/docling-desk.git
cd docling-desk
python3.12 -m venv .venv
.venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1 torchvision==0.29.1
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install --no-deps ./docling ./packages/docling-azure-ocr
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
corepack pnpm --dir frontend install --frozen-lockfile
corepack pnpm --dir frontend build
corepack pnpm --dir frontend export:static
.venv/bin/python -m docling_desk.operations.download_models
```

モデル取得はmanifestのrevisionを確認してから公開ディレクトリへ切り替えます。取得に失敗した途中ディレクトリは起動対象になりません。`.venv/bin/python -m docling_desk.operations.models` が `models match manifest` を出すまで、画面の `/health/ready` は `unavailable` です。理由は `missing:`、`revision:`、`incomplete:`、`unverified:`、`interrupted:`、`recover:`、`disk_space`、`frontend_missing` のいずれかです。`incomplete:` は必須の重み・設定・索引の欠落、空ファイル、設定の破損を区別します。`unverified:` はファイル名が揃っていても、更新時のヘッダ確認が済んでいない状態です。health確認は重み全体を読み直しません。差し替えの途中で止まった場合は `.venv/bin/python -m docling_desk.operations.models recover` が、manifestと一致する完了済みディレクトリだけを戻します。一致しなければ手順を表示し、`.previous` は確認前に消しません。

ローカル画面は `./run.sh` で 127.0.0.1:8765 だけを開きます。外部ナレッジAPIは別の保存領域 `.knowledge-api` を使います。資格情報は本文のバックアップに入れません。

以下の `data/` は開発環境の例です。保存先を変更している場合は、実際の `DOCLING_DATA_DIR` に置き換えてください。内部は `content/`・`derived/`・`runtime/`・`cache/` に分かれます。[保存構成と旧配置の移行](content-storage.md)を参照してください。

## 診断

| 状態 | 見方 |
| --- | --- |
| 起動のみ | `/health/live` が `ok` または `alive` |
| 処理可能 | `/health/ready` が `ready`。それ以外は `reasons` を見る |
| 抽出待ち・失敗・一部成功 | 資料一覧の状態。失敗は再実行できるものと、再アップロードが必要なものを分ける |
| Wiki正本 | `data/content/wiki/revisions/` と `data/content/manifests/wiki.json` |
| 資料原本と分類 | `data/content/documents/<id>/original.*` と `data/content/manifests/library.json` |
| 検索索引・処理状態 | `data/runtime/knowledge/local.sqlite` と `data/runtime/documents/<id>/` |
| OCRの受付不明 | 自動再送しない。画面またはAPIの明示的な再送を一度だけ使う |
| 意味検索未設定 | Wikiと全文検索は使える。索引作成はAzure設定後に利用者が開始する |

ログとエラーには本文、APIキー、署名を残しません。追う識別子は request、job、run、版、ハッシュです。

## バックアップと復元

変換と索引の書き込みが止まってから取得します。Webプロセスが残っていても、書き込みが無ければ取得できます。書き込み中の取得はコマンドが拒否します。

```sh
.venv/bin/python -m docling_desk.operations.backup status --data data
.venv/bin/python -m docling_desk.operations.backup backup --data data --output /secure/backup
.venv/bin/python -m docling_desk.operations.backup restore --archive /secure/backup --destination /empty/restore
```

`status` が `idle` を返すことを確認してから `backup` します。`active convert` または `active index` のときは、その処理が終わるまで待って再度 `status` を見ます。止まったプロセスのロックは残りません。

ローカルバックアップは `content/`・`derived/`・`runtime/` を含め、原本と処理状態を一組で保存します。SQLiteはbackup APIでコピーし、WALの確定済み内容を取り込みます。将来のBlob同期対象であるコンテンツ一式だけの保存とは異なります。バッチの編集用ワークスペースを `DOCLING_DATA_DIR` の外に置いた場合は、そちらも別途保存してください。

`.env`、`credentials.json`、拡張子 `.pem` と `.key`、区切ったファイル名が `api-key`、`apikey`、`secret`、`credential`、`password` であるもの、SQLiteの `-wal` と `-shm` は含めません。`keyboard.txt` のような通常の資料名は残します。取り込み済みの `content/wiki/revisions/` のMarkdown・CSVは、`password.md` などの記事名でも本文として保存します。

復元先は空である必要があります。途中で失敗した復元先は空に戻します。復元はファイルを戻すだけで、OCRやembeddingを再送しません。復元後は原本、文書、親RAG、子RAG、Wiki本文が同じ世代であり、削除済み資料は検索に戻りません。旧配置のバックアップも復元でき、次の起動時に新配置へ移行します。

## 容量

`src/docling_desk/resources/ops/retention-policy.json` で、再生成できる `cache/`・`.cache/` は14日、サムネイルは30日です。

```sh
.venv/bin/python -m docling_desk.operations.retention --root data
.venv/bin/python -m docling_desk.operations.retention --root data --apply
```

`content/` 全体、`runtime/` のSQLite・ジョブ、使用中の `document.json` と `rag*.jsonl` は削除しません。[保存構成と旧配置の移行](content-storage.md)も参照してください。

## 検証

```sh
.venv/bin/python -m docling_desk.operations.verify
.venv/bin/python -m docling_desk.operations.verify --with-js
```

画面の合成fixtureは利用者の `data/` をコピーしません。

```sh
cd frontend && UI_SYNTHETIC=1 pnpm test:e2e:synthetic
```

レイアウト比較の基準画像だけ、`pnpm visual:prepare` が保存済み資料のコピーを使います。

## 更新と戻し

旧配置から初めて更新する場合は、旧Webプロセスとバッチを停止し、変更前にバックアップを取得してください。新バージョンの起動時に保存配置を移行します。異なる内容の移行先がある場合は上書きせず停止します。旧バージョンへ戻す場合は、移行前のバックアップを空の別ディレクトリに復元し、その保存先を指定してください。

1. 新しいソースと `src/docling_desk/resources/models/manifest.json` のrevisionを確認する。
2. `.venv/bin/python -m docling_desk.operations.download_models` を実行する。失敗した場合、直前のモデルディレクトリが残ります。途中でプロセスが落ちたときは `.venv/bin/python -m docling_desk.operations.models recover` を実行します。
3. `.venv/bin/python -m docling_desk.operations.models` と `.venv/bin/python -m docling_desk.operations.verify` が成功してから差し替える。
4. 戻すときは、その時点のソースとmanifestに対応するモデルrevisionへ再度取得する。半端なモデルディレクトリのまま起動しません。旧revisionをmanifestと違うまま有効にはしません。

## 既知の制限

- ローカル画面に認証はありません。公開範囲はループバックです。広くネットワークへ開く構成はこの手順の対象外です。
- 実Azureの課金、OCR品質、回答品質は、このオフライン手順では確認しません。
- 全文検索と意味検索の関連度は、状態と出典の契約とは別に評価します。

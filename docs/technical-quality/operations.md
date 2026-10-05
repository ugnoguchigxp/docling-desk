# ローカル保存のバックアップと復元

この手順はローカル画面の `data` を対象にします。外部ナレッジAPIの `.knowledge-api`、設定ファイル、APIキーは含めません。バックアップと復元はネットワークへ接続しません。

## バックアップ

変換と索引の書き込みが止まってから実行します。Webプロセスの有無だけでは判断しません。`data` は実際の保存ディレクトリ、`archive` は空の新規ディレクトリです。`scripts/knowledge_backup.py` は同じ検査を呼びます。

```sh
.venv/bin/python -m docling_desk.operations.backup status --data data
.venv/bin/python -m docling_desk.operations.backup backup --data data --output archive
```

`status` が `idle` になってから `backup` します。書き込み中は取得を拒否します。止まったプロセスのロックは残りません。

`archive/knowledge.sqlite` はSQLiteのbackup APIでコピーしたWiki正本です。`archive/files` は原本と抽出済みファイルです。symlink、`.env`、秘密鍵はコピーしません。`archive/manifest.json` にWikiのID、版、削除フラグ、本文ハッシュと、ファイルのハッシュがあります。

## 復元

復元先は空のディレクトリにします。中身があるディレクトリへは書きません。

```sh
.venv/bin/python -m docling_desk.operations.backup restore --archive archive --destination restored-data
```

復元後に確認すること:

- Wikiの記事数と、削除済みの記事が検索に戻っていないこと
- 原本ファイルのハッシュがmanifestと一致すること
- embeddingやOCRの要求が自動では送られないこと。意味索引が無い場合は全文検索だけを使います

キャッシュやモデルは再取得できる側です。モデルのrevisionがmanifestと違う、またはファイルが無い場合、`/health/ready` は `unavailable` と理由を返します。未配置は `missing:`、中身のrevisionが違う場合は `revision:` です。通常の起動では `DOCLING_QUALITY_FAULT` を設定しません。この環境変数は試験が保存の途中で落ちた状態を作るためだけに使います。

画面のCIは利用者の `data` をコピーしません。`UI_SYNTHETIC=1` で、リポジトリ内の合成文書から起動します。

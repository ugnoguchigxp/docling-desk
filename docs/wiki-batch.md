# WikiのPythonバッチと従来システムデータの移行

従来システムの処理を参照して、調査・下訳・検証・公開をPythonで実装した。WikiバッチにTypeScript・Bunの実行環境は不要。PDF・Officeの翻訳機能とは別のキューを使用する。

SQLiteの書き込みは画面と共通の専用Writerプロセスを通す。バッチを別プロセスで起動しても同じWriterに接続する。動作と復旧方針は [SQLiteの書き込みプロセス](sqlite-writer.md) を参照。

## 保存先

ワークスペースはアプリ本体と分け、例えばこのプロジェクトの `data/wiki-workspace/` に置く。

```text
data/wiki-workspace/
  sources/notion/                  元の書き出し。Office・添付ファイルも元のバイトで保持
  wiki/pages/original/             原文MarkdownとCSV目次
  wiki/pages/ja/                   訳文MarkdownとCSV目次
  manifests/pages.jsonl            固定ID・原文と訳文の対応・原本版・翻訳状態
  manifests/translation-terminology.json  任意の対訳集（従来システムと同じschemaVersion: 1）
  data/translation.sqlite          翻訳キュー・応答・調査・待機期限
  data/translation/jobs/           調査結果・公開候補・公開前バックアップ
```

`--root` はこの編集用ワークスペース、`--data` は画面用の保存領域で、通常はこのプロジェクトの `data/`。画面のWikiは `data/content/wiki/revisions/` にMarkdown・CSVを公開し、`data/content/manifests/wiki.json` に記事IDと公開版を保存する。検索と処理状態は `data/runtime/knowledge/local.sqlite` を使用する。ワークスペース内の `data/translation.sqlite` は翻訳バッチ専用で、画面用のDBとは別に保持する。

将来Blobへ同期する対象は画面側の `data/content/`。この編集用ワークスペース全体やSQLiteは同期対象に含めない。Blob接続・同期は未実装。[保存構成](content-storage.md)を参照。編集用ワークスペースは公開済みWikiだけから完全には復元できないため、元の書き出し・編集内容・翻訳状態を別途バックアップする。

バッチはワークスペースのファイルを正本として同期するので、画面で削除した記事も次の同期で再登録される。継続して公開対象から外す場合は編集元も整理する。Officeファイルはワークスペースに保存されるが、資料一覧への登録・抽出は従来の資料アップロードで行う。

## 停止済み従来システムからコピーする

稼働中の従来システムを停止した後、空の移行先へコピーする。移行コマンドは元のWiki・資料・翻訳DBを変更しない。ワーカーまたはWiki更新が動いていれば拒否し、SQLiteのbackup APIでWALを含む確定済みの状態をコピーする。

```sh
mkdir -p data/wiki-workspace
.venv/bin/python -m docling_desk.wiki_batch import-workspace \
  --from ../legacy-wiki --root data/wiki-workspace --data data
.venv/bin/python -m docling_desk.wiki_batch status --root data/wiki-workspace
.venv/bin/python -m docling_desk.wiki_batch terms-check --root data/wiki-workspace
```

コピー対象は `sources/`、`wiki/`、`manifests/`、`data/translation/`、`data/translation.sqlite`。コピー前後の資料ハッシュと移行記録を `manifests/migration.json` に保存する。空でない移行先には上書きしない。認証情報 `.env`、従来システムのアプリコード・検索DB、旧目次翻訳専用DB `translation-toc.sqlite` はコピーしない。CSV目次の日本語タイトルは `manifests/toc-translations.json` をコピーして維持する。切り替え前に旧側で公開処理を完了させておく。

コピーしたキューは一時停止状態。元側のワーカーを再開したままコピー先も実行しない。`sync` はネットワークを使わず、記事とCSV目次を画面へ反映する。

```sh
.venv/bin/python -m docling_desk.wiki_batch sync --root data/wiki-workspace --data data
```

今回の実装作業では、稼働中の従来システム・実資料・認証情報を移行していない。上記は切り替え時に実施する操作。

## 新しい書き出しから準備する

空のワークスペース直下にNotionの書き出しフォルダーを置く。`prepare migrate` はそれらを `sources/notion/` に移し、バイトのハッシュを保存する。アプリのソースコードを置いたフォルダーを `--root` に指定しない。

資料群の対応規則を `manifests/collections.json` に置く。値は `[Wiki内の名前, 表示名]`。

```json
{"Export Folder": ["requirements", "要求仕様"]}
```

対応例は `integrations/wiki/collections.example.json`。フォルダーの説明も必要なら `folder-titles.example.json` を `manifests/folder-titles.json` にコピーする。設定がない場合は元フォルダーから名前を作る。

```sh
.venv/bin/python -m docling_desk.wiki_batch prepare --root data/wiki-workspace --prepare-action plan
.venv/bin/python -m docling_desk.wiki_batch prepare --root data/wiki-workspace --prepare-action migrate
.venv/bin/python -m docling_desk.wiki_batch prepare --root data/wiki-workspace --prepare-action build
.venv/bin/python -m docling_desk.wiki_batch prepare --root data/wiki-workspace --prepare-action check
```

`build` は原本・日本語準備ページ・CSV目次・manifest・フォルダー案内を作る。既存の訳文や手動編集がある場合は原本版と状態を照合する。原本のバイトは書き換えない。

## 翻訳を登録・実行する

ワークスペースの `.env` または環境変数に以下を設定する。既に設定された環境変数を優先する。

```dotenv
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
AZURE_OPENAI_API_KEY=YOUR-KEY
AZURE_OPENAI_LUNA_DEPLOYMENT=YOUR-DRAFT-DEPLOYMENT
AZURE_OPENAI_SOL_DEPLOYMENT=YOUR-VERIFY-DEPLOYMENT
```

新規ジョブには登録時のモデル名・指示文・原文・用語定義・分割単位を保存する。登録時にもモデル名を設定し、途中でデプロイを変えない。

```sh
# オフライン登録。資料群・記事または件数で絞れる
.venv/bin/python -m docling_desk.wiki_batch enqueue --root data/wiki-workspace --collection glossary --limit 3

# 最新の異なる3記事を固定して実行。停止後も同じ対象で再開
.venv/bin/python -m docling_desk.wiki_batch run --root data/wiki-workspace --trial

# 通常の継続実行
.venv/bin/python -m docling_desk.wiki_batch run --root data/wiki-workspace
```

`run` だけがAzureへ送信する。キューが空の場合は未登録の未翻訳記事を登録し、用語集を優先する。過去に要確認・失敗となった記事は自動登録し直さない。`--limit` は処理する記事数。`--trial` と併用しない。

```sh
.venv/bin/python -m docling_desk.wiki_batch status --root data/wiki-workspace
.venv/bin/python -m docling_desk.wiki_batch pause --root data/wiki-workspace
.venv/bin/python -m docling_desk.wiki_batch resume --root data/wiki-workspace
.venv/bin/python -m docling_desk.wiki_batch retry --root data/wiki-workspace --key requirements/R-1
```

`pause` とCtrl+Cは通信中の結果を保存して次の段階へ進む前に止まる。`resume` は停止フラグを解除する。プロセスが終了していれば、続けて `run` を実行する。`retry` は失敗・要確認の記事を明示的に戻し、未承認の訳文や不十分な調査を再実行する。公開途中は新規翻訳より先に復旧する。

## 調査・検証・再開

原文読解 → Wiki全文検索 → 関連する原文本文の読解 → 調査の十分性判定 → 登録用語の意味判断 → 下訳 → 対訳検証 → 必要な場合だけ一度修正 → ページ全体の用語検証 → 公開、の順で進む。追加調査は最大2回、読解する関連資料は合計6件まで。解消できなければ `needs_review` にする。

コード、数値、ID、リンク先を保護し、欠落・重複した翻訳単位、表セルや見出し構造の変更を拒否する。条件付きの確認済み用語には原文・関連資料の出典を求め、未確認の訳語は強制しない。CSV目次は翻訳ジョブに入れず、公開した記事のタイトル・分類・状態から更新する。

各段階とモデル応答をSQLiteへ保存する。従来システムの現行 `translation-batch-v5-registered-terms` ジョブは保存済みの単位とID・UTF-16位置・入力ハッシュを保持し、再分割しない。指示文・モデル・原文・日本語準備ページが異なる場合は要確認となる。古い形式や異なるレシピは自動変換しない。

Azure Responses APIを使い、要求は直列で送る。記事間は60秒。429・408・サーバーエラーは再試行期限を保存し、`Retry-After`／`retry-after-ms`を尊重する。通信結果が不明な要求には210秒以上の保留期限を置く。同じ呼び出しは最大6回・24時間まで。成功応答は再利用し、二重ワーカーはOSロックで拒否する。

公開候補、本文、CSV、manifest、検索索引の反映を確認してから `completed` にする。公開途中の記録が残っていれば同じ候補で復旧する。原文や日本語ページを手動編集した場合は上書きしない。

## 検証範囲

`tests/test_wiki_batch.py`、`tests/test_wiki_catalog.py` と既存Wikiテストで、旧形式の合成snapshot、調査の打ち切り、修正上限、保存・再開、429待機、二重ワーカー拒否、公開途中の復旧、手動編集保護、資料コピーを検証する。`frontend/e2e/wiki.spec.ts` はCSV目次と未公開訳文の原文表示を合成データで確認する。実Azureでの翻訳品質と実データの切り替えは別の運用検証になる。

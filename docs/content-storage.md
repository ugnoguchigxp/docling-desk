# コンテンツとローカル状態の保存

将来Azure Blobへ原本とWikiを保存することを想定し、ローカルでも保存領域を用途で分けています。基準ディレクトリは `DOCLING_DATA_DIR`。開発環境では従来どおり `data/` です。通常のwheelインストールの既定値は `~/.local/share/docling-desk/data/`、Dockerでは `/var/lib/docling/data/` です。以下の説明のパスは、特記しない限りこの基準ディレクトリからの相対パスです。

```text
data/
  content/                              原本・Wikiの正本。Blob同期候補
    documents/<資料ID>/
      original.pdf                      docx・xlsx・pptx・md・markdown・txt・textも同じ規則
      manifest.json                     原本名、資料ID、相対キー、SHA-256
    wiki/revisions/<版ID>/<分類キー>/
      manual/guide.md                   元の相対パスとfrontmatterを保持
      index.csv
    manifests/
      library.json                      表示名と画面上のフォルダー分類
      wiki.json                         記事ID、論理パス、公開版、管理情報
  derived/documents/<資料ID>/            抽出・翻訳・解説・プレビュー
    manifest.json                       完了済み抽出結果の情報
    document.json
    document.md
    rag.jsonl
    rag-index.jsonl
  runtime/                              ローカル専用。Blob同期しない
    documents/<資料ID>/job.json
    documents/<資料ID>/explanation.sqlite
    knowledge/local.sqlite
  cache/documents/<資料ID>/thumbnails/   再生成できるサムネイル
```

ファイル形式ごとの保存領域は作りません。資料IDを固定し、表示名の変更や画面内での移動は `library.json` に反映します。原本の保存キーは変わりません。資料コピーは新しいIDを割り当て、原本と成果物をコピーします。削除はそのIDの原本・成果物・実行状態・サムネイルを削除します。

Wikiは書き込み済みの版をmanifestで公開します。分類キーは分類名のハッシュで、OSのファイル名制約や大小文字の違いによる衝突を避けます。分類名と記事の相対パスは `wiki.json` に保持し、記事間リンクはその論理パスで解決します。削除したWiki記事はmanifestの削除状態として保持し、索引の再作成で復活させません。公開済みの版と削除済み本文は復旧用に保持します。

編集は画面からの再取り込み、または既存のWikiバッチワークスペースの `wiki/pages/` で行い、`sync` で公開します。`content/wiki/revisions/` 内を直接編集するとハッシュが一致しなくなるため、次回起動時の索引同期でエラーになります。画面取り込みの本文・CSV・frontmatterと記事情報はファイルに保持されます。旧DBだけに保存されていた記事は、残っている本文と管理情報からファイルへ移行します。旧DBで取り込み時に除いたfrontmatterの元のバイトは復元できません。

SQLiteには検索索引・embedding・待ち時間・処理状態を保存します。Wiki本文の検索用コピーも含みますが、正本はコンテンツファイルです。SQLiteを失ってもWiki記事ID・分類・原文と訳文の対応をファイルから復元できます。失ったembeddingや実行履歴は復元されません。意味検索の索引は必要に応じて作成し直してください。

`content/` 以下の相対パスをBlob名として使用できます。成果物を共有する場合は `derived/` を別のprefixに対応させます。`runtime/` と `cache/`、Wikiバッチの `data/translation.sqlite` や資格情報は同期対象に含めません。Blobへのミラーは `DOCLING_STORAGE=azure-blob` で有効にします（[Blobミラー](#blobミラー)）。

`DOCLING_CACHE_DIR` は描画用補助プログラムなどの共有キャッシュ用です。資料ごとのサムネイルは `DOCLING_DATA_DIR/cache/` に保存します。Wikiバッチの編集元と翻訳状態は[編集用ワークスペース](wiki-batch.md)で別に管理します。

## 旧配置からの移行

起動時、ワーカーを開始する前に共通の書き込みロックを取得して移行します。旧バージョンのWebプロセスと実行中のバッチを停止し、変更前に[バックアップ](operations.md)を取得してください。

- `<資料ID>/original.*` → `content/documents/<資料ID>/original.*`
- その他の資料成果物 → `derived/documents/<資料ID>/`
- `job.json` と解説用SQLite → `runtime/documents/<資料ID>/`
- `library.json` → `content/manifests/library.json`
- `knowledge/local.sqlite` → `runtime/knowledge/local.sqlite`
- 旧Wiki本文 → 初回Wiki・検索利用時にMarkdown・CSVとmanifestへ保存

原本は再変換せず、元のバイトを保持します。SQLiteはbackup APIでWALの確定済み内容を取り込みます。新旧の保存先に異なる内容や同じ資料IDがあれば上書きせず停止します。旧形式のバックアップは引き続き復元でき、次の起動時に移行されます。ローカルバックアップには `runtime/` も含めて、検索の待ち時間や実行状態を保持します。これはBlobに保存するコンテンツ一式とは別です。

## Blobミラー

ローカルの `DOCLING_DATA_DIR` が正本のまま、Azure Blob Storageへ複製します。翻訳タスクなどの書き込みはローカルで行い、バックグラウンドの同期が変更分だけをBlobへ送ります。`DOCLING_STORAGE=local`（既定）では何も送りません。

### 設定

| 環境変数 | 内容 |
|---|---|
| `DOCLING_STORAGE` | `local`（既定）または `azure-blob` |
| `DOCLING_BLOB_CONTAINER` | コンテナ名。必須 |
| `DOCLING_BLOB_ACCOUNT_URL` | `https://<アカウント>.blob.core.windows.net`。Managed Identityで接続する場合に指定 |
| `DOCLING_BLOB_CLIENT_ID` | ユーザー割り当てManaged Identityのクライアント。システム割り当てなら不要 |
| `DOCLING_BLOB_CONNECTION_STRING` | 接続文字列。指定した場合はこちらを優先。Azurite（ローカルのエミュレーター）での検証用 |
| `DOCLING_BLOB_PREFIX` | コンテナ内の接頭辞。1つのコンテナを他と共有する場合に指定 |
| `DOCLING_BLOB_SYNC_DERIVED` | `derived/`（抽出結果・訳文）も複製する。既定 `1` |
| `DOCLING_BLOB_INTERVAL_SECONDS` | 同期間隔。既定30、最小5 |

Managed Identityには、コンテナに対する「Storage Blob Data Contributor」が必要です。

### 保存先と復旧

| データ | ローカルの場所 | Blob | 復旧 |
|---|---|---|---|
| 原本 | `content/documents/<ID>/` | 複製 | Blobから復元 |
| Wiki本文・版 | `content/wiki/revisions/` | 複製 | Blobから復元 |
| 分類・記事情報・削除状態 | `content/manifests/*.json` | 複製（常に最後に送信） | Blobから復元 |
| 抽出結果・保存済み訳文 | `derived/documents/<ID>/` | 複製（`DOCLING_BLOB_SYNC_DERIVED=1`） | Blobから復元 |
| 資料の処理状態 | `runtime/documents/<ID>/job.json` | 複製 | Blobから復元（起動に必要） |
| 解説 | `runtime/documents/<ID>/explanation.sqlite` | 整合性のあるスナップショットを複製 | Blobから復元 |
| 検索索引・embedding・ジョブ | `runtime/knowledge/local.sqlite` | 複製しない | 本文から索引を再作成。embeddingは再作成が必要 |
| サムネイル | `cache/` | 複製しない | 再生成 |

SQLiteは稼働中のファイルをそのままコピーせず、SQLiteのbackup APIで作った一貫した複製を送ります。

### 動作

- 更新は、保存済みのETagを条件にBlobへ書きます。他の経路でBlobが更新されていた場合は上書きせず、競合として記録します（`/api/storage`、`docling-desk-blob status`）。
- 送信に失敗しても成功扱いにしません。ネットワーク・認証の失敗は、その回の同期を中断して記録し、次の同期で再試行します（失敗が続く間に何件も試して待たされることはありません）。詳細（アカウントURLなど）はログだけに残し、`/api/storage` にはエラーの種類だけを出します。1件が読めないだけの場合は、そのファイルだけを飛ばします。保存先のキーはパスで固定なので、再試行で重複は生じません。送信後にハッシュ（SHA-256）を付与し、復元時に照合します。
- 書き込み中のファイル（更新から2秒以内）は送りません。送信中に変わったファイルは、次回に送り直します。manifestは、それが指すファイルの送信待ちが残っている間は送りません。
- 状態ファイルを失っても、本体を送り直さずにBlobの一覧と照合して再開します。Blobが別経路で削除された場合は、ローカルの内容で作り直します。競合したファイルは、内容が変わるまで再送しません。
- 資料や記事を削除したら、すぐに同期します。削除はBlobへ先に反映し、manifestは最後に送ります。復元時も同じ順序で、manifestを最後に取り込みます。そのため削除済みの資料は復元で復活しません。
- `content/` や `runtime/documents/` など、ローカルの保存領域そのものがない場合（ボリューム未接続など）は、その領域のBlobの削除を同期しません。領域があって中身だけが削除された場合は、通常の削除として同期します。
- 起動時にローカルの `content/` が空なら、Blobから復元します。復元の途中で止まった場合は印を残し、次の起動で続きから再開します。完了するまで `/health/ready` は `blob_restore_incomplete` を返し、同期のワーカーは送信より先に復元を再開します（途中のコピーで送信して、まだ取得していないBlobを消すことがないようにするためです）。終了時は、翻訳・解説の処理を止めたあとに最後の同期を行います。

### 手動操作

```sh
docling-desk-blob status   # 状態・競合・エラー
docling-desk-blob push     # 今すぐ同期（復元が完了していない間は拒否）
docling-desk-blob pull     # ローカルにないものをBlobから取得
```

終了コードは、競合やエラーがあれば1です。復元が完了していない間は `push` を拒否します（2）。取得できないBlobが残ったままでも部分的な状態を受け入れる場合に限り、調査のうえ `runtime/blob-restore.incomplete` を削除してください。削除しても、追跡していないBlobを消すことはありません。競合は、どちらの内容を残すかを人が判断して解消します（Blob側を削除するか、ローカルを修正して再送します）。

### 検証

Azurite（`npx azurite`）の接続文字列を `DOCLING_TEST_AZURITE` に指定すると、実際のAzure SDKで条件付き書き込み・削除・復元を確認するテスト（`tests/test_blob_azurite.py`）が走ります。指定がなければスキップします。実際のAzureサブスクリプションへの接続は検証していません。

# Wikiと資料の本文検索

2026年10月4日。既存のFastAPI・React画面に、MarkdownのWikiと資料横断検索を統合した。

## 画面で使う

ルートURL `/` はWikiを表示する。上部の「資料一覧」「Wiki」で表示を切り替える。資料一覧を直接開く場合は `/?mode=library` を使う。それぞれの閲覧位置は画面内で保持され、URLにも記事・見出し・資料・原本の表示位置が入る。従来の`?job=...`・`?folder=...`などのURLも資料画面として利用できる。

Wikiで「Markdownを取り込む」を押し、分類名とファイルまたはフォルダーを選ぶ。UTF-8の`.md`・`.markdown`記事と`.csv`目次を取り込める。1件2 MiB、1回100件・20 MiBまで。同じ分類・相対パスで再取り込みすると更新する。フォルダーを選ぶ場合、選択したフォルダーの直下を相対パスの起点にする。記事一覧の分類と記事名で絞り込める。CSVのタイトル・記事パスは登録済みの記事へのリンクとして表示し、CSV自体は本文検索に含めない。

記事には目次、記事間リンク、Markdownの保存、削除がある。HTMLは文章として表示し、スクリプトや外部画像を実行・取得しない。記事間の相対リンクは同じ分類の登録済み記事、または同じワークスペース内の登録済み記事に解決する。HTTPS・HTTPの外部リンクは別ウィンドウで開く。

WikiへMarkdownファイルをドロップした場合も、Wikiの取り込み画面を開く。資料一覧へのアップロードにはならない。

「資料を追加」で取り込んだMarkdown、資料の抽出で生成した`document.md`、PDF・Office・TXTは、自動ではWiki記事にならない。

## ワークスペースの書き出し原本を開く

Pythonバッチの`sync --root <workspace> --data <app-data>`は、記事に紐づくワークスペースの`sources/`を原本の参照領域として明示的に登録する。`import-workspace`も移行先を同期するので、コピー元ではなくコピー先を登録する。通常の画面取り込みだけではサーバーのフォルダーを登録しない。

Wiki本文の相対リンクは登録済み記事を優先し、対応する記事がなければ登録した`sources/`内の原本へ解決する。Markdown・CSVは別タブの読み取り専用画面で表示し、「原本を取得」で保存できる。添付ファイルは同じ画面から取得する。画像の相対参照も原本へのリンクになり、外部画像は取得しない。資料一覧への登録やDoclingによる変換は不要。取得する内容は元のバイトのままなので、名前・フォルダー構造・改行・BOM・バイナリを維持する。

例えば記事が`wiki/pages/original/requirements/R-1.md`にある場合：

```markdown
[原本](../../../../sources/notion/日本語%20フォルダー/原本%20メモ.md)
[目次](../../../../sources/notion/日本語%20フォルダー/下位/目次%20一覧.csv)
[添付](../../../../sources/notion/日本語%20フォルダー/下位/添付.pdf)
```

日本語・空白・下位フォルダー・パーセント記号を扱える。空白は`%20`にするか、Markdownリンク先を`<...>`で囲む。CSVの`page_path`・`counterpart_path`も相対参照できる。原本CSVをWiki目次として登録する必要はない。

参照できるのは登録領域の通常ファイルだけ。領域外・絶対パス・シンボリックリンク（領域内を指す場合も含む）を拒否する。描画時とファイルを開く時の両方で確認し、ディレクトリを開いた状態で次のパスを辿るため、途中でリンクへ差し替えられても領域外を開かない。欠落・拒否のリンクには理由を表示し、押した場合も画面上で知らせる。表示後にファイルが消えた場合は「原本を参照できません」の画面になる。

Markdown・CSVの画面表示はUTF-8、2 MiB以内。表示できない文字コード・大きなファイル・不正なCSVでも、許可された通常ファイルなら取得できる。HTMLやスクリプトは実行せず、原本を検索索引へ自動登録しない。

登録情報は画面保存領域の`runtime/knowledge/wiki-workspaces.json`に保存する。絶対パスを含むローカル設定なので、アプリやワークスペースをコピー・移動した後はコピー先の`--root`と`--data`を指定して`sync`し直す。公開コンテンツだけを別環境へコピーしても原本領域は登録されない。

## 本文を検索する

「本文を検索」から、Wikiと資料の両方・資料のみ・Wikiのみを選べる。現在の資料・記事、現在の資料フォルダー配下にも絞れる。資料一覧にある従来のファイル名の絞り込みはそのまま使える。

- 全文検索：SQLite FTS5のtrigramを使用。日本語の1〜2文字は部分一致に切り替える。複数の語はAND条件。文字はNFKC・casefoldで正規化する。
- 意味検索：Azureで作成したembeddingを使い、Pythonでcosine類似度を計算する。
- 全文＋意味検索：両方の順位をRRF（係数60）で統合する。

`glossary`分類の用語集は見出しを大小文字まで一致させ、`Meaning`・`意味`の表から検索語を展開する。言語対応のある原文と訳文は同じ記事として検索結果をまとめる。特定の記事に絞る場合はその記事内の断片を返す。

検索結果を押すとWikiの見出し、原本のページ・スライド・シートを開く。Word・MD・TXTは文書全体を開く。本文を選び「選んだ本文からRAGの根拠を取得」を押すと、親文脈を復元し、`[S1]`などの出典を付けた本文を得られる。画面では4,000トークン以内。APIでは100〜16,000トークンを指定でき、省略した部分は引用情報の`truncated`で分かる。回答文の自動生成はこの機能に含まない。

資料の移動・改名・削除、Wikiの更新・削除後は、検索条件と本文の版を再確認する。古い版や検索範囲から外れた本文を根拠として取得することはできない。完了済みの結果も画面で再照合し、更新された出典に対応する根拠本文の表示を解除する。再照合でAzureへの追加要求は発生しない。存在しない資料・フォルダーや空の範囲指定は、処理開始前に拒否する。部分抽出の資料は、保存済みの本文だけを検索し「一部抽出」と表示する。

## 原文・訳文と関連資料

原文と訳文のMarkdownを用意し、任意の`wiki-manifest.json`を一緒に選べる。これは本文ではなく対応情報。フォルダー取り込みでも同じ名前のファイルを自動的に補助情報として扱う。パスは選択したフォルダーからの相対パスとし、今回取り込む記事のみを記載する。

```json
{
  "articles": {
    "original/guide.md": { "language": "en", "translation_group": "guide" },
    "ja/guide.md": { "language": "ja", "translation_group": "guide" }
  }
}
```

同じ分類・`translation_group`の記事を、本文上部の言語メニューで切り替える。`title`で表示名を指定することもできる。対応情報を添えずに本文だけ再取り込みした場合、以前の対応情報を保持する。

Markdown先頭のYAML frontmatterも管理情報として読み取り、本文から除く。従来システム形式の `pages.jsonl` を添付すると、固定ID、`original_path`・`ja_path`、原本のハッシュと翻訳状態を読み取る。今回アップロードした記事だけに適用する。`untranslated`・`needs_review` の日本語準備ページは検索対象から外し、対応する原文が登録されていれば閲覧時に原文を表示する。書き出し原本への付録と目次リンクは検索の根拠に含めない。

大量の記事・CSV目次の取り込みと翻訳バッチは、[Pythonバッチの手順](wiki-batch.md)の `sync`・`import-workspace` を使う。

資料との対応は`source_job_id`（資料のURLの`job`値）と`source_unit`で指定する。登録済み資料のみ受け付ける。本文上部の「関連資料」で原本を開ける。本文中にも`[原本](doc:資料のjob値#page=2)`の形式でリンクを置ける。

## Azure embeddingの設定

起動前に以下の環境変数を設定する。翻訳・解説や外部ナレッジAPIの設定とは独立している。設定を変更したときはアプリを再起動する。

| 環境変数 | 内容 |
| --- | --- |
| `DOCLING_AZURE_EMBEDDING_ENDPOINT` | AzureリソースのHTTPS URL。例：`https://YOUR-RESOURCE.openai.azure.com` |
| `DOCLING_AZURE_EMBEDDING_KEY` | APIキー |
| `DOCLING_AZURE_EMBEDDING_DEPLOYMENT` | 使用するembeddingデプロイの名前 |
| `DOCLING_AZURE_EMBEDDING_MODEL_VERSION` | 実際のモデル名・版を識別する値。モデル差し替え時に変更する |
| `DOCLING_AZURE_EMBEDDING_DIMENSIONS` | 任意。モデルが対応する次元数。未指定ならモデルの既定値 |

設定後、「本文を検索」にある「意味検索の索引を作成・更新」を押す。記事の取り込みだけではAzureへ本文を送らない。新規・変更した本文の索引作成は、この操作で行う。未設定でもWikiと全文検索は使える。

接続はAzure OpenAI v1の`/openai/v1/embeddings`、`api-key`認証を使用する。本文は`cl100k_base`で1断片1,000トークン以内に分割し、1要求最大6断片・6,000トークンとする。設定先のモデル・次元・TPM/RPMを管理画面で確認すること。API仕様は[Microsoftのembedding REST仕様](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/embeddings)を参照。

索引作成と検索のAzure要求は同じキューで直列化する。応答・通信終了後に最低15秒待つ。429等の`Retry-After`／`retry-after-ms`が長ければ、その指定を優先する。通信失敗も15秒待ち、429・サーバーエラー・通信エラーの再試行は最大3回。SDKによる暗黙の再試行は使わない。索引作成は1バッチごとにキューへ戻り、対話中の検索を優先する。

本文と検索文のembeddingを、接続先・デプロイ・モデル版・次元・トークナイザーと本文のハッシュで再利用する。検索文のキャッシュがあればAzureの待ち時間なしで検索する。モデル版を変えると旧索引は使わなくなり、再度索引作成が必要になる。同じモデル版で応答の次元が変わった場合も混在させない。

待ち時間・処理状態はSQLiteへ保存する。再起動後も15秒間隔を守る。通信中にプロセスが落ちた場合は最大180秒の保留時間を残し、実行中ジョブは最終更新から240秒後に失敗状態にする。曖昧な要求を勝手に再送しない。待機中にembeddingの接続設定が変わったジョブも、新しい設定で自動実行せず失敗状態にし、利用者の再実行を必要とする。中止した要求が既に通信中の場合、その通信の終了と待ち時間管理は継続する。

## 保存先とAPI

以下は `DOCLING_DATA_DIR` からの相対パス。Wikiの正本は `content/wiki/revisions/` のMarkdown・CSVと `content/manifests/wiki.json`。画面取り込みでも本文とfrontmatterをファイルに保存する。削除はmanifestに記録し、検索索引から除外する。公開済み本文は復旧用に保持する。Pythonバッチの編集元は従来のワークスペースで、`sync` 時にコンテンツファイルへ公開する。公開済みの版を直接編集せず、再取り込みまたはバッチで更新する。

`runtime/knowledge/local.sqlite` は出典・チャンク・FTS・embedding・ジョブ・待ち時間と検索用本文コピーを保持する。通常のSQLiteで、sqlite-vecは不要。DBを失っても記事IDと管理情報を維持してファイルからWiki索引を復元できる。embeddingや実行履歴はファイルから復元されないので、意味検索の索引は作成し直す。SQLiteはBlobに置かない。外部APIの `.knowledge-api` 保存領域とは共有しない。

資料の原本は `content/documents/<id>/original.*`、表示上の分類は `content/manifests/library.json` が正本。本文索引は検索・状態取得時に同期し、変更のない索引は書き直さない。親文脈は共有保存し、断片ごとには複製しない。旧形式のDBは起動時に断片IDと検索結果IDを保ったまま移行する。移行後の空き領域はSQLiteが再利用するため、既存DBのファイルサイズが直ちに縮むとは限らない。パスと旧Wiki本文の移行は[保存構成と移行](content-storage.md)を参照。

| API | 用途 |
| --- | --- |
| `GET /api/wiki/catalog` | 分類・記事一覧 |
| `POST /api/wiki/import` | `files`・`namespace`・任意の`manifest`をmultipartで取り込む。複数記事は一括で確定する |
| `GET /api/wiki/sources/{id}` | Markdown・安全なHTML・目次・関連資料 |
| `GET /api/wiki/sources/{id}/markdown` | Markdown保存 |
| `GET /api/wiki/sources/{id}/original?path=sources/...` | 原本の読み取り専用画面。`download=true`で元のバイトを取得 |
| `DELETE /api/wiki/sources/{id}` | 記事・索引の無効化 |
| `POST /api/knowledge/search` | 検索を開始。`query,mode,kind,source_id,folder_id,namespace,limit,client_request_id` |
| `GET /api/knowledge/retrievals/{id}` | 検索結果の状態と本文。ポーリングで新しいAzure要求は発生しない |
| `POST /api/knowledge/context` | `retrieval_id,chunk_ids,budget`から出典付き根拠を取得 |
| `GET /api/knowledge/status` | 索引件数・Azure設定状態・待ち時間・索引ジョブ |
| `POST /api/knowledge/index-jobs` | 意味検索用の索引更新 |
| `GET /api/knowledge/index-jobs/{id}` | 索引ジョブの進捗 |
| `POST /api/knowledge/tasks/{id}/cancel` | 待機・実行中の処理を中止 |

検索の応答は、全文検索とキャッシュ済み意味検索では即時完了、それ以外では待機・実行中となる。返された`id`でポーリングする。`client_request_id`を再利用した要求は同じ処理IDになる。同じIDで条件を変えると拒否する。

ブラウザーでの本文編集と回答生成は対象に含めない。CSV目次・従来システム形式のmanifest・用語集展開と調査付き翻訳バッチはPython側で実装している。

検証：[`tests/test_local_knowledge.py`](../tests/test_local_knowledge.py)、[`frontend/e2e/wiki.spec.ts`](../frontend/e2e/wiki.spec.ts)。Azureの実リソースへは送信せず、HTTPの応答形式・待ち時間・再起動時の保持・同時実行・キャッシュ・キュー順序を固定した通信で検証している。

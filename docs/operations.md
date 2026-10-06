# 運用手順

2026年10月6日更新。ここにある手順が現行の操作です。README内のテスト件数は、その変更を入れた時点の記録であり、今の全体結果ではありません。

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


## PowerPointのプレビューだけを修復

本文抽出に成功していて原本プレビューだけが欠落している資料は、保存済みの `document.json`・`slides.json`・`powerpoint-rendered.pdf` を使って修復できます。次の操作はDoclingの再抽出やOfficeのPDF再書き出しを行いません。資料ID・分類・検索用ファイル・保存済み訳文ファイルを保持し、ページ別プレビューと翻訳用展開データを更新します。

```sh
DOCLING_DATA_DIR=/path/to/data .venv/bin/python -m docling_desk.preview.preview_migration --saved-pdf-only --id DOCUMENT_ID
```

`--id` は複数回指定できます。省略すると本文抽出済みのPDF・PowerPoint全資料を対象にします。PDFがない資料、原本と既知のSHA-256が一致しない資料、スライド数・寸法が違う資料は失敗理由を返します。古い資料でハッシュ記録がないPDFは、ページ数と寸法による確認までとなります。保存済み訳文は削除しませんが、原文・テンプレート・素材・生成方式が変わっていれば画面では更新前の訳文として扱います。

原本プレビューがない間も、位置を持つ抽出本文があれば訳文を別枠で表示できます。原本上の文字置換は利用できない理由を状態APIから返します。本文もないページには翻訳対象がない理由を返します。状態取得のエラー後は自動再取得を止め、翻訳ボタンから利用者が再度確認できます。

展開データは `derived/documents/<ID>/translation-source.json` に保存します。原本・抽出本文・スライド情報・HTML/SVG・CSS・フォント・画像・描画PDF・生成方式のハッシュを照合し、変更がなければDocling文書や全ページのテンプレートを解析しません。ファイルの追加・削除も変更として扱います。ファイル属性はローカルでハッシュ再計算を省くために使い、保存する識別子は内容のハッシュです。Blob復元で更新時刻や保存先が変わっても、資料IDと内容が同じなら再利用できます。

`DOCLING_BLOB_SYNC_DERIVED=true` の既存ミラーで、抽出本文・描画PDF・ページ別プレビュー・フォント・展開データ・訳文をまとめて同期します。生成途中の `.tmp` ディレクトリは送りません。ページ資産を送った後にmanifest、展開データ、ジョブ、分類manifestを送り、未送信や競合があれば公開情報の送信を待ちます。

### 2026年10月6日の原因と検証

100ページの固定制限はDesk側の設定でした。合字では複数のUnicode文字に対して描画字形が一つになり、PyMuPDFは後続文字をglyph ID=-1、幅0で返します。旧処理は文字数と描画字形数を同一とみなしていました。[PyMuPDFの公式仕様](https://pymupdf.readthedocs.io/en/latest/functions.html#Page.get_texttrace)に従い、後続文字を含むまとまりを一つの字形に対応付け、Unicodeを全て残したフォント置換とSVGの位置指定を行います。合字を含む行も、翻訳元には行全体の文字列を渡します。

プレビューがnullの状態取得は、nullをスライドのファイルパスとして扱ったため例外になっていました。本文を使う別枠表示への接続を修正しました。また保存済み展開データがあっても全ページを解析していた処理を、変更確認後に保存データを返す処理へ変えました。

macOS 26.6.2 / arm64、Python 3.12.13で、合成した530ページのPPTX（文字、図形、各ページの画像）を使って測定しました。OCRは無効です。ページ生成はSVG・フォント・配置を一ページずつ保存し、一覧HTMLもページ別フレームを遅延表示します。PPTXの構造とDoclingの抽出文書は全体を保持するため、資料に応じたメモリ増加は残ります。

検証用のコードは `qa/performance/powerpoint_preview.py`（変換・保存PDFからの再生成・初回展開・保存データの再利用）、`powerpoint_preview_memory.py`（別プロセスのプレビュー単独測定）、`powerpoint_browser.mjs`（530ページの表示と合字コピー）です。生成した合成資料と測定JSONはGit管理から除外しています。本番資料4件と本番の530ページ原本は、この作業先にはないため検証していません。Stanley Sites内のコピーとAzure本番へのデプロイは行っていません。


| 測定・検証 | 結果 |
| --- | --- |
| Docling変換、保存済みPDFからの原本生成、スライド配置出力 | 530ページ、113.9秒、PythonピークRSS 926.2 MiB |
| 保存PDFからプレビューだけを再生成（同一プロセス） | 6.01秒、ピークRSS 1,039.1 MiB。開始時922.4 MiBを含む |
| 翻訳用展開データの初回生成 | 3.81秒、ピークRSS 1,050.4 MiB |
| 保存済み展開データの再利用10回 | 合計1.67秒（平均0.167秒/回）。全ページの解析関数を呼ばないことも確認 |
| 新規プロセスでのプレビュー単独生成 | 5.12秒、ピークRSS 229.5 MiB（開始時92.9 MiB） |
| 同じ530ページPDFの旧処理との比較 | 旧5.82秒・473.9 MiB、新5.12秒・229.5 MiB。旧処理のページ上限だけ共通設定で外して計測 |
| MacのPowerPointによる530ページPDF書き出し | 21.38秒。続くHTML/SVG生成5.69秒。独立したPowerPointアプリのRSSは測定対象外 |
| Chromeで530ページを順に描画 | 530/530ページ、約49.3秒。SVG・フォント読込と文字描画を確認 |
| 合字を含むPPTX/PDFの選択・コピー・翻訳元 | `office fi fl ff ffi ffl finish` を全て保持。fi/flは実際の合字輪郭、ff/ffi/fflは複数文字のUnicode対応を持つ試験字形 |
| nullプレビュー | 翻訳状態APIは200、本文を使う別枠の翻訳結果も取得。本文がないページは理由を返す |
| 回復しない状態取得エラー | Chromeで422を再現。6.5秒間の要求は1回のみ。単体テストでも30秒間の再取得がなく、明示再試行が可能 |
| 原本・テンプレート・CSS・フォント等の参照素材・生成版の更新 | キャッシュと訳文の原文対応ハッシュを更新。二度目のページ表示はスライド索引を再解析しない |
| 既存資料のプレビュー修復 | 保存済み本文・検索用ファイル・訳文・分類のバイトを保持。抽出とOffice書き出しを呼ばない |
| Blobから空の保存先へ復元 | メモリ内のBlob代替に加え、Azure SDKとAzuriteの両方で成功。プロセス内キャッシュを消した後も展開データを再利用 |

RSSはプロセスの実測値で、変換全体の測定は20msごとに取得しました。一連の変換・再生成・展開・再利用を同じプロセスで実行した際の最大RSSは1,051.8 MiBです。抽出モデルの初期読込やPythonの確保済みメモリも含みます。プレビューだけの値をコンテナ全体の必要メモリと見なさないでください。AzuriteはSDKのAPIバージョンに対応するため `--skipApiVersionCheck` を付け、条件付き書き込みとハッシュ付き復元を確認しました。実Azureの接続、LinuxのLibreOffice、本番原本の530ページでの時間・メモリは未測定です。50 MiBの原本上限、Office展開250 MiB・10,000項目の検査、各処理の時間制限は継続しています。

Python全体はAzure SDK/Azurite試験を含め643件成功・1件スキップ、frontend全体は155件成功でした。変更したPythonのプレビュー・翻訳箇所の型検査、Ruff、frontendの型検査、production build/exportも成功しました。全体のPython型検査には75件の診断（Blob SDKへのオプション渡し、Wiki workerの型など）が残っています。frontend全体のlintにも `Tables.test.tsx` のhook依存警告が1件残ります。変更箇所のlintは成功しています。

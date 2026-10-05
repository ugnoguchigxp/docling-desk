# Linux・Dockerの起動とAzure準備

Python 3.12 / Debian bookwormのLinuxコンテナで、資料のアップロード、Docling抽出、React画面、原本プレビューを動かします。PDFのOCRはTesseract（日本語・英語）、OfficeプレビューはLibreOffice、PPTXのサムネイルは書き出したPDFからPyMuPDFで生成します。macOSでは引き続きApple OCR、PowerPoint、Quick Look、WebKitを使います。

## 初回起動

DockerとComposeが必要です。Apple Silicon MacではLinux arm64、通常のx86_64 LinuxではLinux amd64のイメージを構築します。ビルド時だけパッケージ・モデル取得のためインターネットが必要です。取得元とモデル版は `src/docling_desk/resources/models/manifest.json` に固定しており、抽出時はモデルをダウンロードしません。

```sh
git submodule update --init --recursive
docker compose up -d --build --wait
```

画面は **http://127.0.0.1:18765**。既存のmacOSサーバーの8765とは別ポートです。アップロードした資料・抽出結果・訳文・解説はDockerの名前付きボリューム `<Composeプロジェクト名>_docling-data` に保存し、ホストの既存 `data/` は読み込みません。`docling-desktop` フォルダーから起動した場合は `docling-desktop_docling-data` です。既存データを移す場合は、サーバーを停止した状態で別途コピーしてください。

ボリューム内の `/var/lib/docling/data/` も、原本・Wikiの `content/`、成果物の `derived/`、SQLiteとジョブの `runtime/`、サムネイルの `cache/` に分かれます。旧配置のデータを使う場合は、旧Webプロセスとバッチを停止してバックアップを取得し、新バージョンを起動してください。起動時にIDと原本バイトを維持して移行します。[保存構成と移行](content-storage.md)を参照してください。

`docker run -p 8765:8765` のように直接起動すると、コンテナは全インターフェースで待ち受け、認証なしで公開されます。必ず `-p 127.0.0.1:8765:8765` のようにループバックへ限定するか、同梱のCompose設定を使ってください。

ReactのビルドもDocker内で行います。ホストのPython、Node.js、Office、Swift、認証ファイルを利用する必要はありません。コンテナはUID/GID 10001の一般ユーザーで起動します。

```sh
docker compose ps
curl --fail http://127.0.0.1:18765/health/live
curl --fail http://127.0.0.1:18765/health/ready
docker compose logs --tail 100 -f
docker compose stop
docker compose start --wait
# コンテナを削除しても保存データは残る
docker compose down
docker compose up -d --wait
```

`/health/live` はプロセスの応答、`/health/ready` は画面・モデルの配置、保存先、空き容量2 GiB以上を確認します。readyは未準備なら503を返します。モデル推論そのものの成否は資料変換テストで確認します。`docker compose down -v` は保存ボリュームも削除するため、データを残す停止には使いません。

CPU推論を使います。初期の動作確認は2〜4 CPU、メモリー4〜8 GiBを目安にし、実資料のサイズ・ページ数で計測してください。空き容量はイメージ・ビルドキャッシュの分に加え、データ用に2 GiB以上必要です。GPUは不要です。

## 動作確認

同梱の合成資料だけを使います。PDF・PPTX・XLSX・DOCX・Markdown・テキストに加え、画像だけのPDFを作成して日本語・英語OCRを確認します。資料は検証用ボリュームに新規追加されます。翻訳・解説の外部API呼び出しは含みません。

```sh
docker compose cp samples docling:/tmp/samples
docker compose exec -T docling python scripts/smoke_linux.py --samples /tmp/samples
```

全件 `state: success` となり、プレビュー、表、RAGの取得、PPTX/PDFのサムネイル、Excelのシート切り替え、OCR本文が検証できれば成功です。初期化や変換に失敗したときは `docker compose logs --tail 100` を確認し、原因を直して再ビルド・再実行します。一部完了や失敗を成功扱いにしません。

コード変更時の回帰検証は `python -m pytest -q tests` を使用します。サブモジュール内のDocling本体テストと、過去のQA用テストのコピーは対象にしません。

## 設定

ローカルでは設定なしで起動できます。変更するときは `.env.example` を `.env` にコピーします。`.env` はGitとDockerのビルド対象から除外しています。

Azure OCRを使う場合は、VM上のプロジェクト直下の`.env`へ`DOCLING_AZURE_OCR_ENDPOINT`と`AZURE_DOCUMENT_INTELLIGENCE_API_KEY`だけを記入します。Composeが起動時にコンテナへ渡します。アップロード時にAzure Readを選びます。コード更新後は、VMで最新コードを取得して`docker compose up -d --build --force-recreate --wait`を実行し、イメージとコンテナの両方を更新してください。`.env`はGitに含まれないため、VM側にも作成が必要です。

| 環境変数 | 用途・既定値 |
|---|---|
| `DOCLING_PORT` | Composeのホスト側ポート。18765 |
| `DOCLING_DATA_DIR` | コンテナ内の保存先。`/var/lib/docling/data`。`content/`・`derived/`・`runtime/`・`cache/` の基準 |
| `DOCLING_MODELS_DIR` | コンテナ内のモデル。`/opt/docling/models` |
| `DOCLING_ALLOWED_HOSTS` | 許可するHostをカンマ区切り。Azureでは利用するFQDNを追加。`localhost,127.0.0.1` もヘルスチェック用に残す |
| `DOCLING_ALLOWED_ORIGINS` | 許可するOrigin。Azureでは `https://実際のFQDN` を指定。スキーム・ポートも一致させる |
| `DOCLING_ROOT_PATH` / `DOCLING_AUTH_*` | サブパス公開とログイン共有。[リバースプロキシ配下での公開](reverse-proxy.md)を参照 |
| `DOCLING_TRANSLATION_PROVIDER` | 翻訳接続先。既定は `codex_sdk`。Azureでは必要に応じて `azure_openai` |
| `DOCLING_AZURE_ENDPOINT` / `DOCLING_AZURE_DEPLOYMENT` | Azure OpenAIの接続先・デプロイ名 |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI翻訳用のキー。AzureではSecretから注入 |
| `OPENAI_API_KEY` | Codex翻訳・解説を使う場合の任意設定。ユーザーの `.codex` 全体はマウントしない |

Hostはワイルドカードで開放せず、実際の公開先を指定します。HTTPSの変更要求は明示したOriginかつHostとの一致を確認し、他サイトからの要求は403にします。環境変数を変更したときは `docker compose up -d --force-recreate --wait` で作り直してください。

抽出と表示には外部API資格情報は不要です。翻訳・解説は認証設定が別途必要です。Azure OpenAIの翻訳接続は既存機能を使いますが、Azure OpenAIによる解説は未実装です。

## Azureへ持ち込むとき

今回の作業はローカルLinuxコンテナまでです。Azureリソース作成、レジストリへの送信、クラウド稼働・API認証は実行していません。デプロイ先の一例としてAzure Container Appsを想定します。

Azure用には明示的にLinux amd64をビルドします。Apple Silicon上でarm64のローカルイメージをそのまま送らないでください。CPU版PyTorchを使うためCUDA依存は不要です。

```sh
docker buildx build --platform linux/amd64 \
  --tag docling-desk:azure-amd64 --load .
```

レジストリと接続情報が決まった後の送信例です。`YOUR-REGISTRY` は実際のAzure Container Registry名へ置き換えます。

```sh
az acr login --name YOUR-REGISTRY
docker tag docling-desk:azure-amd64 YOUR-REGISTRY.azurecr.io/docling-desk:linux-v1
docker push YOUR-REGISTRY.azurecr.io/docling-desk:linux-v1
```

Container Appsでは次の設定を適用します。

| 項目 | 設定 |
|---|---|
| ingress | target port 8765、HTTPS。検証はinternal ingressまたはアクセス制限付きで行う |
| プロセス | Uvicorn workerは1。コンテナの起動コマンドをそのまま使う |
| スケール | minReplicas=1、maxReplicas=1、single revision。抽出・翻訳・解説キューはプロセス内管理のため、複数レプリカ・scale-to-zeroには未対応 |
| 保存 | Azure Filesを `/var/lib/docling/data` へReadWriteマウント。UID/GID 10001で書き込めることを確認。コンテナローカルディスクは資料の永続保存に使わない |
| 接続元制限 | FQDNを `DOCLING_ALLOWED_HOSTS`、HTTPS URLを `DOCLING_ALLOWED_ORIGINS` に設定 |
| 認証 | 既定では認証なし。ホストアプリのJWTを共有する `DOCLING_AUTH_MODE=jwt` を使えるほか、ネットワーク設定でも制限する。[リバースプロキシ配下での公開](reverse-proxy.md)を参照 |
| Secret | APIキーはSecret参照から環境変数に注入。イメージやYAMLへキーを直接書かない |

この保存設定は現在のファイルシステム実装向けです。Blob接続・同期は未実装で、ボリュームの設定だけでBlob保存へ切り替わることはありません。Blob対応時のコンテンツ対象は `content/`、必要に応じて `derived/` です。SQLiteを含む `runtime/` と `cache/` はBlobに置きません。

HTTPのstartup/liveness/readiness probeを使う場合は、`Host: localhost` ヘッダーを指定します。ポート8765、startup/livenessは `/health/live`、readinessは `/health/ready`。Host制限があるため、任意の内部IPをHostにしたHTTP probeは使いません。startupは初回ロード時間を許容する閾値に設定してください。DockerのHEALTHCHECKだけでAzure側probeの設定を済ませたことにはしません。

バックグラウンド処理中の再起動は処理を中断します。デプロイ前にジョブ・翻訳・解説の完了を確認し、同じ保存領域に新旧リビジョンが同時に書き込まないよう切り替えます。停止した抽出ジョブは再アップロードが必要です。保存済みの完了結果は再起動後も使えます。複数プロセス化にはキュー・ロック・レート制限を共有する変更が別途必要です。

Azureの設定根拠: [ingress](https://learn.microsoft.com/en-us/azure/container-apps/ingress-how-to)、[ストレージマウント](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts)、[スケール](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)、[ヘルスプローブ](https://learn.microsoft.com/en-us/azure/container-apps/health-probes)。LibreOfficeの変換設定は [起動引数](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html) と [PDF出力引数](https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html) を参照しています。

## 表示の違いと制約

- PPTXはLibreOffice ImpressからPDFを生成し、既存のHTML/SVG変換へ渡します。非表示スライドも含め、ページ数・寸法を検証し、原本は変更しません。Microsoft Office専用の図形・フォント・SmartArt等には差が出ることがあります。
- XLSXとDOCXはLibreOfficeが出力したHTMLを既存のサンドボックス表示へ渡します。Excelはシート単位、Wordは文書全体で表示します。XLSXのHTMLでは保存値と異なる再計算結果が表示される場合がありますが、Doclingの抽出は原本から独立して行います。
- PDFのOCRエンジンが変わるため、macOSと抽出文字や位置が完全一致するとは扱いません。日本語・英語以外はTesseract言語データとOCR設定の追加が必要です。
- 既存の保存結果は自動一括変換しません。macOS用プレビューのキャッシュは継続表示できます。Linuxで新規追加した資料はLinux用の処理で生成します。

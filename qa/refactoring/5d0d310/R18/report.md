# R18 導入と機能別の診断（バックエンドと文書）

対象版: 5d0d310 + 未コミット変更。状態: 検証待ち（画面接続はR04側）。

## 診断契約

`GET /api/diagnostics/features`（`src/docling_desk/api/health.py` のrouterに追加、`app.py` 変更なし）。

- 認証: `/health/*` と `/static/*` 以外なので既存の `authenticate` が適用される。`DOCLING_AUTH_MODE=jwt` の未ログインは401（`login_url` 付き）、不正トークンは403。`none` ではループバック限定で他のAPIと同じ。
- 副作用なし。外部通信・子プロセス起動なし（テストで socket と subprocess を禁止して確認）。`Cache-Control: no-store`。資格情報・パス・接続先URL・モデル名を返さない。
- `/health/live`、`/health/ready` の本文・HTTP statusは変更なし。
- 取得は画面を開いたとき、復旧操作の後。短い間隔の再取得は避ける（モデル検証でファイルを読む）。

```jsonc
{
  "version": 1,
  "platform": "darwin" | "linux" | "other",
  "features": [
    {
      "id": "local_viewing",            // 下表の9件。この順
      "label": "ローカル閲覧・全文検索",
      "description": "…何に影響し、何は使えるか",
      "optional": false,                 // true: 未設定でも製品の中核は使える
      "depends_on": ["conversion"],      // 情報のみ。statusには影響しない
      "status": "available" | "not_configured" | "temporary_failure",
      "reasons": ["models_missing"],     // 不足している必須前提のcode
      "recovery": ["…復旧方法（日本語）"],  // 重複除去済み
      "notes": ["azure_ocr_not_configured"], // 任意前提の不足。statusに影響しない
      "prerequisites": [
        {"id": "models", "label": "抽出モデル", "required": true,
         "status": "ok" | "not_configured" | "temporary_failure" | "disabled",
         "code": null | "models_missing", "recovery": null | "…"}
      ]
    }
  ]
}
```

statusの規則: 必須前提に `not_configured` が1つでもあれば `not_configured`、なければ `temporary_failure` が1つでもあれば `temporary_failure`、他は `available`。任意前提（`required:false`）は `notes` と `prerequisites` に出るだけ。1機能の診断が例外を出しても、その機能だけ `temporary_failure`/`diagnostic_error` になり他は影響を受けない。

表示の指針: `available` は通常表示。`not_configured` で `optional:true` は案内（警告にしない）。`optional:false` の `not_configured`・`temporary_failure` は復旧が必要な表示。`recovery` をそのまま見せてよい。

## 機能と前提

| id | 必須 | 任意（notes） |
| --- | --- | --- |
| local_viewing | frontend構築物、書込み可の保存先、空き2 GiB以上、Blob復元の未完了なし | なし。モデルとクラウド設定は無関係 |
| conversion | models（manifest一致）、空き容量、docling、ローカルOCR（macOS Apple / Linux tesseract） | azure_ocr |
| office_presentation | macOS: PowerPoint.app / Linux: LibreOffice | なし |
| office_documents | macOS: /usr/bin/qlmanage / Linux: LibreOffice | なし |
| tex_preview | tectonic、隔離（macOS sandbox-exec / Linux bwrap） | なし |
| semantic_search | DOCLING_AZURE_EMBEDDING_* 4つ（一部のみは `embedding_invalid`） | なし |
| translation | 選択providerの設定（codex_sdk: SDKと認証 / azure_openai: HTTPS接続先・デプロイ・キー） | なし |
| explanation | Codex SDKと認証 | web_search（brave: キー、duckduckgo・disabled: 不要） |
| blob_storage | DOCLING_STORAGE=azure-blob と、同期の復元・競合・エラーなし | なし。local は `blob_not_configured` |

modelsの状態: `missing/revision/incomplete/unverified/manifest_*` は `not_configured`（取得または検証コマンドを案内）、`interrupted` は `temporary_failure`（`models recover`）。両方あるときは `interrupted` を優先して案内する。

## 追加・変更

- `src/docling_desk/api/diagnostics.py`（新規）、`api/health.py`（route追加のみ）、`operations/models.py`（`diagnose`、各hint）、`cli.py`（`--diagnose [--json]`。サーバー非起動。終了コード: 必須機能が全て available なら0、そうでなければ1）。
- README、docs/operations.md、docs/linux-docker.md を、macOS（ソース）とLinux（Docker）それぞれ一つの導入・初回モデル取得・更新手順に統一。
- tests: 新規 `tests/test_feature_diagnostics.py`（22件）、`test_cov_models.py`・`test_cov_entrypoints.py` に各追加。

## 未実施

- 新規Linux環境でのDockerビルドと起動（`docker compose up -d --build --wait`、`--diagnose` の実機出力）。
- 初見の利用者による導入・復旧の操作確認。
- 画面の設定不足案内（R04）。

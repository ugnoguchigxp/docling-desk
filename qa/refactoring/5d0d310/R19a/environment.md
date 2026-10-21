# R19a 環境記録

採取日: 2026-10-21。値は版・ハッシュ・状態のみ。キー、トークン、接続先、通常環境の設定値は記録しない。

## 対象版

| 項目 | 値 |
| --- | --- |
| 基準commit | 5d0d3109791cde560b64acb22c193c52ae9e9098 |
| 基準ソース識別値 | 5b8dcc9b3122238e099d96d60ca0c7b65a92d2985a6645b72c0539e8dce7bb89（1018ファイル。git ls-tree の5d0d310から再計算して一致を確認） |
| 識別値の方法 | `distribution_evidence.source_digest`。baseline.json の方法と同一。`docs/refactoring/` と `qa/refactoring/` は対象外（計画と証拠は識別する版に含めない） |
| 採取時の作業ツリー | 他作業者の変更が並行して進行中（dirty 49ファイル、1035ファイル、識別値 d9dcd62160930f74fe4f42ed85ba3217dd4d6045e354c7bc192100f24d3a3769）。これは固定版ではなく、R19aの完了版としては使わない |
| 検証入口で版を残す方法 | `python -m docling_desk.operations.verify` の出力 `source`（commit、dirty、識別値、docling、model manifest、lockfiles）。`distribution_evidence` の出力にも同じ `source` |

## Docling・モデル・ロック

| 項目 | 値 |
| --- | --- |
| docling submodule revision | a1c5ff2a8c5ab2bf87d7c1eb896b5bc351f51220（commit記録値と作業ツリーの実体が一致） |
| model manifest (src/docling_desk/resources/models/manifest.json) sha256 | 403f3abf5f1c98acd9bd3430cda0445d4ef98fb0ca9634364b7d55af2720cbef |
| manifest の revision | docling-layout-heron 8f39ad3c0b4c58e9c2d2c84a38465abf757272d8、docling-models fc0f2d45e2218ea24bce5045f58a389aed16dc23 |
| ローカルのモデル | models/ に2ディレクトリあり、各 verified.json の revision がmanifestと一致。採取時に `operations.models` は実行していない（シール書込みを伴うため、他作業の最中に実行しない） |
| requirements-lock.txt sha256 | 4c968f6ef9216f07f7dd11bbe3de8daaf68f3e0431a36abe9ad507f84b2e25ca |
| frontend/pnpm-lock.yaml sha256 | ec164808d6683555358a61552f697c51a4abc53cf98df7a1442d9a8fa64d1569 |
| knowledge-api/bun.lock sha256 | 616c41c350ecb1870032dd413815f96f5a0de24625bcc812b3d7bbe6a0c3c4ae |

## OS・runtime

| 項目 | 値 |
| --- | --- |
| OS | macOS 26.6.2 (25G83) arm64、Darwin 25.6.0 |
| Python | 3.12.13 (.venv) |
| Node | v24.11.1（CIは22を指定。差はあり） |
| pnpm | 10.24.0 |
| Bun | 1.4.3（CIは1.4.2を指定。差はあり） |
| git | 2.52.0 |
| 主な Python 依存 | torch 2.14.1、torchvision 0.29.1、docling-core 2.99.0、docling-azure-ocr 0.1.0、docling-desk 0.1.0、fastapi 0.142.2、uvicorn 0.54.0、PyMuPDF 1.28.2、mcp 1.26.0、pytest 9.1.1、ruff 0.16.10 |
| 主な frontend 依存 | react 19.3.0、vite 8.3.2、vitest 5.0.3、typescript 6.0.2、@excalidraw/excalidraw 0.18.1、@playwright/test 1.63.0 |
| browser | Chrome（`channel: chrome`。CIはchromium）。Playwright cacheにchromium_headless_shellあり |
| 依存の状態 | 既存の .venv・node_modules を参照した検証。新規導入の検証ではない |

## 起動設定

- 検証入口はすべて `DOCLING_ENV_FILE=/dev/null`（verify.py は子プロセスにも設定）。通常環境の接続設定は読み込まない。
- シェルにクラウド・APIキーに見える環境変数が存在するが、値は記録せず、検証入口は読み込まない設定で実行する。
- 図編集e2eは `UI_SYNTHETIC=1`（リポジトリ内の合成fixtureから隔離データを作るQA server）。

## R19aで実行したこと・していないこと

| 項目 | 状態 | 備考 |
| --- | --- | --- |
| `tests/test_distribution_evidence.py` `tests/test_verify_scopes.py` | 実行済み・12 passed | 追加分を含む。DOCLING_ENV_FILE=/dev/null |
| ruff check / format --check（変更したPython） | 実行済み・合格 | |
| `frontend/e2e/diagram.spec.ts` のtsc・eslint・prettier | 実行済み・合格 | `pnpm exec tsc --noEmit`、eslint、prettier --check |
| diagram e2e 3シナリオ | not_run | webServerがQA serverを起動するため実行しない。利用者の許可後に supervisor が `pnpm test:e2e:synthetic`（または `--project=diagram`）を実行する |
| `verify --scope browser` / `--scope distribution` の実実行 | not_run | e2e・build・model取得を伴う。単体試験は実行を差し替えた模擬で入口の分岐のみ確認 |
| `.github/workflows/quality.yml` の `distribution-candidate` ジョブ | not_run | workflow_dispatch専用。YAML構文のみ確認。GitHub上で未実行 |
| Docker、実Azure、保存済み実資料 | not_run | 範囲外 |

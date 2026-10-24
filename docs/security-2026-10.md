# 2026年10月 Node.js・eMudhra/emSign 対応記録

調査日: 2026-10-22（日本時間）。対象: このcheckout、プロジェクト仮想環境、ローカルのLinux arm64イメージ。外部環境への接続やデプロイ権限は使用していない。

## 1. 結論

**一部対応。本番反映は未実施。** Node.js本体の修正版への移行、Pythonのアプリ用・pip内蔵CA、Docker各段階のOS側CA更新を実装した。ローカルで修正版の実行と問題CAチェーンの拒否を確認した。Bun安定版の内蔵CAには影響が残り、全環境の対策完了とは判定しない。

A. Node.js本体: ローカルの24.11.1、CIの22指定、Dockerの24指定を調査した。プロジェクトの指定と検証実行を24.22.1へ揃えた。この版はBrotli、TLS、DNS等の対象修正とCA更新を含む。Python最終イメージにNode.jsはコピーされない。

B. CA問題: 自社サーバー証明書の発行元にかかわらず、クライアントの信頼ストアに影響する。実際に変更前のPython・Node.js・Bunが問題CAの公開検証サイトを受け入れることを観測した。更新後のPython・Node.jsは拒否した。BunはOS側CAだけ更新しても一部チェーンを受け入れた。

稼働中サービスの停止、本番への反映、利用者データの削除、commit/pushは行っていない。開始時の4件（docs/wiki.md、src/docling_desk/knowledge/store.py、src/docling_desk/knowledge/diagram_text.py、tests/test_global_search_media.py）の変更を保持した。アプリの意味判断・プロンプト・API契約の変更は不要と判断し、既存の導入経路を共通のハッシュ付き初回更新手順へ接続した。

## 2. 調査・対応一覧

| 対象 | 変更前 | 変更後 | 影響判定 | 変更ファイル | 根拠URL |
|---|---|---|---|---|---|
| Node.js本体・内蔵CA | ローカル24.11.1、CI 22、Docker node:24-bookworm-slim、engines >=22.12.0 | 検証24.22.1、CIは.node-version参照、Dockerは24.22.1-bookworm-slim、engines >=24.22.1 <25 | A/B対応。24系列を維持しCIも統一 | .node-version、frontend/package.json、Dockerfile、deploy/Dockerfile.gateway、.github/workflows/quality.yml | [セキュリティ告知](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)、[24.22.1](https://nodejs.org/en/blog/release/v24.22.1) |
| アプリcertifi | lock・venv 2026.7.22 | 2026.10.19 | B対応。httpx/requests/明示certifi SSLContextへ反映 | requirements-lock.txt、pyproject.toml、integrations/mcp/requirements.txt | [certifi勧告](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)、[Python公式説明](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576) |
| pipと内蔵certifi | venvにpipなし。新しいPythonベースでもpip 25.0.1 | venv・全Pythonイメージでpip 26.2.2、vendored certifi 2026.10.19 | B対応。ensurepipによるオフライン導入後、配布wheelのSHA-256を必須検証 | requirements-bootstrap.txt、scripts/bootstrap-python.sh、全Python Docker段階、CI、README、docs/operations.md、MANIFEST.in | [公式初回更新方法](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)、[certifi配布ハッシュ](https://pypi.org/pypi/certifi/2026.10.19/json) |
| uv | ホスト0.9.17。venvにはなし。リポジトリの導入処理は未使用 | プロジェクトvenvに0.13.5をハッシュ付きwheelで導入 | プロジェクト用は対応。外部管理のホストuvは残存 | 追跡ファイルのuv依存定義・lockは存在しないため追加せず。本記録に実測を保存 | [修正下限0.13.4](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)、[導入版とハッシュ](https://pypi.org/pypi/uv/0.13.5/json) |
| Debian bookworm CA | NodeベースにCAパッケージなし。再取得したPythonベースは既に修正版 | 全Node/Python段階でca-certificatesを更新し20250419~deb12u2以上を強制 | OS側B対応。pip/内蔵CAとは別に確認 | Dockerfile、deploy/Dockerfile.gateway | [Debian LTS](https://lists.debian.org/debian-lts-announce/2026/10/msg00039.html) |
| BunのDebian trixie CA | oven/bun:1.4.2-slim、20250419 | 同ランタイムで20250419+deb13u1 | OS側のみ対応 | knowledge-api/Dockerfile | [Debian DSA](https://lists.debian.org/debian-security-announce/2026/msg00487.html) |
| Bun内蔵CA | 指定1.4.2、ローカル1.4.3 | ランタイム版は維持 | B未対応。修正PRはマージ済みだが公開安定版への収録未確認。実測でも受け入れが残る | knowledge-api/Dockerfileに残存リスク明記 | [修正PR](https://github.com/oven-sh/bun/pull/46159)、[公開版](https://github.com/oven-sh/bun/releases)、[公式環境変数説明](https://bun.sh/docs/runtime/environment-variables) |
| ホストOpenSSL CA | Homebrew ca-certificates 2026-08-13 | 2026-10-20 | 標準ssl/urllib経路で拒否を確認 | ホストパッケージのみ | [Homebrew公式メタデータ](https://formulae.brew.sh/api/formula/ca-certificates.json) |
| CI Ubuntu OS CA | ubuntu-latest任せ | 各ジョブでCA更新と版出力 | 設定対応済み。GitHub上の実行は未実施 | .github/workflows/quality.yml | [Ubuntu USN](https://ubuntu.com/security/notices/USN-8944-1)、[公式更新方法](https://ubuntu.com/blog/emudhra-ca-compromise-fixes-available) |
| 配布台帳 | 古いNodeタグ/certifi版、CA追加未記録。パッケージ抽出が先頭の導入行だけ | 台帳更新、導入行を全件集計・重複排除、回帰試験追加 | 更新に伴う棚卸し欠落を修正。ライセンス承認は増やしていない | supply/clearance.json、src/docling_desk/operations/supply.py、tests/test_supply_inventory.py | リポジトリの既存台帳契約と再現した検証失敗 |

Node修正判定は公式リリースに基づく。npm auditは本体修正の証明に使用していない。一般のnpm/Python依存物は一括更新していない。

### 指定と実行環境

- Python指定は>=3.12,<3.15、Docker/CIは3.12。venv実測はHomebrew Python 3.12.13 / OpenSSL 3.6.3、再取得ベースと再ビルドイメージはPython 3.12.15 / OpenSSL 3.0.22。
- ホスト既定のPython 3.14.7はプロジェクトに使わない。そこにはpip 26.2.1 / certifi 2026.2.25が残る。OS管理のPythonへ強制pip更新はしていない。
- Node 24.22.1はmiseの独立したインストールを使用。既定PATHの24.11.1は残るため、プロジェクト作業では下記のmise指定または.node-versionを扱える管理ツールで修正版を選択する。バージョンファイルだけでシェルが切り替わるとは扱わない。
- Bunのprocess.versions.node表示はNode互換APIの識別値であり、Node.jsバイナリの実行版とは判定しない。Bunは独自のTLS実装・内蔵CAを持つ。
- frontend/pnpm-lock.yamlとknowledge-api/bun.lockは維持。package managerはpnpm 10.24.0。Pythonのlockはrequirements-lock.txt。uv.lock/poetry.lock、別のvenv、devcontainerは対象checkoutで見つからない。doclingサブモジュールは固定commitを維持。
- Alpine、Red Hat/UBI、scratch、distrolessはプロジェクトのDockerfileに使われていないため、対象OSとしての更新は不要。コンテナ利用のみを理由に影響ありとは判定していない。
- ベースタグは元からdigest固定ではない。調査で取得したdigestと実パッケージ版を下記へ記録し、各段階でOSパッケージの下限を検証する。今後同タグを取得しても同じ内容とは保証しない。

### TLS経路と上書き

- Azure OCR/翻訳/embedding、Wiki provider、viewer gateway、MCP/Open WebUI連携: httpx。既定はcertifiだがSSL_CERT_FILE/SSL_CERT_DIR等の上書きに注意する。
- explanation/web.py: 独自のソケット接続を使うがSSLContextはcertifi.where()を明示。更新したアプリ用certifiが対象。
- requests/urllib3（Azure SDK等）: certifi。Python標準ssl/urllib: OpenSSL既定のOS CA。両方を個別に接続試験した。
- パッケージ取得: npm/pnpmはNode、pipは独自に同梱したcertifiとOS truststore、uvは別の内蔵TLS経路。実際のpip/uvでPyPIへの取得と問題CA拒否も確認した。pipの拒否メッセージはmacOSの取り消しエラーで、OpenSSLの英語エラー名ではなかった。
- モデル取得: huggingface-hub/httpxとhf-xet等のネイティブ補助処理。本体イメージのモデル取得は成功したが、ネイティブ補助処理の全TLS経路の拒否試験は未実施。
- Knowledge APIのworker/viewer/embedding/answer通信: Bun fetch。ループバックのhealth check成功は外部TLSの安全性の証明にならない。
- 配布Python依存物にCodex CLI 0.160.0のネイティブバイナリ（Rust等）が含まれる。Python certifi更新やNode更新が自動的にこのバイナリへ及ぶとは判定しない。[固定版の上流依存定義](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/Cargo.toml)でnative roots関連を確認したが、全接続経路の選択を確定できていない。実API認証を伴う通信は未試験。
- 独自CAバンドル、追加証明書ファイル、TLS検証を無効化する設定は追跡対象コード・デプロイ設定に見つからない。現在のプロセス環境と.envはCA/プロキシ設定の**キー名だけ**を確認し、対象上書きなし。秘密の値は出力していない。外部管理の本番環境変数は未確認。
- 確認対象のキー: SSL_CERT_FILE、SSL_CERT_DIR、REQUESTS_CA_BUNDLE、CURL_CA_BUNDLE、PIP_CERT、PIP_TRUSTED_HOST、UV_SYSTEM_CERTS、UV_NATIVE_TLS、UV_INSECURE_HOST、NODE_EXTRA_CA_CERTS、NODE_OPTIONS、NODE_TLS_REJECT_UNAUTHORIZED、プロキシ関連。
- BunのSSL_CERT_FILE/SSL_CERT_DIR/--use-system-caは内蔵CAへの追加となるため、今回の回避策には使用していない。検証無効化も導入していない。

### 取得ベースのdigest（linux/arm64で実体を確認）

| ベース | 取得時RepoDigest |
|---|---|
| node:24.22.1-bookworm-slim | sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 |
| python:3.12-slim-bookworm | sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 |
| oven/bun:1.4.2-slim | sha256:cb3bbbb08e13a4a2ff400f24c7a2a1d5efa83f6ef8544d52d95a519631e2fc61 |

## 3. 検証結果

部分検証→結合→通しの順で実施。ログはローカルの.cache/security-2026-10/。本番資格情報・原本・業務データを検証先へ送っていない。CA検証先は補助資料掲載の公開サイトを使用し、2種類のチェーンを試験した。全8中間CA・全クロス署名経路の網羅試験ではない。DNS/タイムアウトは成功に数えない。

| 検証 | 結果 |
|---|---|
| ハッシュ付きPython初回更新 | 成功。venv pip 26.2.2、アプリ用/内蔵certifi 2026.10.19 |
| pip check | ローカルvenv、本体イメージで成功 |
| 関連Pythonテスト | 93成功 |
| 配布台帳関連テスト | 37成功（追加回帰試験を含む） |
| frontend frozen install・型確認・417件・lint・build/export | 成功。Node 24.22.1 / pnpm 10.24.0 |
| Knowledge API | ローカルBun 1.4.3のtest/typecheck/lint/契約検査成功。lintは既存の警告あり。最終コンテナBun 1.4.2でも103成功・0失敗（ネットワークなし、checkoutを読み取り専用マウント） |
| OS CA・Python・Node TLS | ローカルPython 25項目、Node 8項目、本体Python 25項目、gateway Pythonは初回20項目、最終イメージで19項目成功・1経路通信エラー。該当urllib経路を例外型・verify_code付きで追加診断し、SSLCertVerificationError / code 20による拒否を確認した。初回の通信エラーを成功へ読み替えていない。frontendイメージNode 8項目が成功。gatewayにrequestsは未導入のため明示スキップ |
| 正常なTLS先 | Python: PyPI/Hugging Face/PyTorch、Node/Bun: npm registry/nodejs.org。HTTPエラー応答もTLSハンドシェイク成功と区別して記録 |
| 問題CAのTLS先 | Python/Nodeは両方を証明書エラーで拒否。ローカルBunは4項目失敗、コンテナBunは2項目失敗（問題CAを受け入れたため）。ネットワークエラーを拒否成功と扱っていない |
| イメージ構築 | 本体・gateway・Knowledge APIを--pull --no-cacheで構築し、最終ソース変更後にも本体/gatewayを--pullで再構築。frontend/models段階の実体も別タグへ保存し確認 |
| 本体イメージhealth | TestClientで/health/live、/health/readyが200。既存ポート・データvolumeへ接続していない |
| gateway起動 | 未設定ではVIEWER_CONFIG_FILE不足で失敗。合成設定と上流のhealth/scopes応答を契約通り用意した最終イメージではlive/readyとも200。途中の補助試験の設定・モック不足は製品障害として扱っていない |
| Python lint | 成功 |
| Python追加型チェック | 失敗。ty checkはサブモジュールを含め1,654診断。今回未変更箇所も多数あり、全件を今回起因とは判定していない |
| Compose構文 | 通常構成とgateway併用構成のquiet検査成功。秘密を展開した設定は出力していない |
| 最終Python全体 | **1,248成功・4スキップ**。最初の全体実行は1,243成功・4失敗・4スキップ。台帳修正後の全体再実行で成功。HTTP 500だったbenchmarkは変更なしの単独試験と最終全体で成功したため、一時失敗の原因は未確定として残す |
| 最終配布物検証 | **成功**。sdistからwheel生成、checkout外のインストール・起動・資源・合成取り込み/検索/削除、別状態領域を確認。最終wheel 26,254,529 bytes |
| 合成ブラウザー | **87成功・7失敗**。印刷3件、bundle/CSS/Origin、欠落chunk、TeX、図編集。更新との因果は未確定。失敗箇所の製品コードや期待値を変更していない |

再実行例（リポジトリルート、必要な依存物導入後）:

```sh
sh scripts/bootstrap-python.sh .venv/bin/python
.venv/bin/python scripts/verify_tls.py
mise exec node@24.22.1 -- node scripts/verify_tls.mjs
bun scripts/verify_tls.mjs  # 現在は残存リスクを検出して非ゼロ終了する
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest tests -q
mise exec node@24.22.1 -- pnpm --dir frontend build
```

最終イメージID（すべてローカル専用タグ）:

| タグ | ID |
|---|---|
| docling-desk:security-20261022 | sha256:fd6f4c4f34db2e982ac7ce688c419360451dc7fe4945362404afb6a2a10e92f4 |
| docling-desk-gateway:security-20261022 | sha256:c1d45f35172ec8699408f19d9c5b3951cc0a31cfeef4de32bb2e55290b0ea78e |
| docling-knowledge:security-20261022 | sha256:7fb0a46e8d01abaf4d2ff54dce6b720178318f4cd1df9cc172275b413ac84e59 |

配布台帳のreviewer検査には今回の実施者Codexを追加した。承認条件・未承認ライセンスの扱いを緩和せず、status=inventoriedと人の確認未了を維持した。Docker構文の集計以外に意味判断を置き換える条件分岐や、検証サイト専用の製品処理は追加していない。

ブラウザー未解決箇所（詳細は.cache/security-2026-10/browser.log）:

- e2e/print.spec.ts: lazy images、retry reloads a failed print shell、print synthetic-glyphs。
- e2e/operations.spec.ts: bundle delivery/CSS/Origin、missing table chunk、TeX upload。
- e2e/diagram.spec.ts: 図形とテキストの作成・保存・再読込。

## 4. 残作業

- Bunの修正入り安定版公開と実バイナリを確認後、package.json・CI・Docker両段階を更新し、同じfetchクライアントで拒否を再確認する。公開前のcanaryや独自CAフィルターは導入していない。現状のKnowledge APIを外向きTLSまで対応済みとして本番展開しない。
- 本番ホストOS・CA、独自信頼ストア・環境変数、CIサービス側の実行基盤、外部配置のOpen WebUI/MCPクライアントは個別確認が必要。GitHub Actions自体が使用する管理ランタイムの安全性は今回のNode指定とは別。
- 本番で最新ベースを取得して対象アーキテクチャ向けに再ビルドし、イメージ内部の版とTLSを確認した後、保守計画に従ってコンテナを再作成・再起動する。本作業では実施していない。amd64の実行は未検証。
- ホストの旧Node 24.11.1、uv 0.9.17、Python 3.14.7側pip/certifiはプロジェクト外にも影響するため残存を明記する。プロジェクトには検証したNode/venvの実行パスを使う。新しいvenvは毎回bootstrapを実行し、ensurepip同梱の旧wheelだけでネットワーク取得しない。
- Azure OpenAI/OCR/Blob、実Webhookや任意のユーザー設定URL、Codex CLI・hf-xet等の全TLS経路は未検証。課金・認証・業務データ送信を伴う機能テストはしていない。各実環境で同じクライアント・同じCA設定を使い、認証情報を渡さない接続試験から追加確認する。
- ブラウザー7件と追加型チェックの失敗は、更新との因果と原因の確定が必要。無関係な製品修正には別途作業依頼が必要。今回の安全条件・テスト期待値を下げて全件成功にしていない。

## 5. 運用上の注意

信頼取り消し後は、問題のルートへ連なる正規サーバーも接続できなくなる場合がある。接続先に安全な別チェーンへの切り替えを依頼する。独自CA、代理サーバーの追加CA、SSL_CERT_DIR内のハッシュファイルに古いルートが残ると、更新したバンドルへ追加されて信頼が復活する可能性があるため別途点検する。

ロールバックは今回の変更ファイルだけをレビューして戻す（既存4件を巻き戻さない）。本番の切り戻しには事前に保存したイメージdigest/設定を使い、データvolumeは変更・削除しない。ただし古いランタイム・CAへ戻すと脆弱性が再導入される。機能上の変更を戻す場合も修正版ランタイム/CAを維持したイメージを再構築する。TLS検証の無効化で回避しない。

Homebrewは当初依存先の一括更新も提案したため、適用前にその処理を中断し、依存先自動更新・cleanupを抑止してca-certificatesだけ更新した。ほかのランタイムやサービスをこの操作で更新・再起動していない。

## 追跡検証（2026-10-22）

ブラウザー失敗7件と型診断の追跡は[続報](security-followup-2026-10.md)を参照。最終ブラウザーは94件すべて成功。今回増えた型診断2件を解消し、既存1,652件は依頼範囲に従って切り分けた。上記の87成功・7失敗および型診断1,654件は初回調査時点の記録である。Bun内蔵CAや本番反映等の残作業は継続する。

## 依存の脆弱性とハッシュ固定（2026-10-24）

- Pythonの `requirements-lock.txt` と新しい `requirements-torch.txt` を、PyPIとPyTorch CPU indexのsha256で固定した。導入・Docker・CIは `--require-hashes` で検証する。macOS arm64で全117件をハッシュ照合付きで取得できることを確認した。
- pip-audit 2.10.1で、filelock 4.2.1、opentelemetry-api 1.45.1、setuptools 84.1.0へ更新した。
- pillow 12.3.0の既知の脆弱性37件は、doclingの一括更新で解消した。doclingサブモジュールをv2.142.1（`498c1b6`）、docling-slim 2.142.1、docling-core 2.104.0、docling-parse 7.22.4、docling-ibm-models 4.0.4、doclang 0.8.0、pillow 13.0.0へ更新し、PowerPoint変換に必要になったpylatexenc 2.11を追加した。pip-auditは0件で、CIは除外なしで監査する。
- knowledge-apiは hono 4.13.13、zod 4.6.7へ更新した（bun auditで0件）。zod更新に伴うOpenAPIの差分は `anyOf` の表記と `format` 注記だけで、契約の意味は同じ。
- frontendはpnpm overridesで、`@excalidraw/excalidraw` を0.18.1に統一し、配下のmermaid・dompurify・nanoid・lodash-esと、js-yaml・viteを修正版にした。配信物（`--prod`）の残りはKaTeXのlow 2件（0.18.2で修正、Plateの互換確認が必要）。開発時だけ使うjsdom配下のundiciのhighは、修正版の公開後日数が浅いため見送った。
- CIはActionをcommit SHAで固定し、bun audit・pnpm audit（`--prod`、high以上）・pip-audit・`ruff format --check` を追加した。

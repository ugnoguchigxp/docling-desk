# ナレッジAPIの検証記録

2026-10-04 のレビュー後の結果は [レビューと改善記録](review.md) を参照。以下は初期実装時の確認記録。

対象は `knowledge-api/`、`knowledge_worker.py`、`deploy/compose.knowledge.yml` と保存済みOpenAPI契約。Bun 1.4.2、既存Python 3.12環境、DockerのLinux arm64コンテナで確認した。別作業で追加中の既存デモ用Linux環境も回帰テストに含めたが、このAPIは別のDB・原本・ポートを使う。

## 実行結果

| 検証 | 結果 |
|---|---|
| `bun run typecheck` | 成功 |
| `bun run lint` | 成功 |
| `bun run contract:check` | 成功。実際のルート、要求・応答のZodスキーマ、保存したOpenAPIの一致を確認 |
| `bun test` | 26件成功、失敗なし。127 assertion |
| `bun run test:integration` | 成功。付属の認証付きmultipartクライアント→実HTTP→Python/Docling→FTS→親文脈→削除→物理削除 |
| `bun run test:docker` | 成功。最新API・処理サービスをビルドし、両コンテナのhealthyを確認。下記6形式がすべて成功 |
| `PYTHONPATH=. .venv/bin/pytest tests -q` | 288件成功、1件スキップ。Linuxの実LibreOffice専用テストはMacでは実行条件外 |
| Ruff / ty | API用Python処理サービスとそのテストが成功 |
| `git diff --check` | 成功 |

Dockerの合成資料：

| 形式 | 資料 | 検証 |
|---|---|---|
| テキスト | `synthetic-api.txt` | 登録、実抽出、FTS、親文脈、位置情報、削除 |
| Markdown | `synthetic-api.md` | 上記と構造化された表 |
| PDF | `synthetic-report.pdf` | 上記と表・ページ出典 |
| Word | `synthetic-report.docx` | 上記と表。ページが得られない場合に仮のページを作らない |
| Excel | `synthetic-sheet.xlsx` | 上記と表・シート出典 |
| PowerPoint | `synthetic-slides.pptx` | 上記とスライド出典 |

Docker検証プロジェクトは `docling-knowledge-qa-de620a85`。終了時に検証専用コンテナ、ネットワーク、2つの保存ボリュームを削除した。普段のサーバーや保存ボリュームを再作成していない。合成資料以外のアップロード、実モデルgatewayへの要求、Azureへのデプロイは行っていない。

## 確認した境界条件

- クライアントトークンと利用者JWTの両方を検証。期限・issuer・audience・署名方式・鍵ID・許可範囲の不一致を拒否。batchの登録範囲を確認。
- Originのある要求を拒否。権限外の資料・原本・ジョブIDは404。別案件・地域の候補と処理待ち件数を表示しない。source_kind・languageフィルターが索引状態にも適用される。
- 日本語の2文字とAPI識別子をFTS検索。semanticは決定的なベクトルで、FTSでは一致しない語を実際に検索。未設定・障害時のFTS切り替えを確認。
- 検索スナップショットと根拠は利用者に結び付く。上限超過、別利用者、返していない参照を拒否。
- 差し替え直後に旧版を通常検索から外し、古い根拠を409にする。明示的な過去版検索、根拠取得、原本URLを確認。
- 同じIdempotency-Keyと内容は同じ資料ID、内容変更は409。最初のIf-Matchによる差し替えの再送は同じ結果。
- 削除直後にすべての読み取りを拒否し、その後に全版・FTS・未参照Embeddingを削除。遅れて完了する抽出が資料を復活させない。
- プロセスの所有リース、ジョブの実行番号、期限切れジョブの復旧を確認。同じDBを別APIが同時に所有できない。
- 再索引時に原本の再抽出をせず、変更なしのEmbeddingを再利用。接続先・モデルprofileの変更がキャッシュ識別子を変える。
- HTTP gatewayのBearer、profile照合、非ゼロ・件数・次元・正規化、JSON不正・応答上限・429・abortを確認。回答の固定指示と資料を分け、ツール経路がないことを確認。
- 回答は渡した根拠IDのみ引用可能。根拠なし、未設定、架空の引用を区別。資料削除で保存済み回答の本文を無効化・消去。
- Python内部APIのトークン、本文上限、原本SHA256、パス制限、symlink、Office入力、PDFページ上限、旧デモ用ルートがないことを確認。

## 未確認・未実装の範囲

実モデルのEmbeddingと回答生成、検索の関連度、生成内容の正しさ、実利用量での応答時間と料金は未確認。API契約はローカルHTTP gatewayで、検索・回答のライフサイクルはテストProviderで検証した。Azure OpenAIへ直接接続するadapterは含めていない。

Azure VMへの配置、公開HTTPS・ネットワーク制限、Azure OCR連携、外部アプリからの呼び出し、利用者画面、既存資料の移行、Wiki編集・メタデータ編集、過去版の自動保管期限、複数APIプロセス化は未実施。完全走査のsemantic検索に大規模データの性能保証はない。

このターンのContext Still利用は `context_compile` 1回、`compile_eval` 1回。プロジェクト開始時の `initial_instructions` は実行済みで、タスクごとの再実行はしていない。

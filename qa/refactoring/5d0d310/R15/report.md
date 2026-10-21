# R15 外部APIの編集処理の分割 実施記録

対象版: 5d0d310（未コミットの他作業と並行。knowledge-api/ 内の変更は本作業のみ）。状態: 検証待ち。

## 構成と依存方向

`knowledge-api/src/editing.ts` は再exportだけのbarrel（7行）。実装は `src/editing/`。
`app.ts`・`responses.ts`・`tests/editing.test.ts` のimportは変更なし。

| ファイル | 責務 |
| --- | --- |
| contract.ts | 公開schema、`EDITING_ENDPOINTS`、`documentListParameters`、`Draft`・行型（OpenAPI/HTTP/応答schemaが共通参照） |
| hash.ts / patch.ts | `sha256`、本文パッチ`applyEdits` |
| service.ts | `DocumentEditing`（認可refresh、状態、冪等性、`remember`）と各操作への委譲 |
| body.ts | 本文読取（I/O後の再認可・版再確認） |
| html.ts | viewer向けHTML変換`markdownView` |
| drafts.ts | 下書き作成・更新 |
| commit.ts | 版確定`save`（受付枠、fsync、再認可、tx、失敗時cleanup） |
| attachments.ts | 添付の参照・読出し・保存 |
| metadata.ts | メタデータ更新（http.tsのインライン処理を移動。順序は同一） |
| http.ts | ルート接続`installEditing`（添付POSTの受付枠、認可、サイズ上限） |

依存は contract/hash/patch（葉）← body/html/drafts/commit/attachments/metadata（`service`は型importのみ）← service ← http ← editing.ts。実行時の循環なし。
各操作は従来どおり `DocumentEditing` のメソッド経由（委譲）で呼ばれ、認可・受付枠・再照合の経路は一本のまま。

## 検証（knowledge-api/）

- `bun test`: 103 pass / 0 fail（基準と同じ）
- `bun run typecheck`: エラーなし
- `bun run lint`: 5 warnings / 7 infos（基準と同数。指摘は既存のuseTemplate等が移動したもののみ）
- `bun run contract:check`: 一致。`contracts/knowledge-api.openapi.json` に差分なし

## 未実施・補足

- DBスキーマ、エラー形式、保存手順は未変更。挙動修正なし。
- Python側document editor gateway・MCP連携の試験は本担当範囲外のため未実行。

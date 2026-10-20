# 図表 P1・P2 実装契約

[実装計画](diagram-design-implementation-plan.md)のP1（独立Excalidrawファイル）とP2（下書きと版管理）の実装契約。作業者はこの契約を変えない。変更が必要な場合は監督者へ報告する。P3以降（AI、Diagram Design、HTTP/MCP公開）は対象外。

## 形式とJob

- 図表は`.excalidraw`。`Job.excalidraw = True`、`state = "success"`、`preview = "excalidraw"`、`filename = "<表示名>.excalidraw"`。`Job.diagram`（draw.io）とは別。
- `Job`（`src/docling_desk/documents/conversion.py`）と`frontend/src/lib/types.ts`・`contracts.ts`の`excalidraw`フィールドは監督者が追加済み。

## 保存配置

```text
DATA/content/documents/<id>/
  manifest.json                         version 2
  revisions/<revision_id>/original.excalidraw
  revisions/<revision_id>/revision.json
  drafts/<draft_id>/versions/<n>/original.excalidraw
  operations/<operation_id>.json
DATA/derived/documents/<id>/            一覧登録に必須（中身は空でよい）
DATA/runtime/documents/<id>/job.json    save_jobで書く
```

manifest version 2:

```json
{
  "version": 2,
  "kind": "excalidraw",
  "id": "<32 hex>",
  "filename": "<表示名>.excalidraw",
  "created": 1760000000.0,
  "key": "documents/<id>/revisions/<revision_id>/original.excalidraw",
  "sha256": "<現在版の原本SHA-256>",
  "current_revision": "<revision_id>",
  "revision_count": 3,
  "draft": {"id": "<draft_id>", "version": 4, "base_revision": "<revision_id>", "versions": [2, 3, 4], "updated": 0.0} | null,
  "operations": {"<sha256(idempotency key)>": {"id": "<operation_id>", "created": 0.0}}
}
```

revision.json: `{"id", "number"(1始まり), "parent"(revision_id|null), "sha256", "size", "created", "restored_from"(revision_id|null)}`。

operations/<operation_id>.json: `{"id", "key_sha256", "request_sha256", "action", "status": 201|200, "result": <応答JSON>, "created"}`。7日を過ぎたものと、200件を超えた古いものはmanifest公開時に参照から外し、ファイルを削除する。

- ID（diagram_id、revision_id、draft_id、operation_id）はすべて`uuid4().hex`。
- `source_etag` = `"r-<current_revision>"`、`draft_etag` = `"d-<draft_id>-<version>"`（引用符付き）。
- 下書きは資料ごとに有効1件、`versions`は最新3件だけ残し、古いversionディレクトリを削除する。
- `storage.original_file(folder, ".excalidraw")`は、manifestがversion 2なら`key`のパスを返す。version 1と既存資料の挙動は変えない。

## 書込み順序と排他

- すべての書込みは`exclusive_write(data, "diagram")`の中で、資料ごとのロックを取って行う。ライブラリ登録は既存`library.LOCK`を使う。
- 新規作成: 版ファイル → manifest（tmp+replace）→ derivedフォルダー → job.json（`save_job`）→ library.json（`register_upload`相当）。
- 更新: 版ファイル → 排他区間で基準版を再確認 → operationファイル → manifest公開。
- 書込み後に`blob_mirror.notify()`を呼ぶ。
- 起動時の`repair_diagrams(data)`: manifest version 2があるのにjob.jsonかderivedフォルダーがない図表を、manifestから再作成する。manifestから参照されない版・下書き・operationファイルで1時間より古いものは削除する。`app.py`のstartupから呼ぶ（登録は監督者）。

## シーン検証（サーバー側。すべて422、容量だけ413）

- 全体16 MiB以下（413）。UTF-8 JSONオブジェクト、入れ子100以下。
- `type == "excalidraw"`、`version`は整数、`elements`は配列（10,000以下）、`appState`はオブジェクトまたはなし、`files`はオブジェクトまたはなし。
- 要素: `id`は空でない文字列で一意、`type`は`rectangle diamond ellipse arrow line freedraw text image frame`のいずれか。それ以外（`embeddable`、`iframe`、`magicframe`、未知）は422。
- 数値はすべて有限。`x y width height`と`points`の座標は絶対値1,000,000以下。
- `link`はnull、空、または`http:` `https:` `mailto:`のURLだけ。
- files: 各値の`mimeType`は`image/png image/jpeg image/webp image/gif`のみ（`image/svg+xml`は422）。`dataURL`は`data:<同じMIME>;base64,`で、復号後4 MiB以下、先頭バイトがMIMEと一致。
- 未知フィールドは保持する（削除・並べ替えをしない）。生バイトで受け取るシーンは、受け取ったバイトそのものを保存する。

## 既知の制約

- 文書変換は`exclusive_write`を変換全体で保持するため、変換中の図表保存・自動下書きはその完了まで待つ。画面は保存中の表示を続ける。

## HTTP（`src/docling_desk/api/diagrams.py`、`router`を公開）

共通: 本文がシーンの要求は`Content-Type: application/vnd.excalidraw+json`または`application/json`の生バイト。シーン応答は`application/vnd.excalidraw+json`と`ETag`。情報応答はJSON。エラーは既存と同じ`{"detail": "<日本語>"}`。

| 操作 | ルート | 条件と応答 |
| --- | --- | --- |
| 作成 | `PUT /api/diagrams/{id}` 本文 `{"name": str, "folder_id": str|null, "scene": object}` | `If-None-Match: *`必須（ないと428）、`Idempotency-Key`必須（428）。idは32桁hex（422）。既存idなら、同じキー・同じ要求は保存済み結果を200で返し、同じキー・別の要求は409、別のキーは412。成功は201 `DiagramInfo`。`scene`はJSON本文の一部なので、検証後に再シリアライズして保存する（受信バイトの保持は`/revisions`、下書き、取込に適用） |
| 情報 | `GET /api/diagrams/{id}` | 200 `DiagramInfo`、`ETag: source_etag` |
| 原本 | `GET /api/diagrams/{id}/content?revision=<id>` | revision省略で現在版。`?download=true`で`Content-Disposition: attachment` |
| 保存 | `POST /api/diagrams/{id}/revisions` 本文シーン | `If-Match: source_etag`（なし428、不一致412）、`Idempotency-Key`必須。201 `DiagramInfo` |
| 版一覧 | `GET /api/diagrams/{id}/revisions?before=<number>&limit=<1..100, 既定50>` | 新しい順。`{"items": [RevisionInfo], "next": number|null}` |
| 復元 | `POST /api/diagrams/{id}/restore` 本文 `{"revision_id": str}` | `If-Match: source_etag`、`Idempotency-Key`。対象版の内容で新しい版を作り`restored_from`を記録。201 `DiagramInfo` |
| 下書き作成 | `POST /api/diagrams/{id}/drafts` 本文シーン（任意。空なら現在版の内容） | `If-Match: source_etag`。既に下書きがあれば409（`detail`に既存draft_id）。201 `DraftInfo`、`ETag: draft_etag` |
| 下書き取得 | `GET /api/diagrams/{id}/drafts/{draft_id}`、`GET .../drafts/{draft_id}/content` | `DraftInfo`またはシーン、`ETag: draft_etag` |
| 下書き更新 | `PUT /api/diagrams/{id}/drafts/{draft_id}` 本文シーン | `If-Match: draft_etag`。200 `DraftInfo` |
| 下書き確定 | `POST /api/diagrams/{id}/drafts/{draft_id}/commit` 本文 `{"source_etag": str}` | `If-Match: draft_etag`、`Idempotency-Key`。両方を同じ排他区間で検査。下書きの最新versionから新しい版を作り、下書きを削除。201 `DiagramInfo` |
| 下書き破棄 | `DELETE /api/diagrams/{id}/drafts/{draft_id}` | `If-Match: draft_etag`。204 |
| 操作結果 | `GET /api/diagrams/{id}/operations/{operation_id}` | 保存済みの`result`を返す。期限切れは404 |

- 冪等性: 同じキーで要求ハッシュが同じなら保存済み結果を元のstatusで返す。異なれば409。照合はETag検査より先に行う。
- 下書きの基準版が現在版と違う場合、確定は412（下書きは残す）。
- 図表でない資料IDへの図表操作は404。
- 既存`/api/library/operations`の`copy`は、対象に図表（直接またはフォルダー配下）を含むと409「図表の複製には対応していません。」。
- 既存`/api/upload`は`.excalidraw`を受け付け、シーン検証のうえ図表として新規作成する（冪等性キーなし、新しいidを割当て）。

```ts
interface DiagramInfo {
  id: string; name: string; folder_id: string | null;
  revision_id: string; revision_number: number; sha256: string; size: number;
  etag: string; created: number; updated: number;
  draft: DraftInfo | null;
  operation_id: string | null;   // 作成・保存・復元・確定の応答だけ
}
interface DraftInfo {
  id: string; version: number; base_revision: string; etag: string; updated: number;
}
interface RevisionInfo {
  id: string; number: number; parent: string | null; sha256: string;
  size: number; created: number; restored_from: string | null; current: boolean;
}
```

## フロントエンド

- `frontend/src/lib/diagrams.ts`: 上記HTTPのクライアント。`withBase`を使う。失敗時は`DiagramRequestError`（`status`と`detail`を持つ`Error`）を投げる。`Idempotency-Key`は呼出し側で`crypto.randomUUID()`を作り、再送では同じ値を使う。
- `frontend/src/diagram/DiagramCanvas.tsx`: Excalidraw 0.18.1の共通ラッパー。`serializeAsJSON(elements, appState, files, "local")`と`restore`を使う。props:
  `{ initialScene: string | null; readOnly?: boolean; onChange?: (scene: string) => void; apiRef?: (api: ExcalidrawImperativeAPI | null) => void }`。
- `frontend/src/diagram/export.ts`: `exportSvg(scene) -> Blob`（image/svg+xml）、`exportRaster(scene, "webp" | "jpeg") -> Blob`（scale 2、品質0.85、1辺4,096pxかつ16メガピクセル以内に縮小、JPEGは白背景、WebP非対応ブラウザーではJPEGへ切替えて`{blob, format}`で返す）、`exportHtml(scene) -> Blob`（SVGを埋め込んだ静的HTML、scriptなし、`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">`）。
- `frontend/src/features/DiagramEditor.tsx`: `export function DiagramEditor(props: { job: Job; onChanged?: () => void })`。読込、キャンバス、保存状態、保存（下書き確定または`/revisions`）、原本ダウンロード、書出し、版一覧と復元、自動下書き保存（入力停止2秒後、最短15秒間隔、差分時のみ）、競合時に入力を保持して表示。画面遷移時の未保存警告。
- `Viewer.tsx`は`job.excalidraw`なら`DiagramEditor`を表示する。
- `Library.tsx`は「図表を作成」（名前入力、現在のフォルダーへ作成、作成後に開く）を追加し、図表を含む選択ではコピー操作を出さない。
- `formats.ts`と取込画面は`.excalidraw`を受け付ける。

## 禁止事項（全作業者）

- コミット、プッシュ、git checkout/reset/rm等の取消せない操作をしない。他人のファイル・データを削除しない。
- サーバー（開発・API・プレビュー）を起動・停止しない。Playwright e2eも実行しない。
- 割り当てられたファイル以外を編集しない。必要なら監督者へ報告する。

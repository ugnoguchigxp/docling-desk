# G1 frontend 実施報告（R09 → R04 → R10 → R11 → R08）

対象版: コミット 5d0d310 の上の未コミット変更（他担当の Python / knowledge-api / e2e の変更を含む）。frontend 側の変更は `frontend/src/**`（`plate/` 除く）、`tests/test_format_contract.py`、`tests/fixtures/format-contract/` のみ。コミット・サーバー起動・build/export は行っていない。

## 状態

| 項目 | 状態 | 要点 |
| --- | --- | --- |
| R09 HTTP通信とエラー情報の統一 | 完了 | `lib/http.ts` に transport とエラー契約を集約。diagrams / api / print-document / Resources を移行 |
| R04 状態と復旧案内の表示 | 完了 | `StatusMessage` に kind / detail / recovery を追加。翻訳・解説・アップロード・画像情報・Wiki・検索等へ接続 |
| R10 取得と更新通知の規則 | 完了（実ブラウザー計測は not_run） | `lib/polling.ts` / `lib/query-keys.ts`。仮想時計で60秒の要求数 31 → 13 |
| R11 非同期操作の結果とフォーカスの寿命 | 完了 | `useAsyncScope` に AbortSignal を追加。Explanation / Translation / Knowledge を接続 |
| R08 形式と対応機能の整理 | 一部 | frontend の静的定義を集約し、共有 fixture で TS/Python を照合。サーバー側の表示名ずれは変更依頼（下記） |

## R09

契約（`frontend/src/lib/http.ts`）

- `HttpError(status, detail, body, headers)`。status 0 は応答なし（通信断）。2xx で本文が壊れている場合は応答の status を保つ。`body` は JSON ならそのまま、JSON でなければ先頭 2000 文字。`headers` は失敗応答の Headers（ETag / Retry-After など）。
- `send` が fetch・root path（`withBase`）・401 の `leaveForLogin`（並行しても遷移は1回）・失敗の HttpError 化を担当。AbortError と、呼出側が abort 済みのときのエラーは通信断に丸めない。GET 以外を含め自動再送は追加していない。
- 読取り関数は本文の種類ごとに分離: `readJson` / `readText` / `discard`（204 等）、`requestJson`（= 既存 `request`）/ `requestText`（text・etag・contentType）/ `requestEmpty`（etag）。204 は `requestEmpty` だけが受け付け、JSON リーダーは壊れた応答として扱う。
- `leaveForLogin` / `loginRedirect` は http.ts に移し、`api.ts` から再 export（既存 import は変更不要）。

移行表

| 利用先 | 変更 |
| --- | --- |
| `lib/api.ts` `request` | `requestJson` へ。通信断は生の TypeError ではなく HttpError(0) になり、文面が日本語になる |
| `lib/diagrams.ts` | 独自 send / json / detailOf を削除し http.ts を使用。`DiagramRequestError` は `HttpError` の別名 adapter として残置（利用先なし。レビュー後に削除可） |
| `DiagramEditor.tsx` / `diagram-editor/problems.ts` / `useDraftSync.ts` / `useDiagramSave.ts` | `DiagramRequestError` → `HttpError` に置換（409・412・404・0 の分岐は同一） |
| `print-document.ts` `readText` / `Resources.tsx` RAG 取得 | 生 fetch → `requestText`。利用者向け文面は `withDetail` で従来どおり |
| `editor/main.tsx`（編集セッション API） | 対象外: 別 bundle、`detail` が機械コード、root path 非適用の独自契約 |
| `lib/media.ts` | HTTP 呼出しなし。変更は R08 側 |

draft_id（要求）: 409 の既存下書き ID はサーバーが文面（`既に下書きがあります。draft_id: <hex>`、`documents/diagram_store.py:548`）にだけ入れている。frontend は構造化情報を優先し、無ければ従来の 32 桁照合に戻る実装を維持した（正規表現の解釈は増やしていない）。構造化するなら、互換を確認した上で `X-Draft-Id` ヘッダーまたは detail オブジェクトで返してほしい。frontend は `HttpError.headers` で受けられる。

## R04

契約（`components/ui.tsx`、README に表を追記）

- `StatusMessage` の追加 props: `kind`（loading / empty / setup / pending / running / partial / stale / unsaved / error）、`detail`（補助情報）、`recovery`（今できる操作のボタン列）。`kind` なしは従来の `<p role="status">`。`error` だけ `role="alert"`、他は polite な status。明示 `role` は優先。`data-kind` を付与。復旧ボタンは読み上げ領域の外。複数通知を1段落にまとめる場合は `mostUrgent`。共通部品は API も業務状態も参照しない。

状態表示表（適用済み）

| 画面 | サーバー状態 → kind | 復旧操作 |
| --- | --- | --- |
| 翻訳（`#translationStatus`） | queued / waiting → pending（秒数付き、やり直しは出さない）、running → running、failed / record.error → error、interrupted → partial、原文更新 → stale、未翻訳 → empty、取得失敗 → error | 失敗・中断・原文更新: 「翻訳をやり直す」。取得失敗: 「状態を再取得」 |
| 解説（`#explanationStatus`） | queued → pending、running → running、failed → error、stale → stale、設定不足 → setup（失敗にしない）、warning → partial | 既存の再試行・再生成ボタン |
| アップロード | 処理中 → running、一部成功 → partial、全件失敗 → error。応答なし（status 0）は「受け付けられている場合があるため資料一覧を確認」へ（受付不明を単純な再試行にしない。再送もしない） | 資料一覧の確認を案内 |
| 画像情報 | 409/412（他で更新）→ stale、それ以外の保存失敗 → error（再保存可、入力保持）、読込 → loading | stale: 「最新情報を読み直す」を StatusMessage の recovery に |
| Wiki / 検索 | 記事読込 loading、記事取得失敗 error + 「再読み込み」、日本語訳未公開 partial、検索中 running、出典更新 stale、索引失敗 error | 記事: 再読み込み |
| 図編集ヘッダー | 保存中 → running、未保存・保存できていない → unsaved（`data-kind` のみ） | 既存 ProblemBanner（conflict / failure の区別と選択肢は変更なし） |
| その他 | Library・Tables・PrintPreview の alert は `kind="error"` へ。Slides・App 詳細・PrintPreview・HistoryPanel・DiagramEditor の読込中は loading 等 | — |

## R10

方針表（`lib/polling.ts` / `lib/query-keys.ts`）

| query | 処理中 | 通常時 | 失敗後 | 備考 |
| --- | --- | --- | --- | --- |
| library（App） | 2秒（queued/running あり） | 資料一覧画面のみ 5秒。他モードは停止 | 10秒 | 一覧へ戻ったら即再取得。非表示 tab は TanStack 既定で停止、online 復帰で再取得 |
| wiki-catalog / wiki-article | — | 5秒（Wiki 表示中のみ） | 10秒 | |
| knowledge-status（検索 dialog） | 2秒（索引作成中） | 5秒 | 10秒 | |
| knowledge-search | 2秒（queued/running） | 完了後は 5秒（出典更新の検知）。failed / cancelled / stale は停止 | 10秒 | 従来は完了後も 2秒で永久に取得 |
| translations | 1.5秒（従来どおり） | 停止 | 処理中なら 10秒（従来は停止して進捗を見失う） | |
| explanations | 1.5秒（従来どおり） | パネルを開いている間のみ 5秒（従来 1.5秒） | 10秒（従来 1.5秒） | |

無効化先: library（アップロード・移動・削除・複製・図作成・画像属性）、wiki-catalog / wiki-articles（保存・削除・取込）、translations / explanations（開始・生成）。図編集の保存は一覧に表示される属性を変えないため library の無効化は追加していない（既存の `onChanged` を維持）。

計測（`lib/polling.test.ts`、仮想時計・実 QueryObserver）: 60秒待機の要求数 固定2秒 31 → 新方針 13（58% 減）。処理中は 60秒で 30 以上のまま（従来の通知遅延を維持、完了は 2.1 秒以内に検知）。他クライアントの変更は WATCH_MS+0.25秒以内に検知（10秒以内）。非表示では 0 件、復帰後に再開。失敗後は 60秒で 7 件以下（間隔 10秒）、復旧後は通常周期。offline → online で 1 件。

## R11

寿命対応表

| 操作 | 失効する条件 | 反映してよいもの | 実装 |
| --- | --- | --- | --- |
| 解説パネルを開く（overview 取得 → 自動生成） | 単位の変更、パネルを閉じる、画面破棄、再度開く | パネル表示・フォーカス・自動生成の POST | `useAsyncScope(current)`（手書きの openAttempt を置換）。rAF のフォーカスも `isCurrent` で保護 |
| 翻訳 dialog を開く | 資料・ページの変更、画面破棄、再度開く | dialog の open | `useAsyncScope(job:page)` |
| Wiki 記事を編集用に読む | 記事・表示の切替 | editor の表示 | `isCurrent.signal` で取消（読取りのみ） |
| Wiki エディター保存（PUT） | 保存は取消さない | 常に一覧を無効化。Wiki を離れていれば画面遷移・メッセージを出さない（古い `route` による引き戻しを廃止） | `present` ref |
| Wiki 取込・削除 | 既存の規則（離れたら結果を出さない） | 一覧の再取得 | 変更なし |
| 図の保存・復元・下書き | 画面の取消 ≠ サーバーの取消 | signal を渡さない。破棄後は `ui` が no-op | 既存の `s.unmounted` を維持 |
| アップロード | 受理済みなので画面で取消さない | 結果は onMessage に必ず届く | 変更なし（受付不明の文面のみ R04） |

`useAsyncScope.start()` は `isCurrent` に `signal` を持たせた（互換あり）。persisted な操作には signal を渡さない、という規則を async-scope.ts に明記。

## R08

形式別対応表（`lib/formats.ts` の `families` が静的事実の唯一の定義）

| family | 拡張子 | 原本表示 | 備考 |
| --- | --- | --- | --- |
| pdf / pptx / xlsx | .pdf .pptx .xlsx | extracted | 実行可否は job（preview / state）。Office の部分成功は `state=partial`（`isDone`） |
| docx / markdown / text / tex | .docx .md .markdown .txt .text .tex .latex | extracted（文書全体 1 単位） | TeX の PDF 有無は `tex_preview` |
| image | .png .jpg .jpeg .gif .webp | image | job.image |
| drawio | .drawio .dio .xml .svg .html .htm、.drawio.png | drawio | 画像内 draw.io は内容で決まる。`.png` でも mxfile があれば `job.diagram` |
| excalidraw | .excalidraw | excalidraw（編集） | job.excalidraw |

- 追加: `familyOf`、`viewKind(job)`（server フラグ優先: excalidraw → drawio → image → extracted）、`originalName(job)`、`isPdf/isPptx/isXlsx/isDocx`、`isWikiArticle/isWikiImportFile`。
- 置換した重複: print-document、PrintPreview、DocumentViewer、Viewer、Tables、Library、media.ts、api.ts、types.ts、Knowledge（Wiki 取込 5 箇所）。拡張子の連動（受付・ラベル・accept）は変更なし。
- 境界の修正: 先頭が拡張子だけの名前（`.pdf`）を frontend は受け付けていたが、サーバー（pathlib の suffix）は 415。frontend 側を合わせた（共有 fixture で検出）。
- 共有 fixture: `tests/fixtures/format-contract/cases.json`（名前 45 件と PNG 内 mxfile の内容 3 件）。TS は `formats.test.ts`、Python は `tests/test_format_contract.py`（実際の `/api/upload` で 415 / 非 415、family、`png_has_diagram`）が同じファイルを読む。
- 変更依頼（Python 担当）: `src/docling_desk/documents/text.py` の `SUPPORTED_LABEL` に Excalidraw が無く、415 の文面が frontend の `supportedLabel` と食い違う。`"... / draw.io / Excalidraw"` に直すと、`test_the_refusal_label_names_every_accepted_family`（現在 strict xfail）が失敗に変わるので xfail を外す。

## 変更ファイル

- 新規: `frontend/src/lib/http.ts`、`http.test.ts`、`polling.ts`、`polling.test.ts`、`query-keys.ts`、`async-scope.test.tsx`、`tests/test_format_contract.py`、`tests/fixtures/format-contract/cases.json`、本報告
- 変更（lib）: `api.ts`、`diagrams.ts`、`async-scope.ts`、`formats.ts`、`media.ts`、`types.ts` と対応テスト（`diagrams.test.ts`、`formats.test.ts`）
- 変更（UI）: `components/ui.tsx`、`components/README.md`、`App.tsx`、`features/` の Translation / Explanation / Upload / ImageDetails / Knowledge / Library / Viewer / PrintPreview / Slides / Tables / Resources / DiagramEditor / print-document / diagram-editor（problems / useDiagramSave / useDraftSync / HistoryPanel）、`viewer/DocumentViewer.tsx` と対応テスト

## 維持した契約

- 409 / 412 / 428 / 404 / 413 / 422 の status・detail・body を保持（図編集の `classify` の分岐は変更なし）。GET 以外の自動再送なし。受付不明（status 0）の再送なし。
- 保存状態の正本は画面へ複製していない（kind は毎回サーバー状態から導出）。
- URL・保存形式・サーバー API の契約は変更なし。plate/ は未変更。

## 実行した検査

| コマンド | 結果 |
| --- | --- |
| `pnpm --dir frontend exec vitest run`（全体） | 48 files / 351 tests 合格（着手前 291） |
| `pnpm --dir frontend typecheck` | 合格 |
| `pnpm --dir frontend lint` | 合格 |
| `DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest tests/test_format_contract.py -q` | 93 passed, 1 xfailed（意図した strict xfail） |
| `.venv/bin/python -m ruff check` / `ruff format --check`（test_format_contract.py） | 合格 |
| 変異確認 | Translation の `isCurrent` と Explanation の rAF 保護を外すと対応テストが失敗することを確認し、元に戻した |

## not_run / 注意

- ブラウザー e2e、`pnpm test:visual`、実ブラウザーでの 60秒要求数計測、build / export:static は未実行（指示どおり）。R10 の要求数は仮想時計の QueryObserver 計測で、実画面の合計ではない。
- 元から prettier 差分がある `Knowledge.tsx` / `Explanation.tsx` / `Upload.test.tsx` / `ui.test.tsx` / `Knowledge.test.tsx` / `Explanation.test.tsx` は、既存部分を整形せず変更箇所だけ整形した。
- Knowledge.test.tsx に `WikiPlate` の mock を追加（編集保存の経路試験のため）。
- 既存の `DiagramRequestError` 別名は利用先なし。R09 のレビュー後に削除してよい。

## 他担当・supervisor への依頼

1. `src/docling_desk/documents/text.py` `SUPPORTED_LABEL` に Excalidraw を追加（Python 担当）。
2. 任意: 409（既存下書き）の draft_id を構造化（ヘッダーまたは detail オブジェクト）。公開エラー契約の互換確認が先。
3. G1 の画面確認時に、資料一覧が 5 秒周期・Wiki 表示中は一覧非取得になった点（R10）を操作観測の対象に含めてほしい。

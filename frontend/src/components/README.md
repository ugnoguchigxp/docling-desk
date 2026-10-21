# 共通画面部品

資料一覧・文書閲覧・表・翻訳・解説は、このディレクトリの部品を組み合わせて作る。同じ役割の部品を機能側へコピーしない。既存のID、class、標準HTML属性とrefを渡し、現在のCSSに合わせる。共通部品から `features/`、資料API、業務状態を参照しない。

| 部品                                    | 利用先                         | 担当                                                                   |
| --------------------------------------- | ------------------------------ | ---------------------------------------------------------------------- |
| Icon / IconDefinitions                  | 一覧、閲覧、追加、フォルダー   | 現行SVGの描画                                                          |
| Button / IconButton / ActionLink        | 全機能                         | 標準属性、操作名、disabled、ref、ボタンのtype                          |
| TextField / SelectField / CheckboxField | 一覧、追加、翻訳、表           | 標準入力属性とチェックボックスの部分選択                               |
| Toolbar                                 | 一覧、閲覧、表                 | 既存の配置を保持するheader/div                                         |
| Tabs                                    | 文書閲覧                       | ARIAと矢印/Home/End操作。文書面の保持は利用側が管理                    |
| Dialog / DialogHeading / DialogActions  | 追加、資料操作、翻訳、表の拡大 | native dialog、Escape、処理中の閉じる制限、フォーカス復帰              |
| DisclosureMenu / Popup                  | 資料行、保存、情報、表         | native detailsまたはポップアップ、外側クリック、Escape、キーボード操作 |
| Breadcrumbs                             | 一覧と保存先選択               | 現在地、経路、移動・ドロップcallback                                   |
| StatusMessage                           | 全機能                         | 状態の種類別のstatus/alert、補助情報、復旧操作の表示                   |
| ContentCard / MetadataDisclosure        | 構造、RAG、解説                | 本文と詳細情報。文書の文字列はReactのテキストとして描画                |
| SidePanel                               | 翻訳・解説                     | 見出しと閉じる操作。幅や重なり方は既存CSSが管理                        |
| DocumentFrame                           | PDF・PPTX・XLSX・DOCX          | iframeの属性・ref。形式別のsandboxとsrcは利用側が指定                  |

共通化のためにDOMのラッパーや余白を追加しない。`Toolbar` を挟むときも、既存flexの直接の子と兄弟関係を保つ。スタイルの違いは既存classと必要なpropsで表す。共有CSSの整理、resetや別テーマの導入は文書レイアウト比較と別の変更にする。

API取得・保存版の選択・生成要求・フォルダーの操作判断は `features/` が管理する。サーバー状態の正本はTanStack Queryの取得結果とし、共通部品に処理状態を複製しない。表専用のSVGと選択処理は `features/TableIcons.tsx` と `features/table-model.ts` に置く。

原本プレビューのコピーは `features/PreviewCopy.tsx` が選択文字を受け取り、共通の `IconButton` と `Dialog` を使う。Wordの表示倍率は `features/Word.tsx` が共通ボタンで操作する。文書内の信頼済みスクリプトは選択文字と寸法を通知し、React側で送信元を照合する。

部品変更時は `pnpm lint`、`pnpm test:run`、`pnpm test:e2e`、`pnpm test:visual` を実行する。新しい画面パーツを追加する場合はこの台帳へ用途を追記する。

## 状態の表示契約

`StatusMessage` は `kind`・本文・`detail`（補助情報）・`recovery`（今できる操作）を受け取る。種類を決めるのは各機能で、サーバー状態や取得結果から対応づける。共通部品はAPIも業務状態も参照しない。`kind` を渡さない従来の使い方は `<p role="status">` のまま。

| kind | 意味 | 読み上げ |
| --- | --- | --- |
| loading | 表示するものを取得中 | status |
| empty | まだ表示するものがない | status |
| setup | 前提の設定が足りない（任意機能の未設定を失敗にしない） | status |
| pending | 受け付け済みで開始前（順番待ち・間隔待ち） | status |
| running | 処理中 | status |
| partial | 完了したが一部が欠ける・省かれた | status |
| stale | 古い版を表示・保存している、他で更新された | status |
| unsaved | 入力がこの画面にだけある | status |
| error | 失敗 | alert |

`recovery` のボタンは読み上げ領域の外に置く。複数の通知を一つにまとめるときは `mostUrgent` が全体の種類を決める。保存状態の正本は画面へ複製せず、`kind` は表示のたびにサーバー状態から求める。待機中・実行中のサーバー状態は「やり直す」ではなく進行として表示し、失敗・中断・原文更新だけを再実行の操作につなげる。

`FeatureBoundary` は表など遅延読込する機能の失敗をその機能内に留め、資料への移動と原本表示を保つ。フォールバックの文面と再読込操作は利用側が指定する。コピー完了・失敗の通知は `lib/async-scope.ts` の共通判定を使い、ページ・タブの変更や画面の破棄後に前の結果を表示しない。

プリントプレビューは `features/PrintPreview.tsx` が共通の `Dialog`、`DialogHeading`、入力・ボタン・`DocumentFrame` を組み合わせる。印刷用の文書はスクリプトを実行しない専用の `public/print.html` に構成し、元の閲覧フレームのsandboxは変更しない。範囲・用紙・言語の判断と読み込みの中断は機能側が担当する。

## 独立した文書ビューアー

Wikiのアップロードと新規作成にも `IconButton` を使用する。`features/WikiEditor.tsx` は共通入力・ボタン・DialogとPlate公式EditorKitを使う `editor/WikiPlate.tsx` を組み合わせ、記事情報の入力と保存を管理する。

`../viewer/DocumentViewer.tsx`は、通常画面とiframe専用入口で共有する閲覧部品です。`ViewerSource`で取得先をインスタンスごとに指定し、翻訳・解説・印刷などの通常画面の操作は`extensions`として外側から渡します。資料一覧、アップロード、Wiki、チャットのDOMには依存しません。埋め込みでは生成・保存・印刷の操作を渡さず、文字選択の質問だけをホストの接続部品へ通知します。

DOMのIDと形式別CSSを再利用するため、同一Reactルートへの複数配置は対象外です。iframeごとの複数配置を使ってください。6形式のsandbox表示、出典位置、表示タブ、狭い画面、再接続、通常画面の操作回帰の記録は`qa/viewer-embedding/`に保存します。

## アプリの外枠・共通値・操作の規則（G3）

詳細な表は [`docs/refactoring/G3-ui-contracts.md`](../../../docs/refactoring/G3-ui-contracts.md)。この台帳には部品の置き場所と守る規則だけを書く。

| 部品 | 場所 | 担当 |
| --- | --- | --- |
| AppShell | `src/app/AppShell.tsx` | 通常画面だけの外枠。アイコン定義、`<main>`、共通ナビゲーション1つ、Ctrl/Cmd+K、検索ダイアログ（注入）、モードごとの最後の位置。本文は一度だけ描画する。埋め込み入口（`src/embed/`）と単独編集画面はimportしない（`src/app/embed-boundary.test.ts`で確認） |
| AppNavigation | `src/app/AppNavigation.tsx` | モード切替、本文検索、RAG、戻り先の操作。モード・イベントだけを受け取り、APIも画面の状態も参照しない。見た目は `app-navigation.css`（印刷では非表示） |
| return-point | `src/app/return-point.ts` | 出典へ移動した後の戻り先（1段、メモリ上のみ）。URLにも保存形式にも含めない |
| app-tokens.css | `src/components/app-tokens.css` | 用途別の共通値（色・線・余白相当・操作部品の高さ・重なり順）。`ui.tsx` が読み込む（通常画面と埋め込み）。単独編集画面は `editor/main.tsx`、PlateのEditorは `WikiPlateEditor.tsx` が読み込む。`static/style.css` は同じ名前を `var(--名前, 従来の値)` で参照し、`--accent` と `--line` だけは両方に定義して `app-tokens.test.ts` で一致を確認する |
| FeatureStatus | `src/features/FeatureStatus.tsx` | `GET /api/diagnostics/features` の表示。ダイアログを開いたときと「もう一度確認」だけで取得し、定期取得しない。表示の種類は機能自身の `status`/`optional` だけで決める |

規則:

- 色・線・高さ・重なり順は `app-tokens.css` の名前を使う。新しい直接指定を足さない。原本ドキュメント内部のCSS、Plate・Excalidrawの内部クラスはここで上書きしない（Plateは `--plate-*` と `@theme inline`、Excalidrawは公開propだけ）。
- 操作は「主要＝`primary`（1つの帯・ダイアログに1つ、最後）」「補助＝既定」「危険＝`danger`（削除・破棄・編集中の内容を捨てる操作。同じ帯の最後）」。無効の理由は `title` か状態表示に出す。
- 用語: 保存＝管理対象を更新、原本を保存＝原本の取得、書き出し＝別形式の生成、削除＝既存の削除契約。同じ語が別の対象を指すときは対象名を補う。

